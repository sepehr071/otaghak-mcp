"""Info tools: site rules and FAQ, destination travel guides and blog."""

from __future__ import annotations

import asyncio
import html
import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import BLOG, CORE, ODATA, WWW, ApiError, fetch
from .registry import tool
from .search import Slug

PAGES = {"terms": "term-of-service", "host_rules": "host-rules", "quality_policy": "quality-policy"}


@tool("Help: rules and FAQ")
async def ot_help(
    topic: Annotated[
        Literal["faq", "terms", "host_rules", "quality_policy"],
        Field(
            description="faq: the help-center FAQ; terms: guest terms of use (booking, cancellation policies, refunds, wallet, stay rules); host_rules; quality_policy."
        ),
    ] = "faq",
    query: Annotated[
        str | None,
        Field(
            min_length=2,
            max_length=60,
            description="Words to look for, Persian works best, e.g. 'لغو' (cancellation) or 'کیف پول' (wallet).",
        ),
    ] = None,
) -> dict[str, Any]:
    """Read Otaghak's help center: the FAQ, or the guest terms (booking and cancellation rules,
    the five cancellation policies, refunds, wallet, check-in rules), host rules or quality policy.

    Without `query` you get every FAQ question (short answers) or the section titles of a page with a
    preview; with `query` the matching FAQ items or page paragraphs. A room's own cancellation policy is in ot_room.
    """
    words = query.split() if query else []
    if topic == "faq":
        faq = await fetch(f"{CORE}/api/v1/Faq/GetAllForFaqPage")
        items = [
            {"question": (f.get("question") or "").strip(), "answer": html_text(f.get("answer"))} for f in faq or []
        ]
        if not words:
            return {"topic": topic, "items": [i | {"answer": i["answer"][:200]} for i in items]}
        hits = [i for i in items if matches(i["question"] + " " + i["answer"], words)]
        return {"topic": topic, "query": query, "items": [i | {"answer": i["answer"][:2000]} for i in hits[:8]]}
    page = await fetch(f"{CORE}/api/v1/Content/GetContent", {"enTitle": PAGES[topic]})
    if not page:
        raise ToolError(f"The {topic} page is not available right now.")
    sections = split_sections(page.get("body") or "")
    out: dict[str, Any] = {
        "topic": topic,
        "title": page.get("title"),
        "url": page.get("canonical") or f"{WWW}/{PAGES[topic]}",
    }
    if not words:
        return out | {"sections": [{"title": s["title"], "preview": s["text"][:250]} for s in sections]}
    hits = []
    for s in sections:  # only the paragraphs that match (all of them when the title matches)
        paragraphs = [p for p in s["text"].split("\n") if matches(s["title"] + " " + p, words)]
        if paragraphs:
            hits.append({"title": s["title"], "text": "\n".join(paragraphs)[:4000]})
    return out | {"query": query, "sections": hits[:4]}


@tool("Destination travel guide")
async def ot_travel_guide(
    place: Annotated[
        Slug, Field(description="City, province or collection slug, e.g. 'ramsar', 'mazandaran', 'beach'.")
    ],
    kind: Annotated[Literal["city", "province", "collection"], Field(description="What `place` is.")] = "city",
    blog_query: Annotated[
        str | None,
        Field(
            min_length=2,
            max_length=60,
            description="Blog search words, e.g. 'رستوران رامسر'. Default: the place's Persian name.",
        ),
    ] = None,
    max_chars: Annotated[int, Field(ge=500, le=12000, description="Max characters of the guide text.")] = 3000,
) -> dict[str, Any]:
    """Get the site's travel guide for a destination: guide text, the destination's FAQ (best season,
    sights, prices), related search pages (city themes such as beach, cottage, apartment with listing
    counts) and matching blog articles (restaurants, sights, souvenirs).

    Use when the user asks what a place is like or what to do there. City theme slugs from
    related_pages go to ot_search_rooms `city_tag`.
    """
    route = {"city": "city", "province": "province", "collection": "landing"}[kind]
    try:
        info = await fetch(
            f"{ODATA}/RoomSearchSeo/GetSearchInfo(route='{route}',code='{place}',permalink='',houseType=0)",
            expect=dict,
        )
    except ApiError as e:
        if e.status == 500:
            raise ApiError(
                f"Unknown {kind} {place!r}. Check the slug with ot_find_place or ot_destinations.", 500
            ) from e
        raise
    name = info.get("faName") or place
    faq_type = {"city": "City", "collection": "Tag"}.get(kind)

    async def faq() -> Any:
        if not faq_type:
            return []
        try:
            return await fetch(f"{CORE}/api/v1/Faq/GetByEnName", {"enName": place, "type": faq_type})
        except ApiError:  # 500 = no FAQ for this slug
            return []

    faqs, posts = await asyncio.gather(faq(), fetch(f"{BLOG}/search", {"search": blog_query or name, "per_page": 6}))
    return {
        "name": name,
        "slug": info.get("enName") or place,
        "kind": kind,
        "province": info.get("faStateName") or None,
        "title": info.get("h1Title"),
        "url": f"{WWW}/{route}/{place}/",
        "guide": html_text(info.get("description"))[:max_chars],
        "related_pages": [
            {
                "title": r.get("title"),
                "city_tag": tag_slug(r.get("url"), place),
                "listings": r.get("roomCount"),
                "url": r.get("url"),
            }
            for r in info.get("relatedSearchs") or []
        ],
        "cities": [{"slug": c.get("enName"), "name": c.get("faName")} for c in info.get("cities") or []],
        "faq": [{"question": f.get("question"), "answer": html_text(f.get("answer"))[:600]} for f in faqs or []],
        "blog": [{"title": html.unescape(p.get("title") or ""), "url": p.get("url")} for p in posts or []],
    }


def html_text(value: str | None) -> str:
    """HTML -> plain text with line breaks between blocks."""
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr)>", "\n", value or "")
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    return "\n".join(line for line in (" ".join(raw.split()) for raw in text.splitlines()) if line)


def split_sections(body: str) -> list[dict[str, str]]:
    """Split an HTML page into {title, text} sections at its h2-h4 headings."""
    parts = re.split(r"(?is)<h[2-4][^>]*>(.*?)</h[2-4]>", body)
    sections = [{"title": "", "text": html_text(parts[0])}] if html_text(parts[0]) else []
    for title, text in zip(parts[1::2], parts[2::2], strict=True):
        section = {"title": html_text(title), "text": html_text(text)}
        if section["title"] or section["text"]:
            sections.append(section)
    return sections


def matches(text: str, words: list[str]) -> bool:
    text = text.replace("\u200c", " ")
    return all(w.replace("\u200c", " ") in text for w in words)


def tag_slug(url: str | None, place: str) -> str | None:
    """'https://www.otaghak.com/city/ramsar/cottage/' -> 'cottage'."""
    m = re.search(rf"/city/{re.escape(place)}/([^/?#]+)", url or "", re.IGNORECASE)
    return m[1] if m else None
