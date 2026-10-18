import asyncio
import sys

import httpx
import pytest

from otaghak_mcp import http

pytestmark = pytest.mark.anyio
URL = "https://core.otaghak.com/api/v1/Faq/GetAllForFaqPage"


async def test_ttl_cache_returns_copies_and_keys_on_params(api):
    api["GetAllForFaqPage"] = [{"q": 1}]
    a = await http.fetch(URL, {"x": "1"}, ttl=60)
    a.append("mutated")
    assert await http.fetch(URL, {"x": "1"}, ttl=60) == [{"q": 1}]
    await http.fetch(URL, {"x": "2"}, ttl=60)
    assert len(api.calls) == 2  # second x=1 call was cached, x=2 was not
    await http.fetch(URL, {"x": "1"})
    assert len(api.calls) == 3  # no ttl: always upstream


async def test_cache_lookup_is_get_only(api):
    api["GetAllForFaqPage"] = [{"q": 1}]
    await http.fetch(URL, ttl=60)
    await http.fetch(URL, method="POST", json={}, ttl=60)
    assert len(api.calls) == 2  # the POST did not get the cached GET body


async def test_cache_is_bounded(api):
    api["GetAllForFaqPage"] = []
    for i in range(http.CACHE_SIZE + 5):
        await http.fetch(URL, {"i": str(i)}, ttl=60)
    assert len(http._cache) == http.CACHE_SIZE


async def test_get_retries_once_on_gateway_error_but_post_does_not(api, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    answers = iter([502, 200])
    api["GetAllForFaqPage"] = lambda r: httpx.Response(next(answers), json=[1])
    assert await http.fetch(URL) == [1]
    api["SearchRooms"] = lambda r: httpx.Response(502)
    with pytest.raises(http.ApiError):
        await http.fetch("https://core.otaghak.com/api/v3/RoomSearch/SearchRooms", method="POST", json={})
    assert len(api.sent("SearchRooms")) == 1


async def test_redirect_off_site_is_refused(api):
    api["GetAllForFaqPage"] = lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"})
    with pytest.raises(http.ApiError, match="refused"):
        await http.fetch(URL)


async def test_xhr_headers_per_host(api):
    api["GetAllForFaqPage"] = []
    api["posts"] = []
    await http.fetch(URL)
    await http.fetch(f"{http.BLOG}/posts")
    core, www = api.calls
    assert core.headers["sec-fetch-site"] == "same-site" and www.headers["sec-fetch-site"] == "same-origin"
    for r in api.calls:
        assert r.headers["origin"] == http.WWW and r.headers["referer"] == f"{http.WWW}/"
        assert r.headers["sec-fetch-mode"] == "cors"


def test_is_challenge():
    html = {"content-type": "text/html"}
    assert http._is_challenge(httpx.Response(403, text="<html>Just a moment...</html>", headers=html))
    assert http._is_challenge(httpx.Response(503, text="<!doctype html><title>ArvanCloud</title>"))
    assert not http._is_challenge(httpx.Response(403, text="<html>Access Denied</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, json={"status": 403}))


async def test_proxy_list_rotates_once_per_client(monkeypatch, api):
    monkeypatch.setenv("OTAGHAK_MCP_PROXY", "http://p1:1, http://p2:2")
    assert http._proxies() == ["http://p1:1", "http://p2:2"]
    old, _ = http._get_client()
    http._next_proxy(old)
    new, _ = http._get_client()
    assert http._proxy_index == 1 and new is not old
    http._next_proxy(old)  # a late failure on the retired client must not skip the proxy now in use
    assert http._proxy_index == 1 and http._client is new


async def test_challenge_cleared_by_browser_once(monkeypatch, api):
    runs = []

    async def fake_browser(proxy):
        runs.append(proxy)
        await asyncio.sleep(0.01)
        return [{"name": "clearance", "value": "ok", "domain": ".otaghak.com", "path": "/"}]

    monkeypatch.setattr(http, "_browser_cookies", fake_browser)

    def guarded(request):
        if "clearance=ok" not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="<html>Just a moment</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, json=[])

    api["GetAllForFaqPage"] = guarded
    await asyncio.gather(http.fetch(URL, {"a": "1"}), http.fetch(URL, {"a": "2"}))
    assert len(runs) == 1  # the second caller reused the cookies the first one fetched
    api["GetAllForFaqPage"] = lambda r: httpx.Response(403, text="<html>Just a moment</html>")
    with pytest.raises(http.ApiError) as e:  # still blocked after the one retry: final
        await http.fetch(URL)
    assert e.value.status == 403


async def test_challenge_without_patchright(monkeypatch, api):
    monkeypatch.setitem(sys.modules, "patchright.async_api", None)  # makes the import fail
    api["GetAllForFaqPage"] = lambda r: httpx.Response(403, text="<html>Just a moment</html>")
    with pytest.raises(http.ApiError, match=r"otaghak-mcp\[browser\].*patchright install chromium"):
        await http.fetch(URL)


async def test_retry_after_is_honoured_and_capped_and_403_not_retried(monkeypatch, api):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)
    answers = iter([(429, "3"), (503, "999"), (200, "")])
    api["GetAllForFaqPage"] = lambda r: httpx.Response(
        (a := next(answers))[0], json=[1], headers={"Retry-After": a[1]} if a[1] else {}
    )
    assert await http.fetch(URL) == [1]
    assert [w for w in waits if w] == [3, http.MAX_RETRY_WAIT]
    api["GetAllForFaqPage"] = lambda r: httpx.Response(403, json={})
    n = len(api.calls)
    with pytest.raises(http.ApiError):
        await http.fetch(URL)
    assert len(api.calls) == n + 1
    api["GetAllForFaqPage"] = lambda r: httpx.Response(429)
    n = len(api.calls)
    with pytest.raises(http.ApiError):
        await http.fetch(URL)
    assert len(api.calls) == n + 3  # gives up after MAX_RETRIES
