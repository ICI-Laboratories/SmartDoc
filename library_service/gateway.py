"""Single authenticated OpenAI-compatible inference entry point for SmartDoc."""
import os
from urllib.parse import urlsplit, urlunsplit


def gateway_url(path):
    base = os.environ.get('LLM_GATEWAY_BASE_URL', 'http://127.0.0.1:8009/v1').strip().rstrip('/')
    parsed = urlsplit(base)
    # Accessing .port also rejects nonnumeric and out-of-range ports.
    parsed.port
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/v1') or any(ord(character) < 32 for character in base)):
        raise ValueError('LLM_GATEWAY_BASE_URL debe ser la base HTTP(S) del gateway, con /v1 opcional, '
                         'sin endpoint, credenciales ni parámetros.')
    return urlunsplit((parsed.scheme, parsed.netloc, '/v1/' + path.lstrip('/'), '', ''))


def gateway_headers():
    key = os.environ.get('LLM_GATEWAY_API_KEY', '').strip()
    if not key or '\n' in key or '\r' in key:
        raise ValueError('Configura LLM_GATEWAY_API_KEY para usar los modelos del gateway.')
    return {'Authorization': 'Bearer ' + key}


def gateway_ready():
    try:
        gateway_url('models')
        gateway_headers()
    except ValueError:
        return False
    return True
