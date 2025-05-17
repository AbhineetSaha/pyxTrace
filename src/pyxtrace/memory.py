"""
memory.py – periodic heap snapshots, but only when the current call-stack
touches root_path.  Use it for long-running programs; for short scripts
the inline snapshots in FilteredTracer are usually enough.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
import time
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Queue
from typing import Optional, TypedDict, TYPE_CHECKING


class _Event(TypedDict, total=False):
    ts: float
    kind: str
    payload: dict


Event = _Event if TYPE_CHECKING else dict


@dataclass
class MemoryEvent:
    ts: float
    current_kb: int
    peak_kb: int


class MemoryTracer:
    def __init__(
        self,
        *,
        root_path: str | Path | None = None,
        interval: float = 0.05,
        queue: Optional[Queue[Event]] = None,
    ):
        self.root = None if root_path is None else Path(root_path).resolve()
        self.interval = interval
        self.queue = queue

        log_path = os.environ.get("PYXTRACE_EVENT_LOG")
        self._fp = open(log_path, "a", buffering=1) if log_path else None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        tracemalloc.start()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ------------------------------------------------------------------ #
    def _poll_loop(self) -> None:
        while True:
            if self._should_record():
                cur, peak = tracemalloc.get_traced_memory()
                ev = MemoryEvent(time.time(), cur // 1024, peak // 1024)
                rec = {"kind": "MemoryEvent", "ts": ev.ts, "payload": asdict(ev)}
                self._write(rec)
                if self.queue is not None:
                    self.queue.put(rec)  # type: ignore[arg-type]
            time.sleep(self.interval)

    # ------------------------------------------------------------------ #
    def _should_record(self) -> bool:
        if self.root is None:
            return True
        for fi in inspect.stack():
            try:
                fp = Path(fi.filename).resolve()
            except RuntimeError:
                continue
            if fp == self.root or self.root in fp.parents:
                return True
        return False

    # ------------------------------------------------------------------ #
    def _write(self, rec: dict) -> None:
        if self._fp:
            json.dump(rec, self._fp)
            self._fp.write("\n")
            self._fp.flush()
