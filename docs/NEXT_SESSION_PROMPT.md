# Handoff prompt for the next session

Copy everything below the line into a fresh chat.

---

I'm working on **PyxTrace** at `/home/abhineet/work/pyxTrace` (branch `main`).

## What it is now

PyxTrace v2.0.0 is a **deterministic performance-regression gate for Python CI**. The thesis:
wall-clock benchmarks are too noisy to gate a PR on (3–4% run-to-run spread on an idle
machine, worse on shared CI runners), but *operation counts* — function calls and lines
executed via `sys.settrace` — are byte-for-byte identical across runs. So gate on counts,
display time. Sampling profilers (py-spy, pyinstrument, Scalene) structurally cannot do this.

It was rewritten this week from a very different tool (a byte-code event logger with a
Streamlit dashboard, ~25,700x overhead, two of three advertised features dead code). Full
analysis and rationale: `PYXTRACE_PRODUCT_STRATEGY.md` in the repo — read the
"Implementation status" table at the top first.

Current commands:

```bash
pyxtrace run app.py -o before.pyxt     # profile → run file (`run` is optional)
pyxtrace diff before.pyxt after.pyxt   # exit 1 on regression, detects N+1
python benchmarks/overhead.py          # overhead + determinism gate, fails past 50x
```

State: **43x overhead** (was 25,700x), 8 tests passing, 2 dependencies, everything
**uncommitted** in the working tree.

## The task: validate the premise on real code before building anything else

Everything verified so far runs against `examples/orders.py` — a synthetic file written in
the same session as the detector that finds its regression. That's a closed loop. It proves
the code does what was intended; it proves nothing about whether the idea survives real code.

**Do not build features yet.** Specifically do NOT build the pytest plugin, GitHub Action, or
`pyxtrace check` — they're on the roadmap but they assume the core is worth integrating, and
that's the untested assumption.

Instead: pick a real Python repo (a mid-size OSS project with a benchmark suite, or one I
name), find two commits where something measurably changed in cost, and run the gate across
them. Report honestly what happens, including if it's bad.

### Specific unknowns to answer

1. **Overhead on real code.** 43x was measured on `fib(22)`, which is pathologically
   call-heavy. Real code does more work per call so it should be lower — but that's a
   prediction, not a measurement. Measure it.
2. **Does the root-path filter work on a real package layout?** It only traces files under
   the *entry script's parent directory*. On a real `src/`-layout project this may trace
   almost nothing, which would be a silent failure — the tool reports "no functions traced"
   but a user might not notice the run is empty.
3. **Does N+1 detection fire on real code, and how often is it wrong?** False positives kill
   a CI gate faster than false negatives. The heuristic is in `src/pyxtrace/run.py:_n_plus_one`
   — it flags a function that runs many times, whose own call count didn't grow, but which
   started calling something at least once per invocation.
4. **Is single-threaded-only fatal?** `sys.settrace` is per-thread; threads, asyncio, and
   subprocesses are invisible. Most Python backends worth gating are threaded or async. This
   is the risk most likely to kill the whole direction — probe it.
5. **Do counts actually correlate with cost on real code?** An algorithmic change can reduce
   operations and still be slower.

### Three acceptable outcomes

- **It catches a real regression** → that becomes the launch post (draft exists at
  `docs/why-ci-perf-tests-lie.md`, currently built on the synthetic example — replace with
  real data).
- **It runs but finds nothing useful** → the premise is weaker than assumed. Say so plainly;
  that's worth learning now rather than after building three integrations.
- **It falls over** → fix a real bug instead of a hypothetical one.

## Ground rules

- Be blunt. If the validation says the direction is wrong, say that — do not soften it. The
  previous session's most useful output was finding that a headline metric ("16.8% wall-clock
  variance") was an artifact of measuring a 3-microsecond workload, and correcting it down to
  3.1% before it shipped in a blog post about measurement honesty.
- Measure, don't estimate. Every number in the strategy doc is measured; keep it that way.
- Don't commit or publish to PyPI without asking. 2.0.0 is a breaking release (`--dash` and
  `replay` were removed) and PyPI is a one-way door.

## Known loose ends (not the task, just don't be surprised)

- `Demo.gif` (5 MB) is unreferenced and shows a deleted feature — pending a decision to remove.
- GitHub repo description and topics are still unset (`gh` CLI is not installed on this machine).
- `pyproject.toml` has a dead `[tool.setuptools.dynamic] version` block; static version wins.
- No author email in package metadata — deliberately left for me to decide.
- Install is 16 MB, not the 5 MB the plan targeted; `pygments` (via `rich`) is 9.9 MB of it.
