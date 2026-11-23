"""ctypes bindings for the Everything SDK (Everything64.dll and friends).

The SDK keeps its search state in global variables and talks to the running
Everything client over IPC. ctypes releases the GIL around every native call,
so concurrent MCP tool invocations would otherwise corrupt each other's state.
Every access therefore happens under a single module-wide lock, and every
result string is copied into Python memory before the lock is released.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import platform
import shutil
import struct
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from .constants import (
    BASE_REQUEST_FLAGS,
    MAX_OFFSET,
    MAX_RESULTS_PER_PAGE,
    REQUEST,
    SORT,
    TARGET_MACHINES,
    decode_attributes,
    human_size,
)
from .errors import (
    ERROR_MESSAGES,
    EVERYTHING_ERROR_IPC,
    EVERYTHING_OK,
    EverythingDllError,
    EverythingQueryError,
)

EPOCH_AS_FILETIME = 116444736000000000
FILETIME_TICKS_PER_SECOND = 10_000_000
MAX_PATH_BUFFER = 32768


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


def host_arch() -> tuple[str, int]:
    """Return (machine, bits) of the *host process*, which picks the DLL."""
    return platform.machine().lower(), struct.calcsize("P") * 8


def pick_dll_name() -> str:
    machine, bits = host_arch()
    if bits == 64 and machine in ("arm64", "aarch64"):
        return "EverythingARM64.dll"
    if bits == 32 and machine in ("arm", "armv7", "armv7l"):
        return "EverythingARM.dll"
    return "Everything64.dll" if bits == 64 else "Everything32.dll"


def bundled_dll_dir() -> Path | None:
    package_dir = Path(__file__).resolve().parent
    dll_name = pick_dll_name()
    for parent in (package_dir, package_dir.parent):
        candidate = parent / "dll"
        if (candidate / dll_name).is_file():
            return candidate
    return None


def resolve_dll_path() -> str:
    override = os.environ.get("EVERYTHING_MCP_DLL")
    if override:
        return override
    bundled = bundled_dll_dir()
    if bundled is not None:
        return str(bundled / pick_dll_name())
    return pick_dll_name()


def find_everything_exe() -> str | None:
    override = os.environ.get("EVERYTHING_MCP_EXE")
    if override and Path(override).exists():
        return override
    found = shutil.which("Everything.exe") or shutil.which("Everything")
    if found:
        return found
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(program_files, "Everything", "Everything.exe"),
        os.path.join(program_files_x86, "Everything", "Everything.exe"),
        os.path.join(local_appdata, "Programs", "Everything", "Everything.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return _everything_exe_from_registry()


def _everything_exe_from_registry() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Everything.exe"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value and Path(value).exists():
                    return value
        except OSError:
            continue
    return None


def filetime_to_datetime(ft: FILETIME) -> datetime | None:
    """Convert a FILETIME (100ns ticks since 1601) to an aware UTC datetime."""
    ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    if ticks == 0:
        return None
    seconds = (ticks - EPOCH_AS_FILETIME) / FILETIME_TICKS_PER_SECOND
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


class EverythingSDK:
    """Thin, thread-safe wrapper around the Everything SDK DLL."""

    def __init__(self, dll_path: str | None = None):
        self.dll_path = str(dll_path or resolve_dll_path())
        self._lock = threading.RLock()
        self._dll = self._load()

    @staticmethod
    def _load_spec() -> list[tuple[str, list, object]]:
        c_uint = ctypes.c_uint
        c_int = ctypes.c_int
        c_wchar_p = ctypes.c_wchar_p
        return [
            ("Everything_SetSearchW", [c_wchar_p], None),
            ("Everything_SetMatchPath", [c_int], None),
            ("Everything_SetMatchCase", [c_int], None),
            ("Everything_SetMatchWholeWord", [c_int], None),
            ("Everything_SetRegex", [c_int], None),
            ("Everything_SetMax", [c_uint], None),
            ("Everything_SetOffset", [c_uint], None),
            ("Everything_SetSort", [c_uint], None),
            ("Everything_SetRequestFlags", [c_uint], None),
            ("Everything_QueryW", [c_int], c_int),
            ("Everything_GetLastError", [], c_uint),
            ("Everything_GetNumResults", [], c_uint),
            ("Everything_GetTotResults", [], c_uint),
            ("Everything_GetResultFileNameW", [c_uint], c_wchar_p),
            ("Everything_GetResultPathW", [c_uint], c_wchar_p),
            ("Everything_GetResultExtensionW", [c_uint], c_wchar_p),
            (
                "Everything_GetResultFullPathNameW",
                [c_uint, ctypes.POINTER(ctypes.c_wchar), c_uint],
                c_uint,
            ),
            ("Everything_IsFolderResult", [c_uint], c_int),
            ("Everything_IsFileResult", [c_uint], c_int),
            ("Everything_IsVolumeResult", [c_uint], c_int),
            ("Everything_GetResultSize", [c_uint, ctypes.POINTER(ctypes.c_longlong)], c_int),
            ("Everything_GetResultDateModified", [c_uint, ctypes.POINTER(FILETIME)], c_int),
            ("Everything_GetResultDateCreated", [c_uint, ctypes.POINTER(FILETIME)], c_int),
            ("Everything_GetResultAttributes", [c_uint], c_uint),
            ("Everything_IsDBLoaded", [], c_int),
            ("Everything_IsAdmin", [], c_int),
            ("Everything_GetMajorVersion", [], c_uint),
            ("Everything_GetMinorVersion", [], c_uint),
            ("Everything_GetRevision", [], c_uint),
            ("Everything_GetBuildNumber", [], c_uint),
            ("Everything_GetTargetMachine", [], c_uint),
            ("Everything_Reset", [], None),
            ("Everything_CleanUp", [], None),
        ]

    def _load(self):
        if os.name != "nt":
            raise EverythingDllError(
                "The Everything SDK is Windows-only; ctypes.WinDLL is unavailable here."
            )
        try:
            dll = ctypes.WinDLL(self.dll_path)
        except OSError as exc:
            machine, bits = host_arch()
            raise EverythingDllError(
                f"Could not load the Everything SDK DLL at {self.dll_path!r} "
                f"(host process is {bits}-bit {machine}). Use a DLL matching the host "
                f"architecture, or point EVERYTHING_MCP_DLL at the right one. "
                f"Original error: {exc}"
            ) from exc
        for name, argtypes, restype in self._load_spec():
            func = getattr(dll, name)
            func.argtypes = argtypes
            func.restype = restype
        return dll

    def _run_search(
        self, dll, query, *, max_results, offset, match_case,
        match_path, whole_word, regex, sort_value, request_flags,
    ):
        dll.Everything_Reset()
        dll.Everything_SetSearchW(query)
        dll.Everything_SetMatchCase(1 if match_case else 0)
        dll.Everything_SetMatchPath(1 if match_path else 0)
        dll.Everything_SetMatchWholeWord(1 if whole_word else 0)
        dll.Everything_SetRegex(1 if regex else 0)
        dll.Everything_SetMax(max_results)
        dll.Everything_SetOffset(offset)
        dll.Everything_SetSort(sort_value)
        dll.Everything_SetRequestFlags(request_flags)
        ok = dll.Everything_QueryW(1)
        error = dll.Everything_GetLastError()
        if not ok or error != EVERYTHING_OK:
            raise EverythingQueryError(error, detail=f"query={query!r}")

    def _collect(self, dll, query, *, paths_only):
        count = dll.Everything_GetNumResults()
        total = dll.Everything_GetTotResults()
        buffer = ctypes.create_unicode_buffer(MAX_PATH_BUFFER) if count else None
        results = []
        for index in range(count):
            full_path = ""
            if dll.Everything_GetResultFullPathNameW(index, buffer, MAX_PATH_BUFFER):
                full_path = buffer.value
            if paths_only:
                results.append(full_path)
                continue

            size = ctypes.c_longlong(0)
            has_size = bool(dll.Everything_GetResultSize(index, ctypes.byref(size)))
            modified = FILETIME()
            has_modified = bool(
                dll.Everything_GetResultDateModified(index, ctypes.byref(modified))
            )
            size_bytes = int(size.value) if has_size else None
            modified_dt = filetime_to_datetime(modified) if has_modified else None
            results.append(
                {
                    "name": dll.Everything_GetResultFileNameW(index) or "",
                    "path": dll.Everything_GetResultPathW(index) or "",
                    "full_path": full_path,
                    "extension": dll.Everything_GetResultExtensionW(index) or "",
                    "size": size_bytes,
                    "size_human": human_size(size_bytes),
                    "date_modified": modified_dt.isoformat() if modified_dt else None,
                    "is_folder": bool(dll.Everything_IsFolderResult(index)),
                    "is_volume": bool(dll.Everything_IsVolumeResult(index)),
                    "attributes": decode_attributes(dll.Everything_GetResultAttributes(index)),
                }
            )
        return {
            "query": query,
            "total": int(total),
            "returned": len(results),
            "results": results,
        }

    def query(
        self,
        query: str = "",
        *,
        max_results: int = 50,
        offset: int = 0,
        match_case: bool = False,
        match_path: bool = False,
        whole_word: bool = False,
        regex: bool = False,
        sort: str = "name_asc",
        paths_only: bool = False,
    ) -> dict:
        sort_value = SORT.get(sort)
        if sort_value is None:
            raise EverythingQueryError(
                9, f"unknown sort {sort!r}; valid values: {', '.join(SORT)}"
            )
        max_results = int(max_results)
        offset = int(offset)
        if not 0 <= max_results <= MAX_RESULTS_PER_PAGE:
            raise EverythingQueryError(
                9, f"max_results must be between 0 and {MAX_RESULTS_PER_PAGE}"
            )
        if not 0 <= offset <= MAX_OFFSET:
            raise EverythingQueryError(9, f"offset must be between 0 and {MAX_OFFSET}")
        request_flags = REQUEST["full_path_and_file_name"] if paths_only else BASE_REQUEST_FLAGS
        with self._lock:
            self._run_search(
                self._dll,
                query or "",
                max_results=max_results,
                offset=offset,
                match_case=match_case,
                match_path=match_path,
                whole_word=whole_word,
                regex=regex,
                sort_value=sort_value,
                request_flags=request_flags,
            )
            return self._collect(self._dll, query or "", paths_only=paths_only)

    def get_file_info(self, path: str) -> dict:
        windows_path = PureWindowsPath(path)
        # Everything indexes drive roots as "C:" and returns that spelling in searches.
        is_drive_root = len(path) == 2 and windows_path.drive == path
        if not (windows_path.is_absolute() or is_drive_root):
            raise EverythingQueryError(9, "path must be an absolute Windows path")
        query_path = ntpath.normpath(path).rstrip("\\")
        if query_path[:8].lower() == "\\\\?\\unc\\":
            query_path = "\\\\" + query_path[8:]
        elif query_path.startswith("\\\\?\\"):
            query_path = query_path[4:]
        if any(char in query_path for char in '"\0*?<>|'):
            raise EverythingQueryError(9, "path contains invalid characters")

        target = ntpath.normcase(query_path)
        result = self.query(f'wfn:path:"{query_path}"', max_results=1)
        for entry in result["results"]:
            indexed_path = ntpath.normpath(entry["full_path"]).rstrip("\\")
            if ntpath.normcase(indexed_path) == target:
                return {"found": True, "path": path, "info": entry}
        return {"found": False, "path": path, "scanned": result["returned"], "info": None}

    def is_running(self) -> bool:
        with self._lock:
            dll = self._dll
            dll.Everything_Reset()
            dll.Everything_SetSearchW("")
            dll.Everything_SetMax(1)
            dll.Everything_QueryW(1)
            return dll.Everything_GetLastError() == EVERYTHING_OK

    def status(self) -> dict:
        machine, bits = host_arch()
        info = {
            "dll_path": self.dll_path,
            "dll_arch": pick_dll_name(),
            "host_arch": f"{machine}-{bits}",
            "everything_running": False,
            "last_error": None,
            "error_message": None,
            "db_loaded": None,
            "is_admin": None,
            "version": None,
            "target_machine": None,
        }
        with self._lock:
            dll = self._dll
            dll.Everything_Reset()
            dll.Everything_SetSearchW("")
            dll.Everything_SetMax(1)
            dll.Everything_QueryW(1)
            error = int(dll.Everything_GetLastError())
            info["last_error"] = error
            if error != EVERYTHING_OK:
                info["error_message"] = ERROR_MESSAGES.get(error, f"unknown error code {error}")
                return info
            info["everything_running"] = True
            info["db_loaded"] = bool(dll.Everything_IsDBLoaded())
            info["is_admin"] = bool(dll.Everything_IsAdmin())
            info["version"] = "{}.{}.{}.{}".format(
                dll.Everything_GetMajorVersion(),
                dll.Everything_GetMinorVersion(),
                dll.Everything_GetRevision(),
                dll.Everything_GetBuildNumber(),
            )
            info["target_machine"] = TARGET_MACHINES.get(dll.Everything_GetTargetMachine())
        return info

    def ensure_running(self, timeout: float = 10.0) -> bool:
        if self.is_running():
            return True
        if os.environ.get("EVERYTHING_MCP_AUTOSTART", "").lower() not in ("1", "true", "yes"):
            return False
        exe = find_everything_exe()
        if not exe:
            return False
        try:
            subprocess.Popen([exe], close_fds=True)
        except OSError:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            time.sleep(0.5)
        return False


_SDK: EverythingSDK | None = None
_SDK_LOCK = threading.Lock()


def get_sdk() -> EverythingSDK:
    global _SDK
    if _SDK is None:
        with _SDK_LOCK:
            if _SDK is None:
                _SDK = EverythingSDK()
    return _SDK


def query(query: str = "", **kwargs) -> dict:
    return get_sdk().query(query, **kwargs)


def get_file_info(path: str) -> dict:
    return get_sdk().get_file_info(path)


def status() -> dict:
    return get_sdk().status()


def ensure_running(timeout: float = 10.0) -> bool:
    return get_sdk().ensure_running(timeout)


def is_ipc_error(exc: EverythingQueryError) -> bool:
    return exc.code == EVERYTHING_ERROR_IPC
