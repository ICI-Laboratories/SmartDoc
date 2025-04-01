import asyncio
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import httpx  # Para llamadas asíncronas a LM Studio
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Cola asíncrona para encolar peticiones al LLM
task_queue = asyncio.Queue()

# Ajusta esta variable según donde tengas corriendo LM Studio
LM_STUDIO_URL = "http://localhost:1235/v1/chat/completions"

# Mensaje base del sistema (prompt institucional para FimeBot)
BASE_SYSTEM_PROMPT = ""

# Modelo de datos para solicitudes de chat/completions
class ChatRequest(BaseModel):
    model: str
    messages: list
    response_format: dict | None = None
    temperature: float = 0.7
    max_tokens: int = 100
    stream: bool = False


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
        print("✅ Prompt base cargado correctamente.")
    except Exception as e:
        print("⚠️ No se pudo cargar el prompt base:", e)
        BASE_SYSTEM_PROMPT = ""


async def worker():
    """
    Procesa secuencialmente las peticiones que llegan a la cola 'task_queue'.
    """
    while True:
        payload, future = await task_queue.get()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(LM_STUDIO_URL, json=payload, timeout=None)

            if response.status_code == 200:
                response_data = response.json()
                future.set_result(response_data)
            else:
                error_detail = f"LM Studio error status: {response.status_code}, detail: {response.text}"
                future.set_exception(HTTPException(status_code=500, detail=error_detail))

        except Exception as e:
            future.set_exception(HTTPException(status_code=500, detail=str(e)))

        finally:
            task_queue.task_done()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, payload: dict):
    """
    Endpoint para solicitudes de completado de chat.
    Inyecta el prompt base si la petición viene desde FimeBot.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    injected_payload = payload.copy()
    injected_messages = injected_payload.get("messages", []).copy()

    # Detectar si es FimeBot por header
    is_fimebot = request.headers.get("X-FimeBot", "").lower() == "true"

    if is_fimebot and BASE_SYSTEM_PROMPT:
        if not any(m.get("role") == "system" for m in injected_messages):
            injected_messages.insert(0, {"role": "system", "content": BASE_SYSTEM_PROMPT})
            injected_payload["messages"] = injected_messages

    await task_queue.put((injected_payload, future))
    result = await future
    return JSONResponse(content=result)


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
        return {
            "filename": file.filename,
            "size": len(content),
            "saved_path": file_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Servir el frontend desde la carpeta del bot
app.mount("/", StaticFiles(directory="C:/Users/pedro/OneDrive/Documentos/GitHub/fimebot/fimebot", html=True), name="index")


if __name__ == "__main__":
    # Ejecutar el servidor en 0.0.0.0:1234
    uvicorn.run("inference:app", host="0.0.0.0", port=1234, reload=True)
