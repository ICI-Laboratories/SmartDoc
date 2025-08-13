from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_INVALID = r"[^A-Za-z0-9._\- ]+"
_MULTI = re.compile(r"\s+")


def slugify(text: str, max_len: int = 120) -> str:
    text = re.sub(_INVALID, " ", text).strip()
    return _MULTI.sub("-", text)[:max_len]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, data: str) -> None:
    _ensure_dir(path.parent)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        name = tmp.name
    Path(name).replace(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    _ensure_dir(path.parent)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        name = tmp.name
    Path(name).replace(path)


def _unique_path(base: Path) -> Path:
    if not base.exists():
        return base
    i = 1
    while True:
        cand = base.with_name(f"{base.stem}-{i}{base.suffix}")
        if not cand.exists():
            return cand
        i += 1


@dataclass(frozen=True)
class SavePaths:
    category: Path
    subcategory: Path
    file: Path


def _category_paths(base: str | Path, main_cat: str, sub_cat: str) -> SavePaths:
    base = Path(base)
    cat = slugify(main_cat) or "Uncategorized"
    sub = slugify(sub_cat) or "General"
    folder = base / cat / sub
    return SavePaths(base / cat, folder, folder)


def save_markdown_to_folder(markdown: str, base: str | Path, filename_base: str, main: str, sub: str) -> Path:
    if not markdown:
        raise ValueError("markdown vacío.")
    paths = _category_paths(base, main, sub)
    safe = slugify(filename_base) or "documento"
    path = (paths.subcategory / f"{safe}.md")
    path = _unique_path(path)
    if not markdown.endswith("\n"):
        markdown += "\n"
    _atomic_write_text(path, markdown)
    logger.info("Markdown guardado en %s", path)
    return path


def save_pdf_to_folder(pdf: bytes, base: str | Path, pdf_name: str, main: str, sub: str) -> Path:
    if not pdf:
        raise ValueError("pdf vacío.")
    paths = _category_paths(base, main, sub)
    name = slugify(pdf_name.rsplit(".", 1)[0]) or "documento"
    path = _unique_path(paths.subcategory / f"{name}.pdf")
    _atomic_write_bytes(path, pdf)
    logger.info("PDF guardado en %s", path)
    return path


def get_existing_categories(base: str | Path) -> Dict[str, List[str]]:
    b = Path(base)
    if not b.exists():
        return {}
    out: Dict[str, List[str]] = {}
    for cat in sorted([p for p in b.iterdir() if p.is_dir()]):
        out[cat.name] = sorted([s.name for s in cat.iterdir() if s.is_dir()])
    return out


def save_hierarchical_summary(data: dict, output_path: str | Path) -> Path:
    if data is None:
        raise ValueError("data None.")
    out = Path(output_path)
    _atomic_write_text(out, json.dumps(data, indent=4, ensure_ascii=False) + "\n")
    return out


def load_hierarchical_summary(output_path: str | Path) -> Optional[dict]:
    p = Path(output_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
