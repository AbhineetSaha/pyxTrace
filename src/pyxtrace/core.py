"""
core.py – runs a script under ProfileTracer and writes the .pyxt run artifact.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pyxtrace.bytecode import ProfileTracer
from pyxtrace.run import RUN_DIR, commit_sha, save, to_dict
from pyxtrace.visual import render_run


@dataclass
class TraceSession:
    script_path: Path
    out: Path | None = None   # .pyxt run file
    root: Path | None = None  # dir to trace (default: the script's own dir)

    # ------------------------------------------------------------------ #
    @property
    def trace_root(self) -> Path:
        """Directory whose code is profiled.

        Defaults to the script's own directory, which misses a library that
        lives anywhere else — installed in site-packages, or one level up from
        a script in benchmarks/. Point --root at the package to trace it.
        """
        return Path(self.root).resolve() if self.root else self.script_path.parent

    # ------------------------------------------------------------------ #
    def _exec_script(self, tracer) -> None:
        """Run the target script under *tracer*."""
        # CPython puts the script's directory on sys.path for `python app.py`;
        # without this, any script importing a sibling module fails under pyxtrace.
        script_dir = str(self.script_path.parent)
        sys.path.insert(0, script_dir)
        try:
            spec = importlib.util.spec_from_file_location("__main__", self.script_path)
            assert spec is not None
            mod: ModuleType = importlib.util.module_from_spec(spec)
            sys.modules["__main__"] = mod
            sys.settrace(tracer)
            # sys.settrace only covers this thread; settrace() registers the
            # same hook for every thread the script starts after this point
            threading.settrace(tracer)
            try:
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
            finally:
                sys.settrace(None)
                threading.settrace(None)  # type: ignore[arg-type]
        finally:
            # embedders keep running after us; leave their sys.path as we found it
            if sys.path and sys.path[0] == script_dir:
                del sys.path[0]

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        """Accumulate per-function totals and write a .pyxt run."""
        sha = commit_sha()
        # keyed by commit, so `diff main HEAD` works; -o overrides
        out = Path(self.out) if self.out else RUN_DIR / f"{sha or 'untracked'}.pyxt"
        root = self.trace_root

        print(f"[pyxTrace] ➜ profiling '{self.script_path}' → {out}")
        tracer = ProfileTracer(root_path=root)
        try:
            self._exec_script(tracer)
        finally:
            print("[pyxTrace] ✔ finished")

        data = to_dict(tracer.stats, root=root, script=self.script_path.name, commit=sha)
        save(data, out)
        render_run(data, out)
