"""
bytecode.py – FilteredTracer
• full  – trace everything
• perf  – skip std-lib/built-ins
• demo  – call/return only
In all modes we also filter by root_path so only events from the user’s
script (or its siblings) are logged.

The root-path decision is cached per ``co_filename``: resolving the path on
every event costs a stat(2) syscall and dominated the tracer's runtime
(~160x slower than the cached form).
"""

from __future__ import annotations

import time
import tracemalloc
from collections import Counter
from pathlib import Path
from types import FrameType
from typing import Any, Dict, List, Set, Tuple

__all__ = ["FilteredTracer", "ProfileTracer"]

Key = Tuple[str, str]  # (file, function)

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


def in_root(filename: str, root: Path, cache: Dict[str, bool]) -> bool:
    """
    Is *filename* inside *root*?  The answer is memoised in *cache*.

    Resolving the path on every trace event costs a stat(2) syscall and was
    ~160x more expensive than this lookup.  Code objects reuse the same
    ``co_filename`` string, so the cache hits on all but the first event
    per source file.
    """
    keep = cache.get(filename)
    if keep is None:
        if filename.startswith("<"):
            # pseudo-files: "<string>", "<frozen importlib._bootstrap>", …
            keep = False
        else:
            try:
                fpath = Path(filename).resolve()
            except (OSError, RuntimeError):  # built-in / unresolvable frames
                keep = False
            else:
                keep = fpath == root or root in fpath.parents
        cache[filename] = keep
    return keep


class FilteredTracer:
    """Raw event-stream tracer — writes one JSONL record per trace event."""

    def __init__(
        self,
        log,
        mode: str = "full",
        *,
        root_path: str | Path | None = None,  # keep only this dir/file
        capture_returns: bool = False,        # repr() every return value
        memory: bool = False,                 # inline tracemalloc snapshots
    ) -> None:
        self._log = log
        self._mode = mode
        self._root = None if root_path is None else Path(root_path).resolve()
        self._capture_returns = capture_returns
        self._memory = memory
        self._keep: Dict[str, bool] = {}

    # ------------------------------------------------------------------ #
    def _in_root(self, filename: str) -> bool:
        return in_root(filename, self._root, self._keep)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    def __call__(self, frame: FrameType, event: str, arg) -> "FilteredTracer | None":  # noqa: D401
        # 1) perf/demo skip list
        if self._mode in ("perf", "demo"):
            mod = frame.f_globals.get("__name__", "")
            if mod.split(".", 1)[0] in _SKIP_MODULES:
                return None

        # 2) root-path filter
        if self._root is not None and not self._in_root(frame.f_code.co_filename):
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
        if event == "return" and self._capture_returns:
            rec["return_value"] = repr(arg)

        # inline heap snapshot (opt-in: costs ~8x on the hot path)
        # is_tracing(): FilteredTracer is public, and a caller who builds one
        # directly may not have started tracemalloc. Skip rather than raise.
        if self._memory and tracemalloc.is_tracing():
            # ponytail: one snapshot per event is wasteful — the value moves far
            # slower than the event rate. Sample on an interval if this matters.
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


class ProfileTracer:
    """
    Accumulating tracer — the one the regression gate uses.

    Keeps per-function totals in memory instead of writing a record per event,
    and is serialised once when the run ends.  ``calls`` and ``lines`` are
    exactly reproducible across runs; ``own_time`` is not, and is for display
    only.  See PYXTRACE_PRODUCT_STRATEGY.md §9.

    ``callees`` records how many times each function called each other
    function — the signal an N+1 query shows up in.
    """

    def __init__(self, *, root_path: str | Path | None = None) -> None:
        self._root = None if root_path is None else Path(root_path).resolve()
        self._keep: Dict[str, bool] = {}
        self.stats: Dict[Key, Dict[str, Any]] = {}
        # frames in flight: [key, entered_at, time spent in callees, stats dict]
        self._stack: List[List[Any]] = []

    # ------------------------------------------------------------------ #
    def _rec(self, key: Key) -> Dict[str, Any]:
        s = self.stats.get(key)
        if s is None:
            s = self.stats[key] = {
                "calls": 0,
                "lines": 0,
                "own_time": 0.0,
                "callees": Counter(),
            }
        return s

    # ------------------------------------------------------------------ #
    # Hot path.  Branches are ordered by event frequency (line > call >
    # return), the frame's own stats dict is carried on the stack so a line
    # event costs one index plus one increment, and the root check runs only
    # on "call" — a frame that reached us with any other event was already
    # accepted when it was called.
    def __call__(self, frame: FrameType, event: str, arg) -> "ProfileTracer | None":  # noqa: D401
        stack = self._stack

        if event == "line":
            if stack:
                stack[-1][3]["lines"] += 1
            return self

        if event == "call":
            code = frame.f_code
            if self._root is not None and not in_root(
                code.co_filename, self._root, self._keep
            ):
                return None
            key = (code.co_filename, code.co_name)
            s = self._rec(key)
            s["calls"] += 1
            if stack:
                # ponytail: an untraced frame between caller and callee shifts
                # this edge to the nearest traced ancestor. Fine while all user
                # code lives under one root; revisit if that stops holding.
                stack[-1][3]["callees"][key] += 1
            stack.append([key, time.perf_counter(), 0.0, s])
            return self

        if event == "return":
            if not stack:  # unbalanced (generator close, exception unwind)
                return self
            _key, entered, in_callees, s = stack.pop()
            elapsed = time.perf_counter() - entered
            # own_time stays correct under recursion; cumulative time does not,
            # so it is deliberately not collected.
            s["own_time"] += elapsed - in_callees
            if stack:
                stack[-1][2] += elapsed
            return self

        return self


BytecodeTracer = FilteredTracer

