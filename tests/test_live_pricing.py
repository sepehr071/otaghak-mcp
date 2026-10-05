from datetime import date, timedelta

import pytest
from conftest import live_call as call
from conftest import live_dates, live_room

from otaghak_mcp.search import today

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_ot_room_calendar(client):
    room = await live_room(client)
    data = await call(client, "ot_room_calendar", {"room_id": room["id"]})
    assert data["days"] and data["days"][0]["date"] == str(today())  # default start: today in Iran
    assert all(d["price"] > 0 for d in data["days"]) and data["open_until"] > str(today() + timedelta(days=25))


async def test_ot_price_quote(client):
    room = await live_room(client)
    check_in, check_out = live_dates()
    args = {"room_id": room["id"], "check_in": check_in, "check_out": check_out, "guests": room["max_guests"]}
    data = await call(client, "ot_price_quote", args)
    assert data["available"], data.get("reason")
    # nights_total is the page amount (totalPrice to 100); calendar nights are rounded to 1,000 each
    assert data["nights_total"] % 100 == 0
    assert abs(data["nights_total"] - sum(n["price"] for n in data["nightly"])) < 1000 * data["nights"]
    assert data["total"] == data["nights_total"] + data["extra_guest_total"]
    assert data["extra_guests"] == room["max_guests"] - room["base_guests"]


async def test_ot_holidays(client):
    data = await call(
        client, "ot_holidays", {"start": str(date.today()), "end": str(date.today() + timedelta(days=30))}
    )
    assert data["holidays"] and all(h["weekday"] for h in data["holidays"])  # Fridays are flagged


async def test_ot_late_checkout(client):
    room = await live_room(client)
    _, check_out = live_dates()
    data = await call(client, "ot_late_checkout", {"room_id": room["id"], "check_out": check_out})
    assert isinstance(data["offered"], bool) and data["check_out"] == check_out
    assert data["prices"] or not data["offered"]
