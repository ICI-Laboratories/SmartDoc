import asyncio
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI()

# Cola asíncrona para encolar peticiones al LLM
task_queue = asyncio.Queue()


# Modelo de datos para solicitudes de chat/completions
class ChatRequest(BaseModel):
    model: str
    messages: list
    response_format: dict = None
    temperature: float = 0.7
    max_tokens: int = 100
    stream: bool = False


@app.on_event("startup")
async def startup_event():
    # Inicia el worker para procesar la cola de tareas
    app.state.worker_task = asyncio.create_task(worker())
    app.state.processing = True


async def worker():
    """Procesa secuencialmente las peticiones encoladas."""
    while True:
        payload, future = await task_queue.get()
        try:
            # Simula un retardo en la inferencia (aquí deberías llamar a tu modelo)
            await asyncio.sleep(2)
            first_message = payload.get("messages", [{}])[0].get("content", "")
            response_data = {
                "choices": [{
                    "message": {
                        "content": f"Respuesta simulada para: {first_message}"
                    }
                }]
            }
            future.set_result(response_data)
        except Exception as e:
            future.set_exception(HTTPException(status_code=500, detail=str(e)))
        finally:
            task_queue.task_done()


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict):
    """
    Endpoint para las solicitudes de completado de chat.
    La petición se encola y se espera el resultado del procesamiento.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    await task_queue.put((payload, future))
    result = await future
    return JSONResponse(content=result)


@app.post("/v1/upload")
async def upload_document(
    file: UploadFile = File(...),
    username: str = Form(...)
):
    """
    Endpoint para subir documentos.
    Se espera el archivo y el nombre de usuario; luego se guarda el archivo en:
      D:\clasdocusers\<username>\nombre_del_archivo
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
    uvicorn.run("inference:app", host="0.0.0.0", port=1234, reload=True)
