import streamlit as st
import fitz # pip install pymupdf
import easyocr
from PIL import Image
import io
import os
import re
import requests
import json

st.set_page_config(page_title="Procesador, Clasificador y Chateador de PDFs", layout="wide")
reader = easyocr.Reader(['en', 'es'], gpu=True)

# -------------------- FUNCIONES DE OCR --------------------
def extract_text_with_easyocr(pdf_file):
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

def is_valid_text(text):
    text = text.strip()
    return len(text) > 50 and not re.match(r'^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,4}$', text)

def count_words(text):
    return len(text.split())

def get_classification_snippet(text):
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

def get_existing_categories(output_folder):
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

def classify_text_with_lmstudio(text, output_folder):
    categories_dict = get_existing_categories(output_folder)
    existing_structure = json.dumps(categories_dict, indent=4)
    snippet = get_classification_snippet(text)

    prompt_instructions = (
        "Analiza el texto y categorízalo en 'main_category' y 'sub_category'. "
        "Si es posible, utiliza alguna de las categorías o subcategorías existentes mostradas a continuación. "
        "De lo contrario, crea una nueva categoría descriptiva. "
        "No uses nombres numéricos ni muy cortos.\n\n"
        f"Categorías existentes:\n{existing_structure}\n\n"
        f"Texto:\n{snippet}\n\n"
    )

    headers = {'Content-Type': 'application/json'}
    payload = {
        "model": "llama-3.2-3b-instruct",
        "messages": [
            {"role": "system", "content": "Eres un asistente que siempre responde con datos en formato JSON."},
            {"role": "user", "content": prompt_instructions}
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "classification_response",
                "strict": "true",
                "schema": {
                    "type": "object",
                    "properties": {
                        "main_category": {"type": "string"},
                        "sub_category": {"type": "string"}
                    },
                    "required": ["main_category", "sub_category"]
                }
            }
        },
        "temperature": 0.7,
        "max_tokens": 100,
        "stream": False
    }

    try:
        response = requests.post("http://localhost:8080/lmstudio", headers=headers, json=payload)
        
        if response.status_code == 200:
            generated_text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            try:
                data = json.loads(generated_text)
                main_category = data.get("main_category", "Sin_Clasificar").strip()
                sub_category = data.get("sub_category", "Sin_Subcategoria").strip()

                # Validar
                if main_category.isnumeric() or len(main_category) < 3:
                    main_category = "Nueva_Categoria_Descriptiva"
                if sub_category.isnumeric() or len(sub_category) < 3:
                    sub_category = "Nueva_Subcategoria_Descriptiva"

                return main_category, sub_category
            except json.JSONDecodeError:
                st.error("No se pudo decodificar el JSON de la respuesta del LLM.")
                return "Sin_Clasificar", "Sin_Subcategoria"
        else:
            st.error(f"Error en la solicitud a LM Studio: {response.status_code}")
            return "Sin_Clasificar", "Sin_Subcategoria"
    except Exception as e:
        st.error(f"Error al intentar conectar con LM Studio: {e}")
        return "Sin_Clasificar", "Sin_Subcategoria"

def save_to_folder(text, base_folder_path, filename, main_category, sub_category):
    os.makedirs(base_folder_path, exist_ok=True)
    category_folder = os.path.join(base_folder_path, main_category)
    os.makedirs(category_folder, exist_ok=True)
    subcategory_folder = os.path.join(category_folder, sub_category)
    os.makedirs(subcategory_folder, exist_ok=True)
    
    file_path = os.path.join(subcategory_folder, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    return file_path

# -------------------- FUNCIONES DE RESUMEN JERÁRQUICO --------------------

def extract_pages_from_text(text):
    pattern = r'--- Página\s+(\d+)\s+---'
    parts = re.split(pattern, text)
    pages = []
    for i in range(1, len(parts), 2):
        page_num = int(parts[i])
        page_text = parts[i+1].strip() if i+1 < len(parts) else ""
        pages.append((page_num, page_text))
    return pages

def summarize_chunk_with_lmstudio(pages):
    if not pages:
        return None
    
    start_page = pages[0][0]
    end_page = pages[-1][0]

    combined_text = ""
    for pnum, ptext in pages:
        combined_text += f"--- Página {pnum} ---\n{ptext}\n"

    prompt = (
        f"Estas son las páginas {start_page} a {end_page} de un conjunto de artículos. "
        "Por favor, elabora un resumen conciso en formato JSON que contenga los puntos más importantes, "
        "manteniendo la referencia a las páginas incluidas.\n"
        "Formato esperado:\n\n"
        "{\n"
        "   \"start_page\": <int>,\n"
        "   \"end_page\": <int>,\n"
        "   \"summary\": \"Resumen...\"\n"
        "}\n\n"
        f"{combined_text}\n\n"
        "Crea el resumen ahora:"
    )

    headers = {'Content-Type': 'application/json'}
    payload = {
        "model": "llama-3.2-3b-instruct",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "chunk_summary",
                "strict": "true",
                "schema": {
                    "type": "object",
                    "properties": {
                        "start_page": {"type": "integer"},
                        "end_page": {"type": "integer"},
                        "summary": {"type": "string"}
                    },
                    "required": ["start_page", "end_page", "summary"]
                }
            }
        },
        "temperature": 0.7,
        "max_tokens": 300,
        "stream": False
    }

    response = requests.post("http://localhost:1234/v1/chat/completions", headers=headers, json=payload)
    if response.status_code == 200:
        generated_text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        try:
            data = json.loads(generated_text)
            return data
        except:
            return {
                "start_page": start_page,
                "end_page": end_page,
                "summary": "No se pudo obtener un resumen estructurado."
            }
    else:
        return {
            "start_page": start_page,
            "end_page": end_page,
            "summary": "Error en la solicitud al LLM."
        }

def hierarchical_summary(pages, max_words=20000, chunk_size=5):
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

    # Si supera el límite, dividimos en chunks
    chunks = [pages[i:i+chunk_size] for i in range(0, len(pages), chunk_size)]

    chunk_summaries = []
    for chunk_pages in chunks:
        chunk_data = summarize_chunk_with_lmstudio(chunk_pages)
        chunk_summaries.append(chunk_data)

    # Convertir summaries a páginas virtuales
    summarized_pages = []
    for i, ch in enumerate(chunk_summaries):
        summarized_pages.append((i+1, ch["summary"]))

    return hierarchical_summary(summarized_pages, max_words=max_words, chunk_size=chunk_size)

def save_hierarchical_summary(final_data, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=4, ensure_ascii=False)

def load_hierarchical_summary(output_path):
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def chat_with_context(context, question):
    prompt = (
        "A continuación tienes un conjunto de páginas (resumidas si fue necesario). "
        "Basándote en el contenido, responde la siguiente pregunta. "
        "Si no encuentras la información, indica que no está disponible.\n\n"
        f"Contexto:\n{context}\n\n"
        f"Pregunta: {question}\n"
        "Respuesta:"
    )

    headers = {'Content-Type': 'application/json'}
    payload = {
        "messages": [{"role": "user", "content": prompt}]
    }

    response = requests.post("http://localhost:1234/v1/chat/completions", headers=headers, json=payload)
    if response.status_code == 200:
        answer = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return answer
    else:
        return f"Error en la solicitud a LM Studio: {response.status_code}"

def find_block_for_page(final_blocks, page_number):
    for block in final_blocks:
        if block["start_page"] <= page_number <= block["end_page"]:
            return block
    return None

# -------------------- INTERFAZ STREAMLIT --------------------

if "final_data" not in st.session_state:
    st.session_state["final_data"] = None

menu = st.sidebar.selectbox("Selecciona una opción", ["Cargar y Transcribir PDFs", "Revisar Archivos Guardados", "Chatear con Artículos"])
output_folder = st.text_input("Elige la carpeta de destino para los archivos extraídos:", value="./output_texts")

if menu == "Cargar y Transcribir PDFs":
    st.title("Cargar y Transcribir PDFs")
    st.markdown("Sube documentos PDF para extraer y clasificar el texto.")

    uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

    if uploaded_files:
        for uploaded_file in uploaded_files:
            st.write(f"\n### Procesando archivo: {uploaded_file.name}")
            
            try:
                with st.spinner("Extrayendo texto..."):
                    extracted_text = extract_text_with_easyocr(uploaded_file)
                    if not extracted_text.strip():
                        st.warning(f"No se detectó texto significativo en {uploaded_file.name}.")
                        continue
                    
                    word_count = count_words(extracted_text)
                    st.success(f"Texto extraído con {word_count} palabras.")

                # Clasificar el texto
                with st.spinner("Clasificando el texto..."):
                    main_cat, sub_cat = classify_text_with_lmstudio(extracted_text, output_folder)
                    st.info(f"Clasificado como: {main_cat} / {sub_cat}")

                # Guardar en la carpeta correspondiente
                save_path = save_to_folder(extracted_text, output_folder, uploaded_file.name.replace(".pdf", ".txt"), main_cat, sub_cat)
                st.info(f"Texto guardado en: `{save_path}`")
                
                st.download_button(
                    label="Descargar texto extraído",
                    data=extracted_text,
                    file_name=os.path.basename(save_path),
                    mime="text/plain"
                )

            except Exception as e:
                st.error(f"Error al procesar {uploaded_file.name}: {e}")

elif menu == "Revisar Archivos Guardados":
    st.title("Revisar Archivos Guardados")
    st.markdown("Explora los archivos de texto extraídos y clasificados.")

    if os.path.exists(output_folder):
        categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
        if categories:
            selected_category = st.selectbox("Selecciona una categoría:", [""] + categories)
            if selected_category:
                subcategories = [d for d in os.listdir(os.path.join(output_folder, selected_category)) 
                                 if os.path.isdir(os.path.join(output_folder, selected_category, d))]
                selected_subcategory = st.selectbox("Selecciona una subcategoría:", [""] + subcategories)
                if selected_subcategory:
                    files = [f for f in os.listdir(os.path.join(output_folder, selected_category, selected_subcategory)) if f.endswith(".txt")]
                    if files:
                        selected_file = st.selectbox("Selecciona un archivo para revisar:", files)
                        if selected_file:
                            with open(os.path.join(output_folder, selected_category, selected_subcategory, selected_file), "r", encoding="utf-8") as f:
                                content = f.read()
                            st.text_area("Contenido del archivo:", content, height=400)
                    else:
                        st.warning("No hay archivos en esta subcategoría.")
        else:
            st.warning("No hay categorías creadas aún.")
    else:
        st.warning("La carpeta seleccionada no existe.")

elif menu == "Chatear con Artículos":
    st.title("Chatear con Artículos")
    st.markdown("Selecciona uno o varios artículos ya clasificados y realiza una pregunta. Generará un resumen jerárquico y lo guardará en un JSON con un título.")

    if os.path.exists(output_folder):
        categories = [d for d in os.listdir(output_folder) if os.path.isdir(os.path.join(output_folder, d))]
        if categories:
            selected_category = st.selectbox("Selecciona una categoría:", [""] + categories)
            if selected_category:
                subcategories = [d for d in os.listdir(os.path.join(output_folder, selected_category)) 
                                 if os.path.isdir(os.path.join(output_folder, selected_category, d))]
                selected_subcategory = st.selectbox("Selecciona una subcategoría:", [""] + subcategories)
                if selected_subcategory:
                    files = [f for f in os.listdir(os.path.join(output_folder, selected_category, selected_subcategory)) if f.endswith(".txt")]
                    selected_files = st.multiselect("Selecciona uno o varios archivos:", files)
                    if selected_files:
                        question = st.text_input("Escribe tu pregunta:")
                        summary_file = st.text_input("Nombre del archivo de resumen (JSON):", value="hierarchical_summary.json")

                        # Generar un título para el artículo
                        if len(selected_files) == 1:
                            article_title = selected_files[0].replace(".txt", "")
                        else:
                            article_title = " & ".join([f.replace(".txt", "") for f in selected_files])

                        summary_path = os.path.join(output_folder, selected_category, selected_subcategory, summary_file)
                        
                        # Botón para generar o usar resumen
                        if st.button("Generar/Usar Resumen"):
                            final_data = load_hierarchical_summary(summary_path)
                            if final_data is None:
                                # Cargar texto
                                texts = []
                                for sf in selected_files:
                                    with open(os.path.join(output_folder, selected_category, selected_subcategory, sf), "r", encoding="utf-8") as f:
                                        texts.append(f.read())
                                # Generar resumen
                                all_pages = []
                                for t in texts:
                                    pages = extract_pages_from_text(t)
                                    all_pages.extend(pages)

                                final_blocks = hierarchical_summary(all_pages, max_words=20000, chunk_size=5)
                                final_data = {
                                    "title": article_title,
                                    "blocks": final_blocks
                                }
                                save_hierarchical_summary(final_data, summary_path)
                                st.success(f"Resumen jerárquico guardado en {summary_path}")
                            else:
                                st.info(f"Usando resumen jerárquico existente: {summary_path}")

                            st.session_state["final_data"] = final_data

                        # Si ya tenemos final_data en sesión y pregunta
                        if st.session_state["final_data"] is not None and question.strip():
                            final_data = st.session_state["final_data"]
                            mode = st.selectbox("¿Deseas usar todo el documento o una página específica?", ["Todo el documento", "Página específica"])

                            if mode == "Todo el documento":
                                if st.button("Preguntar (Todo el documento)"):
                                    context = ""
                                    for blk in final_data["blocks"]:
                                        context += f"--- Páginas {blk['start_page']}-{blk['end_page']} ---\n{blk['summary']}\n"
                                    answer = chat_with_context(context, question)
                                    st.markdown("**Respuesta del LLM:**")
                                    st.write(answer)

                            else:
                                # Página específica
                                page_number = st.number_input("Número de página:", min_value=1, value=1)
                                if st.button("Preguntar (Página específica)"):
                                    blk = find_block_for_page(final_data["blocks"], page_number)
                                    if blk:
                                        context = f"--- Páginas {blk['start_page']}-{blk['end_page']} ---\n{blk['summary']}"
                                        answer = chat_with_context(context, question)
                                        st.markdown("**Respuesta del LLM:**")
                                        st.write(answer)
                                    else:
                                        st.warning("No se encontró un bloque que contenga esa página.")
                    else:
                        st.info("Selecciona al menos un archivo.")
                else:
                    st.info("Selecciona una subcategoría.")
            else:
                st.info("Selecciona una categoría.")
        else:
            st.warning("No hay categorías creadas aún.")
    else:
        st.warning("La carpeta seleccionada no existe.")
