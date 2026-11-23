import asyncio
import json
import os

import pytest

pytest.importorskip("mcp")

from everything_mcp import server  # noqa: E402

TOOLS = {"find_files", "get_file_info", "status", "list_options"}


def _call(name, arguments):
    result = asyncio.run(server.mcp.call_tool(name, arguments))
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_all_tools_registered():
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert TOOLS <= names


def test_instructions_route_agents_to_find_files():
    instructions = server.mcp.instructions
    assert instructions and "find_files" in instructions
    assert "ls" in instructions and "glob" in instructions


def test_tools_are_titled_and_marked_read_only():
    for tool in asyncio.run(server.mcp.list_tools()):
        assert tool.title, tool.name
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


def test_find_files_description_states_when_to_prefer_it():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    description = tools["find_files"].description
    assert "PREFERRED" in description
    assert "Get-ChildItem" in description


def test_list_options_shape():
    payload = _call("list_options", {})
    assert payload["default_sort"] == "name_asc"
    assert "date_modified_desc" in payload["sort"]
    assert "match_path" in payload["match_flags"]


def test_bad_sort_returns_structured_error():
    payload = _call("find_files", {"query": "x", "sort": "nope"})
    assert "error" in payload
    assert payload["error_code"] == 9


def test_find_files_supports_paths_only(monkeypatch):
    path = r"C:\work\test.py"

    def query(query, *, paths_only, **kwargs):
        assert paths_only is True
        return {"query": query, "total": 1, "returned": 1, "results": [path]}

    monkeypatch.setattr(server.sdk, "query", query)
    assert _call("find_files", {"query": "test.py", "paths_only": True}) == {
        "query": "test.py", "total": 1, "returned": 1, "results": [path],
    }


@pytest.mark.skipif(os.name != "nt", reason="Everything SDK is Windows-only")
def test_status_tool_reports_dll():
    payload = _call("status", {})
    assert payload["dll_path"]
    assert payload["host_arch"]
    assert payload["everything_running"] in (True, False)
