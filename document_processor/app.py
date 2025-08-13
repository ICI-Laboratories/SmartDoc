from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, List

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# 👇 Imports absolutos desde el paquete document_processor
from document_processor.core_pdf import convert_pdf_to_markdown, extract_pages_from_text
from document_processor.core_io import (
    slugify,
    get_existing_categories,
    save_markdown_to_folder,
    save_pdf_to_folder,
    save_hierarchical_summary,
)
from document_processor.llm_client import get_http_client, classify_text, summarize_page_with_retry


logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


# ---------------- Settings + seguridad ----------------
class Settings(BaseSettings):
    llm_service_url: str = Field(default="http://127.0.0.1:8001")
    base_dir: Path = Field(default=Path.home() / "SmartDocData")
    enable_cors: bool = True
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    require_api_key: bool = False
    api_key_header_name: str = "x-api-key"
    api_key_value: Optional[str] = None
    max_pdf_bytes: int = 30 * 1024 * 1024
    classify_snippet_len: int = 4000
    http_timeout_seconds: float = 20.0
    max_summary_concurrency: int = 4

    class Config:
        env_prefix = "SMARTDOC_"
        case_sensitive = False


def get_settings() -> Settings:
    s = Settings()
    s.base_dir.mkdir(parents=True, exist_ok=True)
    return s


async def require_api_key(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.require_api_key:
        return
    provided = request.headers.get(settings.api_key_header_name)
    if not provided or provided != (settings.api_key_value or ""):
        raise HTTPException(status_code=401, detail="API key inválida o ausente.")


# ---------------- App ----------------
app = FastAPI(title="SmartDoc Document Processor", version="1.2")
_settings = get_settings()

if _settings.enable_cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class ProcessResponse(BaseModel):
    message: str
    original_filename: str
    markdown_path: str
    pdf_path: str
    category: str
    summary_path: str


# ---------------- Background summary ----------------
async def create_and_save_summary_async(
    markdown_text: str,
    settings: Settings,
    output_path: Path,
    original_filename: str,
):
    pages = extract_pages_from_text(markdown_text)
    client = get_http_client(settings.llm_service_url, settings.http_timeout_seconds)
    sem = asyncio.Semaphore(max(1, settings.max_summary_concurrency))

    async def summarize_one(num: int, txt: str):
        async with sem:
            try:
                return await summarize_page_with_retry(client, [(num, txt)])
            except Exception as e:
                logger.warning("Fallo resumen pág %s: %s", num, e)
                return {"summary": f"Resumen de ejemplo para la página {num}."}

    blocks = await asyncio.gather(*(summarize_one(n, t) for n, t in pages))
    summary_data = {"title": original_filename, "source": "background", "blocks": blocks}
    try:
        save_hierarchical_summary(summary_data, output_path)
        logger.info("Resumen guardado en %s", output_path)
    except Exception as e:
        logger.exception("Error guardando resumen: %s", e)


def create_and_save_summary(*args, **kwargs):
    asyncio.run(create_and_save_summary_async(*args, **kwargs))


# ---------------- Endpoints ----------------
@app.post(
    "/process_document/",
    response_model=ProcessResponse,
    dependencies=[Depends(require_api_key)],
    summary="Procesa un único documento PDF",
)
async def process_document(
    background_tasks: BackgroundTasks,
    username: str = Form(..., min_length=1, max_length=120),
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
):
    t0 = time.perf_counter()
    if not file.filename:
        raise HTTPException(status_code=400, detail="El archivo no tiene nombre.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Archivo vacío.")
    if len(pdf_bytes) > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail="Archivo excede el límite.")

    user_folder = settings.base_dir / slugify(username)

    try:
        markdown_content = convert_pdf_to_markdown(pdf_bytes)
        if not markdown_content:
            raise HTTPException(status_code=400, detail="La conversión a Markdown no produjo contenido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en conversión PDF→MD: {e}")

    snippet = markdown_content[: settings.classify_snippet_len]
    existing = get_existing_categories(user_folder)
    client = get_http_client(settings.llm_service_url, settings.http_timeout_seconds)

    try:
        classification = await classify_text(client, snippet, existing)
        main_cat = classification.get("main_category") or "Sin-Clasificar"
        sub_cat = classification.get("sub_category") or "General"
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo conectar con LLM: {e}")

    filename_base = Path(file.filename).stem
    try:
        md_path = save_markdown_to_folder(markdown_content, user_folder, filename_base, main_cat, sub_cat)
        pdf_path = save_pdf_to_folder(pdf_bytes, user_folder, file.filename, main_cat, sub_cat)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al guardar en disco: {e}")

    summary_path = md_path.with_suffix(".summary.json")

    background_tasks.add_task(
        create_and_save_summary,
        markdown_text=markdown_content,
        settings=settings,
        output_path=summary_path,
        original_filename=file.filename,
    )

    logger.info("Procesado %s en %.2fs -> %s / %s", file.filename, time.perf_counter() - t0, md_path, pdf_path)

    return ProcessResponse(
        message=f"Archivo '{file.filename}' recibido; resumen en segundo plano.",
        original_filename=file.filename,
        markdown_path=str(md_path),
        pdf_path=str(pdf_path),
        category=f"{main_cat}/{sub_cat}",
        summary_path=str(summary_path),
    )


@app.get("/", summary="Endpoint de estado")
async def read_root():
    return {"status": "Document Processor Service is running"}
