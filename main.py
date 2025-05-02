import streamlit as st
import os
import json
import base64
import time

from docs import (
    extract_text_with_easyocr,
    save_to_folder,
    extract_pages_from_text,
    hierarchical_summary,
    save_hierarchical_summary,
    load_hierarchical_summary,
    save_pdf_to_folder,
    get_pages_text_by_numbers
)
from llm import (
    classify_text_with_lmstudio,
    chat_with_context,
    chat_with_multiple_docs,
    get_relevant_pages_from_summary
)

def show_pdf(file_path):
    """Mostrar un PDF embebido en la página."""
    with open(file_path, "rb") as f:
        pdf_data = f.read()
    base64_pdf = base64.b64encode(pdf_data).decode("utf-8")
    pdf_display = f"""
    <iframe 
        src="data:application/pdf;base64,{base64_pdf}" 
        width="100%" 
        height="800" 
        type="application/pdf">
    </iframe>
    """
    st.markdown(pdf_display, unsafe_allow_html=True)

# Configuración de la página
st.set_page_config(
    page_title="SmartDoc - Asistente Inteligente para Documentos",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Aplicar estilos personalizados
st.markdown("""
    <style>
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    h1, h2, h3, h4 {
        margin-bottom: 1rem;
    }
    .chat-message {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
        display: flex;
        flex-direction: column;
    }
    .chat-message-user {
        border-left: 5px solid #4361ee;
    }
    .chat-message-assistant {
        border-left: 5px solid #3498db;
    }
    .chat-message-heading {
        font-weight: bold;
        margin-bottom: 0.5rem;
    }
    .stButton button {
        width: 100%;
    }
    </style>
""", unsafe_allow_html=True)

# Comprobación de login
if not st.user.is_logged_in:
    st.title("SmartDoc 📚")
    st.subheader("Asistente Inteligente para el Análisis de Documentos")
    
    st.markdown("""
    SmartDoc te permite:
    1. Cargar y analizar documentos PDF
    2. Organizar automáticamente tus documentos por categorías
    3. Hacer preguntas sobre el contenido de tus documentos
    4. Obtener respuestas precisas basadas en la información de tus documentos
    
    **Por favor, inicia sesión para comenzar**
    """)
    
    if st.button("Iniciar sesión", on_click=st.login):
        st.stop()
    st.stop()

# Inicializar variables de sesión
if "processed_files" not in st.session_state:
    st.session_state.processed_files = []

if "selected_bibliografia" not in st.session_state:
    st.session_state.selected_bibliografia = []

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "clear_question" not in st.session_state:
    st.session_state.clear_question = False

if "refresh_uploader" not in st.session_state:
    st.session_state.refresh_uploader = False

# Configuración del usuario
user = st.user
output_folder = os.path.join(r"D:\clasdocusers", user.name)
os.makedirs(output_folder, exist_ok=True)

# Sidebar con información del usuario
with st.sidebar:
    st.markdown(f"<h2 style='text-align:center;'>Bienvenido, {user.name}</h2>", unsafe_allow_html=True)
    
    # Estadísticas de uso
    st.markdown("### Estadísticas")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Documentos", len(st.session_state.processed_files))
    with col2:
        st.metric("Consultas", len(st.session_state.chat_history))
    
    if st.button("🚪 Cerrar sesión", on_click=st.logout):
        st.rerun()
    
    st.markdown("---")
    st.caption("Desarrollado por ICI Laboratories, Universidad de Colima")

# Tabs principales
tabs = st.tabs(["📤 Cargar Documentos", "📖 Revisar Documentos", "💬 Consultar Documentos"])

# --------------- Tab 1: Cargar Documentos ---------------
with tabs[0]:
    st.header("📤 Cargar y Procesar Documentos")
    
    # Instrucciones de uso
    with st.expander("ℹ️ Instrucciones de uso", expanded=True):
        st.markdown("""
        **Cómo usar esta sección:**
        
        1. Haz clic en "Examinar archivos" para seleccionar uno o más documentos PDF.
        2. Espera mientras el sistema procesa los documentos (esto puede tomar unos minutos).
        3. Los documentos se analizarán y clasificarán automáticamente en categorías.
        4. Una vez procesados, podrás consultarlos en la pestaña "Consultar Documentos".
        
        > **Nota:** El sistema tiene un límite de 70 MB por archivo.
        """)
    
    # Función para limpiar el uploader
    def refresh_uploader_callback():
        st.session_state.refresh_uploader = True
        st.rerun()
    
    # Área de carga de archivos
    st.subheader("Selecciona los documentos a procesar")
    
    # Crear un ID único para el uploader si necesitamos refrescarlo
    uploader_key = f"pdf_uploader_{time.time()}" if st.session_state.refresh_uploader else "pdf_uploader"
    
    # Restablecer el flag de refresco
    if st.session_state.refresh_uploader:
        st.session_state.refresh_uploader = False
    
    uploaded_files = st.file_uploader(
        "Arrastra o selecciona archivos PDF (máximo 70 MB por archivo)",
        type=["pdf"],
        accept_multiple_files=True,
        key=uploader_key
    )

    if uploaded_files:
        st.subheader("Procesando documentos")
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total_files = len(uploaded_files)
        processed_count = 0
        newly_processed = []
        
        for file in uploaded_files:
            file_display_name = file.name[:40] + "..." if len(file.name) > 40 else file.name
            
            if file.name in st.session_state.processed_files:
                status_text.info(f"⏭️ Omitiendo {file_display_name} (ya procesado)")
                processed_count += 1
                progress_bar.progress(processed_count / total_files)
                continue

            try:
                status_text.info(f"⚙️ Procesando {file_display_name}...")
                
                # Procesamiento del documento
                extracted_text = extract_text_with_easyocr(file)
                if not extracted_text.strip():
                    status_text.warning(f"⚠️ {file_display_name}: No se pudo extraer texto")
                    continue

                # Clasificación del documento
                main_cat, sub_cat = classify_text_with_lmstudio(extracted_text, output_folder)
                
                # Guardado del documento y su texto
                txt_path = save_to_folder(extracted_text, output_folder, file.name.replace(".pdf", ".txt"), main_cat, sub_cat)
                save_pdf_to_folder(file, output_folder, file.name, main_cat, sub_cat)
                
                # Generación de resumen jerárquico
                pages = extract_pages_from_text(extracted_text)
                summary = hierarchical_summary(pages)
                summary_filename = file.name.replace(".pdf", "_hierarchical_summary.json")
                summary_folder = os.path.join(output_folder, main_cat, sub_cat)
                summary_path = os.path.join(summary_folder, summary_filename)
                save_hierarchical_summary({"title": file.name, "blocks": summary}, summary_path)

                # Actualizar estado
                st.session_state.processed_files.append(file.name)
                newly_processed.append({
                    "name": file.name,
                    "category": f"{main_cat} / {sub_cat}",
                    "txt_path": txt_path
                })
                
                processed_count += 1
                progress_bar.progress(processed_count / total_files)
                
            except Exception as e:
                status_text.error(f"❌ Error al procesar {file_display_name}: {str(e)}")
        
        # Resumen final
        if newly_processed:
            status_text.success(f"✅ Procesamiento completado: {len(newly_processed)} documentos nuevos procesados")
            
            st.subheader("Documentos procesados")
            for doc in newly_processed:
                with st.expander(f"📄 {doc['name']}"):
                    st.write(f"**Categoría:** {doc['category']}")
                    st.download_button(
                        "⬇️ Descargar texto extraído",
                        open(doc['txt_path'], 'r', encoding='utf-8').read(),
                        file_name=os.path.basename(doc['txt_path'])
                    )
            
            # Botón para limpiar el área de carga
            if st.button("🔄 Procesar más documentos"):
                refresh_uploader_callback()
        else:
            status_text.info("ℹ️ No se procesaron nuevos documentos")

# --------------- Tab 2: Revisar Documentos ---------------
with tabs[1]:
    st.header("📖 Explorar y Revisar Documentos")
    
    with st.expander("ℹ️ Instrucciones de uso", expanded=True):
        st.markdown("""
        **Cómo usar esta sección:**
        
        1. Selecciona una categoría principal en el primer desplegable.
        2. Selecciona una subcategoría en el segundo desplegable.
        3. Selecciona un documento para visualizarlo.
        4. El documento se mostrará en un visor integrado donde podrás leerlo.
        
        > **Consejo:** Usa el botón de descarga en la esquina superior derecha del visor para guardar el PDF.
        """)
    
    # Selector de documentos
    if os.path.exists(output_folder):
        categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
        
        if not categories:
            st.info("📂 No hay documentos disponibles. Carga algunos documentos en la pestaña 'Cargar Documentos'.")
        else:
            col1, col2 = st.columns(2)
            
            with col1:
                category = st.selectbox("Selecciona una categoría", [""] + categories, key="review_category")
            
            if category:
                with col2:
                    subcats = [d for d in os.listdir(os.path.join(output_folder, category)) if os.path.isdir(os.path.join(output_folder, category, d))]
                    subcat = st.selectbox("Selecciona una subcategoría", [""] + subcats, key="review_subcat")
                
                if subcat:
                    folder = os.path.join(output_folder, category, subcat)
                    pdf_files = [f for f in os.listdir(folder) if f.lower().endswith(".pdf")]
                    
                    if not pdf_files:
                        st.info(f"📂 No hay documentos PDF en la categoría {category}/{subcat}")
                    else:
                        pdf_selected = st.selectbox(
                            "Selecciona un documento para visualizar",
                            [""] + pdf_files,
                            key="review_pdf"
                        )
                        
                        if pdf_selected:
                            st.subheader(f"Visualizando: {pdf_selected}")
                            show_pdf(os.path.join(folder, pdf_selected))

# --------------- Tab 3: Consultar Documentos ---------------
with tabs[2]:
    st.header("💬 Consultar Documentos")
    
    with st.expander("ℹ️ Instrucciones de uso", expanded=True):
        st.markdown("""
        **Cómo usar esta sección:**
        
        1. Selecciona los documentos que quieres consultar usando el panel "Agregar documentos".
        2. Escribe tu pregunta en el campo de texto en la parte inferior.
        3. Haz clic en "Enviar pregunta" para obtener una respuesta basada en los documentos seleccionados.
        4. Puedes hacer múltiples preguntas y la conversación se mantendrá en el historial.
        
        > **Consejo:** Para obtener mejores resultados, selecciona documentos relacionados con tu consulta.
        """)
    
    # Panel lateral para seleccionar documentos
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("Documentos seleccionados")
        
        with st.expander("➕ Agregar documentos", expanded=len(st.session_state.selected_bibliografia) == 0):
            categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
            
            if not categories:
                st.info("📂 No hay documentos disponibles. Carga algunos documentos en la pestaña 'Cargar Documentos'.")
            else:
                selected_category = st.selectbox("Categoría", [""] + categories, key="chat_category")
                
                if selected_category:
                    subcategories = [d for d in os.listdir(os.path.join(output_folder, selected_category)) if os.path.isdir(os.path.join(output_folder, selected_category, d))]
                    selected_subcategory = st.selectbox("Subcategoría", [""] + subcategories, key="chat_subcategory")
                    
                    if selected_subcategory:
                        folder_path = os.path.join(output_folder, selected_category, selected_subcategory)
                        bibliografia_files = [f for f in os.listdir(folder_path) if f.endswith("_hierarchical_summary.json")]
                        
                        if bibliografia_files:
                            selected_biblio = st.selectbox(
                                "Documento",
                                [""] + [f.replace("_hierarchical_summary.json", ".pdf") for f in bibliografia_files],
                                key="chat_biblio"
                            )
                            
                            if selected_biblio and st.button("➕ Agregar documento"):
                                summary_file = selected_biblio.replace(".pdf", "_hierarchical_summary.json")
                                full_path = os.path.join(folder_path, summary_file)
                                
                                if full_path not in st.session_state.selected_bibliografia:
                                    st.session_state.selected_bibliografia.append(full_path)
                                    st.success(f"✅ Documento agregado: {selected_biblio}")
                                    st.rerun()
                                else:
                                    st.info("ℹ️ Este documento ya está en la lista")
                        else:
                            st.info("📂 No hay documentos en esta subcategoría")
        
        # Lista de documentos seleccionados
        if st.session_state.selected_bibliografia:
            for idx, bib_path in enumerate(st.session_state.selected_bibliografia):
                doc_name = os.path.basename(bib_path).replace("_hierarchical_summary.json", ".pdf")
                
                st.markdown(
                    f"""
                    <div style="padding: 10px; border-radius: 5px; margin-bottom: 10px; ">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div style="flex-grow: 1;">📄 {doc_name}</div>
                        </div>
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
                
                # Botón para quitar el documento
                if st.button("❌ Quitar", key=f"remove_{idx}"):
                    st.session_state.selected_bibliografia.pop(idx)
                    st.rerun()
        else:
            st.info("⚠️ No has seleccionado ningún documento")
    
    # Panel principal de chat
    with col2:
        st.subheader("Conversación")
        
        # Área de entrada para preguntas
        question_placeholder = "" if st.session_state.clear_question else st.session_state.get("user_question", "")
        
        # Reinicia el flag
        if st.session_state.clear_question:
            st.session_state.clear_question = False
        
        # Campo de entrada y botón de envío
        question = st.text_input(
            "Escribe tu pregunta:",
            key="user_question",
            value=question_placeholder,
            placeholder="¿Qué deseas saber sobre los documentos seleccionados?"
        )
        
        # Botón de envío
        if st.button("📤 Enviar pregunta"):
            if not st.session_state.selected_bibliografia:
                st.warning("⚠️ Debes seleccionar al menos un documento")
            elif not question.strip():
                st.warning("⚠️ Por favor, escribe una pregunta")
            else:
                # Mostrar indicador de carga
                with st.spinner("⏳ Procesando tu pregunta..."):
                    # Preparar documentos
                    doc_summaries = []
                    doc_txt_paths = []
                    
                    for bib_path in st.session_state.selected_bibliografia:
                        # Cargar resumen
                        bib_data = load_hierarchical_summary(bib_path)
                        doc_summaries.append(bib_data)
                        
                        # Obtener ruta al archivo de texto
                        txt_path = bib_path.replace("_hierarchical_summary.json", ".txt")
                        doc_txt_paths.append(txt_path)
                    
                    # Obtener respuesta
                    if len(doc_summaries) == 1:
                        # Si hay un solo documento
                        bib_data = doc_summaries[0]
                        pages_info = get_relevant_pages_from_summary(bib_data, question)
                        txt_path = doc_txt_paths[0]
                        
                        if os.path.exists(txt_path):
                            with open(txt_path, "r", encoding="utf-8") as f:
                                full_text = f.read()
                            context = get_pages_text_by_numbers(full_text, pages_info.get("pages", []))
                            answer = chat_with_context(context, question)
                            combined_response = f"{answer}\n\n*Fuente: {os.path.basename(txt_path)} (páginas: {', '.join(map(str, pages_info.get('pages', [])))})*"
                        else:
                            combined_response = f"⚠️ Error: No se encontró el archivo de texto para {bib_data['title']}"
                    else:
                        # Si hay múltiples documentos
                        combined_response = chat_with_multiple_docs(doc_summaries, doc_txt_paths, question)
                    
                    # Guardar en el historial
                    st.session_state.chat_history.append({
                        "question": question,
                        "answer": combined_response
                    })
                    
                    # Limpiar campo de pregunta
                    st.session_state.clear_question = True
                    st.rerun()
        
        # Separador visual
        st.markdown("---")
        
        # Contenedor para el historial de chat
        chat_container = st.container()
        
        # Mostrar historial de conversación
        with chat_container:
            if not st.session_state.chat_history:
                st.info("💬 Aún no hay mensajes. Comienza por seleccionar documentos y hacer una pregunta.")
            else:
                # Mostrar mensajes en orden cronológico inverso (los más recientes primero)
                for entry in reversed(st.session_state.chat_history):
                    # Mensaje del usuario
                    st.markdown(
                        f"""
                        <div class="chat-message chat-message-user">
                            <div class="chat-message-heading">🙋 Tú preguntaste:</div>
                            {entry['question']}
                        </div>
                        """, 
                        unsafe_allow_html=True
                    )
                    
                    # Respuesta del asistente
                    st.markdown(
                        f"""
                        <div class="chat-message chat-message-assistant">
                            <div class="chat-message-heading">🤖 Respuesta:</div>
                            {entry['answer']}
                        </div>
                        """, 
                        unsafe_allow_html=True
                    )

# Pie de página
st.markdown("---")
st.caption("SmartReview © 2025 ICI Laboratories, Universidad de Colima - Todos los derechos reservados")