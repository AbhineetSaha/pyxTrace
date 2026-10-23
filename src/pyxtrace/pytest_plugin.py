"""
pytest_plugin.py – counts each test's call phase separately, for `diff` without runs.

Loaded into a pytest subprocess by ``auto.py`` with ``-p pyxtrace.pytest_plugin``;
it does nothing unless PYXTRACE_PYTEST_OUT is set. Only the call phase is
traced: fixtures and collection (imports, module-level code) are setup, not the
work a test measures. Counts are kept per test so ``auto.py`` can compare only
the tests that passed on both commits.
"""

from __future__ import annotations

import gc
import json
import os
import random
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
_failed: set[str] = set()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if _OUT:
        # unseeded `random` changed a test's counts from run to run (measured:
        # jinja's lorem ipsum tests, by up to 18%). Both sides get the same seed,
        # and each test the same one whatever ran before it.
        random.seed(0)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if not _OUT:
        yield
        return
    tracer = ProfileTracer(root_path=_ROOT)
    prev_trace, prev_thread_trace = sys.gettrace(), threading.gettrace()
    # no collection inside the test, where it would run at an allocation count
    # that depends on earlier tests and land their finalizers in this one's
    # counts. (Collecting before each test as well costs 5x on jinja's suite.)
    gc_was_on = gc.isenabled()
    gc.disable()
    sys.settrace(tracer)
    threading.settrace(tracer)
    try:
        yield
    finally:
        sys.settrace(prev_trace)
        threading.settrace(prev_thread_trace)  # type: ignore[arg-type]
        if gc_was_on:
            gc.enable()
    _counts[item.nodeid] = to_dict(tracer.stats, root=_ROOT, script="pytest")["functions"]


def pytest_runtest_logreport(report):
    if report.when == "call" and report.passed:
        _passed.add(report.nodeid)
    elif report.failed:  # in setup, the test itself, or teardown
        _failed.add(report.nodeid)


def pytest_collectreport(report):
    if report.failed:  # a test module that does not even import
        _failed.add(report.nodeid)


def pytest_sessionfinish(session):
    if not _OUT:
        return
    # a failed test's counts stop wherever it failed: not comparable. A test
    # that passed its call but failed in teardown is failed too.
    data = {
        "passed": {n: fns for n, fns in _counts.items() if n in _passed and n not in _failed},
        "failed": sorted(_failed),
    }
    Path(_OUT).write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
