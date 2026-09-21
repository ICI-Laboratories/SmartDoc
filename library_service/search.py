"""Persistent lexical retrieval + optional exact vector retrieval fused with RRF."""
import math
import os
import httpx


def model_name():
    return os.environ.get('SARA_EMBEDDING_MODEL', '')


def embed(texts, timeout=8):
    model = model_name()
    if not model:
        return None
    response = httpx.post(os.environ.get('SARA_OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/embed',
                          json={'model': model, 'input': texts, 'truncate': False}, timeout=timeout)
    response.raise_for_status()
    vectors = response.json()['embeddings']
    if len(vectors) != len(texts) or any(len(v) != 1024 or not all(math.isfinite(x) for x in v)
                                       or not any(v) for v in vectors):
        raise ValueError('Modelo incompatible: se requieren vectores finitos de 1024 dimensiones.')
    return vectors


def vector_literal(values):
    return '[' + ','.join(str(float(x)) for x in values) + ']'


def retrieve(pool, subject, query, document_id=None, semantic=False, limit=20, document_ids=None):
    vector = None
    mode = 'text_fallback' if semantic and not model_name() else 'text'
    if semantic and model_name():
        try:
            vector = embed([query])[0]
            mode = 'hybrid'
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            mode = 'text_fallback'
    scope = 'd.subject = %(subject)s AND (%(doc)s::uuid IS NULL OR d.id = %(doc)s::uuid) AND (%(ids)s::uuid[] IS NULL OR d.id=ANY(%(ids)s::uuid[]))'
    params = {'subject': subject, 'doc': document_id, 'q': query, 'model': model_name(), 'ids': document_ids}
    with pool.connection() as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        lexical = conn.execute(f'''
            SELECT d.id, d.name, c.page, c.ordinal, c.content,
                   ts_rank_cd(c.terms, websearch_to_tsquery('simple', %(q)s)) AS score
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE {scope} AND c.terms @@ websearch_to_tsquery('simple', %(q)s)
            ORDER BY score DESC, d.id, c.ordinal LIMIT 60''', params).fetchall()
        semantic_rows = []
        if vector is not None:
            params['v'] = vector_literal(vector)
            semantic_rows = conn.execute(f'''
                SELECT d.id, d.name, c.page, c.ordinal, c.content
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE {scope} AND d.embedding_model=%(model)s AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> %(v)s::vector, d.id, c.ordinal LIMIT 60''', params).fetchall()
    combined = {}
    for results in (lexical, semantic_rows):
        for rank, row in enumerate(results, 1):
            key = (str(row['id']), row['ordinal'])
            if key not in combined:
                combined[key] = {**row, 'score': 0.0}
            combined[key]['score'] += 1 / (60 + rank)
    return {'mode': mode, 'items': sorted(combined.values(), key=lambda r: r['score'], reverse=True)[:limit]}
