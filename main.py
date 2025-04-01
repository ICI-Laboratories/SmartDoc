import streamlit as st
import os
import json
import base64

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
    get_relevant_pages_from_summary
)

def show_pdf(file_path):
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

# Página inicial
st.set_page_config(page_title="SmartDoc", layout="wide")

# Comprobación de login
if not st.experimental_user.is_logged_in:
    st.warning("Por favor, inicia sesión")
    if st.button("Iniciar sesión", on_click=st.login):
        st.stop()
    st.stop()

user = st.experimental_user
output_folder = os.path.join(r"D:\clasdocusers", user.name)
os.makedirs(output_folder, exist_ok=True)

with st.sidebar:
    st.markdown(f"<h2 style='text-align:center;'>Bienvenido, {user.name}</h2>", unsafe_allow_html=True)
    if st.button("Cerrar sesión", on_click=st.logout):
        st.experimental_rerun()

if "processed_files" not in st.session_state:
    st.session_state.processed_files = []

# Tabs principales
tabs = st.tabs(["Cargar y Transcribir", "Revisar", "Chat"])

# --------------- Tab 1 ---------------
with tabs[0]:
    st.header("Cargar PDFs")
    uploaded_files = st.file_uploader("Sube PDFs", type=["pdf"], accept_multiple_files=True)

    if uploaded_files:
        for file in uploaded_files:
            if file.name in st.session_state.processed_files:
                st.info(f"{file.name} ya fue procesado")
                continue

            try:
                extracted_text = extract_text_with_easyocr(file)
                if not extracted_text.strip():
                    st.warning("Texto vacío")
                    continue

                main_cat, sub_cat = classify_text_with_lmstudio(extracted_text, output_folder)
                txt_path = save_to_folder(extracted_text, output_folder, file.name.replace(".pdf", ".txt"), main_cat, sub_cat)
                save_pdf_to_folder(file, output_folder, file.name, main_cat, sub_cat)

                pages = extract_pages_from_text(extracted_text)
                summary = hierarchical_summary(pages)
                summary_filename = file.name.replace(".pdf", "_hierarchical_summary.json")
                summary_folder = os.path.join(output_folder, main_cat, sub_cat)
                summary_path = os.path.join(summary_folder, summary_filename)
                save_hierarchical_summary({"title": file.name, "blocks": summary}, summary_path)

                st.success(f"Archivo procesado: {file.name}")
                st.download_button("Descargar Texto", extracted_text, file_name=os.path.basename(txt_path))
                st.session_state.processed_files.append(file.name)
            except Exception as e:
                st.error(f"Error en {file.name}: {e}")

# --------------- Tab 2 ---------------
with tabs[1]:
    st.header("Revisar archivos")
    if os.path.exists(output_folder):
        categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
        category = st.selectbox("Categoría", [""] + categories)
        if category:
            subcats = [d for d in os.listdir(os.path.join(output_folder, category))]
            subcat = st.selectbox("Subcategoría", [""] + subcats)
            if subcat:
                folder = os.path.join(output_folder, category, subcat)
                pdf_files = [f for f in os.listdir(folder) if f.lower().endswith(".pdf")]
                pdf_selected = st.selectbox("Archivo PDF", [""] + pdf_files)
                if pdf_selected:
                    show_pdf(os.path.join(folder, pdf_selected))

# --------------- Tab 3 ---------------
with tabs[2]:
    st.header("Chatear con Artículos")

    if "selected_bibliografia" not in st.session_state:
        st.session_state.selected_bibliografia = []

    with st.expander("Agregar bibliografía"):
        categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
        selected_category = st.selectbox("Categoría Chat", [""] + categories, key="chat_category")
        if selected_category:
            subcategories = [d for d in os.listdir(os.path.join(output_folder, selected_category))]
            selected_subcategory = st.selectbox("Subcategoría Chat", [""] + subcategories, key="chat_subcategory")
            if selected_subcategory:
                folder_path = os.path.join(output_folder, selected_category, selected_subcategory)
                bibliografia_files = [f for f in os.listdir(folder_path) if f.endswith("_hierarchical_summary.json")]
                selected_biblio = st.selectbox("Bibliografía", [""] + bibliografia_files, key="chat_biblio")
                if selected_biblio and st.button("Agregar bibliografía"):
                    full_path = os.path.join(folder_path, selected_biblio)
                    if full_path not in st.session_state.selected_bibliografia:
                        st.session_state.selected_bibliografia.append(full_path)
                        st.success("Bibliografía agregada.")
                    else:
                        st.info("La bibliografía ya fue agregada.")

    st.markdown("### Bibliografía seleccionada:")
    if st.session_state.selected_bibliografia:
        for idx, bib_path in enumerate(st.session_state.selected_bibliografia):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(os.path.basename(bib_path))
            with col2:
                if st.button("Quitar", key=f"remove_{idx}"):
                    st.session_state.selected_bibliografia.pop(idx)
                    st.rerun()
    else:
        st.info("No se ha agregado bibliografía.")

    question = st.text_input("Escribe tu pregunta aquí:", key="user_question")

    if st.button("Enviar pregunta"):
        if not st.session_state.selected_bibliografia:
            st.warning("Debes agregar al menos una bibliografía.")
        elif question.strip():
            combined_response = ""
            for bib_path in st.session_state.selected_bibliografia:
                bib_data = load_hierarchical_summary(bib_path)
                pages_info = get_relevant_pages_from_summary(bib_data, question)
                txt_path = bib_path.replace("_hierarchical_summary.json", ".txt")

                if os.path.exists(txt_path):
                    with open(txt_path, "r", encoding="utf-8") as f:
                        full_text = f.read()
                    context = get_pages_text_by_numbers(full_text, pages_info.get("pages", []))
                    answer = chat_with_context(context, question)
                    combined_response += f"\n### Respuesta desde {bib_data['title']}:\n{answer}\n"
                else:
                    combined_response += f"\n### Error:\nArchivo de texto no encontrado para {bib_data['title']}\n"

            st.markdown("## Respuestas combinadas:")
            st.write(combined_response)
        else:
            st.warning("Por favor, ingresa una pregunta válida.")


st.caption("Desarrollado por ICI Laboratories, Universidad de Colima")
