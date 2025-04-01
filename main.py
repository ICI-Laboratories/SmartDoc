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
    find_block_for_page,  # Ahora se utiliza en el chat para búsqueda por página.
    save_pdf_to_folder
)
from llm import classify_text_with_lmstudio, chat_with_context, get_relevant_pages_from_summary, get_pages_text_by_numbers

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

    # Carpeta base del usuario (por ejemplo, "D:\clasdocusers\{usuario}")
    output_folder = os.path.join(r"D:\clasdocusers", user.name)
    os.makedirs(output_folder, exist_ok=True)

    # Crear un estado interno para controlar la sección seleccionada
    if "selected_section" not in st.session_state:
        st.session_state["selected_section"] = None

    # Barra lateral con botones
    with st.sidebar:
        st.write(f"Usuario: {user.name}")

        if st.button("Cargar y Transcribir PDFs"):
            st.session_state["selected_section"] = "upload"

        if st.button("Revisar Archivos Guardados"):
            st.session_state["selected_section"] = "review"

        if st.button("Chatear con Artículos"):
            st.session_state["selected_section"] = "chat"

        if st.button("Cerrar sesión", on_click=st.logout):
            st.experimental_rerun()

    # Determinar la sección seleccionada
    menu = st.session_state["selected_section"]

    # --------------------------------------------------------------------------
    # SECCIÓN 1: Cargar y Transcribir PDFs
    # --------------------------------------------------------------------------
    if menu == "upload" or menu is None:
        st.header(f"Bienvenido, {user.name}!")
        st.title("Cargar y Transcribir PDFs")
        uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

        if uploaded_files:
            for uploaded_file in uploaded_files:
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
                except Exception as e:
                    st.error(f"Error al procesar {uploaded_file.name}: {e}")

    # --------------------------------------------------------------------------
    # SECCIÓN 2: Revisar Archivos Guardados
    # --------------------------------------------------------------------------
    elif menu == "review":
        st.title("Revisar Archivos Guardados")
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
                        pdf_files = [
                            f for f in os.listdir(os.path.join(output_folder, selected_category, selected_subcategory))
                            if f.lower().endswith(".pdf")
                        ]
                        if pdf_files:
                            selected_pdf = st.selectbox("Selecciona un archivo PDF para revisar:", pdf_files)
                            if selected_pdf:
                                file_path = os.path.join(output_folder, selected_category, selected_subcategory, selected_pdf)
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
    # SECCIÓN 3: Chatear con Artículos (flujo modificado)
    # --------------------------------------------------------------------------
        # Dentro de la sección "Chatear con Artículos"...
    elif menu == "chat":
        st.title("Chatear con Artículos")
        st.markdown(
            "Agrega los resúmenes deseados y luego formula tu pregunta. "
            "Puedes optar por extraer únicamente las páginas relevantes según el resumen."
        )

        # (Mantener el panel para agregar resúmenes, igual que en la versión anterior)
        if "selected_summaries" not in st.session_state:
            st.session_state.selected_summaries = []

        with st.expander("Agregar resúmenes"):
            # [Código similar para seleccionar categoría, subcategoría y resumen...]
            # Se conserva el flujo de selección y agregación de resúmenes.
            # ...

            st.write("**Resúmenes seleccionados:**")
        if st.session_state.selected_summaries:
            for idx, summary_path in enumerate(st.session_state.selected_summaries):
                rel_path = os.path.relpath(summary_path, output_folder)
                st.write(f"{idx+1}. {rel_path}")
        else:
            st.info("No se han agregado resúmenes.")

        # Nueva opción de flujo:
        mode = st.selectbox(
            "Elige el modo de consulta:",
            ["Extracción de páginas relevante", "Todo el documento", "Página específica"]
        )

        question = st.text_input("Escribe tu pregunta:")

        if question.strip():
            if mode == "Extracción de páginas relevante":
                # Para este modo asumimos que se ha agregado un solo resumen
                if len(st.session_state.selected_summaries) != 1:
                    st.warning("Selecciona exactamente un resumen para este modo.")
                else:
                    summary_data = load_hierarchical_summary(st.session_state.selected_summaries[0])
                    # Llamamos a la función que indica las páginas relevantes
                    pages_info = get_relevant_pages_from_summary(summary_data, question)
                    if pages_info and "pages" in pages_info:
                        st.write("El LLM determinó que se requieren las siguientes páginas:")
                        st.write(pages_info["pages"])
                        # Asumimos que el archivo de texto se encuentra en la misma carpeta que el resumen,
                        # con el mismo nombre base pero con extensión .txt.
                        base_filename = summary_data["title"]  # O extraer del nombre del archivo resumen
                        txt_filename = base_filename + ".txt"
                        txt_path = os.path.join(os.path.dirname(st.session_state.selected_summaries[0]), txt_filename)
                        if os.path.exists(txt_path):
                            with open(txt_path, "r", encoding="utf-8") as f:
                                full_text = f.read()
                            context = get_pages_text_by_numbers(full_text, pages_info["pages"])
                            if context.strip():
                                # Llamar al modelo potente con el contexto extraído
                                answer = chat_with_context(context, question)
                                st.markdown("**Respuesta del LLM (modelo potente):**")
                                st.write(answer)
                            else:
                                st.warning("No se encontró texto en las páginas indicadas.")
                        else:
                            st.error("No se encontró el archivo de texto correspondiente al documento.")
                    else:
                        st.warning("No se pudo determinar las páginas relevantes.")
            elif mode == "Todo el documento":
                # Flujo actual para un solo resumen
                if len(st.session_state.selected_summaries) != 1:
                    st.warning("Selecciona exactamente un resumen para este modo.")
                else:
                    summary_data = load_hierarchical_summary(st.session_state.selected_summaries[0])
                    if st.button("Preguntar (Todo el documento)"):
                        context = "\n".join([blk["small_summary"] for blk in summary_data["blocks"]])
                        answer = chat_with_context(context, question)
                        st.markdown("**Respuesta del LLM:**")
                        st.write(answer)
            else:  # Página específica
                if len(st.session_state.selected_summaries) != 1:
                    st.warning("Selecciona exactamente un resumen para este modo.")
                else:
                    summary_data = load_hierarchical_summary(st.session_state.selected_summaries[0])
                    page_number = st.number_input("Número de página:", min_value=1, value=1)
                    if st.button("Preguntar (Página específica)"):
                        the_block = find_block_for_page(summary_data["blocks"], page_number)
                        if the_block:
                            context = the_block["small_summary"]
                            answer = chat_with_context(context, question)
                            st.markdown("**Respuesta del LLM:**")
                            st.write(answer)
                        else:
                            st.warning("No se encontró ese número de página en el resumen.")


    # Pie de página (opcional)
    st.caption("Desarrollado por el equipo de ICI Laboratories en la Universidad de Colima, Facultad de Ingeniería Mecánica y Eléctrica.")
