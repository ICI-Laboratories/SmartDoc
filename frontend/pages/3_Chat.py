# frontend/pages/3_Chat.py

from pathlib import Path
from typing import Dict, List, Tuple, Optional
import requests
import streamlit as st

from lib.common import (
    get_http_session,
    LLM_URL,
    USER_FOLDER,
    get_available_summaries,
    read_json_cached,
)

st.title("Chat con documentos")

# ---------------------------------------------------------------------
# Helpers de metadatos (tema / subtemas)
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# Carga de resúmenes disponibles
# ---------------------------------------------------------------------
summary_files_map: Dict[str, str] = get_available_summaries(USER_FOLDER)
if not summary_files_map:
    st.warning("No se encontraron documentos procesados con resúmenes.")
    st.stop()

# Índices: cat -> subcat -> [doc_key], y doc_key -> (cat, subcat) / summary_path
by_cat: Dict[str, Dict[str, List[str]]] = {}
summary_path_by_key: Dict[str, Path] = {}
cat_subcat_by_doc: Dict[str, Tuple[str, str]] = {}

for doc_key, sum_path_str in summary_files_map.items():
    spath = Path(sum_path_str)
    summary_path_by_key[doc_key] = spath
    try:
        rel = spath.relative_to(USER_FOLDER)
        parts = rel.parts
        cat = parts[0] if len(parts) > 0 else "General"
        subcat = parts[1] if len(parts) > 1 else "General"
    except Exception:
        cat, subcat = "General", "General"

    by_cat.setdefault(cat, {}).setdefault(subcat, []).append(doc_key)
    cat_subcat_by_doc[doc_key] = (cat, subcat)

# ---------------------------------------------------------------------
# Estado persistente de selección
# ---------------------------------------------------------------------
if "selected_docs" not in st.session_state:
    st.session_state.selected_docs = []  # lista de doc_keys persistente

# ---------------------------------------------------------------------
# Selección jerárquica: Categoría → Subcategoría → (Tema/Subtema opcional)
# ---------------------------------------------------------------------
categories = sorted(by_cat.keys())
col1, col2 = st.columns(2)
selected_cat = col1.selectbox("Categoría", [""] + categories, index=0)
if not selected_cat:
    st.stop()

subcategories = sorted(by_cat[selected_cat].keys())
selected_subcat = col2.selectbox("Subcategoría", [""] + subcategories, index=0)
if not selected_subcat:
    st.stop()

# Docs de la subcategoría actual
subcat_doc_keys = sorted(by_cat[selected_cat][selected_subcat])

# Construye metadatos de la subcategoría actual (para filtros y etiquetas)
topics_set = set()
subtopics_set = set()
topic_by_doc_local: Dict[str, Optional[str]] = {}
subtopics_by_doc_local: Dict[str, List[str]] = {}
for dk in subcat_doc_keys:
    try:
        sobj = load_summary(summary_path_by_key[dk])
        t, subs = extract_topic_metadata(sobj)
        topic_by_doc_local[dk] = t
        subtopics_by_doc_local[dk] = subs or []
        if t:
            topics_set.add(t)
        for s in subs or []:
            subtopics_set.add(s)
    except Exception:
        topic_by_doc_local[dk] = None
        subtopics_by_doc_local[dk] = []

# Filtros opcionales
col3, col4 = st.columns(2)
topic_filter = col3.selectbox("Tema", ["(Todos)"] + sorted(topics_set), index=0) if topics_set else "(Todos)"
subtopic_filter = col4.selectbox("Subtema", ["(Todos)"] + sorted(subtopics_set), index=0) if subtopics_set else "(Todos)"

def pass_filters(dk: str) -> bool:
    if topic_filter != "(Todos)":
        if topic_by_doc_local.get(dk) != topic_filter:
            return False
    if subtopic_filter != "(Todos)":
        if subtopic_filter not in (subtopics_by_doc_local.get(dk) or []):
            return False
    return True

filtered_doc_keys = [dk for dk in subcat_doc_keys if pass_filters(dk)]

# ---------------------------------------------------------------------
# Multiselect que NO pierde selección al cambiar filtros
#   - Opciones = docs filtrados ∪ docs ya seleccionados
#   - Etiquetas enriquecidas con Tema/Subtemas y (cat/subcat) si aplica
# ---------------------------------------------------------------------
# Complementa metadatos para docs ya seleccionados que no estén en la subcategoría actual
topic_by_doc_extra: Dict[str, Optional[str]] = {}
subtopics_by_doc_extra: Dict[str, List[str]] = {}
for dk in st.session_state.selected_docs:
    if dk not in topic_by_doc_local:
        try:
            sobj = load_summary(summary_path_by_key[dk])
            t, subs = extract_topic_metadata(sobj)
            topic_by_doc_extra[dk] = t
            subtopics_by_doc_extra[dk] = subs or []
        except Exception:
            topic_by_doc_extra[dk] = None
            subtopics_by_doc_extra[dk] = []

def label_for(dk: str) -> str:
    # Usa metadatos locales si existen, si no, los extra
    t = topic_by_doc_local.get(dk, topic_by_doc_extra.get(dk))
    subs = (subtopics_by_doc_local.get(dk) or subtopics_by_doc_extra.get(dk) or [])
    label = dk
    if t and subs:
        label = f"{dk} — Tema: {t} — Subtemas: {', '.join(subs)}"
    elif t:
        label = f"{dk} — Tema: {t}"
    elif subs:
        label = f"{dk} — Subtemas: {', '.join(subs)}"
    # Si el
