import pytest
from conftest import live_call as call
from conftest import live_dates, live_room

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_ot_room(client):
    room = await live_room(client)
    check_in, check_out = live_dates()
    data = await call(client, "ot_room", {"room_id": room["id"], "check_in": check_in, "check_out": check_out})
    assert data["id"] == room["id"] and data["max_guests"] >= data["base_guests"] > 0
    assert data["price"]["dated"] and data["price"]["price_per_night"] > 0 and data["host"]["id"]
    assert data["photos"][0].startswith("https://cdn.otaghak.com/") and data["cancel_policy"]["type"]


async def test_ot_reviews(client):
    room = await live_room(client)
    data = await call(client, "ot_reviews", {"room_id": room["id"], "limit": 3})
    assert 0 < data["rating"] <= 5 and data["rating_count"] > 0 and data["categories"]
    assert data["reviews"] and data["reviews"][0]["date"]


async def test_ot_similar_rooms(client):
    room = await live_room(client)
    check_in, check_out = live_dates()
    args = {"room_id": room["id"], "check_in": check_in, "check_out": check_out, "guests": 2}
    data = await call(client, "ot_similar_rooms", args)
    assert len(data["rooms"]) + len(data["not_available"]) >= 3
    assert all(r["stay_total"] > 0 for r in data["rooms"])


async def test_ot_host(client):
    room = await live_room(client)
    data = await call(client, "ot_host", {"room_id": room["id"], "rooms_limit": 3, "reviews_limit": 3})
    assert data["id"] and data["active_rooms"] >= 1 and data["rooms"]
