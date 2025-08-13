# SmartDoc: Asistente Inteligente de Documentos (Local-First)

[cite_start]SmartDoc es una plataforma de software diseñada para la revisión y consulta inteligente de literatura científica, operando exclusivamente en el entorno local del usuario para garantizar la máxima confidencialidad[cite: 4]. [cite_start]Resuelve la sobrecarga informativa y los riesgos de privacidad asociados con las herramientas de IA basadas en la nube[cite: 3, 29].

![Arquitectura de SmartDoc](https://i.imgur.com/your-architecture-diagram.png) ## Características Principales

* **Procesamiento 100% Local**: Todos los componentes, desde el procesamiento de documentos hasta las consultas del LLM, se ejecutan en tu propia máquina. [cite_start]Tus documentos nunca salen de tu control[cite: 167, 168].
* **Conversión PDF a Markdown**: Utiliza tecnologías modernas para convertir PDFs en Markdown estructurado, preservando el formato y facilitando análisis posteriores.
* [cite_start]**Clasificación Automática con IA**: Organiza automáticamente tus documentos en categorías y subcategorías temáticas para una fácil navegación[cite: 6, 92].
* [cite_start]**RAG Optimizado**: Emplea un enfoque de Generación Aumentada por Recuperación (RAG) que resume cada página de forma estructurada, permitiendo consultas eficientes y precisas en documentos largos o múltiples[cite: 5, 38, 39].
* [cite_start]**Interfaz de Chat Interactiva**: Conversa con uno o varios documentos a la vez en lenguaje natural para extraer información, comparar hallazgos y obtener resúmenes[cite: 6, 36].
* **Arquitectura de Microservicios Robusta**: Construido con una arquitectura escalable que incluye un API Gateway de alto rendimiento en Rust.

## 🛠️ Pila Tecnológica

* **Frontend**: Streamlit
* **API Gateway**: Rust, Axum, Tokio, Rusqlite
* **Servicio de Documentos**: Python, FastAPI
* **Servicio de Lenguaje (IA)**: Python, FastAPI

## ⚙️ Requisitos Previos

1.  **Python** (versión 3.9 o superior)
2.  **Rust**: Instálalo a través de [rustup](https://rustup.rs/).
3.  **Servidor de LLM local**: La aplicación está diseñada para conectarse a un servidor compatible con la API de OpenAI, como **LM Studio**, **Ollama** o similar, ejecutando un modelo de lenguaje.

## 🚀 Instalación y Ejecución

#### 1. Clonar el Repositorio

```bash
git clone <URL_DE_TU_REPOSITORIO>
cd smartdoc_produccion
````

#### 2\. Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto y configúralo según tus necesidades. Puedes usar el archivo `.env.example` como plantilla.

```bash
# .env

# Ruta base donde se guardarán todos los documentos procesados
SMARTDOC_BASE="C:/SmartDocData" # Ejemplo para Windows
# SMARTDOC_BASE="/home/tu_usuario/SmartDocData" # Ejemplo para Linux/macOS

# URL del servidor LLM que se está ejecutando localmente
SMARTDOC_LLM_URL="http://localhost:1234/v1"
```

#### 3\. Instalar Dependencias de Python

Para cada servicio de Python, navega a su carpeta e instala sus dependencias:

```bash
# Para el servicio de lenguaje
cd llm_service && pip install -r requirements.txt && cd ..

# Para el servicio de documentos
cd document_processor && pip install -r requirements.txt && cd ..

# Para el frontend
cd frontend && pip install -r requirements.txt && cd ..

#### 4\. Ejecutar la Aplicación

Abre **4 terminales** y ejecuta un servicio en cada una.

  * **Terminal 1: API Gateway (Rust)**

    ```bash
    cd api_gateway
    cargo run
    ```

    *(El gateway estará escuchando en `http://127.0.0.1:8000`)*

  * **Terminal 2: LLM Service (Python)**

    ```bash
    cd llm_service
    uvicorn api:app --host 127.0.0.1 --port 8001
    ```

  * **Terminal 3: Document Processor (Python)**

    ```bash
    cd document_processor
    uvicorn api:app --host 127.0.0.1 --port 8002
    ```

  * **Terminal 4: Frontend (Streamlit)**

    ```bash
    cd frontend
    streamlit run main.py
    ```

Una vez que todo esté en ejecución, abre tu navegador y ve a la dirección que te proporcionó Streamlit (usualmente `http://localhost:8501`).

## 👨‍💻 Uso de la API (para Desarrolladores)

Todas las peticiones a los microservicios deben pasar a través del **API Gateway**. Para identificarse, el cliente (en este caso, el frontend de Streamlit) debe incluir un header HTTP:

  * **Header**: `X-User-ID`
  * **Valor**: Un nombre de usuario único (ej. `juan-perez`)

El gateway usará este ID para crear una base de datos de usuarios y organizar los archivos en carpetas separadas para cada uno.

## 🔮 Trabajo Futuro

  * [cite\_start]Validación cuantitativa rigurosa con métricas estándar (e.g., ROUGE, BLEU)[cite: 226].
  * [cite\_start]Pruebas de escalabilidad con un corpus de más de 200 artículos[cite: 226].
  * [cite\_start]Optimización del rendimiento, especialmente en el módulo de conversión de PDF[cite: 227].
  * Soporte para más tipos de documentos (e.g., `.docx`, `.html`).

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo `LICENSE` para más detalles.

