"""
pyxtrace
========
Catch Python performance regressions with deterministic operation counts.

Quick start
-----------

>>> from pathlib import Path
>>> from pyxtrace import run_tracer
>>> run_tracer(Path("examples/orders.py"), out=Path("before.pyxt"))

Then compare two runs::

    pyxtrace diff before.pyxt after.pyxt
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pyxtrace")           # installed dist
except PackageNotFoundError:                    # editable / source checkout
    __version__ = "2.0.0"

from .core import Event, TraceSession, run_tracer
from .bytecode import BytecodeTracer, FilteredTracer, ProfileTracer
from .run import diff, load, save
from .visual import TraceVisualizer

__all__ = [
    "TraceSession",
    "run_tracer",
    "ProfileTracer",
    "FilteredTracer",
    "BytecodeTracer",
    "TraceVisualizer",
    "diff",
    "load",
    "save",
    "Event",
    "__version__",
]
