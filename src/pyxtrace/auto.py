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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable

from pyxtrace import monitor
from pyxtrace.run import FORMAT_VERSION, _git, commit_sha, interpreter, merge_base, ref_hint

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
    head_only: int = 0                                 # passed on the head only: new or fixed
    failed: list[str] = field(default_factory=list)   # failed on the head, not compared
    changed: set[str] = field(default_factory=set)    # "file::qualname" changed in the diff
    new: set[str] = field(default_factory=set)        # changed, and not in the base at all
    uncovered: list[str] = field(default_factory=list)  # changed, and no passing test ran it
    unbaselined: list[str] = field(default_factory=list)  # run only by tests new on the head


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


def _all_functions(path: Path) -> set[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return functions_at(source, range(1, source.count("\n") + 2))


def _changed_functions(base: str, head: str, head_tree: Path,
                       new_files: Iterable[str] = ()) -> set[str]:
    """Functions the diff touches; every function in *new_files* (untracked) counts."""
    text = _git_ok("diff", "-U0", "--no-color", "--no-ext-diff", base, head, "--", "*.py")
    lines_by_path = changed_lines(text)
    for path in new_files:
        if path.endswith(".py"):
            lines_by_path[path] = set(range(1, _line_count(head_tree / path) + 1))
    changed = set()
    for path, lines in lines_by_path.items():
        if is_test_path(path):
            continue
        try:
            source = (head_tree / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        changed |= {f"{path}::{q}" for q in functions_at(source, lines)}
    return changed


def _line_count(path: Path) -> int:
    try:
        return len(path.read_bytes().splitlines())
    except OSError:
        return 0


def _untracked() -> list[str]:
    """Files git does not track and does not ignore, relative to the checkout's top.

    `git stash create` leaves them out, so a module that is new and not yet
    added would be missing from the working-tree side.
    """
    top = _git_ok("rev-parse", "--show-toplevel").strip()
    out = _git_ok("-C", top, "ls-files", "--others", "--exclude-standard", "-z")
    top_path = Path(top)
    return [p for p in out.split("\0")
            if p and not p.startswith(".pyxtrace/") and (top_path / p).is_file()
            and not (top_path / p).is_symlink()]


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
_PYTEST = ["-m", "pytest", "-q", "-p", "pyxtrace.pytest_plugin",
           # test order has to match on both sides; no cache from earlier runs
           "-p", "no:randomly", "-p", "no:cacheprovider",
           # a new test importing what the base lacks must not stop the others
           "--continue-on-collection-errors"]


def _pytest(tree: Path, label: str, args: list[str], out: Path, **env: str) -> Dict[str, Any]:
    """Run pytest in *tree* and read what the plugin wrote to *out*."""
    paths = [str(tree)] + [str(tree / "src")] * (tree / "src").is_dir()
    env = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        # the worktree's code, not an installed or editable copy of it
        "PYTHONPATH": os.pathsep.join(paths + [p for p in [os.environ.get("PYTHONPATH")] if p]),
        "PYXTRACE_TRACER": _tracer(),
        "PYXTRACE_PYTEST_ROOT": str(tree),
        **env,
    }
    r = subprocess.run([sys.executable, *_PYTEST, *args], cwd=tree, env=env,
                       capture_output=True, text=True)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-15:])
    if r.returncode not in (0, 1, 5) or not out.is_file():
        if "No module named pytest" in tail:
            raise AutoDiffError("diff without recorded runs runs your tests: `pip install pytest`")
        raise AutoDiffError(f"pytest could not run on {label}:\n{tail}")
    return json.loads(out.read_text(encoding="utf-8"))


def _collect(tree: Path, label: str, scratch: Path) -> tuple[list[str], list[str]]:
    """The test files pytest finds in *tree*, and the arguments that keep its rootdir
    and ini file when it is given one file at a time."""
    found = _pytest(tree, label, ["--collect-only"], scratch / "collected.json",
                    PYXTRACE_PYTEST_COLLECT=str(scratch / "collected.json"))
    if not found["files"]:
        raise AutoDiffError(
            f"no tests found on {label}. Without recorded runs, diff uses your pytest "
            "suite as the workload; add tests, or record runs with `pyxtrace run SCRIPT`"
        )
    config = ["--rootdir", found["rootdir"]] + ["-c", found["inifile"]] * bool(found["inifile"])
    return found["files"], config


def _run_files(tree: Path, label: str, files: list[str], config: list[str], scratch: Path,
               jobs: int) -> Dict[str, Dict[str, Any]]:
    """Each test file's results, from a pytest process of its own.

    A test's counts depend on what ran before it in the same process: caches
    it finds warm, modules already imported. Measured on rich, 298 of 947
    tests counted differently when their file ran alone. One process per file
    keeps a file's counts the same whatever else is run, and the files run in
    parallel.
    """
    _say(f"➜ running {len(files)} test file(s) on {label}")

    def one(i: int, f: str) -> Dict[str, Any]:
        out = scratch / f"{tree.name}-{i}.json"
        return _pytest(tree, label, [*config, f], out, PYXTRACE_PYTEST_OUT=str(out))

    with ThreadPoolExecutor(jobs) as pool:
        return dict(zip(files, pool.map(one, range(len(files)), files), strict=True))


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


def _tracer() -> str:
    """The tracer both sides count with: the same interpreter runs both."""
    return os.environ.get("PYXTRACE_TRACER") or (monitor.NAME if monitor.available() else "settrace")


def _as_run(functions: Dict[str, Any], sha: str) -> Dict[str, Any]:
    return {"pyxtrace": FORMAT_VERSION, "python": interpreter(), "tracer": _tracer(),
            "script": "pytest", "commit": sha, "functions": functions}


def auto_diff(before: str, after: str | None, *, use_merge_base: bool = True,
              jobs: int | None = None) -> AutoResult:
    """Compare *before* and *after* (default: the working tree) by running the tests.

    With *use_merge_base*, the base is where the two diverged, as for a pull
    request: commits on *before* that *after* does not contain are not part of
    the change, and comparing against them would report their effect, reversed,
    as this change's.
    """
    base = commit_sha(before)
    if base is None:
        raise AutoDiffError(f"{before}: not a git commit{ref_hint(before)}")
    if after is None:
        # a commit of the working tree's tracked files, without touching them
        head = _git_ok("stash", "create").strip() or commit_sha("HEAD")
        after_label = "the working tree"
    else:
        head = commit_sha(after)
        after_label = f"{after} ({head[:7]})" if head else after
    if head is None:
        raise AutoDiffError(f"{after}: not a git commit{ref_hint(after or '')}")
    before_label = f"{before} ({base[:7]})"
    if use_merge_base:
        fork = merge_base(base, head)
        if fork is None:
            _say(f"⚠ {before} and {after_label} share no history; comparing the two directly")
        elif fork != base:
            ahead = _git_ok("rev-list", "--count", f"{fork}..{base}").strip()
            _say(f"{before} has {ahead} commit(s) this change does not include; comparing "
                 f"against where they diverged ({fork[:7]}). Pass --no-merge-base to "
                 f"compare against the tip of {before} instead")
            base, before_label = fork, f"merge-base of {before} ({fork[:7]})"

    scratch = Path(tempfile.mkdtemp(prefix="pyxtrace-"))
    trees = {"base": scratch / "base", "head": scratch / "head"}
    try:
        for name, sha in (("base", base), ("head", head)):
            _git_ok("worktree", "add", "--detach", "--quiet", str(trees[name]), sha)
        new_files = _untracked() if after is None else []
        if new_files:
            top = Path(_git_ok("rev-parse", "--show-toplevel").strip())
            for path in new_files:
                dest = trees["head"] / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(top / path, dest)
            _say(f"including {len(new_files)} untracked file(s) in the working tree")

        # the head's tests run on both sides, so only the code under test differs
        tracked = _git_ok("-C", str(trees["head"]), "ls-files").splitlines()
        for path in tracked + new_files:
            if is_test_path(path):
                dest = trees["base"] / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(trees["head"] / path, dest)

        files, config = _collect(trees["head"], after_label, scratch)
        jobs = jobs or os.cpu_count() or 1
        base_results = _run_files(trees["base"], before_label, files, config, scratch, jobs)
        head_results = _run_files(trees["head"], after_label, files, config, scratch, jobs)
        b_tests = {t: c for r in base_results.values() for t, c in r["passed"].items()}
        a_tests = {t: c for r in head_results.values() for t, c in r["passed"].items()}
        failed = sorted(t for r in head_results.values() for t in r["failed"])
        common = sorted(set(b_tests) & set(a_tests))
        if not common:
            raise AutoDiffError(
                "no test passed on both commits, so there is nothing to compare. "
                "Fix the failing tests, or record runs with `pyxtrace run SCRIPT`"
            )
        b_run = _as_run(_merge(b_tests[t] for t in common), base)
        a_run = _as_run(_merge(a_tests[t] for t in common), head)
        changed = _changed_functions(base, head, trees["head"], new_files)
        # new: the base has no such function at all, so there is nothing to compare it with
        new = set()
        for path in {c.split("::", 1)[0] for c in changed}:
            before_names = {f"{path}::{q}" for q in _all_functions(trees["base"] / path)}
            new |= {c for c in changed if c.startswith(f"{path}::") and c not in before_names}
    finally:
        for tree in trees.values():
            if tree.exists():
                _git("worktree", "remove", "--force", str(tree))
        _git("worktree", "prune")
        shutil.rmtree(scratch, ignore_errors=True)

    def run_by(per_test: Iterable[Dict[str, Any]]) -> set[str]:
        ran = {n for fns in per_test for n, fn in fns.items() if fn["calls"]}
        return {c for c in changed if any(is_changed(n, {c}) for n in ran)}

    compared = run_by(a_tests[t] for t in common)
    anywhere = run_by(a_tests.values())
    return AutoResult(
        before=b_run, after=a_run, before_label=before_label, after_label=after_label,
        tests_compared=len(common), head_only=len(set(a_tests) - set(b_tests)),
        failed=failed, changed=changed, new=new,
        uncovered=sorted(changed - anywhere), unbaselined=sorted(anywhere - compared),
    )
