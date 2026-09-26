from pathlib import Path

import pytest

import pyxtrace.core as core
from pyxtrace import run as runfile

# resolve relative to this file so pytest works from any cwd
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "fibonacci.py"


def test_profile_run_counts_are_exact(tmp_path: Path) -> None:
    """fib(5) makes exactly 15 calls — the counts are arithmetic, not samples."""
    out = tmp_path / "run.pyxt"
    core.TraceSession(EXAMPLE, out=out).run()

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
    """Unchanged code must produce a byte-identical run file, so a committed
    baseline shows no git diff until the code actually changes."""
    a, b = tmp_path / "a.pyxt", tmp_path / "b.pyxt"
    core.TraceSession(EXAMPLE, out=a).run()
    core.TraceSession(EXAMPLE, out=b).run()
    assert a.read_bytes() == b.read_bytes()


def test_run_file_carries_no_timing(tmp_path: Path) -> None:
    """own_time varies every run; in a committed artifact it is pure churn."""
    out = tmp_path / "run.pyxt"
    core.TraceSession(EXAMPLE, out=out).run()

    assert "own_time" not in out.read_text()
    assert all("own_time" not in fn for fn in runfile.load(out)["functions"].values())


ORDERS = Path(__file__).resolve().parent.parent / "examples" / "orders.py"


def _orders_run(tmp_path: Path, name: str, n_plus_one: bool, monkeypatch) -> dict:
    monkeypatch.setenv("PYXTRACE_NPLUSONE", "1" if n_plus_one else "0")
    out = tmp_path / name
    core.TraceSession(ORDERS, out=out).run()
    return runfile.load(out)


def test_diff_is_clean_against_itself(tmp_path: Path, monkeypatch) -> None:
    baseline = _orders_run(tmp_path, "a.pyxt", False, monkeypatch)
    assert runfile.diff(baseline, baseline) == []


def test_diff_reports_the_per_item_query_regression(tmp_path: Path, monkeypatch) -> None:
    """Switching to a per-order lookup is extra work, and must be reported."""
    before = _orders_run(tmp_path, "before.pyxt", False, monkeypatch)
    after = _orders_run(tmp_path, "after.pyxt", True, monkeypatch)

    findings = runfile.diff(before, after)
    assert findings, "the extra per-order queries should be reported"

    by_name = {f["name"]: f for f in findings}
    assert "orders.py::process_order" in by_name

    # the growth is attributed to the call that caused it
    callees = {c["name"]: c for c in by_name["orders.py::process_order"]["callees"]}
    fetch = callees["orders.py::fetch_customer"]
    assert (fetch["before"], fetch["after"]) == (0, 120)


def test_diff_ignores_small_changes() -> None:
    """A 2→3 line change is +50% but means nothing; min_ops filters it."""
    before = {"pyxtrace": 1, "script": "x.py", "functions": {"x.py::f": {"calls": 1, "lines": 2, "callees": {}}}}
    after = {"pyxtrace": 1, "script": "x.py", "functions": {"x.py::f": {"calls": 1, "lines": 3, "callees": {}}}}
    assert runfile.diff(before, after) == []


def test_script_can_import_a_sibling_module(tmp_path: Path) -> None:
    """`python app.py` puts the script's dir on sys.path — pyxtrace must too."""
    (tmp_path / "helper.py").write_text("VALUE = 42\n")
    app = tmp_path / "app.py"
    app.write_text("import helper\nassert helper.VALUE == 42\n")

    core.TraceSession(app, out=tmp_path / "run.pyxt").run()  # raised ModuleNotFoundError before

    data = runfile.load(tmp_path / "run.pyxt")
    assert "helper.py::<module>" in data["functions"]


def test_warns_when_only_the_entry_script_was_traced(capsys) -> None:
    """An out-of-tree library records nothing; that must not look like success."""
    from pyxtrace.visual import render_run

    render_run({"script": "app.py", "functions": {"app.py::<module>": {"calls": 1, "lines": 3, "own_time": 0.0}}})
    assert "only the entry script was traced" in capsys.readouterr().out.lower()


def test_root_traces_a_library_outside_the_script_directory(tmp_path: Path) -> None:
    """The default root filters out any package the script does not sit beside."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "mylib.py").write_text("def work(n):\n    return sum(range(n))\n")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    app = app_dir / "main.py"
    app.write_text(f"import sys\nsys.path.insert(0, {str(lib)!r})\nimport mylib\nmylib.work(50)\n")

    core.TraceSession(app, out=tmp_path / "off.pyxt").run()
    core.TraceSession(app, out=tmp_path / "on.pyxt", root=lib).run()

    off = runfile.load(tmp_path / "off.pyxt")["functions"]
    on = runfile.load(tmp_path / "on.pyxt")["functions"]
    assert not any(k.startswith("mylib.py") for k in off)
    assert on["mylib.py::work"]["calls"] == 1


def test_threads_are_traced_and_stay_deterministic(tmp_path: Path) -> None:
    """sys.settrace is per-thread: without threading.settrace a worker is invisible,
    and a shared call stack would interleave frames from different threads."""
    script = tmp_path / "threaded.py"
    script.write_text(
        "import threading\n"
        "def work():\n"
        "    total = 0\n"
        "    for i in range(50):\n"
        "        total += i\n"
        "    return total\n"
        "ts = [threading.Thread(target=work) for _ in range(4)]\n"
        "for t in ts:\n"
        "    t.start()\n"
        "for t in ts:\n"
        "    t.join()\n"
    )

    a, b = tmp_path / "a.pyxt", tmp_path / "b.pyxt"
    core.TraceSession(script, out=a).run()
    core.TraceSession(script, out=b).run()

    fns = runfile.load(a)["functions"]
    assert fns["threaded.py::work"]["calls"] == 4
    # scheduling varies between runs; which lines each thread runs does not
    assert a.read_bytes() == b.read_bytes()


def test_run_is_filed_by_commit_and_diff_resolves_refs(tmp_path: Path, monkeypatch) -> None:
    """CI compares commits, not hand-named files: `pyxtrace diff main HEAD`."""
    import subprocess

    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    sha = runfile.commit_sha()
    assert sha and len(sha) == 40

    core.TraceSession(EXAMPLE).run()  # no -o: keyed by the checked-out commit
    filed = runfile.RUN_DIR / f"{sha}.pyxt"
    assert filed.exists()
    assert runfile.load(filed)["commit"] == sha

    assert runfile.path_for("HEAD") == filed
    assert runfile.path_for(sha[:7]) == filed
    assert runfile.path_for(str(filed)) == filed
    with pytest.raises(FileNotFoundError):
        runfile.path_for("no-such-ref")


def test_cli_implies_run_and_diff_sets_the_exit_code(tmp_path: Path, monkeypatch) -> None:
    from pyxtrace import cli

    monkeypatch.chdir(tmp_path)
    a = tmp_path / "a.pyxt"
    cli.main([str(EXAMPLE), "-o", str(a)])  # bare script → `run`
    assert a.exists()
    for other, code in ((str(a), 0), ("no-such-ref", 2)):
        with pytest.raises(SystemExit) as e:
            cli.main(["diff", str(a), other])
        assert e.value.code == code


def test_a_clean_sys_exit_still_records_the_run(tmp_path: Path) -> None:
    """`sys.exit(main())` is how most scripts end; it wrote nothing and exited 0."""
    app = tmp_path / "app.py"
    app.write_text("import sys\ndef main():\n    return 0\nsys.exit(main())\n")

    core.TraceSession(app, out=tmp_path / "run.pyxt").run()
    assert "app.py::main" in runfile.load(tmp_path / "run.pyxt")["functions"]


@pytest.mark.parametrize("body, exc", [
    ("import sys\nsys.exit(3)\n", SystemExit),
    ("raise ValueError('boom')\n", ValueError),
])
def test_a_failed_script_records_nothing(tmp_path: Path, body: str, exc: type) -> None:
    """Counts from a run that died partway are not a baseline to compare against."""
    app = tmp_path / "app.py"
    app.write_text(body)

    with pytest.raises(exc):
        core.TraceSession(app, out=tmp_path / "run.pyxt").run()
    assert not (tmp_path / "run.pyxt").exists()


def _git(*args: str) -> None:
    import subprocess

    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def test_uncommitted_changes_do_not_overwrite_the_commit_run(tmp_path: Path, monkeypatch) -> None:
    """Experimenting locally filed the run under HEAD and clobbered its clean baseline."""
    from pyxtrace import cli

    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app.py"
    app.write_text("def f():\n    return 1\nf()\n")
    _git("init", "-q")
    _git("add", "app.py")
    _git("commit", "-qm", "x")
    sha = runfile.commit_sha()

    core.TraceSession(app).run()
    clean = runfile.RUN_DIR / f"{sha}.pyxt"
    committed = clean.read_bytes()

    app.write_text("def f():\n" + "    x = 1\n" * 20 + "    return x\nf()\n")
    core.TraceSession(app).run()
    assert clean.read_bytes() == committed
    assert (runfile.RUN_DIR / f"{sha}-dirty.pyxt").exists()

    # `diff HEAD` with no second ref compares against the uncommitted run
    with pytest.raises(SystemExit) as e:
        cli.main(["diff", "HEAD"])
    assert e.value.code == 1


def test_diff_warns_when_runs_come_from_different_pythons(tmp_path: Path, capsys) -> None:
    """3.12 inlined comprehensions: an interpreter upgrade alone moves line counts."""
    from pyxtrace import cli

    fn = {"x.py::f": {"calls": 1, "lines": 2, "callees": {}}}
    for name, py in (("a.pyxt", "cpython-3.11"), ("b.pyxt", "cpython-3.12")):
        runfile.save({"pyxtrace": 1, "python": py, "script": "x.py", "functions": fn},
                     tmp_path / name)

    with pytest.raises(SystemExit):
        cli.main(["diff", str(tmp_path / "a.pyxt"), str(tmp_path / "b.pyxt")])
    assert "different python versions: cpython-3.11 vs cpython-3.12" in capsys.readouterr().out.lower()


def test_run_records_the_interpreter(tmp_path: Path) -> None:
    core.TraceSession(EXAMPLE, out=tmp_path / "run.pyxt").run()
    assert runfile.load(tmp_path / "run.pyxt")["python"] == runfile.interpreter()
