"""Linux backend – uses strace."""
from __future__ import annotations

import shutil
import subprocess as sp
import time
import re
from dataclasses import asdict, dataclass
from .base import SyscallTracerBase
from ..core import Event

_SYSCALL = re.compile(
    r"^(?P<time>\d+\.\d+)\s+(?:(?P<pid>\d+)\s+)?"
    r"(?P<name>[a-zA-Z0-9_]+)\((?P<args>.*)\)\s+=\s+(?P<ret>.+?)\s*$"
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
        return shutil.which("strace") is not None

    # -------------------------------------------------------------- #
    def run(self) -> None:
        cmd = ["strace", "-ff", "-ttt", "-s", "0", "-e", "trace=all"]
        cmd += self.command if self.command else ["-p", str(self.pid)]

        proc = sp.Popen(cmd, stderr=sp.PIPE, text=True, bufsize=1)
        assert proc.stderr
        for line in proc.stderr:
            m = _SYSCALL.match(line)
            if not m:
                continue
            ev = _Sys(
                ts=float(m["time"]),
                pid=int(m["pid"] or proc.pid),
                name=m["name"],
                result=m["ret"],
                raw=line.rstrip(),
            )
            if self.queue:
                self.queue.put(Event(ev.ts, "syscall", asdict(ev)))
        proc.wait()