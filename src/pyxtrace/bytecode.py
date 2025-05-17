"""
bytecode.py – FilteredTracer
• full  – trace everything
• perf  – skip std-lib/built-ins
• demo  – call/return only
In all modes we also filter by root_path so only events from the user’s
script (or its siblings) are logged.
Every accepted BytecodeEvent is accompanied by an inline MemoryEvent.
"""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path
from types import FrameType
from typing import Any, Dict, Set

tracemalloc.start()

__all__ = ["FilteredTracer"]

_SKIP_MODULES: Set[str] = {
    "builtins",
    "sys",
    "types",
    "importlib",
    "collections",
    "abc",
    "functools",
    "inspect",
    "posixpath",
    "genericpath",
    "io",
    "logging",
}


class FilteredTracer:
    def __init__(
        self,
        log,
        mode: str = "full",
        *,
        root_path: str | Path | None = None,  # keep only this dir/file
    ) -> None:
        self._log = log
        self._mode = mode
        self._root = None if root_path is None else Path(root_path).resolve()

    # ------------------------------------------------------------------ #
    def __call__(self, frame: FrameType, event: str, arg) -> "FilteredTracer | None":  # noqa: D401
        # 1) perf/demo skip list
        if self._mode in ("perf", "demo"):
            mod = frame.f_globals.get("__name__", "")
            if mod.split(".", 1)[0] in _SKIP_MODULES:
                return None

        # 2) root-path filter
        if self._root is not None:
            try:
                fpath = Path(frame.f_code.co_filename).resolve()
            except RuntimeError:  # built-in frames
                return None
            if fpath != self._root and self._root not in fpath.parents:
                return None

        # 3) demo mode – keep only call/return
        if self._mode == "demo" and event not in ("call", "return"):
            return self

        ts_now = time.perf_counter()
        rec: Dict[str, Any] = {
            "ts": ts_now,
            "kind": "BytecodeEvent",
            "event": event,
            "func": frame.f_code.co_name,
            "file": frame.f_code.co_filename,
            "line": frame.f_lineno,
            "module": frame.f_globals.get("__name__", ""),
        }
        if event == "return":
            rec["return_value"] = repr(arg)

        # inline heap snapshot
        cur, peak = tracemalloc.get_traced_memory()
        self._log.enqueue(
            {
                "kind": "MemoryEvent",
                "ts": ts_now,
                "payload": {"current_kb": cur // 1024, "peak_kb": peak // 1024},
            }
        )

        self._log.enqueue(rec)
        return self  # keep tracing nested calls

BytecodeTracer = FilteredTracer