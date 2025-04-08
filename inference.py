import asyncio
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import httpx  # Para llamadas asíncronas
from fastapi.staticfiles import StaticFiles
import logging # <--- AÑADIDO: Importar logging

# Configuración básica de logging (opcional pero útil)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()

# Cola asíncrona para encolar peticiones al LLM
task_queue = asyncio.Queue()

# --- ¡¡¡IMPORTANTE!!! ---
# Asegúrate de que esta URL apunta al ENDPOINT /lmstudio de tu balanceador Rust
# Si FastAPI y el balanceador están en la misma máquina:
LM_STUDIO_URL = "http://localhost:8080/lmstudio"
# Si están en máquinas diferentes (reemplaza <IP_DEL_BALANCEADOR_RUST>):
# LM_STUDIO_URL = "http://<IP_DEL_BALANCEADOR_RUST>:8080/lmstudio"
# ---

# Mensaje base del sistema (prompt institucional para FimeBot)
BASE_SYSTEM_PROMPT = ""

# Modelo de datos para solicitudes de chat/completions (no se usa directamente en el endpoint modificado)
# class ChatRequest(BaseModel):
#     model: str
#     messages: list
#     response_format: dict | None = None
#     temperature: float = 0.7
#     max_tokens: int = 100
#     stream: bool = False


@app.on_event("startup")
async def startup_event():
    """
    Evento que se lanza cuando el servidor inicia.
    Carga el prompt base desde archivo y arranca el worker.
    """
    app.state.worker_task = asyncio.create_task(worker())

    # Leer prompt base desde archivo
    prompt_path = "C:/Users/pedro/OneDrive/Documentos/GitHub/fimebot/data/info_fime.txt"
    global BASE_SYSTEM_PROMPT
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            BASE_SYSTEM_PROMPT = f.read().strip()
        logging.info("✅ Prompt base cargado correctamente.")
    except Exception as e:
        logging.warning(f"⚠️ No se pudo cargar el prompt base: {e}")
        BASE_SYSTEM_PROMPT = ""


async def worker():
    """
    Procesa secuencialmente las peticiones que llegan a la cola 'task_queue'.
    """
    while True:
        payload, future = await task_queue.get()

        # <--- AÑADIDO: Log antes de enviar la petición ---
        logging.info(f"FastAPI Worker ENVIANDO payload: {payload} a URL: {LM_STUDIO_URL}")
        # ---

        try:
            # Usar timeout más razonable que None si es posible, aunque para LLMs puede ser largo
            async with httpx.AsyncClient(timeout=300.0) as client: # Timeout de 5 minutos
                response = await client.post(LM_STUDIO_URL, json=payload) # No necesitas timeout=None aquí si lo pones en el cliente

            if response.status_code == 200:
                response_data = response.json()
                future.set_result(response_data)
            else:
                error_detail = f"Error desde el balanceador/nodo - Status: {response.status_code}, Detail: {response.text}"
                logging.error(error_detail)
                future.set_exception(HTTPException(status_code=response.status_code, detail=error_detail))

        except httpx.ReadTimeout:
             error_detail = "Timeout esperando respuesta del balanceador/nodo."
             logging.error(error_detail)
             future.set_exception(HTTPException(status_code=504, detail=error_detail))
        except Exception as e:
            error_detail = f"Excepción general en worker: {str(e)}"
            logging.error(error_detail, exc_info=True) # Loguea el traceback
            future.set_exception(HTTPException(status_code=500, detail=error_detail))

        finally:
            task_queue.task_done()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, payload: dict): # Recibe un dict genérico
    """
    Endpoint para solicitudes de completado de chat.
    Inyecta el prompt base si la petición viene desde FimeBot.
    """
    # <--- AÑADIDO: Log al recibir la petición ---
    logging.info(f"FastAPI /v1/chat/completions RECIBIDO payload: {payload}")
    # ---

    # Validación básica del payload recibido
    if not isinstance(payload, dict) or "messages" not in payload:
         logging.error("Payload recibido inválido o falta el campo 'messages'")
         raise HTTPException(status_code=400, detail="Payload inválido, se requiere un JSON con el campo 'messages'.")

    loop = asyncio.get_event_loop()
    future = loop.create_future()

    injected_payload = payload.copy() # Trabaja sobre una copia
    # Asegúrate de que messages sea una lista mutable
    injected_messages = list(injected_payload.get("messages", []))

    # Detectar si es FimeBot por header
    is_fimebot = request.headers.get("X-FimeBot", "").lower() == "true"

    if is_fimebot and BASE_SYSTEM_PROMPT:
        # Comprueba si ya existe un mensaje de sistema
        has_system_message = any(m.get("role") == "system" for m in injected_messages)
        if not has_system_message:
            logging.info("Inyectando prompt base de FimeBot.")
            injected_messages.insert(0, {"role": "system", "content": BASE_SYSTEM_PROMPT})
            injected_payload["messages"] = injected_messages # Actualiza el payload con los mensajes modificados
        else:
            logging.info("El payload ya contenía un mensaje de sistema, no se inyectó el prompt base.")


    await task_queue.put((injected_payload, future))
    try:
        result = await future
        return JSONResponse(content=result)
    except HTTPException as http_exc:
         # Re-lanza las excepciones HTTP que vienen del worker
         raise http_exc
    except Exception as e:
         # Captura otras posibles excepciones de la future
         logging.error(f"Excepción inesperada esperando resultado de la future: {e}", exc_info=True)
         raise HTTPException(status_code=500, detail=f"Error interno procesando la solicitud: {e}")


@app.post("/v1/upload")
async def upload_document(
    file: UploadFile = File(...),
    username: str = Form(...)
):
    """
    Endpoint para subir documentos.
    Guarda el archivo en D:\clasdocusers\<username>\nombre_del_archivo
    """
    try:
        content = await file.read()
        base_path = r"D:\clasdocusers"
        user_folder = os.path.join(base_path, username)
        os.makedirs(user_folder, exist_ok=True)
        file_path = os.path.join(user_folder, file.filename)
        with open(file_path, "wb") as f:
            f.write(content)
        logging.info(f"Archivo '{file.filename}' subido por '{username}' guardado en '{file_path}'")
        return {
            "filename": file.filename,
            "size": len(content),
            "saved_path": file_path
        }
    except Exception as e:
        logging.error(f"Error subiendo archivo '{file.filename}' para '{username}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Servir el frontend desde la carpeta del bot
try:
    frontend_dir = "C:/Users/pedro/OneDrive/Documentos/GitHub/fimebot/fimebot"
    if os.path.isdir(frontend_dir):
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="index")
        logging.info(f"Sirviendo archivos estáticos desde: {frontend_dir}")
    else:
        logging.error(f"El directorio para archivos estáticos no existe: {frontend_dir}")
except Exception as e:
    logging.error(f"No se pudo montar StaticFiles: {e}")


if __name__ == "__main__":
    # Ejecutar el servidor en 0.0.0.0:1234
    logging.info("Iniciando servidor FastAPI en 0.0.0.0:1234")
    uvicorn.run("inference:app", host="0.0.0.0", port=1234, reload=True)