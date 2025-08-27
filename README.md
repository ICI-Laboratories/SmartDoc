# SmartDoc: Asistente Inteligente de Documentos (Local-First)

SmartDoc es una plataforma de software diseñada para la revisión y consulta inteligente de literatura científica. Opera exclusivamente en el entorno local del usuario para garantizar la máxima confidencialidad y resuelve la sobrecarga informativa asociada con la investigación moderna.

A través de una arquitectura de microservicios robusta y flujos de IA avanzados, SmartDoc transforma una carpeta de PDFs desorganizados en una base de conocimiento interactiva y analizable.

## ✨ Características Principales

  * **Procesamiento 100% Local**: Todos los componentes, desde la conversión de PDF hasta las consultas del LLM y los modelos de embedding, se ejecutan en tu propia máquina. Tus documentos nunca salen de tu control.
  * **Conversión Híbrida de PDF**: Detecta automáticamente si un PDF contiene texto nativo para una extracción rápida y precisa, o si es un documento escaneado que requiere OCR de alta calidad.
  * **Clasificación Automática con IA**: Organiza inteligentemente los documentos en categorías y subcategorías temáticas para una fácil navegación.
  * **Chat Interactivo (RAG Adaptativo)**: Conversa con uno o varios documentos a la vez. El sistema utiliza un avanzado flujo de Generación Aumentada por Recuperación (RAG) que se adapta a la cantidad de información para evitar sobrecargar el contexto del LLM.
  * **Búsqueda Híbrida Avanzada**: Combina la potencia de la búsqueda semántica (por significado) con la precisión de la búsqueda por palabras clave (BM25) para encontrar los fragmentos de texto más relevantes con una precisión excepcional.
  * **Análisis Cuantitativo**: Genera un mapa de calor de similitud conceptual entre documentos, permitiendo descubrir clústeres de investigación y relaciones ocultas en tu biblioteca.
  * **Arquitectura de Microservicios Robusta**: Construido con una arquitectura escalable que incluye un API Gateway de alto rendimiento en Rust para gestionar la comunicación entre servicios.
  * **Multiusuario Persistente**: Cada visitante que accede a la aplicación a través de la web recibe un espacio de trabajo aislado y persistente, asegurando que sus documentos sean privados y no se pierdan al recargar la página.

## 🏛️ Arquitectura del Sistema

SmartDoc opera sobre una arquitectura de microservicios, donde cada componente tiene una responsabilidad clara, garantizando escalabilidad y mantenibilidad.

  * **Frontend (Streamlit)**: La interfaz de usuario web con la que interactúas. Es responsable de la carga de archivos, la visualización de documentos y las interfaces de chat y análisis.
  * **API Gateway (Rust - Axum)**: El punto de entrada único para todas las peticiones del frontend. Gestiona la autenticación de usuarios y enruta el tráfico de manera eficiente y segura al servicio interno correspondiente.
  * **Document Processor (Python - FastAPI)**: El servicio de trabajo pesado. Se encarga de recibir los PDFs, convertirlos a Markdown, generar los resúmenes por página y crear los embeddings vectoriales para la búsqueda.
  * **LLM Service (Python - FastAPI)**: El cerebro de la operación. Contiene la lógica para interactuar con el modelo de lenguaje (para chat y clasificación) y para realizar los análisis cuantitativos (búsqueda híbrida, cálculo de similitud).

## 🧠 Flujos de Inteligencia Artificial

SmartDoc utiliza dos flujos de IA distintos pero complementarios para potenciar sus funcionalidades.

### Flujo 1: Chat con Documentos (RAG con Contexto Adaptativo)

Cuando un usuario chatea con los documentos, se activa un sofisticado proceso de RAG:

1.  **Filtro de Relevancia Inicial**: El sistema toma los resúmenes de todos los documentos seleccionados y le pide al LLM que identifique qué documentos y páginas son los más relevantes para la pregunta del usuario.
2.  **Construcción de Contexto**: Se recopila el texto completo de las páginas identificadas como relevantes.
3.  **Guardián del Contexto (Estrategia Adaptativa)**:
      * **Si el texto cabe en el contexto del LLM**, se envía directamente para obtener una respuesta detallada y precisa, basada en la fuente primaria.
      * **Si el texto es demasiado grande**, el sistema activa un modo "Map-Reduce". En lugar de enviar el texto completo, envía los resúmenes concisos de las páginas relevantes, pidiéndole al LLM que sintetice una respuesta a partir de esa información de alto nivel. Esto garantiza que el sistema siempre pueda responder, sin importar la cantidad de información.
4.  **Generación de Respuesta y Citado de Fuentes**: El LLM genera la respuesta basándose únicamente en el contexto proporcionado, y la aplicación adjunta una lista de los documentos y páginas consultadas.

### Flujo 2: Búsqueda y Análisis (Búsqueda Híbrida)

Cuando se utiliza la función de "Análisis Semántico", el flujo es diferente y está optimizado para la precisión:

1.  **Vectorización de la Consulta**: La pregunta o palabra clave del usuario se convierte en un vector numérico usando un modelo de embedding de última generación (ej. `BAAI/bge-large-en-v1.5`).
2.  **Búsqueda Paralela**: El sistema realiza dos búsquedas simultáneamente en los fragmentos de texto (`chunks`) de los documentos seleccionados:
      * **Búsqueda Semántica**: Compara el vector de la consulta con los vectores precalculados de los chunks para encontrar aquellos con el significado más similar (similitud del coseno).
      * **Búsqueda por Palabras Clave**: Utiliza el algoritmo BM25 para encontrar los chunks que contienen las palabras clave exactas de la consulta.
3.  **Fusión y Re-ranking**: Los resultados de ambas búsquedas se combinan mediante una puntuación ponderada. Esto produce un ranking final que valora tanto la relevancia contextual (semántica) como la precisión literal (palabras clave).
4.  **Presentación de Resultados**: El frontend muestra los fragmentos de texto mejor clasificados, junto con su puntuación de relevancia y el número de veces que aparecen las palabras clave.

## 🛠️ Pila Tecnológica

  * **Frontend**: Streamlit
  * **API Gateway**: Rust (Axum, Tokio, Rusqlite)
  * **Servicios de Backend**: Python (FastAPI)
  * **Modelos de Embeddings**: `sentence-transformers` (ej. `BAAI/bge-large-en-v1.5`)
  * **Búsqueda por Palabras Clave**: `rank-bm25`

## ⚙️ Requisitos Previos

1.  **Python** (versión 3.9 o superior)
2.  **Rust**: Instálalo a través de [rustup](https://rustup.rs/).
3.  **Servidor de LLM local**: La aplicación está diseñada para conectarse a un servidor compatible con la API de OpenAI, como **LM Studio** u **Ollama**, que esté ejecutando un modelo de lenguaje.

## 🚀 Instalación y Ejecución

#### 1\. Clonar el Repositorio

```bash
git clone <URL_DE_TU_REPOSITORIO>
cd smartdoc
```

#### 2\. Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto. Puedes usar la siguiente plantilla:

```bash
# .env

# Ruta base absoluta donde SmartDoc guardará todos los documentos procesados.
# ¡IMPORTANTE! Usa una ruta absoluta. Ejemplo en Windows: D:\SmartDocData, en Linux/macOS: /home/user/SmartDocData
SMARTDOC_BASE=D:\clasdocs

# Endpoint compatible con la API de OpenAI de tu servidor de LLM local (LM Studio, Ollama, etc.).
SMARTDOC_LM_URL=http://localhost:1234/v1/chat/completions

# El identificador del modelo que tienes cargado en tu servidor local.
SMARTDOC_MODEL=google/gemma-3-4b

# Tiempo máximo de espera en segundos para las peticiones al LLM.
SMARTDOC_LM_TIMEOUT=60
```

#### 3\. Instalar Dependencias

Para cada servicio, navega a su carpeta e instala sus dependencias. Se recomienda usar un único entorno virtual para todo el proyecto.

```bash
# Activa tu entorno virtual
# python -m venv env
# source env/bin/activate (Linux/macOS) o env\Scripts\activate (Windows)

# Instalar dependencias del proyecto (un solo requirements.txt en la raíz)
pip install -r requirements.txt
```

#### 4\. Ejecutar la Aplicación

Abre las terminales necesarias y ejecuta un servicio en cada una.

  * **Terminal 1: API Gateway (Rust)**

    ```bash
    cd api_gateway
    cargo run
    ```

    *(El gateway estará escuchando en `http://127.0.0.1:8000`)*

  * **Terminal 2: LLM Service (Python)**

    ```bash
    # Activa tu entorno virtual si no lo has hecho
    uvicorn llm_service.api:app --host 127.0.0.1 --port 8001
    ```

  * **Terminal 3: Document Processor (Python)**

    ```bash
    uvicorn document_processor.app:app --host 127.0.0.1 --port 8002
    ```

  * **Terminal 4: Frontend (Streamlit)**

    ```bash
    cd frontend
    streamlit run app.py
    ```

  * **Terminal 5: Cloudflare Tunnel (Opcional, para demo remota)**

    ```bash
    # Navega a la carpeta donde tienes cloudflared.exe
    .\cloudflared.exe tunnel run <nombre-del-tunel>
    ```

Una vez que todo esté en ejecución, abre tu navegador y ve a la dirección que te proporcionó Streamlit (usualmente `http://localhost:8501`) o a la URL pública de Cloudflare.

## 🌐 Configuración para Demo Remota con Cloudflare (Opcional)

Para exponer tu aplicación local de forma segura en internet para una demostración, puedes usar Cloudflare Tunnel.

  * **Paso 1: Instalar `cloudflared`**
    Descarga el ejecutable desde la [página oficial de Cloudflare](https://www.google.com/search?q=https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/install-and-setup/installation/) (para Windows, es un único archivo `.exe`).

  * **Paso 2: Autenticar**
    Abre una terminal y ejecuta `cloudflared tunnel login`. Se abrirá un navegador para que inicies sesión y autorices el dominio.

  * **Paso 3: Crear el Túnel**
    `cloudflared tunnel create <nombre-para-tu-tunel>` (ej. `smartreview-tunnel`). Guarda el UUID que te devolverá.

  * **Paso 4: Configurar el Túnel**
    Crea un archivo `config.yml` en la carpeta `.cloudflared` de tu usuario con el siguiente contenido, reemplazando el UUID y tu hostname:

    ```yaml
    tunnel: <TU_TUNNEL_UUID>
    credentials-file: C:\Users\<tu_usuario>\.cloudflared\<TU_TUNNEL_UUID>.json
    ingress:
      - hostname: smartreview.tu-dominio.com
        service: http://localhost:8501
      - service: http_status:404
    ```

  * **Paso 5: Crear la Ruta DNS**
    `cloudflared tunnel route dns <nombre-para-tu-tunel> smartreview.tu-dominio.com`

  * **Paso 6: Ejecutar el Túnel**
    Usa el comando de la **Terminal 5** detallado en la sección anterior para iniciar el túnel.

## 👨‍💻 Uso de la API (para Desarrolladores)

Todas las peticiones a los microservicios deben pasar a través del **API Gateway**. Para identificarse, el cliente (el frontend) debe incluir un header HTTP:

  * **Header**: `X-User-ID`
  * **Valor**: Un nombre de usuario único (la aplicación lo genera automáticamente para cada sesión web).

El gateway usará este ID para crear una base de datos de usuarios y organizar los archivos en carpetas separadas para cada uno.

## 🔮 Trabajo Futuro

  * **Extracción de Entidades Nombradas (NER)**: Identificar y visualizar automáticamente personas, organizaciones y términos técnicos clave en los documentos.
  * **Línea de Tiempo de Conceptos**: Graficar la frecuencia de mención de conceptos a través del tiempo, basado en las fechas de publicación de los artículos.
  * **Integración de Bases de Datos Vectoriales**: Migrar el almacenamiento de vectores de archivos `.npz` a una solución más robusta como `ChromaDB` o `Qdrant` para mejorar el rendimiento con miles de documentos.
  * **Soporte para más tipos de documentos** (e.g., `.docx`, `.html`).

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo `LICENSE` para más detalles.