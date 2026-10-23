"""`pyxtrace diff BASE HEAD` with no recorded runs: the test suite is the workload."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pyxtrace.auto import changed_lines, functions_at, is_changed, is_test_path, related_changes

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="needs git",
)

ORDERS = '''\
class Cursor:
    def execute(self, sql, *params):
        return [{"id": p} for p in (params or (0,))]


def fetch_customer(cur, cid):
    cur.execute("one", cid)
    return {"id": cid}


def fetch_customers(cur, ids):
    cur.execute("many", ids)
    return {i: {"id": i} for i in ids}


def process_order(cur, order, customers):
    if customers is None:
        return fetch_customer(cur, order % 20)
    return customers[order % 20]


def handle_request(cur):
    orders = list(range(120))
    customers = fetch_customers(cur, list(range(20)))
    return [process_order(cur, o, customers) for o in orders]


def refund(order_id):
    return order_id * 1.5
'''

TEST = '''\
from shop.orders import Cursor, handle_request


def test_handle_request():
    assert len(handle_request(Cursor())) == 120
'''


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))


def _commit(repo: Path, msg: str, files: dict[str, str]) -> None:
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A src-layout project with tests, no workload script, and no recorded runs."""
    r = tmp_path / "shop"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _commit(r, "base", {
        "src/shop/__init__.py": "",
        "src/shop/orders.py": ORDERS,
        "tests/test_orders.py": TEST,
    })
    _git(r, "checkout", "-qb", "feature")
    return r


def _diff(repo: Path, *refs: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONHASHSEED": "0", "COLUMNS": "200"}
    return subprocess.run([sys.executable, "-m", "pyxtrace", "diff", *refs],
                          cwd=repo, env=env, capture_output=True, text=True)


def _regress(text: str) -> str:
    return text.replace(
        "    customers = fetch_customers(cur, list(range(20)))\n"
        "    return [process_order(cur, o, customers) for o in orders]",
        "    return [process_order(cur, o, None) for o in orders]",
    )


def test_a_regression_is_caught_and_traced_to_the_changed_function(repo: Path) -> None:
    _commit(repo, "regress", {"src/shop/orders.py": _regress(ORDERS)})
    r = _diff(repo, "main", "HEAD")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "src/shop/orders.py::process_order" in r.stdout
    assert "on the call path of changed: src/shop/orders.py::handle_request" in r.stdout
    assert "FAIL" in r.stdout


def test_an_unchanged_workload_passes(repo: Path) -> None:
    _commit(repo, "comment only", {"src/shop/orders.py": ORDERS + "# a comment\n"})
    r = _diff(repo, "main", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_a_test_for_code_the_base_lacks_is_left_out(repo: Path) -> None:
    _commit(repo, "new feature", {
        "src/shop/orders.py": ORDERS + "\n\ndef discount(total):\n    return total * 0.9\n",
        "tests/test_discount.py": "from shop.orders import discount\n\n\n"
                                  "def test_discount():\n    assert discount(10) == 9\n",
    })
    r = _diff(repo, "main", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 test(s) pass only on HEAD" in r.stdout
    # the new function is run by that test, which has no baseline: not "uncovered"
    assert "new function(s) no test runs" not in r.stdout
    assert "run only by tests new in this change" in r.stdout


def test_changed_code_no_test_runs_is_named(repo: Path) -> None:
    _commit(repo, "refund", {"src/shop/orders.py": ORDERS.replace(
        "return order_id * 1.5", "return round(order_id * 1.5, 2)")})
    r = _diff(repo, "main", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 changed function(s) no test runs, so the gate cannot see them" in r.stdout
    assert "src/shop/orders.py::refund" in r.stdout
    assert "0 of 1 changed function(s) were compared" in r.stdout


def test_uncommitted_changes_are_compared_and_left_alone(repo: Path) -> None:
    _write(repo, {"src/shop/orders.py": _regress(ORDERS)})
    r = _diff(repo, "main")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the working tree" in r.stdout
    # the checkout is never touched, and no worktree is left behind
    assert (repo / "src/shop/orders.py").read_text() == _regress(ORDERS)
    worktrees = subprocess.run(["git", "worktree", "list"], cwd=repo,
                               capture_output=True, text=True).stdout
    assert len(worktrees.splitlines()) == 1


def test_no_tests_is_a_usage_error_that_says_what_to_do(repo: Path) -> None:
    _git(repo, "rm", "-q", "tests/test_orders.py")
    _commit(repo, "no tests", {"src/shop/orders.py": _regress(ORDERS)})
    _git(repo, "checkout", "-q", "main")
    _git(repo, "rm", "-q", "tests/test_orders.py")
    _git(repo, "commit", "-qm", "no tests on main either")
    r = _diff(repo, "main", "feature")
    assert r.returncode == 2
    assert "no tests found" in r.stderr


# ─────────────────────────── the diff mapping ─────────────────────── #
def test_changed_lines_reads_the_new_side_of_each_hunk() -> None:
    text = textwrap.dedent("""\
        diff --git a/a.py b/a.py
        --- a/a.py
        +++ b/a.py
        @@ -3,2 +3,3 @@ def f():
        @@ -10 +11,0 @@ def g():
        diff --git a/gone.py b/gone.py
        --- a/gone.py
        +++ /dev/null
        @@ -1,4 +0,0 @@
    """)
    assert changed_lines(text) == {"a.py": {3, 4, 5, 11}}


def test_functions_at_names_the_innermost_function_by_qualname() -> None:
    src = textwrap.dedent("""\
        class Cursor:
            def execute(self):
                def inner():
                    return 1
                return inner()


        @decorator
        def top():
            pass
    """)
    assert functions_at(src, [2]) == {"Cursor.execute"}
    assert functions_at(src, [4]) == {"Cursor.execute.<locals>.inner"}
    assert functions_at(src, [8]) == {"top"}          # the decorator line
    assert functions_at(src, [1, 7]) == set()        # class line, blank line


def test_code_nested_in_a_changed_function_counts_as_changed() -> None:
    changed = {"a.py::f"}
    assert is_changed("a.py::f", changed)
    assert is_changed("a.py::f.<locals>.<listcomp>", changed)
    assert not is_changed("a.py::fg", changed)


def test_related_changes_walks_callers_and_callees() -> None:
    run = {"functions": {
        "a.py::top": {"callees": {"a.py::mid": 1}},
        "a.py::mid": {"callees": {"a.py::leaf": 1}},
        "a.py::leaf": {"callees": {}},
    }}
    assert related_changes("a.py::mid", run, {"a.py::top"}) == ["a.py::top"]
    assert related_changes("a.py::mid", run, {"a.py::leaf"}) == ["a.py::leaf"]
    assert related_changes("a.py::mid", run, set()) == []


@pytest.mark.parametrize("path, expected", [
    ("tests/test_x.py", True), ("test_x.py", True), ("pkg/x_test.py", True),
    ("conftest.py", True), ("tests/helpers.py", True),
    ("src/pkg/x.py", False), ("src/pkg/testing_utils.py", False), ("tests/data.json", False),
])
def test_test_files_are_recognised(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


# ─────────────────────────── which commits ────────────────────────── #
def _branch_behind_a_fix(repo: Path) -> None:
    """main gets a fix after `feature` branched off it; feature changes nothing."""
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "slow", {"src/shop/orders.py": _regress(ORDERS)})
    _git(repo, "checkout", "-qB", "feature")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "fast again", {"src/shop/orders.py": ORDERS})
    _git(repo, "checkout", "-q", "feature")
    _commit(repo, "comment only", {"src/shop/orders.py": _regress(ORDERS) + "# a comment\n"})


def test_a_branch_is_compared_from_where_it_diverged(repo: Path) -> None:
    # main's later fix is not part of this change; against main's tip it would
    # read as this change undoing it
    _branch_behind_a_fix(repo)
    r = _diff(repo, "main", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 commit(s) this change does not include" in r.stderr
    assert "merge-base of main" in r.stdout


def test_a_three_dot_range_is_the_same_as_the_default(repo: Path) -> None:
    _branch_behind_a_fix(repo)
    r = _diff(repo, "main...HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "merge-base of main" in r.stdout


def test_a_two_dot_range_or_the_flag_compares_against_the_tip(repo: Path) -> None:
    _branch_behind_a_fix(repo)
    for refs in (["main..HEAD"], ["main", "HEAD", "--no-merge-base"]):
        r = _diff(repo, *refs)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "merge-base" not in r.stdout


def test_a_range_and_the_flag_cannot_disagree(repo: Path) -> None:
    r = _diff(repo, "main...HEAD", "--no-merge-base")
    assert r.returncode == 2
    assert "contradicts" in r.stderr


def test_a_missing_local_branch_suggests_the_remote_one(repo: Path) -> None:
    # CI checkouts usually have origin/main and no local main
    _git(repo, "update-ref", "refs/remotes/origin/release", "HEAD")
    r = _diff(repo, "release", "HEAD")
    assert r.returncode == 2
    assert "did you mean origin/release?" in r.stderr


def test_ranges_pick_the_recorded_runs_too(repo: Path) -> None:
    _branch_behind_a_fix(repo)
    # untracked, so recording does not mark the runs as uncommitted work
    (repo / "bench.py").write_text(
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent / 'src'))\n"
        "from shop.orders import Cursor, handle_request\n"
        "handle_request(Cursor())\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    fork = subprocess.run(["git", "merge-base", "main", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    for ref in (fork, "main", "feature"):
        _git(repo, "checkout", "-q", ref)
        subprocess.run([sys.executable, "-m", "pyxtrace", "run", "bench.py"],
                       cwd=repo, env=env, check=True, capture_output=True)
    assert _diff(repo, "main...feature").returncode == 0
    assert _diff(repo, "main..feature").returncode == 1


def test_a_shallow_clone_says_why_the_base_is_missing(repo: Path, tmp_path: Path) -> None:
    _commit(repo, "second", {"src/shop/orders.py": ORDERS + "# a comment\n"})
    first = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=repo,
                           capture_output=True, text=True).stdout.strip()
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "feature",
                    repo.as_uri(), str(clone)], check=True, capture_output=True)
    r = _diff(clone, first, "HEAD")
    assert r.returncode == 2
    assert "shallow clone" in r.stderr


def test_an_untracked_module_is_part_of_the_working_tree(repo: Path) -> None:
    # not yet `git add`ed: `git stash create` alone would leave it out, and the
    # head side would fail to import it
    _write(repo, {
        "src/shop/lookup.py": "def lookup(cur, cid):\n"
                              "    cur.execute('one', cid)\n"
                              "    return {'id': cid}\n",
        "src/shop/orders.py": _regress(ORDERS).replace(
            "def fetch_customer(cur, cid):\n",
            "def fetch_customer(cur, cid):\n    from shop.lookup import lookup\n"
            "    lookup(cur, cid)\n"),
    })
    r = _diff(repo, "main")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1 untracked file(s)" in r.stderr
    assert "src/shop/lookup.py::lookup" in r.stdout


# ─────────────────────────── what was left out ─────────────────────── #
BROKEN_TEST = '''\
def test_broken():
    assert 1 == 2
'''


def test_a_test_failing_on_head_is_named_and_only_fails_the_gate_on_request(repo: Path) -> None:
    _commit(repo, "a failing test", {"tests/test_broken.py": BROKEN_TEST})
    r = _diff(repo, "main", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 test(s) failed on HEAD" in r.stdout
    assert "tests/test_broken.py::test_broken" in r.stdout
    r = _diff(repo, "main", "HEAD", "--fail-on-test-failure")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1 test(s) failed (--fail-on-test-failure)" in r.stdout


def test_require_covered_fails_on_changed_code_no_test_runs(repo: Path) -> None:
    _commit(repo, "refund", {"src/shop/orders.py": ORDERS.replace(
        "return order_id * 1.5", "return round(order_id * 1.5, 2)")})
    r = _diff(repo, "main", "HEAD", "--require-covered")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no test runs (--require-covered)" in r.stdout


def test_a_new_function_no_test_runs_is_listed_as_new(repo: Path) -> None:
    _commit(repo, "untested feature", {
        "src/shop/orders.py": ORDERS + "\n\ndef discount(total):\n    return total * 0.9\n"})
    r = _diff(repo, "main", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 new function(s) no test runs yet" in r.stdout
    assert "cannot see them" not in r.stdout


def test_the_test_only_flags_are_refused_for_recorded_runs(repo: Path, tmp_path: Path) -> None:
    run = tmp_path / "a.pyxt"
    run.write_text('{"pyxtrace": 3, "functions": {}}')
    r = _diff(repo, str(run), str(run), "--require-covered")
    assert r.returncode == 2
    assert "apply when diff runs your tests" in r.stderr
