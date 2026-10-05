import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_ot_help(client):
    data = await call(client, "ot_help", {"topic": "terms", "query": "لغو"})
    assert data["sections"] and all("لغو" in s["title"] + s["text"] for s in data["sections"])


async def test_ot_travel_guide(client):
    data = await call(client, "ot_travel_guide", {"place": "ramsar"})
    assert data["name"] == "رامسر" and len(data["guide"]) > 500 and data["faq"] and data["blog"]
