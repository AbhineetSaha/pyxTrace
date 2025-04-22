"""
Darwin (macOS) SyscallTracer – attach with dtruss even when SIP is ON.
"""
from __future__ import annotations

import os, signal, shutil, subprocess as sp, re
from dataclasses import dataclass, asdict
from queue import Queue
from typing import Sequence

from ..core import Event
from .base import SyscallTracerBase

# dtruss line beginning with timestamp, e.g.
# 0.000123  open("/tmp/x",0x0,0x1B6) = 3 0
_RE = re.compile(
    r"^\s*(?P<time>\d+\.\d+)\s+"
    r"(?P<name>[A-Za-z0-9_]+)\((?P<args>.*)\)\s+=\s+(?P<ret>.+?)\s*$"
)


@dataclass
class _Sys:
    ts: float
    pid: int
    name: str
    result: str
    raw: str


class SyscallTracer(SyscallTracerBase):
    @classmethod
    def available(cls) -> bool:
        return shutil.which("dtruss") is not None and os.geteuid() == 0

    # ------------------------------------------------------------------
    def run(self) -> None:
        # 1. launch or attach
        if self.command:
            child = sp.Popen(self.command)
            target_pid = child.pid
        else:
            target_pid = self.pid

        # 2. attach with dtruss (-f follow forks, -t enables timestamps)
        cmd: Sequence[str] = [
            "dtruss",
            "-f",
            "-t", "open,read,write,close,stat64",
            "-p", str(target_pid),
        ]
        proc = sp.Popen(cmd, stderr=sp.PIPE, text=True, bufsize=1, close_fds=False)

        # 3. resume child (SIGCONT) – it’s still stopped in SIGSTOP
        os.kill(target_pid, signal.SIGCONT)

        # 4. stream syscalls
        assert proc.stderr is not None
        for line in proc.stderr:
            m = _RE.match(line)
            if not m:
                continue
            ev = _Sys(
                ts=float(m["time"]),
                pid=target_pid,
                name=m["name"],
                result=m["ret"],
                raw=line.rstrip(),
            )
            if self.queue:
                self.queue.put(Event(ev.ts, "syscall", asdict(ev)))

        proc.wait()
        if self.command:
            child.wait()