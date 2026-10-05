import httpx
import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio


async def test_ot_help_faq(client, api):
    api["GetAllForFaqPage"] = fixture("faq.json")
    data = (await client.call_tool("ot_help", {})).structured_content
    assert len(data["items"]) == 4
    first = data["items"][0]
    assert first["question"] == "چطور به اتاقک اعتماد کنم؟"  # leading space stripped
    assert first["answer"].startswith("امنیت مسافران مهم\u200cترین دغدغه ما") and "<" not in first["answer"]
    assert len(first["answer"]) <= 200


async def test_ot_help_faq_query(client, api):
    api["GetAllForFaqPage"] = fixture("faq.json")
    data = (await client.call_tool("ot_help", {"query": "مالک اتاقک"})).structured_content
    assert [i["question"] for i in data["items"]] == ["چطور می توانم به مالک اتاقک اعتماد کنم؟"]


async def test_ot_help_terms(client, api):
    api["GetContent"] = fixture("terms.json")
    data = (await client.call_tool("ot_help", {"topic": "terms"})).structured_content
    assert data["title"] == "قوانین استفاده از اتاقک به عنوان مهمان"
    assert data["url"] == "https://www.otaghak.com/term-of-service"
    titles = [s["title"] for s in data["sections"]]
    assert "قوانین لغو متعادل رزرو" in titles and all(len(s["preview"]) <= 250 for s in data["sections"])
    assert dict(api.calls[0].url.params) == {"enTitle": "term-of-service"}


async def test_ot_help_terms_query(client, api):
    api["GetContent"] = fixture("terms.json")
    data = (await client.call_tool("ot_help", {"topic": "terms", "query": "لغو متعادل"})).structured_content
    assert [s["title"] for s in data["sections"]] == ["قوانین لغو متعادل رزرو"]
    assert "کنسلی در حین اقامت" in data["sections"][0]["text"] and "<" not in data["sections"][0]["text"]


async def test_ot_help_query_returns_matching_paragraphs(client, api):
    api["GetContent"] = {
        "title": "t",
        "body": "<h2></h2><h2>عمومی</h2><p>کیف پول</p><p>لغو رزرو با پیامک</p><h3>قوانین لغو</h3><p>الف</p><p>ب</p>",
    }
    data = (await client.call_tool("ot_help", {"topic": "terms"})).structured_content
    assert [s["title"] for s in data["sections"]] == ["عمومی", "قوانین لغو"]  # no empty heading
    data = (await client.call_tool("ot_help", {"topic": "terms", "query": "لغو"})).structured_content
    assert data["sections"] == [
        {"title": "عمومی", "text": "لغو رزرو با پیامک"},
        {"title": "قوانین لغو", "text": "الف\nب"},
    ]


async def test_ot_help_page_missing(client, api):
    api["GetContent"] = lambda request: httpx.Response(200, content=b"null")
    result = await client.call_tool("ot_help", {"topic": "quality_policy"})
    assert result.is_error and "not available" in result.content[0].text


async def test_ot_travel_guide(client, api):
    api["GetSearchInfo"] = fixture("page_info.json")
    api["GetByEnName"] = fixture("faq_city.json")
    api["search"] = fixture("blog.json")
    data = (await client.call_tool("ot_travel_guide", {"place": "ramsar", "max_chars": 500})).structured_content
    assert (data["name"], data["province"], data["title"]) == ("رامسر", "مازندران", "اجاره ویلا رامسر")
    assert data["url"] == "https://www.otaghak.com/city/ramsar/"
    assert data["guide"].startswith("اجاره اقامتگاه در رامسر؛ سفر به عروس مازندران\n") and len(data["guide"]) == 500
    assert data["related_pages"][0] == {
        "title": "اجاره آپارتمان در رامسر",
        "city_tag": "apartment",
        "listings": 132,
        "url": "https://www.otaghak.com/city/ramsar/apartment/",
    }
    assert data["faq"][0]["question"] == "چگونه می\u200cتوانم آپارتمان یا ویلا در رامسر رزرو کنم؟"
    assert data["blog"][0] == {
        "title": "بلوار کازینو رامسر؛ بلواری میان کوه، جنگل و دریا",
        "url": "https://www.otaghak.com/blog/moalem-casino-boulevard-ramsar/",
    }
    assert dict(api.sent("GetByEnName")[0].url.params) == {"enName": "ramsar", "type": "City"}
    assert api.sent("search")[0].url.params["search"] == "رامسر"  # the Persian name from the guide


async def test_ot_travel_guide_province_has_no_faq_call(client, api):
    api["GetSearchInfo"] = fixture("page_info.json") | {
        "faName": "مازندران",
        "cities": [{"faName": "رامسر", "enName": "ramsar"}],
    }
    api["search"] = []
    data = (await client.call_tool("ot_travel_guide", {"place": "mazandaran", "kind": "province"})).structured_content
    assert data["cities"] == [{"slug": "ramsar", "name": "رامسر"}] and data["faq"] == []
    assert not api.sent("GetByEnName")
    assert "route='province'" in api.sent("GetSearchInfo")[0].url.path


async def test_ot_travel_guide_unknown_place(client, api):
    api["GetSearchInfo"] = lambda request: httpx.Response(
        500, json={"Code": "CityNotFound", "Message": "شهر مورد نظر یافت نشد"}
    )
    result = await client.call_tool("ot_travel_guide", {"place": "nowhere"})
    assert result.is_error and "Unknown city 'nowhere'" in result.content[0].text
