import httpx
import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

SERVER_ERROR = {"Message": "خطایی در سیستم رخ داده است", "Code": "Error in the application."}


def room_routes(api):
    api["GetRoomPdp"] = fixture("pdp.json")
    api["GetRoomDetail"] = fixture("detail.json")
    api["GetRoomPdpAttributes"] = fixture("attributes.json")


async def test_ot_room(client, api):
    room_routes(api)
    args = {"room_id": 2512254, "check_in": "2026-10-20", "check_out": "2026-10-23", "max_photos": 2}
    data = (await client.call_tool("ot_room", args)).structured_content
    assert data["name"] == "سوئیسی نرگس" and data["type"] == "کلبه" and data["city_slug"] == "ramsar"
    assert data["url"] == "https://www.otaghak.com/room/2512254/?checkIn=2026-10-20&checkOut=2026-10-23"
    assert (data["base_guests"], data["max_guests"], data["bedrooms"], data["beds"], data["area_m2"]) == (
        4,
        6,
        2,
        2,
        96,
    )
    assert data["rent_type"] == "whole_place"
    # dated average of 4.5M + 5M + 5M; afterDiscount 0 means no discount
    assert data["price"] == {
        "dated": True,
        "price_per_night": 4833300,  # no before-discount key: no discount
        "discount_pct": 0,
        "extra_guest_per_night_before_discount": 500000,
    }
    assert data["check_in"] == "14:00-23:00" and data["check_out"] == "12:00"
    assert (data["rating"], data["rating_count"], data["successful_bookings"]) == (5.0, 3, 47)
    assert data["instant_on_some_nights"] is True and "instant" not in data
    assert "reviews" not in data  # seo commentCount can double the written reviews
    assert "ورود حیوان خانگی مجاز نیست" in data["rules"]
    assert data["rules"][-1].startswith("با توجه به چوبی بودن سازه")  # host's own rule
    assert data["documents_required"] == "مدرک محرمیت لازم است"
    assert data["cancel_policy"]["type"] == "Medium" and data["cancel_policy"]["title"] == "متعادل"
    assert data["cancel_policy"]["guest_penalties"][0] == {
        "when": "لغو رزرو تا 48 ساعت مانده به زمان ورود روز کنسل شده",
        "penalty": "15 درصد کل مبلغ رزرو",
    }
    assert data["amenities"]["لوازم خانگی"][0] == "مبلمان (یک مبل ۳ نفره، یک مبل دو نفره، یک مبل یک نفره)"
    assert data["nearby"][0] == {
        "place": "سوپر مارکت",
        "walk": "پیاده: کمتر از 10 دقیقه",
        "drive": "با ماشین: کمتر از 5 دقیقه",
    }
    assert data["mobile_signal"]["همراه اول"] == "خوب/4G"
    # room-detail host stats are often defaults (60 min, 0%), so only ot_host reports them
    assert data["host"] == {"id": 2999269, "name": "فاطمه قنبری", "host_since": "2023-07-03"}
    assert data["location"] == {"lat": 36.87662433, "lon": 50.739135624, "approximate": True}
    assert data["photo_count"] == 10
    assert data["photos"] == [
        "https://cdn.otaghak.com/otg-images-new/Large/14a12d6e-2ddf-4dd7-9390-8a910ac3140c.webp",
        "https://cdn.otaghak.com/otg-images-new/Large/64fe6dc7-7683-42e0-9ba5-b7099232e1df.webp",
    ]
    assert dict(api.sent("GetRoomPdp")[0].url.params) == {
        "roomId": "2512254",
        "checkInDate": "2026-10-20",
        "checkOutDate": "2026-10-23",
    }


async def test_ot_room_unknown(client, api):
    room_routes(api)
    api["GetRoomPdp"] = lambda request: httpx.Response(404)
    result = await client.call_tool("ot_room", {"room_id": 1})
    assert result.is_error and "Unknown room id 1" in result.content[0].text


@pytest.mark.parametrize("content", [b"null", b"[]"])
async def test_ot_room_unexpected_body(client, api, content):
    room_routes(api)
    api["GetRoomPdp"] = lambda request: httpx.Response(200, content=content)
    result = await client.call_tool("ot_room", {"room_id": 2512254})
    assert result.is_error and "returned an unexpected response" in result.content[0].text


async def test_ot_room_gateway_error(client, api):
    room_routes(api)
    api["GetRoomPdp"] = lambda request: httpx.Response(502, text="<html><body>ArvanCloud challenge</body></html>")
    result = await client.call_tool("ot_room", {"room_id": 2512254})
    text = result.content[0].text
    assert result.is_error and "temporarily unavailable (HTTP 502)" in text
    assert "<html>" not in text and "unknown id" not in text


async def test_ot_reviews(client, api):
    api["GetRoomPointsV2"] = fixture("rating.json")
    api["GetRoomRecommendationPercentage"] = fixture("recommendation.json")
    reviews = fixture("reviews.json")
    reviews[1]["replies"] = [{"body": "ممنون از شما", "isFromHost": True}]
    api["GetAllByRoomId"] = reviews
    data = (await client.call_tool("ot_reviews", {"room_id": 2508916, "limit": 3, "page": 1})).structured_content
    assert data["rating"] == 5.0 and data["rating_count"] == 20 and data["recommend_pct"] == 95
    assert data["stars"] == {"5": 20, "4": 0, "3": 0, "2": 0, "1": 0}
    assert data["categories"] == {
        "cleanliness": 5.0,
        "accuracy": 5.0,
        "check_in": 5.0,
        "host_communication": 5.0,
        "neighbourhood_safety": 4.9,
        "value": 5.0,
    }
    assert data["has_more"] and len(data["reviews"]) == 3  # 4 came back for take=4
    assert data["reviews"][0] == {
        "date": "2026-08-12",
        "room_id": 2508916,
        "name": "عزت الله",
        "rating": 5.0,
        "recommends": "yes",
        "text": "همه چيز عالى بشدت پيشنهاد ميكنم \nبرخورد عالى ميزبان و پاسخگويي سريع\nامكانات داخل كلبه\nموقعيت خوب كلبه",
        "pros": [
            "برخورد عالی میزبان",
            "پاسخگویی سریع",
            "امکانات مناسب داخل کلبه",
        ],  # first 3 + count; empty keys are absent
        "pros_more": 1,
    }
    assert data["reviews"][1]["host_reply"] == "ممنون از شما"
    assert dict(api.sent("GetAllByRoomId")[0].url.params) == {"roomId": "2508916", "take": "4", "skip": "3"}


async def test_ot_reviews_not_rated(client, api):
    api["GetRoomPointsV2"] = lambda request: httpx.Response(500, json=SERVER_ERROR)
    api["GetRoomRecommendationPercentage"] = {"recommendationPercentage": 0, "averageRoomVisitCount": 2}
    api["GetAllByRoomId"] = []
    data = (await client.call_tool("ot_reviews", {"room_id": 2531265})).structured_content
    assert data["rating"] is None and data["recommend_pct"] is None and data["reviews"] == []


async def test_ot_reviews_new_room(client, api):
    # A new room (2530974, live) answers like an unknown id: points 500, no reviews, no visits.
    api["GetRoomPointsV2"] = lambda request: httpx.Response(500, json=SERVER_ERROR)
    api["GetRoomRecommendationPercentage"] = {"recommendationPercentage": 0, "averageRoomVisitCount": 0}
    api["GetAllByRoomId"] = []
    data = (await client.call_tool("ot_reviews", {"room_id": 2530974})).structured_content
    assert data["rating"] is None and data["reviews"] == [] and "new rooms" in data["note"]


async def test_ot_similar_rooms_undated(client, api):
    api["GetSimilarRooms"] = fixture("similar.json")
    data = (await client.call_tool("ot_similar_rooms", {"room_id": 2508916})).structured_content
    assert not data["dated"] and [r["id"] for r in data["rooms"]] == [2512254, 2493334, 2470263, 2497656, 2497478]
    first = data["rooms"][0]
    # similar rooms send the base count in personCapacity
    assert (first["base_guests"], first["max_guests"], first["price_per_night"]) == (4, 6, 4050000)
    assert body(api.calls[0]) == {"roomId": 2508916}


async def test_ot_similar_rooms_dated(client, api):
    api["GetSimilarRooms"] = fixture("similar.json")
    api["SearchRooms"] = fixture("similar_search.json")
    args = {"room_id": 2508916, "check_in": "2026-10-20", "check_out": "2026-10-23", "guests": 4}
    data = (await client.call_tool("ot_similar_rooms", args)).structured_content
    assert [(r["id"], r["stay_total"]) for r in data["rooms"]] == [
        (2512254, 14499999),
        (2497478, 22016499),
        (2493334, 28785000),  # 21,375,000 + 2 extra x 1,235,000 x 3
    ]
    # 2470263 is booked (left out by the search), 2497656 takes only 3 guests
    assert data["not_available"] == [2470263, 2497656]
    sent = body(api.sent("SearchRooms")[0])
    assert sent["roomIds"] == ["2512254", "2493334", "2470263", "2497656", "2497478"] and sent["person"] == 4


async def test_ot_similar_rooms_falls_back_to_same_city_and_type(client, api):
    # Since 2026-10 GetSimilarRooms answers no rooms for every room (seen live for 6 rooms on 2026-10-10).
    api["GetSimilarRooms"] = {"viewAllLink": None, "rooms": []}
    search = fixture("search_rooms.json")
    me = search["rooms"][0]["roomId"]

    def search_rooms(request):
        sent = body(request)
        if sent.get("roomIds"):
            return httpx.Response(
                200,
                json={
                    "rooms": [
                        {
                            "roomId": me,
                            "roomTypeId": 154,
                            "cityCode": "ramsar",
                            "afterDiscount": 3900000.0,
                            "basePrice": 5000000.0,
                        }
                    ]
                },
            )
        return httpx.Response(200, json=search)

    api["SearchRooms"] = search_rooms
    args = {"room_id": me, "check_in": "2026-10-20", "check_out": "2026-10-23", "guests": 2}
    data = (await client.call_tool("ot_similar_rooms", args)).structured_content
    assert data["dated"] and data["rooms"] and me not in [r["id"] for r in data["rooms"]] and "note" in data
    assert all(r["stay_total"] > 0 for r in data["rooms"])
    sent = body(api.sent("SearchRooms")[1])
    assert (sent["cities"], sent["type"], sent["maxPrice"], sent["person"]) == (["ramsar"], [154], 5850000, 2)
    assert sent["checkIn"] == "2026-10-20"


async def test_ot_host_by_room(client, api):
    api["GetRoomPdp"] = {"roomInfo": {"hostId": 1626813}}
    api["GetUserPublicProfileInfo"] = fixture("host_profile.json")
    api["GetHostRoomBox"] = fixture("host_rooms.json")
    api["GetHostRoomsComments"] = fixture("host_reviews.json")
    data = (
        await client.call_tool("ot_host", {"room_id": 2508916, "rooms_limit": 5, "reviews_limit": 3})
    ).structured_content
    assert {
        k: data[k] for k in ("id", "name", "host_since", "response_minutes", "response_rate_pct", "acceptance_pct")
    } == {
        "id": 1626813,
        "name": "لیلا حلاجیان",
        "host_since": "2020-08-14",
        "response_minutes": 8,
        "response_rate_pct": 100,
        "acceptance_pct": 100,
    }
    assert data["active_rooms"] == 1 and data["url"] == "https://www.otaghak.com/profile/1626813/"
    room = data["rooms"][0]
    # host rooms send the total in personCapacity (4 = 3 + 1 extra)
    assert (room["id"], room["base_guests"], room["max_guests"], room["price_per_night"]) == (2508916, 3, 4, 3800000)
    assert len(data["reviews"]) == 3 and data["reviews"][0]["room_id"] == 2508916
    assert dict(api.sent("GetHostRoomBox")[0].url.params) == {"hostId": "1626813", "skip": "0", "take": "5"}
    assert api.sent("GetHostRoomsComments")[0].url.params["top"] == "3"


async def test_ot_host_unknown(client, api):
    api["GetUserPublicProfileInfo"] = lambda request: httpx.Response(500, json=SERVER_ERROR)
    result = await client.call_tool("ot_host", {"host_id": 1, "rooms_limit": 0, "reviews_limit": 0})
    assert result.is_error and "Unknown host id 1" in result.content[0].text


async def test_ot_host_needs_an_id(client, api):
    result = await client.call_tool("ot_host", {})
    assert result.is_error and not api.calls


def test_review_full_text_and_dropped_points_marker():
    from otaghak_mcp.room import review

    c = {"body": "x" * 500, "positivePoints": ["a", "b", "c", "d", "e"], "negativePoints": ["n"]}
    short, full = review(c), review(c, True)
    assert short["text"].endswith("[cut]") and short["pros"] == ["a", "b", "c"] and short["pros_more"] == 2
    assert "cons_more" not in short
    assert full["text"] == "x" * 500 and len(full["pros"]) == 5 and "pros_more" not in full
