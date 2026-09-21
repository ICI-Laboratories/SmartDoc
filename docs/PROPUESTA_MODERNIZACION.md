# Revisión y propuesta de modernización

Fecha: 21 de septiembre de 2026. Estado: propuesta para SARA DocReader, nombre sugerido por el usuario; despliegue en servidor propio para uso compartido de laboratorio, con al menos 10 usuarios.

La implementación posterior y su alcance están descritos en [MIGRACION_SARA.md](MIGRACION_SARA.md). Este documento conserva el diagnóstico inicial.

## Objetivo

Convertir el asistente de literatura científica en un gestor documental web rápido: subir, encontrar y abrir documentos debe funcionar sin esperar a la IA. Conservar consulta con fuentes y análisis como capacidades adicionales.

La revisión es estática, con ejecución de las tres pruebas Python de identidad (correctas). No se levantó el sistema completo, no se ejecutaron las pruebas Rust y no se midieron latencias ni consumo de memoria. Los cuellos de botella indicados se deducen del código; su magnitud debe medirse.

## Hallazgos del repositorio

| Prioridad | Evidencia | Consecuencia y cambio propuesto |
|---|---|---|
| Alta | `document_processor/app.py:163`: la petición espera extracción, clasificación, resúmenes y embeddings | Registrar el archivo y devolver un identificador; ejecutar el resto mediante trabajos persistentes. La conversión y `encode` síncronos también ocupan el hilo del endpoint asíncrono. |
| Alta | `llm_service/logic/analysis.py:62`: lee NPZ, tokeniza y construye BM25 por documento en cada consulta | Persistir índices; buscar sobre el corpus autorizado con paginación y límites. |
| Alta | `document_processor/core_pdf.py`: decide OCR por una suma global de 1000 caracteres | Un PDF mixto con suficiente texto evita OCR y omite el contenido de páginas escaneadas. Evaluar extracción por página y preservar página de origen. |
| Alta | `document_processor/app.py:146`: devuelve “Resumen de ejemplo” ante fallos | Persistir estado de error y ofrecer reintento; nunca presentar contenido de ejemplo como análisis. |
| Media | `frontend/lib/common.py:207` y páginas Browse/Chat/Análisis: descubrimiento recursivo y acceso directo a archivos | Crear API documental por identificadores. Desacoplar el navegador y la interfaz del volumen de datos. |
| Media | `llm_service/logic/docs.py:110` y `core.py`: selección de páginas por LLM antes de responder; `stream=False` | Recuperar fragmentos indexados primero; transmitir la respuesta progresivamente y enlazar citas verificables. |
| Media | `document_processor/app.py`: duplicados por nombre/categoría después de conversión y clasificación | Detectar por hash dentro del espacio autorizado, antes del trabajo costoso; admitir archivos distintos con el mismo nombre. |
| Media | README SmartReview, variables SMARTREVIEW y autenticación SmartDoc | Separar marca visible de identificadores técnicos para cambiar nombre sin romper acceso ni ubicación de archivos. |

Ya existen piezas aprovechables: extracción directa de PDF, búsqueda híbrida, gateway Axum con PKCE/CSRF y aislamiento por UUID central. La migración debe mantener sus contratos y pruebas.

## Tecnología recomendada

Destino confirmado: servidor propio `cite-server`, para un laboratorio con al menos 10 usuarios. El total de usuarios no establece cuántos estarán activos simultáneamente; ajustar concurrencia al medir uso, documentos, tamaños y hardware.

La inspección SSH fue bloqueada antes de abrir sesión por `websocket: bad handshake`. El alias usa un ProxyCommand de Cloudflare Access. No se pudo verificar CPU, RAM, GPU, disco ni servicios; no se modificó el servidor. El mensaje no permite distinguir entre problemas de autorización, túnel o configuración del proxy.

| Capa | Elección | Motivo |
|---|---|---|
| Interfaz | SvelteKit + TypeScript | Sustituye Streamlit; control del estado, carga por rutas y navegación sin recargar toda la aplicación. |
| API y sesión | Rust + Axum existente | Ampliar con biblioteca, archivos y trabajos; reutilizar la integración de identidad. Servir interfaz y API bajo un mismo origen. |
| Catálogo y búsqueda | PostgreSQL + pgvector | Metadatos, permisos, texto y vectores persistentes; evita reconstruir índices desde archivos. |
| Procesamiento | Worker Python separado | Reutilizar OCR, Docling y embeddings con concurrencia limitada y trabajos recuperables. |
| Archivos | Disco persistente al inicio, abstracción compatible con objetos posteriormente | Originales fuera de la base; identificadores estables y metadatos en PostgreSQL. |
| Visor | PDF.js cargado al abrir un documento | Navegación por páginas y citas; evitar cargar el visor en la biblioteca. |

SvelteKit no garantiza por sí mismo una aplicación más rápida. React con Vite es una alternativa razonable si el equipo ya lo domina; no hay mediciones de este proyecto que demuestren superioridad entre ambos. Reescribir Rust en otro lenguaje añade riesgo sin evidencia de beneficio. Python sigue siendo útil para OCR e IA, pero fuera de las peticiones interactivas.

## Técnicas que conviene incorporar

1. **Carga y enriquecimiento separados.** Al terminar de guardar el original, mostrarlo en la biblioteca. Estados independientes para extracción, indexación y resumen; descargar/abrir incluso si falla la IA.
2. **Trabajos persistentes.** Cola con estados, intentos, lease, heartbeat, recuperación tras reinicio y operaciones idempotentes. Empezar con PostgreSQL si el volumen lo permite; seleccionar una implementación mantenida antes de programar el worker. Una tarea en memoria no cumple estos requisitos.
3. **Extracción gradual.** Texto nativo primero; OCR en páginas que lo necesitan. Tablas y análisis costosos según el documento. Guardar versión del extractor y del modelo para reprocesar únicamente lo necesario.
4. **Búsqueda indexada.** Texto completo con GIN y vectores con pgvector; evaluar fusión de rankings RRF. El buscador de texto nativo de PostgreSQL no equivale a BM25: comparar calidad antes de retirar el ranking actual. HNSW se justifica con mediciones de volumen, memoria y recall; no es obligatorio al inicio.
5. **Fragmentos con procedencia.** Dividir por estructura y presupuesto de tokens, conservando documento, versión, páginas y localización; evaluar con preguntas reales en español e inglés. Incorporar reranking sólo si mejora calidad con latencia aceptable.
6. **IA opcional en el flujo diario.** Resúmenes bajo demanda o con prioridad baja. Chat con contexto recuperado, citas y streaming; buscar por nombre/texto aunque el modelo local esté apagado.
7. **Biblioteca eficiente.** Paginación por cursor, consultas cancelables, búsqueda con debounce y miniaturas diferidas. Cargar únicamente las páginas visibles del PDF y soportar solicitudes parciales del archivo.
8. **Permisos en todas las rutas.** Filtrar antes de recuperar resultados; autorizar descarga, miniaturas, trabajos y chat. Mantener tokens en servidor. El diseño de equipos requiere membresías explícitas: el aislamiento actual por usuario no constituye colaboración compartida.

## Experiencia propuesta

Pantalla principal de biblioteca con buscador visible, botón Subir y filtros sencillos. Al soltar archivos aparecen filas con progreso y acciones de reintento. Al seleccionar un documento se abre el visor con un panel lateral para metadatos, resumen y preguntas. Reservar análisis avanzados para una vista secundaria.

No exponer rutas del servidor, parámetros de modelos ni configuración de embeddings en el flujo normal. Definir todavía si se necesitan carpetas compartidas, etiquetas, versiones, papelera o formatos adicionales a PDF.

## Nombre

**SARA DocReader** es el nombre propuesto por el usuario y la primera opción para la siguiente etapa. Acompañarlo de “Biblioteca documental del laboratorio” aclara que también permite organizar y buscar. No se ha validado disponibilidad comercial, dominio o marca.

Alternativas consideradas antes de recibir esa preferencia:

- **Folio**: breve y asociado con documentos.
- **Lumbre**: destaca consulta y descubrimiento del contenido.
- **Trama**: destaca relaciones entre documentos.

Cambiar primero marca y textos visibles. Mantener temporalmente audience `smartdoc`, client ID `smartdoc-web`, variables antiguas y rutas de datos; un cambio técnico requiere compatibilidad explícita y coordinación con identidad. No renombrar automáticamente espacios heredados: respetar `LEGACY_DATA_RECONCILIATION.md`.

## Secuencia de migración

1. Medir el flujo actual con PDFs nativos, escaneados y mixtos; registrar tiempos, memoria, errores y calidad de recuperación. Precisar escala y despliegue.
2. Crear catálogo y API de biblioteca/archivo con pruebas de autorización. Importación verificable por manifiesto, hash y conteos, conservando originales y reversibilidad.
3. Incorporar carga asíncrona y worker durable. Probar reinicios, reintentos, duplicados y modelo desconectado.
4. Crear la interfaz SvelteKit de biblioteca, carga y visor contra API real; conservar acceso al sistema anterior durante la transición.
5. Incorporar búsqueda persistente y chat con citas; comparar calidad antes del cambio definitivo.
6. Aplicar nombre elegido, completar pruebas funcionales y retirar Streamlit cuando exista equivalencia validada.

## Metas verificables propuestas

Metas iniciales, no resultados obtenidos: con 10.000 documentos y 10 usuarios concurrentes en hardware acordado, listado y búsqueda textual p95 por debajo de 300 ms de servidor; búsqueda híbrida caliente p95 por debajo de 1 s. Medir por separado transferencia, primer render, OCR y generación del modelo. Confirmación de carga por debajo de 500 ms desde que termina la transferencia y persistencia del original, sin esperar OCR/IA.

Validar recuperación tras caída del worker, ausencia de acceso cruzado, búsqueda sin LLM, preservación de páginas y citas. Evaluar con usuarios tareas de subir, localizar y abrir un documento sin instrucciones.

## Fuentes primarias consultadas

- [SvelteKit: rendimiento](https://svelte.dev/docs/kit/performance): división de código, precarga, carga selectiva y medición en compilación de producción.
- [PostgreSQL: índices de texto](https://www.postgresql.org/docs/17/textsearch-indexes.html): índices GIN para búsqueda textual.
- [pgvector](https://github.com/pgvector/pgvector): búsqueda exacta/aproximada y combinación con búsqueda textual, RRF o reranking.
- [FastAPI: tareas en segundo plano](https://fastapi.tiangolo.com/tutorial/background-tasks/): separación de tareas pesadas mediante herramientas de workers.
- [Docling: opciones de procesamiento](https://docling-project.github.io/docling/reference/pipeline_options/): costes y configuración de OCR, tablas y otras capacidades; verificar compatibilidad con la versión seleccionada.
- [PDF.js](https://mozilla.github.io/pdf.js/): visor y procesamiento de PDF en web.

Las selecciones de arquitectura son recomendaciones derivadas de estas capacidades y del código actual, no benchmarks comparativos.
