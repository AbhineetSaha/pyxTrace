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
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

FORMAT_VERSION = 1


def _name(filename: str, root: Path) -> str:
    """Stable, machine-independent function-file label."""
    try:
        return os.path.relpath(filename, root).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return Path(filename).name


def to_dict(
    stats: Dict[Tuple[str, str], Dict[str, Any]],
    *,
    root: Path,
    script: str,
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
        "script": script,
        "functions": dict(sorted(functions.items())),
    }


def save(run: Dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so an unchanged run produces a byte-identical file
    path.write_text(json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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


def top(run: Dict[str, Any], n: int = 10, by: str = "lines") -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Hottest functions first."""
    items = run["functions"].items()
    return sorted(items, key=lambda kv: kv[1].get(by, 0), reverse=True)[:n]


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


def _n_plus_one(
    fn_before: Dict[str, Any],
    fn_after: Dict[str, Any],
    callees: list[Dict[str, Any]],
    *,
    min_parent_calls: int = 10,
) -> Dict[str, Any] | None:
    """
    Detect per-item fan-out: a function that runs many times and now makes at
    least one extra call each, where it previously made fewer.

    That is the N+1 shape — work that scales with the number of items instead
    of being done once for the batch.  Requiring the *parent* to run many times
    is what separates it from an ordinary loop, where one caller iterates.

    Pure arithmetic over deterministic counts: no model, no confidence score.
    """
    calls_before = fn_before.get("calls", 0)
    calls_after = fn_after.get("calls", 0)
    if calls_after < min_parent_calls:
        return None  # an ordinary loop in a function that runs once or twice
    if calls_after > calls_before * 1.1:
        # The function is running more often than it used to, so its extra
        # calls are explained by its own caller. The N+1 was introduced
        # further up; flagging here would blame the symptom.
        return None

    for row in callees:
        grew = row["after"] - row["before"]
        if grew <= 0 or grew < calls_after:
            continue  # not at least one *additional* call per invocation
        return {
            "callee": row["name"],
            "parent_calls": calls_after,
            "total_before": row["before"],
            "total_after": row["after"],
            "per_call_after": row["after"] / calls_after,
            "per_call_before": row["before"] / calls_before if calls_before else 0.0,
        }
    return None


def diff(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    threshold: float = 10.0,
    min_ops: int = 100,
) -> list[Dict[str, Any]]:
    """
    Compare two runs on ``lines`` — the deterministic operation count.

    A function is reported when it grew by more than *threshold* percent **and**
    by at least *min_ops* operations, so a 2→3 line change does not raise an
    alarm just because it is +50%.  Results are ranked by absolute growth.
    """
    b_fns, a_fns = before["functions"], after["functions"]
    findings = []

    for name in sorted(set(b_fns) | set(a_fns)):
        b = b_fns.get(name, {"calls": 0, "lines": 0, "callees": {}})
        a = a_fns.get(name, {"calls": 0, "lines": 0, "callees": {}})
        delta = a.get("lines", 0) - b.get("lines", 0)
        pct = _pct(b.get("lines", 0), a.get("lines", 0))
        callees = _callee_deltas(b, a)
        npo = _n_plus_one(b, a, callees)

        # Report on operation growth, or on a fan-out pattern even when the
        # function's own line count barely moved — the caller that introduced
        # an N+1 often does not grow itself, it just delegates the new work.
        grew = delta >= min_ops and (
            pct is None or pct == float("inf") or pct >= threshold
        )
        if not grew and npo is None:
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
                "n_plus_one": npo,
            }
        )

    # N+1 findings first — they name the cause, not just the symptom
    return sorted(
        findings, key=lambda f: (f["n_plus_one"] is not None, f["delta"]), reverse=True
    )
