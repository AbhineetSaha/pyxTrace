"""
Byte‑code tracer for CPython 3.12/3.13
⇒ pushes every event to the live queue
⇒ captures the operand stack (3.12)
"""

from __future__ import annotations
import dis, json, os, sys, threading, time
from dataclasses import dataclass, asdict
from types import FrameType
from typing import List, Optional, Union
from queue import Queue

from .core import Event            # your own simple dataclass

# ───────────────────────────────── dataclass ───────────────────────────
@dataclass
class BytecodeEvent:
    ts: float
    func: str
    lineno: int
    opoffset: int
    opname: str
    stack: List[str]

# ───────────────────────────────── tracer ──────────────────────────────
class BytecodeTracer:
    """
    queue may be
      • None   → only write JSON lines
      • True   → create an in‑process Queue and expose it at `.queue`
      • Queue  → use the queue provided by the caller
    """
    def __init__(self, queue: Union[None, bool, Queue[Event]] = None):
        if queue is True:
            queue = Queue()
        self.queue: Optional[Queue[Event]] = queue

        log_path = os.environ.get("PYXTRACE_EVENT_LOG")
        self._fp = open(log_path, "a", buffering=1) if log_path else None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        # ‘START’ marker so that even <1 ms scripts have at least 1 row
        self._write({"kind": "BytecodeEvent",
                     "ts": time.time(),
                     "payload": {"op": "START", "offset": 0}})

        sys.settrace(self._trace)          # current thread
        threading.settrace(self._trace)    # every new thread

    # ------------------------------------------------------------------ #
    def _trace(self, frame: FrameType, event: str, arg):
        if event == "call":
            frame.f_trace_opcodes = True
            return self._trace

        if event == "opcode":
            code   = frame.f_code
            offset = frame.f_lasti
            instr  = next((i for i in dis.get_instructions(code)
                           if i.offset == offset), None)

            opname = instr.opname if instr else f"OP_{offset}"

            # ---- value stack (only before the opcode runs) ------------
            try:
                val_stack = frame.stack        # CPython 3.12
            except AttributeError:
                val_stack = []                 # 3.13 → not available

            ev = BytecodeEvent(
                ts=time.time(),
                func=code.co_name,
                lineno=frame.f_lineno,
                opoffset=offset,
                opname=opname,
                stack=[repr(x) for x in val_stack],
            )

            self._write({"kind": "BytecodeEvent", "ts": ev.ts,
                         "payload": asdict(ev)})

            if self.queue is not None:
                self.queue.put(Event(ev.ts, "BytecodeEvent", asdict(ev)))

        return self._trace

    # ------------------------------------------------------------------ #
    def _write(self, rec: dict) -> None:
        if self._fp:
            json.dump(rec, self._fp); self._fp.write("\n"); self._fp.flush()