import asyncio
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import httpx  # Para llamadas asíncronas a LM Studio

app = FastAPI()

# Cola asíncrona para encolar peticiones al LLM
task_queue = asyncio.Queue()

# Ajusta esta variable según donde tengas corriendo LM Studio
LM_STUDIO_URL = "http://localhost:1235/v1/chat/completions"

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
    Crea una tarea asíncrona (worker) que procesará la cola de peticiones.
    """
    app.state.worker_task = asyncio.create_task(worker())


async def worker():
    """
    Procesa secuencialmente las peticiones que llegan a la cola 'task_queue'.
    """
    while True:
        # Esperar a que llegue un item (payload, future) a la cola
        payload, future = await task_queue.get()

        try:
            # Llamada asíncrona real a LM Studio
            async with httpx.AsyncClient() as client:
                response = await client.post(LM_STUDIO_URL, json=payload, timeout=None)

            if response.status_code == 200:
                # Obtenemos la respuesta de LM Studio tal cual (JSON)
                response_data = response.json()
                future.set_result(response_data)
            else:
                # Si hubo error en LM Studio, notificamos
                error_detail = f"LM Studio error status: {response.status_code}, detail: {response.text}"
                future.set_exception(HTTPException(status_code=500, detail=error_detail))

        except Exception as e:
            future.set_exception(HTTPException(status_code=500, detail=str(e)))

        finally:
            task_queue.task_done()


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict):
    """
    Endpoint para solicitudes de completado de chat.
    - 'payload' debe ser el JSON del request (modelo, messages, etc.)
    - Encola la petición y espera el resultado para retornar.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    # Metemos la petición a la cola
    await task_queue.put((payload, future))

    # Esperamos la respuesta que pondrá el worker
    result = await future
    return JSONResponse(content=result)


@app.post("/v1/upload")
async def upload_document(
    file: UploadFile = File(...),
    username: str = Form(...)
):
    """
    Endpoint (ejemplo) para subir documentos.
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


if __name__ == "__main__":
    # Ejecutar el servidor en 0.0.0.0:1234
    uvicorn.run("inference:app", host="0.0.0.0", port=1234, reload=True)
