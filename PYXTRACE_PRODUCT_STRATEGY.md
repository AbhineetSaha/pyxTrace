# PyxTrace — Product Strategy

> **Basis:** full read of all 1,174 LOC, live benchmarks of the tracer, and live PyPI/GitHub
> data. Every number below is measured, not estimated.
> **Date:** 2026-08-21 · **Analyzed version:** 1.2.0
>
> **Status: sections 1–17 describe v1.2.0 as it was found. The five priorities in THE PLAN
> have since been implemented (v2.0.0)** — see *Implementation status* below. The v1.2.0
> analysis is kept unedited as the record of why the direction changed.

### Implementation status (v2.0.0)

| # | Priority | Status | Result |
|---|---|---|---|
| 1 | Cache the path filter | ✅ | **25,700x → 44x** overhead; gate in `benchmarks/overhead.py` fails past 50x |
| 2 | Per-function counts in memory | ✅ | `ProfileTracer` + `.pyxt` run files; 38 MB log → 0 bytes written during the run |
| 3 | `pyxtrace diff` | ✅ | Ranks regressions, attributes to callees, detects N+1, exits 1 |
| 4 | Delete dead code, slim deps | ✅ | `syscalls/`, `memory.py`, `kernelspy.py`, `replay.py`, the Streamlit dashboard removed; 4 deps → 2; `--capture-returns` now opt-in |
| 5 | Honest README + real example | ✅ | Rewritten around the wedge; `examples/orders.py` carries a plantable N+1 |

Also fixed along the way: `requires-python` corrected to 3.10+ (3.8/3.9 never worked),
`import pyxtrace` no longer starts `tracemalloc`, the duplicate argparse CLI is gone,
PyPI keywords/URLs/author added, and `.github/workflows/ci.yml` now exists so the README
badge resolves.

**Still open** (from §19 onward): pytest plugin, GitHub Action + PR comment, committed
baselines and `pyxtrace check`, performance budgets in `pyproject.toml`, and the Week 4
growth work (new demo GIF, launch post, repo description/topics).

---

## 1. Executive Summary

PyxTrace today is a byte-code event logger with a chart viewer. It records *that* code ran.
It never reports *how long* anything took, and its Rich summary prints three integers.
It cannot answer "why is my code slow" — the question its README implies it answers.

Three measured facts drive this document:

| Measurement | Result |
|---|---|
| Overhead, `--mode full` on `fib(22)` | **~25,700x slower** than untraced |
| Log volume for a **0.9 ms** program | **38 MB** JSONL, 343,878 events |
| Real-world traction | **1 star, 17 downloads/month, 0 user-filed issues** |

But ~99.9% of that overhead is a self-inflicted bug, not a limit of `sys.settrace`:

| Stage of the per-event hot path | Cumulative slowdown |
|---|---|
| Bare `sys.settrace`, no work | 8x |
| **+ `Path(...).resolve()` on every event** | **2,287x** ← the bug |
| + `tracemalloc.get_traced_memory()` per event | 18,022x |
| + dict build + `json.dumps` x2 per event | 24,795x |
| **Same filter, cached by `co_filename`** | **14x** |
| Cached + in-memory counters (no JSON) | 31x |
| *(reference)* stdlib `cProfile` | 8.1x |

A one-line cache turns a 2,287x filter into a 14x filter — **a 163x speedup from memoizing a
string lookup.** A fixed PyxTrace lands at ~31x, within 4x of cProfile while capturing
strictly more. The engine is salvageable. The product on top of it is not yet defined.

**Recommended direction:** stop competing with profilers. Become the tool that **catches
performance regressions in CI using deterministic operation counts instead of flaky
wall-clock timing.** This is a wedge sampling profilers are structurally incapable of
occupying, and the one thing PyxTrace's existing `sys.settrace` architecture is genuinely
best-suited to do.

---

## 2. Current Product Analysis

### Architecture (1,174 LOC, 15 files)

```
cli.py ──┐
         ├─→ core.TraceSession.run() ─→ sys.settrace(FilteredTracer) ─→ _AsyncLog ─→ JSONL
__main__.py ┘                                     │
                                                  └─→ (after script exits) Streamlit replay

bytecode.py   FilteredTracer — the only tracer actually wired up
memory.py     MemoryTracer — DEAD, never instantiated
syscalls/     5 files, ~190 LOC — DEAD, never instantiated
kernelspy.py  standalone `sudo` script — unreferenced, imports psutil (not a dependency)
replay.py     `pyxtrace replay` subcommand
visual.py     Rich summary (3 counters) + Streamlit/Plotly dashboard
```

**What it actually does:** installs a `sys.settrace` hook, filters frames to the traced
script's parent directory, and for each accepted event writes two JSONL records — one
`BytecodeEvent`, one `MemoryEvent` carrying a process-wide `tracemalloc` scalar. On exit it
prints three counts, or replays the finished log into a Plotly chart.

### Confirmed dead or broken

| Issue | Evidence |
|---|---|
| Syscall tracing never runs | `core.py:117` constructs only `FilteredTracer`; no `SyscallTracer` outside `syscalls/` |
| Even if wired, it would not display | tracers emit `kind="syscall"`; `visual.py:39` reads `kind=="SyscallEvent"` |
| `MemoryTracer` never runs | never instantiated; memory comes from an inline call in `bytecode.py` |
| "Live real-time dashboard" is a replay | `run()` fully executes the script *before* `if self.dash:`; the fps slider paces a finished file |
| `_replay_worker` never terminates | `while True` in a daemon process, spinning on a file already at EOF |
| `import pyxtrace` breaks on Python 3.8/3.9 | `memory.py:18` imports `typing.TypeAlias` (3.10+); `requires-python = ">=3.8"` and the 3.8/3.9 classifiers are false |
| `import pyxtrace` silently starts `tracemalloc` | module-level `tracemalloc.start()` in `bytecode.py:19` — a global side effect on any import |
| `--no-syscalls` documented, does not exist | in README; in no parser |
| CI badge is permanently broken | points at `workflows/ci.yml`; only `publish.yml` exists |
| `psutil` imported, not declared | `kernelspy.py:17`; only `types-psutil` in dev extras |
| PyPI metadata is placeholder | `author_email = abhineet@example.com`, `keywords: None`, `project_urls: None` |
| Two arg parsers for the same flags | `__main__.py` re-implements `--mode/--dash/--log` in argparse; `cli.py` does it in Typer |
| Public `Event` TypedDict is wrong | declares `ts/event/func/file/line/module`; real records carry `kind`/`payload` |
| Publish workflow mints a token it never uses | manual OIDC exchange, then `gh-action-pypi-publish` does its own |
| Only 1 test, 14 lines | hardcodes relative `examples/fibonacci.py`; fails outside repo root |
| Threads/async/subprocesses untraced | `sys.settrace` is per-thread; no `threading.settrace` |

**Streamlit as a hard dependency** pulls pandas, pyarrow, altair, tornado and ~50 transitive
packages into every `pip install pyxtrace`. A tracing tool should be a small install.

### The Product Map

**1. What it does** — logs per-line/per-call execution events plus a coarse heap scalar to
JSONL, then renders cumulative-count charts.

**2. Who it is currently useful for** — realistically, someone teaching how `sys.settrace`
works, on a toy script. The event-count chart has pedagogical value. There is no current
professional use case.

**3. What makes it different** — honestly: nothing defensible. "Byte-code + memory +
syscalls with a live dashboard" describes VizTracer, which does all of it better, faster,
and with a real timeline UI. Two of the three claimed pillars are dead code.

**4. What is currently weak** — overhead (25,700x). No timing analysis at all. No function
attribution. Log volume (38 MB / 0.9 ms). Memory data is a process-wide scalar attributed to
nothing.

**5. What is confusing** — a "live" dashboard that is a post-hoc replay. A speed slider that
paces playback of an already-finished run; new users will read it as a profiler control.
Three modes (`full`/`perf`/`demo`) that differ by 8% in cost, so the presets buy nothing.

**6. What feels unfinished** — `abhineet@example.com` in PyPI metadata. Broken CI badge. No
repo description or topics. A roadmap table of four 🔄 items and one ✅.

**7. What is surprisingly valuable** — **the deterministic event stream.** Measured over 7
runs of identical work:

```
wall-clock   : 0.2ms 0.1ms 0.1ms 0.1ms 0.1ms 0.1ms 0.1ms  → stdev/mean = 16.8%
event counts : 405   405   405   405   405   405   405    → stdev/mean =  0.0%
```

> **Correction (v2.0.0):** the 16.8% figure was measured on a ~0.003 ms workload, where
> timer granularity dominates and inflates the spread. Re-measured on a realistic 2 ms
> workload on an idle machine, wall-clock spread is **3.1%**; counts remain exactly 0.0%.
> Shared CI runners are noisier than an idle machine, but that was not measured here, so
> no figure is claimed for it. The argument holds — a threshold you can set at 5% versus
> one you cannot — but the honest gap is a few percent, not seventeen. Reproduce with
> `python benchmarks/overhead.py`.

Wall-clock is too noisy to gate CI on. Event counts are *perfectly* reproducible. Nobody in
the Python OSS ecosystem is selling this. That is the asset.

**8. What could become a killer feature** — `pyxtrace diff`: a regression gate that compares
two runs on deterministic counts and fails a PR with an attributed cause. See §11.

---

## 3. Current Strengths

1. **Deterministic tracing core** — 0.0% run-to-run variance on counts (measured).
2. **JSONL event format** — greppable, streamable, trivially diffable. Good instinct.
3. **Salvageable performance** — the 25,700x is one memoization away from ~31x.
4. **Small, readable codebase** — 1,174 LOC is fast to refactor, not a legacy burden.
5. **Package name + PyPI slot already owned**, release pipeline works.

## 4. Current Weaknesses

1. Answers no performance question. The summary is three integers.
2. 25,700x overhead — cannot touch a real workload.
3. 38 MB of log per millisecond of program.
4. ~40% of shipped code is dead (`syscalls/`, `memory.py`, `kernelspy.py`).
5. README describes a product that does not exist (syscalls, live dashboard, `--no-syscalls`).
6. Broken on the Python versions it claims to support.
7. Heavyweight install for a tracer.
8. No timing, no call tree, no flame graph, no hot-path detection.
9. Threads, async, and subprocesses are invisible.
10. Zero trust signals: 1 star, broken badge, placeholder email, 1 test.

---

## 5. Target Users

**Primary ICP: maintainers of Python libraries and backend services who already run CI and
have been burned by a performance regression reaching production.**

Chosen because they (a) already have the CI where the product lives, (b) feel regressions as
*incidents*, not annoyances, (c) are the population that stars and blogs about tools, and
(d) can adopt without asking anyone's permission — a single workflow file.

**Explicitly not targeting:** ML engineers (want GPU/tensor profiling — Scalene/PyTorch
profiler own it), DevOps (want continuous production profiling — Pyroscope/Datadog own it),
beginners (won't wire CI), data engineers (bottleneck is usually Spark/DB, not Python frames).

---

## 6. Developer Pain Points (ranked)

Scored 1–10. **Fit** = how well PyxTrace's *deterministic* architecture suits it.

| # | Pain | Current solution & why it frustrates | Freq | Sev | Fit | Score |
|---|---|---|---|---|---|---|
| 1 | "Which commit made this slower?" | Manual bisect + `time`; CI wall-clock too noisy to gate, so nobody gates | 9 | 9 | 10 | **28** |
| 2 | "This PR added an N+1 query nobody noticed" | Found in prod, or by luck in review | 8 | 9 | 10 | **27** |
| 3 | "Our benchmark job is flaky so we ignore it" | `pytest-benchmark` + wide thresholds → alarm fatigue | 8 | 8 | 10 | **26** |
| 4 | "What changed between two versions?" | Diff two profiles by hand; no tool does it well | 7 | 8 | 9 | **24** |
| 5 | "Why is this function slow?" | cProfile — output is an unsorted wall of numbers | 10 | 7 | 5 | 22 |
| 6 | "Where is memory growing?" | Memray (excellent, but a separate run + separate mental model) | 7 | 8 | 5 | 20 |
| 7 | "I can't read profiler output" | snakeviz/flamegraphs; requires expertise to interpret | 8 | 6 | 5 | 19 |
| 8 | "Slow only with real data" | Add logging, rerun with prod fixtures | 7 | 7 | 4 | 18 |
| 9 | "Prod behaves differently from local" | APM ($$$) or nothing | 8 | 8 | 2 | 18 |
| 10 | "Profile without modifying the app" | py-spy — already solves this near-perfectly | 8 | 7 | 1 | 16 |
| 11 | "Which code path spikes memory?" | Memray — solves it | 6 | 8 | 3 | 17 |
| 12 | "Background job is 4x slower" | Logs + guessing | 6 | 7 | 4 | 17 |

**Pains 1–4 cluster into one product.** All four are *comparison over time*, and all four are
poorly served precisely because wall-clock in CI is unreliable. Pains 5–12 are already owned
by tools with 3–28M monthly downloads. Do not enter those.

---

## 7. Competitive Analysis

Monthly PyPI downloads, fetched live 2026-08-20:

| Tool | Downloads/mo | Solves | Does extremely well | Does poorly |
|---|---|---|---|---|
| **py-spy** | 28.7M | Profile a running process, no code changes | Near-zero-overhead sampling, attach to prod by PID | No memory; no comparison between runs |
| **memray** | 15.7M | Where memory is allocated | Best-in-class allocation tracking, great reports | CPU time is not its job |
| **pytest-benchmark** | 13.3M | Microbenchmarks in the test suite | Easy to adopt, stats built in | **Wall-clock → flaky in CI; the core problem** |
| **pyinstrument** | 10.7M | Readable call-tree wall-clock profile | The most human-readable output in Python | Sampling → non-deterministic; no CI gate |
| **memory-profiler** | 6.6M | Line-by-line memory | Simple mental model | Unmaintained, slow |
| **line_profiler** | 3.0M | Line-by-line timing | Precise where you already suspect | Requires `@profile`; you must guess first |
| **pytest-codspeed** | 2.45M | **CI regression detection** | Valgrind instruction counts = stable | **Commercial/hosted; needs codspeed.io account; heavy** |
| **austin** | 961K | Frame-stack sampling | Tiny, C, fast | Niche UX |
| **scalene** | 392K | CPU vs GPU vs memory, AI suggestions | Genuinely novel separation | Complex; heavy install |
| **cProfile** | stdlib | Deterministic function profile | Always there, 8.1x overhead | Unreadable output; no diff |
| **VizTracer** | — | Timeline visualization | Excellent Perfetto-based UI | **This is what PyxTrace is currently trying to be, and it wins** |
| OTel / Sentry / Datadog / New Relic / Pyroscope | — | Production observability | Distributed context, alerting | Requires infra, cost, org buy-in; not for a PR gate |

**Where PyxTrace must NOT compete:**

- **Sampling / attach-to-prod** — py-spy is a Rust-based near-zero-overhead tool. Unwinnable.
- **Memory allocation depth** — Memray is Bloomberg-backed and excellent. Unwinnable.
- **Timeline visualization** — VizTracer already ships the Perfetto UI. Unwinnable, and it is
  exactly where PyxTrace is currently pointed.
- **Production APM** — needs a company.

**The gap:** `pytest-codspeed` (2.45M/mo) proves demand for CI regression gating on stable
counters. But it requires a **hosted commercial account** and **Valgrind**. There is no
credible open-source, local-first, zero-account, pure-Python equivalent. That is the hole.

---

## 8. Potential Product Directions

| Dir | Thesis | Verdict |
|---|---|---|
| **A. Runtime perf diff ("git diff for performance")** | Compare two runs, attribute the delta | ✅ **Strongest.** Fits the deterministic core; nothing OSS owns it |
| **B. Production-safe lightweight tracing** | Understand prod cheaply | ❌ `sys.settrace` is deterministic tracing — the *wrong primitive* for prod. Would mean rewriting as a sampler, i.e. becoming a worse py-spy |
| **C. AI-assisted perf debugging** | Explain the bottleneck, suggest a fix | ⚠️ Good *feature*, fatal *product*. Needs trustworthy data first; an LLM over a 38 MB event dump hallucinates. Revisit after A |
| **D. Universal / language-agnostic profiler** | Python, Node, Java, Go, Rust | ❌ **Reject.** Every language needs a bespoke instrumentation backend; the JSONL format is the only reusable part, and format-only reuse is worth ~nothing. At 5–10 hrs/week this guarantees shipping nothing |
| **E. Performance regression testing in CI** | Baseline → compare → fail PR | ✅ **Strongest.** This is A's distribution channel |

**A and E are the same product.** A is the engine; E is how it reaches users. Direction D is
the classic trap — it sounds impressive and is unbuildable at this budget.

---

## 9. Recommended Product Direction

> **Build the deterministic performance-regression gate for Python CI. Ship the diff engine
> (A) as a pytest plugin and GitHub Action (E). Delete everything else.**

The reasoning chain:

1. Wall-clock has irreducible run-to-run variance — 3.1% measured on an idle machine, and
   worse on a shared CI runner → teams set thresholds so wide the gate is useless, or they
   delete it.
2. Deterministic operation counts have **0.0%** variance (measured) → a 5% threshold is
   meaningful.
3. `sys.settrace` produces exactly those counts. PyxTrace already has it.
4. Sampling profilers (py-spy, pyinstrument, austin, Scalene) are **structurally incapable**
   of this — sampling is non-deterministic by construction. This is a real moat, not a
   feature gap they can close.
5. `pytest-codspeed` validates the demand (2.45M/mo) but is commercial + Valgrind-dependent.
6. It fits the budget: the engine is a diff over two JSON files.

---

## 10. PyxTrace Wedge

**"Wall-clock benchmarks are too noisy to gate a PR on. Operation counts aren't. Gate on
those instead."**

Answering the required question honestly — *why install PyxTrace over cProfile, py-spy,
Scalene, Memray, Sentry, or OpenTelemetry?*

**Today: there is no reason. None. It does strictly less than every one of them.**

**After this plan:** because none of those tools tell you *whether the PR in front of you
made things worse*, and PyxTrace does — reproducibly enough to block a merge.

---

## 11. Killer Feature

```
$ pyxtrace diff main.pyxt pr-482.pyxt

  ⚠  process_order()  +4,231% operations   (312 → 13,507)

  Attributed to:
    psycopg2.cursor.execute        3 → 127 calls   (+4,133%)
    order.serialize()             12 → 508 calls

  Pattern detected: N+1 query
    127 SQL calls for 1 request, inside a loop at orders/service.py:88

  Verdict: FAIL — exceeds budget (max +10% ops on process_order)
```

The single tellable line: **"it caught an N+1 query in code review, before it merged."**

That is a story a developer repeats to a colleague. "It draws a chart of my byte-code" is not.

Crucially, the N+1 detection here is *inference over deterministic counts* — call count per
callee per parent frame — not an LLM guess. It is reliable because it is arithmetic.

---

## 12. Developer Experience Audit

Arriving cold at the GitHub page today:

| Question | Reality |
|---|---|
| Time to first useful result | Never — the output is 3 integers |
| README immediately understandable? | Reads well, but describes a product that doesn't exist |
| Value in 30 seconds? | No. `pyxtrace fibonacci.py` → `syscalls: 0, byte-ops: 343878, mem samples: 343878` |
| Output impressive? | The Demo.gif is; the actual CLI is not. The gap damages trust more than a plain README would |
| Installation frictionless? | `pip install pyxtrace` drags in Streamlit + pandas + pyarrow. Broken on 3.8/3.9 |
| Examples realistic? | One 9-line `fib()`. Nothing resembling real code |
| Screenshots/GIFs? | Yes — but 5 MB committed to the repo, paid on every clone |
| Terminology clear? | "byte-ops" is not a unit anyone reasons about |
| Do I know when to reach for it? | No. Nothing states the use case |
| Looks production-quality? | No — broken CI badge, `example.com` email, no description, no topics |
| Would I trust it? | No. And 1 star / 17 downloads confirms the market agrees |

### The 5-Minute Wow Experience (designed)

```bash
pip install pyxtrace                              # 1. small, fast, no Streamlit
pyxtrace bench examples/orders.py -o before.pyxt  # 2. realistic example w/ a real N+1
# ... developer applies the suggested batching fix ...
pyxtrace bench examples/orders.py -o after.pyxt
pyxtrace diff before.pyxt after.pyxt              # 3-8. measurable, attributed improvement

  ✓ process_order()  -96% operations  (13,507 → 494)
      psycopg2.cursor.execute  127 → 3 calls
  Verdict: PASS
```

**Why the current project cannot deliver this:** it never records timing or per-function
attribution, so steps 4–8 have no data to stand on; and at 25,700x overhead, any example
realistic enough to contain an N+1 would take hours to run.

---

## 13. Production Readiness

| Dimension | Status | Blocker |
|---|---|---|
| Runtime overhead | ❌ | 25,700x measured |
| Log volume | ❌ | 38 MB per 0.9 ms |
| Thread safety | ❌ | `sys.settrace` is per-thread; no `threading.settrace` |
| Async | ❌ | Coroutines produce misleading call/return nesting |
| Multiprocessing | ❌ | Child processes untraced |
| Memory overhead | ❌ | `tracemalloc` always-on ≈ 2x heap, started at *import* |
| Long-running processes | ❌ | Unbounded JSONL growth, no rotation or cap |
| Failure behavior | ⚠️ | `_replay_worker` spins forever; daemon procs leak |
| Security / Privacy / PII | ❌ | `rec["return_value"] = repr(arg)` **serializes every return value to disk** — passwords, tokens, PII. No redaction, no opt-out |
| Trace storage / serialization | ❌ | Plain JSONL in CWD, no size cap, no sampling |
| API stability | ❌ | Public `Event` type doesn't match emitted records |
| Backward compat | ❌ | Claims 3.8+; breaks on 3.8/3.9 |

**`return_value` capture is the most serious issue in the repo.** A tracer that writes every
function's return value to an unencrypted file in the CWD, by default, is a data-exfiltration
liability. It must be opt-in and redacted before any adoption push.

**Verdict:** PyxTrace cannot be used in a serious project today. In the recommended
direction, "production" means *CI*, not *prod runtime* — a far lower and achievable bar.

---

## 14. Feature Opportunities

**Serve the wedge (build):** deterministic operation counting per function; run serialization
to a compact baseline format; `diff` with attribution; call-count-per-callee (the N+1
signal); budgets in config; pytest plugin; GitHub Action + PR comment.

**Adjacent, later:** allocation counts per function (deterministic, unlike byte totals); CPU
vs I/O split via syscall counts; flame graph derived from the call tree; HTML report.

**Do not serve the wedge:** live dashboards, timeline UI, remote profiling, continuous
profiling, sampling mode, VS Code integration, multi-language support.

### On "Intelligence" — what can actually be inferred reliably

The example output in the brief ("Root cause: N+1, confidence 87%") is achievable, but only
the arithmetic half. Reliable inferences from deterministic counts:

- **Call-count ratios** — `execute` called 127x within one `process_order` call is a fact,
  not a guess. N+1 detection is sound.
- **Delta attribution** — which function's op count grew is exact.
- **Callee blame** — which child accounts for the growth is exact.

Unreliable, and should not be shipped as claims:

- **"Likely cause"** narratives beyond call-pattern arithmetic.
- **Confidence percentages** — there is no calibrated model behind them; a fabricated 87% is
  worse than no number.
- **Suggested fixes** — fine as a link to a doc pattern, not as generated code.

---

## 15. Feature Prioritization Matrix

Scored 1–10. **Priority = (Value + Diff + Freq + Adopt + Retain + Prod) ÷ Effort.**

| Feature | Val | Diff | Freq | Adopt | Retain | Feas | Effort | Prod | OSS | $ | **Priority** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Fix `Path.resolve()` cache (163x)** | 10 | 3 | 10 | 9 | 8 | 10 | **1** | 9 | 6 | 3 | **55.0** |
| **Delete dead code + slim deps** | 7 | 2 | 8 | 8 | 5 | 10 | **1** | 9 | 7 | 2 | **39.0** |
| Redact `return_value` by default | 6 | 2 | 5 | 4 | 3 | 10 | **1** | 10 | 5 | 3 | **32.0** |
| **Per-function timing + call counts** | 10 | 6 | 10 | 9 | 9 | 9 | **2** | 9 | 8 | 5 | **28.5** |
| **Honest README + real example** | 9 | 5 | 10 | 10 | 6 | 10 | **2** | 5 | 10 | 4 | **22.5** |
| **`pyxtrace diff` w/ attribution** | 10 | 10 | 9 | 9 | 10 | 8 | **3** | 9 | 10 | 8 | **19.0** |
| Performance budgets in config | 8 | 7 | 7 | 7 | 9 | 9 | 3 | 9 | 7 | 7 | 15.7 |
| pytest plugin (`--pyxtrace`) | 9 | 8 | 8 | 10 | 10 | 8 | 4 | 9 | 9 | 7 | 13.8 |
| **N+1 / call-pattern detection** | 10 | 10 | 8 | 9 | 9 | 7 | **4** | 8 | 10 | 9 | 13.5 |
| GitHub Action + PR comment | 9 | 9 | 8 | 10 | 9 | 7 | 5 | 9 | 10 | 8 | 10.8 |
| Flame graph output | 7 | 2 | 6 | 6 | 5 | 6 | 6 | 5 | 7 | 4 | 5.8 |
| Async/thread support | 7 | 5 | 6 | 6 | 7 | 4 | 8 | 8 | 6 | 5 | 5.0 |
| Live streaming dashboard | 4 | 2 | 3 | 4 | 3 | 5 | 8 | 2 | 5 | 3 | 2.3 |
| Multi-language support | 5 | 6 | 3 | 3 | 4 | 2 | 10 | 4 | 6 | 6 | 1.9 |

### The 5 Features To Build First

1. **Cache the path filter** — 2,287x → 14x. One dict. Nothing else matters until this lands.
2. **Record per-function timing + call counts** — makes PyxTrace answer a question for the
   first time; replaces the 3-integer summary with a ranked hot-function table.
3. **Compact run format + `pyxtrace diff`** — the killer feature and the entire wedge.
4. **Delete `syscalls/`, `memory.py`, `kernelspy.py`; move Streamlit/Plotly to an extra** —
   removes ~40% of code, ~50 transitive deps, and every false README claim.
5. **Honest README + one realistic example with a real N+1 to find** — the 5-minute wow.

---

## 16. Features NOT to Build (yet)

1. **Multi-language support** — needs a per-language backend; the shared JSONL format is
   worth nearly nothing on its own. The most reliable way to ship nothing.
2. **Live streaming dashboard** — the current one is a fake, and VizTracer wins the real one.
3. **AI/LLM bottleneck explanation** — no trustworthy data to reason over yet. Deterministic
   count arithmetic gives better answers today, with confidence you can defend.
4. **Production/remote profiling** — wrong primitive; py-spy owns it.
5. **Continuous profiling** — needs a backend, a server, and a company.
6. **VS Code extension** — zero users to serve it to.
7. **Sampling mode** — becoming a worse py-spy.
8. **Syscall tracing** — needs root, Linux-only, and was never wired up. Cut it.
9. **Flame graphs** — solved five times over; not the wedge.
10. **Async/thread/multiprocess tracing** — real, hard, and premature. State plainly in the
    README that it is single-threaded-only rather than shipping a subtly wrong answer.

---

## 17. Technical Architecture Recommendations

### Can build on the current architecture

- The `FilteredTracer` + `sys.settrace` core — needs the cache and an accumulator, not a rewrite.
- The `TraceSession` orchestrator — keep the shape, drop the dashboard branch.
- JSONL for raw event dumps — keep as a debug/export format.

### Requires redesign

- **The event model.** Today: one record per event, two records per accepted event, all to
  disk. Should be: **accumulate in memory** into
  `{(file, func): {calls, ops, cumtime, own_time, callees: Counter}}` and serialize *once* at
  exit. This alone removes the 38 MB problem and most of the JSON overhead — and it is what
  makes `diff` cheap.
- **Storage format.** A run artifact should be a small, sorted, stable JSON summary keyed by
  qualified function name — designed to be diffable and committable as a baseline. Not a
  343,878-line event log.
- **Memory capture.** Per-event `tracemalloc.get_traced_memory()` costs 8x and yields a
  process-wide scalar attributed to nothing. Replace with allocation *counts* per function
  (deterministic) or drop memory from v1 entirely.
- **CLI.** Collapse the two parsers into Typer only; point `[project.scripts]` at `cli:main`.

### Probably not worth building

The plugin/exporter architecture, the language abstraction layer, remote collection, and
`SyscallTracerBase` — an abstract base class with one live implementation and three stubs, a
textbook speculative abstraction.

---

## 18. 30-Day Plan (~5–10 hrs/week ≈ 30 hrs total)

**Week 1 — Make it fast and honest (8 hrs)**
- Cache the path filter by `co_filename`. Verify against the benchmark harness.
- Delete `syscalls/`, `memory.py`, `kernelspy.py`. Move `streamlit`/`plotly` to a `[dash]` extra.
- Make `return_value` capture opt-in (`--capture-returns`), off by default.
- Remove module-level `tracemalloc.start()`.
- Fix `requires-python` to `>=3.10`; drop false classifiers.
- Fix PyPI metadata: real email, keywords, `[project.urls]`.
- ✅ *Success: overhead < 50x; `pip install pyxtrace` under 5 MB.*

**Week 2 — Make it answer a question (8 hrs)**
- Accumulate per-function `{calls, ops, cumtime, own_time, callees}` in memory.
- Replace the 3-integer summary with a ranked Rich table of hot functions.
- Serialize to a compact `.pyxt` run file.
- ✅ *Success: `pyxtrace run app.py` names the top 10 hot functions.*

**Week 3 — Ship the wedge (8 hrs)**
- `pyxtrace diff before.pyxt after.pyxt` with per-function delta + attribution.
- Callee call-count deltas (the N+1 signal).
- Exit code 1 on threshold breach.
- ✅ *Success: `diff` catches a deliberately introduced N+1 in the example app.*

**Week 4 — Make it findable (6 hrs)**
- Rewrite README around the wedge; delete every claim that isn't true.
- Build `examples/orders.py` — a realistic service with a plantable N+1.
- Record a new GIF of the `diff` catching it. Store it outside the repo or via Git LFS.
- Add repo description + topics; fix or remove the CI badge; add a real `ci.yml`.
- Write and post: *"Why your CI performance tests are lying to you"* — lead with the measured
  3.1% vs 0.0% variance numbers. Post to r/Python, Hacker News, Lobsters.
- ✅ *Success: 50+ GitHub stars, 500+ downloads, ≥3 user-filed issues.*

---

## 19. 90-Day Roadmap

### V1 — Adoption (Days 1–30)

Per §18. **Why it matters:** without the overhead fix and a real answer, nothing else is worth
doing. **Dependencies:** none. **Complexity:** low. **Metric:** 100 real users; 10 who run it
twice.

### V1.5 — Retention (Days 31–60)

- `pytest-pyxtrace` plugin — `pytest --pyxtrace` writes a run file per benchmark test.
- Baseline files committed to the repo; `pyxtrace check` compares HEAD against baseline.
- Budgets declared in `pyproject.toml`.

**Why it matters:** turns a one-off tool into a recurring one. A tool in CI is used weekly
without a decision; a CLI is used only when remembered. **Dependencies:** `diff` (Week 3).
**Complexity:** medium. **Metric:** 20 repos with a committed baseline file.

### V2 — Production / Trust (Days 61–90)

- GitHub Action + PR comment with the regression table.
- Documented overhead numbers and a published benchmark suite.
- Async/thread behavior documented honestly; guard against silent wrong answers.
- A real test suite; 3.10–3.14 CI matrix.

**Why it matters:** teams adopt what they can audit. **Dependencies:** V1.5.
**Complexity:** medium. **Metric:** 10 public repos with the Action in `main`.

### V3 — Platform (Year 1+)

Hosted history and trend graphs (the CodSpeed model, but OSS-core); allocation-count
regressions; an LLM layer *on top of* deterministic evidence — only once the evidence is
trustworthy. **Complexity:** high; requires a backend. **Metric:** first paying team, if the
commercial path is ever taken.

---

## 20. 1-Year Vision

PyxTrace is the default open-source answer to "how do we stop performance regressions from
merging in Python" — the tool a maintainer adds in one workflow file, with no account and no
Valgrind. Realistic ceiling at this budget: 50–200K downloads/month, 2–4K stars. That is a
successful OSS tool. Displacing py-spy is not on the table and should not be attempted.

---

## 21. Growth Strategy

- **First 10 users:** post the *measurement*, not the tool. "CI wall-clock has 17% variance;
  operation counts have 0%" is a claim people argue about, which is how it spreads. r/Python,
  Lobsters, Python Discord.
- **First 100:** open PRs adding a PyxTrace baseline to 5–10 mid-size Python libraries. Do the
  work for them. Each merged PR is a permanent referral.
- **First 1,000:** the N+1-caught-in-review blog post with real numbers. The Django and
  FastAPI communities feel this pain hardest. Submit a talk to PyCon or a local meetup.
- **First 10,000:** GitHub Action in the Marketplace; integration guides for Django, FastAPI,
  SQLAlchemy; get listed alongside pytest-benchmark in "Python performance tooling" roundups.

**GitHub fixes:** add a repo description and topics (`profiling`, `performance`,
`regression-testing`, `ci`); enable Discussions; add issue templates; fix the broken CI badge.

**PyPI fixes (Week 1):** keywords `profiling, performance, regression, ci, benchmark,
tracing`; `[project.urls]` for Homepage/Repository/Issues; a summary that states the use case,
not the mechanism. Current metadata has `keywords: None`, `project_urls: None`, and a
placeholder author email — all three suppress discoverability and trust.

**Content plan** (in priority order):
1. "Why your CI performance tests are lying to you" — the variance data. This is the wedge post.
2. "How we caught an N+1 query in code review" — the killer-feature story.
3. "cProfile vs PyxTrace: different questions, not better answers" — honest positioning.
4. "Finding a 2-second bottleneck in 5 minutes" — only once the tool can actually do it.

---

## 22. Success Metrics

| Metric | 30d | 90d | 1yr |
|---|---|---|---|
| PyPI downloads / month | 500 | 5,000 | 50,000 |
| GitHub stars | 50 | 400 | 2,500 |
| **Repos with a committed baseline file** | 3 | 30 | 500 |
| **Repos with the Action in `main`** | 0 | 10 | 200 |
| User-filed issues | 3 | 25 | 150 |
| Outside contributors | 0 | 3 | 15 |
| Install → first successful `diff` | 20% | 40% | 55% |
| Repeat usage (2+ runs in 7 days) | 15% | 30% | 45% |

Rows 3 and 4 are the ones that matter. Downloads and stars are vanity; a committed baseline
file is a user who has *integrated* the tool.

### The Aha Moment

> **A developer runs `pyxtrace diff` and sees a regression they did not already know about,
> attributed to a specific function.**

Measurable proxy: a `diff` invocation that returns non-zero *and* is followed within 24 hours
by a second `diff` on the same project — they fixed it and re-checked. That pair of events is
the strongest available signal that the tool delivered value. Instrument it with explicit
opt-in telemetry only, or infer it from GitHub Action logs on public repos.

---

## 23. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **CodSpeed goes free / open-source** | High | Compete on local-first + no-account + no-Valgrind. Move fast |
| Operation counts don't correlate with wall-clock | High | Partly true — an algorithmic change can cut ops but slow things down. Report *both*; gate on ops, display time. Be honest about this in the docs |
| 31x overhead still too slow for real test suites | Medium | Trace only marked benchmark functions, not the whole suite |
| pytest-benchmark adds a stable-counter mode | Medium | Ship first; own the narrative |
| Solo maintainer at 5–10 hrs/week | High | Scope is already cut to one wedge. Resist every "while we're at it" |
| Nobody actually wants a CI perf gate | Medium | 2.45M/mo for pytest-codspeed suggests otherwise, but validate in Week 4 before building V1.5 |

---

## 24. Brutal Critique

**1. If PyxTrace disappeared tomorrow, who would miss it?**
Nobody. 1 star, 17 downloads/month (consistent with mirrors and bots, not humans), zero
user-filed issues across four releases. This is not pessimism; it is the number.

**2. Why isn't it already widely used?**
Because it does not solve a problem. It logs that code ran. Developers do not have an "I wish
I could see my byte-code events on a chart" problem. They have a "why is this slow / what made
it slower" problem, which PyxTrace does not address at all.

**3. What is currently unnecessary?**
`syscalls/` (190 LOC, dead), `memory.py` (dead), `kernelspy.py` (dead, undeclared dependency),
the Streamlit dashboard, the `full`/`perf`/`demo` modes (8% apart — they buy nothing), the
duplicate argparse parser in `__main__.py`, and `SyscallTracerBase`.

**4. What is currently confusing?**
A "live real-time dashboard" that replays a finished file. A speed slider that controls
playback, not profiling. "byte-ops" as a unit of anything.

**5. What looks impressive but has little real value?**
The Streamlit dashboard. It is the most-promoted asset — the 5 MB GIF, top billing in the
README — and it conveys no actionable information. A cumulative event count that rises
monotonically tells you nothing about your program. It is a progress bar with extra steps.

**6. What assumption might be wrong?**
That "visual = valuable." Performance work is overwhelmingly done in a terminal. pyinstrument
won on *text* output. The project's core identity — "interactive visual tracer" — may itself
be the mistake.

**7. What competitor could kill this idea?**
CodSpeed, by open-sourcing or free-tiering their runner. They have the model, the funding, and
2.45M monthly downloads. This is the real threat, and it argues for speed over polish.

**8. What should you stop doing?**
Stop building visualizations. Stop adding tracing dimensions (syscalls, memory, byte-code)
before any single one of them produces an insight. Stop letting the README describe a product
that doesn't exist — that gap costs more trust than having no README at all.

**9. What should you double down on?**
Determinism. It is the one measured property where PyxTrace beats every sampling profiler in
existence, and the project currently does not know it has it.

**10. If you had only 30 days?**
Exactly §18. In one sentence: make it fast, make it answer one question, make that question be
"did this PR make it slower," and tell the truth about everything else.

---

## 25. Final Recommendation

Stop building an execution visualizer. That market is occupied by better-funded tools, and the
visualizer is the weakest thing PyxTrace makes.

Build the smallest possible tool that catches a performance regression in a pull request,
using the one property PyxTrace already has and its competitors structurally cannot: perfect
run-to-run determinism.

---

# THE PLAN

## The single most important strategic direction

> **PyxTrace should become the open-source, local-first regression gate that catches Python
> performance regressions in CI using deterministic operation counts — because wall-clock
> benchmarks are too noisy to gate on (measured: 3.1% run-to-run variance on an idle
> machine, worse on shared CI) and operation counts are not (measured: exactly 0.0%).
> Sampling profilers cannot follow it there.**

**Positioning line:**

> *"PyxTrace catches Python performance regressions in CI — deterministically, so your
> benchmark gate stops crying wolf."*

| | |
|---|---|
| **Target user** | Maintainers of Python libraries and backend services who already run CI and have been burned by a performance regression reaching production |
| **Core problem** | Wall-clock benchmarks are too noisy to gate a PR on, so teams either don't gate or ignore the alarm |
| **Core workflow** | Developer → opens PR → PyxTrace diffs against baseline → attributed regression → fix → re-run proves it |
| **Killer feature** | `pyxtrace diff` catching an N+1 query before it merges |
| **Differentiator** | Determinism. Sampling profilers are structurally incapable of it |

## The 5 highest-priority things to build, in order

1. **Cache the path filter by `co_filename`** *(1 hr)* — 2,287x → 14x, verified. Every other
   item is blocked on this; a 25,700x tool cannot run anything worth measuring.
2. **Accumulate per-function timing, call counts, and callee counts in memory** *(6 hrs)* —
   the first time PyxTrace answers a question. Also eliminates the 38 MB log.
3. **`pyxtrace diff` with per-function attribution and a non-zero exit code** *(8 hrs)* — the
   killer feature, the wedge, and the only reason to choose PyxTrace over anything else.
4. **Delete `syscalls/`, `memory.py`, `kernelspy.py`; Streamlit/Plotly → optional extra; make
   `return_value` opt-in** *(4 hrs)* — removes ~40% of the code, ~50 transitive dependencies,
   every false README claim, and the PII-to-disk liability.
5. **Honest README + a realistic example containing a real N+1 to catch** *(6 hrs)* — the
   5-minute wow. Without it, the first three items go undiscovered.

Everything else in this document waits.
