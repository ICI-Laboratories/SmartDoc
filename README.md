# SmartDoc: Asistente Inteligente de Documentos (Local-First)

SmartDoc es una plataforma de software diseñada para la revisión y consulta inteligente de literatura científica, operando exclusivamente en el entorno local del usuario para garantizar la máxima confidencialidad. Resuelve la sobrecarga informativa y los riesgos de privacidad asociados con las herramientas de IA basadas en la nube.

## ✨ Características Principales

  * **Procesamiento 100% Local**: Todos los componentes, desde el procesamiento de documentos hasta las consultas del LLM, se ejecutan en tu propia máquina. Tus documentos nunca salen de tu control.
  * **Conversión Avanzada de PDF**: Utiliza tecnologías modernas para convertir PDFs en Markdown estructurado, preservando el formato y facilitando análisis posteriores.
  * **Clasificación Automática con IA**: Organiza tus documentos en categorías y subcategorías temáticas para una fácil navegación.
  * **RAG con Contexto Adaptativo**: Emplea un enfoque de Generación Aumentada por Recuperación (RAG) que, ante un gran volumen de información, aplica una estrategia de "resumen de resúmenes" para evitar exceder el límite de contexto del LLM, garantizando respuestas robustas sin importar la cantidad de documentos consultados.
  * **Interfaz de Chat Interactiva**: Conversa con uno o varios documentos a la vez. El sistema extrae información, compara hallazgos y genera resúmenes citando siempre las fuentes.
  * **Visor Dual de Documentos**: Explora tus archivos alternando fácilmente entre una vista de Markdown limpio y el visor del PDF original incrustado.
  * **Arquitectura de Microservicios Robusta**: Construido con una arquitectura escalable que incluye un API Gateway de alto rendimiento en Rust.

## 🛠️ Pila Tecnológica

  * **Frontend**: Streamlit
  * **API Gateway**: Rust (Axum, Tokio, Rusqlite)
  * **Servicio de Documentos**: Python (FastAPI)
  * **Servicio de Lenguaje (IA)**: Python (FastAPI)

## ⚙️ Requisitos Previos

1.  **Python** (versión 3.9 o superior)
2.  **Rust**: Instálalo a través de [rustup](https://rustup.rs/).
3.  **Servidor de LLM local**: La aplicación está diseñada para conectarse a un servidor compatible con la API de OpenAI, como **LM Studio**, **Ollama** o similar, ejecutando un modelo de lenguaje.

## 🚀 Instalación y Ejecución

#### 1\. Clonar el Repositorio

```bash
git clone <URL_DE_TU_REPOSITORIO>
cd smartdoc
```

#### 2\. Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto. Puedes usar el siguiente template:

```bash
# .env

# ====== SmartDoc (Rutas y Configuración General) ======
# Ruta base absoluta donde se guardarán todos los documentos procesados.
SMARTDOC_BASE=D:\clasdocs

# ====== Endpoints de los Microservicios (Déjalos así si ejecutas todo localmente) ======
SMARTDOC_PROCESSOR_URL=http://127.0.0.1:8002
SMARTDOC_LLM_URL=http://127.0.0.1:8001

# ====== LM Studio (Configuración del Modelo de Lenguaje Local) ======
# Endpoint compatible con la API de OpenAI de tu servidor local.
SMARTDOC_LM_URL=http://localhost:1234/v1/chat/completions

# El identificador del modelo que tienes cargado.
SMARTDOC_MODEL=google/gemma-3-4b

# Tiempo máximo de espera en segundos para las peticiones al LLM.
SMARTDOC_LM_TIMEOUT=60
```

#### 3\. Instalar Dependencias de Python

Para cada servicio de Python, navega a su carpeta e instala sus dependencias:

```bash
# Para el servicio de lenguaje
cd llm_service && pip install -r requirements.txt && cd ..

# Para el servicio de documentos
cd document_processor && pip install -r requirements.txt && cd ..

# Para el frontend (incluye streamlit-pdf-viewer)
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
    uvicorn app:app --host 127.0.0.1 --port 8002
    ```

  * **Terminal 4: Frontend (Streamlit)**

    ```bash
    cd frontend
    streamlit run app.py
    ```

Una vez que todo esté en ejecución, abre tu navegador y ve a la dirección que te proporcionó Streamlit (usualmente `http://localhost:8501`).

## 👨‍💻 Uso de la API (para Desarrolladores)

Todas las peticiones a los microservicios deben pasar a través del **API Gateway**. Para identificarse, el cliente (el frontend de Streamlit) debe incluir un header HTTP:

  * **Header**: `X-User-ID`
  * **Valor**: Un nombre de usuario único (ej. `juan-perez`)

El gateway usará este ID para crear una base de datos de usuarios y organizar los archivos en carpetas separadas para cada uno.

## 🔮 Trabajo Futuro

  * Validación cuantitativa rigurosa con métricas estándar (e.g., ROUGE, BLEU).
  * Pruebas de escalabilidad con un corpus de más de 200 artículos para refinar la estrategia de contexto.
  * Optimización del rendimiento, especialmente en el módulo de conversión de PDF.
  * Soporte para más tipos de documentos (e.g., `.docx`, `.html`).

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo `LICENSE` para más detalles.