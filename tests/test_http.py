import asyncio

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
