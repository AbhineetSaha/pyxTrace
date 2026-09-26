"""
One test per bug found in the hardening pass. Each failed on the code before
its fix; the comment on each says what used to happen.
"""
import json
import os
import random
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pyxtrace import cli, core
from pyxtrace import run as runfile
from pyxtrace.bytecode import ProfileTracer

ROOT = Path(__file__).resolve().parent.parent


def _run(script: Path, out: Path, **kw) -> dict:
    core.TraceSession(script, out=out, **kw).run()
    return runfile.load(out)


def _cli(*args: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """The real entry point, in a fresh interpreter, the way CI runs it."""
    return subprocess.run(
        [sys.executable, "-m", "pyxtrace", *args],
        cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _no_seed_env() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)
    # `python -m pyxtrace` must find the package under test, installed or not
    # (no trailing separator: an empty entry would put the cwd on sys.path)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")]))
    return env


# ───────────────────────────── determinism ───────────────────────────── #
HASH_ORDER = (
    "def find(items, target):\n"
    "    for x in items:\n"
    "        if x == target:\n"
    "            return x\n"
    "names = {f'name{i}' for i in range(50)}\n"
    "for t in ('name7', 'name23', 'name41'):\n"
    "    find(names, t)\n"
)


def test_set_iteration_order_does_not_change_counts(tmp_path: Path) -> None:
    """String hashing was randomised per process: set order, and with it the
    number of lines executed, changed every run (183 / 189 / 175)."""
    (tmp_path / "hs.py").write_text(HASH_ORDER)
    for i in range(3):
        r = subprocess.run(
            [sys.executable, "-m", "pyxtrace", "hs.py", "-o", f"h{i}.pyxt"],
            cwd=tmp_path, env=_no_seed_env(), capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
    runs = [(tmp_path / f"h{i}.pyxt").read_bytes() for i in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_the_api_warns_when_hashing_is_random(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("PYTHONHASHSEED")
    monkeypatch.setattr(core, "hash_seed_fixed", lambda: False)
    (tmp_path / "a.py").write_text("x = 1\n")
    _run(tmp_path / "a.py", tmp_path / "a.pyxt")
    assert "PYTHONHASHSEED=0" in capsys.readouterr().err


UNJOINED = (
    "import threading\n"
    "def work():\n"
    "    t = 0\n"
    "    for i in range(20000):\n"
    "        t += i\n"
    "for _ in range(4):\n"
    "    threading.Thread(target=work).start()\n"
)


def test_threads_the_script_does_not_join_are_counted_in_full(tmp_path: Path) -> None:
    """`python app.py` waits for non-daemon threads; pyxtrace recorded while they
    were still running, so `work` counted 73,432 / 60,697 / 78,924 lines."""
    (tmp_path / "thr.py").write_text(UNJOINED)
    for i in range(3):
        r = _cli("thr.py", "-o", f"t{i}.pyxt", cwd=tmp_path)
        assert r.returncode == 0, r.stderr
        assert "still running" not in r.stderr
    runs = [runfile.load(tmp_path / f"t{i}.pyxt") for i in range(3)]
    work = runs[0]["functions"]["thr.py::work"]
    assert work["calls"] == 4
    assert work["lines"] == 4 * (1 + 2 * 20000 + 1)  # t = 0, loop head + body, loop exit
    assert runs[0] == runs[1] == runs[2]


def test_executor_workers_left_idle_do_not_hang_the_run(tmp_path: Path) -> None:
    """Idle ThreadPoolExecutor workers only exit at interpreter shutdown: joining
    them by hand would wait forever."""
    (tmp_path / "ex.py").write_text(
        "from concurrent.futures import ThreadPoolExecutor\n"
        "POOL = ThreadPoolExecutor(2)\n"
        "def sq(x):\n"
        "    return x * x\n"
        "assert list(POOL.map(sq, range(10)))[-1] == 81\n"
    )
    r = subprocess.run([sys.executable, "-m", "pyxtrace", "ex.py", "-o", "ex.pyxt"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert runfile.load(tmp_path / "ex.pyxt")["functions"]["ex.py::sq"]["calls"] == 10


def test_a_daemon_thread_still_running_is_reported(tmp_path: Path) -> None:
    (tmp_path / "d.py").write_text(
        "import threading, time\n"
        "def spin():\n"
        "    while True:\n"
        "        time.sleep(0.01)\n"
        "threading.Thread(target=spin, daemon=True).start()\n"
    )
    r = _cli("d.py", "-o", "d.pyxt", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "still running" in r.stderr
    assert (tmp_path / "d.pyxt").exists()


def test_per_thread_counts_add_up() -> None:
    """Every thread counts into its own shard; reading `stats` merges them."""
    def work():
        return sum(range(3))

    tracer = ProfileTracer(root_path=Path(__file__).parent)
    threading.settrace(tracer)
    try:
        ts = [threading.Thread(target=work) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        threading.settrace(None)  # type: ignore[arg-type]
    (s,) = [s for (_, name), s in tracer.stats.items() if name.endswith("work")]
    assert s["calls"] == 5


def test_unwinding_exceptions_keep_the_call_stack_balanced(tmp_path: Path) -> None:
    """A frame left by an exception must pop, or later calls are attributed to it."""
    script = tmp_path / "exc.py"
    script.write_text(
        "def inner():\n"
        "    raise KeyError\n"
        "def middle():\n"
        "    inner()\n"
        "def after():\n"
        "    return 1\n"
        "def top():\n"
        "    for _ in range(3):\n"
        "        try:\n"
        "            middle()\n"
        "        except KeyError:\n"
        "            pass\n"
        "    after()\n"
        "top()\n"
    )
    fns = _run(script, tmp_path / "exc.pyxt")["functions"]
    top = [k for k in fns if k.endswith("::top")][0]
    assert fns[top]["callees"] == {"exc.py::after": 1, "exc.py::middle": 3}
    assert fns["exc.py::middle"]["callees"] == {"exc.py::inner": 3}


def test_generators_and_coroutines_are_deterministic(tmp_path: Path) -> None:
    script = tmp_path / "gen.py"
    script.write_text(
        "import asyncio\n"
        "def gen(n):\n"
        "    for i in range(n):\n"
        "        yield i\n"
        "async def job(n):\n"
        "    await asyncio.sleep(0)\n"
        "    return sum(gen(n))\n"
        "async def main():\n"
        "    return await asyncio.gather(*(job(n) for n in range(5)))\n"
        "asyncio.run(main())\n"
    )
    a = _run(script, tmp_path / "a.pyxt")
    b = _run(script, tmp_path / "b.pyxt")
    assert a == b
    # a generator gets a call event per resumption: 0+1+2+3+4 yields + 5 exits
    assert a["functions"]["gen.py::gen"]["calls"] == 15


# ─────────────────────────────── naming ──────────────────────────────── #
@pytest.mark.skipif(sys.version_info < (3, 11), reason="co_qualname is 3.11+")
def test_same_named_methods_are_not_merged(tmp_path: Path) -> None:
    """Keyed by bare name, A.__init__ and B.__init__ were one entry, so growth
    in one was diluted by the other and blamed on neither."""
    script = tmp_path / "cls.py"
    script.write_text(
        "class A:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "class B:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "        self.y = 2\n"
        "A(); B(); B()\n"
    )
    fns = _run(script, tmp_path / "cls.pyxt")["functions"]
    assert fns["cls.py::A.__init__"] == {"calls": 1, "lines": 1, "callees": {}}
    assert fns["cls.py::B.__init__"] == {"calls": 2, "lines": 4, "callees": {}}
    assert "cls.py::__init__" not in fns


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_file_reached_through_a_symlink_keeps_one_name(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "lib.py").write_text("def f():\n    return 1\n")
    (tmp_path / "link").symlink_to(real)
    stats = {
        (str(real / "lib.py"), "f"): {"calls": 1, "lines": 1, "own_time": 0.0, "callees": {}},
        (str(tmp_path / "link" / "lib.py"), "f"): {"calls": 2, "lines": 2, "own_time": 0.0,
                                                    "callees": {}},
    }
    fns = runfile.to_dict(stats, root=real, script="lib.py")["functions"]
    assert fns == {"lib.py::f": {"calls": 3, "lines": 3, "own_time": 0.0, "callees": {}}}


# ──────────────────────────── embedding (API) ────────────────────────── #
def test_the_callers_tracer_and_main_module_are_restored(tmp_path: Path) -> None:
    """Running under coverage or a debugger: pyxtrace switched their tracer off
    for the rest of the process, and left the script installed as __main__."""
    script = tmp_path / "a.py"
    script.write_text("x = 1\n")

    def outer(*_):
        return outer

    main = sys.modules["__main__"]
    prev_sys, prev_thr = sys.gettrace(), threading.gettrace()
    sys.settrace(outer)
    threading.settrace(outer)
    try:
        _run(script, tmp_path / "a.pyxt")
        assert sys.gettrace() is outer
        assert threading.gettrace() is outer
    finally:
        sys.settrace(prev_sys)
        threading.settrace(prev_thr)  # type: ignore[arg-type]
    assert sys.modules["__main__"] is main


def test_relative_paths_survive_a_script_that_changes_directory(tmp_path: Path, monkeypatch) -> None:
    """A relative -o was resolved after the script ran, and landed in its new cwd."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "helper.py").write_text("X = 1\n")
    (tmp_path / "d" / "app.py").write_text(
        f"import os\nimport helper\nos.chdir({str(tmp_path / 'd')!r})\n"
    )
    core.TraceSession(Path("d/app.py"), out=Path("rel.pyxt")).run()
    assert (tmp_path / "rel.pyxt").exists()
    assert not (tmp_path / "d" / "rel.pyxt").exists()


def test_the_api_warns_about_threads_it_cannot_wait_for(tmp_path: Path, capsys) -> None:
    """In-process there is no interpreter exit to wait for: say so."""
    script = tmp_path / "a.py"
    script.write_text(
        "import threading, time\n"
        "threading.Thread(target=time.sleep, args=(0.5,)).start()\n"
    )
    _run(script, tmp_path / "a.pyxt")
    assert "still running" in capsys.readouterr().err


# ─────────────────────────────── diff ────────────────────────────────── #
def _random_run(rng: random.Random) -> dict:
    names = [f"m.py::f{i}" for i in range(rng.randint(0, 8))]
    return {
        "pyxtrace": runfile.FORMAT_VERSION,
        "script": "m.py",
        "functions": {
            n: {
                "calls": rng.randint(0, 50),
                "lines": rng.randint(0, 500),
                "callees": {c: rng.randint(1, 30) for c in rng.sample(names, rng.randint(0, len(names)))},
            }
            for n in names
        },
    }


def test_identical_runs_never_regress_at_any_setting() -> None:
    """--threshold 0 --min-ops 0 flagged every function of an unchanged run."""
    rng = random.Random(1234)
    for _ in range(300):
        run = _random_run(rng)
        for threshold in (0, 0.5, 10, 1000):
            for min_ops in (0, 1, 10):
                assert runfile.diff(run, run, threshold=threshold, min_ops=min_ops) == []


def test_diff_findings_only_ever_describe_growth() -> None:
    rng = random.Random(99)
    for _ in range(300):
        a, b = _random_run(rng), _random_run(rng)
        for f in runfile.diff(a, b, threshold=0, min_ops=0):
            assert f["delta"] > 0 or f["delegated"]


# ────────────────────────────── run files ────────────────────────────── #
@pytest.mark.parametrize("content, message", [
    ("{oops", "not a pyxtrace run file"),
    ("[1, 2]", "not a pyxtrace run file"),
    ('{"pyxtrace": 1, "functions": {}}', "recorded by an older pyxtrace"),
    ('{"pyxtrace": 99, "functions": {}}', "unsupported run format"),
    ('{"pyxtrace": 2}', "damaged run file"),
    ('{"pyxtrace": 2, "functions": {"f": {"calls": "3", "lines": 1, "callees": {}}}}',
     "damaged run file"),
    ('{"pyxtrace": 2, "functions": {"f": {"calls": 3, "lines": -1, "callees": {}}}}',
     "damaged run file"),
])
def test_a_bad_run_file_is_refused_with_a_reason(tmp_path: Path, content: str, message: str) -> None:
    """These raised JSONDecodeError / KeyError tracebacks from deep inside diff."""
    bad = tmp_path / "bad.pyxt"
    bad.write_text(content)
    with pytest.raises(ValueError, match=message):
        runfile.load(bad)


def test_a_bad_run_file_is_a_usage_error_not_a_traceback(tmp_path: Path) -> None:
    (tmp_path / "bad.pyxt").write_text("{oops")
    r = _cli("diff", "bad.pyxt", "bad.pyxt", cwd=tmp_path)
    assert r.returncode == 2
    assert "not a pyxtrace run file" in r.stderr
    assert "Traceback" not in r.stderr


def test_save_replaces_the_file_whole(tmp_path: Path) -> None:
    out = tmp_path / "run.pyxt"
    out.write_text("stale")
    run = {"pyxtrace": runfile.FORMAT_VERSION, "script": "x.py", "functions": {}}
    runfile.save(run, out)
    assert runfile.load(out)["functions"] == {}
    assert [p.name for p in tmp_path.iterdir()] == ["run.pyxt"]  # no temp file left


def test_a_directory_named_like_a_ref_is_not_a_run_file(tmp_path: Path, monkeypatch) -> None:
    """`diff main HEAD` with a main/ directory in the cwd raised IsADirectoryError."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main").mkdir()
    with pytest.raises(FileNotFoundError):
        runfile.path_for("main")


def test_a_ref_that_looks_like_an_option_is_not_passed_to_git() -> None:
    assert runfile.commit_sha("--git-dir") is None


# ──────────────────────────────── CLI ────────────────────────────────── #
def test_version(capsys) -> None:
    import pyxtrace

    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"pyxtrace {pyxtrace.__version__}"


def test_options_may_come_before_the_script(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    cli.main(["-o", str(tmp_path / "a.pyxt"), str(tmp_path / "a.py")])
    assert (tmp_path / "a.pyxt").exists()


def test_arguments_after_the_separator_reach_the_script(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "import sys\nassert sys.argv[1:] == ['--epochs', '3', '--'], sys.argv\n"
    )
    cli.main([str(tmp_path / "a.py"), "-o", str(tmp_path / "a.pyxt"), "--",
              "--epochs", "3", "--"])
    assert (tmp_path / "a.pyxt").exists()


@pytest.mark.parametrize("setting", [
    ["--threshold", "-1"],
    ["--min-ops", "-5"],
    ["--min-ops", "1.5"],
])
def test_nonsense_settings_are_rejected(tmp_path: Path, capsys, setting: list[str]) -> None:
    run = tmp_path / "a.pyxt"
    runfile.save({"pyxtrace": runfile.FORMAT_VERSION, "script": "a.py", "functions": {}}, run)
    with pytest.raises(SystemExit) as e:
        cli.main(["diff", str(run), str(run), *setting])
    assert e.value.code == 2
    assert setting[0] in capsys.readouterr().err


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    with pytest.raises(SystemExit) as e:
        cli.main([str(tmp_path / "a.py"), "--root", str(tmp_path / "missing")])
    assert e.value.code == 2


def test_a_failing_script_fails_the_command(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("raise SystemExit(3)\n")
    r = _cli("a.py", "-o", "a.pyxt", cwd=tmp_path)
    assert r.returncode == 3
    assert "no run recorded" in r.stderr
    assert not (tmp_path / "a.pyxt").exists()


def test_output_that_the_terminal_cannot_encode_does_not_crash(tmp_path: Path) -> None:
    """A Windows runner piping through cp1252 cannot encode ✓ or ⚠."""
    (tmp_path / "a.py").write_text("x = 1\n")
    env = {"PYTHONIOENCODING": "cp1252"}
    r = _cli("a.py", "-o", "a.pyxt", cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr
    r = _cli("diff", "a.pyxt", "a.pyxt", cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr


def test_the_relaunch_runs_the_same_pyxtrace_without_the_cwd_on_sys_path(tmp_path: Path) -> None:
    """A `pyxtrace/` directory in the cwd must not replace the real package, and
    the script sees the sys.path `python app.py` would give it."""
    (tmp_path / "pyxtrace").mkdir()
    (tmp_path / "pyxtrace" / "__init__.py").write_text("raise SystemExit('wrong pyxtrace')\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text(
        "import os, sys\n"
        "assert '' not in sys.path and os.getcwd() not in sys.path[1:], sys.path\n"
    )
    env = _no_seed_env()
    boot = "import sys; del sys.path[0]; from pyxtrace.cli import main; main()"
    r = subprocess.run([sys.executable, "-c", boot, "sub/a.py", "-o", "a.pyxt"],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "a.pyxt").exists()


# ───────────────────────────── packaging ─────────────────────────────── #
def test_the_fallback_version_matches_pyproject() -> None:
    """__init__ carries a copy of the version for source checkouts; keep them equal."""
    try:
        import tomllib
    except ModuleNotFoundError:  # 3.10
        pytest.skip("tomllib is 3.11+")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    init = (ROOT / "src" / "pyxtrace" / "__init__.py").read_text()
    assert f'__version__ = "{project["version"]}"' in init


def test_run_files_are_valid_json_with_sorted_keys(tmp_path: Path) -> None:
    out = tmp_path / "run.pyxt"
    _run(ROOT / "examples" / "orders.py", out)
    text = out.read_text()
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
