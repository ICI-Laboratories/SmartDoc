# main.py
import os
import json
import base64
import time
import getpass
from types import SimpleNamespace

import streamlit as st

from docs import (
    extract_text_with_easyocr,
    save_to_folder,
    extract_pages_from_text,
    hierarchical_summary,
    save_hierarchical_summary,
    load_hierarchical_summary,
    save_pdf_to_folder,
    get_pages_text_by_numbers,
)
from llm import (
    classify_text_with_lmstudio,
    chat_with_context,
    chat_with_multiple_docs,
    get_relevant_pages_from_summary,
)

# --------------------------- Utilidades ---------------------------

def show_pdf(file_path: str, height: int = 800):
    """Muestra un PDF embebido en la página si existe."""
    if not os.path.exists(file_path):
        st.warning(f"No se encontró el archivo: {file_path}")
        return
    with open(file_path, "rb") as f:
        pdf_data = f.read()
    base64_pdf = base64.b64encode(pdf_data).decode("utf-8")
    st.markdown(
        f"""
        <iframe src="data:application/pdf;base64,{base64_pdf}"
                width="100%" height="{height}" type="application/pdf"></iframe>
        """,
        unsafe_allow_html=True,
    )

def safe_call(fn, *args, **kwargs):
    """Envuelve llamadas al LLM para evitar que la app se caiga si el servidor no responde."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": str(e)}

# ---------------------- Configuración de página -------------------

st.set_page_config(
    page_title="SmartDoc - Asistente Inteligente para Documentos",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .main .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    .chat-message { padding: 1rem; border-radius: .5rem; margin-bottom: 1rem; display: flex; flex-direction: column; }
    .chat-message-user { border-left: 5px solid #4361ee; }
    .chat-message-assistant { border-left: 5px solid #3498db; }
    .chat-message-heading { font-weight: bold; margin-bottom: .5rem; }
    .stButton button { width: 100%; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ------------------ Carpeta del usuario (sin login) ----------------

USERNAME = os.getenv("SMARTDOC_USER", getpass.getuser())
BASE_DIR = os.getenv("SMARTDOC_BASE", os.path.join(os.path.expanduser("~"), "SmartDocData"))
output_folder = os.path.join(BASE_DIR, USERNAME)
os.makedirs(output_folder, exist_ok=True)

# ------------------------- Session state --------------------------

st.session_state.setdefault("processed_files", [])
st.session_state.setdefault("selected_bibliografia", [])
st.session_state.setdefault("chat_history", [])
st.session_state.setdefault("clear_question", False)
st.session_state.setdefault("refresh_uploader", False)

# ---------------------------- Sidebar -----------------------------

with st.sidebar:
    st.markdown(f"<h2 style='text-align:center;'>Bienvenido, {USERNAME}</h2>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Documentos", len(st.session_state.processed_files))
    with col2:
        st.metric("Consultas", len(st.session_state.chat_history))
    st.caption(f"Carpeta de trabajo: `{output_folder}`")
    st.markdown("---")
    st.caption("Desarrollado por ICI Laboratories, Universidad de Colima")

# ------------------------------ Tabs ------------------------------

tabs = st.tabs(["📤 Cargar Documentos", "📖 Revisar Documentos", "💬 Consultar Documentos"])

# ---------------------- Tab 1: Cargar Documentos ------------------

with tabs[0]:
    st.header("📤 Cargar y Procesar Documentos")
    with st.expander("ℹ️ Instrucciones de uso", expanded=True):
        st.markdown(
            """
            1. Selecciona uno o más PDFs.
            2. Se extrae texto (OCR si hace falta), se clasifica y se genera un resumen jerárquico.
            3. Todo queda guardado por categorías en tu carpeta de trabajo.
            > Límite recomendado: 70 MB por archivo.
            """
        )

    def refresh_uploader_callback():
        st.session_state.refresh_uploader = True
        st.rerun()

    st.subheader("Selecciona los documentos a procesar")

    uploader_key = f"pdf_uploader_{time.time()}" if st.session_state.refresh_uploader else "pdf_uploader"
    if st.session_state.refresh_uploader:
        st.session_state.refresh_uploader = False

    uploaded_files = st.file_uploader(
        "Arrastra o selecciona archivos PDF",
        type=["pdf"],
        accept_multiple_files=True,
        key=uploader_key,
    )

    if uploaded_files:
        st.subheader("Procesando documentos")
        progress_bar = st.progress(0)
        status_text = st.empty()

        total_files = len(uploaded_files)
        processed_count = 0
        newly_processed = []

        for file in uploaded_files:
            file_display_name = (file.name[:40] + "...") if len(file.name) > 40 else file.name

            if file.name in st.session_state.processed_files:
                status_text.info(f"⏭️ Omitiendo {file_display_name} (ya procesado)")
                processed_count += 1
                progress_bar.progress(processed_count / total_files)
                continue

            try:
                status_text.info(f"⚙️ Procesando {file_display_name}...")

                # 1) Extracción
                extracted_text = extract_text_with_easyocr(file)
                if not extracted_text.strip():
                    status_text.warning(f"⚠️ {file_display_name}: No se pudo extraer texto")
                    processed_count += 1
                    progress_bar.progress(processed_count / total_files)
                    continue

                # 2) Clasificación (carpetas destino)
                main_cat, sub_cat = classify_text_with_lmstudio(extracted_text, output_folder)

                # 3) Guardado .txt y .pdf
                txt_path = save_to_folder(
                    extracted_text,
                    output_folder,
                    file.name.replace(".pdf", ".txt"),
                    main_cat,
                    sub_cat,
                )
                save_pdf_to_folder(
                    file,
                    output_folder,
                    file.name,
                    main_cat,
                    sub_cat,
                )

                # 4) Resumen jerárquico
                pages = extract_pages_from_text(extracted_text)
                summary_blocks = hierarchical_summary(pages)
                summary_filename = file.name.replace(".pdf", "_hierarchical_summary.json")
                summary_folder = os.path.join(output_folder, main_cat, sub_cat)
                summary_path = os.path.join(summary_folder, summary_filename)
                save_hierarchical_summary({"title": file.name, "blocks": summary_blocks}, summary_path)

                # Estado
                st.session_state.processed_files.append(file.name)
                newly_processed.append(
                    {"name": file.name, "category": f"{main_cat} / {sub_cat}", "txt_path": txt_path}
                )

            except Exception as e:
                status_text.error(f"❌ Error al procesar {file_display_name}: {str(e)}")

            processed_count += 1
            progress_bar.progress(processed_count / total_files)

        if newly_processed:
            status_text.success(f"✅ Listo: {len(newly_processed)} documento(s) nuevos procesados")
            st.subheader("Documentos procesados")
            for doc in newly_processed:
                with st.expander(f"📄 {doc['name']}"):
                    st.write(f"**Categoría:** {doc['category']}")
                    with open(doc["txt_path"], "r", encoding="utf-8") as fh:
                        st.download_button(
                            "⬇️ Descargar texto extraído",
                            fh.read(),
                            file_name=os.path.basename(doc["txt_path"]),
                        )
            if st.button("🔄 Procesar más documentos"):
                refresh_uploader_callback()
        else:
            status_text.info("ℹ️ No se procesaron nuevos documentos")

# --------------------- Tab 2: Revisar Documentos ------------------

with tabs[1]:
    st.header("📖 Explorar y Revisar Documentos")
    with st.expander("ℹ️ Instrucciones de uso", expanded=True):
        st.markdown(
            """
            1. Elige categoría y subcategoría.
            2. Selecciona un PDF para visualizarlo en el visor embebido.
            """
        )

    if os.path.exists(output_folder):
        categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]

        if not categories:
            st.info("📂 No hay documentos disponibles. Carga algunos en la pestaña 'Cargar Documentos'.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                category = st.selectbox("Categoría", [""] + categories, key="review_category")
            if category:
                with col2:
                    subcats = [
                        d
                        for d in os.listdir(os.path.join(output_folder, category))
                        if os.path.isdir(os.path.join(output_folder, category, d))
                    ]
                    subcat = st.selectbox("Subcategoría", [""] + subcats, key="review_subcat")

                if subcat:
                    folder = os.path.join(output_folder, category, subcat)
                    pdf_files = [f for f in os.listdir(folder) if f.lower().endswith(".pdf")]

                    if not pdf_files:
                        st.info(f"📂 No hay PDFs en {category}/{subcat}")
                    else:
                        pdf_selected = st.selectbox("Documento", [""] + pdf_files, key="review_pdf")
                        if pdf_selected:
                            st.subheader(f"Visualizando: {pdf_selected}")
                            show_pdf(os.path.join(folder, pdf_selected))

# -------------------- Tab 3: Consultar Documentos -----------------

with tabs[2]:
    st.header("💬 Consultar Documentos")
    with st.expander("ℹ️ Instrucciones de uso", expanded=True):
        st.markdown(
            """
            1. Agrega uno o más documentos (usa sus resúmenes generados).
            2. Escribe tu pregunta y envíala.
            3. La respuesta se basa en las páginas relevantes encontradas.
            """
        )

    col1, col2 = st.columns([1, 2])

    # ----- Selector de documentos -----
    with col1:
        st.subheader("Documentos seleccionados")

        with st.expander("➕ Agregar documentos", expanded=len(st.session_state.selected_bibliografia) == 0):
            categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
            if not categories:
                st.info("📂 No hay documentos disponibles. Carga algunos primero.")
            else:
                selected_category = st.selectbox("Categoría", [""] + categories, key="chat_category")
                if selected_category:
                    subcategories = [
                        d
                        for d in os.listdir(os.path.join(output_folder, selected_category))
                        if os.path.isdir(os.path.join(output_folder, selected_category, d))
                    ]
                    selected_subcategory = st.selectbox("Subcategoría", [""] + subcategories, key="chat_subcategory")

                    if selected_subcategory:
                        folder_path = os.path.join(output_folder, selected_category, selected_subcategory)
                        biblio_files = [f for f in os.listdir(folder_path) if f.endswith("_hierarchical_summary.json")]

                        if biblio_files:
                            selected_biblio = st.selectbox(
                                "Documento",
                                [""] + [f.replace("_hierarchical_summary.json", ".pdf") for f in biblio_files],
                                key="chat_biblio",
                            )
                            if selected_biblio and st.button("➕ Agregar documento"):
                                summary_file = selected_biblio.replace(".pdf", "_hierarchical_summary.json")
                                full_path = os.path.join(folder_path, summary_file)
                                if full_path not in st.session_state.selected_bibliografia:
                                    st.session_state.selected_bibliografia.append(full_path)
                                    st.success(f"✅ Agregado: {selected_biblio}")
                                    st.rerun()
                                else:
                                    st.info("ℹ️ Ya está en la lista")
                        else:
                            st.info("📂 No hay documentos en esta subcategoría")

        if st.session_state.selected_bibliografia:
            for idx, bib_path in enumerate(st.session_state.selected_bibliografia):
                doc_name = os.path.basename(bib_path).replace("_hierarchical_summary.json", ".pdf")
                st.markdown(
                    f"""
                    <div style="padding:10px;border-radius:5px;margin-bottom:10px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <div style="flex-grow:1;">📄 {doc_name}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("❌ Quitar", key=f"remove_{idx}"):
                    st.session_state.selected_bibliografia.pop(idx)
                    st.rerun()
        else:
            st.info("⚠️ No has seleccionado ningún documento")

    # ----- Panel de chat -----
    with col2:
        st.subheader("Conversación")

        placeholder = "" if st.session_state.clear_question else st.session_state.get("user_question", "")
        if st.session_state.clear_question:
            st.session_state.clear_question = False

        question = st.text_input(
            "Escribe tu pregunta:",
            key="user_question",
            value=placeholder,
            placeholder="¿Qué deseas saber sobre los documentos seleccionados?",
        )

        if st.button("📤 Enviar pregunta"):
            if not st.session_state.selected_bibliografia:
                st.warning("⚠️ Debes seleccionar al menos un documento")
            elif not question.strip():
                st.warning("⚠️ Por favor, escribe una pregunta")
            else:
                with st.spinner("⏳ Procesando tu pregunta..."):
                    # Preparar documentos
                    doc_summaries, doc_txt_paths = [], []
                    for bib_path in st.session_state.selected_bibliografia:
                        bib_data = load_hierarchical_summary(bib_path)
                        doc_summaries.append(bib_data)
                        doc_txt_paths.append(bib_path.replace("_hierarchical_summary.json", ".txt"))

                    # Generar respuesta
                    if len(doc_summaries) == 1:
                        bib_data = doc_summaries[0]
                        pages_info = safe_call(get_relevant_pages_from_summary, bib_data, question)
                        txt_path = doc_txt_paths[0]

                        if os.path.exists(txt_path):
                            with open(txt_path, "r", encoding="utf-8") as f:
                                full_text = f.read()
                            pages = pages_info.get("pages", []) if isinstance(pages_info, dict) else []
                            context = get_pages_text_by_numbers(full_text, pages)
                            answer = chat_with_context(context, question)
                            source = f"*Fuente: {os.path.basename(txt_path)}"
                            if pages:
                                source += f" (páginas: {', '.join(map(str, pages))})"
                            combined_response = f"{answer}\n\n{source}"
                        else:
                            combined_response = f"⚠️ No se encontró el archivo de texto para {bib_data.get('title','(sin título)')}"
                    else:
                        # Múltiples documentos (usa utilidad del módulo llm)
                        combined_response = chat_with_multiple_docs(doc_summaries, doc_txt_paths, question)

                    st.session_state.chat_history.append({"question": question, "answer": combined_response})
                    st.session_state.clear_question = True
                    st.rerun()

        st.markdown("---")

        # Historial
        if not st.session_state.chat_history:
            st.info("💬 Aún no hay mensajes. Selecciona documentos y realiza tu primera pregunta.")
        else:
            for entry in reversed(st.session_state.chat_history):
                st.markdown(
                    f"""
                    <div class="chat-message chat-message-user">
                        <div class="chat-message-heading">🙋 Tú preguntaste:</div>
                        {entry['question']}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"""
                    <div class="chat-message chat-message-assistant">
                        <div class="chat-message-heading">🤖 Respuesta:</div>
                        {entry['answer']}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

# --------------------------- Pie de página ------------------------

st.markdown("---")
st.caption("SmartReview © 2025 ICI Laboratories, Universidad de Colima - Todos los derechos reservados")
