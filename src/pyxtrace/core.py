"""
core.py – runs a script under ProfileTracer and writes the .pyxt run artifact.
"""

from __future__ import annotations

import atexit
import importlib.util
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pyxtrace.bytecode import ProfileTracer
from pyxtrace.run import (
    RUN_DIR,
    commit_sha,
    display_name,
    run_name,
    save,
    to_dict,
    worktree_dirty,
)
from pyxtrace.visual import render_run


def hash_seed_fixed() -> bool:
    """Is string hashing deterministic in this process?

    With a random seed, iterating a set or dict of strings visits items in a
    different order every run, so the same code executes a different number of
    lines. Only fixable at interpreter start: the CLI re-launches itself with
    PYTHONHASHSEED=0.
    """
    return sys.flags.hash_randomization == 0 or os.environ.get("PYTHONHASHSEED", "").isdigit()


def _warn(msg: str) -> None:
    print(f"[pyxTrace] ⚠ {msg}", file=sys.stderr)


@dataclass
class TraceSession:
    script_path: Path
    out: Path | None = None   # .pyxt run file
    root: Path | None = None  # dir to trace (default: the script's own dir)
    # If the script leaves threads running, finish the run at interpreter exit,
    # after Python's own shutdown has waited for them, as `python app.py` does.
    # Only for a process that exits once the run is done: the CLI.
    wait_at_exit: bool = False

    def __post_init__(self) -> None:
        # resolved now: the script may chdir, and every path is used after it runs
        self.script_path = Path(self.script_path).resolve()
        if self.out is not None:
            self.out = Path(self.out).resolve()
        if self.root is not None:
            self.root = Path(self.root).resolve()

    # ------------------------------------------------------------------ #
    @property
    def trace_root(self) -> Path:
        """Directory whose code is profiled.

        Defaults to the script's own directory, which misses a library that
        lives anywhere else — installed in site-packages, or one level up from
        a script in benchmarks/. Point --root at the package to trace it.
        """
        return self.root or self.script_path.parent

    # ------------------------------------------------------------------ #
    def _exec_script(self, tracer: ProfileTracer) -> None:
        """Run the target script under *tracer*."""
        # CPython puts the script's directory on sys.path for `python app.py`;
        # without this, any script importing a sibling module fails under pyxtrace.
        script_dir = str(self.script_path.parent)
        sys.path.insert(0, script_dir)
        # an embedder (a test runner, a coverage tool) keeps running after us:
        # hand back its __main__ and its trace hooks exactly as we found them
        prev_main = sys.modules.get("__main__")
        prev_trace, prev_thread_trace = sys.gettrace(), threading.gettrace()
        try:
            spec = importlib.util.spec_from_file_location("__main__", self.script_path)
            assert spec is not None and spec.loader is not None
            mod: ModuleType = importlib.util.module_from_spec(spec)
            sys.modules["__main__"] = mod
            sys.settrace(tracer)
            # sys.settrace only covers this thread; settrace() registers the
            # same hook for every thread the script starts after this point
            threading.settrace(tracer)
            try:
                spec.loader.exec_module(mod)
            except SystemExit as e:
                # `sys.exit(main())` is how most scripts end. A clean exit is a
                # finished run; anything else is a failed one and propagates.
                if e.code not in (None, 0):
                    raise
            finally:
                sys.settrace(prev_trace)
                threading.settrace(prev_thread_trace)  # type: ignore[arg-type]
        finally:
            if prev_main is None:
                sys.modules.pop("__main__", None)
            else:
                sys.modules["__main__"] = prev_main
            if sys.path and sys.path[0] == script_dir:
                del sys.path[0]

    # ------------------------------------------------------------------ #
    def run(self) -> dict | None:
        """Accumulate per-function totals and write a .pyxt run; returns the run dict.

        Returns None when the run is finished at interpreter exit instead
        (``wait_at_exit`` and the script left threads running).
        """
        sha = commit_sha()
        out = self.out or RUN_DIR.resolve() / run_name(sha, worktree_dirty())
        root = self.trace_root

        print(f"[pyxTrace] ➜ profiling '{self.script_path}' → {out}")
        if not hash_seed_fixed():
            _warn("string hashing is randomised in this process, so set and dict "
                  "order can change counts between runs; set PYTHONHASHSEED=0 "
                  "(the pyxtrace command does this for you)")
        tracer = ProfileTracer(root_path=root)
        before = set(threading.enumerate())
        try:
            self._exec_script(tracer)
        except BaseException:
            # a partial count is not a baseline anyone should compare against
            print("[pyxTrace] ✘ script failed — no run recorded", file=sys.stderr)
            raise

        def still_running() -> list[threading.Thread]:
            return [t for t in threading.enumerate() if t not in before and t.is_alive()]

        def finish() -> dict:
            left = still_running()
            if left:
                _warn(f"{len(left)} thread(s) the script started were still running "
                      "when the run was recorded; their counts are partial and will "
                      "vary between runs. Join them before the script ends.")
            # saved before anything is printed: `pyxtrace app.py | head` closes
            # the pipe, and the run should survive the output being cut short
            data = to_dict(tracer.stats, root=root,
                           script=display_name(str(self.script_path), root), commit=sha)
            save(data, out)
            print("[pyxTrace] ✔ finished")
            render_run(data, out)
            return data

        if self.wait_at_exit and still_running():
            atexit.register(_finish_at_exit, finish)
            return None
        return finish()


def _finish_at_exit(finish) -> None:
    """atexit runs after Python has joined the script's non-daemon threads."""
    try:
        finish()
    except BaseException:
        import traceback

        traceback.print_exc()
        print("[pyxTrace] ✘ could not record the run", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        # an exception inside an atexit handler does not change the exit
        # status, and CI must not read a missing run as success
        os._exit(1)
