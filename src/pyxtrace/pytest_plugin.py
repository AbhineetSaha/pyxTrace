"""
pytest_plugin.py – counts each test's call phase separately, for `diff` without runs.

Loaded into a pytest subprocess by ``auto.py`` with ``-p pyxtrace.pytest_plugin``;
it does nothing unless PYXTRACE_PYTEST_OUT is set. Only the call phase is
traced: fixtures and collection (imports, module-level code) are setup, not the
work a test measures. Counts are kept per test so ``auto.py`` can compare only
the tests that passed on both commits.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from pyxtrace.bytecode import ProfileTracer
from pyxtrace.run import to_dict

_OUT = os.environ.get("PYXTRACE_PYTEST_OUT")
_ROOT = Path(os.environ.get("PYXTRACE_PYTEST_ROOT", ".")).resolve()

_counts: Dict[str, Dict[str, Any]] = {}
_passed: set[str] = set()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if not _OUT:
        yield
        return
    tracer = ProfileTracer(root_path=_ROOT)
    prev_trace, prev_thread_trace = sys.gettrace(), threading.gettrace()
    sys.settrace(tracer)
    threading.settrace(tracer)
    try:
        yield
    finally:
        sys.settrace(prev_trace)
        threading.settrace(prev_thread_trace)  # type: ignore[arg-type]
    _counts[item.nodeid] = to_dict(tracer.stats, root=_ROOT, script="pytest")["functions"]


def pytest_runtest_logreport(report):
    if report.when == "call" and report.passed:
        _passed.add(report.nodeid)


def pytest_sessionfinish(session):
    if not _OUT:
        return
    # a failed test's counts stop wherever it failed: not comparable
    data = {nodeid: fns for nodeid, fns in _counts.items() if nodeid in _passed}
    Path(_OUT).write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
