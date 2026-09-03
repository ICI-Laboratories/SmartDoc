# SmartReview: Asistente Inteligente de Documentos (Local-First)

SmartReview es una plataforma de software diseñada para la revisión y consulta inteligente de literatura científica. Opera exclusivamente en el entorno local del usuario para garantizar la máxima confidencialidad y resuelve la sobrecarga informativa asociada con la investigación moderna.

A través de una arquitectura de microservicios robusta y flujos de IA avanzados, SmartReview transforma una carpeta de PDFs desorganizados en una base de conocimiento interactiva y analizable.

## Características Principales

  * **Procesamiento 100% Local**: Todos los componentes, desde la conversión de PDF hasta las consultas del LLM y los modelos de embedding, se ejecutan en tu propia máquina. Tus documentos nunca salen de tu control.
  * **Conversión Híbrida de PDF**: Detecta automáticamente si un PDF contiene texto nativo para una extracción rápida y precisa, o si es un documento escaneado que requiere OCR de alta calidad.
  * **Clasificación Automática con IA**: Organiza inteligentemente los documentos en categorías y subcategorías temáticas para una fácil navegación.
  * **Chat Interactivo (RAG Adaptativo)**: Conversa con uno o varios documentos a la vez. El sistema utiliza un avanzado flujo de Generación Aumentada por Recuperación (RAG) que se adapta a la cantidad de información para evitar sobrecargar el contexto del LLM.
  * **Búsqueda Híbrida Avanzada**: Combina la potencia de la búsqueda semántica (por significado) con la precisión de la búsqueda por palabras clave (BM25) para encontrar los fragmentos de texto más relevantes con una precisión excepcional.
  * **Análisis Cuantitativo**: Genera un mapa de calor de similitud conceptual entre documentos, permitiendo descubrir clústeres de investigación y relaciones ocultas en tu biblioteca.
  * **Arquitectura de Microservicios Robusta**: Construido con una arquitectura escalable que incluye un API Gateway de alto rendimiento en Rust para gestionar la comunicación entre servicios.
  * **Multiusuario con identidad central**: Cada cuenta de `auth_services` recibe un espacio aislado cuyo namespace es su UUID central canónico.

## Arquitectura del Sistema

SmartReview opera sobre una arquitectura de microservicios, donde cada componente tiene una responsabilidad clara, garantizando escalabilidad y mantenibilidad.

  * **Frontend (Streamlit)**: La interfaz de usuario web con la que interactúas. Es responsable de la carga de archivos, la visualización de documentos y las interfaces de chat y análisis.
  * **API Gateway/BFF (Rust - Axum)**: Ejecuta Authorization Code + PKCE contra el portal central, conserva tokens sólo del lado servidor, valida el audience `smartdoc`, protege mutaciones con CSRF y enruta el tráfico autenticado.
  * **Document Processor (Python - FastAPI)**: El servicio de trabajo pesado. Se encarga de recibir los PDFs, convertirlos a Markdown, generar los resúmenes por página y crear los embeddings vectoriales para la búsqueda.
  * **LLM Service (Python - FastAPI)**: El cerebro de la operación. Contiene la lógica para interactuar con el modelo de lenguaje (para chat y clasificación) y para realizar los análisis cuantitativos (búsqueda híbrida, cálculo de similitud).

## Flujos de Inteligencia Artificial

SmartReview utiliza dos flujos de IA distintos pero complementarios para potenciar sus funcionalidades.

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

## Pila Tecnológica

  * **Frontend**: Streamlit
  * **API Gateway**: Rust (Axum, Tokio)
  * **Servicios de Backend**: Python (FastAPI)
  * **Modelos de Embeddings**: `sentence-transformers` (ej. `BAAI/bge-large-en-v1.5`)
  * **Búsqueda por Palabras Clave**: `rank-bm25`

## Requisitos Previos

1.  **Python** (versión 3.9 o superior)
2.  **Rust**: Instálalo a través de [rustup](https://rustup.rs/).
3.  **Servidor de LLM local**: La aplicación está diseñada para conectarse a un servidor compatible con la API de OpenAI, como **LM Studio** u **Ollama**, que esté ejecutando un modelo de lenguaje.

## Instalación y Ejecución

#### 1\. Clonar el Repositorio

```bash
git clone <URL_DE_TU_REPOSITORIO>
cd smartreview
```

#### 2\. Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto a partir de `.env.example`. La
configuración local predeterminada usa el portal central en `127.0.0.1:3000`, la
API de identidad central en `127.0.0.1:8001` y este callback registrado:

```bash
SMARTREVIEW_BASE=D:\clasdocs
SMARTREVIEW_LM_URL=http://localhost:1234/v1/chat/completions
SMARTREVIEW_MODEL=google/gemma-3-4b
SMARTREVIEW_LM_TIMEOUT=60
SMARTDOC_AUTH_CALLBACK_URL=http://127.0.0.1:8043/auth/callback
```

#### 3\. Instalar Dependencias

Para cada servicio, navega a su carpeta e instala sus dependencias. Se recomienda usar un único entorno virtual para todo el proyecto.

```bash
pip install -r requirements.txt
```

#### 4\. Ejecutar la Aplicación

Abre las terminales necesarias y ejecuta un servicio en cada una.

  * **Terminal 1: API Gateway (Rust)**

    ```bash
    cd api_gateway
    cargo run
    ```

  * **Terminal 2: LLM Service (Python)**

    ```bash
    uvicorn llm_service.api:app --host 127.0.0.1 --port 8044
    ```

  * **Terminal 3: Document Processor (Python)**

    ```bash
    uvicorn document_processor.app:app --host 127.0.0.1 --port 8045
    ```

  * **Terminal 4: Frontend (Streamlit)**

    ```bash
    cd frontend
    streamlit run app.py
    ```

  * **Terminal 5: Cloudflare Tunnel (Opcional, para demo remota)**

    ```bash
    .\cloudflared.exe tunnel run <nombre-del-tunel>
    ```

Una vez que todo esté en ejecución, abre `http://127.0.0.1:8501`. No mezcles
`localhost` y `127.0.0.1` en el mismo flujo: las cookies y la comprobación de
`Origin` usan el host configurado de forma exacta.

## Configuración para Demo Remota con Cloudflare (Opcional)

Para exponer la aplicación en internet se necesitan dos orígenes HTTPS hermanos:
uno para Streamlit y otro para el gateway/BFF. Un túnel directo sólo a Streamlit
rompe el callback y no es una configuración autenticada válida.

  * **Paso 1: Instalar `cloudflared`**
    Descarga el ejecutable desde la [documentación oficial de Cloudflare](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/) (para Windows, es un único archivo `.exe`).

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
      - hostname: smartreview-api.tu-dominio.com
        service: http://localhost:8043
      - service: http_status:404
    ```

  * **Paso 5: Crear las Rutas DNS**

    ```bash
    cloudflared tunnel route dns <nombre-para-tu-tunel> smartreview.tu-dominio.com
    cloudflared tunnel route dns <nombre-para-tu-tunel> smartreview-api.tu-dominio.com
    ```

  * **Paso 6: Ejecutar el Túnel**
    Usa el comando de la **Terminal 5** detallado en la sección anterior para iniciar el túnel.

  * **Paso 7: Configurar el BFF y registrar el callback**

    ```dotenv
    SMARTREVIEW_API_GATEWAY_URL=https://smartreview-api.tu-dominio.com
    SMARTDOC_PUBLIC_GATEWAY_URL=https://smartreview-api.tu-dominio.com
    SMARTDOC_FRONTEND_URL=https://smartreview.tu-dominio.com/
    SMARTDOC_AUTH_CALLBACK_URL=https://smartreview-api.tu-dominio.com/auth/callback
    SMARTDOC_COOKIE_DOMAIN=.tu-dominio.com
    SMARTDOC_COOKIE_SECURE=true
    ```

    Registra exactamente ese callback para `client_id=smartdoc-web` y audience
    `smartdoc` en `auth_services`. Usa un dominio de cookie más estrecho si la
    organización aloja aplicaciones no confiables bajo el mismo dominio padre.

## Uso de la API (para Desarrolladores)

Todas las peticiones humanas a los microservicios deben pasar por el **API Gateway**. Abra `/auth/login`; el gateway redirige al portal de `auth_services` y completa el flujo PKCE. El navegador recibe únicamente una cookie opaca `HttpOnly`.

`X-User-ID` dejó de ser una credencial y se rechaza/descarta. El gateway obtiene el UUID desde `GET /auth/introspect` con el audience fijo `smartdoc`, e inyecta la identidad validada sólo hacia la red interna. Las mutaciones requieren además el control CSRF y `Origin` configurado.

Consulte [`docs/AUTH_CUTOVER.md`](docs/AUTH_CUTOVER.md) para configuración y [`docs/LEGACY_DATA_RECONCILIATION.md`](docs/LEGACY_DATA_RECONCILIATION.md) antes de tocar espacios anónimos anteriores.

## Trabajo Futuro

  * **Extracción de Entidades Nombradas (NER)**: Identificar y visualizar automáticamente personas, organizaciones y términos técnicos clave en los documentos.
  * **Línea de Tiempo de Conceptos**: Graficar la frecuencia de mención de conceptos a través del tiempo, basado en las fechas de publicación de los artículos.
  * **Integración de Bases de Datos Vectoriales**: Migrar el almacenamiento de vectores de archivos `.npz` a una solución más robusta como `ChromaDB` o `Qdrant` para mejorar el rendimiento con miles de documentos.
  * **Soporte para más tipos de documentos** (e.g., `.docx`, `.html`).

## Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo `LICENSE` para más detalles.
