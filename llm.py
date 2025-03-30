import requests
import json
import streamlit as st
from docs import get_classification_snippet, get_existing_categories

from typing import Tuple

def classify_text_with_lmstudio(text: str, output_folder: str) -> Tuple[str, str]:
    """
    Clasifica el texto en 'main_category' y 'sub_category' usando LM Studio.
    """
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
        response = requests.post("http://localhost:1234/v1/chat/completions", headers=headers, json=payload)
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
            st.error(f"Error en la solicitud a LM Studio: {response.status_code}")
            return "Sin_Clasificar", "Sin_Subcategoria"
    except Exception as e:
        st.error(f"Error al conectar con LM Studio: {e}")
        return "Sin_Clasificar", "Sin_Subcategoria"


def summarize_chunk_with_lmstudio(pages):
    """
    Resume un chunk de páginas utilizando LM Studio.
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

    try:
        response = requests.post("http://localhost:1234/v1/chat/completions", headers=headers, json=payload)
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
                "summary": "Error en la solicitud al LLM."
            }
    except Exception as e:
        return {
            "start_page": start_page,
            "end_page": end_page,
            "summary": f"Error al conectar con LM Studio: {e}"
        }


def chat_with_context(context: str, question: str) -> str:
    """
    Permite chatear con el contenido resumido utilizando LM Studio.
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
        response = requests.post("http://localhost:1234/v1/chat/completions", headers=headers, json=payload)
        if response.status_code == 200:
            answer = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return answer
        else:
            return f"Error en la solicitud a LM Studio: {response.status_code}"
    except Exception as e:
        return f"Error al conectar con LM Studio: {e}"
