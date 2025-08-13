# llm_service/logic/core.py

import os
import json
import requests
from typing import Tuple, List, Dict, Optional

# --- Configuración ---
INFERENCE_SERVER_URL = os.getenv("SMARTDOC_LM_URL", "http://localhost:1234/v1/chat/completions")
MODEL_NAME = os.getenv("SMARTDOC_MODEL", "llama-3.2-3b-instruct")
REQUEST_TIMEOUT = float(os.getenv("SMARTDOC_LM_TIMEOUT", "60"))
HEADERS = {"Content-Type": "application/json"}

# --- Helpers ---
def get_classification_snippet(text: str) -> str:
    """Extrae un fragmento de texto para la clasificación."""
    lower_text = text.lower()
    starts = []
    for kw in ("resumen", "introducción"):
        idx = lower_text.find(kw)
        if idx != -1:
            starts.append(idx)
    if starts:
        start_pos = min(starts)
        return text[start_pos:start_pos + 2000]
    return " ".join(text.split()[:2000])

# --- Core LLM Call ---
def call_llm(
    prompt: str,
    schema: Optional[dict] = None,
    temperature: float = 0.7,
    max_tokens: int = 3000,
) -> dict:
    """Llama al servidor OpenAI-compatible y maneja errores."""
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "strict": True, "schema": schema},
        }

    try:
        response = requests.post(
            INFERENCE_SERVER_URL, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return {"error": f"Connection error: {e}"}

    try:
        content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if schema:
            return json.loads(content)
        else:
            return {"content": content}
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        return {"error": f"Invalid response format from LLM: {e}", "response_text": response.text}

# --- Funciones principales ---
def classify_text_with_lmstudio(text: str, categories_dict: dict) -> Tuple[str, str]:
    """Clasifica texto usando el LLM."""
    snippet = get_classification_snippet(text)
    prompt = (
        "Clasifica este texto en categorías existentes o nuevas. No uses nombres numéricos ni cortos.\n\n"
        f"Categorías existentes:\n{json.dumps(categories_dict, indent=2)}\n\n"
        f"Texto a clasificar:\n{snippet}"
    )
    schema = {
        "type": "object",
        "properties": {
            "main_category": {"type": "string", "description": "La categoría principal"},
            "sub_category": {"type": "string", "description": "La subcategoría específica"}
        },
        "required": ["main_category", "sub_category"]
    }
    result = call_llm(prompt, schema=schema, temperature=0.3)
    main_category = result.get("main_category", "Sin_Clasificar").replace(" ", "_")
    sub_category = result.get("sub_category", "Sin_Subcategoria").replace(" ", "_")
    return main_category, sub_category

def summarize_chunk_with_lmstudio(pages: List[Tuple[int, str]]) -> Dict:
    """Resume un bloque de páginas."""
    if not pages:
        return {"error": "No pages provided to summarize."}
    start_page, end_page = pages[0][0], pages[-1][0]
    combined_text = "\n".join(f"--- Página {pnum} ---\n{ptext}" for pnum, ptext in pages)
    prompt = (
        f"Resume de forma concisa el contenido de las páginas {start_page} a {end_page}. "
        "Extrae los puntos, ideas y conclusiones clave.\n\nTexto:\n"
        f"{combined_text}"
    )
    schema = {
        "type": "object",
        "properties": {
            "start_page": {"type": "integer"},
            "end_page": {"type": "integer"},
            "summary": {"type": "string"}
        },
        "required": ["start_page", "end_page", "summary"]
    }
    return call_llm(prompt, schema=schema)

def generate_short_summary_with_lmstudio(page_text: str, doc_name: str, page_num: int) -> Dict:
    """Genera un resumen corto para una página."""
    prompt = (
        f"Genera un resumen muy breve (máx. 3 frases) para la página {page_num} del documento '{doc_name}'.\n\n"
        f"Texto:\n{page_text}"
    )
    schema = {
        "type": "object",
        "properties": {
            "page_num": {"type": "integer"},
            "short_summary": {"type": "string"}
        },
        "required": ["page_num", "short_summary"]
    }
    return call_llm(prompt, schema=schema)

def chat_with_context(context: str, question: str) -> str:
    """Genera una respuesta basada en un contexto y una pregunta."""
    prompt = (
        "Basándote únicamente en el siguiente contexto, responde a la pregunta del usuario de forma clara y concisa. "
        "Si la respuesta no se encuentra en el contexto, indica que no tienes suficiente información.\n\n"
        f"## Contexto:\n{context}\n\n"
        f"## Pregunta: {question}\n\n"
        "## Respuesta:"
    )
    result = call_llm(prompt)
    return result.get("content", f"Error al generar la respuesta: {result.get('error', 'desconocido')}")
