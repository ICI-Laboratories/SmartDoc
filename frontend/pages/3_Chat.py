from pathlib import Path
from typing import Dict, List, Tuple, Optional
import requests
import streamlit as st

from lib.common import (
    ensure_session_id,
    get_http_session,
    LLM_URL,
    get_current_user_folder,
    get_available_summaries,
    read_json_cached,
)

st.title("Chat con documentos")

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "selected_docs" not in st.session_state:
    st.session_state.selected_docs = []

def extract_topic_metadata(summary_obj: dict) -> Tuple[Optional[str], List[str]]:
    topic = None
    subtopics: List[str] = []
    if not isinstance(summary_obj, dict):
        return topic, subtopics
    
    for k in ("topic", "tema", "label", "category", "categoria", "title"):
        v = summary_obj.get(k)
        if isinstance(v, str) and v.strip():
            topic = v.strip()
            break

    for k in ("subtopics", "subtemas", "topics", "labels", "etiquetas", "tags", "keywords"):
        v = summary_obj.get(k)
        if isinstance(v, list):
            subtopics = [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]
            break
            
    return topic, subtopics

def load_summary(path: Path) -> dict:
    mtime_json = path.stat().st_mtime_ns
    return read_json_cached(str(path), mtime_json)

SESSION_ID = ensure_session_id()
USER_FOLDER = get_current_user_folder(SESSION_ID)
summary_files_map: Dict[str, str] = get_available_summaries(SESSION_ID)

by_cat: Dict[str, Dict[str, List[str]]] = {}
summary_path_by_key: Dict[str, Path] = {}
metadata_by_doc: Dict[str, dict] = {}

if summary_files_map:
    for doc_key, sum_path_str in summary_files_map.items():
        spath = Path(sum_path_str)
        summary_path_by_key[doc_key] = spath
        try:
            rel = spath.relative_to(USER_FOLDER)
            parts = rel.parts
            cat = parts[0] if len(parts) > 1 else "General"
            subcat = parts[1] if len(parts) > 2 else "General"
            by_cat.setdefault(cat, {}).setdefault(subcat, []).append(doc_key)
            
            sobj = load_summary(spath)
            t, subs = extract_topic_metadata(sobj)
            metadata_by_doc[doc_key] = {"topic": t, "subtopics": subs, "category": f"{cat}/{subcat}"}
        except Exception:
            metadata_by_doc[doc_key] = {"topic": None, "subtopics": [], "category": "N/A"}

with st.sidebar:
    st.header("Fuente de Datos")
    
    def clear_chat():
        st.session_state.chat_history = []
        st.session_state.selected_docs = []

    st.button("Nueva Conversación ", on_click=clear_chat, use_container_width=True)

    if not summary_files_map:
        st.warning("No has procesado documentos. Ve a 'Cargar' para empezar.")
        st.stop()

    categories = sorted(by_cat.keys())
    selected_cat = st.selectbox("Filtrar por Categoría:", ["(Todas)"] + categories, index=0)

    docs_in_scope = []
    if selected_cat == "(Todas)":
        docs_in_scope = list(summary_files_map.keys())
    else:
        docs_in_scope.extend(
            doc for subcat_docs in by_cat.get(selected_cat, {}).values() for doc in subcat_docs
        )

    def label_for(dk: str) -> str:
        meta = metadata_by_doc.get(dk, {})
        t = meta.get("topic")
        cat = meta.get("category", "N/A")
        label = f"{dk} ({cat})"
        if t:
            label += f" — {t}"
        return label

    combined_options = sorted(list(set(docs_in_scope).union(set(st.session_state.selected_docs))))
    
    st.session_state.selected_docs = st.multiselect(
        "Selecciona los documentos para el chat:",
        options=combined_options,
        format_func=label_for,
        default=st.session_state.get("selected_docs", []),
    )
    st.info(f"**Seleccionados:** {len(st.session_state.selected_docs)} documento(s).")

st.header("Conversación")

for entry in st.session_state.chat_history:
    with st.chat_message("user"):
        st.markdown(entry["question"])
    with st.chat_message("assistant"):
        st.markdown(entry["answer"])

if question := st.chat_input("Escribe tu pregunta sobre los documentos seleccionados..."):
    if not st.session_state.selected_docs:
        st.warning("Por favor, selecciona al menos un documento en la barra lateral antes de preguntar.")
        st.stop()
    
    with st.chat_message("user"):
        st.markdown(question)

    with st.spinner("Pensando..."):
        try:
            summaries = [load_summary(summary_path_by_key[dk]) for dk in st.session_state.selected_docs]
            doc_paths = [str(summary_path_by_key[dk].with_suffix("").with_suffix(".md")) for dk in st.session_state.selected_docs]
            
            session = get_http_session(SESSION_ID)
            payload = {"summaries": summaries, "doc_paths": doc_paths, "question": question}
            response = session.post(f"{LLM_URL}/chat_with_multiple_docs", json=payload, timeout=90)
            response.raise_for_status()
            
            answer = response.json().get("answer", "No se recibió una respuesta válida.")
            
            st.session_state.chat_history.append({"question": question, "answer": answer})
            
            st.rerun()

        except requests.exceptions.RequestException as e:
            st.error(f"Error de comunicación con el servicio de IA: {e}")
        except Exception as e:
            st.error(f"Ocurrió un error inesperado: {e}")
