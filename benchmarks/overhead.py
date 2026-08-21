#!/usr/bin/env python3
"""
Overhead + determinism gate for the tracer hot path.

    python benchmarks/overhead.py

Loads bytecode.py directly so it runs with no third-party deps installed.
Exits non-zero if the default (profiling) path regresses past MAX_OVERHEAD or
stops producing byte-stable counts.
"""
from __future__ import annotations

import cProfile
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

# x slower than untraced; see PYXTRACE_PRODUCT_STRATEGY.md §18. Sized to catch a
# structural regression (the cached path filter going away costs ~2,287x), not
# drift: shared CI runners measure 40-56x for the same code.
MAX_OVERHEAD = 75

_SRC = Path(__file__).resolve().parent.parent / "src" / "pyxtrace" / "bytecode.py"
_spec = importlib.util.spec_from_file_location("_pyx_bytecode", _SRC)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
FilteredTracer = _mod.FilteredTracer
ProfileTracer = _mod.ProfileTracer

HERE = Path(__file__).resolve().parent


class CountingLog:
    """Stand-in for core._AsyncLog — serialises like the real writer does."""

    def __init__(self) -> None:
        self.n = 0
        self.bytes = 0

    def enqueue(self, obj: dict) -> None:
        self.n += 1
        self.bytes += len(json.dumps(obj, default=str)) + 1


def fib(n: int) -> int:
    return n if n < 2 else fib(n - 1) + fib(n - 2)


def _time(fn, *a) -> float:
    t0 = time.perf_counter()
    fn(*a)
    return time.perf_counter() - t0


def _under(tracer, n: int) -> float:
    sys.settrace(tracer)
    try:
        return _time(fib, n)
    finally:
        sys.settrace(None)


def main() -> int:
    n = 22
    base = min(_time(fib, n) for _ in range(5))
    print(f"baseline fib({n}) = {base * 1e3:.2f} ms\n")

    # --- default path: accumulate in memory --------------------------- #
    # Best-of-N on both sides. Timing the baseline best-of-5 against a single
    # traced run inflated the ratio and made this gate flake on CI.
    prof_times = []
    for _ in range(5):
        prof = ProfileTracer(root_path=HERE)
        prof_times.append(_under(prof, n))
    prof_dt = min(prof_times)
    prof_ratio = prof_dt / base
    total_calls = sum(s["calls"] for s in prof.stats.values())
    print("default path (ProfileTracer, accumulates in memory)")
    print(f"  {prof_dt * 1e3:8.1f} ms   {prof_ratio:7.1f}x   {total_calls:,} calls tracked, 0 bytes written\n")

    # --- opt-in path: raw JSONL event stream -------------------------- #
    print("--events path (FilteredTracer, one JSONL record per event)")
    print(f"  {'mode':6} {'time_ms':>10} {'overhead':>10} {'events':>10} {'log_MB':>9}")
    for mode in ("demo", "perf", "full"):
        log = CountingLog()
        dt = _under(FilteredTracer(log, mode=mode, root_path=HERE), n)
        print(f"  {mode:6} {dt * 1e3:10.1f} {dt / base:9.1f}x {log.n:10,} {log.bytes / 1e6:9.2f}")

    # --- reference ----------------------------------------------------- #
    pr = cProfile.Profile()
    pr.enable()
    cp_dt = _time(fib, n)
    pr.disable()
    print(f"\nreference: cProfile {cp_dt / base:.1f}x")

    # --- determinism: the property the whole approach rests on ---------- #
    # Same work, seven times. Wall-clock wanders; operation counts do not.
    # These are the numbers the README and the launch post quote.
    wall, counts = [], []
    for _ in range(7):
        p = ProfileTracer(root_path=HERE)
        wall.append(_under(p, 16))
        counts.append(sum(s["lines"] for s in p.stats.values()))

    wall_spread = statistics.stdev(wall) / statistics.mean(wall)
    spread = 0.0 if len(set(counts)) == 1 else statistics.stdev(counts) / statistics.mean(counts)

    print("\nsame work, 7 runs:")
    print(f"  wall-clock       : {'  '.join(f'{w * 1e3:.2f}ms' for w in wall)}")
    print(f"                     spread {wall_spread * 100:.1f}%  <- too noisy to gate on")
    print(f"  operation counts : {'  '.join(str(c) for c in counts)}")
    print(f"                     spread {spread * 100:.1f}%  <- safe to gate on")

    ok = True
    if prof_ratio > MAX_OVERHEAD:
        print(f"\nFAIL: default-path overhead {prof_ratio:.0f}x exceeds budget {MAX_OVERHEAD}x")
        ok = False
    if spread != 0.0:
        print(f"\nFAIL: line counts are not deterministic ({counts})")
        ok = False
    if ok:
        print(
            f"\nPASS: default path {prof_ratio:.0f}x (budget {MAX_OVERHEAD}x), "
            f"counts deterministic"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
