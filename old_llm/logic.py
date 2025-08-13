# llm_service/logic.py

import requests
import json
import os
from typing import Tuple, List, Dict, Any, Optional

# --- Helper functions that might be needed ---

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

def get_pages_text_by_numbers(full_text: str, pages_list: list) -> str:
    """
    Extrae el texto de páginas específicas de un texto completo previamente paginado.
    """
    import re
    pattern = r'--- Página\s+(\d+)\s+---'
    parts = re.split(pattern, full_text)
    pages = []
    for i in range(1, len(parts), 2):
        try:
            page_num = int(parts[i])
            page_text = parts[i + 1].strip() if i + 1 < len(parts) else ""
            pages.append((page_num, page_text))
        except (ValueError, IndexError):
            continue

    selected_texts = []
    for pnum, ptext in pages:
        if pnum in pages_list:
            selected_texts.append(f"--- Página {pnum} ---\n{ptext}")
    return "\n".join(selected_texts)

# --- Configuración Mejorada ---
INFERENCE_SERVER_URL = os.getenv("SMARTDOC_LM_URL", "http://localhost:1234/v1/chat/completions")
MODEL_NAME = os.getenv("SMARTDOC_MODEL", "llama-3.2-3b-instruct")
REQUEST_TIMEOUT = float(os.getenv("SMARTDOC_LM_TIMEOUT", "60"))
HEADERS = {"Content-Type": "application/json"}

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
            "main_category": {"type": "string", "description": "La categoría principal, ej: 'Ciencia de Datos'"},
            "sub_category": {"type": "string", "description": "La subcategoría específica, ej: 'Procesamiento de Lenguaje'"}
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
        f"Resume de forma concisa el contenido de las páginas {start_page} a {end_page}. Extrae los puntos, ideas y conclusiones clave.\n\nTexto:\n{combined_text}"
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
    """Genera una respuesta basada en un contexto y una pregunta."""
    prompt = (
        "Basándote únicamente en el siguiente contexto, responde a la pregunta del usuario de forma clara y concisa. Si la respuesta no se encuentra en el contexto, indica que no tienes suficiente información.\n\n"
        f"## Contexto:\n{context}\n\n"
        f"## Pregunta: {question}\n\n"
        "## Respuesta:"
    )
    result = call_llm(prompt)
    return result.get("content", f"Error al generar la respuesta: {result.get('error', 'desconocido')}")


def get_relevant_pages_from_multiple_docs(summaries: List[Dict], question: str) -> Dict:
    """Determina qué páginas de qué documentos son relevantes para una pregunta."""
    prompt = (
        "Analiza los resúmenes de varios documentos y una pregunta. Determina qué páginas de qué documentos son las más relevantes.\n"
        "REGLA MUY IMPORTANTE: El 'doc_id' DEBE ser el índice de la lista de resúmenes (empezando en 0).\n\n"
        f"## Pregunta: {question}\n\n"
        f"## Resúmenes de Documentos (doc_id 0, 1, ...):\n{json.dumps(summaries, indent=2, ensure_ascii=False)}\n\n"
        "Responde únicamente con un JSON que contenga una lista de documentos relevantes."
    )
    schema = {
        "type": "object",
        "properties": {
            "relevant_documents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "integer", "description": "El ID del documento (índice 0)."},
                        "pages": {"type": "array", "items": {"type": "integer"}}
                    }, "required": ["doc_id", "pages"]
                }
            }
        }, "required": ["relevant_documents"]
    }
    
    result = call_llm(prompt, schema=schema, temperature=0.2, max_tokens=1000)

    # --- INICIO DE LA CORRECCIÓN ---
    # Validación y corrección de los IDs de documento devueltos por el LLM.
    if "relevant_documents" in result:
        validated_docs = []
        for doc in result["relevant_documents"]:
            doc_id = doc.get("doc_id", -1)
            # Si el doc_id está fuera de los límites, lo ignoramos para evitar errores.
            if 0 <= doc_id < len(summaries):
                validated_docs.append(doc)
        result["relevant_documents"] = validated_docs
    # --- FIN DE LA CORRECCIÓN ---

    if "error" in result or not result.get("relevant_documents"):
        return {"relevant_documents": [{"doc_id": idx, "pages": list(range(1, 6))} for idx in range(len(summaries))]}
    return result


def get_context_from_multiple_docs(relevant_docs: Dict, doc_paths: List[str]) -> str:
    """Extrae el texto de las páginas relevantes de múltiples archivos de texto."""
    context_pieces = []
    for doc_info in relevant_docs.get("relevant_documents", []):
        doc_id = doc_info.get("doc_id", -1)
        pages = doc_info.get("pages", [])
        if 0 <= doc_id < len(doc_paths) and pages:
            txt_path = doc_paths[doc_id]
            doc_name = os.path.basename(txt_path)
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    full_text = f.read()
                pages_text = get_pages_text_by_numbers(full_text, pages)
                if pages_text.strip():
                    context_pieces.append(f"--- INICIO DEL DOCUMENTO: {doc_name} (Páginas: {', '.join(map(str, pages))}) ---\n{pages_text}\n--- FIN DEL DOCUMENTO: {doc_name} ---")
            except FileNotFoundError:
                context_pieces.append(f"--- ERROR: No se encontró el archivo {doc_name} en la ruta {txt_path} ---")
            except Exception as e:
                context_pieces.append(f"--- ERROR: No se pudo leer el archivo {doc_name}: {e} ---")
    return "\n\n".join(context_pieces)


def chat_with_multiple_docs(doc_summaries: List[Dict], doc_paths: List[str], question: str) -> str:
    """Función orquestadora para chatear con múltiples documentos."""
    relevant_docs = get_relevant_pages_from_multiple_docs(doc_summaries, question)
    if "error" in relevant_docs:
        return f"Error al determinar las páginas relevantes: {relevant_docs['error']}"
    
    context = get_context_from_multiple_docs(relevant_docs, doc_paths)
    if not context.strip():
        return "No se pudo encontrar información relevante en los documentos seleccionados para responder a tu pregunta."
    
    answer = chat_with_context(context, question)
    
    sources_info = []
    for doc_info in relevant_docs.get("relevant_documents", []):
        doc_id = doc_info.get("doc_id", -1)
        pages = doc_info.get("pages", [])
        if 0 <= doc_id < len(doc_paths) and pages:
            doc_name = os.path.basename(doc_paths[doc_id].replace(".md", ".pdf"))
            sources_info.append(f"{doc_name} (págs: {', '.join(map(str, pages))})")
    
    if sources_info:
        answer += "\n\n**Fuentes consultadas:**\n- " + "\n- ".join(sources_info)
    
    return answer