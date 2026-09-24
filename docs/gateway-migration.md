# Inferencia de SmartDoc mediante el gateway

La biblioteca, el chat, la síntesis y los auxiliares usan un único destino OpenAI-compatible,
configurado en los servicios de backend. No se deben entregar la URL interna ni la clave al navegador.

| Variable | Función |
| --- | --- |
| `LLM_GATEWAY_BASE_URL` | Base común. En Docker: `http://llm-gateway:8000/v1`; fuera de Docker: `http://127.0.0.1:8009/v1`. Se acepta también la base sin `/v1`. |
| `LLM_GATEWAY_API_KEY` | Clave de SmartDoc enviada mediante `Authorization: Bearer`. Obligatoria para cualquier llamada de inferencia. |
| `SARA_LLM_MODEL` | Alias del chat y la síntesis. Vacío mantiene estas funciones deshabilitadas. |
| `SARA_EMBEDDING_MODEL` | Alias para ingesta y consulta semántica. Vacío deshabilita vectores. |
| `SARA_EMBEDDING_REVISION` | Identidad de la configuración de vectores; inicial `llama.cpp-v1`. |
| `SARA_EMBEDDING_QUERY_INSTRUCTION` | Instrucción opcional para consultas de búsqueda; vacía conserva las entradas sin prefijo. Nunca se añade a los documentos. |
| `SARA_OCR_MODEL` | Alias OCR independiente. Vacío mantiene la extracción y OCR actuales en CPU. |
| `SARA_OCR_PROFILE` | `glm-ocr` por defecto, o `lightonocr-2` para solicitudes con solamente una imagen. |
| `SARA_OCR_MAX_SIDE` | Lado máximo al renderizar cada página para OCR remoto: 1600 píxeles por defecto, permitido entre 256 y 2048. |

`SARA_OLLAMA_URL` y `SARA_LLM_URL` ya no se consultan. No existe fallback a Ollama ni a un
servidor de modelos directo. Antes de desplegar esta versión hay que trasladar la configuración
de chat a las variables comunes. La base admite únicamente la raíz o `/v1`; una URL antigua
terminada en `/api/embed` o `/v1/chat/completions` se rechaza, no se reutiliza como base.
La clave debe pertenecer a la aplicación y conservar los límites
del gateway. El Compose base conecta `library` y `worker` a la red externa `llm-apps`, donde
debe existir el alias DNS `llm-gateway`. No se inicia ni se modifica el gateway con este Compose.

## Fase de aplicaciones

En esta fase mantener `SARA_EMBEDDING_MODEL=` y `SARA_OCR_MODEL=`. El gateway actual aún no
ofrece los auxiliares requeridos. El procesamiento CPU y la búsqueda por texto siguen disponibles.
Chat y síntesis utilizan `POST /v1/chat/completions`, autenticado, con el alias configurado.

Cuando el gateway y el modelo auxiliar estén validados, los modelos se habilitan explícitamente:

- Embeddings: `POST /v1/embeddings`, cuerpo `model`, `input` y `encoding_format: "float"`.
  La respuesta debe contener `data` con un índice entero único por entrada y vectores no nulos
  de 1024 números finitos. Los resultados se reordenan por `index` antes de persistirlos.
- OCR: `POST /v1/chat/completions`, imagen PNG en `image_url`, `stream: false`, el alias OCR
  explícito y el prompt `Text Recognition:`. No hereda el modelo del chat. El gateway debe
  enrutar ese alias al modelo OCR, sin fallback al chat principal.

El OCR procesa secuencialmente las páginas que contienen imágenes o carecen de texto nativo;
conserva las páginas de texto nativo y su numeración. Cada imagen tiene lado máximo acotado,
no más de 8 MiB, timeout de 120 segundos y salida de hasta 4096 tokens. El límite global de
páginas y el plazo del trabajo siguen aplicándose. Una página fallida, vacía o truncada hace
fallar el trabajo: no se publica un índice aparentemente completo omitiendo esa página.
Estos límites acotan solicitudes; no garantizan por sí mismos una cuota de VRAM del servidor.

La ausencia o fallo de embeddings mantiene la ingesta sin vectores y la consulta por texto,
con el error de vectores visible en el documento. Los errores del modelo OCR se registran en
el trabajo con la página afectada, y se pueden volver a intentar. Una respuesta de síntesis
fallida conserva el documento y la búsqueda. No se activan ni descargan modelos en esta fase.

## Identidad y reindexación

Los vectores nuevos guardan `gateway:<modelo>@<revisión>` en `documents.embedding_model`.
Ingesta, búsquedas y comparación de documentos usan esta misma identidad. Por eso los vectores
antiguos identificados solamente por el nombre de Ollama quedan excluidos de la comparación
semántica, aunque también tengan 1024 dimensiones. La búsqueda léxica permanece disponible.

Un alias debe representar una configuración estable. Cambiar pesos, cuantización, pooling,
normalización o instrucciones de entrada requiere actualizar `SARA_EMBEDDING_REVISION` y
reprocesar los documentos mediante la acción de reintento. No basta renombrar las identidades
en la base de datos. No se modifica ni reindexa automáticamente ningún documento existente.

## Perfiles opcionales de modelos

Los perfiles no activan ni descargan modelos. Después de validar el alias de
Qwen3-Embedding-0.6B en el gateway, la configuración de ejemplo es:

```dotenv
SARA_EMBEDDING_MODEL=<alias-validado-en-el-gateway>
SARA_EMBEDDING_REVISION=<revision-de-pesos-cuantizacion-y-pooling>
SARA_EMBEDDING_QUERY_INSTRUCTION=Given a web search query, retrieve relevant passages that answer the query
```

SmartDoc envía las consultas como `Instruct: <instrucción>\nQuery: <consulta>` y
los fragmentos documentales sin prefijo. Aplica a la biblioteca y al buscador
anterior. La identidad de vectores añade `:query-sha256:<hash>` de la instrucción
y versión del formato; una instrucción diferente excluye los índices anteriores
hasta reprocesarlos. Ingesta y consulta deben recibir exactamente la misma
configuración. Con instrucción vacía, la identidad y entradas BGE-M3 anteriores
se conservan. Véase la [ficha oficial de Qwen](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B).

La biblioteca produce fragmentos de hasta 1800 caracteres con solapamiento de
180; el procesador anterior también divide sus párrafos largos con esos límites,
conservando las colas. Ambos envían lotes de hasta 16 entradas. Los caracteres no
equivalen a tokens: el perfil Qwen desplegado con contexto 2048 y límite de
secuencia/ubatch de 2048 tokens puede rechazar texto Unicode muy denso, aunque
quepa en el límite de 8192 caracteres del gateway. Se conserva el texto completo
y se propaga el error, sin truncarlo silenciosamente ni cambiar de modelo. La
biblioteca mantiene búsqueda léxica y registra el fallo de vectores; el proceso
anterior no escribe un índice NPZ parcial cuando falla un lote.

Para un alias validado de LightOnOCR-2:

```dotenv
SARA_OCR_MODEL=<alias-ocr-validado-en-el-gateway>
SARA_OCR_PROFILE=lightonocr-2
SARA_OCR_MAX_SIDE=1540
```

Este perfil envía únicamente `image_url`, sin prompt textual; renderiza hasta
200 dpi manteniendo proporciones y limita el lado largo al menor entre el
límite configurado y 1540 píxeles. Un límite menor se respeta. Los perfiles
desconocidos se rechazan antes de llamar al gateway. `glm-ocr` conserva el
prompt `Text Recognition:` y el renderizado anteriores. Véase la
[ficha oficial de LightOnOCR-2](https://huggingface.co/lightonai/LightOnOCR-2-1B).

## Servicios anteriores de SmartReview

El código de `document_processor` y `llm_service` también usa el gateway. Ya no carga
SentenceTransformer ni descarga modelos de embeddings al iniciar. El chat anterior conserva
`SMARTREVIEW_MODEL` (por defecto `sara-main`) y envía `response_format` de OpenAI para JSON.
`SMARTREVIEW_LM_URL` deja de seleccionar el motor. El servicio interno `llm_service` sigue
coordinando clasificación y resúmenes; no es un motor de inferencia.

Los procesos locales deben recibir las variables `LLM_GATEWAY_*` en su entorno. Los scripts
Windows comprueban que existen y ya no inician ni descargan modelos. En Docker se pasan a
los backends desde Compose. Cada aplicación conserva su propia credencial; no reutilizar
una clave administrativa global. No se entrega ninguna clave de inferencia al frontend.

El `.npz` anterior no almacenaba identidad de modelo; además, ingesta y consulta usaban
modelos distintos. Ahora ambas usan `SARA_EMBEDDING_MODEL` y guardan `embedding_identity`.
Los índices antiguos deben regenerarse, nunca simplemente etiquetarse. Mientras embeddings
esté desactivado, búsqueda semántica y similitud del servicio anterior no están disponibles.
La biblioteca actual mantiene búsqueda léxica. Este cambio no migra datos ni activa el
despliegue antiguo; para DocReader se utiliza `compose.sara.yml`.

El OCR anterior también admite `SARA_OCR_MODEL`, vacío por defecto. Con el modelo habilitado
procesa páginas individualmente usando el mismo adaptador de OCR y conserva numeración.
La conversión se ejecuta fuera del bucle asíncrono para no bloquear las demás peticiones.

## Comprobación local

```sh
.venv/bin/python -m unittest discover -s tests -p test_gateway_contract.py -v
```

Estas pruebas usan HTTP simulado y PDFs temporales; verifican autenticación, rutas, orden y
validación de vectores, identidad compartida, funciones deshabilitadas, fallback léxico,
streaming de chat y tratamiento íntegro de páginas OCR. No prueban el despliegue ni consumo
de GPU. Las pruebas de PostgreSQL en `tests/test_library.py` requieren una base desechable
terminada en `_test`, configurada con `SARA_TEST_DATABASE_URL`.

## Configuración activa del 24 de septiembre de 2026

La fase de aplicaciones terminó. El gateway de producción ya sirve `qwen-local`,
`ocr` (GLM-OCR Q8) y `qwen3-embedding` (0.6B F16). Library y worker usan
la misma clave de SmartDoc y configuración. El OCR GLM compara su salida con
Tesseract spa+eng, limitado a un hilo y 30 segundos por página. Conserva el
cuerpo reconocido y recupera márgenes de alta confianza en una sección identificada.
Una omisión interior relevante, repetición, truncamiento o falta de evidencia de
verificación impide indexar el documento y deja un error visible para revisión.
Esta comprobación de cobertura no certifica cifras, fórmulas ni exactitud absoluta.

En cite-server, desplegar únicamente library y worker con `.env.sara` y los tres
archivos `compose.sara.yml`, `compose.sara.server.yml` y
`compose.sara.gateway.json`. El último selecciona la imagen final validada.
No iniciar nuevamente el despliegue anterior de SmartReview.
