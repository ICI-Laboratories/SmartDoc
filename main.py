import streamlit as st
import os
import json

from docs import (
    extract_text_with_easyocr,
    count_words,
    save_to_folder,
    extract_pages_from_text,
    hierarchical_summary,
    save_hierarchical_summary,
    load_hierarchical_summary,
    find_block_for_page
)
from llm import classify_text_with_lmstudio, chat_with_context

st.set_page_config(page_title="Procesador, Clasificador y Chateador de PDFs", layout="wide")

# Verificar autenticación con Google
if not st.experimental_user.is_logged_in:
    st.header("Esta aplicación es privada")
    st.subheader("Por favor, inicia sesión con tu cuenta de Google")
    if st.button("Iniciar sesión con Google", on_click=st.login):
        st.stop()
    else:
        st.stop()
else:
    user = st.experimental_user
    st.header(f"Bienvenido, {user.name}!")
    if st.button("Cerrar sesión", on_click=st.logout):
        st.experimental_rerun()

    # Definir la carpeta de salida para el usuario autenticado
    output_folder = os.path.join(r"D:\clasdocusers", user.name)
    os.makedirs(output_folder, exist_ok=True)

    if "final_data" not in st.session_state:
        st.session_state["final_data"] = None

    menu = st.sidebar.selectbox("Selecciona una opción", [
        "Cargar y Transcribir PDFs",
        "Revisar Archivos Guardados",
        "Chatear con Artículos"
    ])

    if menu == "Cargar y Transcribir PDFs":
        st.title("Cargar y Transcribir PDFs")
        st.markdown("Sube documentos PDF para extraer y clasificar el texto.")

        uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

        if uploaded_files:
            for uploaded_file in uploaded_files:
                st.write(f"\n### Procesando archivo: {uploaded_file.name}")
                try:
                    with st.spinner("Extrayendo texto..."):
                        extracted_text = extract_text_with_easyocr(uploaded_file)
                        if not extracted_text.strip():
                            st.warning(f"No se detectó texto significativo en {uploaded_file.name}.")
                            continue
                        word_count = count_words(extracted_text)
                        st.success(f"Texto extraído con {word_count} palabras.")

                    # Clasificar el texto usando LM Studio
                    with st.spinner("Clasificando el texto..."):
                        main_cat, sub_cat = classify_text_with_lmstudio(extracted_text, output_folder)
                        st.info(f"Clasificado como: {main_cat} / {sub_cat}")

                    # Guardar el archivo en la estructura: output_folder/main_cat/sub_cat/
                    save_path = save_to_folder(
                        extracted_text,
                        output_folder,
                        uploaded_file.name.replace(".pdf", ".txt"),
                        main_cat,
                        sub_cat
                    )
                    st.info(f"Texto guardado en: `{save_path}`")
                    
                    st.download_button(
                        label="Descargar texto extraído",
                        data=extracted_text,
                        file_name=os.path.basename(save_path),
                        mime="text/plain"
                    )
                except Exception as e:
                    st.error(f"Error al procesar {uploaded_file.name}: {e}")

    elif menu == "Revisar Archivos Guardados":
        st.title("Revisar Archivos Guardados")
        st.markdown("Explora los archivos de texto extraídos y clasificados.")

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
                        files = [
                            f for f in os.listdir(os.path.join(output_folder, selected_category, selected_subcategory))
                            if f.endswith(".txt")
                        ]
                        if files:
                            selected_file = st.selectbox("Selecciona un archivo para revisar:", files)
                            if selected_file:
                                file_path = os.path.join(output_folder, selected_category, selected_subcategory, selected_file)
                                with open(file_path, "r", encoding="utf-8") as f:
                                    content = f.read()
                                st.text_area("Contenido del archivo:", content, height=400)
                        else:
                            st.warning("No hay archivos en esta subcategoría.")
            else:
                st.warning("No hay categorías creadas aún.")
        else:
            st.warning("La carpeta de usuario no existe.")

    elif menu == "Chatear con Artículos":
        st.title("Chatear con Artículos")
        st.markdown("Selecciona uno o varios artículos ya clasificados y realiza una pregunta. Generará un resumen jerárquico y lo guardará en un JSON con un título.")

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
                        files = [
                            f for f in os.listdir(os.path.join(output_folder, selected_category, selected_subcategory))
                            if f.endswith(".txt")
                        ]
                        selected_files = st.multiselect("Selecciona uno o varios archivos:", files)
                        if selected_files:
                            question = st.text_input("Escribe tu pregunta:")
                            summary_file = st.text_input("Nombre del archivo de resumen (JSON):", value="hierarchical_summary.json")

                            if len(selected_files) == 1:
                                article_title = selected_files[0].replace(".txt", "")
                            else:
                                article_title = " & ".join([f.replace(".txt", "") for f in selected_files])

                            summary_path = os.path.join(output_folder, selected_category, selected_subcategory, summary_file)
                            
                            if st.button("Generar/Usar Resumen"):
                                final_data = load_hierarchical_summary(summary_path)
                                if final_data is None:
                                    texts = []
                                    for sf in selected_files:
                                        file_path = os.path.join(output_folder, selected_category, selected_subcategory, sf)
                                        with open(file_path, "r", encoding="utf-8") as f:
                                            texts.append(f.read())
                                    all_pages = []
                                    for t in texts:
                                        pages = extract_pages_from_text(t)
                                        all_pages.extend(pages)
                                    final_blocks = hierarchical_summary(all_pages, max_words=20000, chunk_size=5)
                                    final_data = {
                                        "title": article_title,
                                        "blocks": final_blocks
                                    }
                                    save_hierarchical_summary(final_data, summary_path)
                                    st.success(f"Resumen jerárquico guardado en {summary_path}")
                                else:
                                    st.info(f"Usando resumen jerárquico existente: {summary_path}")
                                st.session_state["final_data"] = final_data

                            if st.session_state["final_data"] is not None and question.strip():
                                final_data = st.session_state["final_data"]
                                mode = st.selectbox("¿Deseas usar todo el documento o una página específica?", ["Todo el documento", "Página específica"])
                                if mode == "Todo el documento":
                                    if st.button("Preguntar (Todo el documento)"):
                                        context = ""
                                        for blk in final_data["blocks"]:
                                            context += f"--- Páginas {blk['start_page']}-{blk['end_page']} ---\n{blk['summary']}\n"
                                        answer = chat_with_context(context, question)
                                        st.markdown("**Respuesta del LLM:**")
                                        st.write(answer)
                                else:
                                    page_number = st.number_input("Número de página:", min_value=1, value=1)
                                    if st.button("Preguntar (Página específica)"):
                                        blk = find_block_for_page(final_data["blocks"], page_number)
                                        if blk:
                                            context = f"--- Páginas {blk['start_page']}-{blk['end_page']} ---\n{blk['summary']}"
                                            answer = chat_with_context(context, question)
                                            st.markdown("**Respuesta del LLM:**")
                                            st.write(answer)
                                        else:
                                            st.warning("No se encontró un bloque que contenga esa página.")
                        else:
                            st.info("Selecciona al menos un archivo.")
                    else:
                        st.info("Selecciona una subcategoría.")
                else:
                    st.info("Selecciona una categoría.")
            else:
                st.warning("No hay categorías creadas aún.")
        else:
            st.warning("La carpeta de usuario no existe.")
