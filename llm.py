# llm.py

import requests
import json
import os
from typing import Tuple, List, Dict, Any, Optional
from docs import (
    get_classification_snippet,
    get_existing_categories,
    get_pages_text_by_numbers,
    extract_pages_from_text,  # <- necesario para el fallback en get_context_from_multiple_docs
)

# --- Configuración Mejorada ---
# Se utilizan variables de entorno para mayor flexibilidad.
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
    """
    Llama al servidor OpenAI-compatible de LM Studio.
    - Si `schema` se proporciona, exige una respuesta JSON y la parsea.
    - Si no, devuelve texto en `{"content": "..."}`
    - Maneja errores de conexión y respuestas no válidas.
    """
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
            "json_schema": {
                "name": "response",
                "strict": True,  # booleano, no string
                "schema": schema,
            },
        }

    try:
        response = requests.post(
            INFERENCE_SERVER_URL, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT
        )
    except Exception as e:
        # Error de conexión o timeout
        return {"error": f"Connection error: {e}"}

    if not response.ok:
        # Error en la respuesta del servidor (HTTP 4xx, 5xx)
        return {"error": f"HTTP Error {response.status_code}", "details": response.text}

    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    
    if schema:
        # Si se esperaba un JSON, intentar decodificarlo
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON response", "content": content}
    else:
        # Si no se esperaba JSON, devolver el contenido directamente
        return {"content": content}


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
    """
    Usa el resumen jerárquico para determinar qué páginas son relevantes para la pregunta.
    """
    prompt = (
        f"Usa el siguiente resumen para indicar qué páginas responden a la pregunta.\n\n"
        f"Resumen:\n{json.dumps(summary, indent=2, ensure_ascii=False)}\n\nPregunta: {question}\n"
        "Devuelve sólo JSON:\n{\"document\": \"nombre\", \"pages\": [páginas]}"
    )

    schema = {
        "type": "object",
        "properties": {
            "document": {"type": "string"},
            "pages": {
                "type": "array",
                "items": {"type": "integer"}
            }
        },
        "required": ["document", "pages"]
    }

    result = call_llm(prompt, schema=schema, temperature=0.3)

    if "pages" in result:
        result["pages"] = [int(p) for p in result["pages"] if str(p).isdigit()]

    return result


def get_relevant_pages_from_multiple_docs(summaries: List[Dict], question: str) -> Dict:
    """
    Determina qué páginas de qué documentos son relevantes para la pregunta.
    """
    docs_summary = []
    for idx, summary in enumerate(summaries):
        doc_info = {
            "doc_id": idx,
            "title": summary.get("title", f"Documento {idx}"),
            "blocks": []
        }
        
        blocks = summary.get("blocks", [])
        if blocks:
            for block in blocks:
                if "page" in block:
                    page_num = block.get("page", 0)
                    summary_text = block.get("small_summary", "")
                    doc_info["blocks"].append({
                        "page": page_num,
                        "summary": summary_text
                    })
                else:
                    start_page = block.get("start_page", 0)
                    end_page = block.get("end_page", 0)
                    summary_text = block.get("summary", "")
                    if start_page > 0 or end_page > 0 or summary_text:
                        doc_info["blocks"].append({
                            "start_page": start_page,
                            "end_page": end_page,
                            "summary": summary_text
                        })
        
        docs_summary.append(doc_info)
    
    all_empty = all(len(doc["blocks"]) == 0 or all(not block.get("summary", "") for block in doc["blocks"]) 
                    for doc in docs_summary)
    
    if all_empty:
        return {
            "relevant_documents": [
                {
                    "doc_id": idx,
                    "pages": list(range(1, 11))  # Asumimos hasta 10 páginas por defecto
                }
                for idx in range(len(summaries))
            ]
        }
    
    prompt = (
        f"Analiza los resúmenes de los siguientes documentos para determinar qué páginas de qué documentos "
        f"contienen información relevante para responder a esta pregunta:\n\n"
        f"Pregunta: {question}\n\n"
        f"Resúmenes:\n{json.dumps(docs_summary, indent=2, ensure_ascii=False)}\n\n"
        f"Devuelve una lista de documentos y páginas específicas que deben consultarse. "
        f"Solo incluye las páginas más relevantes que contengan información para responder la pregunta."
    )
    
    schema = {
        "type": "object",
        "properties": {
            "relevant_documents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "integer"},
                        "pages": {
                            "type": "array",
                            "items": {"type": "integer"}
                        }
                    },
                    "required": ["doc_id", "pages"]
                }
            }
        },
        "required": ["relevant_documents"]
    }
    
    result = call_llm(prompt, schema=schema, temperature=0.3, max_tokens=500)
    
    if "relevant_documents" not in result or "error" in result:
        return {
            "relevant_documents": [
                {
                    "doc_id": idx,
                    "pages": list(range(1, 6))  # Primeras 5 páginas de cada documento
                }
                for idx in range(len(summaries))
            ]
        }
    
    return result


def get_context_from_multiple_docs(relevant_docs: Dict, doc_paths: List[str]) -> str:
    """
    Extrae el texto de las páginas relevantes de múltiples documentos.
    """
    context_pieces = []
    
    for doc_info in relevant_docs.get("relevant_documents", []):
        doc_id = doc_info.get("doc_id", 0)
        pages = doc_info.get("pages", [])
        
        if 0 <= doc_id < len(doc_paths):
            txt_path = doc_paths[doc_id]
            doc_name = os.path.basename(txt_path)
            
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    full_text = f.read()
                
                pages_text = get_pages_text_by_numbers(full_text, pages)
                
                # Si no se encontró texto, intentar un fallback con las primeras páginas
                if not pages_text.strip():
                    # Lógica de fallback mejorada
                    page_count = len(extract_pages_from_text(full_text))
                    fallback_pages = list(range(1, min(6, page_count + 1)))
                    pages_text = get_pages_text_by_numbers(full_text, fallback_pages)
                    
                    if pages_text.strip():
                        context_pieces.append(f"--- DOCUMENTO: {doc_name} (páginas respaldo: {', '.join(map(str, fallback_pages))}) ---\n{pages_text}")
                    else:
                        # Como último recurso, tomar todo el documento
                        context_pieces.append(f"--- DOCUMENTO: {doc_name} (documento completo) ---\n{full_text}")
                else:
                    context_pieces.append(f"--- DOCUMENTO: {doc_name} (páginas: {', '.join(map(str, pages))}) ---\n{pages_text}")
            except Exception as e:
                context_pieces.append(f"Error al cargar el documento {doc_name}: {str(e)}")
    
    return "\n\n".join(context_pieces)


def chat_with_multiple_docs(doc_summaries: List[Dict], doc_paths: List[str], question: str) -> str:
    """
    Función principal para chatear con múltiples documentos.
    """
    # Paso 1: Obtener páginas relevantes
    relevant_docs = get_relevant_pages_from_multiple_docs(doc_summaries, question)
    
    if "error" in relevant_docs:
        return f"Error al determinar páginas relevantes: {relevant_docs['error']}"
    
    # Paso 2: Extraer el contexto
    context = get_context_from_multiple_docs(relevant_docs, doc_paths)
    
    if not context.strip():
        return "No se encontró información relevante en los documentos para responder la pregunta."
    
    # Paso 3: Generar respuesta
    answer = chat_with_context(context, question)
    
    # Añadir fuentes consultadas a la respuesta
    sources_info = []
    for doc_info in relevant_docs.get("relevant_documents", []):
        doc_id = doc_info.get("doc_id", -1)
        pages = doc_info.get("pages", [])
        if 0 <= doc_id < len(doc_paths):
            doc_name = os.path.basename(doc_paths[doc_id])
            sources_info.append(f"{doc_name} (páginas: {', '.join(map(str, pages))})")
    
    if sources_info:
        answer += "\n\n**Fuentes consultadas:**\n- " + "\n- ".join(sources_info)
    
    return answer