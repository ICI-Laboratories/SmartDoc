from pathlib import Path
import streamlit as st

from lib.common import USER_FOLDER, list_categories, list_subcategories, list_md_files, read_markdown_cached

st.title("Explorar documentos")

if not USER_FOLDER.exists():
    st.info("No hay documentos. Procese algunos en la sección de carga.")
else:
    categories = list_categories(USER_FOLDER)
    col1, col2 = st.columns(2)
    selected_category = col1.selectbox("Categoría", [""] + categories, key="cat_select")

    if selected_category:
        cat_path = USER_FOLDER / selected_category
        subcategories = list_subcategories(cat_path)
        selected_subcat = col2.selectbox("Subcategoría", [""] + subcategories, key="subcat_select")

        if selected_subcat:
            subcat_path = cat_path / selected_subcat
            md_files = list_md_files(subcat_path)

            if not md_files:
                st.info("No se encontraron documentos Markdown en esta subcategoría.")
            else:
                selected_md = st.selectbox("Documento", [""] + md_files, key="md_select")
                if selected_md:
                    md_path = subcat_path / selected_md
                    try:
                        mtime_ns = md_path.stat().st_mtime_ns
                        markdown_content = read_markdown_cached(str(md_path), mtime_ns)
                        st.markdown("---")
                        st.subheader(selected_md)
                        st.markdown(markdown_content, unsafe_allow_html=True)
                    except Exception as e:
                        st.error(f"No se pudo leer el archivo: {e}")
