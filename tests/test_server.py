import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio


async def test_tools_are_read_only_and_documented(client):
    tools = (await client.list_tools()).tools
    assert all(t.name.startswith("ot_") and t.annotations.read_only_hint for t in tools)
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    assert sorted(re.findall(r"^\| `(ot_\w+)` \|", readme, re.M)) == sorted(t.name for t in tools)
