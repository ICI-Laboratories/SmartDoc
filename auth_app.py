import streamlit as st
import os

def login_screen():
    """Pantalla de inicio de sesión."""
    st.header("Esta aplicación es privada")
    st.subheader("Por favor, inicia sesión con tu cuenta de Google")
    # El botón llama a st.login() para iniciar el flujo de autenticación
    if st.button("Iniciar sesión con Google", on_click=st.login):
        pass

# Verifica si el usuario ya se ha autenticado
if not st.experimental_user.is_logged_in:
    login_screen()
else:
    # Una vez autenticado, se obtiene la información del usuario
    user = st.experimental_user
    st.header(f"Bienvenido, {user.name}!")
    
    # Botón para cerrar sesión
    if st.button("Cerrar sesión", on_click=st.logout):
        st.experimental_rerun()
    
    # Definir la ruta base para guardar los archivos
    base_path = r"D:\clasdocusers"
    
    # Crear una carpeta con el nombre del usuario
    user_folder = os.path.join(base_path, user.name)
    os.makedirs(user_folder, exist_ok=True)
    
    st.write(f"Tu carpeta de usuario es: {user_folder}")
    # Aquí puedes seguir integrando la lógica de la aplicación (cargar archivos, guardarlos, etc.)
