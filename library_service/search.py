"""Persistent lexical retrieval + optional exact vector retrieval fused with RRF."""
import hashlib
import math
import os
import httpx
from .gateway import gateway_headers, gateway_url


def model_name():
    return os.environ.get('SARA_EMBEDDING_MODEL', '').strip()


def query_instruction():
    """Optional retrieval instruction; document embeddings remain unprefixed."""
    return os.environ.get('SARA_EMBEDDING_QUERY_INSTRUCTION', '').strip()


def embedding_identity():
    """Separate old provider vectors and revisions, even at identical dimensions."""
    model = model_name()
    if not model:
        return ''
    revision = os.environ.get('SARA_EMBEDDING_REVISION', 'llama.cpp-v1').strip()
    if not revision:
        raise ValueError('Configura una revisión estable para los embeddings del gateway.')
    identity = f'gateway:{model}@{revision}'
    instruction = query_instruction()
    if instruction:
        # Include the template version as well as the instruction. Changing the
        # query profile must not silently reuse an index from another profile.
        digest = hashlib.sha256(f'query-v1\nInstruct: {instruction}\nQuery: '.encode('utf-8')).hexdigest()
        identity += f':query-sha256:{digest}'
    return identity


def embed(texts, timeout=8, *, is_query=False):
    model = model_name()
    if not model:
        return None
    embedding_identity()
    if not texts:
        return []
    instruction = query_instruction() if is_query else ''
    inputs = [f'Instruct: {instruction}\nQuery: {text}' for text in texts] if instruction else texts
    response = httpx.post(gateway_url('embeddings'), headers=gateway_headers(),
                          json={'model': model, 'input': inputs, 'encoding_format': 'float'}, timeout=timeout)
    response.raise_for_status()
    try:
        data = response.json()['data']
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError()
        vectors = [None] * len(texts)
        for item in data:
            index, vector = item['index'], item['embedding']
            if (type(index) is not int or not 0 <= index < len(texts) or vectors[index] is not None
                    or not isinstance(vector, list) or len(vector) != 1024
                    or not all(type(x) in (int, float) and math.isfinite(x) for x in vector)
                    or not any(vector)):
                raise ValueError()
            vectors[index] = vector
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Respuesta incompatible: se requieren vectores finitos de 1024 dimensiones e índices únicos por entrada.') from exc
    return vectors


def vector_literal(values):
    return '[' + ','.join(str(float(x)) for x in values) + ']'


def retrieve(pool, subject, query, document_id=None, semantic=False, limit=20, document_ids=None):
    vector = None
    mode = 'text_fallback' if semantic and not model_name() else 'text'
    if semantic and model_name():
        try:
            vector = embed([query], is_query=True)[0]
            mode = 'hybrid'
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            mode = 'text_fallback'
    scope = 'd.subject = %(subject)s AND (%(doc)s::uuid IS NULL OR d.id = %(doc)s::uuid) AND (%(ids)s::uuid[] IS NULL OR d.id=ANY(%(ids)s::uuid[]))'
    params = {'subject': subject, 'doc': document_id, 'q': query, 'model': embedding_identity() if vector is not None else '', 'ids': document_ids}
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
