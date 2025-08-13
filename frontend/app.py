# frontend/app.py

import streamlit as st
st.set_page_config(
    page_title="SmartDoc",
    layout="wide",
)

# Ahora el resto de imports
import os
from pathlib import Path

from lib.common import USERNAME, USER_FOLDER

def sidebar_info():
    with st.sidebar:
        st.markdown("### SmartDoc")
        st.caption("Asistente de documentos")
        st.divider()
        st.markdown(f"**Usuario:** `{USERNAME}`")
        st.markdown(f"**Carpeta de datos:** `{USER_FOLDER}`")
        st.divider()
        st.caption("© 2025 SmartDoc")

def main():
    sidebar_info()
    st.title("SmartDoc")
    st.markdown(
        "Bienvenido. Use la navegación superior para cargar documentos, explorarlos o iniciar una conversación con ellos."
    )
    base = Path(USER_FOLDER)
    if not base.exists():
        st.warning("La carpeta de datos aún no existe. Procese al menos un documento en la sección de carga.")

if __name__ == "__main__":
    main()
