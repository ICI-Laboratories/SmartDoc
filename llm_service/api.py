# llm_service/api.py

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Tuple

# Importa el módulo con la lógica de negocio que creamos antes
import logic

# --- Modelos de Datos (Pydantic) para validar las peticiones ---
# Definen la estructura esperada para los datos JSON que recibirá cada endpoint.

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

# --- Inicialización de la Aplicación FastAPI ---

app = FastAPI(
    title="SmartDoc LLM Service",
    version="1.0",
    description="Microservicio para todas las interacciones con el modelo de lenguaje (clasificación, resumen, chat).",
)


# --- Endpoints de la API ---

@app.post("/classify", response_model=Dict, summary="Clasifica un fragmento de texto")
def classify_text(request: ClassifyRequest):
    """
    Recibe un texto y un diccionario de categorías existentes.
    Devuelve la categoría y subcategoría sugeridas por el LLM.
    """
    try:
        main_cat, sub_cat = logic.classify_text_with_lmstudio(request.text, request.categories)
        return {"main_category": main_cat, "sub_category": sub_cat}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error durante la clasificación: {str(e)}")


@app.post("/summarize_chunk", response_model=Dict, summary="Resume un bloque de páginas")
def summarize_chunk(request: SummarizeChunkRequest):
    """Recibe un conjunto de páginas y devuelve un resumen del bloque."""
    result = logic.summarize_chunk_with_lmstudio(request.pages)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@app.post("/generate_short_summary", response_model=Dict, summary="Genera un resumen corto para una página")
def generate_short_summary(request: ShortSummaryRequest):
    """Recibe el texto de una página y devuelve un resumen muy breve."""
    result = logic.generate_short_summary_with_lmstudio(request.page_text, request.doc_name, request.page_num)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@app.post("/chat_with_context", response_model=Dict, summary="Chatea con un contexto específico")
def chat_with_context(request: ChatRequest):
    """Recibe un contexto y una pregunta, y devuelve la respuesta del LLM."""
    answer = logic.chat_with_context(request.context, request.question)
    return {"answer": answer}


@app.post("/get_relevant_pages", response_model=Dict, summary="Encuentra páginas relevantes de un documento")
def get_relevant_pages(request: RelevantPagesRequest):
    """Usa un resumen para determinar las páginas más relevantes para responder una pregunta."""
    result = logic.get_relevant_pages_from_summary(request.summary, request.question)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@app.post("/chat_with_multiple_docs", response_model=Dict, summary="Orquesta un chat con múltiples documentos")
def chat_with_multiple_docs(request: MultiDocChatRequest):
    """
    Recibe resúmenes, rutas de archivos y una pregunta.
    Orquesta todo el proceso de encontrar contexto y generar una respuesta.
    """
    answer = logic.chat_with_multiple_docs(request.summaries, request.doc_paths, request.question)
    return {"answer": answer}

@app.get("/", summary="Endpoint de estado")
def read_root():
    """Endpoint simple para verificar que el servicio está en funcionamiento."""
    return {"status": "LLM Service is running"}