"""
core.py – orchestrates syscall, byte‑code and memory tracers.

    •  SyscallTracer  (optional)  – live, publishes to an in‑memory queue
    •  BytecodeTracer & MemoryTracer  – write JSONL; parent tails the file
"""

from __future__ import annotations

import json
import os
import subprocess as sp
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Sequence

from rich.console import Console
from rich.table import Table

# ───────────────────────── Event object ────────────────────────────────
@dataclass
class Event:
    ts: float
    kind: str
    payload: dict = field(default_factory=dict)


# internal imports AFTER Event to avoid circular import
from .bytecode import BytecodeTracer          # noqa: E402  (only needed inside child)
from .memory   import MemoryTracer            # noqa: E402
from .syscalls import SyscallTracer           # noqa: E402
from .visual   import TraceVisualizer         # noqa: E402


# ───────────────────────── TraceSession ────────────────────────────────
class TraceSession:
    def __init__(self, *, trace_syscalls: bool = False, live_console: bool = True):
        self.trace_syscalls = trace_syscalls
        self.live_console   = live_console
        self._queue: "Queue[Event]" = Queue()

    # ─────────────────────── public API ────────────────────────────────
    def trace(
        self,
        script_path: str | os.PathLike[str],
        argv: Sequence[str] | None = None,
        *,
        open_dashboard: bool = False,
    ) -> None:
        """Run *script_path* under full tracing."""
        script_path = Path(script_path).resolve()
        if not script_path.exists():
            raise FileNotFoundError(script_path)

        # ── 0) prepare a temporary JSONL file ──────────────────────────
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="pyxtrace_", suffix=".jsonl")
        os.close(tmp_fd)                       # we’ll re‑open it as needed

        env = os.environ.copy()
        env["PYXTRACE_EVENT_LOG"] = tmp_path   # children will append here

        # ── (optional) start the Dash dashboard immediately ───────────
        dash_thread: threading.Thread | None = None
        if open_dashboard:
            vis_live = TraceVisualizer(tmp_path, live=True)
            dash_thread = threading.Thread(
                target=vis_live.dash, name="dash-server", daemon=True
            )
            dash_thread.start()               # server up → browser pops open

        # ── Rich console summary table ─────────────────────────────────
        console = Console()
        table: Table | None = None
        if self.live_console:
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("t (s)",  style="cyan", no_wrap=True)
            table.add_column("event")
            table.add_column("details")

        # ── child bootstrap (one‑liner) ────────────────────────────────
        pause = (
            "import signal,os;"
            "signal.signal(signal.SIGUSR1, lambda *_: None);"
            "os.kill(os.getpid(), signal.SIGSTOP);"        # let dtruss attach
            if self.trace_syscalls and SyscallTracer.available()
            else ""
        )

        launcher = (
            "import os,runpy,sys;"                         # stdlib
            f"{pause}"
            "from pyxtrace.bytecode import BytecodeTracer;"
            "from pyxtrace.memory   import MemoryTracer;"
            "bt=BytecodeTracer(); mt=MemoryTracer();"
            "bt.start(); mt.start();"
            "runpy.run_path(sys.argv[1], run_name='__main__')"
        )

        child_cmd = [sys.executable, "-u", "-c", launcher, str(script_path)]
        if argv:
            child_cmd += list(argv)

        # ── 1) launch tracer or plain child ────────────────────────────
        if self.trace_syscalls and SyscallTracer.available():
            tracer      = SyscallTracer(command=child_cmd, queue=self._queue)
            wait_thread = threading.Thread(
                target=tracer.run, name="syscall-tracer", daemon=True
            )
        else:
            proc        = sp.Popen(child_cmd, env=env)
            wait_thread = threading.Thread(
                target=proc.wait,  name="child-wait",      daemon=True
            )

        wait_thread.start()

        # ── 2) tail the JSONL file and push rows into our queue ───────
        def _tail(path: str) -> None:
            with open(path, "r", buffering=1) as fp:
                while wait_thread.is_alive():              # stop when child ends
                    pos  = fp.tell()
                    line = fp.readline()
                    if not line:
                        time.sleep(0.05)
                        fp.seek(pos)
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        # picked up an incomplete line → retry shortly
                        if ' "BytecodeEvent"' in line:
                            continue
                        time.sleep(0.02)
                        continue
                    self._queue.put(
                        Event(rec["ts"], rec["kind"], rec.get("payload", {}))
                    )

        tail_thread = threading.Thread(
            target=_tail, args=(tmp_path,), name="jsonl-tail", daemon=True
        )
        tail_thread.start()

        # ── 3) consumer → nicely formatted Rich table rows ────────────
        stop_token = object()

        def _consume() -> None:
            while True:
                try:
                    ev: Event = self._queue.get(timeout=0.1)
                except Empty:
                    if not wait_thread.is_alive():
                        break
                    continue
                if ev is stop_token:
                    break
                # skip byte‑code spam unless you really want it
                if ev.kind == "BytecodeEvent":
                    continue
                if table:
                    table.add_row(f"{ev.ts:.3f}", ev.kind, str(ev.payload)[:120])

        cons_thread = threading.Thread(
            target=_consume, name="event-consumer", daemon=True
        )
        cons_thread.start()

        # ── 4) wait for everything to finish ──────────────────────────
        wait_thread.join()     # traced program (or syscall tracer) ended
        tail_thread.join()     # stop tailing the JSONL file
        self._queue.put(stop_token)
        cons_thread.join()

        # ── 5) final Rich summary (and keep dashboard alive) ───────────
        if table:
            console.print(table)

        # static summary (non‑live) – prints counts etc.
        TraceVisualizer.from_jsonl(tmp_path).render()

        # The Dash thread is a daemon → the process will exit when the
        # user closes the browser or interrupts with Ctrl‑C.