# llm_service/api.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Tuple

# --- Imports explícitos desde los módulos de lógica ---
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

# --- Modelos de Datos ---
class ClassifyRequest(BaseModel):
    text: str
    categories: Dict

class SummarizeChunkRequest(BaseModel):
    pages: List[Tuple[int, str]]

class ShortSummaryRequest(BaseModel):
    page_text: str
    doc_name: str
    page_num: int

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

# --- Inicialización de la Aplicación ---
app = FastAPI(
    title="SmartDoc LLM Service",
    version="1.0",
    description="Microservicio para todas las interacciones con el modelo de lenguaje (clasificación, resumen, chat).",
)

# --- Endpoints ---

@app.post("/classify", response_model=Dict, summary="Clasifica un fragmento de texto")
def classify_text_ep(request: ClassifyRequest):
    try:
        main_cat, sub_cat = classify_text_with_lmstudio(request.text, request.categories)
        return {"main_category": main_cat, "sub_category": sub_cat}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error durante la clasificación: {str(e)}")

@app.post("/summarize_chunk", response_model=Dict, summary="Resume un bloque de páginas")
def summarize_chunk_ep(request: SummarizeChunkRequest):
    result = summarize_chunk_with_lmstudio(request.pages)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.post("/generate_short_summary", response_model=Dict, summary="Genera un resumen corto para una página")
def generate_short_summary_ep(request: ShortSummaryRequest):
    result = generate_short_summary_with_lmstudio(request.page_text, request.doc_name, request.page_num)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.post("/chat_with_context", response_model=Dict, summary="Chatea con un contexto específico")
def chat_with_context_ep(request: ChatRequest):
    answer = chat_with_context_fn(request.context, request.question)
    return {"answer": answer}

@app.post("/get_relevant_pages", response_model=Dict, summary="Encuentra páginas relevantes de un documento")
def get_relevant_pages_ep(request: RelevantPagesRequest):
    result = get_relevant_pages_from_summary(request.summary, request.question)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.post("/chat_with_multiple_docs", response_model=Dict, summary="Orquesta un chat con múltiples documentos")
def chat_with_multiple_docs_ep(request: MultiDocChatRequest):
    answer = chat_with_multiple_docs(request.summaries, request.doc_paths, request.question)
    return {"answer": answer}

@app.get("/", summary="Endpoint de estado")
def read_root():
    """Endpoint simple para verificar que el servicio está en funcionamiento."""
    return {"status": "LLM Service is running"}
