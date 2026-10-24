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
from pathlib import Path
from types import FrameType
from typing import Any, Dict, List, Tuple

Key = Tuple[str, str]  # (file, qualified function name)

# `Cursor.execute`, not `execute`: two classes' `__init__` in one file must not
# merge into one entry. co_qualname is 3.11+; on 3.10 the key is the bare name
# and first line, which names.py turns into the same qualified name from source.
_QUALNAME = sys.version_info >= (3, 11)
# separates the two parts of a 3.10 key; no name contains it. Defined here, not
# in names.py, so this module still loads on its own (benchmarks/overhead.py)
SEP = "\x00"


# installed code that can sit inside a project: never the project's own
_INSTALLED = {"site-packages", "dist-packages", "node_modules", "__pypackages__"}


def _installed(path: Path, root: Path) -> bool:
    """Is *path* inside a virtualenv or package directory under *root*?

    A virtualenv is recognised by its ``pyvenv.cfg``, whatever it is called
    (.venv, venv, .tox/py312, .nox/tests).
    """
    d = root
    for part in path.relative_to(root).parts[:-1]:
        d = d / part
        if part in _INSTALLED or (d / "pyvenv.cfg").is_file():
            return True
    return False


def in_root(filename: str, root: Path, cache: Dict[str, bool]) -> bool:
    """
    Is *filename* the project's own code under *root*?  Memoised in *cache*.

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
                path = Path(filename).resolve()
                keep = path.is_relative_to(root) and not _installed(path, root)
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
    only.

    ``callees`` records how many times each function called each other
    function, which is what attributes a regression to the call that caused it.

    Every thread counts into its own shard, merged when ``stats`` is read, so
    no counter is ever shared between threads: a lost update would make the
    counts nondeterministic, and nothing but the GIL would prevent one.
    """

    def __init__(self, *, root_path: str | Path | None = None, max_calls: int = 0) -> None:
        self._root = None if root_path is None else Path(root_path).resolve()
        # past max_calls (0: never) counting stops and `capped` is set
        self._calls_left = max_calls or sys.maxsize
        self.capped = False
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
            # a thread's first event: its stack and its shard are made together,
            # so the call path below reads the shard without a method call
            stack = self._local.stack = []
            self._local.shard = {}
            self._shards.append(self._local.shard)

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
            self._calls_left -= 1
            if self._calls_left < 0:
                self._give_up(frame)
                return None
            # a flag test, not attrgetter: the plain attribute read is ~4% faster
            key = (code.co_filename, code.co_qualname if _QUALNAME  # type: ignore[attr-defined]
                   else f"{code.co_name}{SEP}{code.co_firstlineno}")
            shard = self._local.shard
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

    def _give_up(self, frame: FrameType) -> None:
        """Stop tracing this thread, so the rest runs at full speed; the counts are partial."""
        self.capped = True
        sys.settrace(None)
        f: FrameType | None = frame
        while f is not None:  # frames already traced would go on reporting lines
            f.f_trace = None
            f = f.f_back


def _new_rec() -> Dict[str, Any]:
    return {"calls": 0, "lines": 0, "own_time": 0.0, "callees": Counter()}
