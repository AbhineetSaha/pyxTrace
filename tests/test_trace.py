import json
from pathlib import Path

import pyxtrace.core as core
from pyxtrace import run as runfile

# resolve relative to this file so pytest works from any cwd
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "fibonacci.py"


def test_profile_run_counts_are_exact(tmp_path: Path) -> None:
    """fib(5) makes exactly 15 calls — the counts are arithmetic, not samples."""
    out = tmp_path / "run.pyxt"
    core.run_tracer(EXAMPLE, out=out)

    data = runfile.load(out)
    fib = data["functions"]["fibonacci.py::fib"]
    assert fib["calls"] == 15
    assert fib["lines"] > 0
    # fib recurses into itself 14 times; __main__ makes the 15th call
    assert fib["callees"]["fibonacci.py::fib"] == 14
    assert data["functions"]["fibonacci.py::<module>"]["callees"] == {
        "fibonacci.py::fib": 1
    }


def test_profile_run_is_byte_stable(tmp_path: Path) -> None:
    """Unchanged code must produce an identical run file, timing aside."""

    def counts(path: Path) -> str:
        data = runfile.load(path)
        for fn in data["functions"].values():
            fn.pop("own_time")
        return json.dumps(data, sort_keys=True)

    a, b = tmp_path / "a.pyxt", tmp_path / "b.pyxt"
    core.run_tracer(EXAMPLE, out=a)
    core.run_tracer(EXAMPLE, out=b)
    assert counts(a) == counts(b)


def test_event_stream_still_writes_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "trace.jsonl"
    core.run_tracer(EXAMPLE, mode="demo", log_path=log_path)

    assert log_path.exists()
    rows = log_path.read_text().splitlines()
    assert rows, "log should not be empty"
    assert "kind" in json.loads(rows[0])


ORDERS = Path(__file__).resolve().parent.parent / "examples" / "orders.py"


def _orders_run(tmp_path: Path, name: str, n_plus_one: bool) -> dict:
    import os

    out = tmp_path / name
    prev = os.environ.get("PYXTRACE_NPLUSONE")
    os.environ["PYXTRACE_NPLUSONE"] = "1" if n_plus_one else "0"
    try:
        core.run_tracer(ORDERS, out=out)
    finally:
        if prev is None:
            os.environ.pop("PYXTRACE_NPLUSONE", None)
        else:
            os.environ["PYXTRACE_NPLUSONE"] = prev
    return runfile.load(out)


def test_diff_is_clean_against_itself(tmp_path: Path) -> None:
    baseline = _orders_run(tmp_path, "a.pyxt", n_plus_one=False)
    assert runfile.diff(baseline, baseline) == []


def test_diff_catches_the_n_plus_one(tmp_path: Path) -> None:
    before = _orders_run(tmp_path, "before.pyxt", n_plus_one=False)
    after = _orders_run(tmp_path, "after.pyxt", n_plus_one=True)

    findings = runfile.diff(before, after)
    assert findings, "the N+1 regression should be reported"

    # the introduction point ranks first, not the downstream query function
    top_finding = findings[0]
    assert top_finding["name"] == "orders.py::process_order"

    npo = top_finding["n_plus_one"]
    assert npo is not None
    assert npo["callee"] == "orders.py::fetch_customer"
    assert npo["parent_calls"] == 120
    assert npo["total_after"] == 120
    assert npo["total_before"] == 0


def test_diff_does_not_blame_downstream_callees(tmp_path: Path) -> None:
    """fetch_customer runs more because its caller does — that is not its N+1."""
    before = _orders_run(tmp_path, "before.pyxt", n_plus_one=False)
    after = _orders_run(tmp_path, "after.pyxt", n_plus_one=True)

    by_name = {f["name"]: f for f in runfile.diff(before, after)}
    assert by_name["orders.py::fetch_customer"]["n_plus_one"] is None


def test_diff_ignores_small_changes() -> None:
    """A 2→3 line change is +50% but means nothing; min_ops filters it."""
    before = {"pyxtrace": 1, "script": "x.py", "functions": {"x.py::f": {"calls": 1, "lines": 2, "callees": {}}}}
    after = {"pyxtrace": 1, "script": "x.py", "functions": {"x.py::f": {"calls": 1, "lines": 3, "callees": {}}}}
    assert runfile.diff(before, after) == []


def test_return_values_are_not_captured_by_default(tmp_path: Path) -> None:
    """Return values can carry secrets — they must be opt-in."""
    log_path = tmp_path / "trace.jsonl"
    core.run_tracer(EXAMPLE, mode="demo", log_path=log_path)

    assert "return_value" not in log_path.read_text()

