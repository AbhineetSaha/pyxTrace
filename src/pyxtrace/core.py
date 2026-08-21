"""
core.py – orchestrates a traced run and writes the .pyxt run artifact.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tracemalloc
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
from types import ModuleType
from typing import Optional, TypedDict

from pyxtrace.bytecode import FilteredTracer, ProfileTracer
from pyxtrace.run import save as save_run, to_dict as run_to_dict
from pyxtrace.visual import render_run, TraceVisualizer


# ---------------- async JSONL writer -------------------------------- #
class _AsyncLog:
    _END = object()

    def __init__(self, path: Path, flush_ms: float = 10):
        self._path = path
        self._q: SimpleQueue = SimpleQueue()
        self._f = self._path.open("a", buffering=1)
        self._period = flush_ms / 1_000
        threading.Thread(target=self._writer, daemon=True).start()

    def enqueue(self, obj: dict) -> None:
        self._q.put_nowait(obj)

    def close(self) -> None:
        self._q.put_nowait(self._END)

    def _writer(self) -> None:
        while (item := self._q.get()) is not self._END:
            self._f.write(json.dumps(item, default=str) + "\n")
            t0 = time.perf_counter()
            while (time.perf_counter() - t0) < self._period and not self._q.empty():
                nxt = self._q.get_nowait()
                if nxt is self._END:
                    item = self._END
                    break
                self._f.write(json.dumps(nxt, default=str) + "\n")
        self._f.flush()
        self._f.close()


# ---------------- public API types ---------------------------------- #
class Event(TypedDict, total=False):
    ts: float
    event: str
    func: str
    file: str
    line: int
    module: str


@dataclass
class TraceSession:
    script_path: Path
    log_path: Optional[Path] = None
    mode: str = "full"
    capture_returns: bool = False
    memory: bool = False
    events: bool = False        # write the raw JSONL event stream instead
    out: Optional[Path] = None  # .pyxt run file
    root: Optional[Path] = None  # dir to trace (default: the script's own dir)

    # ------------------------------------------------------------------ #
    @property
    def trace_root(self) -> Path:
        """Directory whose code is profiled.

        Defaults to the script's own directory, which misses a library that
        lives anywhere else — installed in site-packages, or one level up from
        a script in benchmarks/. Point --root at the package to trace it.
        """
        return Path(self.root).resolve() if self.root else self.script_path.parent

    # ------------------------------------------------------------------ #
    def _exec_script(self, tracer) -> None:
        """Run the target script under *tracer*."""
        # CPython puts the script's directory on sys.path for `python app.py`;
        # without this, any script importing a sibling module fails under pyxtrace.
        sys.path.insert(0, str(self.script_path.parent))
        spec = importlib.util.spec_from_file_location("__main__", self.script_path)
        assert spec is not None
        mod: ModuleType = importlib.util.module_from_spec(spec)
        sys.modules["__main__"] = mod
        sys.settrace(tracer)
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        finally:
            sys.settrace(None)

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        if not self.events:
            return self._run_profile()
        return self._run_events()

    # ------------------------------------------------------------------ #
    def _run_profile(self) -> None:
        """Default path: accumulate per-function totals, write a .pyxt run."""
        ts = time.strftime("%Y%m%d-%H%M%S")
        out = Path(self.out) if self.out else Path(f"pyxtrace-{ts}.pyxt").resolve()
        root = self.trace_root

        print(f"[pyxTrace] ➜ profiling '{self.script_path}' → {out}")
        tracer = ProfileTracer(root_path=root)
        try:
            self._exec_script(tracer)
        finally:
            print("[pyxTrace] ✔ finished")

        data = run_to_dict(tracer.stats, root=root, script=self.script_path.name)
        save_run(data, out)
        render_run(data, out)

    # ------------------------------------------------------------------ #
    def _run_events(self) -> None:
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.log_path = self.log_path or Path(f"pyxtrace-{ts}.jsonl").resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        os.environ["PYXTRACE_EVENT_LOG"] = str(self.log_path)

        if self.memory:
            tracemalloc.start()

        log = _AsyncLog(self.log_path)
        tracer = FilteredTracer(
            log,
            mode=self.mode,
            root_path=self.trace_root,           # only user files
            capture_returns=self.capture_returns,
            memory=self.memory,
        )

        print(
            f"[pyxTrace] ➜ tracing '{self.script_path}' "
            f"(mode={self.mode}) → {self.log_path}"
        )

        try:
            self._exec_script(tracer)
        finally:
            log.close()
            print("[pyxTrace] ✔ finished")

        TraceVisualizer.from_jsonl(self.log_path).render()


def run_tracer(
    script_path: Path,
    *,
    mode: str = "full",
    log_path: Path | None = None,
    out: Path | None = None,
    root: Path | None = None,
):
    """
    Profile *script_path* and write a .pyxt run to *out*.

    Passing ``log_path`` selects the raw JSONL event stream instead.
    """
    TraceSession(
        script_path,
        log_path=log_path,
        mode=mode,
        out=out,
        root=root,
        events=log_path is not None,
    ).run()
