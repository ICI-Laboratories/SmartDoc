from __future__ import annotations

import logging
import re
from typing import List, Tuple

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
    if not pdf_bytes:
        raise ValueError("Se recibieron bytes vacíos para el PDF.")
    try:
        import os
        from io import BytesIO
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat, DocumentStream
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

        ImageRefMode = None
        try:
            from docling_core.types.doc import ImageRefMode as _IRM  # type: ignore
            ImageRefMode = _IRM
        except Exception:
            try:
                from docling.datamodel.document import ImageRefMode as _IRM  # type: ignore
                ImageRefMode = _IRM
            except Exception:
                pass

        pb_marker = "<!-- pagebreak -->"
        pipeline_opts = PdfPipelineOptions(
            artifacts_path=os.getenv("DOCLING_ARTIFACTS_PATH"),
            enable_remote_services=os.getenv("DOCLING_ENABLE_REMOTE", "").lower() in {"1", "true", "yes"},
            do_table_structure=True,
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
                if hasattr(pipeline_opts, "generate_picture_images"):
                    pipeline_opts.generate_picture_images = True
        else:
            image_ref_mode = None

        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)})
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
            md = "\n\n".join(f"--- Página {i+1} ---\n\n{p}" for i, p in enumerate(parts) if p)
        return md.strip()
    except Exception as e:
        logger.exception("Error Docling: %s", e)
        raise
