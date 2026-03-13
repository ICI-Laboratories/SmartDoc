import os
import sys
import glob
import json
import logging
import asyncio
import httpx
from pathlib import Path

# Config
PDF_DIR = r"C:\paper_citca_2026-1\corpus_data"
API_URL = "http://127.0.0.1:8002/process_document/"
STATE_FILE = "batch_ingest_state.json"
USERNAME = "walter_citca"
TIMEOUT = 300.0  # 5 minutos por documento
MAX_CONCURRENT = 1  # 1 es más seguro para VRAM y estabilidad

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_state(processed_list):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(processed_list), f, indent=2)

async def upload_document(client, pdf_path):
    filename = os.path.basename(pdf_path)
    try:
        with open(pdf_path, 'rb') as f:
            files = {'file': (filename, f, 'application/pdf')}
            data = {'username': USERNAME}
            
            logger.info(f"Subiendo documento: {filename}")
            response = await client.post(API_URL, data=data, files=files, timeout=TIMEOUT)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Exito: {filename} -> {result.get('category')}")
                return True
            elif response.status_code == 409:
                logger.warning(f"Duplicado o ya existe: {filename}")
                return True # Treat as processed
            else:
                logger.error(f"Error {response.status_code} for {filename}: {response.text}")
                return False
    except httpx.TimeoutException:
        logger.error(f"Timeout al procesar {filename}. El modelo de lenguaje podría estar saturado.")
        return False
    except Exception as e:
        logger.error(f"Fallo en la peticion para {filename}: {e}")
        return False

import subprocess
import time

def start_ollama():
    logger.info("Iniciando Ollama en segundo plano...")
    # Attempt to start Ollama silently
    try:
        # We start the process and don't wait for it
        
        # fix the linter error regarding CREATE_NO_WINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == "win32" else 0
        
        ollama_process = subprocess.Popen(
            ["ollama", "serve"], 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL,
            creationflags=flags
        )
        logger.info("Esperando 5 segundos a que Ollama inicie completamente...")
        time.sleep(5) # Give it time to bind to port
        return ollama_process
    except FileNotFoundError:
        logger.error("No se encontro el ejecutable 'ollama' en el PATH del sistema.")
        return None
    except Exception as e:
        logger.error(f"Error al iniciar Ollama: {e}")
        return None

def stop_ollama(process=None):
    if process:
        logger.info("Deteniendo proceso hijo de Ollama...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    
    # Also try to kill any dangling Ollama processes on Windows
    logger.info("Asegurando que Ollama se cierre completamente...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True)

async def main():
    if not os.path.exists(PDF_DIR):
        logger.error(f"El directorio {PDF_DIR} no existe.")
        sys.exit(1)
        
    logger.info("Buscando archivos PDF...")
    all_pdfs = glob.glob(os.path.join(PDF_DIR, "**", "*.pdf"), recursive=True)
    logger.info(f"Se encontraron {len(all_pdfs)} PDFs.")
    
    processed = load_state()
    logger.info(f"Ya procesados en corridas anteriores: {len(processed)} PDFs.")
    
    pending_pdfs = [p for p in all_pdfs if p not in processed]
    logger.info(f"Pendientes por procesar: {len(pending_pdfs)} PDFs.")
    
    if not pending_pdfs:
        logger.info("Todos los documentos ya fueron procesados. Saliendo.")
        return

    ollama_proc = start_ollama()

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for i, pdf_path in enumerate(pending_pdfs):
                async with sem:
                    success = await upload_document(client, pdf_path)
                    if success:
                        processed.add(pdf_path)
                        save_state(processed)
                    else:
                        logger.warning(f"No se pudo procesar {pdf_path}. Se reintentara en la proxima corrida.")
                        # Sleep a bit to let the local LLM recover if there was an error
                        await asyncio.sleep(5)
                
                # Print progress every 10 items
                if (i + 1) % 10 == 0:
                    logger.info(f"Progreso: {len(processed)} / {len(all_pdfs)} ({(len(processed)/len(all_pdfs))*100:.2f}%)")
    finally:
        stop_ollama(ollama_proc)

if __name__ == "__main__":
    asyncio.run(main())
