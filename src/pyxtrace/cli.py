"""
CLI entry‑points (uses Typer → autocompletion:  `pyxtrace --help`)
"""

from __future__ import annotations

import platform, typer
import importlib
import runpy
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich import print

from .core import TraceSession

app = typer.Typer(add_completion=True, help="pyxtrace – interactive Python tracer")

@app.command()
def trace(
    script: Path = typer.Argument(..., exists=True, help="Target .py file to run"),
    args: List[str] = typer.Argument(None, help="Arguments forwarded to script"),
    dashboard: bool = typer.Option(False, "--dash", help="Launch Dash GUI afterwards"),
    no_syscalls: bool = typer.Option(False, "--no-syscalls", help="Skip syscall tracing (macOS users can leave this off; "
                                     "pyxtrace auto-skips when dtruss is unavailable).")
):
    """Run SCRIPT under full tracing and open the Rich table summary."""
    trace_syscalls = (
        not no_syscalls
        and platform.system() != "Darwin"
    )
    sess = TraceSession(trace_syscalls=trace_syscalls)
    sess.trace(str(script), args, open_dashboard=dashboard)


@app.command()
def kernelspy():
    """(root) Trace all Python processes’ syscalls in real‑time."""
    import pyxtrace.kernelspy as ks

    ks.main()


def main():
    app()


if __name__ == "__main__":
    main()