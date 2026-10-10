from datetime import date

import httpx
import pytest
from conftest import body, fixture

from otaghak_mcp import search

pytestmark = pytest.mark.anyio

STAY = {"check_in": "2026-10-20", "check_out": "2026-10-23"}


async def test_ot_find_place_city(client, api):
    api["GetSearchResult"] = fixture("autocomplete.json")
    data = (await client.call_tool("ot_find_place", {"query": " رامسر "})).structured_content
    assert data["places"][0] == {
        "kind": "city",
        "name": "رامسر",
        "slug": "ramsar",
        "listings": 1241,
        "province": "مازندران",
        "province_slug": "mazandaran",
        "url": "https://www.otaghak.com/city/ramsar/",
    }
    assert len(data["places"]) == 3 and "hint" not in data
    assert api.calls[0].url.params["input"] == "رامسر"


async def test_ot_find_place_landmark_hint(client, api):
    api["GetSearchResult"] = []  # the API's answer for 'حرم امام رضا'
    data = (await client.call_tool("ot_find_place", {"query": "حرم امام رضا"})).structured_content
    assert data["places"] == [] and "district_id" in data["hint"]


async def test_ot_find_place_room_code(client, api):
    api["GetSearchResult"] = fixture("autocomplete_room.json")
    data = (await client.call_tool("ot_find_place", {"query": "2508916"})).structured_content
    assert data["places"] == [
        {
            "kind": "room",
            "name": "سوییسی لیلی",
            "room_id": 2508916,
            "city": "ramsar",
            "max_guests": 4,
            "province": "مازندران",
            "province_slug": "mazandaran",
            "url": "https://www.otaghak.com/room/2508916/",
        }
    ]


async def test_ot_search_rooms_dated(client, api):
    api["SearchRooms"] = fixture("search_rooms.json")
    args = {
        "city": ["ramsar"],
        **STAY,
        "guests": 4,
        "sort": "cheapest",
        "max_price": 5000000,
        "room_types": [138, 154],
        "rent_types": ["whole_place"],
        "filters": ["attribute=1201|1202|1203|1401", "attributeValues=200201|200202 "],
        "page": 1,
        "limit": 3,
    }
    data = (await client.call_tool("ot_search_rooms", args)).structured_content
    assert data["total"] == 904 and data["dated"] and data["nights"] == 3
    assert data["lowest_price_per_night"] == 1083333
    # The API's cheapest-per-night room is the dearest stay for 4 guests: the page is re-sorted by stay_total.
    assert [r["stay_total"] for r in data["rooms"]] == [3600000, 4650000, 5049999]
    assert data["rooms"][2] == {
        "id": 2512604,
        "name": "رادین",
        "type": "خانه",
        "city": "رامسر",
        "province": "مازندران",
        "rating": 4.7,
        "reviews": 7,
        "base_guests": 2,
        "max_guests": 5,
        "bedrooms": 1,
        "area_m2": 40,
        "rent_type": "semi_private",
        "price_per_night": 1083333,
        "price_per_night_before_discount": 1083333,
        "discount_pct": 0,
        "stay_total": 5049999,  # 3,249,999 for 2 guests + 2 extra x 300,000 x 3 nights
        "instant": False,
        "prime": False,
        "location": {"lat": 36.873, "lon": 50.768},
        "photo": "https://cdn.otaghak.com/otg-images-new/X500/78513dd4-5262-4a64-bd42-b21a46af48f2.webp",
        "url": "https://www.otaghak.com/room/2512604/?checkIn=2026-10-20&checkOut=2026-10-23",
    }
    sent = body(api.calls[0])
    assert sent["cities"] == ["ramsar"] and sent["boxCode"] == "" and sent["stateCodes"] == []
    assert {k: sent[k] for k in ("checkIn", "checkOut", "person", "sortingType", "skip", "take", "maxPrice")} == {
        "checkIn": "2026-10-20",
        "checkOut": "2026-10-23",
        "person": 4,
        "sortingType": "MinPricePriority",
        "skip": 3,
        "take": 3,
        "maxPrice": 5000000,
    }
    assert sent["type"] == [138, 154] and sent["rentTypes"] == ["Dedicate"]
    assert sent["attribute"] == ["1201|1202|1203|1401"] and sent["attributeValues"] == ["200201|200202"]


def test_card_one_night():
    # A dated 1-night search answers totalNights 0 (seen live 2026-10-10): the card stays dated.
    room = {**fixture("search_rooms.json")["rooms"][0], "totalNights": 0}
    c = search.card(room, 2, date(2026, 10, 20), date(2026, 10, 21))
    assert c["url"].endswith("?checkIn=2026-10-20&checkOut=2026-10-21") and c["stay_total"] > 0
    assert c["price_per_night"] == room["afterDiscountAverage"]


async def test_ot_search_rooms_undated(client, api):
    api["SearchRooms"] = fixture("search_rooms_undated.json")
    data = (
        await client.call_tool("ot_search_rooms", {"province": "mazandaran", "sort": "top_rated"})
    ).structured_content
    assert not data["dated"] and data["nights"] is None
    room = data["rooms"][1]
    assert (room["price_per_night"], room["price_per_night_before_discount"], room["discount_pct"]) == (
        1800000,
        3000000,
        40,
    )
    assert "stay_total" not in room and room["url"] == "https://www.otaghak.com/room/2435648/"
    assert room["base_guests"] == 4 and room["max_guests"] == 5  # undated search sends basePersonCount 0
    sent = body(api.calls[0])
    assert sent["stateCodes"] == ["mazandaran"] and sent["checkIn"] is None and sent["sortingType"] == "MostRated"


async def test_ot_search_rooms_most_discounted_resorts(client, api):
    rooms = fixture("search_discounted.json")
    rooms["rooms"].reverse()  # the API order is only roughly by percent
    api["SearchRooms"] = rooms
    data = (
        await client.call_tool("ot_search_rooms", {"collection": "discounted", "sort": "most_discounted"})
    ).structured_content
    assert [r["discount_pct"] for r in data["rooms"]] == [90, 80, 80, 71]


@pytest.mark.parametrize(
    "args, message",
    [
        ({}, "Give a scope"),
        ({"city": ["ramsar"], "check_in": "1405-07-28", "check_out": "1405-07-30"}, "1405-07-28 is 2026-10-20"),
        ({"city": ["ramsar"], "check_in": "2026-10-01", "check_out": "2026-10-05"}, "in the past"),
        ({"city": ["ramsar"], "check_in": "2026-10-20"}, "both check_in and check_out"),
        ({"city": ["ramsar"], "check_in": "2026-10-20", "check_out": "2026-10-20"}, "after check_in"),
        ({"city": ["ramsar"], "check_in": "2026-10-20", "check_out": "2026-11-10"}, "at most 20 nights"),
        ({"city": ["ramsar"], "check_in": "2026-11-20", "check_out": "2026-11-22"}, "up to 2026-11-21"),
        ({"city": ["ramsar"], "filters": ["pool"]}, "Unknown filter"),
        ({"city": ["ram sar"]}, "pattern"),
    ],
)
async def test_ot_search_rooms_rejects_bad_input(client, api, args, message):
    result = await client.call_tool("ot_search_rooms", args)
    assert result.is_error and message in result.content[0].text
    assert not api.calls


@pytest.mark.parametrize(
    "args, scope",
    [
        ({"collection": "nowhere"}, "collection='nowhere'"),
        ({"city": ["tehrann"]}, "city=['tehrann']"),
        ({"province": "mazandarn"}, "province='mazandarn'"),
    ],
)
async def test_ot_search_rooms_unknown_place(client, api, args, scope):
    api["SearchRooms"] = lambda request: httpx.Response(404)  # the API's answer to any unknown slug
    result = await client.call_tool("ot_search_rooms", args)
    text = result.content[0].text
    assert result.is_error and f"Unknown place ({scope})" in text and "ot_find_place" in text
    assert "None" not in text


async def test_ot_search_filters(client, api):
    api["GetSearchFilter"] = fixture("search_filters.json")
    api["GetAllByCityName"] = fixture("city_districts.json")
    data = (await client.call_tool("ot_search_filters", {"city": "ramsar"})).structured_content
    assert data["price_range"] == {"min": 40000, "max": 90000000}
    assert data["room_types"][:2] == [{"id": 138, "name": "ویلا"}, {"id": 137, "name": "آپارتمان"}]
    codes = {f["name"]: f["code"] for f in data["filters"]}
    assert codes["استخر: آب گرم"] == "attributeValues=130201:1302:XC1"
    assert codes["امکان همراه داشتن حیوان خانگی"] == "ruleValues=10101|10103|10102"
    assert codes["جنگلی"] == "attributeValues=20203:RA"
    assert all(not f["code"].startswith(("instant", "minRate", "rentTypes", "type")) for f in data["filters"])
    assert data["districts"][1] == {
        "id": 231,
        "name": "کاخ گلستان",
        "en_name": "Golestan Palac",
        "type": "Tourist",
        "radius_km": 2.0,
    }
    assert dict(api.sent("GetSearchFilter")[0].url.params) == {"boxCode": "", "cityCodes": "ramsar", "stateCodes": ""}


async def test_ot_search_filters_unknown_city(client, api):
    api["GetSearchFilter"] = lambda request: httpx.Response(500, json={"Message": "خطایی در سیستم رخ داده است"})
    api["GetAllByCityName"] = []
    result = await client.call_tool("ot_search_filters", {"city": "nowhere"})
    assert result.is_error and "ot_find_place" in result.content[0].text


async def test_ot_destinations(client, api):
    api["GetSearchSuggestion"] = fixture("popular.json")
    api["ListCityLanding"] = fixture("city_landing.json")
    data = (await client.call_tool("ot_destinations", {"collection": "beach"})).structured_content
    assert data["cities"][1] == {
        "slug": "ramsar",
        "name": "رامسر",
        "province": "مازندران",
        "province_slug": "mazandaran",
        "listings": 1241,
    }
    assert data["collections"][0] == {"code": "discounted", "name": "اجاره اقامتگاه با تخفیف", "listings": 1699}
    assert data["collection_cities"][0] == {"slug": "ramsar", "name": "رامسر"}
    assert "ListCityLanding(boxName='beach')" in str(api.sent("ListCityLanding")[0].url).replace("%27", "'")


async def test_ot_destinations_unknown_collection(client, api):
    api["GetSearchSuggestion"] = fixture("popular.json")
    api["ListCityLanding"] = {"value": []}
    result = await client.call_tool("ot_destinations", {"collection": "nowhere"})
    assert result.is_error and "Unknown collection" in result.content[0].text


async def test_ot_deals(client, api):
    api["GetHomePage"] = fixture("home.json")
    api["SearchRooms"] = fixture("search_discounted.json")
    data = (await client.call_tool("ot_deals", {"limit": 4})).structured_content
    assert [r["discount_pct"] for r in data["last_second"]] == [59, 40, 30]  # fixture order is ascending
    assert data["last_second"][0] == {
        "id": 2501800,
        "name": "چیکا",
        "type": "ویلا",
        "city": "صومعه\u200cسرا",
        "price_per_night": 7175000,
        "price_per_night_before_discount": 17500000,
        "discount_pct": 59,
        "rating": 5.0,
        "reviews": 14,
        "max_guests": 34,
        "instant": True,
        "url": "https://www.otaghak.com/room/2501800/",
    }
    assert data["last_second_hours"] == "14:00:00-23:50:00"
    assert data["most_discounted"][0]["discount_pct"] == 90 and data["most_discounted_total"] == 1715
    sent = body(api.sent("SearchRooms")[0])
    assert sent["boxCode"] == "discounted" and sent["sortingType"] == "MostDiscounted" and sent["take"] == 4


async def test_ot_deals_in_a_city(client, api):
    found = fixture("search_discounted.json")
    found["rooms"][0]["totalDiscountValue"] = 0  # discounted-collection rooms can have 0% on the asked dates
    api["SearchRooms"] = found
    args = {"city": "kish", **STAY, "guests": 4}
    data = (await client.call_tool("ot_deals", args)).structured_content
    assert "last_second" not in data and not api.sent("GetHomePage")  # site-wide deals from other cities
    assert data["most_discounted"] and all(r["discount_pct"] > 0 for r in data["most_discounted"])
    assert len(data["most_discounted"]) == len(found["rooms"]) - 1
    sent = body(api.sent("SearchRooms")[0])
    assert (sent["cities"], sent["boxCode"], sent["person"]) == (["kish"], "discounted", 4)


def test_jalali_and_horizon():
    assert search.jalali(date(2026, 10, 4)) == (1405, 7, 12)
    assert search.jalali(date(2026, 3, 21)) == (1405, 1, 1)
    assert search.from_jalali(1405, 7, 28) == date(2026, 10, 20)
    assert search.horizon(date(2026, 10, 4)) == date(2026, 11, 21)  # end of Aban 1405
    assert search.horizon(date(2027, 3, 1)) == date(2027, 4, 20)  # Esfand -> end of Farvardin 1406
