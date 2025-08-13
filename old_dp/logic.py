# document_processor/logic.py

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ----------------------------
# Configuración y utilidades
# ----------------------------

INVALID_CHARS = r'[^A-Za-z0-9._\- ]+'
MULTISPACE = re.compile(r"\s+")


def slugify(text: str, max_len: int = 120) -> str:
    text = re.sub(INVALID_CHARS, " ", text, flags=re.UNICODE).strip()
    text = MULTISPACE.sub("-", text)
    return text[:max_len] if max_len > 0 else text


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    _ensure_dir(path.parent)
    with tempfile.NamedTemporaryFile("w", encoding=encoding, delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        tmp.flush()
        tmp_name = tmp.name
    Path(tmp_name).replace(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    _ensure_dir(path.parent)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        tmp.flush()
        tmp_name = tmp.name
    Path(tmp_name).replace(path)


def _unique_path(base: Path) -> Path:
    if not base.exists():
        return base
    stem, suffix = base.stem, base.suffix
    i = 1
    while True:
        cand = base.with_name(f"{stem}-{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


# --------------------------------
# Conversión de documentos (PDF→MD)
# --------------------------------

def convert_pdf_to_markdown(
    pdf_bytes: bytes,
    *,
    max_num_pages: Optional[int] = None,
    max_file_size: Optional[int] = None,
    enable_remote_services: bool = False,
    table_mode: str = "ACCURATE",      # "ACCURATE" | "FAST"
    image_mode: str = "PLACEHOLDER",   # "PLACEHOLDER" | "EMBEDDED" | "REFERENCED"
    images_scale: float = 1.5,
) -> str:
    """
    Convierte PDF (bytes) a Markdown usando Docling.
    - Exporta el documento completo (sin iterar por páginas).
    - Si la versión soporta 'page_break_placeholder', inserta marcadores de salto.
    - Maneja imágenes como PLACEHOLDER/EMBEDDED/REFERENCED cuando el enum existe.

    Variables de entorno (opcionales):
      - DOCLING_ARTIFACTS_PATH
      - DOCLING_ENABLE_REMOTE: "1"/"true"/"yes"
      - DOCLING_TABLE_MODE: "FAST" | "ACCURATE"
      - DOCLING_MD_IMAGE_MODE: "PLACEHOLDER" | "EMBEDDED" | "REFERENCED"
      - DOCLING_IMAGES_SCALE: float
    """
    if not pdf_bytes:
        raise ValueError("Se recibieron bytes vacíos para el PDF.")

    try:
        import os
        from io import BytesIO

        # Importa Docling dentro de la función para evitar hard deps en import-time.
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat, DocumentStream
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

        # ImageRefMode puede vivir en distintos módulos según la versión
        ImageRefMode = None  # tipo: ignore
        try:
            from docling_core.types.doc import ImageRefMode as _ImageRefMode  # type: ignore
            ImageRefMode = _ImageRefMode
        except Exception:
            try:
                # Algunas versiones lo exponen aquí:
                from docling.datamodel.document import ImageRefMode as _ImageRefMode  # type: ignore
                ImageRefMode = _ImageRefMode
            except Exception:
                logger.info(
                    "No se encontró ImageRefMode en esta versión de Docling; "
                    "se usará el modo por defecto del exportador."
                )

        artifacts_path = os.getenv("DOCLING_ARTIFACTS_PATH")
        enable_remote_env = os.getenv("DOCLING_ENABLE_REMOTE", "").lower() in {"1", "true", "yes"}
        table_mode_env = (os.getenv("DOCLING_TABLE_MODE") or table_mode).upper()
        image_mode_env = (os.getenv("DOCLING_MD_IMAGE_MODE") or image_mode).upper()
        images_scale_env = float(os.getenv("DOCLING_IMAGES_SCALE", images_scale))

        pipeline_opts = PdfPipelineOptions(
            artifacts_path=artifacts_path,
            enable_remote_services=enable_remote_services or enable_remote_env,
            do_table_structure=True,
        )

        # Modo de tablas (FAST/ACCURATE)
        if table_mode_env in {"FAST", "ACCURATE"}:
            pipeline_opts.table_structure_options.mode = getattr(TableFormerMode, table_mode_env)

        # Opciones de imágenes — solo asignamos si existen en esta versión
        if ImageRefMode is not None:
            # Map a enum si existe
            image_mode_map = {
                "PLACEHOLDER": getattr(ImageRefMode, "PLACEHOLDER", None),
                "EMBEDDED": getattr(ImageRefMode, "EMBEDDED", None),
                "REFERENCED": getattr(ImageRefMode, "REFERENCED", None),
            }
            image_ref_mode = image_mode_map.get(image_mode_env) or getattr(ImageRefMode, "PLACEHOLDER", None)
        else:
            image_ref_mode = None  # el export usará su default

        # Activar generación de imágenes solo si hace falta y la opción existe
        if image_ref_mode is not None and image_mode_env in {"EMBEDDED", "REFERENCED"}:
            if hasattr(pipeline_opts, "images_scale"):
                setattr(pipeline_opts, "images_scale", images_scale_env)
            if hasattr(pipeline_opts, "generate_page_images"):
                setattr(pipeline_opts, "generate_page_images", True)
            if hasattr(pipeline_opts, "generate_picture_images"):
                setattr(pipeline_opts, "generate_picture_images", True)

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)}
        )

        source = DocumentStream(name="upload.pdf", stream=BytesIO(pdf_bytes))

        convert_kwargs = {}
        if max_num_pages is not None:
            convert_kwargs["max_num_pages"] = int(max_num_pages)
        if max_file_size is not None:
            convert_kwargs["max_file_size"] = int(max_file_size)

        conv_res = converter.convert(source, **convert_kwargs)
        doc = conv_res.document

        # Export a Markdown con tolerancia a diferencias de firma
        md = _export_markdown_with_compat(doc, image_ref_mode=image_ref_mode)

        return md.strip()

    except ImportError as e:
        # Docling no instalado
        logger.exception("Docling no está instalado o no se pudo importar: %s", e)
        raise
    except Exception as e:
        logger.exception("Error durante la conversión con Docling: %s", e)
        raise


def _export_markdown_with_compat(doc, *, image_ref_mode):
    """
    Exporta a Markdown tolerando diferencias entre versiones de Docling:
      - Si existe 'page_break_placeholder' lo usamos.
      - Si 'image_ref_mode' es None, omitimos el parámetro 'image_mode'.
    """
    import inspect

    export_fn = getattr(doc, "export_to_markdown", None)
    if export_fn is None:
        raise RuntimeError("La instancia de documento Docling no expone 'export_to_markdown'.")

    params = set(inspect.signature(export_fn).parameters.keys())

    kwargs = {}
    if image_ref_mode is not None and "image_mode" in params:
        kwargs["image_mode"] = image_ref_mode

    # Algunos builds permiten colocar un marcador de salto de página
    pagebreak_marker = "<!-- pagebreak -->"
    if "page_break_placeholder" in params:
        kwargs["page_break_placeholder"] = pagebreak_marker

    md = export_fn(**kwargs)

    # Si quieres encabezados visibles por página, puedes convertir los marcadores aquí:
    if pagebreak_marker in md:
        parts = [p.strip() for p in md.split(pagebreak_marker)]
        md = "\n\n".join(
            f"--- Página {i+1} ---\n\n{chunk}" for i, chunk in enumerate(parts) if chunk
        )
    return md


# (Opcional) conversión a JSON “lossless” para depuración/índices enriquecidos
def convert_pdf_to_lossless_json(pdf_bytes: bytes) -> dict:
    """
    Devuelve la representación completa del DoclingDocument como dict.
    Útil para trazabilidad, indexación avanzada o depuración.
    """
    if not pdf_bytes:
        raise ValueError("Se recibieron bytes vacíos para el PDF.")

    from io import BytesIO
    from docling.document_converter import DocumentConverter
    from docling.datamodel.base_models import DocumentStream

    conv = DocumentConverter()
    res = conv.convert(DocumentStream(name="upload.pdf", stream=BytesIO(pdf_bytes)))
    return res.document.export_to_dict()


# -------------------------------------
# Estructura de carpetas y guardado I/O
# -------------------------------------

@dataclass(frozen=True)
class SavePaths:
    category: Path
    subcategory: Path
    file: Path


def get_existing_categories(output_folder: str | Path) -> Dict[str, List[str]]:
    base = Path(output_folder)
    if not base.exists():
        return {}
    result: Dict[str, List[str]] = {}
    for cat_dir in sorted([p for p in base.iterdir() if p.is_dir()]):
        subcats = sorted([s.name for s in cat_dir.iterdir() if s.is_dir()])
        result[cat_dir.name] = subcats
    return result


def _category_paths(base_folder_path: str | Path, main_category: str, sub_category: str) -> SavePaths:
    base = Path(base_folder_path)
    cat = slugify(main_category) or "Uncategorized"
    sub = slugify(sub_category) or "General"
    category_folder = base / cat
    subcategory_folder = category_folder / sub
    return SavePaths(category_folder, subcategory_folder, subcategory_folder)


def save_markdown_to_folder(
    markdown_text: str,
    base_folder_path: str | Path,
    filename_base: str,
    main_category: str,
    sub_category: str,
    *,
    exist_ok: bool = False,
    ensure_trailing_newline: bool = True,
) -> Path:
    """
    Guarda Markdown en base/category/subcategory/filename.md de forma atómica.
    - Sanitiza nombres y crea carpetas si no existen.
    - Si exist_ok=False y el archivo ya existe, crea nombre único -1, -2, ...
    """
    if not markdown_text:
        raise ValueError("markdown_text está vacío.")

    paths = _category_paths(base_folder_path, main_category, sub_category)
    safe_base = slugify(filename_base) or "documento"
    md_path = paths.subcategory / f"{safe_base}.md"
    if not exist_ok:
        md_path = _unique_path(md_path)

    if ensure_trailing_newline and not markdown_text.endswith("\n"):
        markdown_text += "\n"

    _atomic_write_text(md_path, markdown_text, encoding="utf-8")
    logger.info("Markdown guardado en: %s", md_path)
    return md_path


def save_pdf_to_folder(
    pdf_bytes: bytes,
    base_folder_path: str | Path,
    pdf_filename: str,
    main_category: str,
    sub_category: str,
    *,
    exist_ok: bool = False,
) -> Path:
    """
    Guarda PDF en base/category/subcategory/filename.pdf de forma atómica.
    - Sanitiza nombre
    - Si exist_ok=False y el archivo existe, crea nombre único.
    """
    if not pdf_bytes:
        raise ValueError("pdf_bytes está vacío.")

    paths = _category_paths(base_folder_path, main_category, sub_category)
    name = slugify(pdf_filename.rsplit(".", 1)[0] if "." in pdf_filename else pdf_filename) or "documento"
    pdf_path = paths.subcategory / f"{name}.pdf"
    if not exist_ok:
        pdf_path = _unique_path(pdf_path)

    _atomic_write_bytes(pdf_path, pdf_bytes)
    logger.info("PDF guardado en: %s", pdf_path)
    return pdf_path


# -------------------------------
# Resúmenes y archivos auxiliares
# -------------------------------

def save_hierarchical_summary(final_data: dict, output_path: str | Path) -> Path:
    """Guarda JSON (UTF-8) con escritura atómica."""
    if final_data is None:
        raise ValueError("final_data no puede ser None.")
    out = Path(output_path)
    _atomic_write_text(out, json.dumps(final_data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("Resumen jerárquico guardado en: %s", out)
    return out


def load_hierarchical_summary(output_path: str | Path) -> Optional[dict]:
    """Carga JSON y devuelve dict o None si no existe o está corrupto."""
    path = Path(output_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("JSON inválido en %s: %s", path, e)
        return None
