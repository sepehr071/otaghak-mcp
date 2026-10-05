"""Room tools: room details, reviews, similar rooms, host profile."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import CORE, ODATA, WWW, ApiError, fetch
from .registry import tool
from .search import RENT_NAMES, CheckIn, CheckOut, Guests, card, photo_url, rating, room_url, stay

RoomId = Annotated[int, Field(ge=1, le=999_999_999, description="Room id (code), e.g. 2512254.")]

# Rating categories by the API's icon key.
CATEGORIES = {
    "vacuum": "cleanliness",
    "tax-check": "accuracy",
    "user-check": "check_in",
    "comment_heart": "host_communication",
    "cctv": "neighbourhood_safety",
    "map-marker-check": "value",
}
RECOMMENDS = {"Recommended": "yes", "NotSure": "not_sure", "NotRecommended": "no"}


@tool("Room details")
async def ot_room(
    room_id: RoomId,
    check_in: CheckIn = None,
    check_out: CheckOut = None,
    max_photos: Annotated[int, Field(ge=0, le=40, description="Photo URLs to return.")] = 8,
) -> dict[str, Any]:
    """Get one room: description, capacity, rooms and beds, amenities, house rules, check-in/out times,
    cancellation policy, host, nearby places, approximate location, photos and nightly price.

    With check_in/check_out the price is the average night for those dates, even when they are
    booked (ot_price_quote or ot_room_calendar tells whether they are free); without, an undated
    starting price. instant_on_some_nights is the room-level flag: instant booking can differ per night,
    so for given dates read `instant` in ot_price_quote or ot_room_calendar.
    Use after ot_search_rooms or ot_find_place. Next: ot_price_quote (exact total),
    ot_room_calendar (free nights), ot_reviews, ot_host (host response and acceptance rates),
    ot_similar_rooms.
    """
    nights = stay(check_in, check_out)
    params: dict[str, Any] = {"roomId": room_id}
    if nights:
        params |= {"checkInDate": str(check_in), "checkOutDate": str(check_out)}
    try:
        pdp, detail, amenities = await asyncio.gather(
            fetch(f"{CORE}/api/v3/Rooms/GetRoomPdp", params, expect=dict),
            fetch(f"{CORE}/api/v3/Rooms/GetRoomDetail", {"roomId": room_id}, expect=dict),
            fetch(f"{CORE}/api/v3/Rooms/GetRoomPdpAttributes", {"roomId": room_id}),
        )
    except ApiError as e:
        if e.status in (404, 500):  # unknown room: pdp 404, detail 500
            raise ApiError(
                f"Unknown room id {room_id}. Get ids from ot_search_rooms or ot_find_place.", e.status
            ) from e
        raise
    info, seo, crumb, price = (
        pdp["roomInfo"],
        pdp.get("seoInfo") or {},
        pdp.get("breadCrumb") or {},
        pdp.get("price") or {},
    )
    sections = {s.get("sectionType"): s for s in pdp.get("pdpSections") or []}

    def items(kind: str) -> list[dict[str, Any]]:
        return (sections.get(kind) or {}).get("items") or []

    times = {i.get("name"): re.findall(r"\d{1,2}:\d{2}", i.get("description") or "") for i in items("RoomTime")}
    check_in_window = next((t for k, t in times.items() if k and "ورود" in k), None) or [detail.get("checkIn")]
    cancel = sections.get("RoomCancelRuleType") or {}
    before = price.get("basePrice")
    after = price.get("afterDiscount") or before  # 0 means no discount
    media = [m.get("name") for m in (sections.get("Images") or {}).get("media") or []]
    location = sections.get("Map") or {}
    base, extra = info.get("personCount") or 0, info.get("extraPersonCount") or 0
    return {
        "id": info.get("roomId"),
        "name": info.get("roomName"),
        "url": room_url(info.get("roomId"), check_in, check_out),
        "active": info.get("isActive"),
        "type": crumb.get("roomType"),
        "city": crumb.get("cityName"),
        "city_slug": crumb.get("cityEnName"),
        "province": crumb.get("stateName"),
        "province_slug": crumb.get("stateEnName"),
        "description": (info.get("description") or "").strip()[:1500],
        "base_guests": base,
        "max_guests": base + extra,
        "bedrooms": detail.get("bedRoom"),
        "beds": detail.get("bedCount"),
        "area_m2": seo.get("area"),
        "rent_type": RENT_NAMES.get(seo.get("rentType"), seo.get("rentType")),
        "about": {i.get("name"): i.get("description") for i in items("AboutRoom") if i.get("description")},
        "rating": rating(seo.get("rate"), seo.get("rateCount")),
        # seo `commentCount` can be twice the written reviews (16 vs 8 for 2510831): use the rating count only.
        "rating_count": seo.get("rateCount"),
        "successful_bookings": seo.get("successfulBookingCount"),
        "instant_on_some_nights": seo.get("isInstant"),  # room flag; per night it can be off (2388753)
        "prime": seo.get("isPrime"),
        "last_second_discount": seo.get("isLastSecondDiscount"),
        "price": {
            "dated": bool(nights),
            "price_per_night": after,
            "price_per_night_before_discount": before,
            "discount_pct": round(price.get("totalDiscountValue") or 0, 1),
            "extra_guest_per_night_before_discount": price.get("extraPersonPrice"),
        },
        "check_in": "-".join(check_in_window),
        "check_out": detail.get("checkOut"),
        "rules": [i.get("name") for i in items("RoomRules") + items("RoomHostRule") if i.get("name")],
        "documents_required": detail.get("authenticationDocuments") or None,
        "child_rule": detail.get("roomChildRateRule") or None,
        "cancel_policy": {
            "type": detail.get("cancelRuleType"),
            "title": cancel.get("cancelRuleTitle"),
            "guest_penalties": [
                {"when": i.get("title"), "penalty": i.get("description")} for i in cancel.get("items") or []
            ],
        },
        "amenities": {
            g.get("name"): [
                f"{i.get('name')} ({i['description'].strip()})"
                if (i.get("description") or "").strip()
                else i.get("name")
                for i in g.get("items") or []
            ]
            for g in amenities or []
        },
        "nearby": [
            {
                "place": (i.get("name") or "").strip(),
                "walk": i.get("footPathDescription"),
                "drive": i.get("drivePathDescription"),
            }
            for i in items("CityFacility")
        ],
        "mobile_signal": {i.get("name"): i.get("description") for i in items("BTS")},
        "host": {
            "id": info.get("hostId"),
            "name": " ".join((detail.get("hostName") or "").split()),
            # Room-detail host stats are often defaults (60 min, 0%): ot_host has the real ones.
            "host_since": (detail.get("beHostDateTime") or "")[:10] or None,
        },
        "location": {"lat": location.get("latitude"), "lon": location.get("longitude"), "approximate": True}
        if location.get("latitude")
        else None,
        "photo_count": len(media),
        "photos": [photo_url(m) for m in media[:max_photos]],
    }


@tool("Room reviews and rating")
async def ot_reviews(
    room_id: RoomId,
    limit: Annotated[int, Field(ge=1, le=50, description="Reviews per page.")] = 10,
    page: Annotated[int, Field(ge=0, le=100, description="Zero-based page number.")] = 0,
) -> dict[str, Any]:
    """Get a room's rating (0-5), star histogram, six category scores, percent of guests who recommend it,
    and written reviews, newest first (with host replies).

    rating is null and reviews empty when the room has no ratings yet (new rooms). Use after ot_room or
    ot_search_rooms to judge quality; ot_host has reviews across all of a host's rooms.
    """

    async def points_or_none() -> Any:
        try:
            return await fetch(f"{CORE}/api/v2/Points/GetRoomPointsV2", {"roomId": room_id})
        except ApiError as e:
            if e.status == 500:  # no ratings yet (or unknown room)
                return None
            raise

    take = limit + 1  # one extra tells whether there is another page
    points, reviews, rec = await asyncio.gather(
        points_or_none(),
        fetch(f"{CORE}/api/v2/Comments/GetAllByRoomId", {"roomId": room_id, "take": take, "skip": page * limit}),
        fetch(f"{ODATA}/Point/GetRoomRecommendationPercentage(roomId={room_id})"),
    )
    out: dict[str, Any] = {}
    if not points and not reviews:  # new rooms and unknown ids look the same here
        out["note"] = "No ratings or reviews yet (common for new rooms). ot_room confirms the room exists."
    points = points or {}
    return out | {
        "room_id": room_id,
        "rating": rating(points.get("score"), points.get("totalCount")),
        "rating_count": points.get("totalCount") or 0,
        "stars": {str(p.get("score")): p.get("count") for p in points.get("progresses") or []},
        "categories": {
            CATEGORIES.get(p.get("iconName"), p.get("title")): p.get("point") for p in points.get("points") or []
        },
        "recommend_pct": (rec or {}).get("recommendationPercentage") if points else None,
        "page": page,
        "has_more": len(reviews or []) > limit,
        "reviews": [review(c) for c in (reviews or [])[:limit]],
    }


@tool("Similar rooms")
async def ot_similar_rooms(
    room_id: RoomId,
    check_in: CheckIn = None,
    check_out: CheckOut = None,
    guests: Guests = None,
) -> dict[str, Any]:
    """Get 4-5 alternatives to a room (same city and type), as the room page shows them.

    Without dates prices are undated starting prices (not tonight's). With check_in/check_out the
    rooms are re-priced for that stay; rooms that are booked or too small for `guests` are listed in
    not_available. Use when the user's room is full or too expensive. Next: ot_room, ot_price_quote.
    """
    nights = stay(check_in, check_out)
    data = await fetch(f"{CORE}/api/v1/Rooms/GetSimilarRooms", method="POST", json={"roomId": room_id})
    similar = (data or {}).get("rooms") or []
    if not similar:
        raise ToolError(f"No similar rooms for {room_id} (unknown id or no matches). Try ot_search_rooms in its city.")
    if not nights:
        return {
            "room_id": room_id,
            "dated": False,
            "rooms": [card(r, None, None, None, from_search=False) for r in similar],
        }
    ids = [str(r.get("roomId")) for r in similar]
    body = {
        "roomIds": ids,
        "checkIn": str(check_in),
        "checkOut": str(check_out),
        "person": guests,
        "skip": 0,
        "take": len(ids),
    }
    found = await fetch(f"{CORE}/api/v3/RoomSearch/SearchRooms", method="POST", json=body, expect=dict)
    # With roomIds the API skips the capacity check, so drop rooms that are too small here.
    rooms = [card(r, guests, check_in, check_out) for r in found.get("rooms") or []]
    rooms = [r for r in rooms if not guests or guests <= r["max_guests"]]
    priced = {str(r["id"]) for r in rooms}
    return {
        "room_id": room_id,
        "dated": True,
        "nights": nights,
        "rooms": sorted(rooms, key=lambda r: r["stay_total"]),
        "not_available": [int(i) for i in ids if i not in priced],
    }


@tool("Host profile")
async def ot_host(
    host_id: Annotated[int | None, Field(ge=1, description="Host (user) id, e.g. 1626813.")] = None,
    room_id: Annotated[int | None, Field(ge=1, description="Or a room id to look up its host, e.g. 2512254.")] = None,
    rooms_limit: Annotated[int, Field(ge=0, le=30, description="Host's rooms to list.")] = 10,
    reviews_limit: Annotated[int, Field(ge=0, le=30, description="Newest reviews across the host's rooms.")] = 5,
) -> dict[str, Any]:
    """Check a host: response time, response and acceptance rates, member since, active rooms
    (undated starting prices) and the newest guest reviews across all their rooms.

    Pass host_id, or room_id to find the room's host. Use to judge whether a host is reliable or to
    see their other places.
    """
    if not host_id:
        if not room_id:
            raise ToolError("Give host_id or room_id.")
        try:
            pdp = await fetch(f"{CORE}/api/v3/Rooms/GetRoomPdp", {"roomId": room_id}, expect=dict)
        except ApiError as e:
            if e.status == 404:
                raise ApiError(f"Unknown room id {room_id}.", 404) from e
            raise
        host_id = pdp["roomInfo"]["hostId"]
    calls = [fetch(f"{CORE}/api/v1/UserProfile/GetUserPublicProfileInfo", {"userId": host_id}, expect=dict)]
    if rooms_limit:
        calls.append(
            fetch(f"{CORE}/api/v1/HostRoom/GetHostRoomBox", {"hostId": host_id, "skip": 0, "take": rooms_limit})
        )
    if reviews_limit:
        params = {"hostId": host_id, "top": reviews_limit, "skip": 0, "count": "true"}
        calls.append(fetch(f"{CORE}/api/v2/Comments/GetHostRoomsComments", params))
    try:
        p, *rest = await asyncio.gather(*calls)
    except ApiError as e:
        if e.status == 500:
            raise ApiError(f"Unknown host id {host_id}. Get it from ot_room (host.id).", 500) from e
        raise
    if not p.get("isHost"):
        raise ToolError(f"User {host_id} is not a host.")
    rooms = rest.pop(0) if rooms_limit else {}
    reviews = rest.pop(0) if reviews_limit else {}
    return {
        "id": p.get("id"),
        "name": " ".join(f"{p.get('userFirstName') or ''} {p.get('userLastName') or ''}".split()),
        "url": f"{WWW}/profile/{p.get('id')}/",
        "about": (p.get("biography") or "").strip() or None,
        "city": p.get("cityFaName"),
        "province": p.get("stateFaName"),
        "host_since": (p.get("registeredDate") or "")[:10] or None,
        "response_minutes": p.get("hostResponseTime") or None,
        "response_rate_pct": p.get("hostResponsesPercentage"),
        "acceptance_pct": p.get("confirmationPercent"),
        "active_rooms": p.get("hostRoomsCount"),
        "replies_to_reviews": p.get("repliesCount"),
        "rooms": [card(r, None, None, None) for r in (rooms or {}).get("roomBoxes") or []],
        "reviews": [review(c) for c in (reviews or {}).get("comments") or []],
    }


def review(c: dict[str, Any]) -> dict[str, Any]:
    """Compact guest review (room and host review lists share the shape)."""
    replies = [r.get("body") for r in c.get("replies") or [] if r.get("isFromHost") and r.get("body")]
    return {
        "date": (c.get("creationDateTime") or "")[:10],
        "room_id": c.get("roomId"),
        "name": (c.get("senderDisplayName") or "").strip(),
        "rating": round(c["point"], 1) if c.get("point") else None,
        "recommends": RECOMMENDS.get(c.get("recomendationType")),
        "text": (c.get("body") or "").strip()[:800],
        "pros": [p.strip() for p in c.get("positivePoints") or []],
        "cons": [p.strip() for p in c.get("negativePoints") or []],
        "host_reply": replies[0][:500] if replies else None,
        "photos": [photo_url(m.get("name"), "Medium") for m in c.get("pdpMediaObject") or [] if m.get("name")],
    }
