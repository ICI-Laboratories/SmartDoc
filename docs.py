#docs.py
import fitz
import easyocr
from PIL import Image
import io
import os
import re
import json
import torch
torch.classes.__path__ = [os.path.join(torch.__path__[0], torch.classes.__file__)] 


reader = easyocr.Reader(['en', 'es'], gpu=True)

def extract_text_with_easyocr(pdf_file) -> str:
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
    text = text.strip()
    return len(text) > 50 and not re.match(r'^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,4}$', text)

def count_words(text: str) -> int:
    return len(text.split())

def get_classification_snippet(text: str) -> str:
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
    os.makedirs(base_folder_path, exist_ok=True)
    category_folder = os.path.join(base_folder_path, main_category)
    os.makedirs(category_folder, exist_ok=True)
    subcategory_folder = os.path.join(category_folder, sub_category)
    os.makedirs(subcategory_folder, exist_ok=True)
    file_path = os.path.join(subcategory_folder, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    return file_path

def save_pdf_to_folder(pdf_file, base_folder_path, pdf_filename, main_category, sub_category):
    os.makedirs(base_folder_path, exist_ok=True)
    category_folder = os.path.join(base_folder_path, main_category)
    os.makedirs(category_folder, exist_ok=True)
    subcategory_folder = os.path.join(category_folder, sub_category)
    os.makedirs(subcategory_folder, exist_ok=True)
    pdf_path = os.path.join(subcategory_folder, pdf_filename)
    with open(pdf_path, "wb") as f:
        f.write(pdf_file.getvalue())
    return pdf_path

def extract_pages_from_text(text: str):
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
    Genera un resumen jerárquico de las páginas de un documento.
    
    Args:
        pages: Lista de tuplas (número_página, texto_página)
        max_words: Número máximo de palabras para procesar directamente
        chunk_size: Tamaño de los bloques para resumir
        
    Returns:
        List: Lista de bloques con resúmenes
    """
    from llm import generate_short_summary_with_lmstudio, summarize_chunk_with_lmstudio
    
    # Verificar que tengamos páginas para procesar
    if not pages:
        return []
        
    # Filtrar páginas vacías
    pages = [(pnum, ptext) for pnum, ptext in pages if ptext.strip()]
    
    if not pages:
        return []
    
    total_text = "\n".join([p[1] for p in pages])
    
    # Si el documento es corto, procesar página por página
    if count_words(total_text) <= max_words:
        final_blocks = []
        for pnum, ptext in pages:
            # Verificar que la página tenga contenido
            if not ptext.strip():
                continue
                
            # Generar un pequeño resumen para cada página
            doc_name = "Documento"  # Puedes personalizar esto si tienes el nombre del documento
            summary_data = generate_short_summary_with_lmstudio(ptext, doc_name, pnum)
            
            final_blocks.append({
                "page": pnum,
                "small_summary": summary_data.get("short_summary", "").strip()
            })
        return final_blocks
    
    # Para documentos largos, dividir en bloques y resumir cada bloque
    chunks = [pages[i:i+chunk_size] for i in range(0, len(pages), chunk_size)]
    chunk_summaries = []
    
    for chunk in chunks:
        # Verificar que el chunk tenga contenido
        if not chunk:
            continue
            
        # Resumir el bloque de páginas
        chunk_data = summarize_chunk_with_lmstudio(chunk)
        
        # Verificar que obtuvimos un resumen
        if "summary" in chunk_data and chunk_data["summary"].strip():
            # Guardar el número de página inicial del bloque y su resumen
            start_page = chunk[0][0]
            end_page = chunk[-1][0]
            chunk_summaries.append({
                "start_page": start_page,
                "end_page": end_page,
                "summary": chunk_data.get("summary", "").strip()
            })
    
    # Si no se pudo resumir por bloques, intentar un enfoque página por página
    if not chunk_summaries:
        final_blocks = []
        for pnum, ptext in pages:
            if not ptext.strip():
                continue
                
            summary_data = generate_short_summary_with_lmstudio(ptext, "Documento", pnum)
            
            if "short_summary" in summary_data and summary_data["short_summary"].strip():
                final_blocks.append({
                    "page": pnum,
                    "small_summary": summary_data.get("short_summary", "").strip()
                })
        return final_blocks
    
    # Si tenemos resúmenes de bloques, devolver esos
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
    """
    Busca y devuelve el bloque cuyo atributo 'page' coincide con el número de página.
    """
    for block in blocks:
        if block["page"] == page_number:
            return block
    return None


def get_pages_text_by_numbers(full_text: str, pages_list: list) -> str:
    """
    Dado el texto completo (con las marcas de páginas) y una lista de números de página,
    extrae y retorna el contenido concatenado de aquellas páginas.
    
    Se basa en la función 'extract_pages_from_text'.
    """
    pages = extract_pages_from_text(full_text)
    selected_texts = []
    for pnum, ptext in pages:
        if pnum in pages_list:
            selected_texts.append(f"--- Página {pnum} ---\n{ptext}")
    return "\n".join(selected_texts)
