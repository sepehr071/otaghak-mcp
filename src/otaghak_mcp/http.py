"""Shared async HTTP client for the Otaghak API.

Every tool goes through `fetch`, which caps concurrency and turns HTTP failures into
`ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import os
import time
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

CORE = "https://core.otaghak.com"
WWW = "https://www.otaghak.com"
BLOG = f"{WWW}/blog/wp-json/wp/v2"
ODATA = f"{CORE}/odata/Otaghak"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0",
    "Accept": "application/json, text/plain, */*",
    "Origin": WWW,
    "Referer": f"{WWW}/",
}

MAX_CONCURRENCY = 4
CACHE_SECONDS = 3600  # for static GET data (FAQ, terms, filter codes, destinations)
CACHE_SIZE = 64

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_cache: dict[tuple, tuple[float, Any]] = {}


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, None for network errors."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit
    _transport, _client, _limit = transport, None, None
    _cache.clear()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Direct calls are fastest; ignore system proxy settings unless the user opts in
        # with OTAGHAK_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=30,
            follow_redirects=True,
            event_hooks={"response": [_same_site_redirect]},
            trust_env=False,
            proxy=os.environ.get("OTAGHAK_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def _same_site_redirect(r: httpx.Response) -> None:
    """Refuse a redirect that leaves otaghak.com (the requests carry browser-like Origin / Referer headers)."""
    if r.is_redirect:
        target = httpx.URL(r.headers.get("location", "")).host or r.url.host
        if target != "otaghak.com" and not target.endswith(".otaghak.com"):
            raise ApiError(f"{r.url.host} redirected to {target}; refused.", r.status_code)


async def fetch(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    json: Any = None,
    expect: type | None = None,
    ttl: int = 0,
) -> Any:
    """Call an endpoint (full URL) and return the parsed JSON body (None for an empty or `null` body).

    With `expect` (dict or list) any other body, `null` included, raises an ApiError. With `ttl` (seconds, GET only)
    the body is cached in memory and a copy is returned.
    """
    key = (url, tuple(sorted((params or {}).items())))
    if ttl and method == "GET" and (hit := _cache.get(key)) and hit[0] > time.monotonic():
        return copy.deepcopy(hit[1])
    data = await _fetch(url, params, method, json, expect)
    if ttl and method == "GET":
        if len(_cache) >= CACHE_SIZE:
            _cache.pop(next(iter(_cache)))  # oldest insert
        _cache[key] = (time.monotonic() + ttl, copy.deepcopy(data))
    return data


async def _fetch(url: str, params: dict[str, Any] | None, method: str, json: Any, expect: type | None) -> Any:
    client, limit = _get_client()
    host = httpx.URL(url).host
    r = None
    # A connection reset before the request is sent is safe to retry once; so is a GET answered 502-504.
    for attempt in range(2):
        try:
            async with limit:
                r = await client.request(method, url, params=params, json=json)
        except httpx.TimeoutException as e:
            raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
        except httpx.RequestError as e:
            if attempt == 0 and isinstance(e, httpx.ConnectError):
                await asyncio.sleep(1)
                continue
            raise ApiError(
                f"Could not reach {host} ({type(e).__name__}). Check the internet connection, or set OTAGHAK_MCP_PROXY."
            ) from e
        if attempt == 0 and method == "GET" and r.status_code in (502, 503, 504):
            await asyncio.sleep(1)
            continue
        break
    assert r is not None

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code)
    try:
        data = r.json() if r.content else None
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e
    if expect is not None and not isinstance(data, expect):
        raise ApiError(f"{host} returned an unexpected response. Try again in a moment.", r.status_code)
    return data


def _status_message(r: httpx.Response, host: str) -> str:
    code = r.status_code
    if code == 403:
        return (
            f"{host} blocked the request (HTTP 403). Wait a minute; if it persists, "
            "turn off the VPN or set OTAGHAK_MCP_PROXY to another proxy."
        )
    if code == 404:
        return f"Not found on {host} (HTTP 404). Check the room id, host id or place slug."
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code > 500:  # gateway / WAF errors (502-504) carry an HTML page, not a reason
        return f"{host} is temporarily unavailable (HTTP {code}). Try again in a minute."
    if code == 500:
        # Otaghak answers 500 for unknown ids and slugs and for dates it cannot handle, too.
        return (
            f"{host} had a server error (HTTP 500): {_error_text(r)}. "
            "This usually means an unknown id or slug, or dates outside the bookable window."
        )
    return f"{host} rejected the request (HTTP {code}): {_error_text(r)}"


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200] or "empty body"
    if isinstance(body, dict):
        return str(body.get("Message") or body.get("message") or body)[:300]
    return str(body)[:300]
