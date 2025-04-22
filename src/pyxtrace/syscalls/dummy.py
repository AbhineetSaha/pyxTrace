"""Graceful no‑op."""
from __future__ import annotations

from time import sleep, time
from .base import SyscallTracerBase
from ..core import Event


class SyscallTracer(SyscallTracerBase):
    @classmethod
    def available(cls) -> bool:
        return True

    def run(self) -> None:
        # emit nothing, just let the session continue
        sleep(0.1)