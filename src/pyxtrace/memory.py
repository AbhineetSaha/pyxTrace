"""
memory.py – periodic heap snapshots via tracemalloc.
"""

from __future__ import annotations
import json, os, time, tracemalloc, threading
from dataclasses import dataclass, asdict
from typing import Optional
from queue import Queue

from .core import Event

@dataclass
class MemoryEvent:
    ts: float
    current_kb: int
    peak_kb: int

# ───────────────────────────────── tracer ──────────────────────────────
class MemoryTracer:
    def __init__(self, queue: Optional[Queue[Event]] = None,
                 interval: float = 0.05):
        self.queue  = queue
        self.interval = interval

        log_path = os.environ.get("PYXTRACE_EVENT_LOG")
        self._fp = open(log_path, "a", buffering=1) if log_path else None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        tracemalloc.start()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ------------------------------------------------------------------ #
    def _poll_loop(self) -> None:
        while True:
            cur, peak = tracemalloc.get_traced_memory()
            ev = MemoryEvent(time.time(), cur // 1024, peak // 1024)

            self._write({"kind": "MemoryEvent", "ts": ev.ts,
                         "payload": asdict(ev)})

            if self.queue is not None:
                self.queue.put(Event(ev.ts, "MemoryEvent", asdict(ev)))

            time.sleep(self.interval)

    # ------------------------------------------------------------------ #
    def _write(self, rec: dict) -> None:
        if self._fp:
            json.dump(rec, self._fp); self._fp.write("\n"); self._fp.flush()