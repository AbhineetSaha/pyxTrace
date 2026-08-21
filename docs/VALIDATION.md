# Validating the premise on real code

> **Basis:** sqlglot (7k★, pure-Python SQL parser) at 13 real consecutive commits, plus
> jinja2 for package-layout probes. Every number measured on this machine, 2026-08-21.
> Python 3.14.6. Nothing here comes from `examples/orders.py`.

## Why this repo, and which commits

sqlglot commit [`105cbb67`](https://github.com/tobymao/sqlglot/commit/105cbb67368d133b18ef541dd5141626873485a0)
("Fix(parser): handle chained table join with consecutive USING clauses") shipped a genuine
performance regression. The maintainer found it and reverted the shape five hours later in
[`84033b01e`](https://github.com/tobymao/sqlglot/commit/84033b01e0ebbd655ab5982c19cf92b924b6ecec),
titled *"Fix: performance regression due to 105cbb67"*.

The bug: the original code used `if/elif` so that matching `ON` or `USING` short-circuited.
The new code called the condition parser first and then ran `_parse_joins()` **unconditionally**,
speculatively parsing and then retreating on every join. Cost doubles per join in the chain.

Three commits were traced: `105cbb67^` (before), `105cbb67` (regressed), `84033b01e` (fixed).

**The detail that matters for the launch post:** the guard the maintainer added against
recurrence is this, in sqlglot's own test suite:

```python
now = time.time()
query = parse_one("""select * FROM a LEFT JOIN b ON a.id = b.id ...""")
self.assertLessEqual(time.time() - now, 0.1)
```

A hardcoded wall-clock threshold, in a top-tier Python project, guarding a real regression.
That is the thesis's antagonist found in the wild, not constructed.

---

## The headline result

The regression's severity depends on how many joins are chained — it is exponential:

| joins | before | regressed | ratio |
|---|---|---|---|
| 1 | 0.100 ms | 0.103 ms | **1.03x** |
| 2 | 0.140 ms | 0.171 ms | 1.22x |
| 4 | 0.208 ms | 0.568 ms | 2.73x |
| 8 | 0.372 ms | 7.32 ms | 19.7x |
| 18 | 1.02 ms | 7,339 ms | 7,200x |

At 18 joins anything catches it. **The interesting case is 1 join, where the regression is
3% — and measured wall-clock spread on this machine is 3.1%.** The regression is smaller
than the noise.

Running the gate on that case:

```
⚠  sqlglot/parser.py::_parse_join  +40% operations  (47 → 66)
   Attributed to:
     sqlglot/parser.py::_retreat   2 → 4 calls   +100%
     sqlglot/parser.py::_match_pair   6 → 8 calls   +33%
     ...
✗ FAIL — 1 function(s) grew by more than 10%
```

It names `_parse_join` — the exact function the commit modified — and attributes the growth
to `_retreat` doubling, which *is* the bug: speculative parse, then backtrack. One finding,
no noise around it. A maintainer reading this goes straight to the defect.

**Caveat, and it is a real one: this required `--min-ops 5`. At the shipped default of
`--min-ops 100` the gate passes and misses the regression entirely.** The default was
calibrated against `examples/orders.py`, where the synthetic N+1 moves thousands of
operations. On real code a hot parser function grows by 19 operations on a single query.
The default is tuned to the wrong workload.

### Where wall clock is not just noisy but wrong

Correlating operation growth against time growth across the join sweep:

| joins | Δ wall time | Δ lines | µs per line |
|---|---|---|---|
| 1 | **−0.9 µs** | +44 | — |
| 2 | +31 µs | +1,459 | 0.021 |
| 4 | +311 µs | +15,245 | 0.020 |
| 6 | +1,621 µs | +78,333 | 0.021 |
| 8 | +6,740 µs | +338,629 | 0.020 |

**Pearson r = 0.99995** (n=8), at a stable ~20 ns per traced line across four orders of
magnitude. Note the first row: the clock says the regressed build is *faster*. The sign is
wrong. The count says +44 operations, and the count is correct.

---

## The five unknowns

### 1. Overhead on real code — **9.2x, not 43x**

Workload: parse + regenerate 838 real SQL statements × 10 (0.687 s untraced).

| | time | overhead |
|---|---|---|
| untraced | 0.687 s | 1x |
| **pyxtrace** | **6.29 s** | **9.2x** |
| cProfile, identical region | 2.28 s | 3.4x |
| *(reference)* pyxtrace on `fib(22)` | — | 42x |

The prediction was right: 43x was an artifact of `fib(22)`, which does almost nothing per
call. Real code amortises the per-event cost over actual work. **9.2x is a usable CI number**
— and it is 2.7x cProfile, capturing strictly more (per-callee edges).

### 2. Root-path filter on real layouts — **silent failure confirmed. This was the worst bug found.**

Same jinja2 workload, three placements:

| setup | functions traced | verdict |
|---|---|---|
| src-layout, script at repo root | 306 | works |
| src-layout, script in `benchmarks/` subdir | **1** | silently empty |
| library `pip install`ed, script anywhere | **1** | silently empty |

The last row is the *normal* way people work. `pip install` a library, write a benchmark
script, run the gate — you trace exactly one function, your own script's module, and
pyxtrace prints a cheerful table and exits 0. Commit that as a baseline and the gate passes
forever, on nothing.

There was already a `"No functions traced"` guard, but it could never fire: the entry script
is always traced, so the broken case is one function, not zero.

**Fixed** ([visual.py](../src/pyxtrace/visual.py)) — the condition now tests whether anything
*beyond the entry script* was captured, and warns explicitly that the run is empty and a
baseline saved from it will never detect a regression. This makes the failure visible; it
does not make the case work. A `--root` flag is the obvious follow-up.

### 3. N+1 detection — **fires rarely, and the one time it fired on real code it was wrong**

Across 12 consecutive real sqlglot commits at shipped defaults:

| | count |
|---|---|
| pairs that failed the gate | 2 / 12 |
| true positive (the actual regression commit) | 1 |
| N+1 patterns flagged | 1 |
| N+1 flags that were correct | **0** |

The one N+1 fired on *"Feat!(mysql): add support for multi-table DELETE syntax"*:

```
Pattern detected: N+1 — _parse_delete() runs 14x and calls _match() 3x each
  42 total calls to _match(), was 14. Batch it outside the loop.
```

There is no loop. The commit changed `_parse_delete` from one `_match(FROM)` to three
(`_match(FROM, advance=False)`, `_match(FROM)`, `_match(USING)`) to support MySQL's
multi-table syntax. That is a **constant** +2 calls per invocation, not work scaling with
item count. The advice printed is actively misleading.

**This is structural, not a tuning problem.** The heuristic fires when a callee grows by
≥ `parent_calls`, which any constant-factor increase of ≥1 call per invocation satisfies.
Distinguishing "once per item" from "twice per call" requires knowing the item count — and
you cannot infer complexity from a single input size. Detecting N+1 honestly means running
the workload at **two different input sizes** and checking whether the callee/parent ratio
scales. That is a different measurement than diffing two commits.

Recommendation: either rebuild it as a two-size scaling check, or cut the N+1 claim. As
shipped it produces confident wrong diagnoses, which is worse than producing none.

### 4. Threads and async — **not fatal, and the assumption about async was backwards**

| construct | traced? |
|---|---|
| main thread | yes |
| **asyncio coroutines** | **yes** |
| `threading.Thread` | **no — 0 functions** |
| `ThreadPoolExecutor` | **no — 0 functions** |
| `subprocess` | no |

Async is *not* invisible: coroutines run on the event loop's thread, which is the traced
thread. (One wrinkle: a coroutine gets a fresh `call` event per resumption, so `calls`
counts resumptions, not invocations.)

Threads genuinely capture nothing. But this is a small fix, not a wall. Prototyped
`threading.settrace()` plus a `threading.local` call stack (the current single shared
`self._stack` would corrupt under concurrency), then ran 4 threads doing real sqlglot
parsing, 5 times:

```
run0..run4: functions=193  total_calls=131884  total_lines=637028   (identical, 5/5)
```

**Counts stay exactly deterministic under real thread concurrency**, because scheduling
changes the interleaving but not which lines each thread executes. This is the most
important finding for the direction: the risk flagged as most likely to kill it is roughly
six lines of work. *(Prototyped in the scratchpad only — not applied to the repo, per
instructions not to build.)*

Caveat: the shared `stats` dict is unguarded and would race under free-threaded builds.

### 5. Do counts predict cost — **r = 0.99995, and then a blind spot big enough to matter**

The correlation above is about as good as a correlation gets. But it holds *within* a
workload whose operation mix is homogeneous. Counter-example, two files with identical
Python structure where the cost moved into C:

```python
# v1: cheap C call            # v2: catastrophic regex backtracking, all inside C
S.replace("a", "b")           PAT.match(S)      # PAT = re.compile(r"^(a+)+$")
```

| | wall time | traced lines | calls |
|---|---|---|---|
| v1 | 0.0001 s | 608 | 2 |
| v2 | **23.08 s** | **608** | 2 |

```
$ pyxtrace diff v1.pyxt v2.pyxt --min-ops 1 --threshold 0.01
✓ PASS — no function grew by more than 0.01%
```

**A 230,000x regression passes at maximum sensitivity.** pyxtrace counts *Python*
operations. Cost inside C extensions, regex, I/O, syscalls, or the network is structurally
invisible — the exact mirror of "sampling profilers structurally cannot be deterministic."

This is not fatal but it must be stated in the README before a user discovers it. The honest
scope: **pure-Python CPU-bound logic** — parsers, compilers, template engines, serializers,
business logic, and call-count explosions like ORM N+1 (each query is a Python-level call,
so the *count* is visible even though the query's cost is not). It is weak on numeric,
dataframe, and I/O-bound code, which is a large share of Python backends.

---

## Determinism on real code — holds, with a packaging wart

1,057 functions traced across two runs of identical code: `calls`, `lines`, and `callees`
identical for every single function. The gate passes on identical code even at `--min-ops 1`.
**Run-to-run false positives are zero, and that is the real claim** — you can turn
sensitivity to maximum and noise still never fires. No wall-clock benchmark can offer that.

But the `.pyxt` file is **not** byte-identical between runs, because `own_time` is stored in
it. For a format explicitly designed to be committed as a baseline, that means every run
produces a git diff on every function. Either drop `own_time` from the file or move it out of
the committed artifact.

---

## Bugs found and fixed

| # | bug | status |
|---|---|---|
| 1 | `pyxtrace run app.py` crashed on **any** script importing a sibling module — `sys.path` never got the script's directory, which CPython does for `python app.py` | **fixed** in [core.py](../src/pyxtrace/core.py), + regression test |
| 2 | Empty-trace guard could never fire; installed/out-of-tree libraries produced a silently empty baseline with exit 0 | **fixed** in [visual.py](../src/pyxtrace/visual.py), + regression test |

Bug 1 blocked the entire validation at step one — it means the tool could not run any
multi-file project, which is the whole target audience. 10/10 tests pass; overhead gate
passes at 42x.

## Open, not fixed (decisions for you)

- **`--min-ops 100` default misses real regressions.** Measured: 5 works on sqlglot. Needs a
  defensible default, or scaling relative to the function's own baseline rather than absolute.
- **No `--root` flag**, so installed packages cannot be traced at all. Bug 2 makes this
  visible rather than silent, but the case still does not work.
- **N+1 detection** — rebuild as a two-input-size scaling check, or cut it.
- **`own_time` in the committed artifact** causes baseline churn.
- **Threading** — ~6 lines, prototype validated.

## Verdict

The premise survives, and the sqlglot case is a genuine launch story: a real regression, in
a real project, that the maintainers guarded with exactly the flaky wall-clock assertion the
post argues against — caught deterministically at a 3% wall-clock delta where the clock's
sign was wrong.

Two things must change before that story is told. The scope has to narrow honestly to
pure-Python CPU-bound code, because the C and I/O blind spot is total and the first user to try
this on a Django app will find it and say so publicly. And the N+1 claim needs to be rebuilt
or dropped, because it currently emits confident misdiagnoses on real commits.

The integrations on the roadmap (pytest plugin, GitHub Action, `pyxtrace check`) are worth
building **after** the `min_ops` default and the `--root` gap are closed — those two decide
whether a first-time user sees a real finding or an empty file.
