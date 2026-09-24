"""Opt-in page OCR through the gateway; CPU extraction remains the default."""
import base64
import os

import httpx

from .gateway import gateway_headers, gateway_url
from .ocr_quality import preserve_native, tesseract_lines, validate_recognition, verify_and_preserve


def ocr_model():
    # Never inherit the chat model: the OCR route must be explicitly provisioned.
    return os.environ.get('SARA_OCR_MODEL', '').strip()


def ocr_profile():
    profile = os.environ.get('SARA_OCR_PROFILE', 'glm-ocr').strip()
    if profile not in {'glm-ocr', 'lightonocr-2'}:
        raise ValueError('SARA_OCR_PROFILE debe ser glm-ocr o lightonocr-2.')
    return profile


def extract_page(page):
    import pymupdf

    model = ocr_model()
    if not model:
        raise ValueError('El modelo OCR del gateway no está configurado.')
    profile = ocr_profile()
    endpoint, headers = gateway_url('chat/completions'), gateway_headers()
    max_side = int(os.environ.get('SARA_OCR_MAX_SIDE', '1600'))
    if not 256 <= max_side <= 2048:
        raise ValueError('SARA_OCR_MAX_SIDE debe estar entre 256 y 2048 píxeles.')
    if profile == 'lightonocr-2':
        max_side = min(max_side, 1540)
    else:
        max_side = min(max_side, 1600)
    if page.rect.is_empty or page.rect.is_infinite:
        raise ValueError('La página PDF no tiene dimensiones válidas.')
    dpi = 200 if profile == 'lightonocr-2' else 150
    scale = min(dpi / 72, max_side / max(page.rect.width, page.rect.height))
    # Render one bounded page at a time, without retaining images for the document.
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
    image = pix.tobytes('png')
    if len(image) > 8 * 1024 * 1024:
        raise ValueError('La imagen de la página supera el límite de OCR (8 MiB).')
    content = [{'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')}}]
    if profile == 'glm-ocr':
        content.insert(0, {'type': 'text', 'text': 'Text Recognition:'})
    try:
        response = httpx.post(endpoint, headers=headers, timeout=120, json={
            'model': model, 'stream': False, 'temperature': 0, 'max_tokens': 4096,
            'messages': [{'role': 'user', 'content': content}],
        })
        response.raise_for_status()
        choice = response.json()['choices'][0]
        text = choice['message']['content']
        if choice.get('finish_reason') != 'stop' or not isinstance(text, str) or not text.strip():
            raise ValueError('El OCR devolvió texto vacío, incompleto o truncado.')
        validate_recognition(text)
        if profile == 'glm-ocr':
            text = verify_and_preserve(text, pix.height, tesseract_lines(image), page.get_text(sort=True))
        else:
            text = preserve_native(text, page.get_text(sort=True))
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        # A failed page must fail the job, not produce a deceptively complete index.
        raise ValueError(f'No se pudo completar o verificar el OCR de la página {page.number + 1}. '
                         'La página requiere revisión; no se aceptó un resultado parcial.') from exc
    return text.strip()
