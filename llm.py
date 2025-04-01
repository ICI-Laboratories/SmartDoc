import requests
import json
import streamlit as st
from docs import get_classification_snippet, get_existing_categories, get_pages_text_by_numbers
from typing import Tuple

INFERENCE_SERVER_URL = "http://localhost:1234/v1/chat/completions"

def classify_text_with_lmstudio(text: str, output_folder: str) -> Tuple[str, str]:
    """
    Clasifica el texto en 'main_category' y 'sub_category' utilizando el servidor de inferencia.
    """
    categories_dict = get_existing_categories(output_folder)
    existing_structure = json.dumps(categories_dict, indent=4)
    snippet = get_classification_snippet(text)

    prompt_instructions = (
        "Analiza el texto y categorízalo en 'main_category' y 'sub_category'. "
        "Utiliza las categorías existentes si es posible, o crea una nueva categoría descriptiva. "
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
        response = requests.post(INFERENCE_SERVER_URL, headers=headers, json=payload)
        if response.status_code == 200:
            generated_text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            try:
                data = json.loads(generated_text)
                main_category = data.get("main_category", "Sin_Clasificar").strip()
                sub_category = data.get("sub_category", "Sin_Subcategoria").strip()
                if main_category.isnumeric() or len(main_category) < 3:
                    main_category = "Nueva_Categoria_Descriptiva"
                if sub_category.isnumeric() or len(sub_category) < 3:
                    sub_category = "Nueva_Subcategoria_Descriptiva"
                return main_category, sub_category
            except json.JSONDecodeError:
                st.error("No se pudo decodificar el JSON de la respuesta del LLM.")
                return "Sin_Clasificar", "Sin_Subcategoria"
        else:
            st.error(f"Error en la solicitud a Inference Server: {response.status_code} => {response.text}")
            return "Sin_Clasificar", "Sin_Subcategoria"
    except Exception as e:
        st.error(f"Error al conectar con el servidor de inferencia: {e}")
        return "Sin_Clasificar", "Sin_Subcategoria"

def summarize_chunk_with_lmstudio(pages):
    """
    Resume un chunk de páginas utilizando el servidor de inferencia.
    """
    if not pages:
        return None

    start_page = pages[0][0]
    end_page = pages[-1][0]
    combined_text = ""
    for pnum, ptext in pages:
        combined_text += f"--- Página {pnum} ---\n{ptext}\n"

    prompt = (
        f"Estas son las páginas {start_page} a {end_page} de un conjunto de artículos. "
        "Elabora un resumen conciso en formato JSON que contenga los puntos más importantes, "
        "manteniendo la referencia a las páginas.\n"
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

    try:
        response = requests.post(INFERENCE_SERVER_URL, headers=headers, json=payload)
        if response.status_code == 200:
            generated_text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            try:
                data = json.loads(generated_text)
                return data
            except json.JSONDecodeError:
                return {
                    "start_page": start_page,
                    "end_page": end_page,
                    "summary": "No se pudo obtener un resumen estructurado."
                }
        else:
            return {
                "start_page": start_page,
                "end_page": end_page,
                "summary": f"Error en la solicitud al servidor de inferencia: {response.status_code}"
            }
    except Exception as e:
        return {
            "start_page": start_page,
            "end_page": end_page,
            "summary": f"Error al conectar con el servidor de inferencia: {e}"
        }

def chat_with_context(context: str, question: str) -> str:
    """
    Chatea con el contenido resumido usando el servidor de inferencia.
    """
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

    try:
        response = requests.post(INFERENCE_SERVER_URL, headers=headers, json=payload)
        if response.status_code == 200:
            answer = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return answer
        else:
            return f"Error en la solicitud al servidor de inferencia: {response.status_code} => {response.text}"
    except Exception as e:
        return f"Error al conectar con el servidor de inferencia: {e}"

def generate_short_summary_with_lmstudio(page_text: str, doc_name: str, page_num: int) -> dict:
    """
    Envía 'page_text' a la inferencia para obtener un resumen corto (10-20 palabras).
    """
    prompt = (
        f"Documento: {doc_name}, página {page_num}.\n"
        "Elabora un resumen ultra corto (10 a 20 palabras) que incluya:\n"
        "- Tema central\n"
        "- Acción o conclusión\n"
        "- Indica también la página y el documento.\n\n"
        "Texto de la página:\n"
        f"{page_text}\n\n"
        "Resumen corto:"
    )

    headers = {"Content-Type": "application/json"}
    payload = {
        "model": "llama-3.2-3b-instruct",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 100,
        "stream": False
    }

    try:
        response = requests.post(INFERENCE_SERVER_URL, headers=headers, json=payload)
        if response.status_code == 200:
            short_summary = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return {
                "doc_name": doc_name,
                "page_number": page_num,
                "short_summary": short_summary
            }
        else:
            return {
                "doc_name": doc_name,
                "page_number": page_num,
                "short_summary": f"Error {response.status_code}: No se pudo obtener resumen"
            }
    except Exception as e:
        return {
            "doc_name": doc_name,
            "page_number": page_num,
            "short_summary": f"Exception: {str(e)}"
        }


def get_relevant_pages_from_summary(summary: dict, question: str) -> dict:
    """
    Dado un resumen (con título y bloques) y una pregunta, 
    solicita al LLM que indique, en formato JSON, el documento y
    las páginas relevantes para responder la pregunta.
    
    El output esperado es:
    {
        "document": "<título>",
        "pages": [<número1>, <número2>, ...]
    }
    """
    prompt = (
        "A continuación se muestra el resumen de un documento:\n\n"
        f"{json.dumps(summary, indent=2)}\n\n"
        "Con base en el resumen, determina qué páginas contienen la información necesaria "
        "para responder la siguiente pregunta:\n\n"
        f"Pregunta: {question}\n\n"
        "Responde únicamente en el siguiente formato JSON, sin texto adicional:\n"
        "{\n"
        '  "document": "<título>",\n'
        '  "pages": [número1, número2, ...]\n'
        "}\n\n"
        "Asegúrate de que los números de página sean enteros."
    )
    
    headers = {'Content-Type': 'application/json'}
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "temperature": 0.7,
        "stream": False
    }
    
    try:
        response = requests.post(INFERENCE_SERVER_URL, headers=headers, json=payload)
        if response.status_code == 200:
            output = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            try:
                data = json.loads(output)
                # Validamos que 'pages' sea una lista de enteros.
                if "pages" in data and isinstance(data["pages"], list):
                    data["pages"] = [int(p) for p in data["pages"] if isinstance(p, int) or (isinstance(p, str) and p.isdigit())]
                return data
            except json.JSONDecodeError:
                st.error("No se pudo decodificar el JSON de la respuesta al obtener páginas relevantes.")
                return {}
        else:
            st.error(f"Error en la solicitud para obtener páginas relevantes: {response.status_code}")
            return {}
    except Exception as e:
        st.error(f"Error al conectar con el servidor de inferencia: {e}")
        return {}
