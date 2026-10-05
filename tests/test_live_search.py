import pytest
from conftest import live_call as call
from conftest import live_dates

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_ot_find_place(client):
    data = await call(client, "ot_find_place", {"query": "رامسر"})
    assert data["places"][0]["slug"] == "ramsar" and data["places"][0]["listings"] > 100


async def test_ot_search_rooms(client):
    check_in, check_out = live_dates()
    args = {
        "city": ["ramsar"],
        "check_in": check_in,
        "check_out": check_out,
        "guests": 2,
        "sort": "cheapest",
        "limit": 10,
    }
    data = await call(client, "ot_search_rooms", args)
    totals = [r["stay_total"] for r in data["rooms"]]  # cheapest re-sorts a dated page by stay_total
    assert data["dated"] and data["total"] > 50 and totals == sorted(totals) and totals[0] > 0
    assert all(r["stay_total"] >= r["price_per_night"] * 3 - 10 for r in data["rooms"])


async def test_ot_search_filters(client):
    data = await call(client, "ot_search_filters", {"city": "tehran"})
    assert data["price_range"]["max"] > 0 and any(t["id"] == 137 for t in data["room_types"])
    assert any(f["code"].startswith("ruleValues=") for f in data["filters"]) and data["districts"]


async def test_ot_destinations(client):
    data = await call(client, "ot_destinations", {"collection": "beach"})
    assert data["cities"] and data["collections"] and any(c["slug"] == "ramsar" for c in data["collection_cities"])


async def test_ot_deals(client):
    data = await call(client, "ot_deals", {"limit": 5})
    pcts = [r["discount_pct"] for r in data["most_discounted"]]
    assert pcts and pcts == sorted(pcts, reverse=True) and pcts[0] > 0
