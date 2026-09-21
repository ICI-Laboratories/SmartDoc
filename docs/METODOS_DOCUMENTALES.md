# Métodos de SARA DocReader

Investigación y decisiones: 21 de septiembre de 2026.

## Flujo interactivo y procesamiento

El original se guarda antes de responder `202`. El registro y el trabajo se insertan en una transacción. El gateway transmite las nuevas cargas al catálogo sin acumular todo el PDF en RAM. La extracción se ejecuta en un proceso separado con límite de tiempo; cada worker reclama un trabajo con `FOR UPDATE SKIP LOCKED`, renueva un lease y sólo publica resultados si conserva su token. Los trabajos sobreviven a reinicios y permiten tres intentos automáticos y reintento manual.

PostgreSQL documenta `SKIP LOCKED` como útil para consumidores de una tabla tipo cola. Su vista inconsistente no debe trasladarse a consultas normales del catálogo. Se eligió una cola pequeña integrada para el servidor del laboratorio; si las necesidades incluyen prioridades complejas o cadenas distribuidas, evaluar una cola especializada antes de ampliar este mecanismo.

Fuente: [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html).

## OCR por página

Se extrae texto nativo ordenado; páginas con imágenes o sin texto pasan por Tesseract a través de PyMuPDF. El OCR parcial conserva texto nativo cuando lo hay. Se guardan números de página en todos los fragmentos, incluidos los que llegan de páginas mixtas. Tesseract español e inglés se instala en la imagen del worker.

Es una primera heurística conservadora: una ilustración puede activar OCR aunque no lo necesite. Tampoco reconstruye perfectamente tablas o fórmulas. Docling sigue siendo candidato para una segunda ruta de alta fidelidad, seleccionada por necesidad del documento; no se incluye en el camino rápido ni se afirma equivalencia de calidad sin evaluación.

Fuentes: [PyMuPDF OCR](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html), [opciones Docling](https://docling-project.github.io/docling/reference/pipeline_options/).

## Fragmentación y recuperación

La primera implementación divide texto dentro de cada página, con un máximo de 1800 caracteres y solapamiento de 180. Conserva referencia de página y evita fragmentos sin procedencia. Es un punto de partida medible, no fragmentación semántica ni conteo exacto de tokens. Antes de producción intensiva, comparar contra segmentación por encabezados y presupuesto del tokenizer.

El índice `tsvector` usa configuración `simple`, útil para conservar términos técnicos de ambos idiomas; no aplica stemming español/inglés ni BM25. Un índice GIN persiste la estructura invertida. La búsqueda por nombre usa trigramas para consultas parciales y paginación por fecha/UUID.

La ruta opcional de significado consulta Ollama `/api/embed`, valida 1024 dimensiones y valores finitos, y compara sólo fragmentos indexados con el mismo identificador de modelo. `bge-m3` es el candidato de partida porque ya se utilizaba en la ingesta anterior. Evitar etiquetas mutables del modelo en producción y reprocesar al cambiar sus pesos. No reutilizar NPZ anteriores: la aplicación anterior generaba con `bge-m3` y consultaba con `bge-large-en-v1.5`.

Los 60 candidatos de cada modalidad se fusionan mediante `1/(60+posición)` (RRF); se devuelven hasta 20 fragmentos. Se inicia con búsqueda vectorial exacta, filtrada por propietario. HNSW puede reducir latencia en catálogos grandes, pero se debe evaluar recall y selectividad de permisos antes de introducirlo. Si el servicio de embeddings falla, se conserva la búsqueda textual y la interfaz informa la degradación.

Fuentes: [índices GIN](https://www.postgresql.org/docs/17/textsearch-indexes.html), [pgvector y búsqueda híbrida](https://github.com/pgvector/pgvector), [Ollama embed](https://docs.ollama.com/api/embed).

## Consultas y lectura

El chat recupera hasta ocho fragmentos de los documentos seleccionados (máximo 20), envía el contexto al modelo compatible con OpenAI configurado y transmite texto por SSE. Muestra las fuentes recuperadas con enlaces a página; las citas generadas por el modelo aún requieren evaluación de fidelidad. No se ejecutan herramientas ni instrucciones de los documentos. No hay resúmenes ficticios ni selección previa de todos los documentos mediante una llamada al modelo.

PDF.js se carga bajo demanda y dibuja sólo una página a la vez. El endpoint de archivo soporta Range y autorización por UUID de usuario. SvelteKit mantiene los recursos del visor separados de la carga inicial. Las fuentes Inter/Manrope están alojadas en el mismo despliegue, sin solicitudes a Google Fonts.

Fuentes: [PDF.js](https://mozilla.github.io/pdf.js/examples/), [rendimiento SvelteKit](https://svelte.dev/docs/kit/performance), [SvelteKit adapter-node](https://svelte.dev/docs/kit/adapter-node).

La síntesis y clasificación son trabajos bajo demanda: reutilizan texto/vectores ya indexados y declaran cuántos fragmentos entraron en el contexto (presupuesto de 18.000 caracteres). Se persisten resultado y categoría sugerida; un fallo deja un error explícito. La matriz de similitud compara vectores promedio del mismo modelo, filtrados por propietario. No mide certeza ni demuestra relación científica.

## Evaluación pendiente con documentos del laboratorio

Construir un conjunto revisado de preguntas y páginas relevantes: español/inglés, palabras exactas, términos técnicos, tablas, PDF escaneado y mixto. Medir recall@10, MRR, fidelidad de citas, tasa de OCR fallido, tiempo a primera página, tiempo a primer token, p50/p95 de búsqueda y memoria con 10 usuarios concurrentes.

Comparar: texto GIN; texto + vectores/RRF; RRF + reranker; extracción directa/OCR contra Docling. Mantener la opción más sencilla que cumpla calidad y latencia. No se ha demostrado aún mejora porcentual frente al sistema anterior ni rendimiento a 10.000 documentos.
