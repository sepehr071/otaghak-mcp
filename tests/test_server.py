import re
from pathlib import Path

import pytest
from conftest import fixture
from mcp import Client

from otaghak_mcp.server import mcp

pytestmark = pytest.mark.anyio


async def test_tools_are_read_only_and_documented(client):
    tools = (await client.list_tools()).tools
    assert all(t.name.startswith("ot_") and t.annotations.read_only_hint for t in tools)
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    assert sorted(re.findall(r"^\| `(ot_\w+)` \|", readme, re.M)) == sorted(t.name for t in tools)


async def test_results_are_one_compact_text_block(client):
    tool = next(t for t in (await client.list_tools()).tools if t.name == "ot_destinations")
    assert tool.output_schema is None
    text = "".join(t.text for t in (await client.call_tool("ot_destinations", {})).content)
    assert "\n" not in text and ": " not in text


async def test_raw_client_one_text_block_no_structured_content(api):
    # No conftest patch here: the real client sees exactly what a user's client sees.
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert all(t.output_schema is None for t in tools)
        assert not any("title" in p for t in tools for p in t.input_schema["properties"].values())
        api["GetSearchSuggestion"] = fixture("popular.json")
        result = await c.call_tool("ot_destinations", {})
        assert not result.is_error and result.structured_content is None
        assert len(result.content) == 1 and result.content[0].type == "text"
