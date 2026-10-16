"""Pricing tools: nightly calendar, exact stay quote, holidays, late checkout."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import CORE, ODATA, fetch
from .registry import tool
from .room import RoomId
from .search import check_date, drop_empty, horizon, jalali, room_url, stay, today

LateHours = Annotated[int | None, Field(ge=1, le=10, description="Late checkout hours to add (offered rooms), e.g. 4.")]


def jalali_str(d: date) -> str:
    return "{}/{:02d}/{:02d}".format(*jalali(d))


@tool("Room calendar")
async def ot_room_calendar(
    room_id: RoomId,
    start: Annotated[date | None, Field(description="First day YYYY-MM-DD, e.g. '2026-10-20'. Default: today.")] = None,
    end: Annotated[
        date | None, Field(description="Last day (inclusive), e.g. '2026-11-20'. Default: start + 30 days.")
    ] = None,
) -> dict[str, Any]:
    """Get a room's calendar day by day: nightly price after and before discount (Toman), free or booked,
    instant booking, min and max nights when arriving that day, weekend, public holiday, late checkout.

    A night is priced on its arrival day. Absent keys: price_before_discount = no discount, instant /
    weekend / holiday / late_checkout = no. Use to find free dates, cheap nights or allowed stay lengths.
    Next: ot_price_quote for a stay.
    """
    t = today()
    start, last = start or t, horizon(t)
    end = min(end or start + timedelta(days=30), last)
    for d, name in ((start, "start"), (end, "end")):
        check_date(d, name)
    if start < t:
        raise ToolError(f"start {start} is in the past (today in Iran is {t}).")
    if start > last:
        raise ToolError(f"The calendar is open only up to {last} (end of next Jalali month).")
    if end <= start:
        end = start + timedelta(days=1)  # the API needs end after start
    days = await calendar(room_id, start, end)
    out = [
        drop_empty(
            {
                "date": d["date"][:10],
                "jalali": jalali_str(date.fromisoformat(d["date"][:10])),
                "weekday": date.fromisoformat(d["date"][:10]).strftime("%a"),
                "available": not d.get("isBlocked"),
                "price": d.get("price"),
                "price_before_discount": d.get("beforeDiscountPrice")
                if d.get("beforeDiscountPrice") != d.get("price")
                else None,
                "instant": d.get("isInstant") or None,
                "min_nights": d.get("minNights"),
                "max_nights": d.get("maxNights"),
                "weekend": d.get("eventDateType") == "Weekend" or None,
                "holiday": d.get("isPublicHoliday") or None,
                "late_checkout": d.get("hasLateCheckout") or None,
            }
        )
        for d in days
    ]
    free = [d for d in out if d["available"]]
    return {
        "room_id": room_id,
        "open_until": str(last),
        "available_days": len(free),
        "cheapest_available": min(free, key=lambda d: d["price"])["date"] if free else None,
        "days": out,
    }


@tool("Exact stay price")
async def ot_price_quote(
    room_id: RoomId,
    check_in: Annotated[date, Field(description="Arrival day YYYY-MM-DD, e.g. '2026-10-20'.")],
    check_out: Annotated[date, Field(description="Departure day, e.g. '2026-10-23' (max 20 nights).")],
    guests: Annotated[int, Field(ge=1, le=50, description="Guests, e.g. 4.")],
    late_checkout_hours: LateHours = None,
) -> dict[str, Any]:
    """Get the amount to pay (Toman) for a room, dates and guests: nights_total (the room page's
    payable amount for the base guests), the extra-guest charge, optional late checkout, and the total.
    `nightly` is the calendar breakdown (each discounted night is rounded to 1,000, so it can add up to
    a little more than nights_total; price_before_discount is absent when there is no discount).

    Also checks that every night is free, the stay length is allowed and the room takes that many
    guests (available=false with a reason otherwise). Late checkout that cannot be booked keeps the
    quote, with late_checkout.available=false. Use before the user books on otaghak.com.
    """
    nights = stay(check_in, check_out)
    calls = [
        calendar(room_id, check_in, check_out),
        fetch(
            f"{CORE}/api/v3/RoomSearch/SearchRooms",
            method="POST",
            json={
                "roomIds": [str(room_id)],
                "checkIn": str(check_in),
                "checkOut": str(check_out),
                "person": guests,
                "take": 1,
            },
            expect=dict,
        ),
    ]
    if late_checkout_hours:
        calls.append(late_info(room_id))
    days, found, *late = await asyncio.gather(*calls)
    by_day = {d["date"][:10]: d for d in days}
    stay_days = [by_day.get(str(check_in + timedelta(days=i))) for i in range(nights)]
    out: dict[str, Any] = {
        "room_id": room_id,
        "check_in": str(check_in),
        "check_out": str(check_out),
        "nights": nights,
        "guests": guests,
        "url": room_url(room_id, check_in, check_out),
    }

    def unavailable(reason: str) -> dict[str, Any]:
        return out | {"available": False, "reason": reason}

    if None in stay_days:
        return unavailable("Some nights are not in the room's calendar yet.")
    blocked = [d["date"][:10] for d in stay_days if d.get("isBlocked")]
    if blocked:
        return unavailable(f"Booked or closed nights: {', '.join(blocked)}. See ot_room_calendar or ot_similar_rooms.")
    first = stay_days[0]
    if not first.get("minNights", 0) <= nights <= (first.get("maxNights") or nights):
        return unavailable(
            f"Arriving {check_in} the stay must be {first.get('minNights')}-{first.get('maxNights')} nights."
        )
    room = next(iter(found.get("rooms") or []), None)
    if not room:
        return unavailable("Otaghak does not offer this room for these dates.")
    max_guests, extra_cap = room.get("personCapacity") or 0, room.get("extraPersonCapacity") or 0
    base = room.get("basePersonCount") or max_guests - extra_cap
    out |= {"base_guests": base, "max_guests": max_guests}
    if guests > max_guests:
        return unavailable(f"The room takes at most {max_guests} guests ({base} included + {extra_cap} extra).")

    extra = max(0, guests - base)
    # The room page's payable amount is `totalPrice` rounded to 100 (14,499,999 -> 14,500,000; 67,920,600 stays);
    # the calendar sum is not (each discounted night is rounded to 1,000: 67,921,000).
    nights_total = int(round(room.get("totalPrice") or sum(d["price"] for d in stay_days), -2))
    # The extra-guest charge gets each night's discount too (the site's quote: 400,000 x 0.8 = 320,000).
    extra_price = room.get("extraPersonPriceAfterDiscountAverage") or 0
    extra_total = int(round(extra_price * extra * nights, -2))
    late_out = None
    if late_checkout_hours:
        try:
            late_out = {"available": True} | late_price(late[0], late_checkout_hours, by_day.get(str(check_out)))
        except ToolError as e:
            late_out = {"available": False, "hours": late_checkout_hours, "reason": str(e)}
    total = nights_total + extra_total + ((late_out or {}).get("price") or 0)
    return out | {
        "available": True,
        "instant": all(d.get("isInstant") for d in stay_days),
        "nightly": [
            drop_empty(
                {
                    "date": d["date"][:10],
                    "price": d["price"],
                    "price_before_discount": d.get("beforeDiscountPrice")
                    if d.get("beforeDiscountPrice") != d["price"]
                    else None,
                }
            )
            for d in stay_days
        ],
        "nights_total": nights_total,
        "discount_total": sum((d.get("beforeDiscountPrice") or d["price"]) - d["price"] for d in stay_days),
        "extra_guests": extra,
        "extra_guest_price_per_night": round(extra_price),
        "extra_guest_price_per_night_before_discount": room.get("extraPersonPriceBeforeDiscountAverage"),
        "extra_guest_total": extra_total,
        "late_checkout": late_out,
        "total": total,
    }


@tool("Holidays and weekdays")
async def ot_holidays(
    start: Annotated[date, Field(description="First day YYYY-MM-DD, e.g. '2026-10-20'.")],
    end: Annotated[date, Field(description="Last day (inclusive), e.g. '2026-12-31'; max a year after start.")],
) -> dict[str, Any]:
    """List Iranian public holidays (Fridays included) in a date range, with Jalali dates and weekdays.

    Holiday flags are filled only about 7 weeks ahead: later days show no holidays, which means
    unknown, not working days. Use to plan around long weekends; weekend nights (Wed, Thu) and holidays
    usually cost more (see ot_room_calendar).
    """
    for d, name in ((start, "start"), (end, "end")):
        check_date(d, name)
    if end < start or (end - start).days > 366:
        raise ToolError("end must be on or after start and at most a year later.")
    days = await fetch(
        f"{CORE}/api/v1/Calendar/GetCalendar", {"startDate": str(start), "endDate": str(end + timedelta(days=1))}
    )
    holidays = [d for d in days or [] if d.get("isPublicHoliday")]
    return {
        "start": str(start),
        "end": str(end),
        "start_jalali": jalali_str(start),
        "end_jalali": jalali_str(end),
        "holidays": [
            {
                "date": d["date"][:10],
                "jalali": jalali_str(date.fromisoformat(d["date"][:10])),
                "weekday": date.fromisoformat(d["date"][:10]).strftime("%A"),
            }
            for d in holidays
        ],
    }


@tool("Late checkout")
async def ot_late_checkout(
    room_id: RoomId,
    check_out: Annotated[date | None, Field(description="Departure day to check, e.g. '2026-10-23'.")] = None,
) -> dict[str, Any]:
    """Check whether a room offers late checkout, for how many hours and at what price (Toman, a unit
    not confirmed on the site), and with check_out whether it can be booked on that departure day
    (offered rooms are often closed for it on a given day).

    Use when the user wants to leave later than the check-out time. ot_price_quote adds it to a
    total with late_checkout_hours.
    """
    calls = [late_info(room_id)]
    if check_out:
        check_date(check_out, "check_out")
        t, last = today(), horizon(today())
        if not t < check_out <= last:
            raise ToolError(f"check_out must be after today ({t}) and on or before {last} (end of the open calendar).")
        calls.append(calendar(room_id, check_out - timedelta(days=1), check_out))
    info, *cal = await asyncio.gather(*calls)
    offered = bool(info.get("maxLateCheckoutHours"))
    out: dict[str, Any] = {
        "room_id": room_id,
        "offered": offered,
        "min_hours": info.get("minLateCheckoutHours") if offered else None,
        "max_hours": info.get("maxLateCheckoutHours") if offered else None,
        "base_price": info.get("lateCheckoutBasePrice") if offered else None,
        "extra_hour_price": info.get("lateCheckoutExtraHourlyPrice") if offered else None,
        "prices": {
            str(h): late_price(info, h)["price"]
            for h in range(info.get("minLateCheckoutHours") or 0, (info.get("maxLateCheckoutHours") or 0) + 1)
        }
        if offered
        else {},
    }
    if check_out:
        day = next((d for d in cal[0] if d["date"][:10] == str(check_out)), None)
        out["check_out"] = str(check_out)
        out["available_on_check_out"] = bool(offered and day and day.get("hasLateCheckout"))
    elif offered:
        out["note"] = "Offered does not mean bookable: pass check_out to see whether that day allows it."
    return out


async def calendar(room_id: int, start: date, end: date) -> list[dict[str, Any]]:
    """Calendar days start..end (both inclusive; end must be after start). Raises for an unknown room."""
    data = await fetch(
        f"{ODATA}/RoomCalendarDetail/GetRoomCalendarDetails(roomId={room_id},startDate={start},endDate={end})"
    )
    days = (data or {}).get("value") or []
    if not days:
        raise ToolError(f"No calendar for room {room_id}: unknown id, or the dates are past the open calendar.")
    return days


async def late_info(room_id: int) -> dict[str, Any]:
    info = await fetch(f"{CORE}/api/v3/Bookings/GetLateCheckoutInfo", method="POST", json={"roomId": room_id})
    if not info:  # the API answers `null` for an unknown room
        raise ToolError(f"Unknown room id {room_id}.")
    return info


def late_price(info: dict[str, Any], hours: int, check_out_day: dict[str, Any] | None = None) -> dict[str, Any]:
    """Late checkout price for `hours`, as the site computes it; raises when not offered or out of range."""
    lo, hi = info.get("minLateCheckoutHours") or 0, info.get("maxLateCheckoutHours") or 0
    if not hi:
        raise ToolError("This room does not offer late checkout.")
    if not lo <= hours <= hi:
        raise ToolError(f"Late checkout is {lo}-{hi} hours for this room.")
    if check_out_day is not None and not check_out_day.get("hasLateCheckout"):
        raise ToolError("Late checkout is not open on this check-out day.")
    price = (info.get("lateCheckoutBasePrice") or 0) + max(0, hours - lo) * (
        info.get("lateCheckoutExtraHourlyPrice") or 0
    )
    return {"hours": hours, "price": round(price)}
