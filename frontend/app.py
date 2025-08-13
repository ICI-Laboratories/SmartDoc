# frontend/app.py

import streamlit as st

# --- st.set_page_config() debe ser el primer comando de Streamlit ---
st.set_page_config(
    page_title="SmartDoc",
    layout="wide",
)

# Resto de imports
from lib.common import USERNAME, USER_FOLDER


# --- Estado de Sesión para Notificaciones ---
if "processing_notifications" not in st.session_state:
    st.session_state.processing_notifications = []


# --- Lógica para mostrar notificaciones (sin emojis) ---
if st.session_state.processing_notifications:
    for notif in st.session_state.processing_notifications:
        msg = notif.get("message", "")
        kind = (notif.get("type") or "").lower()
        # Sin emojis en el texto ni en el icono (omitimos el parámetro icon)
        st.toast(msg)
    # Limpia la cola para que no se repitan
    st.session_state.processing_notifications = []


# --- Sidebar (común) ---
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

    st.title("SmartDoc: Asistente Inteligente")
    st.markdown(
        "Bienvenido. Usa la navegación de la izquierda para cargar documentos, "
        "explorarlos o iniciar una conversación con ellos."
    )

    # --- Sección explicativa para presentación (sin emojis) ---
    st.subheader("¿Qué es SmartDoc?")
    st.write(
        """
        SmartDoc es un asistente para la revisión y consulta de literatura científica,
        diseñado para funcionar totalmente de forma local, garantizando la
        confidencialidad de los documentos.

        A diferencia de herramientas basadas en la nube, SmartDoc emplea un enfoque
        de Generación Aumentada por Recuperación (RAG) modificado que procesa PDFs
        página por página y genera resúmenes estructurados y concisos (menos de 30 palabras),
        preservando el núcleo semántico y reduciendo significativamente la carga computacional.
        """
    )

    st.subheader("Funcionalidades principales")
    st.markdown(
        """
        - Extracción de texto con OCR para PDFs escaneados  
        - Clasificación temática automática  
        - Resúmenes por página con título, puntos clave e idea principal  
        - Búsqueda y respuesta basadas en contexto local  
        - Interfaz de chat para consultas en lenguaje natural
        """
    )

    st.subheader("Beneficios")
    st.markdown(
        """
        - Privacidad total: todos los procesos se realizan en el equipo local  
        - Menor carga computacional gracias a la reducción de contexto  
        - Consulta eficiente en corpus extensos y heterogéneos  
        """
    )

    # Aviso si la carpeta de datos aún no existe
    if not USER_FOLDER.exists():
        st.warning("La carpeta de datos aún no existe. Procesa al menos un documento en la sección de carga.")


if __name__ == "__main__":
    main()
