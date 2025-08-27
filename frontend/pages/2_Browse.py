# frontend/pages/2_Browse.py

from pathlib import Path
import streamlit as st
from streamlit_pdf_viewer import pdf_viewer

from lib.common import (
    get_current_user_folder,
    list_categories,
    list_subcategories,
    list_md_files,
    read_markdown_cached,
)

st.title("Explorar y Visualizar Documentos")
st.markdown("Selecciona una categoría, una subcategoría y un documento para verlo en Markdown o como PDF.")

# Usa la ruta del usuario actual, no una ruta global
USER_FOLDER = get_current_user_folder()

if not USER_FOLDER.exists():
    st.info("No has procesado ningún documento todavía. Ve a la sección de 'Cargar' para empezar.")
    st.stop()

# --- Selección de categoría y subcategoría ---
categories = list_categories()
col1, col2 = st.columns(2)

selected_category = col1.selectbox("Categoría", [""] + categories, key="cat_select")
if not selected_category:
    st.stop()

cat_path = USER_FOLDER / selected_category
subcategories = list_subcategories(cat_path)
selected_subcat = col2.selectbox("Subcategoría", [""] + subcategories, key="subcat_select")
if not selected_subcat:
    st.stop()

# --- Listado de documentos (Markdown) ---
subcat_path = cat_path / selected_subcat
md_files = list_md_files(subcat_path)
if not md_files:
    st.info("No se encontraron documentos Markdown en esta subcategoría.")
    st.stop()

selected_md_file = st.selectbox("Documento", [""] + md_files, key="md_select")
if not selected_md_file:
    st.stop()

st.markdown("---")

# Rutas del documento seleccionado
md_path = subcat_path / selected_md_file
pdf_path = md_path.with_suffix(".pdf")

# --- Botón para descargar el Markdown ---
try:
    md_bytes = md_path.read_bytes()
    st.download_button(
        label="Descargar Markdown",
        data=md_bytes,
        file_name=md_path.name,
        mime="text/markdown",
    )
except Exception as e:
    st.error(f"No se pudo preparar la descarga del Markdown: {e}")

# --- Pestañas de visualización ---
tab_markdown, tab_pdf = st.tabs(["Vista Markdown", "Vista PDF"])

# Vista Markdown
with tab_markdown:
    st.subheader(f"Contenido de: {selected_md_file}")
    try:
        mtime_ns = md_path.stat().st_mtime_ns
        markdown_content = read_markdown_cached(str(md_path), mtime_ns)
        st.markdown(markdown_content, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"No se pudo leer el archivo Markdown: {e}")

# Vista PDF
with tab_pdf:
    st.subheader(f"PDF: {pdf_path.name}")
    if pdf_path.exists():
        try:
            pdf_bytes = pdf_path.read_bytes()
            pdf_viewer(pdf_bytes, height=800)
        except Exception as e:
            st.error(f"No se pudo cargar el PDF: {e}")
    else:
        st.warning("No se encontró el archivo PDF correspondiente para este documento.")