import logging
from pathlib import Path
from typing import List, Dict, Tuple, Iterable
import re
import heapq

import numpy as np
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

_MIN_COMBINED_SCORE = 0.30
_DEFAULT_ALPHA = 0.5
_EMBEDDING_MODEL_NAME = "BAAI/bge-large-en-v1.5"

def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)

def _safe_normalize(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    maxv = float(np.max(arr))
    if maxv <= 0:
        return np.zeros_like(arr, dtype=np.float32)
    return (arr / maxv).astype(np.float32)

def _safe_semantic_scale(arr: np.ndarray) -> np.ndarray:
    return ((arr + 1.0) * 0.5).astype(np.float32)

def _load_npz(vector_path: Path):
    try:
        data = np.load(vector_path, allow_pickle=True, mmap_mode="r")
        if "chunks" not in data or "embeddings" not in data:
            raise ValueError("El archivo NPZ no contiene 'chunks' ni 'embeddings'")
        chunks = np.array(data["chunks"], dtype=object)
        embeddings = np.array(data["embeddings"])
        if chunks.size == 0 or embeddings.size == 0:
            raise ValueError("NPZ sin datos")
        if len(chunks) != embeddings.shape[0]:
            raise ValueError("Desalineación entre 'chunks' y 'embeddings'")
        return chunks, embeddings
    except Exception as e:
        logger.error(f"Error cargando NPZ {vector_path}: {e}", exc_info=True)
        raise

def _l2_normalize(mat: np.ndarray, axis: int = -1, eps: float = 1e-12) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=axis, keepdims=True)
    norms = np.maximum(norms, eps)
    return (mat / norms).astype(np.float32)

try:
    EMBEDDING_MODEL = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    logger.info(f"Modelo de SentenceTransformer '{_EMBEDDING_MODEL_NAME}' cargado en llm_service.")
except Exception as e:
    logger.error(f"FATAL: No se pudo cargar el modelo de SentenceTransformer: {e}")
    EMBEDDING_MODEL = None


def hybrid_search_in_docs(
    doc_paths: List[str],
    query: str,
    top_k: int = 5,
    alpha: float = _DEFAULT_ALPHA
) -> List[Dict]:
    if not EMBEDDING_MODEL:
        return [{"error": "El modelo de embeddings no está disponible."}]
    if not query or not query.strip():
        return [{"error": "La consulta de búsqueda no puede estar vacía."}]

    alpha = float(min(max(alpha, 0.0), 1.0))

    query_vec = EMBEDDING_MODEL.encode([query], normalize_embeddings=True)
    query_vec = query_vec.astype(np.float32)
    q_tokens = _tokenize(query)

    unique_tokens = [t for t in dict.fromkeys(q_tokens) if len(t) >= 2]
    if unique_tokens:
        keyword_pattern = re.compile(r"\b(" + r"|".join(map(re.escape, unique_tokens)) + r")\b", flags=re.IGNORECASE | re.UNICODE)
    else:
        keyword_pattern = None

    heap: List[Tuple[float, str, str, int]] = []

    for md_path_str in doc_paths:
        md_path = Path(md_path_str)
        vector_path = md_path.with_suffix(".npz")
        doc_name = md_path.stem

        if not vector_path.exists():
            logger.warning(f"No existe vector_path para {doc_name}: {vector_path}")
            continue

        try:
            chunks, embeddings = _load_npz(vector_path)
            if embeddings.dtype != np.float32:
                embeddings = embeddings.astype(np.float32, copy=False)

            tokenized_corpus = [_tokenize(ch) for ch in chunks.tolist()]
            bm25 = BM25Okapi(tokenized_corpus)

            if q_tokens:
                keyword_scores = np.asarray(bm25.get_scores(q_tokens), dtype=np.float32)
            else:
                keyword_scores = np.zeros((len(chunks),), dtype=np.float32)

            embeddings_norm = _l2_normalize(embeddings, axis=1)
            semantic_scores = (embeddings_norm @ query_vec[0]).astype(np.float32)

            norm_keyword = _safe_normalize(keyword_scores)
            norm_semantic = _safe_semantic_scale(semantic_scores)

            combined = (alpha * norm_semantic) + ((1.0 - alpha) * norm_keyword)

            for i, score in enumerate(combined):
                if score < _MIN_COMBINED_SCORE:
                    continue
                text = chunks[i].item() if isinstance(chunks[i], np.generic) else chunks[i]
                if not isinstance(text, str):
                    continue
                if keyword_pattern is not None:
                    kw_count = len(keyword_pattern.findall(text))
                else:
                    kw_count = 0

                item = (float(score), doc_name, text, int(kw_count))
                if len(heap) < top_k:
                    heapq.heappush(heap, item)
                else:
                    if item[0] > heap[0][0]:
                        heapq.heapreplace(heap, item)

        except Exception as e:
            logger.error(f"Error procesando {vector_path} para búsqueda: {e}", exc_info=True)
            continue

    if not heap:
        return []

    top_items = heapq.nlargest(top_k, heap, key=lambda x: x[0])
    return [
        {"score": score, "document": doc, "text": text, "keyword_count": kw}
        for score, doc, text, kw in top_items
    ]


def calculate_document_similarity(doc_paths: List[str]) -> Dict:
    if not EMBEDDING_MODEL:
        return {"error": "El modelo de embeddings no está disponible."}

    doc_vectors: List[np.ndarray] = []
    doc_names: List[str] = []
    errors: List[str] = []

    for md_path_str in doc_paths:
        md_path = Path(md_path_str)
        vector_path = md_path.with_suffix(".npz")
        doc_name = md_path.stem

        if not vector_path.exists():
            msg = f"No se encontró el archivo de vectores para: {doc_name}"
            errors.append(msg)
            logger.warning(msg)
            continue

        try:
            _, embeddings = _load_npz(vector_path)
            avg_vec = np.mean(embeddings, axis=0)
            if not np.all(np.isfinite(avg_vec)):
                raise ValueError("Vector promedio contiene valores no finitos")
            doc_vectors.append(avg_vec.astype(np.float32))
            doc_names.append(doc_name)
        except Exception as e:
            error_msg = f"Error al procesar {doc_name}: {e}"
            errors.append(error_msg)
            logger.error(error_msg, exc_info=True)

    if not doc_vectors:
        return {"error": "No se pudo calcular ningún vector de documento.", "details": errors}

    M = np.vstack(doc_vectors).astype(np.float32)
    M = _l2_normalize(M, axis=1)
    similarity_matrix = (M @ M.T).astype(np.float32)

    return {
        "doc_names": doc_names,
        "similarity_matrix": similarity_matrix.tolist(),
        "errors": errors
    }
