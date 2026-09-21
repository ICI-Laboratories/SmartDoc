"""Durable PostgreSQL jobs with lease fencing and bounded child processes."""
import json
import logging
import multiprocessing
import os
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from .db import make_pool, original
from .search import embed, model_name, vector_literal

log = logging.getLogger(__name__)


def split_text(text, max_chars=1800, overlap=180):
    text = text.replace('\x00', '').strip()
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind(' ', start + max_chars // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            yield part
        if end == len(text):
            break
        start = end - overlap


def extract(path):
    import pymupdf
    chunks = []
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError('PDF protegido con contraseña; exporta una copia sin contraseña.')
        if len(pdf) > int(os.environ.get('SARA_MAX_PAGES', '1500')):
            raise ValueError('El PDF supera el límite de páginas.')
        for index, page in enumerate(pdf):
            text = page.get_text(sort=True)
            # OCR is decided per page. Partial OCR keeps native text on mixed pages.
            if page.get_images() or not text.strip():
                tp = page.get_textpage_ocr(language='spa+eng', dpi=150, full=not text.strip())
                text = page.get_text(textpage=tp, sort=True)
            for part in split_text(text):
                chunks.append({'page': index + 1, 'content': part})
        pages = len(pdf)
    if not chunks:
        raise ValueError('No se encontró texto legible en el documento.')
    return pages, chunks


def process_child(path, output, summary_requested=False, cached_input=None):
    try:
        cached = json.loads(Path(cached_input).read_text()) if cached_input else None
        if cached:
            pages, chunks = cached['pages'], cached['chunks']
            model, embedding_error = cached['model'], cached['embedding_error']
        else:
            pages, chunks = extract(path)
            model, embedding_error = None, None
        if model_name() and not cached:
            try:
                for offset in range(0, len(chunks), 16):
                    batch = chunks[offset:offset + 16]
                    vectors = embed([c['content'] for c in batch], timeout=60)
                    for chunk, vector in zip(batch, vectors):
                        chunk['embedding'] = vector
                model = model_name()
            except Exception:
                embedding_error = 'No se pudieron generar vectores. La búsqueda por texto sigue disponible.'
                for chunk in chunks:
                    chunk.pop('embedding', None)
        result = {'pages': pages, 'chunks': chunks, 'model': model, 'embedding_error': embedding_error}
        if summary_requested:
            from .enrichment import summarize
            try:
                result['summary'], result['category'] = summarize(chunks)
            except Exception:
                result['summary_error'] = 'No se pudo generar la síntesis. El original y la búsqueda siguen disponibles.'
    except Exception as exc:
        log.exception('Falló extracción')
        result = {'error': str(exc)[:500]}
    Path(output).write_text(json.dumps(result), encoding='utf-8')


def claim(pool):
    with pool.connection() as conn:
        # Expired final attempts must not remain permanently "processing".
        expired = conn.execute("""UPDATE jobs SET state='failed', lease_token=NULL, lease_until=NULL
            WHERE state='running' AND lease_until<now() AND attempts>=3 RETURNING document_id""").fetchall()
        for row in expired:
            conn.execute("UPDATE documents SET status='failed', error='Se agotaron los intentos de procesamiento.' WHERE id=%s", (row['document_id'],))
        token = uuid4()
        job = conn.execute("""WITH next AS (
            SELECT document_id FROM jobs WHERE attempts<3 AND
            ((state='queued' AND available_at<=now()) OR (state='running' AND lease_until<now()))
            ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1
        ) UPDATE jobs j SET state='running', attempts=attempts+1, lease_token=%s,
            lease_until=now()+interval '45 seconds' FROM next
            WHERE j.document_id=next.document_id RETURNING j.*""", (token,)).fetchone()
        if job:
            row = conn.execute("UPDATE documents SET status='processing', error=NULL WHERE id=%s RETURNING subject,summary_requested", (job['document_id'],)).fetchone()
            job['subject'] = row['subject']
            job['summary_requested'] = row['summary_requested']
        return job


def heartbeat(pool, job):
    with pool.connection() as conn:
        return conn.execute("""UPDATE jobs SET lease_until=now()+interval '45 seconds'
            WHERE document_id=%s AND lease_token=%s AND state='running'
            RETURNING document_id""", (job['document_id'], job['lease_token'])).fetchone() is not None


def _finish(pool, job, result):
    with pool.connection() as conn:
        current = conn.execute("""SELECT * FROM jobs WHERE document_id=%s AND lease_token=%s
            AND state='running' AND lease_until>now() FOR UPDATE""", (job['document_id'], job['lease_token'])).fetchone()
        if not current:
            return False  # A stale worker cannot overwrite the newer owner's results.
        if 'error' in result:
            state = 'queued' if current['attempts'] < 3 else 'failed'
            conn.execute("""UPDATE jobs SET state=%s, available_at=now()+interval '30 seconds',
                lease_until=NULL, lease_token=NULL WHERE document_id=%s""", (state, job['document_id']))
            conn.execute('UPDATE documents SET status=%s,error=%s WHERE id=%s', (state, result['error'], job['document_id']))
            return True
        conn.execute('DELETE FROM chunks WHERE document_id=%s', (job['document_id'],))
        with conn.cursor() as cursor:
            cursor.executemany('INSERT INTO chunks(document_id,ordinal,page,content,embedding) VALUES (%s,%s,%s,%s,%s::vector)',
                [(job['document_id'], i, c['page'], c['content'], vector_literal(c['embedding']) if c.get('embedding') else None)
                 for i, c in enumerate(result['chunks'])])
        conn.execute("""UPDATE documents SET status='ready',page_count=%s,error=NULL,
            embedding_model=%s,embedding_error=%s,summary=coalesce(%s,summary),
            category=coalesce(%s,category),summary_error=%s,summary_requested=false WHERE id=%s""",
            (result['pages'], result.get('model'), result.get('embedding_error'), result.get('summary'),
             result.get('category'), result.get('summary_error'), job['document_id']))
        conn.execute("UPDATE jobs SET state='done', lease_until=NULL,lease_token=NULL WHERE document_id=%s", (job['document_id'],))
        return True


def finish(pool, job, result):
    completed = _finish(pool, job, result)
    if completed:
        from .analytics import record_events
        event = ('processing_attempt_failed' if 'error' in result else
                 'summary_failed' if result.get('summary_error') else
                 'summary_completed' if job.get('summary_requested') else 'processing_completed')
        try:
            record_events(pool,[dict(id=uuid4(),visitor=job['subject'],event=event,source='worker',
                duration_ms=result.get('duration_ms'),status=500 if 'error' in result or result.get('summary_error') else 200)])
        except Exception:
            log.warning('No se pudo guardar la métrica del worker.')
    return completed


def run_job(pool, job):
    with tempfile.TemporaryDirectory(prefix='sara-worker-') as tmp:
        output = str(Path(tmp) / 'result.json')
        cached_input = None
        if job.get('summary_requested'):
            with pool.connection() as conn:
                doc = conn.execute('SELECT page_count,embedding_model,embedding_error FROM documents WHERE id=%s', (job['document_id'],)).fetchone()
                chunks = conn.execute('SELECT page,content,embedding::text AS vector FROM chunks WHERE document_id=%s ORDER BY ordinal', (job['document_id'],)).fetchall()
            if chunks:
                for chunk in chunks:
                    vector = chunk.pop('vector')
                    if vector:
                        chunk['embedding'] = json.loads(vector)
                cached_input = str(Path(tmp) / 'input.json')
                Path(cached_input).write_text(json.dumps({'pages': doc['page_count'], 'chunks': chunks,
                    'model': doc['embedding_model'], 'embedding_error': doc['embedding_error']}))
        child = multiprocessing.get_context('spawn').Process(target=process_child,
            args=(str(original(job['subject'], job['document_id'])), output, job.get('summary_requested',False), cached_input))
        child.start()
        started = time.monotonic()
        deadline = time.monotonic() + int(os.environ.get('SARA_JOB_TIMEOUT', '900'))
        try:
            while child.is_alive():
                child.join(timeout=10)
                if not heartbeat(pool, job):
                    return
                if time.monotonic() > deadline:
                    finish(pool, job, {'error': 'Se agotó el tiempo de procesamiento.'})
                    return
            result = json.loads(Path(output).read_text()) if Path(output).exists() else {'error': 'El proceso terminó inesperadamente.'}
            result['duration_ms'] = min(3600000,round((time.monotonic()-started)*1000))
            finish(pool, job, result)
        finally:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
                if child.is_alive():
                    child.kill()
                    child.join()


def main():
    logging.basicConfig(level=logging.INFO)
    pool = make_pool()
    pool.open()
    pool.wait()
    try:
        while True:
            try:
                job = claim(pool)
                if job:
                    run_job(pool, job)
                else:
                    time.sleep(2)
            except Exception:
                log.exception('Worker no pudo completar el ciclo; el lease permitirá recuperar el trabajo')
                time.sleep(5)
    finally:
        pool.close()


if __name__ == '__main__':
    main()
