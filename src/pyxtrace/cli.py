"""
pyxTrace command-line interface

    pyxtrace run app.py                    # profile → .pyxtrace/<commit sha>.pyxt
    pyxtrace app.py                        # same thing, `run` is implied
    pyxtrace diff main HEAD                # regression gate, exits 1 on failure
    pyxtrace diff main...HEAD              # git ranges work too
    pyxtrace diff a.pyxt b.pyxt            # explicit run files work too
    pyxtrace diff main                     # main vs. the checkout as it is now
                                           # (no runs recorded? the tests are run)
    pyxtrace run app.py -- --epochs 10     # args after `--` go to the script

Exit status: 0 pass, 1 regression found (or the traced script failed),
2 usage error or an unreadable run file.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pyxtrace
from pyxtrace import core
from pyxtrace.auto import AutoDiffError, auto_diff, is_changed, related_changes
from pyxtrace.run import (
    commit_sha,
    current_run,
    diff,
    interpreter_mismatch,
    load,
    merge_base,
    path_for,
)
from pyxtrace.visual import (
    render_auto_header,
    render_diff,
    render_interpreter_mismatch,
    render_uncovered,
)

_COMMANDS = ("run", "diff")


def _non_negative(kind):
    def parse(text: str):
        value = kind(text)
        # nan compares false against every growth and would pass any regression
        if not math.isfinite(value) or value < 0:
            raise argparse.ArgumentTypeError(f"must be a number, 0 or more, got {text}")
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
                     help="directory to profile (default: the git checkout the script is in, "
                          "else the script's own directory; virtualenvs inside it are "
                          "skipped); point this at an installed package to trace it")

    d = sub.add_parser("diff", help="compare two runs and exit 1 on a performance regression")
    d.add_argument("before", help="commit (main, HEAD, a sha), a range (main...HEAD, "
                                  "main..HEAD) or .pyxt path")
    d.add_argument("after", nargs="?",
                   help="commit or .pyxt path (default: the current checkout, "
                        "including uncommitted changes)")
    d.add_argument("-t", "--threshold", type=_non_negative(float), default=10.0,
                   help="fail if a function grows more than this percent (default 10)")
    d.add_argument("--min-ops", type=_non_negative(int), default=10,
                   help="ignore growth smaller than this many operations (default 10)")
    d.add_argument("--no-merge-base", dest="merge_base", action="store_false", default=None,
                   help="when running the tests, compare against the tip of BEFORE rather "
                        "than where AFTER diverged from it (same as BEFORE..AFTER)")
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
        # checked before the script runs, not after a long workload has finished
        if a.out is not None:
            if a.out.is_dir():
                p.error(f"-o {a.out}: is a directory")
            try:
                a.out.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                p.error(f"-o {a.out}: cannot create {a.out.parent} ({e.strerror or e})")
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


def _split_range(p: argparse.ArgumentParser, a: argparse.Namespace) -> None:
    """`A...B` compares from where A and B diverged, `A..B` from A itself, as in git.

    Either side may be left out and means HEAD. Git refuses `..` inside a ref
    name, so a ref is never misread as a range; a file path can be, and wins.
    """
    for sep, use_merge_base in (("...", True), ("..", False)):
        if a.after and sep in a.after and not Path(a.after).is_file():
            p.error(f"{a.after}: give a range as one argument, BEFORE{sep}AFTER")
        if a.after is None and sep in a.before and not Path(a.before).is_file():
            before, after = a.before.split(sep, 1)
            a.before, a.after = before or "HEAD", after or "HEAD"
            if a.merge_base is False and use_merge_base:
                p.error(f"--no-merge-base contradicts the range {before}{sep}{after}")
            a.merge_base = use_merge_base
            return


def _no_runs(a: argparse.Namespace) -> bool:
    """Are both sides commits with no run recorded for at least one of them?"""
    refs = [a.before] + ([a.after] if a.after else [])
    if any(Path(r).is_file() or commit_sha(r) is None for r in refs):
        return False
    try:
        path_for(a.before)
        after = path_for(a.after) if a.after else current_run()
    except FileNotFoundError:
        return True
    return not after.is_file()


def _auto_diff(p: argparse.ArgumentParser, a: argparse.Namespace) -> NoReturn:
    """No runs recorded: run the test suite on both sides and compare that."""
    try:
        # a pull request is compared from where it branched off, by default
        r = auto_diff(a.before, a.after, use_merge_base=a.merge_base is not False)
    except AutoDiffError as e:
        p.exit(2, f"pyxtrace: error: {e}\n")
    render_auto_header(r.before_label, r.after_label, r.tests_compared, r.tests_left_out)
    findings = diff(r.before, r.after, threshold=a.threshold, min_ops=a.min_ops)
    notes = {}
    for f in findings:
        if is_changed(f["name"], r.changed):
            notes[f["name"]] = "← changed in this diff"
        else:
            related = related_changes(f["name"], r.after, r.changed)
            if related:
                notes[f["name"]] = "← on the call path of changed: " + ", ".join(related)
    render_diff(findings, threshold=a.threshold, notes=notes)
    render_uncovered(r.uncovered)
    sys.exit(1 if findings else 0)


def _diff(p: argparse.ArgumentParser, a: argparse.Namespace) -> NoReturn:
    _split_range(p, a)
    if _no_runs(a):
        _auto_diff(p, a)
    head = a.after or "HEAD"
    if a.merge_base and commit_sha(a.before) and commit_sha(head) \
            and not Path(a.before).is_file() and not Path(head).is_file():
        # recorded runs are filed by commit, so `A...B` looks up the run of the fork
        fork = merge_base(a.before, head)
        if fork is None:
            p.error(f"{a.before} and {head} share no history")
        a.before = fork
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
