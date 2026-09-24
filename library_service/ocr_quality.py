"""Independent, bounded CPU checks for gateway OCR; not a completeness proof.

Keep the model's reading order, tables and equations intact. Missing high-confidence
interior phrases require review; only omitted margin lines are supplemented.
"""
from __future__ import annotations

import csv
import html
import io
import os
import re
import subprocess
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass

_verification_slot = threading.Lock()


@dataclass(frozen=True)
class VerifiedLine:
    text: str
    top: int
    bottom: int
    words: tuple[tuple[str, float], ...]


def normalized_words(text: str) -> list[str]:
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Join words hyphenated at a physical line boundary, not separate words.
    text = re.sub(r"(?<=\w)[-\u2010\u2011]\s*\n\s*(?=\w)", "", text)
    return re.findall(r"[a-z0-9]+", text)


def validate_recognition(text: str) -> None:
    """Reject degenerate output even if a backend incorrectly reports stop."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("OCR vacío; la página requiere revisión.")
    plain = html.unescape(re.sub(r"<[^>]*>", " ", text))
    compact = re.sub(r"\s+", "", plain)
    if any(any(character.isalpha() for character in match.group(1))
           for match in re.finditer(r"(.{1,80})\1{19,}", compact)):
        raise ValueError("OCR repetitivo; la página requiere revisión.")
    words = normalized_words(text)
    lexical = [word for word in words if not word.isdigit()]
    if len(lexical) >= 80 and len(set(lexical)) <= 3:
        raise ValueError("OCR repetitivo; la página requiere revisión.")


def tesseract_lines(image: bytes) -> list[VerifiedLine]:
    """One CPU thread/process, no shell, bounded image and execution time."""
    environment = dict(os.environ, OMP_THREAD_LIMIT="1", OMP_NUM_THREADS="1")
    try:
        with _verification_slot:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "spa+eng", "--psm", "3", "tsv"],
                input=image, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30, check=True, env=environment,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("No se pudo verificar la cobertura OCR; la página requiere revisión.") from exc
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("La verificación OCR excedió el límite de salida.")
    groups = {}
    try:
        rows = csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t")
        required = {"level", "page_num", "block_num", "par_num", "line_num", "text", "conf", "top", "height"}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError("invalid TSV header")
        for row in rows:
            if row["level"] != "5" or not row["text"].strip():
                continue
            key = tuple(row[name] for name in ("page_num", "block_num", "par_num", "line_num"))
            group = groups.setdefault(key, [])
            group.append((row["text"].strip(), float(row["conf"]), int(row["top"]), int(row["height"])))
        return [VerifiedLine(" ".join(w[0] for w in group), min(w[2] for w in group),
                             max(w[2] + w[3] for w in group), tuple((w[0], w[1]) for w in group))
                for group in groups.values()]
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("La verificación OCR devolvió datos inválidos.") from exc


def preserve_native(text: str, native_text: str) -> str:
    """Retain native signs, symbols and word order without normalized comparison."""
    result = text.strip()
    native = native_text.strip()
    if native and native not in result:
        result += "\n\n### Texto nativo del PDF conservado\n\n" + native
    return result


def verify_and_preserve(text: str, image_height: int, lines: list[VerifiedLine], native_text: str = "") -> str:
    """Conservative omissions check, preserving original model output verbatim.

    Confidence is an OCR heuristic, not calibrated certainty. We compare substantial
    words rather than layout order or math/numeric notation to avoid rewriting tables.
    """
    validate_recognition(text)
    recognized = Counter(normalized_words(text))
    native_words = Counter(normalized_words(native_text))
    available = recognized + native_words
    margins = []
    evidence = 0
    for line in lines:
        strong = [word for raw, confidence in line.words if confidence >= 90
                  for word in normalized_words(raw) if len(word) >= 3 and not word.isdigit()]
        if len(strong) < 2:
            # Keep page labels useful without treating bare numbers as text evidence.
            if re.match(r"^(pagina|page)\s+\d+\b", " ".join(normalized_words(line.text))):
                strong = normalized_words(line.text) if all(c >= 90 for _, c in line.words) else []
            if len(strong) < 2:
                continue
        evidence += len(strong)
        missing = Counter(strong) - available
        if not missing:
            continue
        substantial = sum(missing.values()) >= 2 and sum(missing.values()) / len(strong) >= 0.4
        margin = line.bottom <= image_height * 0.15 or line.top >= image_height * 0.85
        if margin and all(confidence >= 90 for _, confidence in line.words):
            margins.append(line.text)
            available.update(normalized_words(line.text))
        elif substantial:
            raise ValueError("La verificación detectó texto omitido o discrepante; "
                             "la página requiere revisión antes de indexarse.")
    if evidence < 4:
        raise ValueError("La imagen no aporta texto suficiente para verificar el OCR; "
                         "la página requiere revisión.")
    result = preserve_native(text, native_text)
    if margins:
        result += "\n\n### Texto de márgenes recuperado por verificación OCR\n\n" + "\n".join(margins)
    return result
