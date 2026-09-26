"""
bytecode.py – ProfileTracer, the sys.settrace hook behind the regression gate.

Only frames whose file lives under ``root_path`` are counted, and that decision
is cached per ``co_filename``: resolving the path on every event costs a
stat(2) syscall and dominated the tracer's runtime (~160x slower than the
cached form).
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from operator import attrgetter
from pathlib import Path
from types import FrameType
from typing import Any, Dict, List, Tuple

Key = Tuple[str, str]  # (file, qualified function name)

# `Cursor.execute`, not `execute`: two classes' `__init__` in one file must not
# merge into one entry. co_qualname is 3.11+; 3.10 falls back to the bare name.
_qualname = attrgetter("co_qualname" if sys.version_info >= (3, 11) else "co_name")


def in_root(filename: str, root: Path, cache: Dict[str, bool]) -> bool:
    """
    Is *filename* inside *root*?  The answer is memoised in *cache*.

    Code objects reuse the same ``co_filename`` string, so the cache hits on
    all but the first event per source file.
    """
    keep = cache.get(filename)
    if keep is None:
        if filename.startswith("<"):
            # pseudo-files: "<string>", "<frozen importlib._bootstrap>", …
            keep = False
        else:
            try:
                keep = Path(filename).resolve().is_relative_to(root)
            except (OSError, RuntimeError):  # built-in / unresolvable frames
                keep = False
        cache[filename] = keep
    return keep


class ProfileTracer:
    """
    Accumulating tracer — the one the regression gate uses.

    Keeps per-function totals in memory instead of writing a record per event,
    and is serialised once when the run ends.  ``calls`` and ``lines`` are
    exactly reproducible across runs; ``own_time`` is not, and is for display
    only.  See PYXTRACE_PRODUCT_STRATEGY.md §9.

    ``callees`` records how many times each function called each other
    function, which is what attributes a regression to the call that caused it.

    Every thread counts into its own shard, merged when ``stats`` is read, so
    no counter is ever shared between threads: a lost update would make the
    counts nondeterministic, and nothing but the GIL would prevent one.
    """

    def __init__(self, *, root_path: str | Path | None = None) -> None:
        self._root = None if root_path is None else Path(root_path).resolve()
        self._keep: Dict[str, bool] = {}
        # one shard per thread that ever ran traced code; list.append is atomic
        self._shards: List[Dict[Key, Dict[str, Any]]] = []
        # frames in flight: [key, entered_at, time spent in callees, stats dict]
        # one stack per thread: sys.settrace is per-thread, and a shared list
        # would interleave frames from different threads into one call chain
        self._local = threading.local()

    # ------------------------------------------------------------------ #
    @property
    def stats(self) -> Dict[Key, Dict[str, Any]]:
        """Per-function totals across every thread."""
        merged: Dict[Key, Dict[str, Any]] = {}
        for shard in list(self._shards):
            # dict() copies in one C call, so a daemon thread still running
            # cannot change a shard's size under this loop
            for key, s in dict(shard).items():
                m = merged.get(key)
                if m is None:
                    m = merged[key] = _new_rec()
                m["calls"] += s["calls"]
                m["lines"] += s["lines"]
                m["own_time"] += s["own_time"]
                m["callees"].update(dict(s["callees"]))
        return merged

    def _shard(self) -> Dict[Key, Dict[str, Any]]:
        try:
            return self._local.shard
        except AttributeError:
            shard = self._local.shard = {}
            self._shards.append(shard)
            return shard

    # ------------------------------------------------------------------ #
    # Hot path.  Branches are ordered by event frequency (line > call >
    # return), the frame's own stats dict is carried on the stack so a line
    # event costs one index plus one increment, and the root check runs only
    # on "call" — a frame that reached us with any other event was already
    # accepted when it was called.
    def __call__(self, frame: FrameType, event: str, arg) -> "ProfileTracer | None":  # noqa: D401
        # try/except, not getattr(..., default): the miss happens once per
        # thread and a non-raising try costs nothing, while getattr pays a
        # function call on every event. Measured 51x -> 49x on fib(22).
        try:
            stack = self._local.stack
        except AttributeError:
            stack = self._local.stack = []

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
            key = (code.co_filename, _qualname(code))
            shard = self._shard()
            s = shard.get(key)
            if s is None:
                s = shard[key] = _new_rec()
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


def _new_rec() -> Dict[str, Any]:
    return {"calls": 0, "lines": 0, "own_time": 0.0, "callees": Counter()}
