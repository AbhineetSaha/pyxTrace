"""
core.py – orchestrates tracing + optional Streamlit dashboard replay.
"""

from __future__ import annotations

import importlib.util
import json
import multiprocessing as mp
import os
import shutil
import subprocess as sp
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
from types import ModuleType
from typing import Optional, TypedDict

from pyxtrace.bytecode import FilteredTracer
from pyxtrace.visual import serve_dashboard, TraceVisualizer


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


# ---------------- replay worker ------------------------------------- #
def _replay_worker(src_jsonl: str, dst_jsonl: str, fps: float) -> None:
    """
    Copy *all* rows from src → dst.

    • The first pass is written as fast as possible so the dashboard sees
      the full history immediately.
    • Subsequent passes honour the fps delay for a smooth “live” effect.
    """
    delay = 1.0 / max(fps, 1e-3)

    while True:
        # copy all rows from src to dst
        with open(src_jsonl, "r", encoding="utf-8") as fp_src, open(
            dst_jsonl, "a", encoding="utf-8"
        ) as fp_dst:
            # seek to current EOF of dst to avoid duplicates
            fp_dst.seek(0, 2)
            offset = fp_dst.tell()
            fp_src.seek(offset)

            for raw in fp_src:
                fp_dst.write(raw)
                fp_dst.flush()
                time.sleep(delay)

        # src file is exhausted – sleep briefly, then poll again
        time.sleep(delay)



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
    dash: bool = False
    fps: float = 20.0

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.log_path = self.log_path or Path(f"pyxtrace-{ts}.jsonl").resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        os.environ["PYXTRACE_EVENT_LOG"] = str(self.log_path)

        log = _AsyncLog(self.log_path)
        tracer = None
        tracer = FilteredTracer(
            log,
            mode=self.mode,
            root_path=self.script_path.parent,   # only user files
        )

        print(
            f"[pyxTrace] ➜ tracing '{self.script_path}' "
            f"(mode={self.mode}) → {self.log_path}"
        )

        sys.settrace(tracer)

        spec = importlib.util.spec_from_file_location("__main__", self.script_path)
        assert spec is not None
        mod: ModuleType = importlib.util.module_from_spec(spec)
        sys.modules["__main__"] = mod
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        finally:
            sys.settrace(None)
            log.close()
            print("[pyxTrace] ✔ finished")

        # --- dashboard & replay -------------------------------------- #
        if self.dash:
            tmp_dir = Path(tempfile.mkdtemp(prefix="pyxtrace-replay-"))
            stream_path = tmp_dir / self.log_path.name
            stream_path.touch()

            replay_proc = mp.Process(
                target=_replay_worker,
                args=(str(self.log_path), str(stream_path), self.fps),
                daemon=True,
            )
            replay_proc.start()

            dash_proc = mp.Process(
                target=serve_dashboard,
                args=(str(stream_path),),
                daemon=True,
            )
            dash_proc.start()

            print("[pyxTrace] Streamlit dashboard: http://127.0.0.1:8050  (CTRL-C to stop)")
            try:
                while dash_proc.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                dash_proc.terminate()
                replay_proc.terminate()
                dash_proc.join()
                replay_proc.join()
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            TraceVisualizer.from_jsonl(self.log_path).render()


def run_tracer(script_path: Path, *, mode: str = "full", log_path: Path | None = None):
    TraceSession(script_path, log_path=log_path, mode=mode).run()
