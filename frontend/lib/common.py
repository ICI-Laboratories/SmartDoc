# frontend/lib/common.py

import os
import getpass
import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / '.env')

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

# ===============================
# Configuración de la Aplicación
# ===============================
PROCESSOR_URL = os.getenv("SMARTDOC_PROCESSOR_URL", "http://127.0.0.1:8002")
LLM_URL = os.getenv("SMARTDOC_LLM_URL", "http://127.0.0.1:8001")

BASE_DIR = Path(os.getenv("SMARTDOC_BASE", Path.home() / "SmartDocData"))
USERNAME = getpass.getuser()
USER_FOLDER = BASE_DIR / USERNAME


@st.cache_resource
def get_http_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["POST", "GET"]),
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(
        {
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
        }
    )
    return s


@st.cache_data(ttl=60, show_spinner=False)
def get_available_summaries(user_folder: Path) -> dict:
    """
    Mapea 'Categoria/Subcategoria/NombreDoc' -> ruta absoluta del .summary.json (str).
    Cacheada 60s para evitar escaneos de FS en cada rerender.
    """
    if not user_folder.exists():
        return {}
    summary_files_map: dict[str, str] = {}
    for summary_path in user_folder.rglob("*.summary.json"):
        try:
            doc_name = summary_path.name.replace(".summary.json", "")
            key = f"{summary_path.parent.parent.name}/{summary_path.parent.name}/{doc_name}"
            summary_files_map[key] = str(summary_path)
        except IndexError:
            continue
    return summary_files_map


@st.cache_data(ttl=30, show_spinner=False)
def list_categories(user_folder: Path) -> list[str]:
    if not user_folder.exists():
        return []
    return sorted([d.name for d in user_folder.iterdir() if d.is_dir()])


@st.cache_data(ttl=30, show_spinner=False)
def list_subcategories(cat_path: Path) -> list[str]:
    if not cat_path.exists():
        return []
    return sorted([d.name for d in cat_path.iterdir() if d.is_dir()])


@st.cache_data(ttl=30, show_spinner=False)
def list_md_files(subcat_path: Path) -> list[str]:
    if not subcat_path.exists():
        return []
    return sorted([f.name for f in subcat_path.glob("*.md")])


@st.cache_data(max_entries=256, show_spinner=False)
def read_markdown_cached(path: str, mtime_ns: int) -> str:
    return Path(path).read_text(encoding="utf-8")


@st.cache_data(max_entries=256, show_spinner=False)
def read_json_cached(path: str, mtime_ns: int) -> dict:
    p = Path(path)
    try:
        import orjson
        return orjson.loads(p.read_bytes())
    except Exception:
        return json.loads(p.read_text(encoding="utf-8"))