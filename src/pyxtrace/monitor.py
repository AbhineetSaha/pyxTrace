"""
monitor.py – MonitorTracer, the sys.monitoring (PEP 669) counter used for test suites on 3.12+.

Counts the same things as ``ProfileTracer`` (calls, lines, and who called
whom) in less time on real suites (jinja 14.2 s → 10.8 s, rich 12.5 s →
9.8 s), because code outside the root is switched off for good, by returning
``DISABLE`` from its first event, instead of being filtered on every call.
Two things keep that saving:

- The tool is registered once per process and never restarted. Restarting,
  or re-registering per test, throws every ``DISABLE`` away and re-instruments
  all code; measured, that made it slower than settrace.
- Counters are keyed by ``id(code)``, with the code object kept alive here so
  the id stays unique. Keying by the code object hashes it on every event,
  which cost 4x.

The counts are *not* interchangeable with ProfileTracer's. settrace reports a
generator resuming as a call and ``PY_START`` does not, so ``calls`` differ
wherever generators run. A run file records which tracer made it, and runs from
different tracers are never compared.

``own_time`` is not measured: it needs a call stack, which is what this
avoids. The test-suite report does not show it.
"""

from __future__ import annotations

import sys
import threading
from collections import Counter
from pathlib import Path
from types import CodeType, FrameType
from typing import Any, Dict, List, Tuple

from pyxtrace.bytecode import Key, in_root

NAME = "monitoring"


def available() -> bool:
    """sys.monitoring exists (3.12+), and the GIL is on.

    Free-threaded builds stay on settrace until this tracer is tested there.
    """
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    return hasattr(sys, "monitoring") and gil


class _Tool:
    """The process-wide registration, made once and kept."""

    id: int | None = None
    root: Path | None = None

    @classmethod
    def acquire(cls, root: Path) -> int:
        mon = sys.monitoring  # type: ignore[attr-defined]
        if cls.id is None:
            tool = mon.PROFILER_ID
            mon.use_tool_id(tool, "pyxtrace")  # ValueError if a profiler holds it
            cls.id = tool
        elif cls.root != root:
            # what was switched off was outside the old root, not this one
            mon.restart_events()
        cls.root = root
        return cls.id


class MonitorTracer:
    """Counts calls and lines of code under *root_path* between start() and stop()."""

    def __init__(self, *, root_path: str | Path, max_calls: int = 0) -> None:
        self._root = Path(root_path).resolve()
        # past max_calls (0: never) counting stops and `capped` is set
        self._max_calls = max_calls or sys.maxsize
        self.capped = False
        self._codes: Dict[int, CodeType] = {}  # keeps every counted code object alive
        self._shards: List[Tuple[Counter, Counter, Counter]] = []
        self._local = threading.local()
        self._boundary: FrameType | None = None
        self._tool: int | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        mon = sys.monitoring  # type: ignore[attr-defined]
        self._tool = _Tool.acquire(self._root)
        # a call's caller is the nearest counted frame below it, but never one
        # from before start(): settrace's call stack began here too
        self._boundary = sys._getframe(1)
        on_start, on_line, on_jump = self._callbacks()
        mon.register_callback(self._tool, mon.events.PY_START, on_start)
        mon.register_callback(self._tool, mon.events.LINE, on_line)
        mon.register_callback(self._tool, mon.events.JUMP, on_jump)
        mon.set_events(self._tool, mon.events.PY_START | mon.events.LINE | mon.events.JUMP)

    def stop(self) -> None:
        mon = sys.monitoring  # type: ignore[attr-defined]
        mon.set_events(self._tool, 0)
        mon.register_callback(self._tool, mon.events.PY_START, None)
        mon.register_callback(self._tool, mon.events.LINE, None)
        mon.register_callback(self._tool, mon.events.JUMP, None)
        self._boundary = None

    def _callbacks(self):  # noqa: C901 - three hot closures sharing state
        DISABLE = sys.monitoring.DISABLE  # type: ignore[attr-defined]
        root, keep_cache, codes = self._root, _keep_cache(self._root), self._codes
        local, shards = self._local, self._shards
        getframe = sys._getframe
        boundary, tool = self._boundary, self._tool

        def keep(filename: str) -> bool:
            k = keep_cache.get(filename)
            if k is None:
                # pyxtrace's own code runs while counting (stop(), the pytest
                # plugin); settrace never sees it, and neither may this
                k = keep_cache[filename] = in_root(filename, root, {}) and not _own(filename)
            return k

        def shard() -> Tuple[Counter, Counter, Counter]:
            # one per thread: `counter[k] += 1` is three bytecodes, and two
            # threads interleaving them would lose an update
            try:
                return local.shard
            except AttributeError:
                s: Tuple[Counter, Counter, Counter] = (Counter(), Counter(), Counter())
                local.shard = s
                shards.append(s)  # list.append is atomic
                return s

        calls_left = self._max_calls

        def on_start(code: CodeType, offset: int) -> Any:
            nonlocal calls_left
            if not keep(code.co_filename):
                return DISABLE  # for good: this code is never reported again
            calls_left -= 1
            if calls_left < 0:
                # the rest runs at full speed; the counts are partial
                self.capped = True
                sys.monitoring.set_events(tool, 0)  # type: ignore[attr-defined]
                return None
            i = id(code)
            if i not in codes:
                codes[i] = code
            _, calls, edges = shard()
            calls[i] += 1
            f = getframe(1).f_back  # frame 1 is the one starting; its f_back called it
            while f is not None and f is not boundary:
                c = f.f_code
                if keep(c.co_filename):
                    j = id(c)
                    if j not in codes:
                        codes[j] = c
                    edges[(j, i)] += 1
                    break
                f = f.f_back

        boundary_code = boundary.f_code if boundary is not None else None

        def on_line(code: CodeType, line: int) -> Any:
            i = id(code)
            if code is boundary_code and getframe(1) is boundary:
                return None  # the frame that called start(): it began before counting did
            if i not in codes:
                if not keep(code.co_filename):
                    return DISABLE
                codes[i] = code
            shard()[0][i] += 1

        # LINE fires only when the line number changes, so a loop that jumps
        # back to its own line (any one-line comprehension) would count one
        # line however many times it ran. settrace reports a line on every
        # backward jump; this counts the ones LINE misses.
        loops: Dict[Tuple[int, int], bool] = {}
        judged: Dict[int, CodeType] = {}

        def on_jump(code: CodeType, src: int, dest: int) -> Any:
            if code is boundary_code and getframe(1) is boundary:
                return None
            i = id(code)
            same = loops.get((i, src))
            if same is None:
                same = loops[(i, src)] = (
                    dest < src and keep(code.co_filename) and _line_at(code, src) == _line_at(code, dest)
                )
                # kept alive whatever the answer: `loops` is keyed by id, and a
                # template engine's freed code could hand its id to new code.
                # Not in `codes`, which on_line reads as "counted code"
                judged[i] = code
            if not same:
                return DISABLE  # whether a jump qualifies never changes
            if i not in codes:
                codes[i] = code
            shard()[0][i] += 1

        return on_start, on_line, on_jump

    # ------------------------------------------------------------------ #
    @property
    def stats(self) -> Dict[Key, Dict[str, Any]]:
        """Per-function totals across every thread, shaped like ProfileTracer.stats."""
        def key(i: int) -> Key:
            code = self._codes[i]
            return (code.co_filename, code.co_qualname)  # type: ignore[attr-defined]

        merged: Dict[Key, Dict[str, Any]] = {}

        def rec(k: Key) -> Dict[str, Any]:
            r = merged.get(k)
            if r is None:
                r = merged[k] = {"calls": 0, "lines": 0, "own_time": 0.0, "callees": Counter()}
            return r

        for lines, calls, edges in list(self._shards):
            for i, n in dict(lines).items():
                rec(key(i))["lines"] += n
            for i, n in dict(calls).items():
                rec(key(i))["calls"] += n
            for (caller, callee), n in dict(edges).items():
                rec(key(caller))["callees"][key(callee)] += n
        return merged


_OWN_DIR = str(Path(__file__).resolve().parent)


def _own(filename: str) -> bool:
    try:
        return str(Path(filename).resolve().parent) == _OWN_DIR
    except OSError:
        return False


def _line_at(code: CodeType, offset: int) -> int | None:
    for start, end, line in code.co_lines():
        if start <= offset < end:
            return line
    return None


# in_root answers per root, shared by every tracer in the process: the answer
# for a file never changes, and resolving paths again per test costs a stat each
_KEEP: Dict[Path, Dict[str, bool]] = {}


def _keep_cache(root: Path) -> Dict[str, bool]:
    return _KEEP.setdefault(root, {})
