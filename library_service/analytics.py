"""First-party product metrics with bounded schemas. Never accept free text."""
import asyncio
import logging
import os
import re
import time
from .anonymous import purge_expired
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

log = logging.getLogger(__name__)
BROWSER_VALUES = {
    'library_view': {None}, 'page_view': {None}, 'citation_open': {None},
    'theme_changed': {'light', 'dark'},
    'search_mode_changed': {'name', 'text', 'hybrid'},
    'answer_feedback': {'positive', 'negative'},
}
SERVER_EVENTS = {
    ('POST', '/api/documents'): 'document_upload',
    ('GET', '/api/search'): 'search',
    ('POST', '/api/chat'): 'chat_requested',
    ('POST', '/api/similarity'): 'comparison',
}


def enabled():
    return os.environ.get('SARA_ANALYTICS_ENABLED', 'true').lower() in ('true', '1', 'yes')


def route_event(method, path):
    event = SERVER_EVENTS.get((method, path))
    if event:
        return event
    if re.fullmatch(r'/api/documents/[0-9a-f-]{36}/file', path) and method == 'GET':
        return 'document_open'
    if re.fullmatch(r'/api/documents/[0-9a-f-]{36}/(summary|retry)', path) and method == 'POST':
        return 'summary_requested' if path.endswith('/summary') else 'processing_retry'
    return None


def record_events(pool, rows):
    if not enabled() or not rows:
        return
    with pool.connection(timeout=0.2) as conn:
        conn.execute("SET LOCAL statement_timeout='500ms'")
        # Consent checked at persistence time, including queued events after opt-out.
        with conn.cursor() as cursor:
            cursor.executemany('''INSERT INTO usage_events(id,visitor,event,source,value,status,duration_ms)
                SELECT %s,s.subject,%s,%s,%s,%s,%s FROM usage_preferences s
                WHERE s.subject=%s AND s.analytics_enabled AND NOT EXISTS (SELECT 1 FROM anonymous_sessions a WHERE a.subject=s.subject AND a.expires_at<=now())
                ON CONFLICT (id) DO NOTHING''',
                [(r['id'],r['event'],r['source'],r.get('value'),r.get('status'),r.get('duration_ms'),r['visitor']) for r in rows])


def retention_days():
    return max(1, min(365, int(os.environ.get('SARA_ANALYTICS_RETENTION_DAYS', '90'))))


def purge(pool):
    days = retention_days()
    with pool.connection() as conn:
        conn.execute("DELETE FROM usage_events WHERE occurred_at<now()-%s*interval '1 day'", (days,))
        # Document expiry is handled independently, including when metrics are disabled.


class EventSink:
    """Bounded best-effort queue; analytics cannot block document operations."""
    def __init__(self, pool):
        self.pool = pool
        self.queue = asyncio.Queue(maxsize=1000)
        self.task = None
        self.last_purge = None
        self.last_expiry = 0

    def emit(self, visitor, event, source='server', status=None, duration_ms=None):
        if not enabled():
            return
        try:
            self.queue.put_nowait(dict(id=uuid4(),visitor=visitor,event=event,source=source,
                                      status=status,duration_ms=duration_ms))
        except asyncio.QueueFull:
            pass

    async def run(self):
        while True:
            rows = []
            try:
                rows.append(await asyncio.wait_for(self.queue.get(), timeout=1))
            except asyncio.TimeoutError:
                pass
            while not self.queue.empty() and len(rows)<50:
                rows.append(self.queue.get_nowait())
            # Expiry remains independent of analytics availability and consent.
            if time.monotonic()-self.last_expiry >= 60:
                try:
                    await asyncio.to_thread(purge_expired,self.pool)
                except Exception:
                    log.warning('No se pudieron limpiar espacios caducados; se reintentará en un minuto.')
                self.last_expiry=time.monotonic()
            try:
                await asyncio.to_thread(record_events, self.pool, rows)
                today = datetime.now(timezone.utc).date()
                if self.last_purge != today:
                    await asyncio.to_thread(purge, self.pool)
                    self.last_purge = today
            except Exception:
                log.warning('No se pudo guardar un lote de métricas; no afecta los documentos.')
            finally:
                for _ in rows:
                    self.queue.task_done()

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def close(self):
        try:
            await asyncio.wait_for(self.queue.join(), timeout=2)
        except asyncio.TimeoutError:
            pass
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass


class BrowserEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    event: Literal['library_view','page_view','citation_open','theme_changed','search_mode_changed','answer_feedback']
    value: Literal['light','dark','name','text','hybrid','positive','negative'] | None = None

    @model_validator(mode='after')
    def valid_value(self):
        if self.value not in BROWSER_VALUES[self.event]:
            raise ValueError('Valor no permitido para este evento.')
        return self


class EventBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    events: list[BrowserEvent] = Field(min_length=1, max_length=20)


class Preference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


def routes(subject_dependency):
    router = APIRouter()

    @router.get('/api/analytics/preferences')
    def preference(request: Request, owner=Depends(subject_dependency)):
        with request.app.state.pool.connection() as conn:
            row = conn.execute('SELECT analytics_enabled FROM usage_preferences WHERE subject=%s', (owner,)).fetchone()
        return {'enabled': bool(enabled() and row and row['analytics_enabled']), 'available': enabled(), 'retention_days': retention_days()}

    @router.post('/api/analytics/preferences')
    def update_preference(body: Preference, request: Request, owner=Depends(subject_dependency)):
        with request.app.state.pool.connection() as conn:
            conn.execute('UPDATE usage_preferences SET analytics_enabled=%s WHERE subject=%s', (body.enabled,owner))
        return {'enabled': enabled() and body.enabled}

    @router.post('/api/events', status_code=202)
    def receive(body: EventBatch, request: Request, owner=Depends(subject_dependency)):
        if not enabled():
            return {'accepted': False}
        with request.app.state.pool.connection() as conn:
            # Fixed per-visitor quota; no client IP or device fingerprint is needed.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,1))', (str(owner),))
            recent = conn.execute("SELECT count(*) AS n FROM usage_events WHERE visitor=%s AND source='browser' AND occurred_at>now()-interval '1 minute'", (owner,)).fetchone()['n']
            if recent+len(body.events)>120:
                raise HTTPException(429, 'Demasiados eventos.')
            for event in body.events:
                conn.execute('''INSERT INTO usage_events(id,visitor,event,source,value)
                    SELECT %s,subject,%s,'browser',%s FROM usage_preferences s
                    WHERE subject=%s AND analytics_enabled AND NOT EXISTS (SELECT 1 FROM anonymous_sessions a WHERE a.subject=s.subject AND a.expires_at<=now())
                    ON CONFLICT (id) DO NOTHING''', (event.id,event.event,event.value,owner))
        return {'accepted': True}

    return router
