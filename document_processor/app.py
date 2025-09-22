from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, List

import httpx
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sentence_transformers import SentenceTransformer

from document_processor.core_pdf import convert_pdf_to_markdown, extract_pages_from_text
from document_processor.core_io import (
    slugify,
    get_existing_categories,
    save_markdown_to_folder,
    save_pdf_to_folder,
    save_hierarchical_summary,
    _category_paths,
)
from document_processor.llm_client import get_http_client, classify_text, summarize_page_with_retry


logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


try:
    EMBEDDING_MODEL = SentenceTransformer('BAAI/bge-large-en-v1.5')
    logger.info("Modelo de SentenceTransformer 'BAAI/bge-large-en-v1.5' cargado correctamente.")
except Exception as e:
    logger.error(f"FATAL: No se pudo cargar el modelo de SentenceTransformer: {e}")
    EMBEDDING_MODEL = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent / '.env', env_file_encoding='utf-8', extra='ignore')

    llm_service_url: str = Field(default="http://127.0.0.1:8001")
    base_dir: Path = Field(alias="SMARTREVIEW_BASE", default=Path.home() / "SmartReviewData")
    enable_cors: bool = True
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    require_api_key: bool = False
    api_key_header_name: str = "x-api-key"
    api_key_value: Optional[str] = None
    max_pdf_bytes: int = 30 * 1024 * 1024
    classify_snippet_len: int = 4000
    http_timeout_seconds: float = 20.0
    max_summary_concurrency: int = 4

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


app = FastAPI(title="SmartReview Document Processor", version="1.5")
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
    summary_data = {"title": original_filename, "source": "synchronous", "blocks": blocks}
    try:
        save_hierarchical_summary(summary_data, output_path)
        logger.info("Resumen guardado en %s", output_path)
    except Exception as e:
        logger.exception("Error guardando resumen: %s", e)


@app.post(
    "/process_document/",
    response_model=ProcessResponse,
    dependencies=[Depends(require_api_key)],
    summary="Procesa un único documento PDF de forma síncrona",
)
async def process_document(
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
    safe_filename_base = slugify(filename_base) or "documento"
    target_paths = _category_paths(user_folder, main_cat, sub_cat)
    potential_path = target_paths.subcategory / f"{safe_filename_base}.md"

    if potential_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"El documento '{file.filename}' ya existe en '{main_cat}/{sub_cat}'. No se procesó el duplicado."
        )

    try:
        md_path = save_markdown_to_folder(markdown_content, user_folder, filename_base, main_cat, sub_cat)
        pdf_path = save_pdf_to_folder(pdf_bytes, user_folder, file.filename, main_cat, sub_cat)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al guardar en disco: {e}")

    summary_path = md_path.with_suffix(".summary.json")
    await create_and_save_summary_async(
        markdown_text=markdown_content,
        settings=settings,
        output_path=summary_path,
        original_filename=file.filename,
    )
    
    if EMBEDDING_MODEL:
        try:
            text_chunks = [p.strip() for p in markdown_content.split('\n\n') if len(p.strip()) > 30]
            
            if text_chunks:
                logger.info(f"Generando {len(text_chunks)} vectores para '{file.filename}'...")
                embeddings = EMBEDDING_MODEL.encode(text_chunks, show_progress_bar=False, convert_to_numpy=True)
                
                vector_path = md_path.with_suffix(".npz")
                np.savez_compressed(vector_path, embeddings=embeddings, chunks=np.array(text_chunks, dtype=object))
                logger.info(f"Vectores para '{file.filename}' guardados en: {vector_path}")
            else:
                logger.warning(f"No se encontraron chunks de texto suficientemente largos para vectorizar en '{file.filename}'.")

        except Exception as e:
            logger.error(f"Fallo al crear o guardar los vectores para '{file.filename}': {e}")
    else:
        logger.warning("El modelo de embeddings no está cargado. Se omitirá el paso de vectorización.")

    logger.info("Procesado y resumido %s en %.2fs -> %s", file.filename, time.perf_counter() - t0, md_path)

    return ProcessResponse(
        message=f"Archivo '{file.filename}' procesado y resumido con éxito.",
        original_filename=file.filename,
        markdown_path=str(md_path),
        pdf_path=str(pdf_path),
        category=f"{main_cat}/{sub_cat}",
        summary_path=str(summary_path),
    )


@app.get("/", summary="Endpoint de estado")
async def read_root():
    return {"status": "Document Processor Service is running"}