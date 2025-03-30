import fitz
import easyocr
from PIL import Image
import io
import os
import re
import json

# Inicializamos el lector de easyocr de forma global.
reader = easyocr.Reader(['en', 'es'], gpu=True)

def extract_text_with_easyocr(pdf_file) -> str:
    """
    Extrae el texto de un PDF utilizando PyMuPDF y, si es necesario, aplica OCR.
    """
    doc = fitz.open(stream=pdf_file.read(), filetype="pdf")
    extracted_text = ""
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        if len(text.strip()) >= 20:
            extracted_text += f"\n--- Página {page_num + 1} ---\n{text.strip()}\n"
        else:
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_bytes = io.BytesIO()
            img.save(img_bytes, format="PNG")
            img_bytes = img_bytes.getvalue()
            ocr_text = " ".join(reader.readtext(img_bytes, detail=0))
            if is_valid_text(ocr_text):
                extracted_text += f"\n--- Página {page_num + 1} ---\n{ocr_text.strip()}\n"
    return extracted_text

def is_valid_text(text: str) -> bool:
    """
    Valida que el texto extraído tenga longitud suficiente y no sea solo un número o una fecha.
    """
    text = text.strip()
    return len(text) > 50 and not re.match(r'^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,4}$', text)

def count_words(text: str) -> int:
    """
    Cuenta el número de palabras en un texto.
    """
    return len(text.split())

def get_classification_snippet(text: str) -> str:
    """
    Obtiene un fragmento representativo del texto para la clasificación.
    """
    lower_text = text.lower()
    resumen_idx = lower_text.find("resumen")
    intro_idx = lower_text.find("introducción")

    starts = []
    if resumen_idx != -1:
        starts.append(resumen_idx)
    if intro_idx != -1:
        starts.append(intro_idx)

    if starts:
        start_pos = min(starts)
        snippet = text[start_pos:start_pos+2000]
    else:
        words = text.split()
        snippet = " ".join(words[:2000])
    return snippet

def get_existing_categories(output_folder: str) -> dict:
    """
    Devuelve un diccionario con las categorías y subcategorías existentes en la carpeta de salida.
    """
    categories_dict = {}
    if os.path.exists(output_folder):
        for cat in os.listdir(output_folder):
            cat_path = os.path.join(output_folder, cat)
            if os.path.isdir(cat_path):
                subcats = []
                for subcat in os.listdir(cat_path):
                    subcat_path = os.path.join(cat_path, subcat)
                    if os.path.isdir(subcat_path):
                        subcats.append(subcat)
                categories_dict[cat] = subcats
    return categories_dict

def save_to_folder(text: str, base_folder_path: str, filename: str, main_category: str, sub_category: str) -> str:
    """
    Guarda el texto en una estructura de carpetas basada en la categoría y subcategoría.
    """
    os.makedirs(base_folder_path, exist_ok=True)
    category_folder = os.path.join(base_folder_path, main_category)
    os.makedirs(category_folder, exist_ok=True)
    subcategory_folder = os.path.join(category_folder, sub_category)
    os.makedirs(subcategory_folder, exist_ok=True)
    
    file_path = os.path.join(subcategory_folder, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    return file_path

def extract_pages_from_text(text: str):
    """
    Separa el texto en páginas basándose en un delimitador predefinido.
    """
    pattern = r'--- Página\s+(\d+)\s+---'
    parts = re.split(pattern, text)
    pages = []
    for i in range(1, len(parts), 2):
        try:
            page_num = int(parts[i])
        except ValueError:
            page_num = i
        page_text = parts[i+1].strip() if i+1 < len(parts) else ""
        pages.append((page_num, page_text))
    return pages

def hierarchical_summary(pages, max_words=20000, chunk_size=5):
    """
    Genera un resumen jerárquico a partir de las páginas.
    Si el contenido total es menor a 'max_words', se retorna cada página como bloque.
    En caso contrario, se divide en chunks y se resume cada uno recursivamente.
    """
    total_text = "\n".join([p[1] for p in pages])
    if count_words(total_text) <= max_words:
        final_blocks = []
        for pnum, ptext in pages:
            final_blocks.append({
                "start_page": pnum,
                "end_page": pnum,
                "summary": ptext
            })
        return final_blocks

    # Dividir en chunks
    chunks = [pages[i:i+chunk_size] for i in range(0, len(pages), chunk_size)]
    from llm import summarize_chunk_with_lmstudio
    chunk_summaries = []
    for chunk_pages in chunks:
        chunk_data = summarize_chunk_with_lmstudio(chunk_pages)
        chunk_summaries.append(chunk_data)
    # Convertir resúmenes en páginas virtuales y resumir de nuevo
    summarized_pages = []
    for i, ch in enumerate(chunk_summaries):
        summarized_pages.append((i+1, ch["summary"]))
    return hierarchical_summary(summarized_pages, max_words=max_words, chunk_size=chunk_size)

def save_hierarchical_summary(final_data, output_path: str) -> None:
    """
    Guarda el resumen jerárquico en un archivo JSON.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=4, ensure_ascii=False)

def load_hierarchical_summary(output_path: str):
    """
    Carga el resumen jerárquico desde un archivo JSON si existe.
    """
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def find_block_for_page(final_blocks, page_number: int):
    """
    Retorna el bloque de resumen que contiene la página indicada.
    """
    for block in final_blocks:
        if block["start_page"] <= page_number <= block["end_page"]:
            return block
    return None
