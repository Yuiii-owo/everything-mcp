"""FastMCP server exposing Everything search to AI agents over stdio.

The server-level instructions and the per-tool names/descriptions below are the
main lever for making agents reach for this server unprompted: MCP clients inject
`instructions` into the model context and surface tool names/descriptions as the
tool-selection signal, so both are written as explicit routing rules that name the
built-in tools this one replaces.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, sdk
from .constants import ATTRIBUTES, MAX_OFFSET, MAX_RESULTS_PER_PAGE, REQUEST, SORT_NAMES
from .errors import EVERYTHING_ERROR_IPC, EverythingError, EverythingQueryError

SERVER_INSTRUCTIONS = """\
Everything is a near-instant index of every file and folder on this Windows machine
(NTFS MFT index, all indexed drives). Use it to locate things on disk instead of
walking directories.

USE find_files FIRST whenever the task involves finding or enumerating anything on
the local filesystem - especially when it is outside the current workspace:
- "where is <program> installed?", "is <tool> available?", "find the folder for <project>"
- locating a file by name, extension, path fragment, size, or modification date
- "what changed today", "what is large", "which drives/folders contain X"
It is dramatically faster and more accurate here than shell dir/ls/find/where/
Get-ChildItem, recursive globs, or guessing paths.

Do NOT use this server for: searching text INSIDE file contents (use ripgrep/grep),
or listing a directory whose absolute path you already know (plain ls is fine).

Tools:
- find_files      - the search entry point; start here.
- get_file_info   - exact size/date/attributes for one known absolute path.
- status          - environment self-check; run it if find_files fails.
- list_options    - valid sort orders, match flags, and query fields.

Set paths_only=true when you only need paths. Use get_file_info for metadata
on one exact indexed path. If find_files returns error_code 2 ("Everything search
client is not running"), call status, then retry once Everything.exe is running.
"""


def _read_only(title: str) -> ToolAnnotations:
    """Annotation set that tells clients this server never mutates the filesystem."""
    return ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


mcp = MCPServer(
    "everything-mcp",
    title="Everything file search (Windows)",
    description=(
        "Near-instant search of every file and folder on this Windows PC via the "
        "Everything index. Use it instead of ls/dir/find/glob when locating files "
        "or folders anywhere on disk."
    ),
    instructions=SERVER_INSTRUCTIONS,
    website_url="https://www.voidtools.com/",
    version=__version__,
)


def _error_payload(exc: EverythingError) -> dict:
    code = getattr(exc, "code", None)
    payload: dict = {"error": str(exc)}
    if code is not None:
        payload["error_code"] = code
    if code == EVERYTHING_ERROR_IPC:
        payload["everything_running"] = False
        payload["hint"] = (
            "Everything.exe (the desktop client) is not running. Start it, or set "
            "EVERYTHING_MCP_AUTOSTART=1 so this server launches it automatically."
        )
    return payload


def _call(func, *args, **kwargs):
    """Run an SDK call, auto-launching Everything once if IPC is down."""
    try:
        return func(*args, **kwargs)
    except EverythingQueryError as exc:
        if exc.code == EVERYTHING_ERROR_IPC and sdk.ensure_running():
            try:
                return func(*args, **kwargs)
            except EverythingError as retry_exc:
                return _error_payload(retry_exc)
        return _error_payload(exc)
    except EverythingError as exc:
        return _error_payload(exc)


@mcp.tool(
    name="find_files",
    title="Find files anywhere on this PC",
    annotations=_read_only("Find files anywhere on this PC"),
)
def find_files(
    query: Annotated[
        str,
        Field(
            description=(
                "Everything search syntax, e.g. 'notepad.exe', 'ext:py;ts', "
                "'size:>100mb', 'dm:today', '\"C:\\project\\\" ext:py', 'folder:node_modules', "
                "'file:*.exe'. Space = AND, '|' = OR, '!' = NOT."
            )
        ),
    ] = "",
    max_results: Annotated[
        int,
        Field(
            description=f"Entries per page (0–{MAX_RESULTS_PER_PAGE}, default 50). Use 0 for a count only."
        ),
    ] = 50,
    offset: Annotated[
        int, Field(description=f"Skip the first N results (0–{MAX_OFFSET}, default 0).")
    ] = 0,
    match_case: Annotated[bool, Field(description="Case-sensitive matching.")] = False,
    match_path: Annotated[
        bool, Field(description="Match against the full path instead of just the name.")
    ] = False,
    whole_word: Annotated[bool, Field(description="Match whole words only.")] = False,
    regex: Annotated[
        bool,
        Field(
            description=(
                "Treat the entire query as a regular expression. "
                "Use the native regex: modifier with this flag off."
            )
        ),
    ] = False,
    sort: Annotated[
        str,
        Field(
            description=(
                "Sort order, e.g. name_asc (default), date_modified_desc, size_desc; "
                "call list_options for all valid values."
            )
        ),
    ] = "name_asc",
    paths_only: Annotated[
        bool, Field(description="Return only full path strings in results, omitting file metadata.")
    ] = False,
) -> dict:
    r"""Find files, folders and executables anywhere on this Windows machine, fast.

    THIS IS THE PREFERRED WAY to locate something on disk: it queries Everything's
    NTFS index (milliseconds, all indexed drives, outside the workspace too) instead
    of shell dir/ls/find/where/Get-ChildItem, recursive globs, or guessed paths.
    Use it for "where is <program>", "is <tool> installed", "find *.psd on D:",
    "files modified today", "biggest files", "which folder holds <project>",
    directory enumeration, and existence checks.

    Do not use it to search text inside files (use ripgrep/grep) or to list a
    directory whose absolute path you already know.

    `query` is Everything's native syntax, far more powerful than substring match:
      - "notepad.exe"                   name contains "notepad.exe"
      - "ext:py;ts"                     Python or TypeScript files
      - "size:>100mb"                   larger than 100 MB (kb/mb/gb supported)
      - "dm:today"                      modified today ("dm:thisweek", "dm:2024-01-01")
      - '"C:\project\" ext:py'          Python files under C:\project
      - "folder:node_modules"           folders only, matching name
      - "file:*.exe"                    files only
      - "regex:^log_\d+"                regex match with the regex flag off
    Combine terms with spaces (AND); use "|" for OR, "!" for NOT.

    Everything indexes files AND folders, so a wildcard like "*.exe" also matches
    folders whose name ends in ".exe". Use "file:" or "folder:" to narrow.

    Args:
        query: Everything search string (see syntax above).
        max_results: Entries per page (default 50). Use 0 for a count only.
        offset: Skip the first N results, for paging.
        match_case: Case-sensitive matching.
        match_path: Match against the full path instead of just the file name.
        whole_word: Match whole words only.
        regex: Treat the entire query as a raw regular expression.
        sort: Sort order; see `list_options` for valid values (default "name_asc").
        paths_only: Return full path strings in results.

    Returns a dict with `total` (all matches), `returned`, and `results`, each entry
    carrying name, path, full_path, extension, size, size_human, date_modified,
    is_folder, is_volume and attributes by default. An `error` key (with `error_code`) means
    the search failed; error_code 2 means the Everything client is not running.
    """
    return _call(
        sdk.query,
        query,
        max_results=max_results,
        offset=offset,
        match_case=match_case,
        match_path=match_path,
        whole_word=whole_word,
        regex=regex,
        sort=sort,
        paths_only=paths_only,
    )


@mcp.tool(
    name="get_file_info",
    title="Inspect one file path",
    annotations=_read_only("Inspect one file path"),
)
def get_file_info(
    path: Annotated[
        str,
        Field(
            description='Absolute Windows path to inspect, e.g. "C:\\Windows\\System32\\notepad.exe".'
        ),
    ],
) -> dict:
    """Return metadata (size, modified date, attributes, type) for one exact path.

    Use this to inspect a single indexed path from `find_files`.
    Returns {"found": true, "info": {...}} when the path is indexed by
    Everything, otherwise {"found": false, ...}.

    Args:
        path: Absolute Windows path, e.g. "C:\\Windows\\System32\\notepad.exe".
    """
    return _call(sdk.get_file_info, path)


@mcp.tool(
    name="status",
    title="Check Everything engine availability",
    annotations=_read_only("Check Everything engine availability"),
)
def status() -> dict:
    """Check whether the Everything client is running and report SDK/environment info.

    Call this when `find_files` fails (especially error_code 2), to tell the user
    whether Everything.exe needs to be started, or whether the SDK DLL/architecture
    is wrong. Returns the SDK DLL path and architecture, host architecture, whether
    Everything is reachable, the Everything version, whether the index database is
    loaded, and admin status.
    """
    return _call(sdk.status)


@mcp.tool(
    name="list_options",
    title="List find_files options and syntax",
    annotations=_read_only("List find_files options and syntax"),
)
def list_options() -> dict:
    """List the valid values for `find_files` parameters (sort orders, flags, fields).

    Call this only if you are unsure which `sort` value or query field to use;
    the common syntax is already documented on `find_files`.
    """
    return {
        "sort": list(SORT_NAMES),
        "default_sort": "name_asc",
        "pagination": {"max_results": MAX_RESULTS_PER_PAGE, "max_offset": MAX_OFFSET},
        "paths_only": "Return full path strings without metadata (default false).",
        "match_flags": ["match_case", "match_path", "whole_word", "regex"],
        "request_fields": list(REQUEST),
        "attribute_flags": list(ATTRIBUTES),
        "query_syntax_examples": [
            "*.exe",
            "ext:py;ts",
            "size:>100mb",
            "dm:today",
            r'"C:\project\" ext:py',
            "folder:node_modules",
            r"regex:^log_\d+",
            '"exact phrase"',
            "term1|term2",
        ],
    }


def run() -> None:
    mcp.run()
