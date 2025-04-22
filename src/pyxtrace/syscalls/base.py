"""Abstract interface every concrete tracer must implement."""
from __future__ import annotations

import abc
from queue import Queue
from typing import Sequence


class SyscallTracerBase(abc.ABC):
    """
    Implementations must:
        • accept either `command` **or** `pid`
        • push Event objects into `queue`
        • expose   available() -> bool   class‑method
    """

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        pid: int | None = None,
        queue: "Queue" | None = None,
    ):
        if (command is None) == (pid is None):
            raise ValueError("Provide exactly one of command=… or pid=…")
        self.command, self.pid, self.queue = command, pid, queue

    # --------------------------------------------------------------------- #
    @classmethod
    @abc.abstractmethod
    def available(cls) -> bool: ...

    @abc.abstractmethod
    def run(self) -> None: ...