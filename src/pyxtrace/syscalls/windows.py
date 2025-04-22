"""Windows backend – stub using psutil until ETW hook is added."""
from __future__ import annotations

import platform
from time import time, sleep
from .base import SyscallTracerBase
from ..core import Event


class SyscallTracer(SyscallTracerBase):
    @classmethod
    def available(cls) -> bool:
        return False          # flip to True once ETW implementation lands

    # -------------------------------------------------------------- #
    def run(self) -> None:
        # Poor‑man's placeholder: emit a single warning event then exit.
        if self.queue:
            self.queue.put(
                Event(time(), "warning",
                      {"msg": "Windows syscall tracing not yet implemented"})
            )
        sleep(0.1)