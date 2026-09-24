import json
import re
import requests
from pathlib import Path
from typing import Tuple, List, Dict, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from library_service.gateway import gateway_headers, gateway_url


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent.parent / '.env', env_file_encoding='utf-8', extra='ignore')
    model_name: str = Field(alias="SMARTREVIEW_MODEL", default="sara-main")
    request_timeout: float = Field(alias="SMARTREVIEW_LM_TIMEOUT", default=60.0)

settings = LLMSettings()

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


def _extract_json_from_text(text: str) -> Optional[dict]:
    """Try to extract JSON from text that might have markdown code blocks, thinking tags, or extra text."""
    if not text:
        return None

    # Remove thinking tags (gemma3n, qwen, etc.)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON in code blocks
    code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object in the text (greedy, finds largest match)
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    # Try finding JSON with nested objects more aggressively
    # Find the first { and last } and try to parse
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    return None


def call_llm(
    prompt: str,
    schema: Optional[dict] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> dict:
    payload = {
        "model": settings.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    if schema:
        # Add schema hint to the prompt for better results
        schema_hint = f"\n\nResponde SOLO con JSON válido siguiendo este esquema: {json.dumps(schema, ensure_ascii=False)}"
        payload["messages"][0]["content"] = prompt + schema_hint
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "smartdoc_response", "schema": schema},
        }

    try:
        response = requests.post(
            gateway_url("chat/completions"),
            headers=gateway_headers(),
            json=payload,
            timeout=settings.request_timeout,
            allow_redirects=False,
        )
        response.raise_for_status()
        if response.is_redirect:
            return {"error": "El gateway devolvió una redirección no permitida."}
    except (requests.exceptions.RequestException, ValueError):
        return {"error": "El gateway de inferencia no está disponible o no está configurado."}

    try:
        choice = response.json()['choices'][0]
        if choice.get('finish_reason') != 'stop':
            return {"error": "El gateway devolvió una respuesta incompleta."}
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            return {"error": "El gateway devolvió una respuesta vacía."}
        content = content.strip()
        print(f"\n{'='*60}\nLLM RAW RESPONSE:\n{content}\n{'='*60}\n")
        if schema:
            # Try robust JSON extraction
            result = _extract_json_from_text(content)
            if result:
                print(f"PARSED JSON: {result}")
                return result
            print(f"FAILED TO PARSE JSON from: {content[:500]}")
            return {"error": f"Could not parse JSON from response", "response_text": content[:500]}
        else:
            return {"content": content}
    except (json.JSONDecodeError, IndexError, KeyError, TypeError, AttributeError) as e:
        print(f"LLM RESPONSE ERROR: {e}\nRaw: {response.text[:500]}")
        return {"error": f"Invalid response format from LLM: {e}", "response_text": response.text[:500]}

import logging
_logger = logging.getLogger(__name__)

def classify_text_with_lmstudio(text: str, categories_dict: dict) -> Tuple[str, str]:
    snippet = get_classification_snippet(text)
    prompt = (
        "Clasifica este texto académico. Tienes dos tareas principales:\n"
        "1. Identificar el año de publicación o creación del documento en el texto. "
        "Si el texto menciona o se infiere que es entre 2019 y 2021, la categoría principal DEBE ser 'Pre-IA_2019-2021'. "
        "Si es entre 2023 y 2025 (o posterior), la categoría principal DEBE ser 'Post-IA_2023-2025'. "
        "Si es de otros años u otra temática principal, usa tu mejor juicio o crea una temática.\n"
        "2. Asignar una subcategoría temática específica de qué trata el paper.\n\n"
        "No uses nombres puramente numéricos ni cortos.\n\n"
        f"Categorías existentes:\n{json.dumps(categories_dict, indent=2)}\n\n"
        f"Texto a clasificar:\n{snippet}"
    )
    schema = {
        "type": "object",
        "properties": {
            "main_category": {"type": "string", "description": "La categoría principal"},
            "sub_category": {"type": "string", "description": "La subcategoría específica"}
        },
        "required": ["main_category", "sub_category"]
    }
    result = call_llm(prompt, schema=schema, temperature=0.1)  # Low temp for consistent classification
    _logger.info(f"Classification LLM result: {result}")

    # Check for error
    if "error" in result:
        _logger.error(f"Classification failed: {result.get('error')}")

    main_category = result.get("main_category", "Sin_Clasificar").replace(" ", "_")
    sub_category = result.get("sub_category", "Sin_Subcategoria").replace(" ", "_")
    return main_category, sub_category

def summarize_chunk_with_lmstudio(pages: List[Tuple[int, str]]) -> Dict:
    if not pages:
        return {"error": "No pages provided to summarize."}
    
    start_page, end_page = pages[0][0], pages[-1][0]
    combined_text = "\n".join(ptext for _, ptext in pages)

    prompt = f"""
Eres un experto sintetizador de información. Analiza el texto de la página {start_page} de un documento.
Tu misión es generar un resumen telegráfico, denso y extremadamente conciso.

Reglas estrictas:
1.  **Máximo 30 palabras.** No excedas este límite bajo ninguna circunstancia.
2.  **Enfócate en la idea central.** Ignora detalles secundarios, ejemplos o introducciones. Ve directo al núcleo del argumento, hallazgo o propuesta.
3.  **Formato de párrafo único.** No uses listas ni saltos de línea.

Texto a resumir:
---
{combined_text}
---
"""
    schema = {
        "type": "object",
        "properties": {
            "start_page": {"type": "integer"},
            "end_page": {"type": "integer"},
            "summary": {"type": "string", "description": "El resumen ultra-corto (máx 30 palabras)."}
        },
        "required": ["start_page", "end_page", "summary"]
    }
    
    return call_llm(prompt, schema=schema, temperature=0.1, max_tokens=150)

def generate_short_summary_with_lmstudio(page_text: str, doc_name: str, page_num: int) -> Dict:
    prompt = (
        f"Genera un resumen muy breve (máx. 3 frases) para la página {page_num} del documento '{doc_name}'.\n\n"
        f"Texto:\n{page_text}"
    )
    schema = {
        "type": "object",
        "properties": {
            "page_num": {"type": "integer"},
            "short_summary": {"type": "string"}
        },
        "required": ["page_num", "short_summary"]
    }
    return call_llm(prompt, schema=schema)

def chat_with_context(context: str, question: str) -> str:
    prompt = (
        "Basándote únicamente en el siguiente contexto, responde a la pregunta del usuario de forma clara y concisa. "
        "Si la respuesta no se encuentra en el contexto, indica que vuelva a formular su pregunta.\n\n"
        f"## Contexto:\n{context}\n\n"
        f"## Pregunta: {question}\n\n"
        "## Respuesta:"
    )
    result = call_llm(prompt)
    return result.get("content", f"Error al generar la respuesta: {result.get('error', 'desconocido')}")
