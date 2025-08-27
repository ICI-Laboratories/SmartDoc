# frontend/pages/1_Upload.py
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json
import math
from typing import Tuple, Union, Dict, Any, List

import requests
import streamlit as st

# MODIFICACIÓN 1: Importar get_session_id y ajustar las otras importaciones
from lib.common import get_http_session, get_session_id, PROCESSOR_URL

# -----------------------------
# Configuración de la página
# -----------------------------
st.title("Cargar y Procesar Documentos")

st.markdown(
    """
    Selecciona uno o más archivos **PDF** para procesarlos.
    """
)

# -----------------------------
# Parámetros (puedes ajustarlos)
# -----------------------------
MAX_FILE_MB = 50            # Tamaño máximo por archivo
CONNECT_TIMEOUT = 30         # Timeout de conexión en segundos
READ_TIMEOUT_PER_FILE = 600  # Timeout de lectura (por archivo) en segundos
MAX_WORKERS_CAP = 6          # Máximo de hilos concurrentes
RETRY_ATTEMPTS = 3           # Reintentos ante TIMEOUT/5xx
RETRY_BACKOFF_BASE = 1.8     # Factor de backoff exponencial

# -----------------------------
# Formulario de subida
# -----------------------------
with st.form("upload_form", clear_on_submit=True):
    uploaded_files = st.file_uploader(
        "Archivos PDF",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_uploader",
        help=f"Tamaño máximo sugerido por archivo: {MAX_FILE_MB} MB.",
    )
    submit = st.form_submit_button("Iniciar procesamiento", use_container_width=True)

# -----------------------------
# Utilidades
# -----------------------------
def _is_pdf(file) -> bool:
    name_ok = file.name.lower().endswith(".pdf")
    mime = getattr(file, "type", "") or ""
    mime_ok = "pdf" in mime.lower() if mime else True
    return name_ok and mime_ok

def _is_under_size(file, max_mb: int) -> bool:
    size = getattr(file, "size", None)
    if size is None:
        return True
    return size <= max_mb * 1024 * 1024

def _safe_json(resp: requests.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.text)
        except Exception:
            return {"detail": resp.text.strip()[:300] or "Respuesta no parseable."}

# MODIFICACIÓN 2: La función ahora acepta y usa el session_id
def _post_with_retries(file, session_id: str) -> Tuple[str, Union[str, requests.Response, Exception]]:
    """
    Sube un archivo con reintentos ante TIMEOUT o 5xx.
    """
    session = get_http_session()
    files_payload = {"file": (file.name, file.getvalue(), getattr(file, "type", "application/pdf"))}
    # Usa el ID de sesión del visitante como 'username' para el backend
    data_payload = {"username": session_id}

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = session.post(
                f"{PROCESSOR_URL}/process_document/",
                files=files_payload,
                data=data_payload,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_PER_FILE),
            )
            if 500 <= r.status_code < 600:
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_BASE ** attempt)
                    continue
            return file.name, r
        except requests.exceptions.Timeout:
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_BASE ** attempt)
                continue
            return file.name, "TIMEOUT"
        except Exception as e:
            if attempt < RETRY_ATTEMPTS and isinstance(e, requests.exceptions.RequestException):
                time.sleep(RETRY_BACKOFF_BASE ** attempt)
                continue
            return file.name, e
    return file.name, "TIMEOUT"

def _append_notification(kind: str, message: str) -> None:
    if "processing_notifications" not in st.session_state:
        st.session_state.processing_notifications = []
    st.session_state.processing_notifications.append({"type": kind, "message": message})

# -----------------------------
# Ejecución del envío
# -----------------------------
if submit:
    if not uploaded_files:
        st.warning("No seleccionaste archivos. Agrega al menos un PDF.")
        st.stop()

    unique_by_name = {f.name: f for f in uploaded_files}
    files_to_send = list(unique_by_name.values())

    invalid_ext = [f.name for f in files_to_send if not _is_pdf(f)]
    oversize = [f"{f.name} ({math.ceil(getattr(f, 'size', 0)/1024/1024)} MB)" for f in files_to_send if not _is_under_size(f, MAX_FILE_MB)]

    if invalid_ext:
        st.error("Se encontraron archivos no PDF:\n- " + "\n- ".join(invalid_ext))
        st.stop()
    if oversize:
        st.error(
            "Estos archivos superan el tamaño permitido:\n- " + "\n- ".join(oversize) +
            f"\nAjusta el límite o comprime el PDF (límite actual: {MAX_FILE_MB} MB)."
        )
        st.stop()

    # MODIFICACIÓN 3: Obtener el ID de sesión único antes de empezar a subir
    current_session_id = get_session_id()

    max_workers = min(MAX_WORKERS_CAP, len(files_to_send))
    progress = st.progress(0.0)
    status_placeholder = st.empty()
    results_table: List[Dict[str, str]] = []

    st.info(f"Enviando {len(files_to_send)} archivo(s) para el usuario '{current_session_id}' con hasta {max_workers} subidas en paralelo.")
    started = time.time()
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        # MODIFICACIÓN 4: Pasar el ID de sesión a la función de subida
        futures = {ex.submit(_post_with_retries, f, current_session_id): f.name for f in files_to_send}
        for fut in as_completed(futures):
            name, resp_or_err = fut.result()
            data = {}
            if isinstance(resp_or_err, requests.Response):
                data = _safe_json(resp_or_err)

            if resp_or_err == "TIMEOUT":
                msg = f"{name}: tiempo de espera agotado. No se procesó."
                _append_notification("error", msg)
                results_table.append({"archivo": name, "resultado": "Error", "detalle": "Timeout"})
            elif isinstance(resp_or_err, Exception):
                msg = f"{name}: error de conexión - {resp_or_err}"
                _append_notification("error", msg)
                results_table.append({"archivo": name, "resultado": "Error", "detalle": "Conexión"})
            else:
                resp = resp_or_err
                if resp.status_code == 200:
                    cat = data.get("category", "N/A")
                    msg = f"{name}: procesado y guardado en '{cat}'."
                    _append_notification("success", msg)
                    results_table.append({"archivo": name, "resultado": "OK", "detalle": f"Categoría: {cat}"})
                elif resp.status_code == 409:
                    detail = data.get("detail", "Archivo duplicado.")
                    msg = f"{name}: omitido. {detail}"
                    _append_notification("warning", msg)
                    results_table.append({"archivo": name, "resultado": "Omitido", "detalle": detail})
                else:
                    detail = data.get("detail", "Error desconocido.")
                    msg = f"{name}: error {resp.status_code} - {detail}"
                    _append_notification("error", msg)
                    results_table.append({"archivo": name, "resultado": "Error", "detalle": f"{resp.status_code}: {detail}"})

            completed += 1
            progress.progress(completed / len(files_to_send))
            with status_placeholder.container():
                st.markdown("**Resultados parciales**")
                st.dataframe(results_table, use_container_width=True)

    elapsed = time.time() - started
    st.success(
        f"Se enviaron {len(files_to_send)} archivo(s). "
        f"Recibirás notificaciones a medida que concluyan. "
        f"Tiempo total: {elapsed:.1f} s."
    )
    st.rerun()