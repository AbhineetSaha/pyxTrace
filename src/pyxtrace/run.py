"""
run.py – the ``.pyxt`` run artifact: one small, sorted, diffable summary of a
traced run.

Deliberately *not* an event log.  A run file is meant to be committed as a
baseline and compared against, so it has to stay small and byte-stable across
runs of unchanged code.  ``calls`` and ``lines`` satisfy that; ``own_time``
does not and is carried for display only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

FORMAT_VERSION = 1

# Where `pyxtrace run` files a run when no -o is given: one file per commit, so
# CI can compare "the base of this PR" against "HEAD" by name.
RUN_DIR = Path(".pyxtrace")

# How often a function must run before extra per-invocation calls mean anything.
# Below this it is an ordinary loop in a function that runs once or twice.
_MIN_REPEAT = 10


def _name(filename: str, root: Path) -> str:
    """Stable, machine-independent function-file label."""
    try:
        return os.path.relpath(filename, root).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return Path(filename).name


def commit_sha(rev: str = "HEAD") -> str | None:
    """Full sha for *rev*; None outside a git repo, or when *rev* is not a commit."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
            capture_output=True, text=True,
        )
    except OSError:  # no git on PATH
        return None
    return r.stdout.strip() or None


def worktree_dirty() -> bool:
    """Do tracked files differ from HEAD?  False outside a git repo.

    Untracked files are ignored: `.pyxtrace/` itself is usually one, and a new
    module only changes the run once something tracked imports it.
    """
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def run_name(sha: str | None, dirty: bool = False) -> str:
    """File name for a run of commit *sha*.

    Uncommitted changes get their own name, so experimenting locally never
    overwrites the clean run recorded for the commit underneath them.
    """
    return f"{sha or 'untracked'}{'-dirty' if dirty else ''}.pyxt"


def current_run() -> Path:
    """The run file for the checkout as it is right now, committed or not."""
    return RUN_DIR / run_name(commit_sha(), worktree_dirty())


def path_for(ref: str) -> Path:
    """Resolve a `diff` argument: an existing file, else the run recorded for a commit."""
    if Path(ref).exists():
        return Path(ref)
    sha = commit_sha(ref)
    if sha is None:
        raise FileNotFoundError(f"{ref}: not a run file or a git commit")
    path = RUN_DIR / run_name(sha)
    if not path.exists():
        dirty = RUN_DIR / run_name(sha, dirty=True)
        hint = (
            f" (there is a run of uncommitted changes on top of it: {dirty})"
            if dirty.exists() else ""
        )
        raise FileNotFoundError(
            f"no run recorded for {ref}: check it out and `pyxtrace run SCRIPT`, "
            f"or pass a .pyxt path{hint}"
        )
    return path


def interpreter() -> str:
    """e.g. ``cpython-3.12``.  Line events shift between minor versions (3.12
    inlined comprehensions), so counts are only comparable within one."""
    v = sys.version_info
    return f"{sys.implementation.name}-{v.major}.{v.minor}"


def interpreter_mismatch(before: Dict[str, Any], after: Dict[str, Any]) -> str | None:
    """Describe a Python version difference between two runs, if there is one.

    Runs written before the field existed carry no version and are trusted.
    """
    b, a = before.get("python"), after.get("python")
    if b and a and b != a:
        return f"{b} vs {a}"
    return None


def to_dict(
    stats: Dict[Tuple[str, str], Dict[str, Any]],
    *,
    root: Path,
    script: str,
    commit: str | None = None,
) -> Dict[str, Any]:
    """Convert a ProfileTracer's raw stats into the serialisable run form."""
    functions: Dict[str, Any] = {}
    for (filename, func), s in stats.items():
        key = f"{_name(filename, root)}::{func}"
        functions[key] = {
            "calls": s["calls"],
            "lines": s["lines"],
            "own_time": round(s["own_time"], 6),
            "callees": {
                f"{_name(cf, root)}::{cn}": n
                for (cf, cn), n in sorted(s["callees"].items())
            },
        }
    return {
        "pyxtrace": FORMAT_VERSION,
        "python": interpreter(),
        "script": script,
        "commit": commit,
        "functions": dict(sorted(functions.items())),
    }


def save(run: Dict[str, Any], path: str | Path) -> Path:
    """Write the run artifact, without the timing that would churn a baseline.

    ``own_time`` moves every run, so writing it would produce a git diff on
    every function of a file whose whole purpose is to be committed and
    compared.  It stays in the in-memory dict for the terminal summary.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stable = dict(run)
    stable["functions"] = {
        name: {k: v for k, v in fn.items() if k != "own_time"}
        for name, fn in run["functions"].items()
    }
    # sort_keys so an unchanged run produces a byte-identical file
    path.write_text(json.dumps(stable, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load(path: str | Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("pyxtrace")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"{path}: unsupported run format {version!r} "
            f"(this pyxtrace reads version {FORMAT_VERSION})"
        )
    return data


def top(run: Dict[str, Any], n: int = 10) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Hottest functions first, by lines executed."""
    items = run["functions"].items()
    return sorted(items, key=lambda kv: kv[1].get("lines", 0), reverse=True)[:n]


# ────────────────────────────── diff ──────────────────────────────── #
def _pct(before: int, after: int) -> float | None:
    """Percentage change; None when there is no baseline to divide by."""
    if before == 0:
        return None if after == 0 else float("inf")
    return (after - before) / before * 100.0


def _callee_deltas(before: Dict[str, Any], after: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Per-callee call-count change, biggest absolute growth first."""
    b_callees = before.get("callees", {})
    a_callees = after.get("callees", {})
    rows = []
    for name in sorted(set(b_callees) | set(a_callees)):
        b, a = b_callees.get(name, 0), a_callees.get(name, 0)
        if a != b:
            rows.append({"name": name, "before": b, "after": a, "pct": _pct(b, a)})
    return sorted(rows, key=lambda r: r["after"] - r["before"], reverse=True)


def diff(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    threshold: float = 10.0,
    min_ops: int = 10,
) -> list[Dict[str, Any]]:
    """
    Compare two runs on ``lines`` — the deterministic operation count.

    A function is reported when it grew by more than *threshold* percent **and**
    by at least *min_ops* operations, so a 2→3 line change does not raise an
    alarm just because it is +50%.  Results are ranked by absolute growth.

    ``min_ops`` is a significance floor, not a noise floor: counts are exact, so
    an unchanged run reports nothing at any setting.  10 is the smallest value
    measured to catch a real regression (sqlglot 105cbb67, +19 operations in
    ``_parse_join``) without burying it in incidental one-line changes.
    """
    b_fns, a_fns = before["functions"], after["functions"]
    findings = []

    for name in sorted(set(b_fns) | set(a_fns)):
        b = b_fns.get(name, {"calls": 0, "lines": 0, "callees": {}})
        a = a_fns.get(name, {"calls": 0, "lines": 0, "callees": {}})
        delta = a.get("lines", 0) - b.get("lines", 0)
        pct = _pct(b.get("lines", 0), a.get("lines", 0))
        callees = _callee_deltas(b, a)

        grew = delta >= min_ops and (
            pct is None or pct == float("inf") or pct >= threshold
        )
        # A function that introduces extra work often does not grow itself, it
        # just calls something else more often. Without this the diff blames the
        # callee that got busier instead of the caller that made it happen.
        #
        # Deliberately narrow: the function has to run often, must not simply be
        # running more because its own caller does, and some callee has to gain
        # at least one extra call for every invocation. Reporting any callee
        # growth instead cascades up the call chain (measured: 30+ findings per
        # commit on sqlglot, against 1-2 here).
        calls_before, calls_after = b.get("calls", 0), a.get("calls", 0)
        extra_calls = sum(r["after"] - r["before"] for r in callees)
        delegated = (
            calls_after >= _MIN_REPEAT
            and calls_after <= calls_before * 1.1
            and any(r["after"] - r["before"] >= calls_after for r in callees)
        )
        if not (grew or delegated):
            continue

        findings.append(
            {
                "name": name,
                "lines_before": b.get("lines", 0),
                "lines_after": a.get("lines", 0),
                "delta": delta,
                "pct": pct,
                "calls_before": b.get("calls", 0),
                "calls_after": a.get("calls", 0),
                "is_new": name not in b_fns,
                "callees": callees,
                "extra_calls": extra_calls,
                "delegated": delegated,
            }
        )

    # callers that introduced work rank above the callees that absorbed it
    return sorted(findings, key=lambda f: (f["delegated"], f["delta"]), reverse=True)
