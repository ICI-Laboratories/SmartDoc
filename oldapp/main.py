# frontend/main.py

import os
import getpass
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

# ===============================
# Configuración de la Aplicación
# ===============================
PROCESSOR_URL = os.getenv("SMARTDOC_PROCESSOR_URL", "http://127.0.0.1:8002")
LLM_URL = os.getenv("SMARTDOC_LLM_URL", "http://127.0.0.1:8001")

# Debe ser la MISMA ruta base que usa tu backend (document_processor)
BASE_DIR = Path(os.getenv("SMARTDOC_BASE", Path.home() / "SmartDocData"))
USERNAME = getpass.getuser()
USER_FOLDER = BASE_DIR / USERNAME

# -------------------------------------------------
# Página y estilos (evitar rerender con CSS inline)
# -------------------------------------------------
st.set_page_config(
    page_title="SmartDoc - Asistente Inteligente",
    page_icon="📚",
    layout="wide",
)

st.markdown(
    """
    <style>
        .stButton>button { width: 100%; }
        .block-container { padding-top: 2rem; }
        .chat-message {
            padding: 1rem;
            border-radius: 0.5rem;
            margin-bottom: 1rem;
            display: flex;
            flex-direction: column;
        }
        .chat-message-user { border-left: 5px solid #4A90E2; background-color: #F0F8FF; }
        .chat-message-assistant { border-left: 5px solid #50E3C2; background-color: #F2FFFA; }
        .chat-message-heading { font-weight: bold; margin-bottom: 0.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ===========================
# Recursos (HTTP Session pool)
# ===========================
@st.cache_resource
def get_http_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["POST", "GET"]),
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(
        {
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
        }
    )
    return s


SESSION = get_http_session()

# ====================
# Estado de la sesión
# ====================
if "processed_files" not in st.session_state:
    st.session_state.processed_files = []
if "selected_docs" not in st.session_state:
    st.session_state.selected_docs = []
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# =====================
# Funciones cacheadas
# =====================
@st.cache_data(ttl=60, show_spinner=False)
def get_available_summaries(user_folder: Path) -> dict:
    """
    Mapea 'Categoria/Subcategoria/NombreDoc' -> ruta absoluta del .summary.json (str).
    Cacheada 60s para evitar escaneos de FS en cada rerender.
    """
    if not user_folder.exists():
        return {}
    summary_files_map: dict[str, str] = {}
    for summary_path in user_folder.rglob("*.summary.json"):
        try:
            doc_name = summary_path.name.replace(".summary.json", "")
            key = f"{summary_path.parent.parent.name}/{summary_path.parent.name}/{doc_name}"
            summary_files_map[key] = str(summary_path)
        except IndexError:
            # Ignora archivos fuera de la estructura esperada
            continue
    return summary_files_map


@st.cache_data(ttl=30, show_spinner=False)
def list_categories(user_folder: Path) -> list[str]:
    if not user_folder.exists():
        return []
    return sorted([d.name for d in user_folder.iterdir() if d.is_dir()])


@st.cache_data(ttl=30, show_spinner=False)
def list_subcategories(cat_path: Path) -> list[str]:
    if not cat_path.exists():
        return []
    return sorted([d.name for d in cat_path.iterdir() if d.is_dir()])


@st.cache_data(ttl=30, show_spinner=False)
def list_md_files(subcat_path: Path) -> list[str]:
    if not subcat_path.exists():
        return []
    return sorted([f.name for f in subcat_path.glob("*.md")])


@st.cache_data(max_entries=256, show_spinner=False)
def read_markdown_cached(path: str, mtime_ns: int) -> str:
    return Path(path).read_text(encoding="utf-8")


@st.cache_data(max_entries=256, show_spinner=False)
def read_json_cached(path: str, mtime_ns: int) -> dict:
    p = Path(path)
    # Intentamos orjson si está disponible (más rápido), si no, json estándar
    try:
        import orjson  # type: ignore
        return orjson.loads(p.read_bytes())
    except Exception:
        return json.loads(p.read_text(encoding="utf-8"))


# ============
# Sidebar
# ============
with st.sidebar:
    st.markdown(f"## Bienvenido, {USERNAME}")
    st.caption("Asistente Inteligente de Documentos")
    st.markdown("---")
    st.info(f"**Carpeta de Datos:** `{USER_FOLDER}`")
    st.markdown("---")
    st.caption("© 2025 SmartDoc")

# ==============================
# Pestañas principales (tabs)
# ==============================
tabs = st.tabs(["📤 Cargar Documentos", "📖 Explorar Documentos", "💬 Chatear con Documentos"])

# ==============================================================================
# PESTAÑA 1: CARGAR DOCUMENTOS (subidas paralelas + keep-alive)
# ==============================================================================
with tabs[0]:
    st.header("📤 Cargar y Procesar Nuevos Documentos")
    st.markdown(
        "Sube uno o más archivos PDF. El sistema los convertirá, clasificará con IA "
        "y los organizará automáticamente en tu carpeta de datos."
    )
    with st.form("upload_form", clear_on_submit=False):
        uploaded_files = st.file_uploader(
            "Arrastra tus archivos PDF aquí o haz clic para seleccionar",
            type=["pdf"],
            accept_multiple_files=True,
            key="pdf_uploader",
        )
        # ¡No lo deshabilites aquí!
        submit_uploads = st.form_submit_button("Iniciar Procesamiento")
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

    if submit_uploads and uploaded_files:
        st.subheader("Progreso del Procesamiento")
        progress = st.progress(0, text="Subiendo…")
        total = len(uploaded_files)

        # max 8 hilos o número de archivos (lo que sea menor)
        max_workers = min(8, total)
        successes = 0

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_upload_one, f): f for f in uploaded_files}
            done = 0
            for fut in as_completed(futures):
                name, resp = fut.result()
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        st.success(
                            f"✅ **{name}** aceptado. Categoría: **{data.get('category', 'N/A')}**. "
                            f"El resumen se genera en segundo plano."
                        )
                        st.session_state.processed_files.append(data.get("original_filename", name))
                        successes += 1
                    except Exception:
                        st.success(f"✅ **{name}** aceptado.")
                else:
                    detail = "Error desconocido"
                    try:
                        detail = resp.json().get("detail", detail)
                    except Exception:
                        pass
                    st.error(f"❌ {name}: {detail}")
                done += 1
                progress.progress(done / total, text=f"{done}/{total} completados")

        st.toast(f"Procesados: {successes}/{total}")

# ==============================================================================
# PESTAÑA 2: EXPLORAR DOCUMENTOS (cache por mtime para lectura rápida)
# ==============================================================================
with tabs[1]:
    st.header("📖 Explorar y Visualizar Documentos")
    st.markdown(
        "Navega por las categorías generadas y visualiza el contenido de tus "
        "documentos en formato Markdown."
    )

    if not USER_FOLDER.exists():
        st.info("Aún no se han procesado documentos. Sube algunos en la pestaña 'Cargar Documentos'.")
    else:
        categories = list_categories(USER_FOLDER)
        col1, col2 = st.columns(2)
        selected_category = col1.selectbox("Elige una Categoría:", [""] + categories, key="cat_select")

        if selected_category:
            cat_path = USER_FOLDER / selected_category
            subcategories = list_subcategories(cat_path)
            selected_subcat = col2.selectbox("Elige una Subcategoría:", [""] + subcategories, key="subcat_select")

            if selected_subcat:
                subcat_path = cat_path / selected_subcat
                md_files = list_md_files(subcat_path)

                if not md_files:
                    st.info(f"No hay documentos Markdown en '{selected_category}/{selected_subcat}'.")
                else:
                    selected_md = st.selectbox("Selecciona un Documento para Ver:", [""] + md_files, key="md_select")
                    if selected_md:
                        md_path = subcat_path / selected_md
                        try:
                            mtime_ns = md_path.stat().st_mtime_ns
                            markdown_content = read_markdown_cached(str(md_path), mtime_ns)
                            with st.expander("Ver Contenido del Documento", expanded=True):
                                # Renderizado de Markdown (rápido, cacheado)
                                st.markdown(markdown_content, unsafe_allow_html=True)
                        except Exception as e:
                            st.error(f"No se pudo leer el archivo: {e}")

# ==============================================================================
# PESTAÑA 3: CHATEAR CON DOCUMENTOS (cache de índices y lecturas JSON)
# ==============================================================================
with tabs[2]:
    st.header("💬 Chatear con tus Documentos")
    st.markdown(
        "Selecciona los documentos con los que quieres conversar, haz una pregunta, y la IA "
        "buscará la información relevante en ellos."
    )

    col1, col2 = st.columns([1, 2])

    # --- Panel de Selección de Documentos ---
    with col1:
        st.subheader("Fuente de Datos")

        summary_files_map = get_available_summaries(USER_FOLDER)

        if not summary_files_map:
            st.warning("No se encontraron documentos procesados con resúmenes.")
            st.button("Limpiar Caché y Refrescar", on_click=lambda: (get_available_summaries.clear(), st.rerun()))
        else:
            options = sorted(summary_files_map.keys())
            st.session_state.selected_docs = st.multiselect(
                "Selecciona los documentos para el chat:",
                options,
                default=st.session_state.get("selected_docs", []),
                help="Puedes seleccionar múltiples documentos de diferentes categorías.",
            )

            st.info(f"**Seleccionados:** {len(st.session_state.selected_docs)} documento(s).")
            if st.button("Limpiar Caché y Refrescar"):
                # Forzar recarga de índices cacheados
                get_available_summaries.clear()
                list_categories.clear()
                list_subcategories.clear()
                list_md_files.clear()
                read_markdown_cached.clear()
                read_json_cached.clear()
                st.rerun()

    # --- Panel de Chat ---
    with col2:
        st.subheader("Conversación")
        with st.form("chat_form", clear_on_submit=False):
            question = st.text_area(
                "Escribe tu pregunta aquí:",
                key="user_question",
                placeholder="Ej: ¿Cuáles son las conclusiones principales sobre el análisis de datos?",
                height=100,
            )
            submit_q = st.form_submit_button(
                "Enviar Pregunta"
            )

        if submit_q:
            with st.spinner("Pensando... La IA está buscando en los documentos seleccionados..."):
                try:
                    # 1) Cargar resúmenes (cache por mtime)
                    summaries = []
                    doc_paths = []
                    for doc_key in st.session_state.selected_docs:
                        summary_path_str = summary_files_map[doc_key]
                        summary_path = Path(summary_path_str)
                        md_path = summary_path.with_suffix("").with_suffix(".md")  # .summary.json -> .md

                        mtime_json = summary_path.stat().st_mtime_ns
                        summary_obj = read_json_cached(str(summary_path), mtime_json)
                        summaries.append(summary_obj)
                        doc_paths.append(str(md_path))

                    # 2) Petición al LLM
                    payload = {
                        "summaries": summaries,
                        "doc_paths": doc_paths,
                        "question": question,
                    }
                    resp = SESSION.post(f"{LLM_URL}/chat_with_multiple_docs", json=payload, timeout=90)
                    resp.raise_for_status()
                    answer = resp.json().get("answer", "No se recibió una respuesta válida.")
                    st.session_state.chat_history.insert(0, {"question": question, "answer": answer})
                except requests.RequestException as e:
                    st.error(f"Error de comunicación con el servicio de IA: {e}")
                except Exception as e:
                    st.error(f"Ocurrió un error inesperado: {e}")

        st.markdown("---")

        # Mostrar historial de chat (más reciente primero)
        if not st.session_state.chat_history:
            st.info("El historial de la conversación aparecerá aquí.")
        else:
            for entry in st.session_state.chat_history:
                with st.container():
                    st.markdown(
                        f'<div class="chat-message chat-message-user">'
                        f'<div class="chat-message-heading">🙋 Tu Pregunta:</div>{entry["question"]}'
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<div class="chat-message chat-message-assistant">'
                        f'<div class="chat-message-heading">🤖 Respuesta:</div>{entry["answer"]}'
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown("---")
