"""
pyxtrace
========
Interactive visual tracer for Python system‑calls, byte‑code and memory.

Quick start
-----------

>>> from pyxtrace import TraceSession
>>> TraceSession().trace("examples/fibonacci.py")

(The CLI wrapper does the same thing:  `pyxtrace trace examples/fibonacci.py`)
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

# --------------------------------------------------------------------------- #
# Package version                                                             #
# --------------------------------------------------------------------------- #
try:
    __version__ = version("pyxtrace")           # installed dist
except PackageNotFoundError:                    # editable / source checkout
    __version__ = "0.0.0+dev"

# --------------------------------------------------------------------------- #
#  ▄▄▄  We must import `core` FIRST so the Event dataclass already exists     #
# ▀█ █▀ Otherwise bytecode/memory modules that need Event will hit a loop     #
# --------------------------------------------------------------------------- #
from .core import TraceSession, Event  # noqa: E402  (import after metadata)

# The following imports *may* depend on core.Event, so we do them afterwards
from .syscalls import SyscallTracer          # platform‑specific subclass
from .bytecode import BytecodeTracer
from .memory import MemoryTracer
from .visual import TraceVisualizer

__all__ = [
    "TraceSession",
    "SyscallTracer",
    "BytecodeTracer",
    "MemoryTracer",
    "TraceVisualizer",
    "Event",              # useful for type‑checking user extensions
    "__version__",
]