"""
python -m pyxtrace  → behaves like the `pyxtrace` console-script stub.
Smart dispatch:
    • pyxtrace  myscript.py  [--mode …] [--dash]   ← direct trace
    • pyxtrace  replay …                         ← Typer sub-command
    • pyxtrace  --help                           ← Typer help
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import List


def _looks_like_script(arg: str) -> bool:
    """Heuristic: path exists *or* ends with .py."""
    return arg.endswith(".py") or Path(arg).exists()


def main(argv: List[str] | None = None) -> None:
    argv = argv or sys.argv[1:]

    if argv and not argv[0].startswith("-") and _looks_like_script(argv[0]):
        # ── Direct trace path ────────────────────────────────────────
        script = Path(argv[0]).resolve()
        from pyxtrace import core, cli  # cli imported for options parsing

        # pull out our own options (dash / mode) – leave script's args intact
        import argparse

        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--mode", "-m", choices=["full", "perf", "demo"],
                       default="full")
        p.add_argument("--dash", action="store_true")
        p.add_argument("--log", "-o")

        opts, remaining = p.parse_known_args(argv[1:])
        sys.argv = [str(script)] + remaining        # script sees its args

        core.TraceSession(
            script_path=script,
            log_path=Path(opts.log).resolve() if opts.log else None,
            mode=opts.mode,
            dash=opts.dash,
        ).run()
    else:
        # ── Fall back to Typer app for sub-commands / help ───────────
        cli = importlib.import_module("pyxtrace.cli")
        cli.main()      # runs Typer app


if __name__ == "__main__":      # pragma: no cover
    main()
