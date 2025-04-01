import streamlit as st
import os
import json
import base64

from docs import (
    extract_text_with_easyocr,
    count_words,
    save_to_folder,
    extract_pages_from_text,
    hierarchical_summary,
    save_hierarchical_summary,
    load_hierarchical_summary,
    find_block_for_page,  # Se utiliza en el chat para búsqueda por página.
    save_pdf_to_folder,
    get_pages_text_by_numbers
)
from llm import (
    classify_text_with_lmstudio,
    chat_with_context,
    get_relevant_pages_from_summary
)

def show_pdf(file_path):
    """
    Muestra un archivo PDF incrustado en la app usando un <iframe> con datos en base64.
    """
    with open(file_path, "rb") as f:
        pdf_data = f.read()
    base64_pdf = base64.b64encode(pdf_data).decode("utf-8")
    pdf_display = f"""
    <iframe 
        src="data:application/pdf;base64,{base64_pdf}" 
        width="700" 
        height="1000" 
        type="application/pdf">
    </iframe>
    """
    st.markdown(pdf_display, unsafe_allow_html=True)

# Configuración inicial de la página
st.set_page_config(page_title="Procesador, Clasificador y Chateador de PDFs", layout="wide")

# Verificar autenticación con Google
if not st.experimental_user.is_logged_in:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.header("Esta aplicación es privada")
        st.subheader("Por favor, inicia sesión con tu cuenta de Google")
        if st.button("Iniciar sesión con Google", on_click=st.login):
            st.stop()
    st.stop()
else:
    # Obtener información del usuario
    user = st.experimental_user

    # Carpeta base del usuario (ej.: "D:\clasdocusers\{usuario}")
    output_folder = os.path.join(r"D:\clasdocusers", user.name)
    os.makedirs(output_folder, exist_ok=True)

    # Barra lateral con información y opción de cerrar sesión
    st.sidebar.title("Opciones de usuario")
    st.sidebar.write(f"Usuario: {user.name}")
    if st.sidebar.button("Cerrar sesión", on_click=st.logout):
        st.experimental_rerun()

    # Inicializar key para el file uploader (para reiniciarlo tras procesar archivos)
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    # Inicializar la lista de archivos ya procesados para evitar reprocesamiento
    if "processed_files" not in st.session_state:
        st.session_state.processed_files = []

    # Selector de pestañas para las secciones principales
    tabs = st.tabs(["Cargar y Transcribir PDFs", "Revisar Archivos Guardados", "Chatear con Artículos"])

    # --------------------------------------------------------------------------
    # SECCIÓN 1: Cargar y Transcribir PDFs
    # --------------------------------------------------------------------------
    with tabs[0]:
        st.header(f"Bienvenido, {user.name}!")
        st.subheader("Cargar y Transcribir PDFs")

        uploaded_files = st.file_uploader(
            "Sube tus archivos PDF", 
            type=["pdf"], 
            accept_multiple_files=True,
            key=f"uploader_{st.session_state.uploader_key}"
        )

        new_files_processed = False  # Bandera para indicar que se procesó al menos un archivo nuevo

        if uploaded_files:
            for uploaded_file in uploaded_files:
                if uploaded_file.name in st.session_state.processed_files:
                    st.info(f"El archivo {uploaded_file.name} ya fue procesado.")
                    continue

                st.write(f"### Procesando archivo: {uploaded_file.name}")
                try:
                    # 1) EXTRAER TEXTO
                    extracted_text = extract_text_with_easyocr(uploaded_file)
                    if not extracted_text.strip():
                        st.warning(f"No se detectó texto significativo en {uploaded_file.name}.")
                        continue

                    # 2) CLASIFICAR
                    main_cat, sub_cat = classify_text_with_lmstudio(extracted_text, output_folder)
                    st.info(f"Clasificado como: {main_cat} / {sub_cat}")

                    # 3) GUARDAR TEXTO
                    txt_filename = uploaded_file.name.replace(".pdf", ".txt")
                    save_path = save_to_folder(
                        extracted_text,
                        output_folder,
                        txt_filename,
                        main_cat,
                        sub_cat
                    )

                    # 4) GUARDAR PDF ORIGINAL
                    save_pdf_to_folder(
                        uploaded_file,
                        output_folder,
                        uploaded_file.name,
                        main_cat,
                        sub_cat
                    )

                    # 5) GENERAR RESUMEN JERÁRQUICO AUTOMÁTICO
                    pages = extract_pages_from_text(extracted_text)
                    final_blocks = hierarchical_summary(pages, max_words=20000, chunk_size=5)
                    doc_title = uploaded_file.name.replace(".pdf", "")
                    final_data = {
                        "title": doc_title,
                        "blocks": final_blocks
                    }
                    summary_filename = doc_title + "_hierarchical_summary.json"
                    summary_folder = os.path.join(output_folder, main_cat, sub_cat)
                    summary_path = os.path.join(summary_folder, summary_filename)
                    save_hierarchical_summary(final_data, summary_path)

                    st.success(f"Resumen jerárquico guardado en: {summary_path}")

                    # 6) Botón para descargar el texto extraído
                    st.download_button(
                        label="Descargar texto extraído",
                        data=extracted_text,
                        file_name=os.path.basename(save_path),
                        mime="text/plain"
                    )

                    # Marcar el archivo como procesado y activar la bandera
                    st.session_state.processed_files.append(uploaded_file.name)
                    new_files_processed = True
                except Exception as e:
                    st.error(f"Error al procesar {uploaded_file.name}: {e}")

            # Si se procesó al menos un archivo nuevo, reiniciamos el file uploader
            if new_files_processed:
                st.session_state.uploader_key += 1
                st.rerun()

    # --------------------------------------------------------------------------
    # SECCIÓN 2: Revisar Archivos Guardados
    # --------------------------------------------------------------------------
    with tabs[1]:
        st.header("Revisar Archivos Guardados")
        if os.path.exists(output_folder):
            categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
            if categories:
                selected_category = st.selectbox("Selecciona una categoría:", [""] + categories)
                if selected_category:
                    subcategories = [
                        d for d in os.listdir(os.path.join(output_folder, selected_category))
                        if os.path.isdir(os.path.join(output_folder, selected_category, d))
                    ]
                    selected_subcategory = st.selectbox("Selecciona una subcategoría:", [""] + subcategories)
                    if selected_subcategory:
                        folder_path = os.path.join(output_folder, selected_category, selected_subcategory)
                        pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".pdf")]
                        if pdf_files:
                            selected_pdf = st.selectbox("Selecciona un archivo PDF para revisar:", pdf_files)
                            if selected_pdf:
                                file_path = os.path.join(folder_path, selected_pdf)
                                show_pdf(file_path)
                        else:
                            st.warning("No hay archivos PDF en esta subcategoría.")
                else:
                    st.info("Selecciona una categoría.")
            else:
                st.warning("No hay categorías creadas aún.")
        else:
            st.warning("La carpeta de usuario no existe.")

    # --------------------------------------------------------------------------
    # SECCIÓN 3: Chatear con Artículos (flujo simplificado)
    # --------------------------------------------------------------------------
    with tabs[2]:
        st.header("Chatear con Artículos")
        st.markdown(
            "Agrega la bibliografía deseada y luego formula tu pregunta. "
            "El sistema extraerá las páginas relevantes de la bibliografía para responder tu consulta."
        )

        # Inicializar la lista de bibliografía seleccionada en el estado de sesión
        if "selected_bibliografia" not in st.session_state:
            st.session_state.selected_bibliografia = []

        # Panel para agregar bibliografía
        with st.expander("Agregar bibliografía"):
            # Seleccionar categoría
            categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
            selected_category = st.selectbox("Selecciona una categoría", [""] + categories, key="chat_category")
            if selected_category:
                subcategories = [d for d in os.listdir(os.path.join(output_folder, selected_category))
                                 if os.path.isdir(os.path.join(output_folder, selected_category, d))]
                selected_subcategory = st.selectbox("Selecciona una subcategoría", [""] + subcategories, key="chat_subcategory")
                if selected_subcategory:
                    folder_path = os.path.join(output_folder, selected_category, selected_subcategory)
                    bibliografia_files = [f for f in os.listdir(folder_path) if f.endswith("_hierarchical_summary.json")]
                    selected_biblio = st.selectbox("Selecciona una bibliografía", [""] + bibliografia_files, key="chat_biblio")
                    if selected_biblio and st.button("Agregar bibliografía"):
                        full_path = os.path.join(folder_path, selected_biblio)
                        if full_path not in st.session_state.selected_bibliografia:
                            st.session_state.selected_bibliografia.append(full_path)
                            st.success("Bibliografía agregada.")
                        else:
                            st.info("La bibliografía ya fue agregada.")

        st.markdown("### Bibliografía seleccionada:")
        if st.session_state.selected_bibliografia:
            # Mostrar cada elemento con opción para quitarlo
            for idx, bib_path in enumerate(st.session_state.selected_bibliografia):
                col1, col2 = st.columns([4, 1])
                with col1:
                    rel_path = os.path.relpath(bib_path, output_folder)
                    st.write(f"{idx+1}. {rel_path}")
                with col2:
                    if st.button("Quitar", key=f"remove_{idx}"):
                        st.session_state.selected_bibliografia.pop(idx)
                        st.rerun()
        else:
            st.info("No se ha agregado bibliografía.")

        # En este flujo solo se usa la extracción de páginas relevantes
        question = st.text_input("Escribe tu pregunta:")

        if question.strip():
            if len(st.session_state.selected_bibliografia) != 1:
                st.warning("Selecciona exactamente una bibliografía para formular tu pregunta.")
            else:
                bib_data = load_hierarchical_summary(st.session_state.selected_bibliografia[0])
                pages_info = get_relevant_pages_from_summary(bib_data, question)
                if pages_info and "pages" in pages_info:
                    base_filename = bib_data["title"]  # Se asume que el .txt comparte nombre base
                    txt_filename = base_filename + ".txt"
                    txt_path = os.path.join(os.path.dirname(st.session_state.selected_bibliografia[0]), txt_filename)
                    if os.path.exists(txt_path):
                        with open(txt_path, "r", encoding="utf-8") as f:
                            full_text = f.read()
                        context = get_pages_text_by_numbers(full_text, pages_info["pages"])
                        if context.strip():
                            answer = chat_with_context(context, question)
                            st.markdown("**Respuesta del LLM (modelo potente):**")
                            st.write(answer)
                        else:
                            st.warning("No se encontró texto en las páginas indicadas.")
                    else:
                        st.error("No se encontró el archivo de texto correspondiente al documento.")
                else:
                    st.warning("No se pudo determinar las páginas relevantes para la consulta.")

    # Pie de página (opcional)
    st.caption("Desarrollado por el equipo de ICI Laboratories en la Universidad de Colima, Facultad de Ingeniería Mecánica y Eléctrica.")
