# document_processor/api.py
import os
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from typing import List

import logic # La lógica de docs.py

app = FastAPI(
    title="Document Processor Service",
    description="Un microservicio para procesar y almacenar documentos."
)

# Configuración de URLs de servicios y carpetas base
LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL", "http://127.0.0.1:8001")
BASE_DIR = os.getenv("SMARTDOC_BASE", os.path.join(os.path.expanduser("~"), "SmartDocData"))

@app.post("/process_document/", summary="Procesa un documento PDF")
async def process_document(
    username: str = Form(...),
    file: UploadFile = File(...)
):
    """
    Endpoint principal que orquesta el procesamiento de un archivo:
    1. Extrae texto (con OCR si es necesario).
    2. Llama al LLM Service para clasificar.
    3. Guarda el .txt y el .pdf original.
    4. Genera y guarda el resumen jerárquico.
    """
    output_folder = os.path.join(BASE_DIR, username)
    os.makedirs(output_folder, exist_ok=True)

    try:
        # 1. Extracción de Texto
        contents = await file.read()
        extracted_text = logic.extract_text_with_easyocr(contents)
        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail="No se pudo extraer texto del documento.")

        # 2. Clasificación (llamando al llm_service)
        existing_categories = logic.get_existing_categories(output_folder)
        response = requests.post(
            f"{LLM_SERVICE_URL}/classify",
            json={"text": extracted_text, "categories": existing_categories}
        )
        response.raise_for_status() # Lanza un error si la petición falla
        classification = response.json()
        main_cat = classification.get("main_category", "Sin_Clasificar")
        sub_cat = classification.get("sub_category", "Sin_Subcategoria")

        # 3. Guardado de .txt y .pdf
        txt_path = logic.save_to_folder(
            extracted_text, output_folder, file.filename.replace(".pdf", ".txt"), main_cat, sub_cat
        )
        # Para guardar el PDF, necesitamos "rebobinar" el archivo en memoria
        await file.seek(0)
        logic.save_pdf_to_folder(
            file.file, output_folder, file.filename, main_cat, sub_cat
        )

        # 4. Resumen Jerárquico (llamando al llm_service)
        pages = logic.extract_pages_from_text(extracted_text)
        
        # Aquí la lógica de hierarchical_summary necesita ser adaptada para llamar a la API
        # en lugar de a la función directamente.
        # Por simplicidad, este ejemplo muestra el concepto. 
        # Deberías refactorizar hierarchical_summary en logic.py para que acepte la URL del servicio LLM.
        
        # summary_blocks = logic.hierarchical_summary(pages, llm_service_url=LLM_SERVICE_URL)
        # ... (guardar resumen) ...

        return {
            "message": f"Documento '{file.filename}' procesado y guardado.",
            "filename": file.filename,
            "category": f"{main_cat}/{sub_cat}",
            "text_path": txt_path
        }

    except requests.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Error al comunicar con LLM Service: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno al procesar el archivo: {e}")