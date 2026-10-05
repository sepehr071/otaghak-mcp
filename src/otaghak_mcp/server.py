"""MCP server entry point: registers every read-only Otaghak tool."""

import logging

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, info, pricing, room, search  # noqa: F401  (imports register the tools)
from .registry import TOOLS

INSTRUCTIONS = """\
Unofficial, read-only access to Otaghak (otaghak.com), an Iranian short-term rental marketplace
(villas, cottages, apartments, eco-lodges booked by the night). Nothing here can log in, book or pay.

Workflow:
1. Place -> slug: ot_find_place ('رامسر' -> city 'ramsar'; a numeric room code -> that room).
   No destination yet: ot_destinations (popular cities and collections such as beach, jungle, villapool).
2. Search: ot_search_rooms with city / province / collection, check_in, check_out and guests
   (dated results are available rooms with stay prices). Amenity, rule and region codes, room type ids
   and landmarks (district_id) come from ot_search_filters. The API sorts cheapest by nightly price;
   with dates each page is re-sorted by stay_total, so compare stay_total across pages for a party.
3. One room: ot_room (details, rules, amenities, cancellation, photos; a dated price does not mean
   the nights are free), ot_reviews, ot_host (response and acceptance rates), ot_similar_rooms.
4. Dates and money: ot_room_calendar (free nights, nightly prices, min/max nights),
   ot_price_quote (exact amount to pay for dates + guests incl. extra-guest charges, optional late
   checkout), ot_late_checkout, ot_holidays.
5. Deals: ot_deals (last-second and most discounted). Help and guides: ot_help (FAQ, terms,
   cancellation policies), ot_travel_guide (destination guide, FAQ, blog).

Conventions: all prices are Toman (the API is Toman; 1 Toman = 10 Rial). price_per_night is after
discount; without dates it is an undated starting price, not a quote. stay_total in search is an
estimate (can be a few Toman off); ot_price_quote `total` is the payable amount (coupons and wallet
credit need login and are not included). Rooms have base_guests included in the price and allow up
to max_guests with an extra charge per extra guest and night. Dates are Gregorian YYYY-MM-DD,
check_out is the departure day, no past dates, at most 20 nights, and calendars open only to the end
of next Jalali month. Ratings are 0-5, null = not rated. Exact address and host phone are given only
after booking; locations are approximate. Persian place names and queries match best.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

mcp = MCPServer(
    "otaghak-mcp",
    title="Otaghak",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/sepehr071/otaghak-mcp",
)

for fn, title in TOOLS:
    mcp.add_tool(fn, title=title, annotations=READ_ONLY)


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request floods client logs
    mcp.run()


if __name__ == "__main__":
    main()
