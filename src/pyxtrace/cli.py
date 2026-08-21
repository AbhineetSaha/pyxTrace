"""
pyxTrace command-line interface

    pyxtrace run app.py -o before.pyxt     # profile → .pyxt run file
    pyxtrace app.py -o before.pyxt         # same thing, `run` is implied
    pyxtrace diff before.pyxt after.pyxt   # regression gate, exits 1 on failure
    pyxtrace run app.py --events           # raw JSONL event stream instead
    pyxtrace run app.py -- --epochs 10     # args after `--` go to the script
"""
from __future__ import annotations

import enum
import sys
from pathlib import Path

import typer

from pyxtrace import core

app = typer.Typer(
    add_completion=False,
    help="pyxTrace – catch Python performance regressions with deterministic counts",
    no_args_is_help=True,
)


class TraceMode(str, enum.Enum):
    full = "full"      # trace everything
    perf = "perf"      # skip std-lib / built-ins
    demo = "demo"      # call + return events only


# -------------------------------------------------------------------- #
@app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run_cmd(
    ctx: typer.Context,
    script: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Run file to write (default ./pyxtrace-<ts>.pyxt)"
    ),
    root: Path | None = typer.Option(
        None,
        "--root",
        help="Directory to profile (default: the script's own directory). "
        "Point this at an installed or out-of-tree package to trace it.",
    ),
    events: bool = typer.Option(
        False, "--events", help="Write the raw JSONL event stream instead of a run file"
    ),
    mode: TraceMode = typer.Option(
        TraceMode.full, "--mode", "-m", help="Event-stream depth: full | perf | demo"
    ),
    log: Path | None = typer.Option(
        None, "--log", help="JSONL output path (implies --events)"
    ),
    capture_returns: bool = typer.Option(
        False,
        "--capture-returns",
        help="Record every return value. These may contain secrets — off by default",
    ),
    memory: bool = typer.Option(
        False, "--memory", help="Sample heap usage via tracemalloc (slower)"
    ),
) -> None:
    """Profile SCRIPT and write a .pyxt run file."""
    # forward anything after `--` to the traced program
    idx = sys.argv.index("--") + 1 if "--" in sys.argv else len(sys.argv)
    sys.argv = [str(script)] + sys.argv[idx:]

    core.TraceSession(
        script_path=script,
        log_path=log,
        out=out,
        root=root,
        mode=mode.value,
        events=events or log is not None,
        capture_returns=capture_returns,
        memory=memory,
    ).run()


# -------------------------------------------------------------------- #
@app.command("diff")
def diff_cmd(
    before: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    after: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    threshold: float = typer.Option(
        10.0, "--threshold", "-t", help="Fail if a function grows more than this percent"
    ),
    min_ops: int = typer.Option(
        10, "--min-ops", help="Ignore growth smaller than this many operations"
    ),
) -> None:
    """
    Compare two .pyxt runs and fail (exit 1) on a performance regression.

    Compares deterministic operation counts, not wall-clock time, so the same
    code always produces the same verdict.
    """
    from pyxtrace.run import diff as run_diff, load as load_run
    from pyxtrace.visual import render_diff

    findings = run_diff(
        load_run(before), load_run(after), threshold=threshold, min_ops=min_ops
    )
    render_diff(findings, threshold=threshold)
    raise typer.Exit(1 if findings else 0)


# -------------------------------------------------------------------- #
def main() -> None:  # noqa: D401 – imperative ("Run …")
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
