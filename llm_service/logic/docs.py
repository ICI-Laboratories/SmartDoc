# llm_service/logic/docs.py

from __future__ import annotations

import json
import os
import re
from typing import List, Dict

from llm_service.logic.core import call_llm, chat_with_context

# --- Utilidades de paginado ---
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

# --- Relevancia multi-documento ---
def get_relevant_pages_from_multiple_docs(summaries: List[Dict], question: str) -> Dict:
    """
    Dado un arreglo de resúmenes (uno por documento) y una pregunta,
    devuelve qué páginas de qué documentos son relevantes.
    """
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

    # Validación dura: doc_id en rango y páginas únicas/ordenadas >0
    if "relevant_documents" in res:
        valid = []
        for item in res["relevant_documents"]:
            did = item.get("doc_id", -1)
            if isinstance(did, int) and 0 <= did < len(summaries):
                pages = sorted({p for p in item.get("pages", []) if isinstance(p, int) and p > 0})
                if pages:
                    valid.append({"doc_id": did, "pages": pages})
        res["relevant_documents"] = valid

    # Fallback conservador si falla el modelo o no hay nada útil
    if "error" in res or not res.get("relevant_documents"):
        return {"relevant_documents": [{"doc_id": i, "pages": list(range(1, 6))} for i in range(len(summaries))]}
    return res

def get_context_from_multiple_docs(relevant_docs: Dict, doc_paths: List[str]) -> str:
    """
    Construye el contexto concatenando las páginas solicitadas de cada documento (.md paginado).
    """
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
    Pipeline: detectar páginas → extraer contexto → chatear con contexto → añadir fuentes.
    """
    relevant = get_relevant_pages_from_multiple_docs(doc_summaries, question)
    if "error" in relevant:
        return f"Error al determinar páginas relevantes: {relevant['error']}"

    context = get_context_from_multiple_docs(relevant, doc_paths)
    if not context.strip():
        return "No se encontró información relevante para responder."

    answer = chat_with_context(context, question)

    # Trailing: fuentes para trazabilidad
    sources: List[str] = []
    for item in relevant.get("relevant_documents", []):
        did, pages = item.get("doc_id", -1), item.get("pages", [])
        if isinstance(did, int) and 0 <= did < len(doc_paths) and pages:
            pdf_name = os.path.basename(doc_paths[did]).replace(".md", ".pdf")
            sources.append(f"{pdf_name} (págs: {', '.join(map(str, pages))})")
    if sources:
        answer += "\n\n**Fuentes consultadas:**\n- " + "\n- ".join(sources)
    return answer

# --- Compatibilidad single-doc ---
def get_relevant_pages_from_summary(summary: Dict, question: str) -> Dict:
    """
    Versión single-doc (mantiene contrato antiguo): devuelve {'pages': [...]}
    usando la misma lógica multi-doc pero con un solo elemento.
    """
    multi = get_relevant_pages_from_multiple_docs([summary], question)
    pages = []
    for item in multi.get("relevant_documents", []):
        if item.get("doc_id") == 0:
            pages = item.get("pages", [])
            break
    return {"pages": pages}
