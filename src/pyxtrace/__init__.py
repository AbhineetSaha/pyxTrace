"""
pyxtrace
========
Catch Python performance regressions with deterministic operation counts.

Quick start
-----------

>>> from pathlib import Path
>>> from pyxtrace import TraceSession
>>> TraceSession(Path("examples/orders.py")).run()   # → .pyxtrace/<commit sha>.pyxt

Then compare two commits::

    pyxtrace diff main HEAD
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pyxtrace")           # installed dist
except PackageNotFoundError:                    # source checkout on sys.path
    __version__ = "3.0.0"

from .bytecode import ProfileTracer
from .core import TraceSession
from .run import diff, load, save

__all__ = ["ProfileTracer", "TraceSession", "__version__", "diff", "load", "save"]
