import httpx
import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

QUOTE = {"room_id": 2508916, "check_in": "2026-10-20", "check_out": "2026-10-23"}


def quote_routes(api):
    api["GetRoomCalendarDetails"] = fixture("calendar_quote.json")
    api["SearchRooms"] = fixture("quote_search.json")


async def test_ot_room_calendar(client, api):
    api["GetRoomCalendarDetails"] = fixture("calendar.json")
    data = (
        await client.call_tool("ot_room_calendar", {"room_id": 2512254, "start": "2026-10-20", "end": "2026-10-23"})
    ).structured_content
    assert data["open_until"] == "2026-11-21" and data["available_days"] == 4
    assert data["cheapest_available"] == "2026-10-23"
    assert data["days"][1] == {
        "date": "2026-10-21",
        "jalali": "1405/07/29",
        "weekday": "Wed",
        "available": True,
        "price": 5000000,
        "price_before_discount": 5000000,
        "instant": True,
        "min_nights": 1,
        "max_nights": 30,
        "weekend": True,
        "holiday": False,
        "late_checkout": False,
    }
    assert data["days"][3]["holiday"] and data["days"][3]["jalali"] == "1405/08/01"
    assert "(roomId=2512254,startDate=2026-10-20,endDate=2026-10-23)" in api.calls[0].url.path


async def test_ot_room_calendar_defaults_and_limits(client, api):
    api["GetRoomCalendarDetails"] = fixture("calendar.json")
    await client.call_tool("ot_room_calendar", {"room_id": 2512254})
    assert "startDate=2026-10-04,endDate=2026-11-03" in api.calls[0].url.path  # today + 30 days
    await client.call_tool("ot_room_calendar", {"room_id": 2512254, "start": "2026-11-10", "end": "2026-12-30"})
    assert "startDate=2026-11-10,endDate=2026-11-21" in api.calls[1].url.path  # cut at the open calendar
    result = await client.call_tool("ot_room_calendar", {"room_id": 2512254, "start": "2026-12-01"})
    assert result.is_error and "open only up to 2026-11-21" in result.content[0].text


async def test_ot_room_calendar_unknown_room(client, api):
    api["GetRoomCalendarDetails"] = {"value": []}
    result = await client.call_tool("ot_room_calendar", {"room_id": 1})
    assert result.is_error and "unknown id" in result.content[0].text


async def test_ot_price_quote_with_extra_guest(client, api):
    quote_routes(api)
    data = (await client.call_tool("ot_price_quote", QUOTE | {"guests": 4})).structured_content
    assert data["available"] and data["instant"]
    assert (data["base_guests"], data["max_guests"], data["extra_guests"]) == (3, 4, 1)
    assert data["nightly"] == [
        {"date": "2026-10-20", "price": 4000000, "price_before_discount": 5000000},
        {"date": "2026-10-21", "price": 4000000, "price_before_discount": 5000000},
        {"date": "2026-10-22", "price": 4250000, "price_before_discount": 5000000},
    ]
    # payable = totalPrice 12,249,999 rounded to 100, as the room page shows it
    assert data["nights_total"] == 12250000 and data["discount_total"] == 2750000
    # 400,000 per extra guest and night, with each night's discount: 320,000 + 320,000 + 340,000
    assert (data["extra_guest_price_per_night"], data["extra_guest_price_per_night_before_discount"]) == (
        326666,
        400000,
    )
    assert data["extra_guest_total"] == 980000
    assert data["total"] == 13230000 and data["late_checkout"] is None
    sent = body(api.sent("SearchRooms")[0])
    assert sent["roomIds"] == ["2508916"] and sent["person"] == 4 and sent["checkIn"] == "2026-10-20"


async def test_ot_price_quote_total_is_the_page_amount_not_the_calendar_sum(client, api):
    # Room 2413750, 20..23 Oct (live 2026-10-04): calendar nights add up to 67,921,000 (each discounted night is
    # rounded to 1,000), the room page charges totalPrice 67,920,600.
    calendar = fixture("calendar_quote.json")
    for d, (price, before) in zip(
        calendar["value"], [(20257000, 25970000), (23832000, 29790000), (23832000, 29790000)], strict=False
    ):
        d |= {"price": price, "beforeDiscountPrice": before}
    api["GetRoomCalendarDetails"] = calendar
    search = fixture("quote_search.json")
    search["rooms"][0] |= {"totalPrice": 67920600.0, "basePersonCount": 3}
    api["SearchRooms"] = search
    data = (await client.call_tool("ot_price_quote", QUOTE | {"guests": 2})).structured_content
    assert sum(n["price"] for n in data["nightly"]) == 67921000
    assert data["nights_total"] == data["total"] == 67920600


async def test_ot_price_quote_base_guests(client, api):
    quote_routes(api)
    data = (await client.call_tool("ot_price_quote", QUOTE | {"guests": 2})).structured_content
    assert data["extra_guests"] == 0 and data["total"] == 12250000


async def test_ot_price_quote_over_capacity(client, api):
    quote_routes(api)
    data = (await client.call_tool("ot_price_quote", QUOTE | {"guests": 5})).structured_content
    assert not data["available"] and data["reason"] == "The room takes at most 4 guests (3 included + 1 extra)."
    assert "total" not in data


async def test_ot_price_quote_blocked_night(client, api):
    quote_routes(api)
    calendar = fixture("calendar_quote.json")
    calendar["value"][1]["isBlocked"] = True
    api["GetRoomCalendarDetails"] = calendar
    api["SearchRooms"] = {"count": 0, "rooms": []}
    data = (await client.call_tool("ot_price_quote", QUOTE | {"guests": 2})).structured_content
    assert not data["available"] and "2026-10-21" in data["reason"]


async def test_ot_price_quote_min_nights(client, api):
    quote_routes(api)
    calendar = fixture("calendar_quote.json")
    calendar["value"][0]["minNights"] = 5
    api["GetRoomCalendarDetails"] = calendar
    data = (await client.call_tool("ot_price_quote", QUOTE | {"guests": 2})).structured_content
    assert not data["available"] and "5-7 nights" in data["reason"]


async def test_ot_price_quote_late_checkout(client, api):
    quote_routes(api)
    calendar = fixture("calendar_quote.json")
    calendar["value"][3]["hasLateCheckout"] = True  # the check-out day
    api["GetRoomCalendarDetails"] = calendar
    api["GetLateCheckoutInfo"] = fixture("late_info.json")
    data = (
        await client.call_tool("ot_price_quote", QUOTE | {"guests": 2, "late_checkout_hours": 4})
    ).structured_content
    assert data["late_checkout"] == {"available": True, "hours": 4, "price": 500000}
    assert data["total"] == 12250000 + 500000


async def test_ot_price_quote_late_checkout_closed_that_day(client, api):
    quote_routes(api)
    api["GetLateCheckoutInfo"] = fixture("late_info.json")
    data = (
        await client.call_tool("ot_price_quote", QUOTE | {"guests": 2, "late_checkout_hours": 4})
    ).structured_content
    assert data["available"] and data["total"] == 12250000  # the quote stays, without late checkout
    assert data["late_checkout"] == {
        "available": False,
        "hours": 4,
        "reason": "Late checkout is not open on this check-out day.",
    }


async def test_ot_price_quote_rejects_past_dates(client, api):
    result = await client.call_tool(
        "ot_price_quote", {"room_id": 2508916, "check_in": "2026-09-01", "check_out": "2026-09-03", "guests": 2}
    )
    assert result.is_error and "in the past" in result.content[0].text and not api.calls


async def test_ot_holidays(client, api):
    api["GetCalendar"] = fixture("holidays.json")
    data = (await client.call_tool("ot_holidays", {"start": "2026-10-04", "end": "2026-12-31"})).structured_content
    assert data["start_jalali"] == "1405/07/12" and data["end_jalali"] == "1405/10/10"
    assert data["holidays"][0] == {"date": "2026-10-09", "jalali": "1405/07/17", "weekday": "Friday"}
    assert data["holidays"][-1] == {"date": "2026-12-23", "jalali": "1405/10/02", "weekday": "Wednesday"}
    assert "flags_known_until" not in data  # the last holiday in range is not how far flags are known
    assert dict(api.calls[0].url.params) == {"startDate": "2026-10-04", "endDate": "2027-01-01"}  # end is exclusive


async def test_ot_holidays_range_limit(client, api):
    result = await client.call_tool("ot_holidays", {"start": "2026-10-04", "end": "2028-01-01"})
    assert result.is_error and not api.calls


async def test_ot_late_checkout(client, api):
    api["GetLateCheckoutInfo"] = fixture("late_info.json")
    api["GetRoomCalendarDetails"] = fixture("late_calendar.json")
    data = (
        await client.call_tool("ot_late_checkout", {"room_id": 2485328, "check_out": "2026-10-23"})
    ).structured_content
    assert data == {
        "room_id": 2485328,
        "offered": True,
        "min_hours": 4,
        "max_hours": 4,
        "base_price": 500000,
        "extra_hour_price": 100000,
        "prices": {"4": 500000},
        "check_out": "2026-10-23",
        "available_on_check_out": False,
    }
    assert body(api.sent("GetLateCheckoutInfo")[0]) == {"roomId": 2485328}
    assert "startDate=2026-10-22,endDate=2026-10-23" in api.sent("GetRoomCalendarDetails")[0].url.path


async def test_ot_late_checkout_without_day_warns(client, api):
    api["GetLateCheckoutInfo"] = fixture("late_info.json")
    data = (await client.call_tool("ot_late_checkout", {"room_id": 2485328})).structured_content
    assert data["offered"] and "pass check_out" in data["note"] and not api.sent("GetRoomCalendarDetails")


async def test_ot_late_checkout_not_offered(client, api):
    api["GetLateCheckoutInfo"] = fixture("late_info_none.json")
    data = (await client.call_tool("ot_late_checkout", {"room_id": 2512254})).structured_content
    assert not data["offered"] and data["prices"] == {} and data["max_hours"] is None


async def test_ot_late_checkout_unknown_room(client, api):
    api["GetLateCheckoutInfo"] = lambda request: httpx.Response(200, content=b"null")  # the API's unknown-room answer
    result = await client.call_tool("ot_late_checkout", {"room_id": 1})
    assert result.is_error and "Unknown room id 1" in result.content[0].text
