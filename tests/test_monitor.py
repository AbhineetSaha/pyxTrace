"""The sys.monitoring tracer counts what ProfileTracer counts, exactly, at less cost."""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest

from pyxtrace import monitor
from pyxtrace.bytecode import ProfileTracer
from pyxtrace.run import to_dict

pytestmark = pytest.mark.skipif(not monitor.available(), reason="needs sys.monitoring and the GIL")

ROOT = Path(__file__).resolve().parent.parent
ORDERS = ROOT / "examples" / "orders.py"


def _orders(nplusone: bool):
    """examples/orders.py's request handler, batched or one query per order."""
    spec = importlib.util.spec_from_file_location("orders_under_test", ORDERS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return lambda: mod.handle_request(mod.Cursor(), not nplusone)


def _counts(stats, root: Path) -> dict:
    fns = to_dict(stats, root=root, script="x")["functions"]
    return {name: {k: v for k, v in fn.items() if k != "own_time"} for name, fn in fns.items()}


def _with_monitoring(work, root: Path = ROOT) -> dict:
    t = monitor.MonitorTracer(root_path=root)
    t.start()
    try:
        work()
    finally:
        t.stop()
    return _counts(t.stats, root)


def _with_settrace(work, root: Path = ROOT) -> dict:
    t = ProfileTracer(root_path=root)
    sys.settrace(t)
    try:
        work()
    finally:
        sys.settrace(None)
    return _counts(t.stats, root)


@pytest.mark.parametrize("nplusone", [False, True])
def test_it_counts_what_settrace_counts(nplusone: bool) -> None:
    """Calls, lines and callers agree where no generator is resumed (orders.py has none)."""
    work = _orders(nplusone)
    assert _with_monitoring(work) == _with_settrace(work)


def test_repeated_runs_are_identical() -> None:
    work = _orders(True)
    first = _with_monitoring(work)
    assert first, "nothing was counted"
    for _ in range(20):
        assert _with_monitoring(work) == first


def test_counts_from_many_threads_add_up_exactly() -> None:
    """Callbacks run in every thread; a shared `+= 1` would lose updates."""
    request = _orders(True)

    def work():
        ts = [threading.Thread(target=request) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

    one = _with_monitoring(request)
    many = _with_monitoring(work)
    name = "examples/orders.py::fetch_customer"
    assert many[name]["calls"] == 8 * one[name]["calls"]
    assert many[name]["lines"] == 8 * one[name]["lines"]


def test_code_outside_the_root_is_not_counted() -> None:
    import json

    counts = _with_monitoring(lambda: json.dumps({"a": [1, 2, 3]}))
    assert not any("json" in name for name in counts)


SHAPES = '''\
import functools


def loops(n):
    total = 0
    i = 0
    while i < n:
        i += 1
        if i % 3 == 0:
            continue
        total += i
    for j in range(n): total += j
    squares = [x * x for x in range(n) if x % 2]
    table = {k: [v for v in range(k)] for k in range(n)}
    return total + sum(squares) + len(table)


def recurse(n):
    return n if n < 2 else recurse(n - 1) + recurse(n - 2)


def fails(n):
    try:
        if n:
            raise ValueError(n)
    except ValueError:
        return -1
    finally:
        n += 1
    return n


def deco(f):
    @functools.wraps(f)
    def wrapper(*a):
        return f(*a)
    return wrapper


@deco
def decorated(n):
    return (lambda y: y * 2)(n)


class Box:
    def __init__(self, n):
        self.items = [Box.make(i) for i in range(n)]

    @staticmethod
    def make(i):
        return i


def run():
    for n in (5, 12):
        loops(n); recurse(n); fails(n); fails(0); decorated(n); Box(n)
'''


def test_loops_exceptions_recursion_and_decorators_count_alike(tmp_path: Path) -> None:
    src = tmp_path / "shapes.py"
    src.write_text(SHAPES)
    spec = importlib.util.spec_from_file_location("shapes", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    counted = _with_monitoring(mod.run, tmp_path)
    assert counted["shapes.py::loops"]["lines"] > 0
    assert counted == _with_settrace(mod.run, tmp_path)


def test_a_held_profiler_slot_is_a_clear_error_not_a_silent_fallback(tmp_path: Path) -> None:
    import os
    import subprocess

    (tmp_path / "conftest.py").write_text(
        "import sys\nsys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, 'someone else')\n")
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")
    env = {**os.environ, "PYXTRACE_PYTEST_OUT": str(tmp_path / "out.json"),
           "PYXTRACE_PYTEST_ROOT": str(tmp_path), "PYXTRACE_TRACER": "monitoring"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "pyxtrace.pytest_plugin",
                        "-p", "no:cacheprovider", str(tmp_path)],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "PYXTRACE_TRACER=settrace" in r.stdout + r.stderr


def test_a_one_line_loop_outside_the_root_is_not_counted(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "lib" / "dep.py").write_text("def dep(n):\n    return [i for i in range(n)]\n")
    (tmp_path / "app" / "main.py").write_text(
        f"import sys\nsys.path.insert(0, {str(tmp_path / 'lib')!r})\nimport dep\n\n"
        "def main():\n    for _ in range(3): dep.dep(50)\n")
    spec = importlib.util.spec_from_file_location("app_main", tmp_path / "app" / "main.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    counted = _with_monitoring(mod.main, tmp_path / "app")
    assert set(counted) == {"main.py::main"}
    assert counted == _with_settrace(mod.main, tmp_path / "app")


def test_counting_stops_past_max_calls_and_starts_again_for_the_next_test() -> None:
    work = _orders(True)
    t = monitor.MonitorTracer(root_path=ROOT, max_calls=10)
    t.start()
    try:
        work()
    finally:
        t.stop()
    assert t.capped
    assert sum(fn["calls"] for fn in _counts(t.stats, ROOT).values()) == 10
    # switching events off did not leave the next tracer blind
    assert _with_monitoring(work) == _with_settrace(work)
