import requests
import json
import streamlit as st
from docs import get_classification_snippet, get_existing_categories
from typing import Tuple, List, Dict

INFERENCE_SERVER_URL = "http://localhost:1234/v1/chat/completions"
MODEL_NAME = "meta-llama-3.1-8b-instruct"

headers = {'Content-Type': 'application/json'}

def call_llm(prompt: str, schema: dict = None, temperature: float = 0.7, max_tokens: int = 300) -> dict:
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False
    }

    if schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": schema}

    response = requests.post(INFERENCE_SERVER_URL, headers=headers, json=payload)

    if response.ok:
        content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON response", "content": content}
    else:
        return {"error": f"HTTP Error {response.status_code}", "details": response.text}

def classify_text_with_lmstudio(text: str, output_folder: str) -> Tuple[str, str]:
    categories_dict = get_existing_categories(output_folder)
    snippet = get_classification_snippet(text)

    prompt = (
        "Clasifica este texto en categorías existentes o nuevas. No uses nombres numéricos ni cortos.\n\n"
        f"Categorías existentes:\n{json.dumps(categories_dict, indent=4)}\n\n"
        f"Texto:\n{snippet}"
    )

    schema = {
        "type": "object",
        "properties": {
            "main_category": {"type": "string"},
            "sub_category": {"type": "string"}
        },
        "required": ["main_category", "sub_category"]
    }

    result = call_llm(prompt, schema=schema)
    main_category = result.get("main_category", "Sin_Clasificar")
    sub_category = result.get("sub_category", "Sin_Subcategoria")

    return main_category, sub_category

def summarize_chunk_with_lmstudio(pages: List[Tuple[int, str]]) -> Dict:
    if not pages:
        return {}

    start_page, end_page = pages[0][0], pages[-1][0]
    combined_text = "\n".join(f"--- Página {pnum} ---\n{ptext}" for pnum, ptext in pages)

    prompt = (
        f"Resume brevemente las páginas {start_page}-{end_page}. Incluye los puntos clave.\n\n{combined_text}"
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

def chat_with_context(context: str, question: str) -> str:
    prompt = (
        f"Contexto:\n{context}\n\n"
        f"Pregunta: {question}\n"
        "Respuesta:"
    )

    result = call_llm(prompt)
    return result.get("content", result.get("error", "Sin respuesta válida"))

def generate_short_summary_with_lmstudio(page_text: str, doc_name: str, page_num: int) -> Dict:
    prompt = (
        f"Documento: {doc_name}, página {page_num}. Resume en 10-20 palabras el tema principal y conclusiones.\n\n{page_text}"
    )

    result = call_llm(prompt)
    return {
        "doc_name": doc_name,
        "page_number": page_num,
        "short_summary": result.get("content", result.get("error", "Sin resumen válido"))
    }

def get_relevant_pages_from_summary(summary: Dict, question: str) -> Dict:
    prompt = (
        f"Usa el siguiente resumen para indicar qué páginas responden a la pregunta.\n\n"
        f"Resumen:\n{json.dumps(summary, indent=2)}\n\nPregunta: {question}\n"
        "Devuelve sólo JSON:\n{\"document\": \"nombre\", \"pages\": [páginas]}"
    )

    result = call_llm(prompt)

    if "pages" in result:
        result["pages"] = [int(p) for p in result["pages"] if str(p).isdigit()]

    return result