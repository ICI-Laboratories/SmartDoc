from __future__ import annotations

import logging
import re
from typing import List, Tuple

# Se añade la importación de fitz (PyMuPDF) y las opciones de Docling
import fitz  # PyMuPDF
from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    OcrOptions,
    PdfPipelineOptions,
    TableFormerMode,
)

logger = logging.getLogger(__name__)

_PAGE_SPLIT = re.compile(r"--- Página\s+(\d+)\s+---")


def extract_pages_from_text(text: str) -> List[Tuple[int, str]]:
    if not text:
        return []
    parts = re.split(_PAGE_SPLIT, text)
    out: List[Tuple[int, str]] = []
    for i in range(1, len(parts), 2):
        try:
            num = int(parts[i])
            body = parts[i + 1].strip()
            if body:
                out.append((num, body))
        except Exception:
            continue
    if not out:  # fallback: todo como una sola página
        out = [(1, text.strip())]
    return out


def convert_pdf_to_markdown(
    pdf_bytes: bytes,
    *,
    max_num_pages: int | None = None,
    max_file_size: int | None = None,
    table_mode: str = "ACCURATE",
    image_mode: str = "PLACEHOLDER",
    images_scale: float = 1.5,
) -> str:
    """
    Convierte PDF a Markdown con un enfoque híbrido y optimizado para calidad.
    1.  Intenta extraer texto directamente con PyMuPDF (rápido y preciso).
    2.  Si no hay texto nativo, recurre a Docling para el OCR, configurado para
        alta calidad (idiomas específicos y mayor DPI).
    """
    if not pdf_bytes:
        raise ValueError("Se recibieron bytes vacíos para el PDF.")

    # --- 1. Intento de extracción directa con PyMuPDF ---
    try:
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_text = [page.get_text() for page in pdf_doc]
        total_text_len = sum(len(text) for text in pages_text)

        # Si se extrajo una cantidad de texto razonable, se considera un PDF nativo.
        if total_text_len > 100:
            logger.info("PDF con texto nativo detectado. Usando extracción directa (PyMuPDF).")
            md_parts = []
            for i, page_content in enumerate(pages_text):
                if page_content.strip():
                    md_parts.append(f"--- Página {i + 1} ---\n\n{page_content.strip()}")
            return "\n\n".join(md_parts).strip()

    except Exception as e:
        logger.warning(
            "Fallo en el intento de extracción directa con PyMuPDF: %s. Se procederá con OCR.", e
        )

    # --- 2. Fallback a Docling (OCR) si no se encontró texto nativo ---
    logger.info(
        "El PDF parece escaneado o no tiene texto extraíble. Recurriendo a OCR con Docling."
    )
    try:
        import os
        from io import BytesIO
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat, DocumentStream

        ImageRefMode = None
        try:
            from docling_core.types.doc import ImageRefMode as _IRM
            ImageRefMode = _IRM
        except Exception:
            pass

        # --- Configuración de OCR para alta calidad ---
        ocr_opts = OcrOptions(
            easy_ocr=EasyOcrOptions(
                lang=["es", "en"]  # Especificar idiomas mejora la precisión
            )
        )

        pb_marker = ""
        pipeline_opts = PdfPipelineOptions(
            artifacts_path=os.getenv("DOCLING_ARTIFACTS_PATH"),
            enable_remote_services=os.getenv("DOCLING_ENABLE_REMOTE", "").lower() in {"1", "true", "yes"},
            do_table_structure=True,
            ocr=ocr_opts, # Aplicar las opciones de OCR
            images_dpi=300, # Aumentar DPI para mejorar la calidad de la imagen para el OCR
        )

        mode = (os.getenv("DOCLING_TABLE_MODE") or table_mode).upper()
        if mode in {"FAST", "ACCURATE"}:
            pipeline_opts.table_structure_options.mode = getattr(TableFormerMode, mode)

        if ImageRefMode is not None:
            imode = (os.getenv("DOCLING_MD_IMAGE_MODE") or image_mode).upper()
            image_ref_mode = getattr(ImageRefMode, imode, getattr(ImageRefMode, "PLACEHOLDER", None))
            if image_ref_mode is not None and imode in {"EMBEDDED", "REFERENCED"}:
                if hasattr(pipeline_opts, "images_scale"):
                    pipeline_opts.images_scale = float(os.getenv("DOCLING_IMAGES_SCALE", images_scale))
                if hasattr(pipeline_opts, "generate_page_images"):
                    pipeline_opts.generate_page_images = True
        else:
            image_ref_mode = None

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)}
        )
        conv = converter.convert(DocumentStream(name="upload.pdf", stream=BytesIO(pdf_bytes)))

        export_fn = getattr(conv.document, "export_to_markdown")
        params = export_fn.__code__.co_varnames
        kwargs = {}
        if "image_mode" in params and image_ref_mode is not None:
            kwargs["image_mode"] = image_ref_mode
        if "page_break_placeholder" in params:
            kwargs["page_break_placeholder"] = pb_marker

        md = export_fn(**kwargs)
        if pb_marker in md:
            parts = [p.strip() for p in md.split(pb_marker)]
            md = "\n\n".join(
                f"--- Página {i+1} ---\n\n{p}" for i, p in enumerate(parts) if p
            )
        return md.strip()
    except Exception as e:
        logger.exception("Error durante la conversión con Docling: %s", e)
        raise