"""
kernelspy.py – rudimentary ‘top’ for all Python PIDs on the system.
Requires `sudo` and Linux.

Run:

    sudo python -m pyxtrace.kernelspy
"""

import os
import signal
import subprocess as sp
import sys
from pathlib import Path
from typing import Iterable, List

import psutil
from rich.console import Console
from rich.table import Table


def _iter_python_pids() -> Iterable[int]:
    for p in psutil.process_iter(["name", "cmdline"]):
        if p.info["name"] == "python" or (
            p.info["cmdline"] and "python" in p.info["cmdline"][0]
        ):
            yield p.pid


def main() -> None:
    console = Console()
    while True:
        tbl = Table(title="kernelspy – live syscalls (last sec)")
        tbl.add_column("PID")
        tbl.add_column("cmd")
        tbl.add_column("#syscalls")
        for pid in _iter_python_pids():
            try:
                out = sp.check_output(
                    ["strace", "-c", "-p", str(pid), "-qq", "-f", "-e", "trace=all", "-o", "/dev/null", "-tt"],
                    timeout=1.0,
                    stderr=sp.DEVNULL,
                    text=True,
                )
            except (sp.TimeoutExpired, sp.CalledProcessError):
                continue
            n = out.count("\n")
            p = psutil.Process(pid)
            tbl.add_row(str(pid), " ".join(p.cmdline())[:40], str(n))
        console.clear()
        console.print(tbl)
        try:
            console.input("[grey](Press Ctrl‑C to quit)[/]")
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("kernelspy must be run as root (needs ptrace).")
    main()