"""Private-network session resolution for the gateway. No raw tokens in storage."""
import hashlib
import os
import secrets
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()
def session_seconds():
    return max(1, min(168, int(os.environ.get("SARA_EPHEMERAL_HOURS", "24")))) * 3600


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str | None = Field(default=None, max_length=128)
    create: bool = False


@router.post('/internal/anonymous/session')
def resolve_session(body: SessionRequest, request: Request):
    with request.app.state.pool.connection() as conn:
        if body.token:
            row = conn.execute('''SELECT subject, ephemeral,
                greatest(0,extract(epoch FROM expires_at-now())::integer) AS max_age
                FROM anonymous_sessions WHERE token_hash=%s AND expires_at>now()''',
                (hashlib.sha256(body.token.encode()).hexdigest(),)).fetchone()
            if row:
                return {**row, 'token': None}
        if not body.create:
            raise HTTPException(401, 'Sesión anónima no disponible.')
        token = secrets.token_urlsafe(32)
        subject = uuid4()
        conn.execute('''INSERT INTO anonymous_sessions(subject,token_hash,expires_at,ephemeral)
            VALUES (%s,%s,now()+%s*interval '1 second',true)''',
            (subject, hashlib.sha256(token.encode()).hexdigest(), session_seconds()))
        conn.execute('INSERT INTO usage_preferences(subject) VALUES (%s) ON CONFLICT DO NOTHING', (subject,))
        return {'subject': subject, 'token': token, 'max_age': session_seconds(), 'ephemeral': True}


@router.post('/internal/anonymous/end')
def end_session(body: SessionRequest, request: Request):
    if body.token:
        with request.app.state.pool.connection() as conn:
            conn.execute("UPDATE anonymous_sessions SET expires_at=now() WHERE token_hash=%s",
                (hashlib.sha256(body.token.encode()).hexdigest(),))
    purge_expired(request.app.state.pool)
    return {'ended': True}


def purge_expired(pool):
    """Delete only explicitly temporary spaces. Retry failures without losing ownership records."""
    from .db import original, storage
    with pool.connection() as conn:
        sessions = conn.execute("SELECT subject FROM anonymous_sessions WHERE ephemeral AND expires_at<=now() AND cleaned_at IS NULL FOR UPDATE SKIP LOCKED").fetchall()
        for session in sessions:
            owner = session['subject']
            docs = conn.execute('SELECT id FROM documents WHERE subject=%s', (owner,)).fetchall()
            for doc in docs:
                original(owner, doc['id']).unlink(missing_ok=True)
            conn.execute('DELETE FROM documents WHERE subject=%s', (owner,))
            folder=storage()/str(owner)
            if folder.exists():
                # Upload scratch files in this UUID-owned folder are also temporary.
                for item in folder.glob('*.upload'):
                    item.unlink(missing_ok=True)
                try:
                    folder.rmdir()
                except OSError:
                    pass
            conn.execute('DELETE FROM usage_preferences WHERE subject=%s', (owner,))
            conn.execute('UPDATE anonymous_sessions SET cleaned_at=now() WHERE subject=%s', (owner,))
        conn.execute("DELETE FROM anonymous_sessions WHERE cleaned_at<now()-interval '7 days'")
