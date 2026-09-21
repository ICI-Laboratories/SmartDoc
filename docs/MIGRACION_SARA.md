# Migración a SARA DocReader

## Componentes nuevos

- `web/`: SvelteKit, TypeScript, PDF.js y activos originales de IdentidadGrafica/SARA v0.4.0.
- `library_service/`: catálogo FastAPI ligero, PostgreSQL, búsqueda, importación y worker independiente. No importa Streamlit, PyTorch ni modelos en el proceso HTTP.
- `api_gateway/`: Rust con selector de acceso temporal y cuentas centrales opcionales, CSRF, `/api/*` y transmisión de cargas.
- `compose.sara.yml`: despliegue nuevo, con base/archivos en volúmenes separados del sistema anterior.

La nueva API documental se implementa en Python detrás del gateway Rust para compartir contratos de base con el worker. El despliegue moderno ya no requiere autenticación central; conserva separación de bibliotecas por sesión anónima. Streamlit queda disponible en el compose anterior durante la transición; el nuevo compose no lo ejecuta.

## Arranque en servidor propio

```bash
cp .env.sara.example .env.sara
# Rellenar SARA_DB_PASSWORD con un secreto URL-safe; no se requiere identidad central.
docker compose --env-file .env.sara -f compose.sara.yml up --build -d
```

Acceso predeterminado: `http://127.0.0.1:8043`. La aplicación completa usa un mismo origen. Sólo el proxy publica puerto y lo enlaza a loopback: colocar el reverse proxy HTTPS o túnel del servidor frente a él. Catálogo, PostgreSQL y worker no deben publicarse directamente porque el catálogo confía en la identidad inyectada por el gateway.

Para acceso remoto, `SARA_PUBLIC_ORIGIN=https://<dominio>` y `SMARTDOC_COOKIE_SECURE=true`. Con cuentas desactivadas no se necesita portal. Para habilitarlas después, seguir la configuración de cuentas en el documento de acceso. Las sesiones anónimas se guardan en PostgreSQL y sobreviven al reinicio del gateway; borrar las cookies pierde acceso a esa biblioteca. Ver [acceso anónimo y métricas](ACCESO_ANONIMO_Y_METRICAS.md).

Se comienza con un worker. Después de medir memoria, CPU y cola, `docker compose --env-file .env.sara -f compose.sara.yml up -d --scale worker=2` permite dos consumidores. No implica capacidad demostrada para 10 usuarios concurrentes. Los límites actuales son 100 MB/PDF, 1500 páginas y 900 segundos por intento de worker.

La búsqueda por nombre y contenido funciona sin IA. Para significado, configurar `SARA_EMBEDDING_MODEL` (1024 dimensiones; candidato `bge-m3`) y `SARA_OLLAMA_URL`; descargar el modelo previamente en el servidor Ollama. Para consultas, configurar `SARA_LLM_URL` con la URL completa de chat completions y `SARA_LLM_MODEL`. Al habilitar/cambiar embeddings hay que reprocesar archivos existentes mediante `POST /api/documents/{id}/retry` (con sesión/CSRF); el botón de reintento permite recuperar errores, y la operación masiva debe planearse según el volumen.

## Datos existentes: copia verificable

No se han movido ni importado datos reales. Crear copia de seguridad y un CSV explícito:

```csv
source,subject,sha256
/ruta/SmartReview/UUID-central/categoria/documento.pdf,UUID-central,HASH-SHA256
```

Sustituir los marcadores por valores reales. El origen debe estar dentro del namespace UUID indicado bajo `--source-root`. El manifiesto heredado debe conservar propietarios revisados. No asignar documentos de cuentas antiguas a una cookie anónima sin una decisión explícita. Las carpetas anónimas no se asignan por nombre/email: seguir `LEGACY_DATA_RECONCILIATION.md`.

```bash
# Con el entorno Python del catálogo y SARA_DATABASE_URL / SARA_STORAGE configurados:
python -m library_service.import_legacy manifiesto.csv --source-root /ruta/SmartReview
python -m library_service.import_legacy manifiesto.csv --source-root /ruta/SmartReview --apply > resultado-importacion.json
```

El primer comando valida sin escribir. El segundo copia, verifica hash, registra metadatos y encola procesamiento; repetirlo no duplica archivos. No carga NPZ/pickle, no borra originales ni reutiliza embeddings incompatibles. En Docker hay que montar el origen y manifiesto en lectura, además del volumen `documents` del catálogo. Conservar el informe de importación fuera del contenedor.

La base y el sistema de archivos no forman una única transacción: un fallo después del rename y antes del commit puede dejar un archivo huérfano (sin exposición en catálogo). No eliminar archivos huérfanos sin comparar catálogo y almacenamiento con backup. Respaldar ambos volúmenes de forma coordinada.

Reversión: detener el compose nuevo y reactivar el anterior conservando sus datos. Los documentos nuevos de SARA no aparecen automáticamente en Streamlit; exportarlos antes de una reversión si es necesario. No ejecutar `down -v` para revertir.

## Pruebas

```bash
python -m venv .venv
.venv/bin/pip install -r library_service/requirements.lock
SARA_TEST_DATABASE_URL=postgresql://.../sara_test .venv/bin/python -m unittest discover -s tests -v
cd web
npm ci
npm run check
npm run build
npx playwright install chromium
SARA_E2E_DATABASE_URL=postgresql://.../sara_ui_test npx playwright test
```

La suite PostgreSQL exige base desechable con nombre terminado en `_test` y vacía sus tablas. Sin variable se omite, y siguen corriendo las pruebas antiguas de identidad. Las pruebas web arrancan automáticamente la vista previa de producción en `127.0.0.1:4173`, el catálogo en `127.0.0.1:8046` y un worker con almacenamiento temporal; simulan la frontera HTTP del gateway con sesiones anónimas reales de PostgreSQL y enrutan llamadas a la API real. Las pruebas Rust y el smoke test de compose comprueban por separado la frontera de cookies y CSRF. Ejecutar las pruebas Rust con `cargo test` en `api_gateway`.

## Alcance y pendientes

Migrados: biblioteca, carga, búsqueda por nombre/contenido y significado opcional, lectura de PDF y consulta individual/multidocumento con fuentes, síntesis y categoría sugerida bajo demanda, y matriz de similitud de hasta 20 documentos. La interfaz conserva aislamiento por navegador; colaborar en un laboratorio no concede acceso automático a archivos de compañeros.

La clasificación pasa a ser una sugerencia temática bajo demanda; no mueve archivos ni crea categorías físicas. La síntesis persistente declara cuántos fragmentos cubre y no equivale a resumir todas las páginas de un documento largo. La comparación usa el coseno de vectores promedio, no una probabilidad de relación.

Quedan permisos de equipos, formatos distintos de PDF, evaluación de calidad y rendimiento con corpus real y despliegue en `cite-server`. Las conexiones a modelos se prueban con respuestas controladas; la validación contra los modelos reales del servidor está pendiente.
