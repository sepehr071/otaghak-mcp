"""Search tools: place lookup, room search, filter codes, destinations, deals; shared date and card helpers."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import CACHE_SECONDS, CORE, ODATA, WWW, ApiError, fetch
from .registry import tool

IRAN = timezone(timedelta(hours=3, minutes=30))  # no DST since 2022
MAX_NIGHTS = 20
PHOTO = "https://cdn.otaghak.com/otg-images-new"

FullText = Annotated[bool, Field(description="Return long texts in full instead of clipped.")]
Slug = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{1,60}$")]
CheckIn = Annotated[
    date | None, Field(description="Arrival day YYYY-MM-DD, e.g. '2026-10-20' (not past, in the open calendar).")
]
CheckOut = Annotated[date | None, Field(description="Departure day YYYY-MM-DD, e.g. '2026-10-23' (max 20 nights).")]
Guests = Annotated[int | None, Field(ge=1, le=50, description="Guests, e.g. 4; smaller rooms are left out.")]
Limit = Annotated[int, Field(ge=1, le=50, description="Rooms per page.")]
Page = Annotated[int, Field(ge=0, le=100, description="Zero-based page.")]

SORTS = {
    "recommended": "OtaghakSuggestions",
    "cheapest": "MinPricePriority",
    "most_expensive": "MaxPricePriority",
    "top_rated": "MostRated",
    "newest": "MostRecent",
    "most_discounted": "MostDiscounted",
}
Sort = Literal["recommended", "cheapest", "most_expensive", "top_rated", "newest", "most_discounted"]
RENT_TYPES = {"whole_place": "Dedicate", "semi_private": "SemiDedicate", "shared": "Shared"}
RENT_NAMES = {v: k for k, v in RENT_TYPES.items()}
LIST_FIELDS = ("attribute", "attributeValues", "tagCodes", "ruleValues")


@tool("Find place or room code")
async def ot_find_place(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=60,
            description="City/province name (Persian or slug), e.g. 'رامسر', or a room code, e.g. '2508916'.",
        ),
    ],
) -> dict[str, Any]:
    """Turn a place name into the city / province slugs that ot_search_rooms needs, or a room code into its room.

    Cities come with their listing count (undated). A province `listings` number is not a search count.
    Room names are not searchable here: use ot_search_rooms with `name`. Next: ot_search_rooms, or
    ot_room for a room hit.
    """
    data = await fetch(f"{CORE}/api/v1/Search/GetSearchResult", {"input": query.strip()})
    places = []
    for p in data or []:
        kind = {"state": "province"}.get(p.get("typeCode"), p.get("typeCode"))
        item: dict[str, Any] = {"kind": kind, "name": p.get("name")}
        if kind == "room":
            item |= {"room_id": p.get("roomId"), "city": p.get("cityCode"), "max_guests": p.get("personCapacity")}
        else:
            item |= {"slug": p.get("cityCode") if kind == "city" else p.get("stateCode"), "listings": p.get("count")}
        item |= {"province": p.get("state"), "province_slug": p.get("stateCode"), "url": f"{WWW}/{p.get('url')}/"}
        places.append(item)
    if not places:
        return {
            "places": [],
            "hint": "Only cities, provinces and room codes match. Landmarks (a shrine, a beach, a square) are "
            "districts: ot_search_filters(city) -> districts -> ot_search_rooms district_id.",
        }
    return {"places": places}


@tool("Search stays")
async def ot_search_rooms(
    city: Annotated[
        list[Slug] | None,
        Field(max_length=5, description="City slugs from ot_find_place, e.g. ['ramsar'] (any of them)."),
    ] = None,
    province: Annotated[Slug | None, Field(description="Province slug, e.g. 'mazandaran'.")] = None,
    collection: Annotated[
        Slug | None, Field(description="Collection code from ot_destinations, e.g. 'beach', 'villapool'.")
    ] = None,
    city_tag: Annotated[
        Slug | None, Field(description="City theme slug with `city`, e.g. 'cottage' (ot_travel_guide).")
    ] = None,
    check_in: CheckIn = None,
    check_out: CheckOut = None,
    guests: Guests = None,
    sort: Annotated[
        Sort, Field(description="Order; with dates, cheapest re-sorts each page by stay_total.")
    ] = "recommended",
    min_price: Annotated[int | None, Field(ge=0, description="Min nightly Toman after discount, e.g. 3000000.")] = None,
    max_price: Annotated[int | None, Field(ge=0, description="Max nightly Toman after discount, e.g. 8000000.")] = None,
    bedrooms: Annotated[int | None, Field(ge=1, le=20, description="Minimum bedrooms, e.g. 2.")] = None,
    min_rating: Annotated[Literal[3, 4, 5] | None, Field(description="Minimum rating.")] = None,
    instant: Annotated[bool, Field(description="Only instant booking.")] = False,
    prime: Annotated[bool, Field(description="Only 'prime' rooms.")] = False,
    room_types: Annotated[
        list[int] | None, Field(max_length=12, description="Room type ids from ot_search_filters, e.g. [138] villa.")
    ] = None,
    rent_types: Annotated[
        list[Literal["whole_place", "semi_private", "shared"]] | None, Field(description="Rent type.")
    ] = None,
    filters: Annotated[
        list[str] | None,
        Field(
            max_length=10,
            description="Codes from ot_search_filters exactly as returned, e.g. ['ruleValues=10101|10103|10102'] (pets). All must match.",
        ),
    ] = None,
    district_id: Annotated[
        int | None, Field(ge=1, description="Landmark district id from ot_search_filters, with `city`, e.g. 230.")
    ] = None,
    name: Annotated[str | None, Field(min_length=2, max_length=60, description="Room name words, e.g. 'نرگس'.")] = None,
    include_nearby: Annotated[bool, Field(description="Also rooms around the city.")] = False,
    page: Page = 0,
    limit: Limit = 10,
) -> dict[str, Any]:
    """Search Otaghak stays (villas, cottages, apartments, eco-lodges) in a city, province or collection.

    With check_in/check_out every room is available on those dates and carries the stay price:
    price_per_night (average after discount) and stay_total for `guests` (incl. extra-guest charges;
    estimate, can be a few Toman off). Without dates prices are undated starting prices. Get slugs
    from ot_find_place, filter codes from ot_search_filters. Next: ot_room for details, ot_price_quote
    for the exact total, ot_room_calendar for other dates.
    """
    if not (city or province or collection):
        raise ToolError("Give a scope: city, province or collection (slugs from ot_find_place or ot_destinations).")
    nights = stay(check_in, check_out)
    body: dict[str, Any] = {
        "cities": city or [],
        "stateCodes": [province] if province else [],
        "boxCode": collection or "",
        "sortingType": SORTS[sort],
        "checkIn": str(check_in) if nights else None,
        "checkOut": str(check_out) if nights else None,
        "person": guests,
        "minPrice": min_price,
        "maxPrice": max_price,
        "bedRoom": bedrooms,
        "minRate": min_rating,
        "instant": instant,
        "prime": prime,
        "type": room_types or [],
        "rentTypes": [RENT_TYPES[r] for r in rent_types or []],
        "aroundLocations": include_nearby,
        "cityDistrictId": district_id,
        "filter": name or "",
        "skip": page * limit,
        "take": limit,
    }
    if city_tag:
        body["cityTag"] = city_tag
    for f in filters or []:
        field, _, value = f.partition("=")
        if field not in LIST_FIELDS or not value.strip():
            raise ToolError(
                f"Unknown filter {f!r}. Copy codes from ot_search_filters, e.g. 'attribute=1201|1202|1203|1401'."
            )
        body.setdefault(field, []).append(value.strip())
    try:
        data = await fetch(f"{CORE}/api/v3/RoomSearch/SearchRooms", method="POST", json=body, expect=dict)
    except ApiError as e:
        if e.status == 404:  # an unknown city, province, collection or city_tag slug
            sent = {"city": city, "province": province, "collection": collection, "city_tag": city_tag}
            scope = ", ".join(f"{k}={v!r}" for k, v in sent.items() if v)
            raise ApiError(
                f"Unknown place ({scope}). Check slugs with ot_find_place (cities, provinces) "
                "or ot_destinations (collections).",
                404,
            ) from e
        raise
    rooms = [card(r, guests, check_in, check_out) for r in data.get("rooms") or []]
    if sort == "most_discounted":  # the API order is only roughly by percent
        rooms.sort(key=lambda r: -r["discount_pct"])
    elif sort == "cheapest" and nights:  # the API sorts by nightly price, ignoring extra-guest charges
        rooms.sort(key=lambda r: r["stay_total"])
    return {
        "total": data.get("count"),
        "page": page,
        "dated": bool(nights),
        "nights": nights or None,
        "lowest_price_per_night": data.get("minPrice") or None,
        "rooms": rooms,
    }


@tool("Search filter codes")
async def ot_search_filters(
    city: Annotated[Slug | None, Field(description="City slug, e.g. 'ramsar'; also returns its districts.")] = None,
    province: Annotated[Slug | None, Field(description="Province slug, e.g. 'mazandaran'.")] = None,
    collection: Annotated[Slug | None, Field(description="Collection code, e.g. 'beach'.")] = None,
) -> dict[str, Any]:
    """List the search filters of a city, province or collection (or the whole site) with the codes ot_search_rooms takes.

    Returns the nightly price range (Toman), room types (ids for room_types), filter codes for
    `filters` (pool, jacuzzi, parking, heating, pets, parties, beach / jungle area, ...), and for a
    city its districts and landmarks (district_id). Use when the user asks for an amenity, a rule,
    a room type or a place near a landmark.
    """
    params = {"boxCode": collection or "", "cityCodes": city or "", "stateCodes": province or ""}
    calls = [fetch(f"{CORE}/api/v3/RoomSearch/GetSearchFilter", params, ttl=CACHE_SECONDS)]
    if city:
        calls.append(fetch(f"{CORE}/api/v1/CityDistrict/GetAllByCityName", {"cityName": city}, ttl=CACHE_SECONDS))
    try:
        data, *rest = await asyncio.gather(*calls)
    except ApiError as e:
        if e.status == 500:
            raise ApiError(
                f"Unknown place {city or province or collection!r}. Check the slug with ot_find_place.", 500
            ) from e
        raise
    out: dict[str, Any] = {"price_range": None, "room_types": [], "filters": []}
    for s in (data or {}).get("sections") or []:
        kind, children = s.get("sectionName"), s.get("children") or []
        if kind == "Price":
            bounds = {c.get("fieldName"): c.get("defaultValue") for c in children}
            out["price_range"] = {"min": bounds.get("minPrice"), "max": bounds.get("maxPrice")}
        elif kind == "RoomTypes":
            out["room_types"] = [{"id": c.get("value"), "name": c.get("title")} for c in children]
        elif kind not in ("BookingItems", "BedRoom", "MinRate", "RentType"):  # those are plain parameters
            for c in children:
                for item, label in [(c, c.get("title"))] + [
                    (g, f"{c.get('title')}: {g.get('title')}") for g in c.get("children") or []
                ]:
                    if item.get("fieldName") in LIST_FIELDS and item.get("value"):
                        out["filters"].append(
                            {
                                "name": (label or "").strip(),
                                "group": s.get("title"),
                                "code": f"{item['fieldName']}={str(item['value']).strip()}",
                            }
                        )
    if rest:
        out["districts"] = [
            {
                "id": d.get("id"),
                "name": d.get("faName"),
                "en_name": (d.get("enName") or "").strip(),
                "type": d.get("districtType"),
                "radius_km": d.get("radius"),
            }
            for d in rest[0] or []
        ]
    return out


@tool("Popular destinations and collections")
async def ot_destinations(
    collection: Annotated[Slug | None, Field(description="Collection code to list its cities, e.g. 'beach'.")] = None,
) -> dict[str, Any]:
    """Popular cities and collections (themed landing pages: discounted, jungle villas, villas with pool, ...).

    Use when the user has no destination yet, or to find collection codes for ot_search_rooms
    `collection`. With `collection`, also lists the cities that collection covers (city slugs).
    """
    calls = [fetch(f"{CORE}/api/v1/Search/GetSearchSuggestion", expect=dict, ttl=CACHE_SECONDS)]
    if collection:
        calls.append(fetch(f"{ODATA}/RoomCityLanding/ListCityLanding(boxName='{collection}')", ttl=CACHE_SECONDS))
    data, *rest = await asyncio.gather(*calls)
    out: dict[str, Any] = {
        "cities": [
            {
                "slug": c.get("enName"),
                "name": c.get("faName"),
                "province": c.get("state"),
                "province_slug": c.get("stateCode"),
                "listings": c.get("roomCount"),
            }
            for c in data.get("popularCities") or []
        ],
        "collections": [
            {"code": t.get("enName"), "name": t.get("faName"), "listings": t.get("roomCount")}
            for t in data.get("popularTags") or []
        ],
        "more_collections": "beach, jungle, villapool, villa-north-iran, eco, luxury, economy, cottage, desert, "
        "pool, pet, honey-moon, nice-view, instant, otaghak-prime, lastsecond, weekend-tehran, foreign-room",
    }
    if rest:
        cities = (rest[0] or {}).get("value") or []
        if not cities:
            raise ToolError(f"Unknown collection {collection!r} (no cities). Pick one from `collections`.")
        out["collection_cities"] = [{"slug": c.get("code"), "name": c.get("faName")} for c in cities]
    return out


@tool("Deals and discounts")
async def ot_deals(
    city: Annotated[Slug | None, Field(description="City slug, e.g. 'ramsar'. Default: whole site.")] = None,
    check_in: CheckIn = None,
    check_out: CheckOut = None,
    guests: Guests = None,
    limit: Annotated[int, Field(ge=1, le=30, description="Rooms per list.")] = 8,
) -> dict[str, Any]:
    """Current discounts: the home page's last-second deals and the most discounted stays (biggest percent first).

    last_second rooms are site-wide tonight-style deals shown 14:00-23:50 Iran time with undated
    prices; they are left out when `city` is given. most_discounted lists rooms of the 'discounted'
    collection (in `city` if given) that have a discount, sorted by percent; with dates the prices
    are for that stay. Next: ot_price_quote for the exact total.
    """
    nights = stay(check_in, check_out)
    body: dict[str, Any] = {
        "cities": [city] if city else [],
        "boxCode": "discounted",
        "sortingType": "MostDiscounted",
        "checkIn": str(check_in) if nights else None,
        "checkOut": str(check_out) if nights else None,
        "person": guests,
        "skip": 0,
        "take": limit,
    }
    calls = [fetch(f"{CORE}/api/v3/RoomSearch/SearchRooms", method="POST", json=body, expect=dict)]
    if not city:
        calls.append(fetch(f"{CORE}/api/v3/Home/GetHomePage", expect=list))
    found, *home = await asyncio.gather(*calls)
    # A discounted-collection room can have no discount on the asked dates.
    rooms = sorted(
        (c for c in (card(r, guests, check_in, check_out) for r in found.get("rooms") or []) if c["discount_pct"]),
        key=lambda r: -r["discount_pct"],
    )
    out: dict[str, Any] = {"most_discounted": rooms, "most_discounted_total": found.get("count"), "dated": bool(nights)}
    if city:
        return out
    section = next((s for s in home[0] if s.get("className") == "lastsecond"), None) or {}
    last = sorted(section.get("rooms") or [], key=lambda r: -(r.get("totalDiscountValue") or 0))[:limit]
    return out | {
        "last_second": [
            drop_empty(
                {
                    "id": r.get("roomId"),
                    "name": r.get("roomName"),
                    "type": r.get("roomTypeName"),
                    "city": r.get("cityFaName"),
                    "price_per_night": r.get("afterDiscount"),
                    "price_per_night_before_discount": r.get("basePrice"),
                    "discount_pct": round(r.get("totalDiscountValue") or 0),
                    "rating": rating(r.get("rate"), r.get("commentsCount")),
                    "reviews": r.get("commentsCount"),
                    "max_guests": r.get("totalPersonCapacity"),
                    "instant": any(b.get("name") == "instant" for b in r.get("badgeCodes") or []) or None,
                    "url": room_url(r.get("roomId")),
                }
            )
            for r in last
        ],
        "last_second_hours": f"{section.get('effectiveTimeFrom')}-{section.get('effectiveTimeTo')}"
        if section
        else None,
    }


def card(
    r: dict[str, Any], guests: int | None, check_in: date | None, check_out: date | None, from_search: bool = True
) -> dict[str, Any]:
    """Compact room card from a search-shaped room (search, similar rooms, host rooms)."""
    # A dated 1-night search answers totalNights 0: count the nights from the dates.
    nights = r.get("totalNights") or ((check_out - check_in).days if check_in and check_out else 0)
    cap, extra_cap = r.get("personCapacity") or 0, r.get("extraPersonCapacity") or 0
    # Search and host rooms send the total (base + extra) in `personCapacity`; similar rooms send the base count.
    base, max_guests = (r.get("basePersonCount") or cap - extra_cap, cap) if from_search else (cap, cap + extra_cap)
    out: dict[str, Any] = {
        "id": r.get("roomId"),
        "name": r.get("roomName"),
        "type": r.get("roomTypeName"),
        "city": r.get("cityFaName"),
        "province": r.get("stateFaName"),
        "rating": rating(r.get("rate"), r.get("commentsCount")),
        "reviews": r.get("commentsCount"),
        "base_guests": base,
        "max_guests": max_guests,
        "bedrooms": r.get("bedRoom"),
        "area_m2": r.get("area") or None,
        "rent_type": RENT_NAMES.get(r.get("rentType"), r.get("rentType")),
        "price_per_night": r.get("afterDiscountAverage") if nights else r.get("afterDiscount"),
        "price_per_night_before_discount": r.get("averagePrice") if nights else r.get("basePrice"),
        "discount_pct": round(r.get("totalDiscountValue") or 0, 1),
    }
    if nights:
        extra = min(max(0, (guests or 0) - base), extra_cap)
        out["stay_total"] = round(
            (r.get("totalPrice") or 0) + (r.get("extraPersonPriceAfterDiscountAverage") or 0) * extra * nights
        )
    out |= {
        "instant": r.get("isInstantBook") or None,  # absent = no
        "prime": r.get("isPrimeRoom") or None,
        "location": {"lat": r.get("latitude"), "lon": r.get("longitude")} if r.get("latitude") else None,
        "photo": photo_url((r.get("roomMediaTitles") or {}).get("mainImageTitle"), "X500"),
        "url": room_url(r.get("roomId"), check_in if nights else None, check_out if nights else None),
    }
    if out["price_per_night_before_discount"] == out["price_per_night"]:  # absent = no discount
        del out["price_per_night_before_discount"]
    return drop_empty(out)


def drop_empty(d: dict[str, Any], keep: tuple[str, ...] = ("rating",)) -> dict[str, Any]:
    """Drop None / empty list / empty dict values (absent = none); `keep` keys stay (`rating` null = not rated)."""
    return {k: v for k, v in d.items() if k in keep or v not in (None, [], {})}


def clip(text: str | None, n: int | None) -> str:
    """Strip and cut long third-party text to n characters, marking the cut (n None = no cut)."""
    text = (text or "").strip()
    return text[:n].rstrip() + "…[cut]" if n and len(text) > n else text


def rating(rate: Any, count: Any) -> float | None:
    """Ratings are 0-5 already; 0 or no ratings means not rated yet."""
    return round(rate, 1) if rate and count else None


def photo_url(file: str | None, size: str = "Large") -> str | None:
    """Room photo file name -> CDN URL (sizes Small, X500, Medium, Large)."""
    return f"{PHOTO}/{size}/{file}" if file and len(file) > 5 else None


def room_url(room_id: Any, check_in: date | None = None, check_out: date | None = None) -> str | None:
    if not room_id:
        return None
    dates = f"?checkIn={check_in}&checkOut={check_out}" if check_in and check_out else ""
    return f"{WWW}/room/{room_id}/{dates}"


# Dates: the API wants Gregorian dates inside the hosts' open calendars and answers 500 otherwise.


def today() -> date:
    return datetime.now(IRAN).date()


def jalali(d: date) -> tuple[int, int, int]:
    """Gregorian date -> Jalali (year, month, day)."""
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def from_jalali(jy: int, jm: int, jd: int) -> date | None:
    """Jalali -> Gregorian by scanning the Jalali year (it starts in March)."""
    start = date(jy + 621, 3, 1)
    for i in range(400):
        d = start + timedelta(days=i)
        if jalali(d) == (jy, jm, jd):
            return d
    return None


def horizon(t: date) -> date:
    """Last bookable day: the end of the next Jalali month (calendars are open up to it)."""
    _, month, _ = jalali(t)
    target = month % 12 + 1
    d = t
    while jalali(d)[1] != target:
        d += timedelta(days=1)
    while jalali(d + timedelta(days=1))[1] == target:
        d += timedelta(days=1)
    return d


def check_date(d: date, name: str) -> None:
    if d.year < 1700:
        g = from_jalali(d.year, d.month, d.day)
        hint = f" ({d.isoformat()} is {g.isoformat()})" if g else ""
        raise ToolError(f"{name} looks like a Jalali date. Send the Gregorian date YYYY-MM-DD{hint}.")


def stay(check_in: date | None, check_out: date | None, max_nights: int = MAX_NIGHTS) -> int:
    """Validate a stay and return its nights (0 = undated)."""
    if check_in is None and check_out is None:
        return 0
    if check_in is None or check_out is None:
        raise ToolError("Give both check_in and check_out (Gregorian YYYY-MM-DD), or neither.")
    check_date(check_in, "check_in")
    check_date(check_out, "check_out")
    t = today()
    if check_in < t:
        raise ToolError(f"check_in {check_in} is in the past (today in Iran is {t}).")
    if check_out <= check_in:
        raise ToolError("check_out must be after check_in (check_out is the departure day).")
    nights = (check_out - check_in).days
    if nights > max_nights:
        raise ToolError(f"Otaghak books at most {max_nights} nights per stay; this is {nights}. Split the stay.")
    h = horizon(t)
    if check_out > h:
        raise ToolError(
            f"Otaghak calendars are open only up to {h} (end of next Jalali month); check_out must be on or before it."
        )
    return nights
