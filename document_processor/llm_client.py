from __future__ import annotations

import asyncio
from typing import Dict, List, Tuple

import httpx

_client_cache: dict[tuple[str, float], httpx.AsyncClient] = {}


def get_http_client(base_url: str, timeout_s: float) -> httpx.AsyncClient:
    key = (base_url, float(timeout_s))
    client = _client_cache.get(key)
    if client and not client.is_closed:
        return client
    client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout_s), follow_redirects=True)
    _client_cache[key] = client
    return client


async def _retry(func, *args, retries: int = 2, delay: float = 1.0, **kwargs):
    last = None
    for i in range(retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last = e
            if i < retries:
                await asyncio.sleep(delay)
    raise last


async def classify_text(client: httpx.AsyncClient, snippet: str, existing_categories: Dict[str, List[str]]):
    r = await client.post("/classify", json={"text": snippet, "categories": existing_categories})
    r.raise_for_status()
    return r.json() or {}


async def _call_summarize_chunk(client: httpx.AsyncClient, pages_chunk: List[Tuple[int, str]]):
    r = await client.post("/summarize_chunk", json={"pages": pages_chunk})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"summary": str(data)}


async def summarize_page_with_retry(client: httpx.AsyncClient, one_page_chunk: List[Tuple[int, str]]):
    return await _retry(_call_summarize_chunk, client, one_page_chunk, retries=1, delay=1.5)
