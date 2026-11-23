"""Error types for the Everything SDK wrapper."""

from __future__ import annotations

ERROR_MESSAGES = {
    0: "no error detected",
    1: "out of memory",
    2: "Everything search client is not running",
    3: "unable to register window class",
    4: "unable to create listening window",
    5: "unable to create listening thread",
    6: "invalid index",
    7: "invalid call",
    8: "invalid request data, request data first",
    9: "bad parameter",
}

EVERYTHING_OK = 0
EVERYTHING_ERROR_IPC = 2


class EverythingError(Exception):
    """Base class for all Everything MCP errors."""


class EverythingDllError(EverythingError):
    """The SDK DLL could not be located or loaded."""


class EverythingQueryError(EverythingError):
    """The SDK reported a non-zero error code for a query."""

    def __init__(self, code: int, detail: str | None = None):
        self.code = code
        message = ERROR_MESSAGES.get(code, f"unknown error code {code}")
        if code == EVERYTHING_ERROR_IPC:
            message += (
                ". Start Everything.exe (the desktop client must be running for the "
                "SDK to work), then retry."
            )
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
