# llm_service/logic/docs.py

from __future__ import annotations
import json
import os
import re
from typing import List, Dict

from llm_service.logic.core import call_llm, chat_with_context


# Límite de tokens aproximado para el contexto final.
CONTEXT_TOKEN_LIMIT = 31000
def _estimate_tokens(text: str) -> int:
    """Estimación simple y rápida de tokens. Un token es ~4 caracteres en inglés,
    un poco menos en español. Usamos 3.5 como un promedio conservador."""
    return int(len(text) / 3.5)

def _get_final_sources(relevant_docs: Dict, doc_paths: List[str]) -> List[str]:
    """Helper para construir la lista de fuentes consultadas."""
    sources: List[str] = []
    for item in relevant_docs.get("relevant_documents", []):
        did, pages = item.get("doc_id", -1), item.get("pages", [])
        if isinstance(did, int) and 0 <= did < len(doc_paths) and pages:
            pdf_name = os.path.basename(doc_paths[did]).replace(".md", ".pdf")
            sources.append(f"{pdf_name} (págs: {', '.join(map(str, pages))})")
    return sources



# --- Utilidades de paginado (sin cambios) ---
_PAGE_SPLIT = re.compile(r'--- Página\s+(\d+)\s+---')

def _extract_pages(full_text: str) -> List[tuple[int, str]]:
    parts = re.split(_PAGE_SPLIT, full_text)
    out: List[tuple[int, str]] = []
    for i in range(1, len(parts), 2):
        try:
            n = int(parts[i])
            body = (parts[i + 1] if i + 1 < len(parts) else "").strip()
            if body:
                out.append((n, body))
        except Exception:
            continue
    return out

def get_pages_text_by_numbers(full_text: str, pages_list: List[int]) -> str:
    wanted = set(p for p in pages_list if isinstance(p, int) and p > 0)
    if not wanted:
        return ""
    pages = _extract_pages(full_text)
    return "\n".join(f"--- Página {n} ---\n{t}" for n, t in pages if n in wanted)

# --- Relevancia multi-documento (sin cambios) ---
def get_relevant_pages_from_multiple_docs(summaries: List[Dict], question: str) -> Dict:
    prompt = (
        "Analiza los resúmenes de varios documentos y una pregunta. Devuelve SOLO JSON con los documentos relevantes.\n"
        "Reglas:\n"
        " - 'doc_id' debe ser el índice (0..n-1) del documento en la lista dada.\n"
        " - 'pages' debe ser una lista de enteros de páginas (>0).\n\n"
        f"Pregunta: {question}\n\n"
        f"Resúmenes (doc_id 0..):\n{json.dumps(summaries, indent=2, ensure_ascii=False)}\n"
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
                        "pages": {"type": "array", "items": {"type": "integer"}}
                    },
                    "required": ["doc_id", "pages"]
                }
            }
        },
        "required": ["relevant_documents"]
    }
    res = call_llm(prompt, schema=schema, temperature=0.2, max_tokens=1000)
    if "relevant_documents" in res:
        valid = []
        for item in res["relevant_documents"]:
            did = item.get("doc_id", -1)
            if isinstance(did, int) and 0 <= did < len(summaries):
                pages = sorted({p for p in item.get("pages", []) if isinstance(p, int) and p > 0})
                if pages:
                    valid.append({"doc_id": did, "pages": pages})
        res["relevant_documents"] = valid
    if "error" in res or not res.get("relevant_documents"):
        return {"relevant_documents": [{"doc_id": i, "pages": list(range(1, 6))} for i in range(len(summaries))]}
    return res

def get_context_from_multiple_docs(relevant_docs: Dict, doc_paths: List[str]) -> str:
    chunks: List[str] = []
    for item in relevant_docs.get("relevant_documents", []):
        did, pages = item.get("doc_id", -1), item.get("pages", [])
        if not (isinstance(did, int) and 0 <= did < len(doc_paths) and pages):
            continue
        path = doc_paths[did]
        name = os.path.basename(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                full_text = f.read()
            body = get_pages_text_by_numbers(full_text, pages)
            if body.strip():
                chunks.append(
                    f"--- INICIO: {name} (págs: {', '.join(map(str, pages))}) ---\n"
                    f"{body}\n--- FIN: {name} ---"
                )
        except FileNotFoundError:
            chunks.append(f"--- ERROR: No se encontró {name} en {path} ---")
        except Exception as e:
            chunks.append(f"--- ERROR: No se pudo leer {name}: {e} ---")
    return "\n\n".join(chunks)

def chat_with_multiple_docs(doc_summaries: List[Dict], doc_paths: List[str], question: str) -> str:
    """
    Pipeline orquestador con estrategia de contexto adaptativo.
    """
    # 1. Filtro Rápido: usa resúmenes para encontrar páginas potencialmente relevantes.
    relevant_pages_info = get_relevant_pages_from_multiple_docs(doc_summaries, question)
    if "error" in relevant_pages_info:
        return f"Error al determinar páginas relevantes: {relevant_pages_info['error']}"
    if not relevant_pages_info.get("relevant_documents"):
        return "No se encontró información relevante para responder a la pregunta."

    # 2. Guardián del Contexto: intenta construir el contexto con el texto completo.
    context = get_context_from_multiple_docs(relevant_pages_info, doc_paths)
    
    answer = ""
    
    # 3. Comprueba si el contexto excede el límite de tokens.
    if _estimate_tokens(context) < CONTEXT_TOKEN_LIMIT:
        # El contexto cabe. Procede de la forma normal (alta precisión).
        if not context.strip():
            return "No se encontró información relevante para responder."
        answer = chat_with_context(context, question)
    else:
        # El contexto es demasiado grande. Activa la estrategia "Map-Reduce".
        # Construye un prompt con los resúmenes cortos de las páginas relevantes.
        summary_context_parts = []
        for item in relevant_pages_info.get("relevant_documents", []):
            doc_id = item["doc_id"]
            doc_title = os.path.basename(doc_paths[doc_id]).replace(".md", ".pdf")
            
            # Busca el resumen de cada página relevante
            for page_num in item.get("pages", []):
                summary_text = "Resumen no disponible."
                if doc_summaries[doc_id] and doc_summaries[doc_id].get("blocks"):
                    for block in doc_summaries[doc_id]["blocks"]:
                        # Asume que start_page y end_page son iguales para resúmenes de una sola página
                        if block.get("start_page") == page_num:
                            summary_text = block.get("summary", summary_text)
                            break
                summary_context_parts.append(
                    f"- Fuente: '{doc_title}', Página: {page_num}\n  Resumen: \"{summary_text}\""
                )
        
        summary_context = "\n".join(summary_context_parts)
        prompt = (
            "Eres un asistente de investigación. Responde la pregunta del usuario basándote únicamente en la siguiente lista de resúmenes de páginas de documentos. "
            "Sintetiza la información de manera cohesiva.\n\n"
            f"## Pregunta del usuario:\n{question}\n\n"
            f"## Resúmenes disponibles:\n{summary_context}\n\n"
            "## Respuesta final:"
        )
        
        # Llama al LLM una sola vez con el contexto de resúmenes
        response = call_llm(prompt, temperature=0.3)
        answer = response.get("content", f"Error al generar la respuesta desde los resúmenes: {response.get('error', 'desconocido')}")

    # Adjunta las fuentes consultadas a la respuesta final
    sources = _get_final_sources(relevant_pages_info, doc_paths)
    if sources:
        answer += "\n\n**Fuentes consultadas:**\n- " + "\n- ".join(sources)
        
    return answer


# --- Compatibilidad single-doc ---
def get_relevant_pages_from_summary(summary: Dict, question: str) -> Dict:
    multi = get_relevant_pages_from_multiple_docs([summary], question)
    pages = []
    for item in multi.get("relevant_documents", []):
        if item.get("doc_id") == 0:
            pages = item.get("pages", [])
            break
    return {"pages": pages}