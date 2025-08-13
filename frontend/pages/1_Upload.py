import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import streamlit as st

from lib.common import SESSION, USERNAME, PROCESSOR_URL

st.title("Cargar documentos")

st.markdown(
    "Seleccione uno o más archivos PDF para procesarlos. Se convertirán a Markdown, se clasificarán y se generará un resumen en segundo plano."
)

with st.form("upload_form", clear_on_submit=False):
    uploaded_files = st.file_uploader(
        "Archivos PDF",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_uploader",
    )
    submit = st.form_submit_button("Iniciar procesamiento")

def _upload_one(file):
    files_payload = {"file": (file.name, file.getvalue(), file.type)}
    data_payload = {"username": USERNAME}
    r = SESSION.post(
        f"{PROCESSOR_URL}/process_document/",
        files=files_payload,
        data=data_payload,
        timeout=30,
    )
    return file.name, r

if submit and uploaded_files:
    progress = st.progress(0, text="Subiendo")
    total = len(uploaded_files)
    successes = 0
    max_workers = min(8, total)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_upload_one, f): f for f in uploaded_files}
        done = 0
        for fut in as_completed(futures):
            name, resp = fut.result()
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    st.write(f"{name}: aceptado. Categoría: {data.get('category','N/A')}.")
                    successes += 1
                except Exception:
                    st.write(f"{name}: aceptado.")
            else:
                detail = "Error desconocido"
                try:
                    detail = resp.json().get("detail", detail)
                except Exception:
                    pass
                st.error(f"{name}: {detail}")
            done += 1
            progress.progress(done / total, text=f"{done}/{total}")

    st.write(f"Procesados: {successes}/{total}")
