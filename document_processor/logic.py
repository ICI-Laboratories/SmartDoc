# document_processor/logic.py

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import docling

logger = logging.getLogger(__name__)
# Config por defecto (el integrador puede ajustar el nivel/handler en main)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ----------------------------
# Configuración y utilidades
# ----------------------------

INVALID_CHARS = r'[^A-Za-z0-9._\- ]+'  # conservar letras, números, ., _, -, espacio
MULTISPACE = re.compile(r"\s+")


def slugify(text: str, max_len: int = 120) -> str:
    """
    Convierte texto a un "slug" seguro para usar en rutas de archivos.
    - Quita caracteres inválidos
    - Colapsa espacios a '-'
    - Recorta longitud
    """
    text = re.sub(INVALID_CHARS, " ", text, flags=re.UNICODE).strip()
    text = MULTISPACE.sub("-", text)
    return text[:max_len] if max_len > 0 else text


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Escritura atómica para texto."""
    _ensure_dir(path.parent)
    with tempfile.NamedTemporaryFile("w", encoding=encoding, delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        tmp.flush()
        # En Windows es más seguro cerrar antes de replace (NamedTemporaryFile ya cierra al salir)
        tmp_name = tmp.name
    Path(tmp_name).replace(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Escritura atómica para binarios."""
    _ensure_dir(path.parent)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        tmp.flush()
        tmp_name = tmp.name
    Path(tmp_name).replace(path)


def _unique_path(base: Path) -> Path:
    """Genera un path único si `base` existe (archivo-1.md, archivo-2.md...)."""
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

def convert_pdf_to_markdown(pdf_bytes: bytes) -> str:
    """
    Convierte el contenido de un PDF (bytes) a Markdown.
    Lanza ValueError si pdf_bytes está vacío.
    """
    if not pdf_bytes:
        raise ValueError("Se recibieron bytes vacíos para el PDF.")

    try:
        # Ejemplo real (cuando integres docling):
        # md = docling.from_bytes(pdf_bytes).to_markdown()

        # --- Placeholder de demostración ---
        md = (
            "# Documento Convertido\n\n"
            "Ejemplo de contenido convertido a Markdown.\n\n"
            "## Sección 1\n\n"
            "- Punto A\n- Punto B\n\n"
            "## Tablas y Datos\n\n"
            "| Métrica | Valor |\n|---|---|\n| Tasa de Éxito | 95% |\n"
        )
        # --- Fin placeholder ---

        return md.strip()

    except Exception as e:
        logger.exception("Error durante la conversión de PDF a Markdown: %s", e)
        # Relevanta la excepción para que el caller decida cómo proceder
        raise


# -------------------------------------
# Estructura de carpetas y guardado I/O
# -------------------------------------

@dataclass(frozen=True)
class SavePaths:
    category: Path
    subcategory: Path
    file: Path


def get_existing_categories(output_folder: str | Path) -> Dict[str, List[str]]:
    """
    Escanea el directorio base y devuelve {categoria: [subcategorias]}.
    Devuelve {} si la carpeta no existe.
    """
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
    - Sanitiza nombres
    - Si exist_ok=False y el archivo existe, crea nombre único -1, -2, ...
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

    # fuerza extensión .pdf si no la trae
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
