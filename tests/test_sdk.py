import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from everything_mcp import sdk
from everything_mcp.constants import BASE_REQUEST_FLAGS, REQUEST, SORT, decode_attributes, human_size
from everything_mcp.errors import EverythingQueryError


def test_sort_mapping_matches_sdk_constants():
    assert SORT["name_asc"] == 1
    assert SORT["name_desc"] == 2
    assert SORT["date_modified_desc"] == 14
    assert len(set(SORT.values())) == len(SORT)


def test_filetime_zero_is_none():
    assert sdk.filetime_to_datetime(sdk.FILETIME()) is None


def test_filetime_epoch_conversion():
    ft = sdk.FILETIME()
    ft.dwLowDateTime = sdk.EPOCH_AS_FILETIME & 0xFFFFFFFF
    ft.dwHighDateTime = sdk.EPOCH_AS_FILETIME >> 32
    dt = sdk.filetime_to_datetime(ft)
    assert dt is not None
    assert dt.year == 1970
    assert dt.tzinfo is not None


def test_decode_attributes():
    assert decode_attributes(0) == []
    assert decode_attributes(0x2) == ["hidden"]
    assert "directory" in decode_attributes(0x10)


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1536).endswith("KB")
    assert human_size(None) is None


def test_pick_dll_name_shape():
    name = sdk.pick_dll_name()
    assert name.startswith("Everything") and name.endswith(".dll")


def test_resolve_dll_path_prefers_package_and_accepts_override(monkeypatch):
    package = Path(sdk.__file__).resolve().parent
    monkeypatch.delenv("EVERYTHING_MCP_DLL", raising=False)
    dll_name = sdk.pick_dll_name()
    source_dll = package.parent / "dll" / dll_name
    package_dll = package / "dll" / dll_name
    available_dlls = {source_dll}
    monkeypatch.setattr(Path, "is_file", lambda path: path in available_dlls)
    assert sdk.resolve_dll_path() == str(source_dll)
    available_dlls.add(package_dll)
    assert sdk.resolve_dll_path() == str(package_dll)
    monkeypatch.setenv("EVERYTHING_MCP_DLL", "override.dll")
    assert sdk.resolve_dll_path() == "override.dll"


@pytest.fixture
def fake_sdk(monkeypatch):
    dll = Mock()
    dll.Everything_QueryW.return_value = 1
    dll.Everything_GetLastError.return_value = 0
    dll.Everything_GetNumResults.return_value = 1
    dll.Everything_GetTotResults.return_value = 10
    monkeypatch.setattr(sdk.EverythingSDK, "_load", lambda self: dll)
    return sdk.EverythingSDK("test.dll"), dll


@pytest.mark.parametrize(
    "paging",
    [
        {"max_results": -1},
        {"max_results": 501},
        {"max_results": 2**32 - 1},
        {"offset": -1},
        {"offset": 2**32},
    ],
)
def test_query_rejects_out_of_range_paging_before_calling_dll(fake_sdk, paging):
    instance, dll = fake_sdk
    with pytest.raises(EverythingQueryError) as exc:
        instance.query("test", **paging)
    assert exc.value.code == 9
    assert next(iter(paging)) in str(exc.value)
    assert not dll.mock_calls


def test_query_supports_count_only(fake_sdk):
    instance, dll = fake_sdk
    dll.Everything_GetNumResults.return_value = 0

    result = instance.query("test", max_results=0)

    assert result == {"query": "test", "total": 10, "returned": 0, "results": []}
    dll.Everything_SetMax.assert_called_once_with(0)
    dll.Everything_SetRequestFlags.assert_called_once_with(BASE_REQUEST_FLAGS)
    dll.Everything_GetResultFullPathNameW.assert_not_called()


def test_paths_only_requests_and_reads_only_full_path(fake_sdk):
    instance, dll = fake_sdk
    path = r"C:\work\test.py"

    def write_path(index, buffer, capacity):
        buffer.value = path
        return len(path)

    dll.Everything_GetResultFullPathNameW.side_effect = write_path
    result = instance.query("test", max_results=1, offset=2, paths_only=True)

    assert result == {"query": "test", "total": 10, "returned": 1, "results": [path]}
    dll.Everything_SetMax.assert_called_once_with(1)
    dll.Everything_SetOffset.assert_called_once_with(2)
    dll.Everything_SetRequestFlags.assert_called_once_with(REQUEST["full_path_and_file_name"])
    dll.Everything_GetResultFileNameW.assert_not_called()
    dll.Everything_GetResultSize.assert_not_called()


@pytest.mark.parametrize(
    ("path", "query_path", "indexed_path"),
    [
        ("C:/work/site-packages/./", r"C:\work\site-packages", r"c:\work\SITE-PACKAGES"),
        ("C:\\", "C:", "C:"),
        ("C:", "C:", "C:"),
        (r"\\?\UNC\Server\Share\Dir", r"\\Server\Share\Dir", r"\\server\share\dir"),
        (r"\\?\C:\Program Files\Test!", r"C:\Program Files\Test!", r"C:\Program Files\Test!"),
    ],
)
def test_get_file_info_queries_exact_indexed_path(fake_sdk, monkeypatch, path, query_path, indexed_path):
    instance, _ = fake_sdk
    entry = {"full_path": indexed_path, "is_folder": True}
    query = Mock(return_value={"returned": 1, "results": [entry]})
    monkeypatch.setattr(instance, "query", query)

    assert instance.get_file_info(path) == {"found": True, "path": path, "info": entry}
    query.assert_called_once_with(f'wfn:path:"{query_path}"', max_results=1)


def test_get_file_info_does_not_accept_partial_match(fake_sdk, monkeypatch):
    instance, _ = fake_sdk
    path = r"C:\work\site-packages"
    monkeypatch.setattr(
        instance,
        "query",
        Mock(return_value={"returned": 1, "results": [{"full_path": path + r"\child"}]}),
    )

    assert instance.get_file_info(path) == {
        "found": False, "path": path, "scanned": 1, "info": None,
    }


@pytest.mark.skipif(os.name != "nt", reason="Everything SDK is Windows-only")
def test_dll_loads_and_reports_status():
    instance = sdk.EverythingSDK()
    info = instance.status()
    assert info["dll_path"]
    assert info["host_arch"]
    if info["everything_running"]:
        assert info["version"] is not None
    else:
        assert info["last_error"] is not None


@pytest.mark.skipif(os.name != "nt", reason="Everything SDK is Windows-only")
def test_search_smoke():
    instance = sdk.EverythingSDK()
    try:
        result = instance.query("*.exe", max_results=5)
    except EverythingQueryError as exc:
        if exc.code == 2:
            pytest.skip("Everything client is not running")
        raise
    assert result["returned"] <= 5
    assert result["total"] >= result["returned"]
    for entry in result["results"]:
        assert entry["full_path"]
        assert "size_human" in entry and "attributes" in entry
    paths = instance.query("*.exe", max_results=5, paths_only=True)
    assert paths["returned"] <= 5
    assert all(isinstance(path, str) and path for path in paths["results"])


@pytest.mark.skipif(os.name != "nt", reason="Everything SDK is Windows-only")
def test_query_rejects_bad_sort():
    instance = sdk.EverythingSDK()
    with pytest.raises(EverythingQueryError):
        instance.query("*.exe", sort="nonsense")
