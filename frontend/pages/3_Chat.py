from pathlib import Path
import requests
import streamlit as st

from lib.common import (
    SESSION, LLM_URL, USER_FOLDER,
    get_available_summaries, read_json_cached
)

st.title("Chat con documentos")

summary_files_map = get_available_summaries(USER_FOLDER)
if not summary_files_map:
    st.warning("No se encontraron documentos procesados con resúmenes.")
else:
    options = sorted(summary_files_map.keys())
    selected_docs = st.multiselect(
        "Documentos",
        options,
        help="Seleccione uno o más documentos para contextualizar la respuesta.",
    )

    st.markdown("---")
    with st.form("chat_form", clear_on_submit=False):
        question = st.text_area(
            "Pregunta",
            placeholder="Ejemplo: ¿Cuáles son las conclusiones principales?",
            height=100,
        )
        submit = st.form_submit_button("Enviar")

    if submit:
        if not selected_docs:
            st.warning("Seleccione al menos un documento.")
        elif not question.strip():
            st.warning("Escriba una pregunta.")
        else:
            with st.spinner("Buscando información relevante..."):
                try:
                    summaries = []
                    doc_paths = []
                    for k in selected_docs:
                        summary_path = Path(summary_files_map[k])
                        md_path = summary_path.with_suffix("").with_suffix(".md")
                        mtime_json = summary_path.stat().st_mtime_ns
                        summary_obj = read_json_cached(str(summary_path), mtime_json)
                        summaries.append(summary_obj)
                        doc_paths.append(str(md_path))

                    payload = {"summaries": summaries, "doc_paths": doc_paths, "question": question}
                    resp = SESSION.post(f"{LLM_URL}/chat_with_multiple_docs", json=payload, timeout=90)
                    resp.raise_for_status()
                    answer = resp.json().get("answer", "No se recibió una respuesta válida.")
                    st.markdown("#### Respuesta")
                    st.write(answer)
                except requests.RequestException as e:
                    st.error(f"Error de comunicación con el servicio: {e}")
                except Exception as e:
                    st.error(f"Ocurrió un error: {e}")

st.markdown("---")
if st.button("Limpiar cachés"):
    get_available_summaries.clear()
    st.experimental_rerun()
