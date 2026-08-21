#!/usr/bin/env python3
"""
Regenerate docs/demo.svg — the README's demo image.

    python tools/make_demo.py

An SVG rather than a GIF: it stays sharp at any zoom, is ~40x smaller, and is
regenerated from the real tool, so it cannot drift out of date the way a
hand-recorded screencast does.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pyxtrace.run import diff, load  # noqa: E402
from pyxtrace.visual import render_diff  # noqa: E402

EXAMPLE = ROOT / "examples" / "orders.py"
OUT = ROOT / "docs" / "demo.svg"


def _record(tmp: Path, name: str, n_plus_one: bool) -> Path:
    out = tmp / name
    env = dict(os.environ, PYXTRACE_NPLUSONE="1" if n_plus_one else "0")
    # Argument list, no shell. EXAMPLE is a module constant and `out` is built
    # from a TemporaryDirectory this function created, so nothing here comes
    # from outside the process.
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
    subprocess.run(
        [sys.executable, "-m", "pyxtrace", str(EXAMPLE), "-o", str(out)],
        env={**env, "PYTHONPATH": str(ROOT / "src")},
        check=True,
        capture_output=True,
    )
    return out


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        before = _record(tmp, "before.pyxt", n_plus_one=False)
        after = _record(tmp, "after.pyxt", n_plus_one=True)
        findings = diff(load(before), load(after))

    console = Console(record=True, width=88)
    console.print("[bold green]$[/] pyxtrace diff before.pyxt after.pyxt")
    render_diff(findings, threshold=10.0, console=console)
    console.print("\n[dim]exit code 1 — the pull request fails[/]")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT), title="pyxtrace diff")
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT.relative_to(ROOT)} ({size_kb:.0f} KB)")

    if not findings:
        print("ERROR: the example produced no findings — demo would be empty")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
