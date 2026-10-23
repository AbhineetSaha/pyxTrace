"""
auto.py – `pyxtrace diff BASE HEAD` with no recorded runs: the test suite is the workload.

Both commits are checked out into temporary git worktrees, so the user's
checkout is never touched. The *head's* test files are copied over the base, so
both sides run identical tests and only the code under test differs. Each test
is counted on its own, and only tests that passed on both sides are compared:
a test for a feature the base does not have yet fails there and is left out,
instead of showing up as a regression.

The diff between the commits then says which changed functions each regression
traces back to.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable

from pyxtrace.bytecode import _QUALNAME
from pyxtrace.run import FORMAT_VERSION, _git, commit_sha, interpreter

# how pytest finds tests by default, plus the conftest files they depend on
_TEST_FILE = re.compile(r"(^test_.*|.*_test|^conftest)\.py$")
_TEST_DIRS = {"test", "tests", "testing"}


class AutoDiffError(Exception):
    """Something the user has to fix before an automatic diff can run."""


@dataclass
class AutoResult:
    before: Dict[str, Any]
    after: Dict[str, Any]
    before_label: str
    after_label: str
    tests_compared: int
    tests_left_out: int
    changed: set[str] = field(default_factory=set)   # "file::qualname" changed in the diff
    uncovered: list[str] = field(default_factory=list)  # changed, but no compared test ran it


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(_TEST_FILE.match(p.name)) or (
        p.suffix == ".py" and any(part in _TEST_DIRS for part in p.parts[:-1])
    )


def _say(msg: str) -> None:
    print(f"[pyxTrace] {msg}", file=sys.stderr, flush=True)


def _git_ok(*args: str) -> str:
    r = _git(*args)
    if r is None:
        raise AutoDiffError("git is not installed")
    if r.returncode != 0:
        raise AutoDiffError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


# ─────────────────────────── what changed ─────────────────────────── #
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(diff_text: str) -> Dict[str, set[int]]:
    """Lines touched on the new side of a ``git diff -U0``, per file.

    A pure deletion touches no new line; the line it sat after is marked
    instead, so the function it was deleted from still counts as changed.
    """
    out: Dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            current = out.setdefault(path[2:], set()) if path.startswith("b/") else None
        elif current is not None:
            m = _HUNK.match(line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                current.update(range(start, start + count) if count else (max(start, 1),))
    return out


def functions_at(source: str, lines: Iterable[int]) -> set[str]:
    """Qualified names of the innermost functions containing *lines*.

    Names follow ``co_qualname`` (``Cursor.execute``, ``outer.<locals>.inner``),
    which is how runs key functions on 3.11+.
    """
    spans: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                first = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                spans.append((first, child.end_lineno or child.lineno, name))
                walk(child, f"{name}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    try:
        walk(ast.parse(source), "")
    except SyntaxError:
        return set()
    found = set()
    for n in lines:
        inside = [s for s in spans if s[0] <= n <= s[1]]
        if inside:
            found.add(max(inside, key=lambda s: s[0])[2])  # innermost starts last
    return found


def _changed_functions(base: str, head: str, head_tree: Path) -> set[str]:
    text = _git_ok("diff", "-U0", "--no-color", "--no-ext-diff", base, head, "--", "*.py")
    changed = set()
    for path, lines in changed_lines(text).items():
        if is_test_path(path):
            continue
        try:
            source = (head_tree / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        names = functions_at(source, lines)
        if not _QUALNAME:  # 3.10 runs key functions by bare name
            names = {q.rsplit(".", 1)[-1] for q in names}
        changed |= {f"{path}::{q}" for q in names}
    return changed


def is_changed(name: str, changed: set[str]) -> bool:
    """Is *name* a changed function, or code nested in one (a comprehension, a closure)?"""
    return name in changed or any(name.startswith(f"{c}.<locals>.") for c in changed)


def related_changes(name: str, run: Dict[str, Any], changed: set[str], limit: int = 3) -> list[str]:
    """Changed functions on *name*'s call path: what calls it, or what it calls.

    Breadth-first, so the nearest changes come first.
    """
    fns = run["functions"]
    callers: Dict[str, set[str]] = {}
    for caller, fn in fns.items():
        for callee in fn.get("callees", {}):
            callers.setdefault(callee, set()).add(caller)
    found: list[str] = []
    for edges in (lambda n: callers.get(n, ()), lambda n: fns.get(n, {}).get("callees", {})):
        seen, frontier = {name}, [name]
        while frontier and len(found) < limit:
            nxt = []
            for n in frontier:
                for m in sorted(edges(n)):
                    if m not in seen:
                        seen.add(m)
                        nxt.append(m)
                        if is_changed(m, changed) and m not in found:
                            found.append(m)
            frontier = nxt
    return found[:limit]


# ─────────────────────────── running tests ────────────────────────── #
def _run_tests(tree: Path, label: str, scratch: Path) -> Dict[str, Dict[str, Any]]:
    """Run pytest in *tree*; per-test counts of the tests that passed."""
    out = scratch / f"{tree.name}.json"
    paths = [str(tree)] + [str(tree / "src")] * (tree / "src").is_dir()
    env = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        # the worktree's code, not an installed or editable copy of it
        "PYTHONPATH": os.pathsep.join(paths + [p for p in [os.environ.get("PYTHONPATH")] if p]),
        "PYXTRACE_PYTEST_OUT": str(out),
        "PYXTRACE_PYTEST_ROOT": str(tree),
    }
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "pyxtrace.pytest_plugin",
           # test order has to match on both sides; no cache from earlier runs
           "-p", "no:randomly", "-p", "no:cacheprovider",
           # a new test importing what the base lacks must not stop the others
           "--continue-on-collection-errors"]
    _say(f"➜ running tests on {label}")
    r = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-15:])
    if r.returncode == 5:
        raise AutoDiffError(
            f"no tests found on {label}. Without recorded runs, diff uses your pytest "
            "suite as the workload; add tests, or record runs with `pyxtrace run SCRIPT`"
        )
    if r.returncode not in (0, 1) or not out.is_file():
        if "No module named pytest" in tail:
            raise AutoDiffError("diff without recorded runs runs your tests: `pip install pytest`")
        raise AutoDiffError(f"pytest could not run on {label}:\n{tail}")
    data: Dict[str, Dict[str, Any]] = json.loads(out.read_text(encoding="utf-8"))
    return data


def _merge(per_test: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Add per-test counts up into one run's functions."""
    total: Dict[str, Dict[str, Any]] = {}
    for fns in per_test:
        for name, fn in fns.items():
            t = total.setdefault(name, {"calls": 0, "lines": 0, "callees": {}})
            t["calls"] += fn["calls"]
            t["lines"] += fn["lines"]
            for callee, n in fn["callees"].items():
                t["callees"][callee] = t["callees"].get(callee, 0) + n
    for t in total.values():
        t["callees"] = dict(sorted(t["callees"].items()))
    return dict(sorted(total.items()))


def _as_run(functions: Dict[str, Any], sha: str) -> Dict[str, Any]:
    return {"pyxtrace": FORMAT_VERSION, "python": interpreter(), "script": "pytest",
            "commit": sha, "functions": functions}


def auto_diff(before: str, after: str | None) -> AutoResult:
    """Compare *before* and *after* (default: the working tree) by running the tests."""
    base = commit_sha(before)
    if base is None:
        raise AutoDiffError(f"{before}: not a git commit")
    if after is None:
        # a commit of the working tree's tracked files, without touching them
        head = _git_ok("stash", "create").strip() or commit_sha("HEAD")
        after_label = "the working tree"
    else:
        head = commit_sha(after)
        after_label = f"{after} ({head[:7]})" if head else after
    if head is None:
        raise AutoDiffError(f"{after}: not a git commit")
    before_label = f"{before} ({base[:7]})"

    scratch = Path(tempfile.mkdtemp(prefix="pyxtrace-"))
    trees = {"base": scratch / "base", "head": scratch / "head"}
    try:
        for name, sha in (("base", base), ("head", head)):
            _git_ok("worktree", "add", "--detach", "--quiet", str(trees[name]), sha)

        # the head's tests run on both sides, so only the code under test differs
        for path in _git_ok("-C", str(trees["head"]), "ls-files").splitlines():
            if is_test_path(path):
                dest = trees["base"] / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(trees["head"] / path, dest)

        b_tests = _run_tests(trees["base"], before_label, scratch)
        a_tests = _run_tests(trees["head"], after_label, scratch)
        common = sorted(set(b_tests) & set(a_tests))
        if not common:
            raise AutoDiffError(
                "no test passed on both commits, so there is nothing to compare. "
                "Fix the failing tests, or record runs with `pyxtrace run SCRIPT`"
            )
        b_run = _as_run(_merge(b_tests[t] for t in common), base)
        a_run = _as_run(_merge(a_tests[t] for t in common), head)
        changed = _changed_functions(base, head, trees["head"])
    finally:
        for tree in trees.values():
            if tree.exists():
                _git("worktree", "remove", "--force", str(tree))
        _git("worktree", "prune")
        shutil.rmtree(scratch, ignore_errors=True)

    ran = {n for n, fn in a_run["functions"].items() if fn["calls"]}
    uncovered = sorted(c for c in changed if not any(is_changed(n, {c}) for n in ran))
    return AutoResult(
        before=b_run, after=a_run, before_label=before_label, after_label=after_label,
        tests_compared=len(common),
        tests_left_out=len(set(b_tests) | set(a_tests)) - len(common),
        changed=changed, uncovered=uncovered,
    )
