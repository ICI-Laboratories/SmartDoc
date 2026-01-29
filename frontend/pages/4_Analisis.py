from pathlib import Path
from typing import Dict, List
import json
import requests
import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import re

from lib.common import (
    ensure_session_id,
    get_http_session,
    LLM_URL,
    get_available_summaries,
)

st.set_page_config(page_title="Análisis de Documentos", layout="wide")

st.title("Análisis Cuantitativo de Documentos")

def build_doc_paths(keys: List[str], summary_map: Dict[str, str]) -> List[str]:
    paths = []
    for key in keys:
        md = Path(summary_map[key]).with_suffix("").with_suffix(".md")
        if md.exists():
            paths.append(str(md))
    return paths

def build_stem_to_md_map(keys: List[str], summary_map: Dict[str, str]) -> Dict[str, Path]:
    idx = {}
    for k in keys:
        md = Path(summary_map[k]).with_suffix("").with_suffix(".md")
        if md.exists():
            idx[md.stem] = md
    return idx

def read_text_full(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"**No se pudo abrir el documento**: {e}"

def clamp_for_display(text: str, max_chars: int = 1_000_000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n…\n\n**(Truncado para visualización)**"

def highlight_text(text: str, query: str) -> str:
    tokens = [t for t in re.findall(r"\w+", query, flags=re.UNICODE) if len(t) >= 2]
    if not tokens:
        return text
    pattern = re.compile(r"\b(" + r"|".join(map(re.escape, tokens)) + r")\b", re.IGNORECASE | re.UNICODE)
    return pattern.sub(r"**\g<0>**", text)

def to_percent(x: float) -> float:
    try:
        return float(x) * 100.0
    except Exception:
        return 0.0

SESSION_ID = ensure_session_id()
summary_files_map: Dict[str, str] = get_available_summaries(SESSION_ID)
if not summary_files_map:
    st.warning("No se encontraron documentos procesados para tu sesión. Procesa al menos un documento en la sección de 'Cargar'.")
    st.stop()

with st.sidebar:
    st.header("Fuente de Datos para Análisis")
    doc_keys = sorted(summary_files_map.keys())

    col_a, _ = st.columns([1, 1])
    with col_a:
        select_all = st.checkbox("Seleccionar todos", value=False)
    if select_all:
        selected_docs = st.multiselect(
            "Selecciona los documentos a analizar:",
            options=doc_keys,
            default=doc_keys,
            help="Puedes seleccionar múltiples documentos para realizar análisis."
        )
    else:
        selected_docs = st.multiselect(
            "Selecciona los documentos a analizar:",
            options=doc_keys,
            help="Puedes seleccionar múltiples documentos para realizar análisis."
        )

    st.info(f"**Documentos seleccionados:** {len(selected_docs)}")

tab_search, tab_similarity = st.tabs(["Búsqueda Semántica", "Análisis de Similitud"])

with tab_search:
    st.header("Búsqueda por Significado")

    if not selected_docs:
        st.info("Selecciona al menos un documento en la barra lateral para empezar.")
    else:
        with st.form("form_search", clear_on_submit=False):
            query = st.text_input(
                "Ingresa tu consulta:",
                placeholder="Ej: ¿Cuáles son las conclusiones sobre el análisis de datos?",
            )
            top_k = st.slider("Número de resultados (top_k)", min_value=1, max_value=25, value=10, step=1)
            submitted_search = st.form_submit_button("Buscar Relevancia", use_container_width=True)

        if submitted_search:
            if not query.strip():
                st.error("La consulta no puede estar vacía.")
            else:
                with st.spinner("Realizando búsqueda híbrida..."):
                    st.session_state['last_search_results'] = None
                    try:
                        doc_paths = build_doc_paths(selected_docs, summary_files_map)
                        if not doc_paths:
                            st.error("No se encontraron archivos .md válidos para los documentos seleccionados.")
                        else:
                            session = get_http_session(SESSION_ID)
                            payload = {"doc_paths": doc_paths, "query": query, "top_k": top_k}
                            response = session.post(f"{LLM_URL}/analyze/semantic_search", json=payload, timeout=60)
                            response.raise_for_status()
                            search_data = response.json()
                            if "error" in search_data:
                                st.session_state['last_search_error'] = search_data
                            else:
                                st.session_state['last_search_results'] = search_data.get("results", [])
                                st.session_state['last_query'] = query
                                st.session_state['last_doc_paths'] = doc_paths
                                if 'last_search_error' in st.session_state:
                                    del st.session_state['last_search_error']
                    except requests.RequestException as e:
                        st.error(f"Error de comunicación: {e}")
                    except json.JSONDecodeError:
                        st.error("La respuesta del servidor no es un JSON válido.")
                    except Exception as e:
                        st.error(f"Error inesperado: {e}")

        st.markdown("---")
        st.subheader("Resultados de la Búsqueda")

        show_full_doc = st.toggle(
            "Mostrar documento completo en lugar del fragmento",
            value=False,
            help="Si está activado, se renderiza el .md entero del resultado."
        )
        highlight_full_doc = st.checkbox(
            "Resaltar términos de la consulta en el documento completo",
            value=True
        )

        if 'last_search_error' in st.session_state:
            st.error("Ocurrió un error durante la búsqueda:")
            st.json(st.session_state['last_search_error'])

        elif st.session_state.get('last_search_results') is not None:
            results = st.session_state['last_search_results']
            if not results:
                st.info("La búsqueda se completó, pero no se encontraron fragmentos relevantes para tu consulta.")
            else:
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Documentos consultados", len(st.session_state.get('last_doc_paths', [])))
                with col2:
                    st.metric("Resultados", len(results))
                with col3:
                    avg_score = np.mean([to_percent(r.get('score', 0.0)) for r in results])
                    st.metric("Relevancia promedio", f"{avg_score:.1f}%")

                df = pd.DataFrame([{
                    "documento": r.get("document", ""),
                    "relevancia_%": round(to_percent(r.get("score", 0.0)), 1),
                    "menciones": r.get("keyword_count", 0),
                    "fragmento": r.get("text", "").replace("\n", " ")[:300] + ("…" if len(r.get("text", "")) > 300 else "")
                } for r in results])
                with st.expander("Ver tabla resumida"):
                    st.dataframe(df, use_container_width=True, hide_index=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇ Descargar resultados (CSV)",
                    data=csv,
                    file_name="resultados_busqueda.csv",
                    mime="text/csv",
                    key="dl_results_csv",
                )

                stem_to_md = build_stem_to_md_map(selected_docs, summary_files_map)
                last_query = st.session_state.get('last_query', '')

                for i, res in enumerate(results):
                    score_percent = to_percent(res.get('score', 0.0))
                    keyword_count = res.get("keyword_count", 0)
                    doc_stem = str(res.get('document', ''))
                    with st.container(border=True):
                        st.markdown(
                            f"**Resultado #{i+1}** | **Documento:** `{doc_stem}` | "
                            f"**Menciones:** `{keyword_count}`"
                        )
                        st.progress(int(score_percent), text=f"Relevancia Híbrida: {score_percent:.1f}%")

                        if show_full_doc:
                            md_path = stem_to_md.get(doc_stem)
                            if not md_path:
                                st.warning("No se encontró el archivo .md para este resultado.")
                            else:
                                raw_full = read_text_full(md_path)
                                display_txt = clamp_for_display(raw_full)
                                with st.expander(f"Ver documento completo · {md_path.name}", expanded=False):
                                    if highlight_full_doc and last_query.strip():
                                        st.markdown(highlight_text(display_txt, last_query))
                                    else:
                                        st.markdown(display_txt)
                                st.download_button(
                                    "⬇ Descargar .md",
                                    data=raw_full.encode("utf-8"),
                                    file_name=md_path.name,
                                    mime="text/markdown",
                                    key=f"dl_md_{i}_{doc_stem}",
                                )
                        else:
                            with st.expander("Ver texto del fragmento"):
                                highlighted = highlight_text(res.get('text', ''), last_query)
                                st.markdown(f"> {highlighted}")

        else:
            st.info("Ingresa una consulta y presiona **Buscar Relevancia** para ver los resultados.")

with tab_similarity:
    st.header("Matriz de Similitud entre Documentos")
    st.markdown(
        """
        Calcula qué tan relacionados están los documentos seleccionados entre sí.
        Un valor **1.0** indica alta similitud; valores cercanos a **0.0** indican baja similitud.
        """
    )

    if len(selected_docs) < 2:
        st.info("Selecciona al menos dos documentos en la barra lateral para comparar.")
    else:
        with st.form("form_similarity", clear_on_submit=False):
            submitted_sim = st.form_submit_button("Calcular Matriz de Similitud", use_container_width=True)

        if submitted_sim:
            with st.spinner("Calculando vectores promedio y comparando documentos..."):
                st.session_state['last_similarity_data'] = None
                try:
                    doc_paths = build_doc_paths(selected_docs, summary_files_map)
                    if len(doc_paths) < 2:
                        st.error("No hay suficientes archivos .md válidos para comparar (se requieren al menos 2).")
                    else:
                        session = get_http_session(SESSION_ID)
                        payload = {"doc_paths": doc_paths}
                        response = session.post(f"{LLM_URL}/analyze/document_similarity", json=payload, timeout=90)
                        response.raise_for_status()
                        st.session_state['last_similarity_data'] = response.json()
                except requests.RequestException as e:
                    st.error(f"Error de comunicación: {e}")
                except json.JSONDecodeError:
                    st.error("La respuesta del servidor no es un JSON válido.")
                except Exception as e:
                    st.error(f"Error inesperado: {e}")

    data = st.session_state.get('last_similarity_data')
    if data is not None:
        if "error" in data:
            st.error(f"Error al calcular similitud: {data['error']}")
            if "details" in data and data["details"]:
                with st.expander("Detalles del error"):
                    st.write("\n".join(data["details"]))
        else:
            matrix = np.array(data.get('similarity_matrix', []), dtype=float)
            labels = data.get('doc_names', [])
            if matrix.size == 0 or not labels:
                st.info("No hay datos de similitud para mostrar.")
            else:
                display_vals = np.round(matrix, 2)
                fig = go.Figure(data=go.Heatmap(
                    z=matrix,
                    x=labels,
                    y=labels,
                    hoverongaps=False,
                    colorscale="Viridis",
                    zmin=0.0, zmax=1.0,
                    text=display_vals,
                    texttemplate="%{text}"
                ))
                fig.update_layout(
                    title='Mapa de Calor de Similitud Conceptual',
                    height=600,
                    xaxis=dict(tickangle=45),
                    margin=dict(t=60, l=60, r=20, b=120),
                )
                st.plotly_chart(fig, use_container_width=True)

                df_sim = pd.DataFrame(matrix, index=labels, columns=labels)
                with st.expander("Ver tabla de similitud"):
                    st.dataframe(df_sim.style.format("{:.2f}"), use_container_width=True)
                csv_sim = df_sim.to_csv().encode("utf-8")
                st.download_button(
                    "⬇ Descargar matriz (CSV)",
                    data=csv_sim,
                    file_name="matriz_similitud.csv",
                    mime="text/csv",
                    key="dl_matrix_csv",
                )
