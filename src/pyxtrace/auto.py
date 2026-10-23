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
import hashlib
import importlib.metadata
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
    files_run: int = 0                                 # test files run on the head
    files_total: int = 0                               # of all the suite's test files
    failed: list[str] = field(default_factory=list)   # failed on the head, not compared
    unstable: list[str] = field(default_factory=list)  # counts moved between head runs
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


def _changed_functions(base: str, head: str, base_tree: Path, head_tree: Path,
                       new_files: Iterable[str] = ()) -> tuple[set[str], str | None]:
    """Functions the diff touches, and why any test at all may be affected, if so.

    Every function in *new_files* (untracked) counts. A change to anything but
    a function's code can reach any test: a module constant, an import, a data
    or config file, a deleted module.
    """
    new_files = list(new_files)
    text = _git_ok("diff", "-U0", "--no-color", "--no-ext-diff", base, head, "--", "*.py")
    lines_by_path = changed_lines(text)
    for path in new_files:
        if path.endswith(".py"):
            lines_by_path[path] = set(range(1, _line_count(head_tree / path) + 1))
    wide = None
    for path in _git_ok("diff", "--name-only", "--no-renames", base, head).splitlines() + new_files:
        if is_test_path(path) or (path.endswith(".py") and (head_tree / path).is_file()):
            continue
        if not _inert(path, base_tree, head_tree):
            wide = wide or f"{path} changed"
    changed = set()
    for path, lines in lines_by_path.items():
        if is_test_path(path):
            continue
        try:
            source = (head_tree / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        changed |= {f"{path}::{q}" for q in functions_at(source, lines)}
        if _outside_functions(source, lines):
            wide = wide or f"{path} changed outside a function"
    return changed, wide


def _inert(path: str, base_tree: Path, head_tree: Path) -> bool:
    """Is *path* a file whose change cannot move a test's counts?

    Prose, CI workflows, and package metadata (a version bump, a dependency
    that is not installed until someone reinstalls). Without this, nearly
    every commit runs every test: 20 of rich's last 25 touch a changelog,
    a README or the version.
    """
    # shortcut: a test reading a .md file that pytest does not collect is missed
    if path.endswith((".md", ".rst")) or path.startswith(".github/"):
        return True
    if path == "pyproject.toml" and sys.version_info >= (3, 11):
        import tomllib

        def rest(tree: Path) -> Any:
            try:
                data = tomllib.loads((tree / path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return {k: v for k, v in data.items() if k not in ("project", "build-system")}

        before = rest(base_tree)
        return before is not None and before == rest(head_tree)
    return False


def _outside_functions(source: str, lines: set[int]) -> bool:
    """Do *lines* touch code that runs outside any function body: an import, a
    constant, a class attribute? Comments, blank lines and docstrings do not count."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True

    def hit(body: list[ast.stmt]) -> bool:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                continue  # a docstring
            if isinstance(node, ast.ClassDef):
                first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                if lines & set(range(first, node.body[0].lineno)) or hit(node.body):
                    return True
            elif lines & set(range(node.lineno, (node.end_lineno or node.lineno) + 1)):
                return True
        return False

    return hit(tree.body)


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
               jobs: int, runs: int = 1) -> Dict[str, Dict[str, Any]]:
    """Each test file's results, from a pytest process of its own, *runs* times over.

    A test's counts depend on what ran before it in the same process: caches
    it finds warm, modules already imported. Measured on rich, 298 of 947
    tests counted differently when their file ran alone. One process per file
    keeps a file's counts the same whatever else is run, and the files run in
    parallel.
    """
    _say(f"➜ running {len(files)} test file(s) on {label}" + f", {runs} times" * (runs > 1))
    tasks = [f for _ in range(runs) for f in files]

    def one(i: int, f: str) -> Dict[str, Any]:
        out = scratch / f"{tree.name}-{i}.json"
        return _pytest(tree, label, [*config, f], out, PYXTRACE_PYTEST_OUT=str(out))

    by_file: Dict[str, list[Dict[str, Any]]] = {f: [] for f in files}
    with ThreadPoolExecutor(jobs) as pool:
        for f, r in zip(tasks, pool.map(one, range(len(tasks)), tasks), strict=True):
            by_file[f].append(r)
    return {f: _settle(rs) for f, rs in by_file.items()}


def _settle(runs: list[Dict[str, Any]]) -> Dict[str, Any]:
    """One file's result from repeated runs: a test whose counts moved between them is
    unstable, and comparing it would report noise as a regression."""
    first, *rest = runs
    failed = sorted(set().union(*(r["failed"] for r in runs)))
    passed = {t: c for t, c in first["passed"].items() if all(r["passed"].get(t) == c for r in rest)}
    unstable = sorted(set(first["passed"]) - set(passed) - set(failed))
    return {"passed": passed, "failed": failed, "unstable": unstable}


# ─────────────────────── base results, kept per file ─────────────────────── #
def _sha(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        b = p if isinstance(p, bytes) else p.encode()
        h.update(len(b).to_bytes(8, "big") + b)  # length first: no two lists hash alike
    return h.hexdigest()


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _cache_keys(tree: Path, sha: str, files: list[str], test_paths: list[str],
                cache: Path) -> Dict[str, Path]:
    """Where each test file's results on commit *sha* are kept.

    A file's results are a pure function of what the key hashes: the commit's
    code, the file, every test helper and conftest beside it, and the
    interpreter, tracer, installed packages and pyxTrace that counted it.
    Anything else that changes them would make the cache give wrong answers,
    so the key errs toward too much.
    """
    # shortcut: a test module importing another test module is not in its key;
    # hash every test file into every key if that turns up
    shared = _sha(
        sys.version, sys.platform, _tracer(), str(FORMAT_VERSION),
        *sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()),
        *(_bytes(p) for p in sorted(Path(__file__).parent.glob("*.py"))),
        sha,
        *(part for p in test_paths if p not in files for part in (p, _bytes(tree / p))),
    )
    return {f: cache / f"{_sha(shared, f, _bytes(tree / f))}.json" for f in files}


def _load(path: Path) -> Dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _store(path: Path, result: Dict[str, Any]) -> None:
    # shortcut: entries are never deleted; prune .pyxtrace/tests by age if it grows
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)  # a reader never sees half a file
    except OSError as e:
        _say(f"⚠ could not keep results in {path.parent} ({e.strerror or e}); counting again next time")


def _reaches(result: Dict[str, Any], changed: set[str]) -> bool:
    """Can a change to *changed* move this file's counts, judging by its run on the base?

    A test that failed there ran an unknown part of itself, so it might.
    """
    return bool(result["failed"]) or any(
        fn["calls"] and is_changed(name, changed)
        for fns in result["passed"].values() for name, fn in fns.items())


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
              jobs: int | None = None, stability_runs: int = 1,
              all_tests: bool = False) -> AutoResult:
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

    top = Path(_git_ok("rev-parse", "--show-toplevel").strip())
    scratch = Path(tempfile.mkdtemp(prefix="pyxtrace-"))
    trees = {"base": scratch / "base", "head": scratch / "head"}
    try:
        for name, sha in (("base", base), ("head", head)):
            _git_ok("worktree", "add", "--detach", "--quiet", str(trees[name]), sha)
        new_files = _untracked() if after is None else []
        if new_files:
            for path in new_files:
                dest = trees["head"] / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(top / path, dest)
            _say(f"including {len(new_files)} untracked file(s) in the working tree")

        # the head's tests run on both sides, so only the code under test differs
        tracked = _git_ok("-C", str(trees["head"]), "ls-files").splitlines()
        test_paths = sorted(p for p in tracked + new_files if is_test_path(p))
        for path in test_paths:
            dest = trees["base"] / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(trees["head"] / path, dest)

        files, config = _collect(trees["head"], after_label, scratch)
        jobs = jobs or os.cpu_count() or 1
        keys = _cache_keys(trees["base"], base, files, test_paths, top / ".pyxtrace" / "tests")
        base_results = {f: r for f in files if (r := _load(keys[f])) is not None}
        missing = [f for f in files if f not in base_results]
        if base_results:
            _say(f"{len(base_results)} of {len(files)} test file(s) on {before_label} "
                 "counted before, reusing them")
        if missing:
            fresh = _run_files(trees["base"], before_label, missing, config, scratch, jobs)
            for f, r in fresh.items():
                base_results[f] = r
                _store(keys[f], r)

        changed, wide = _changed_functions(base, head, trees["base"], trees["head"], new_files)
        if all_tests or wide:
            selected = files
            if wide and not all_tests:
                _say(f"{wide}, which any test may depend on: running every test file")
        else:
            selected = [f for f in files if _reaches(base_results[f], changed)]
            _say(f"{len(selected)} of {len(files)} test file(s) run code this change touches; "
                 "running only those (--all-tests runs every one)")
        head_results = _run_files(trees["head"], after_label, selected, config, scratch, jobs,
                                  runs=stability_runs) if selected else {}
        b_tests = {t: c for r in base_results.values() for t, c in r["passed"].items()}
        a_tests = {t: c for r in head_results.values() for t, c in r["passed"].items()}
        failed = sorted(t for r in head_results.values() for t in r["failed"])
        unstable = sorted(t for r in head_results.values() for t in r["unstable"])
        common = sorted(set(b_tests) & set(a_tests))
        if not common and selected == files:  # with fewer files run, this can be fine
            raise AutoDiffError(
                "no test passed on both commits, so there is nothing to compare. "
                "Fix the failing tests, or record runs with `pyxtrace run SCRIPT`"
            )
        b_run = _as_run(_merge(b_tests[t] for t in common), base)
        a_run = _as_run(_merge(a_tests[t] for t in common), head)
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
        files_run=len(selected), files_total=len(files),
        failed=failed, unstable=unstable, changed=changed, new=new,
        uncovered=sorted(changed - anywhere), unbaselined=sorted(anywhere - compared),
    )
