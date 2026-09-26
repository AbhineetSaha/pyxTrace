"""
pyxTrace command-line interface

    pyxtrace run app.py                    # profile → .pyxtrace/<commit sha>.pyxt
    pyxtrace app.py                        # same thing, `run` is implied
    pyxtrace diff main HEAD                # regression gate, exits 1 on failure
    pyxtrace diff a.pyxt b.pyxt            # explicit run files work too
    pyxtrace diff main                     # main vs. the checkout as it is now
    pyxtrace run app.py -- --epochs 10     # args after `--` go to the script

Exit status: 0 pass, 1 regression found (or the traced script failed),
2 usage error or an unreadable run file.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pyxtrace
from pyxtrace import core
from pyxtrace.run import (
    current_run,
    diff,
    interpreter_mismatch,
    load,
    path_for,
)
from pyxtrace.visual import render_diff, render_interpreter_mismatch

_COMMANDS = ("run", "diff")


def _non_negative(kind):
    def parse(text: str):
        value = kind(text)
        if value < 0:
            raise argparse.ArgumentTypeError(f"must be 0 or more, got {text}")
        return value
    parse.__name__ = kind.__name__  # argparse names the type in its errors
    return parse


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyxtrace",
        description="Catch Python performance regressions with deterministic counts",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {pyxtrace.__version__}")
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
    d.add_argument("after", nargs="?",
                   help="commit or .pyxt path (default: the current checkout, "
                        "including uncommitted changes)")
    d.add_argument("-t", "--threshold", type=_non_negative(float), default=10.0,
                   help="fail if a function grows more than this percent (default 10)")
    d.add_argument("--min-ops", type=_non_negative(int), default=10,
                   help="ignore growth smaller than this many operations (default 10)")
    return p


def _safe_output() -> None:
    """Never crash on a character the terminal cannot encode.

    A Windows runner writing to a cp1252 pipe cannot encode ✓ or ⚠; that must
    cost a replaced glyph, not the run.
    """
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def _relaunch_with_fixed_hash_seed(argv: list[str]) -> NoReturn:
    """Re-run this command in a fresh interpreter with PYTHONHASHSEED=0.

    The seed is read once at interpreter start, so it cannot be fixed from in
    here. The child runs the same pyxtrace (its location is passed along), and
    the current directory is dropped from its sys.path, which `python app.py`
    would not have put there either.
    """
    pkg_parent = str(Path(pyxtrace.__file__).resolve().parent.parent)
    boot = (
        "import sys; del sys.path[0]; "
        f"sys.path.append({pkg_parent!r}) if {pkg_parent!r} not in sys.path else None; "
        "from pyxtrace.cli import main; main()"
    )
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    try:
        r = subprocess.run([sys.executable, "-c", boot, *argv], env=env)
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(r.returncode)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    _safe_output()
    p = _parser()
    # `pyxtrace app.py …` and `pyxtrace -o x.pyxt app.py …` → `pyxtrace run …`
    if argv and argv[0] not in _COMMANDS and argv[0] not in ("-h", "--help", "--version"):
        argv.insert(0, "run")
    # anything after `--` belongs to the traced script
    script_args: list[str] = []
    if argv and argv[0] == "run" and "--" in argv:
        i = argv.index("--")
        argv, script_args = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)

    if a.cmd == "run":
        if not a.script.is_file():
            p.error(f"{a.script}: no such script")
        if a.root is not None and not a.root.is_dir():
            p.error(f"--root {a.root}: no such directory")
        if not core.hash_seed_fixed() and sys.executable:
            _relaunch_with_fixed_hash_seed(_original_args(a, script_args))
        sys.argv = [str(a.script)] + script_args
        core.TraceSession(script_path=a.script, out=a.out, root=a.root,
                          wait_at_exit=True).run()
        return

    _diff(p, a)


def _original_args(a: argparse.Namespace, script_args: list[str]) -> list[str]:
    """Rebuild a `run` command line from parsed arguments, for the relaunch."""
    args = ["run", str(a.script)]
    if a.out is not None:
        args += ["-o", str(a.out)]
    if a.root is not None:
        args += ["--root", str(a.root)]
    return args + (["--", *script_args] if script_args else [])


def _diff(p: argparse.ArgumentParser, a: argparse.Namespace) -> NoReturn:
    try:
        before = path_for(a.before)
        after = path_for(a.after) if a.after else current_run()
        if not after.is_file():
            raise FileNotFoundError(
                f"no run recorded for the current checkout ({after}): "
                f"`pyxtrace run SCRIPT` first"
            )
    except FileNotFoundError as e:
        p.error(str(e))
    try:
        b_run, a_run = load(before), load(after)
    except (OSError, ValueError) as e:
        p.exit(2, f"pyxtrace: error: {e}\n")

    mismatch = interpreter_mismatch(b_run, a_run)
    if mismatch:
        render_interpreter_mismatch(mismatch)
    findings = diff(b_run, a_run, threshold=a.threshold, min_ops=a.min_ops)
    render_diff(findings, threshold=a.threshold)
    sys.exit(1 if findings else 0)
