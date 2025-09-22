import os
import getpass
import json
from pathlib import Path
import random
import string
import uuid

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / '.env')

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

API_GATEWAY_URL = "http://127.0.0.1:8000"
PROCESSOR_URL = API_GATEWAY_URL
LLM_URL = API_GATEWAY_URL

BASE_DIR = Path(os.getenv("SMARTREVIEW_BASE", Path.home() / "SmartReviewData"))
SERVER_USERNAME = getpass.getuser()


def get_session_id() -> str:
    if "session_id" in st.query_params:
        session_id = st.query_params["session_id"]
        st.session_state['session_id'] = session_id
        return session_id

    if 'session_id' in st.session_state:
        st.query_params["session_id"] = st.session_state['session_id']
        return st.session_state['session_id']

    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    new_session_id = f"usuario_web_{random_suffix}"
    
    st.session_state['session_id'] = new_session_id
    
    st.query_params["session_id"] = new_session_id
    
    return new_session_id


def get_current_user_folder() -> Path:
    session_user_id = get_session_id()
    return BASE_DIR / session_user_id


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

    session_user_id = get_session_id()
    s.headers.update(
        {
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
            "X-User-ID": session_user_id
        }
    )
    return s


@st.cache_data(ttl=60, show_spinner=False)
def get_available_summaries() -> dict:
    user_folder = get_current_user_folder()
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
def list_categories() -> list[str]:
    user_folder = get_current_user_folder()
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