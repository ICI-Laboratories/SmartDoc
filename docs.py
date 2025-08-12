# docs.py
import fitz
import easyocr
from PIL import Image
import io
import os
import re
import json
import streamlit as st

# ---------- Config & helpers ----------

def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "y")

@st.cache_resource
def get_reader():
    # GPU opcional por variable de entorno SMARTDOC_OCR_GPU=1
    use_gpu = _env_flag("SMARTDOC_OCR_GPU", "0")
    return easyocr.Reader(['en', 'es'], gpu=use_gpu)

reader = get_reader()

# ---------- OCR & extracción ----------

def extract_text_with_easyocr(pdf_file) -> str:
    """Extrae texto por página; usa OCR si el texto nativo es pobre."""
    # Obtén bytes del PDF de forma robusta
    if hasattr(pdf_file, "getvalue"):
        raw = pdf_file.getvalue()
    else:
        raw = pdf_file.read()

    doc = fitz.open(stream=raw, filetype="pdf")
    extracted_text = ""

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if len(text.strip()) >= 20:
            extracted_text += f"\n--- Página {page_num + 1} ---\n{text.strip()}\n"
        else:
            # Render a imagen y pasa por OCR
            pix = page.get_pixmap(dpi=200, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_bytes = io.BytesIO()
            img.save(img_bytes, format="PNG")
            ocr_text = " ".join(reader.readtext(img_bytes.getvalue(), detail=0))
            if is_valid_text(ocr_text):
                extracted_text += f"\n--- Página {page_num + 1} ---\n{ocr_text.strip()}\n"
    return extracted_text

def is_valid_text(text: str) -> bool:
    text = text.strip()
    # descarta cadenas cortas y “solo fecha”
    return len(text) > 50 and not re.match(r'^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,4}$', text)

def count_words(text: str) -> int:
    return len(text.split())

def get_classification_snippet(text: str) -> str:
    lower_text = text.lower()
    starts = []
    for kw in ("resumen", "introducción"):
        idx = lower_text.find(kw)
        if idx != -1:
            starts.append(idx)
    if starts:
        start_pos = min(starts)
        return text[start_pos:start_pos + 2000]
    return " ".join(text.split()[:2000])

# ---------- Estructura de carpetas y guardado ----------

def get_existing_categories(output_folder: str) -> dict:
    categories_dict = {}
    if os.path.exists(output_folder):
        for cat in os.listdir(output_folder):
            cat_path = os.path.join(output_folder, cat)
            if os.path.isdir(cat_path):
                subcats = [s for s in os.listdir(cat_path)
                           if os.path.isdir(os.path.join(cat_path, s))]
                categories_dict[cat] = subcats
    return categories_dict

def save_to_folder(text: str, base_folder_path: str, filename: str,
                   main_category: str, sub_category: str) -> str:
    os.makedirs(base_folder_path, exist_ok=True)
    category_folder = os.path.join(base_folder_path, main_category)
    subcategory_folder = os.path.join(category_folder, sub_category)
    os.makedirs(subcategory_folder, exist_ok=True)
    file_path = os.path.join(subcategory_folder, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    return file_path

def save_pdf_to_folder(pdf_file, base_folder_path, pdf_filename,
                       main_category, sub_category):
    os.makedirs(base_folder_path, exist_ok=True)
    category_folder = os.path.join(base_folder_path, main_category)
    subcategory_folder = os.path.join(category_folder, sub_category)
    os.makedirs(subcategory_folder, exist_ok=True)
    pdf_path = os.path.join(subcategory_folder, pdf_filename)
    # escribe bytes robustamente
    raw = pdf_file.getvalue() if hasattr(pdf_file, "getvalue") else pdf_file.read()
    with open(pdf_path, "wb") as f:
        f.write(raw)
    return pdf_path

# ---------- Paginado, resúmenes y utilidades ----------

def extract_pages_from_text(text: str):
    pattern = r'--- Página\s+(\d+)\s+---'
    parts = re.split(pattern, text)
    pages = []
    for i in range(1, len(parts), 2):
        try:
            page_num = int(parts[i])
        except ValueError:
            page_num = i
        page_text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        pages.append((page_num, page_text))
    return pages

def hierarchical_summary(pages, max_words=20000, chunk_size=5):
    """
    Genera un resumen jerárquico. Si el doc es corto, devuelve
    mini-resúmenes por página; si es largo, resume por bloques.
    """
    from llm import generate_short_summary_with_lmstudio, summarize_chunk_with_lmstudio

    if not pages:
        return []

    pages = [(pnum, ptext) for pnum, ptext in pages if ptext.strip()]
    if not pages:
        return []

    total_text = "\n".join(p for _, p in pages)
    if count_words(total_text) <= max_words:
        final_blocks = []
        for pnum, ptext in pages:
            if not ptext.strip():
                continue
            summary_data = generate_short_summary_with_lmstudio(ptext, "Documento", pnum)
            final_blocks.append({
                "page": pnum,
                "small_summary": summary_data.get("short_summary", "").strip()
            })
        return final_blocks

    # documento largo: divide en chunks
    chunks = [pages[i:i + chunk_size] for i in range(0, len(pages), chunk_size)]
    chunk_summaries = []
    for chunk in chunks:
        if not chunk:
            continue
        chunk_data = summarize_chunk_with_lmstudio(chunk) or {}
        if "summary" in chunk_data and chunk_data["summary"].strip():
            start_page, end_page = chunk[0][0], chunk[-1][0]
            chunk_summaries.append({
                "start_page": start_page,
                "end_page": end_page,
                "summary": chunk_data.get("summary", "").strip()
            })

    if not chunk_summaries:
        # fallback: por página
        final_blocks = []
        for pnum, ptext in pages:
            if not ptext.strip():
                continue
            summary_data = generate_short_summary_with_lmstudio(ptext, "Documento", pnum)
            if summary_data.get("short_summary", "").strip():
                final_blocks.append({
                    "page": pnum,
                    "small_summary": summary_data.get("short_summary", "").strip()
                })
        return final_blocks

    return chunk_summaries

def save_hierarchical_summary(final_data, output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=4, ensure_ascii=False)

def load_hierarchical_summary(output_path: str):
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def find_block_for_page(blocks, page_number: int):
    for block in blocks:
        if block.get("page") == page_number:
            return block
    return None

def get_pages_text_by_numbers(full_text: str, pages_list: list) -> str:
    pages = extract_pages_from_text(full_text)
    selected_texts = []
    for pnum, ptext in pages:
        if pnum in pages_list:
            selected_texts.append(f"--- Página {pnum} ---\n{ptext}")
    return "\n".join(selected_texts)
