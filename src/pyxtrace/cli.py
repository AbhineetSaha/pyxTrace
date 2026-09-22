"""
pyxTrace command-line interface

    pyxtrace run app.py                    # profile → .pyxtrace/<commit sha>.pyxt
    pyxtrace app.py                        # same thing, `run` is implied
    pyxtrace diff main HEAD                # regression gate, exits 1 on failure
    pyxtrace diff a.pyxt b.pyxt            # explicit run files work too
    pyxtrace run app.py -- --epochs 10     # args after `--` go to the script
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyxtrace import core
from pyxtrace.run import diff, load, path_for
from pyxtrace.visual import render_diff


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyxtrace",
        description="Catch Python performance regressions with deterministic counts",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="profile SCRIPT and write a .pyxt run file")
    run.add_argument("script", type=Path)
    run.add_argument("-o", "--out", type=Path,
                     help="run file to write (default .pyxtrace/<commit sha>.pyxt)")
    run.add_argument("--root", type=Path,
                     help="directory to profile (default: the script's own directory); "
                          "point this at an installed or out-of-tree package to trace it")

    d = sub.add_parser("diff", help="compare two runs and exit 1 on a performance regression")
    d.add_argument("before", help="commit (main, HEAD, a sha) or .pyxt path")
    d.add_argument("after", help="commit (main, HEAD, a sha) or .pyxt path")
    d.add_argument("-t", "--threshold", type=float, default=10.0,
                   help="fail if a function grows more than this percent (default 10)")
    d.add_argument("--min-ops", type=int, default=10,
                   help="ignore growth smaller than this many operations (default 10)")
    return p


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = _parser()
    # `pyxtrace app.py …` → `pyxtrace run app.py …`
    if argv and argv[0] not in ("run", "diff") and not argv[0].startswith("-"):
        argv.insert(0, "run")
    # anything after `--` belongs to the traced script
    script_args: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, script_args = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)

    if a.cmd == "run":
        if not a.script.is_file():
            p.error(f"{a.script}: no such script")
        sys.argv = [str(a.script)] + script_args
        core.TraceSession(script_path=a.script.resolve(), out=a.out, root=a.root).run()
        return

    try:
        before, after = path_for(a.before), path_for(a.after)
    except FileNotFoundError as e:
        p.error(str(e))
    findings = diff(load(before), load(after), threshold=a.threshold, min_ops=a.min_ops)
    render_diff(findings, threshold=a.threshold)
    sys.exit(1 if findings else 0)
