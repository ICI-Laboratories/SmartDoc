# frontend/app.py

import streamlit as st

st.set_page_config(
    page_title="SmartReview",
    layout="wide",
)

# MODIFICACIÓN: Se importan solo las funciones necesarias
from lib.common import get_current_user_folder


# --- Estado de Sesión para Notificaciones ---
if "processing_notifications" not in st.session_state:
    st.session_state.processing_notifications = []


# --- Lógica para mostrar notificaciones (sin emojis) ---
if st.session_state.processing_notifications:
    for notif in st.session_state.processing_notifications:
        msg = notif.get("message", "")
        kind = (notif.get("type") or "").lower()
        st.toast(msg)
    st.session_state.processing_notifications = []


# --- Sidebar (común) ---
def sidebar_info():
    with st.sidebar:
        # MODIFICACIÓN: Se ha eliminado toda la información de depuración
        st.markdown("### SmartReview")
        st.caption("Asistente de documentos")
        st.divider()
        st.caption("© 2025 SmartReview")


def main():
    sidebar_info()

    st.title("SmartReview: Asistente Inteligente")
    st.markdown(
        "Bienvenido. Usa la navegación de la izquierda para cargar documentos, "
        "explorarlos o iniciar una conversación con ellos."
    )

    st.subheader("¿Qué es SmartReview?")
    st.write(
        """
        SmartReview es un asistente para la revisión y consulta de literatura científica,
        diseñado para funcionar totalmente de forma local, garantizando la
        confidencialidad de los documentos.

        A diferencia de herramientas basadas en la nube, SmartReview emplea un enfoque
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
    
    user_folder = get_current_user_folder()
    if not user_folder.exists():
        st.info("Tu espacio de trabajo está listo. Procesa tu primer documento en la sección de 'Cargar'.")


if __name__ == "__main__":
    main()