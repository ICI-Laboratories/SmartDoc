# llm_service/api.py
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Tuple

# Importamos la lógica que movimos
import logic

app = FastAPI(
    title="LLM Service",
    description="Un microservicio para interactuar con el modelo de lenguaje."
)

# --- Modelos de Datos (Pydantic) para validar las peticiones ---

class ClassifyRequest(BaseModel):
    text: str
    categories: Dict

class SummarizeRequest(BaseModel):
    pages: List[Tuple[int, str]]

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


# --- Endpoints de la API ---

@app.post("/classify", summary="Clasifica un texto")
def classify_text(request: ClassifyRequest) -> Dict:
    """Clasifica el texto proporcionado en una categoría y subcategoría."""
    snippet = logic.get_classification_snippet(request.text)
    # Nota: output_folder se usa para obtener categorías existentes, así que lo pasamos como un diccionario.
    main_cat, sub_cat = logic.classify_text_with_lmstudio(snippet, request.categories)
    return {"main_category": main_cat, "sub_category": sub_cat}

@app.post("/summarize_chunk", summary="Resume un bloque de páginas")
def summarize_chunk(request: SummarizeRequest) -> Dict:
    """Genera un resumen para un conjunto de páginas."""
    summary_data = logic.summarize_chunk_with_lmstudio(request.pages)
    if "error" in summary_data:
        raise HTTPException(status_code=500, detail=summary_data["error"])
    return summary_data

@app.post("/chat_with_context", summary="Chatea con contexto")
def chat_with_context(request: ChatRequest) -> Dict:
    """Obtiene una respuesta del LLM basada en un contexto y una pregunta."""
    answer = logic.chat_with_context(request.context, request.question)
    return {"answer": answer}

@app.post("/get_relevant_pages", summary="Encuentra páginas relevantes en un resumen")
def get_relevant_pages(request: RelevantPagesRequest) -> Dict:
    """Usa un resumen jerárquico para encontrar las páginas más relevantes para una pregunta."""
    pages_info = logic.get_relevant_pages_from_summary(request.summary, request.question)
    if "error" in pages_info:
        raise HTTPException(status_code=500, detail=pages_info["error"])
    return pages_info

@app.post("/chat_with_multiple_docs", summary="Chatea con múltiples documentos")
def chat_with_multiple_docs(request: MultiDocChatRequest) -> Dict:
    """Orquesta la obtención de páginas, contexto y respuesta para múltiples documentos."""
    response = logic.chat_with_multiple_docs(request.summaries, request.doc_paths, request.question)
    return {"answer": response}