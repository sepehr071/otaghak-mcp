"""Shared async HTTP client for the Otaghak API.

Every tool goes through `fetch`, which caps concurrency and turns HTTP failures into
`ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import os
import random
import re
import time
from typing import Any

import httpx
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

CORE = "https://core.otaghak.com"
WWW = "https://www.otaghak.com"
BLOG = f"{WWW}/blog/wp-json/wp/v2"
ODATA = f"{CORE}/odata/Otaghak"

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Origin": WWW,
    "Referer": f"{WWW}/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "priority": "u=1, i",
}

MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each upstream call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
RETRY_DELAY = 0.5
CACHE_SECONDS = 3600  # for static GET data (FAQ, terms, filter codes, destinations)
CACHE_SIZE = 64

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, float] | None = None
_cache: dict[tuple, tuple[float, Any]] = {}


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, None for network errors."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _proxy_index, _browser_lock, _cleared = transport, None, None, 0, None, None
    _cache.clear()


def _proxies() -> list[str]:
    """OTAGHAK_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("OTAGHAK_MCP_PROXY", "").split(",") if p.strip()]


# Plain CDN error pages are not challenges; only a page that carries a bot-check marker is.
_CHALLENGE = re.compile(r"just a moment|captcha|challenge|arvan|attention required|enable javascript", re.I)


def _is_challenge(r: httpx.Response) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known challenge marker."""
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + r.text[:20].lower()
        and bool(_CHALLENGE.search(r.text[:5000]))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(30)  # longer than the 30 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Direct calls are fastest; ignore system proxy settings unless the user opts in
        # with OTAGHAK_MCP_PROXY. curl_cffi presents Chrome's TLS/HTTP2 fingerprint and default headers;
        # cookies and redirects stay with httpx. The proxy goes to the transport (a client-level proxy
        # would be ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=30,
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=True,
            event_hooks={"response": [_same_site_redirect]},
            trust_env=False,
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
    A GET that times out, hits a network error, 5xx or 429 is retried up to MAX_RETRIES times with jittered
    exponential backoff, or after Retry-After when sent. Other 4xx (403 too) are never retried. A POST (which only
    reads here) is retried only after a connection reset, which happens before the request is sent.
    """
    key = (url, tuple(sorted((params or {}).items())))
    if ttl and method == "GET" and (hit := _cache.get(key)) and hit[0] > time.monotonic():
        return copy.deepcopy(hit[1])
    for attempt in range(MAX_RETRIES + 1):
        try:
            data = await _fetch(url, params, method, json, expect)
            break
        except ApiError as e:
            transient = e.status is None or e.status >= 500 or e.status == 429
            if method != "GET":
                transient = isinstance(e.__cause__, httpx.ConnectError)
            if attempt == MAX_RETRIES or not transient:
                raise
            wait = e.retry_after if e.retry_after is not None else RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
    if ttl and method == "GET":
        if len(_cache) >= CACHE_SIZE:
            _cache.pop(next(iter(_cache)))  # oldest insert
        _cache[key] = (time.monotonic() + ttl, copy.deepcopy(data))
    return data


async def _send(
    client: httpx.AsyncClient,
    limit: asyncio.Semaphore,
    url: str,
    params: dict[str, Any] | None,
    method: str,
    json: Any,
) -> httpx.Response:
    host = httpx.URL(url).host
    # The web app (www) calls core.otaghak.com cross-origin but same-site; its own blog calls are same-origin.
    headers = {"sec-fetch-site": "same-origin" if host == httpx.URL(WWW).host else "same-site"}
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            return await client.request(method, url, params=params, json=json, headers=headers)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.TooManyRedirects as e:
        _next_proxy(client)
        raise ApiError(f"{host} kept redirecting. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection, or set OTAGHAK_MCP_PROXY."
        ) from e


async def _browser_cookies(proxy: str | None) -> list[dict[str, Any]]:
    """Open the home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "Otaghak served a bot-check page. Install the browser extra (`pip install otaghak-mcp[browser]`, or "
            "`uvx --with patchright==1.63.0 otaghak-mcp`) and run `patchright install chromium`.",
            403,
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(WWW, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|access denied|attention required|arvancloud/i.test(document.title)", timeout=30_000
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[0] is client and time.monotonic() - _cleared[1] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"Otaghak bot check was not cleared by the browser ({type(e).__name__}).", 403) from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, time.monotonic())


async def _fetch(url: str, params: dict[str, Any] | None, method: str, json: Any, expect: type | None) -> Any:
    client, limit = _get_client()
    host = httpx.URL(url).host
    r = await _send(client, limit, url, params, method, json)
    if _is_challenge(r):
        _next_proxy(client)
        client, limit = _get_client()
        await _clear_challenge(client)
        r = await _send(client, limit, url, params, method, json)  # one retry; a second block is final
        if _is_challenge(r):
            _next_proxy(client)

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code, _retry_after(r))
    try:
        data = r.json() if r.content else None
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e
    if expect is not None and not isinstance(data, expect):
        raise ApiError(f"{host} returned an unexpected response. Try again in a moment.", r.status_code)
    return data


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


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
