import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, validator
from typing import List, Dict, Tuple

from llm_service.logic.core import (
    classify_text_with_lmstudio,
    summarize_chunk_with_lmstudio,
    generate_short_summary_with_lmstudio,
    chat_with_context as chat_with_context_fn,
)
from llm_service.logic.docs import (
    get_relevant_pages_from_summary,
    chat_with_multiple_docs,
)
from llm_service.logic.analysis import hybrid_search_in_docs, calculate_document_similarity
from llm_service.path_scope import SubjectScopeError, paths_for_subject

logger = logging.getLogger(__name__)


def _base_dir() -> Path:
    configured = Path(os.getenv("SMARTREVIEW_BASE", "smartdoc_data"))
    if not configured.is_absolute():
        configured = Path(__file__).parent.parent / configured
    return configured.resolve()


def _subject_paths(paths: List[str], subject_header: str | None) -> List[str]:
    if not subject_header:
        raise HTTPException(status_code=401, detail="Identidad central requerida.")
    try:
        return paths_for_subject(_base_dir(), subject_header, paths)
    except SubjectScopeError as exc:
        raise HTTPException(
            status_code=403,
            detail="La ruta no pertenece al espacio de la identidad central.",
        ) from exc

class ClassifyRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Texto a clasificar")
    categories: Dict

class SummarizeChunkRequest(BaseModel):
    pages: List[Tuple[int, str]] = Field(..., description="Lista de (número_página, texto)")

class ShortSummaryRequest(BaseModel):
    page_text: str
    doc_name: str
    page_num: int = Field(..., ge=0)

class ChatRequest(BaseModel):
    context: str
    question: str

class RelevantPagesRequest(BaseModel):
    summary: Dict
    question: str

class MultiDocChatRequest(BaseModel):
    summaries: List[Dict]
    doc_paths: List[str]
    question: str

class SemanticSearchRequest(BaseModel):
    doc_paths: List[str] = Field(..., min_items=1, description="Rutas absolutas o relativas a archivos .md")
    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=50, description="Número máximo de resultados a devolver (1-50)")

    @validator("doc_paths", each_item=True)
    def _non_empty_paths(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Cada ruta de documento debe ser una cadena no vacía.")
        return v

class DocumentSimilarityRequest(BaseModel):
    doc_paths: List[str] = Field(..., min_items=2, description="Al menos dos documentos para comparar")

    @validator("doc_paths", each_item=True)
    def _non_empty_paths_sim(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Cada ruta de documento debe ser una cadena no vacía.")
        return v

tags_metadata = [
    {"name": "classification", "description": "Clasificación y resúmenes"},
    {"name": "chat", "description": "Chat con contexto y múltiples documentos"},
    {"name": "analysis", "description": "Búsqueda híbrida y similitud entre documentos"},
    {"name": "health", "description": "Estado del servicio"},
]

app = FastAPI(
    title="SmartReview LLM Service",
    version="1.2",
    description="Microservicio para interacciones con el LLM y análisis de documentos.",
    openapi_tags=tags_metadata,
)

@app.post("/classify", response_model=Dict, summary="Clasifica un fragmento de texto", tags=["classification"])
def classify_text_ep(request: ClassifyRequest):
    try:
        main_cat, sub_cat = classify_text_with_lmstudio(request.text, request.categories)
        return {"main_category": main_cat, "sub_category": sub_cat}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error durante la clasificación: {str(e)}")

@app.post("/summarize_chunk", response_model=Dict, summary="Resume un bloque de páginas", tags=["classification"])
def summarize_chunk_ep(request: SummarizeChunkRequest):
    result = summarize_chunk_with_lmstudio(request.pages)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.post("/generate_short_summary", response_model=Dict, summary="Genera un resumen corto para una página", tags=["classification"])
def generate_short_summary_ep(request: ShortSummaryRequest):
    result = generate_short_summary_with_lmstudio(request.page_text, request.doc_name, request.page_num)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.post("/chat_with_context", response_model=Dict, summary="Chatea con un contexto específico", tags=["chat"])
def chat_with_context_ep(request: ChatRequest):
    answer = chat_with_context_fn(request.context, request.question)
    return {"answer": answer}

@app.post("/get_relevant_pages", response_model=Dict, summary="Encuentra páginas relevantes de un documento", tags=["classification"])
def get_relevant_pages_ep(request: RelevantPagesRequest):
    result = get_relevant_pages_from_summary(request.summary, request.question)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.post("/chat_with_multiple_docs", response_model=Dict, summary="Orquesta un chat con múltiples documentos", tags=["chat"])
def chat_with_multiple_docs_ep(
    request: MultiDocChatRequest,
    subject_header: str | None = Header(default=None, alias="X-SmartDoc-Subject"),
):
    safe_paths = _subject_paths(request.doc_paths, subject_header)
    answer = chat_with_multiple_docs(request.summaries, safe_paths, request.question)
    return {"answer": answer}

@app.post("/analyze/semantic_search", response_model=Dict, summary="Búsqueda HÍBRIDA en documentos", tags=["analysis"])
def semantic_search_ep(
    request: SemanticSearchRequest,
    subject_header: str | None = Header(default=None, alias="X-SmartDoc-Subject"),
):
    try:
        query = request.query.strip()
        if not query:
            raise HTTPException(status_code=422, detail="La consulta de búsqueda no puede estar vacía.")

        results = hybrid_search_in_docs(
            doc_paths=_subject_paths(request.doc_paths, subject_header),
            query=query,
            top_k=request.top_k
        )
        if isinstance(results, list) and results and isinstance(results[0], dict) and "error" in results[0]:
            raise HTTPException(status_code=500, detail=results[0]["error"])

        return {"results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fatal durante la búsqueda híbrida: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

@app.post("/analyze/document_similarity", response_model=Dict, summary="Calcula la similitud entre documentos", tags=["analysis"])
def document_similarity_ep(
    request: DocumentSimilarityRequest,
    subject_header: str | None = Header(default=None, alias="X-SmartDoc-Subject"),
):
    try:
        results = calculate_document_similarity(
            doc_paths=_subject_paths(request.doc_paths, subject_header)
        )
        if isinstance(results, dict) and "error" in results:
            raise HTTPException(status_code=500, detail=results.get("error", "Error en cálculo de similitud"))
        return results
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fatal durante el cálculo de similitud: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

@app.get("/", summary="Endpoint de estado", tags=["health"])
def read_root():
    return {"status": "LLM Service is running"}
