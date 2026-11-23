"""Constant mappings from the Everything SDK (see include/Everything.h)."""

from __future__ import annotations

MAX_RESULTS_PER_PAGE = 500
MAX_OFFSET = 0xFFFFFFFF

SORT = {
    "name_asc": 1,
    "name_desc": 2,
    "path_asc": 3,
    "path_desc": 4,
    "size_asc": 5,
    "size_desc": 6,
    "extension_asc": 7,
    "extension_desc": 8,
    "type_name_asc": 9,
    "type_name_desc": 10,
    "date_created_asc": 11,
    "date_created_desc": 12,
    "date_modified_asc": 13,
    "date_modified_desc": 14,
    "attributes_asc": 15,
    "attributes_desc": 16,
    "run_count_asc": 19,
    "run_count_desc": 20,
    "date_recently_changed_asc": 21,
    "date_recently_changed_desc": 22,
    "date_accessed_asc": 23,
    "date_accessed_desc": 24,
}

REQUEST = {
    "file_name": 0x00000001,
    "path": 0x00000002,
    "full_path_and_file_name": 0x00000004,
    "extension": 0x00000008,
    "size": 0x00000010,
    "date_created": 0x00000020,
    "date_modified": 0x00000040,
    "date_accessed": 0x00000080,
    "attributes": 0x00000100,
    "run_count": 0x00000400,
}

BASE_REQUEST_FLAGS = (
    REQUEST["file_name"]
    | REQUEST["path"]
    | REQUEST["full_path_and_file_name"]
    | REQUEST["extension"]
    | REQUEST["size"]
    | REQUEST["date_modified"]
    | REQUEST["attributes"]
)

ATTRIBUTES = {
    "readonly": 0x00000001,
    "hidden": 0x00000002,
    "system": 0x00000004,
    "directory": 0x00000010,
    "archive": 0x00000020,
    "device": 0x00000040,
    "normal": 0x00000080,
    "temporary": 0x00000100,
    "sparse_file": 0x00000200,
    "reparse_point": 0x00000400,
    "compressed": 0x00000800,
    "offline": 0x00001000,
    "not_content_indexed": 0x00002000,
    "encrypted": 0x00004000,
}

TARGET_MACHINES = {
    1: "x86",
    2: "x64",
    3: "arm",
}

SORT_NAMES = tuple(SORT.keys())


def decode_attributes(value: int) -> list[str]:
    """Translate a Windows FILE_ATTRIBUTE_* bitmask into readable names."""
    return [name for name, bit in ATTRIBUTES.items() if value & bit]


def human_size(num_bytes: int | None) -> str | None:
    """Format a byte count as a human friendly string."""
    if num_bytes is None:
        return None
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
