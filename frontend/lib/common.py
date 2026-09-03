import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from lib.central_identity import (
    InvalidCentralSubject,
    canonical_subject,
    subject_directory,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv(Path(__file__).parent.parent.parent / ".env")


def get_default_data_dir() -> Path:
    """Return the platform-appropriate SmartReview data directory."""
    app_name = "SmartReview"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / app_name


API_GATEWAY_URL = os.getenv("SMARTREVIEW_API_GATEWAY_URL", "http://127.0.0.1:8043").rstrip("/")
PUBLIC_GATEWAY_URL = os.getenv("SMARTDOC_PUBLIC_GATEWAY_URL", "http://127.0.0.1:8043").rstrip("/")
FRONTEND_URL = os.getenv("SMARTDOC_FRONTEND_URL", "http://127.0.0.1:8501/")
_frontend_parts = urlsplit(FRONTEND_URL)
FRONTEND_ORIGIN = f"{_frontend_parts.scheme}://{_frontend_parts.netloc}"
SESSION_COOKIE_NAME = os.getenv("SMARTDOC_SESSION_COOKIE_NAME", "smartdoc_session")
CSRF_COOKIE_NAME = os.getenv("SMARTDOC_CSRF_COOKIE_NAME", "smartdoc_csrf")
AUTH_LOGIN_URL = f"{PUBLIC_GATEWAY_URL}/auth/login"
PROCESSOR_URL = API_GATEWAY_URL
LLM_URL = API_GATEWAY_URL

_env_base = os.getenv("SMARTREVIEW_BASE")
if _env_base:
    _base_path = Path(_env_base)
    if not _base_path.is_absolute():
        _base_path = Path(__file__).parent.parent.parent / _env_base
    BASE_DIR = _base_path
else:
    BASE_DIR = get_default_data_dir()
BASE_DIR.mkdir(parents=True, exist_ok=True)

_COMPONENT_PATH = Path(__file__).parent / "browser_session_component"
_auth_browser_component = components.declare_component(
    "smartdoc_auth_browser", path=str(_COMPONENT_PATH)
)


class CentralSessionUnavailable(RuntimeError):
    """The central identity service could not validate this browser session."""


def _browser_cookie(name: str) -> str | None:
    try:
        value = st.context.cookies.get(name)
    except (AttributeError, RuntimeError):
        return None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _canonical_subject(raw_subject: object) -> str:
    try:
        return canonical_subject(raw_subject)
    except InvalidCentralSubject as exc:
        raise CentralSessionUnavailable("auth_services devolvio un subject invalido") from exc


def _session_cookie_header() -> tuple[str, str] | None:
    session_cookie = _browser_cookie(SESSION_COOKIE_NAME)
    csrf_cookie = _browser_cookie(CSRF_COOKIE_NAME)
    if not session_cookie or not csrf_cookie:
        return None
    # Forward only SmartDoc's two cookies; never forward Streamlit or arbitrary cookies.
    return (
        f"{SESSION_COOKIE_NAME}={session_cookie}; {CSRF_COOKIE_NAME}={csrf_cookie}",
        csrf_cookie,
    )


def _show_login() -> None:
    st.warning("Inicia sesion con la cuenta central para usar SmartReview.")
    st.link_button("Iniciar sesion", AUTH_LOGIN_URL, use_container_width=True)
    st.stop()


def ensure_session_id() -> str:
    """Validate the HttpOnly BFF cookie and return the central UUID namespace."""
    for parameter in ("code", "state", "access_token", "refresh_token"):
        if parameter in st.query_params:
            del st.query_params[parameter]

    cookie_header = _session_cookie_header()
    if cookie_header is None:
        _show_login()

    try:
        response = requests.get(
            f"{API_GATEWAY_URL}/session/me",
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "Cookie": cookie_header[0],
            },
            timeout=(3, 8),
        )
    except requests.RequestException:
        st.error("No se pudo validar la sesion con el gateway de SmartDoc.")
        st.stop()

    if response.status_code == 401:
        _show_login()
    if response.status_code != 200:
        st.error("La identidad central no esta disponible. Intenta nuevamente mas tarde.")
        st.stop()
    try:
        subject = _canonical_subject(response.json().get("subject"))
    except (ValueError, requests.JSONDecodeError):
        st.error("El gateway devolvio una sesion invalida.")
        st.stop()

    st.session_state["central_subject"] = subject
    _render_logout_control(cookie_header[1])
    return subject


def _render_logout_control(csrf_token: str) -> None:
    with st.sidebar:
        if st.button("Cerrar sesion", key="smartdoc_central_logout", use_container_width=True):
            result = _auth_browser_component(
                action="logout",
                gateway_url=PUBLIC_GATEWAY_URL,
                frontend_url=FRONTEND_URL,
                csrf_token=csrf_token,
                key="smartdoc_logout_browser_request",
                default=None,
            )
            if isinstance(result, dict) and result.get("status") == "error":
                st.error("No se pudo cerrar la sesion. Intenta nuevamente.")
            st.stop()


def get_session_id() -> str:
    return ensure_session_id()


def get_current_user_folder(session_user_id: str | None = None) -> Path:
    subject = _canonical_subject(session_user_id) if session_user_id else ensure_session_id()
    try:
        return subject_directory(BASE_DIR, subject)
    except InvalidCentralSubject as exc:
        raise CentralSessionUnavailable("Namespace central invalido") from exc


def get_gateway_auth_context() -> tuple[str, str]:
    """Capture the server-visible HttpOnly session for worker-thread HTTP clients."""
    cookie_header = _session_cookie_header()
    if cookie_header is None:
        raise CentralSessionUnavailable("Sesion central ausente")
    return cookie_header


def get_http_session(
    session_user_id: str,
    auth_context: tuple[str, str] | None = None,
) -> requests.Session:
    """Build a server-side client bound to the current central browser session."""
    _canonical_subject(session_user_id)
    cookie_header = auth_context or get_gateway_auth_context()
    session = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
            "Cookie": cookie_header[0],
            "Origin": FRONTEND_ORIGIN,
            "X-CSRF-Token": cookie_header[1],
        }
    )
    return session


@st.cache_data(ttl=60, show_spinner=False)
def get_available_summaries(session_user_id: str) -> dict:
    user_folder = get_current_user_folder(session_user_id)
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
def list_categories(session_user_id: str) -> list[str]:
    user_folder = get_current_user_folder(session_user_id)
    if not user_folder.exists():
        return []
    return sorted([directory.name for directory in user_folder.iterdir() if directory.is_dir()])


@st.cache_data(ttl=30, show_spinner=False)
def list_subcategories(cat_path: Path) -> list[str]:
    if not cat_path.exists():
        return []
    return sorted([directory.name for directory in cat_path.iterdir() if directory.is_dir()])


@st.cache_data(ttl=30, show_spinner=False)
def list_md_files(subcat_path: Path) -> list[str]:
    if not subcat_path.exists():
        return []
    return sorted([file.name for file in subcat_path.glob("*.md")])


@st.cache_data(max_entries=256, show_spinner=False)
def read_markdown_cached(path: str, mtime_ns: int) -> str:
    del mtime_ns
    return Path(path).read_text(encoding="utf-8")


@st.cache_data(max_entries=256, show_spinner=False)
def read_json_cached(path: str, mtime_ns: int) -> dict:
    del mtime_ns
    target = Path(path)
    try:
        import orjson

        return orjson.loads(target.read_bytes())
    except (ImportError, TypeError, ValueError):
        return json.loads(target.read_text(encoding="utf-8"))
