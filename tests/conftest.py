import asyncio
import json
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from mcp import Client

from otaghak_mcp import http, search
from otaghak_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = datetime(2026, 10, 4, 12, tzinfo=search.IRAN)  # fixtures were recorded on this day (1405-07-12)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def route(request: httpx.Request) -> str:
    """Endpoint name of a request: the last path segment without OData arguments ('GetRoomPdp', 'GetBasicsPrice')."""
    return request.url.path.rstrip("/").rsplit("/", 1)[-1].split("(")[0]


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def live_dates(nights: int = 3) -> tuple[str, str]:
    """A stay well inside the open calendar (it always reaches at least a month ahead)."""
    start = date.today() + timedelta(days=16)
    return str(start), str(start + timedelta(days=nights))


async def live_call(client, name: str, args: dict) -> dict:
    """Call a tool against the real site, about one request per second, and fail on tool errors."""
    await asyncio.sleep(1)
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def live_room(client) -> dict:
    """A rated Ramsar room that is free on live_dates() (search card)."""
    check_in, check_out = live_dates()
    args = {"city": ["ramsar"], "check_in": check_in, "check_out": check_out, "guests": 2, "sort": "top_rated"}
    data = await live_call(client, "ot_search_rooms", args)
    return next(r for r in data["rooms"] if r["reviews"] and r["rating"])


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new loop."""
    monkeypatch.setattr(http, "RETRY_DELAY", 0)
    monkeypatch.setattr(http, "JITTER", (0, 0))
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def api(monkeypatch):
    """Route table for a fake upstream: api["GetRoomPdp"] = JSON body, or a callable(request) -> httpx.Response.

    Keyed by endpoint name (see `route`). Unknown keys return 404. Every request is appended to
    api.calls so tests can assert on what was sent.
    """

    class Routes(dict):
        calls: list[httpx.Request]

        def sent(self, name: str) -> list[httpx.Request]:
            return [r for r in self.calls if route(r) == name]

    routes = Routes()
    routes.calls = []
    # Date checks (past, open calendar) are judged against the day the fixtures were recorded.
    monkeypatch.setattr(search, "datetime", types.SimpleNamespace(now=lambda tz=None: RECORDED))

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(route(request))
        if target is None:
            return httpx.Response(404)
        if callable(target):
            return target(request)
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def compact_result(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:  # tools return one compact JSON text block; tests read it as structured content
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = compact_result
        yield c
