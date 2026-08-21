# Why your CI performance tests are lying to you

*Draft — not published. See the checklist at the bottom before posting.*

---

Run the same unchanged Python function seven times and time it:

```
2.06ms  2.06ms  2.20ms  2.04ms  2.06ms  2.05ms  2.17ms
```

That is a 3.1% spread on code that did not change — on an idle laptop with
nothing else running. Nothing was edited between those runs. Same machine, same
interpreter, same input.

Now count the operations that function performed on those same runs:

```
3196  3196  3196  3196  3196  3196  3196
```

Zero spread. Not "small." Zero.

Two caveats before I build anything on that, because the first number is the one
people will argue with and they will be right to:

**3.1% is the best case.** That was a quiet machine with no neighbours. A shared
CI runner is worse. I have not measured how much worse on your provider, and I
am not going to quote a figure I did not measure.

**Small workloads look far worse than they are.** The same function at
`fib(10)` — about 3 microseconds — swings 16.8%, but that is mostly timer
granularity, not real variance. If you benchmark something that tiny, your
numbers are noise regardless of tooling.

So the honest version of the claim is narrower than the dramatic one: wall-clock
gives you a few percent of irreducible wobble in the best case and more in
realistic CI, while operation counts give you exactly zero, everywhere. That gap
is small in absolute terms and decisive in practice, because it is the
difference between a threshold you can set at 5% and one you cannot.

## The threshold trap

Say you add a benchmark job to CI and fail the build when things get 10% slower.
With a few percent of baseline noise — more on a busy runner — you get failures
on PRs that changed a docstring. Somebody re-runs the job, it passes, and
everyone learns the check is a liar.

So you widen the threshold to 50%. Now the job is quiet — and useless. A change
that makes an endpoint 40% slower sails through. You have kept the CI minutes
and thrown away the signal.

I have watched teams land on a third option, which is to delete the job and rely
on noticing in production. That is usually the correct call given the tools, and
it is a bad place for the ecosystem to be.

## Where the noise comes from

Wall-clock time on a shared runner measures your code plus: CPU frequency
scaling, a noisy neighbour VM, page cache state, ASLR changing cache line
alignment, GC timing, and whatever the hypervisor felt like doing. Your code is
a minority shareholder in that number.

None of that is fixable from inside your test. You can pin CPUs, take the
minimum of N runs, use statistical tests. These help. They do not get you to a
number stable enough to block a merge on a 5% change, which is roughly the size
of regression that actually matters.

## Count work instead of measuring time

Here is the thing wall-clock obscures: a performance regression in application
code is almost never the same instructions getting slower. It is *more
instructions running*. An N+1 query. A cache that stopped hitting. A helper
called in a loop that used to be called once.

You can count that. Deterministically.

```python
sys.settrace(tracer)   # tracer increments a counter per call and per line
```

Function calls and lines executed are properties of the program's control flow.
Given the same input, they are identical every time — across machines, across
runs, across a noisy CI box that is also compiling someone's Rust.

That is the whole idea. Gate on the count; display the time.

## What this catches that a profiler does not

Profilers answer "why is this slow *right now*." That is a different question
from "did this change make it worse," and the second question is the one code
review needs.

Here is a real shape. An order endpoint batches its customer lookups — one query
for all of them. Someone refactors, and now each order fetches its own customer:

```
⚠  orders.py::process_order  +120 calls to other functions  (same code, more work)
   Attributed to:
     orders.py::fetch_customer   0 → 120 calls   new
   Pattern detected: N+1 — process_order() runs 120x and calls fetch_customer() 1x each
     120 total calls to fetch_customer(), was 0. Batch it outside the loop.

✗ FAIL — exit code 1
```

Two things worth noting.

**It blames `process_order`, not the query function.** The database wrapper also
runs 60x more, and a naive diff would rank it first because its numbers moved
most. But it is working correctly — it is being called more. The regression was
introduced one level up, in the function whose own call count did *not* change
while its callees exploded. That distinction is the difference between a report
that sends you to the right file and one that sends you to optimise something
that is fine.

**There is no confidence score.** "N+1 detected, 87% confident" is a number with
nothing behind it. This is arithmetic over exact counts: `process_order` ran 120
times, it called `fetch_customer` 120 times, it previously called it 0 times.
There is nothing to be uncertain about. When a tool cannot be certain, it should
say nothing rather than manufacture a percentage.

## The honest limitations

Counting operations is a proxy for cost, and proxies lie in specific ways:

- **Fewer operations can be slower.** Swap a Python loop for one call into a
  slow C extension and the count drops while wall-clock rises. Gate on counts to
  catch the common case; keep a real timer for the uncommon one.
- **I/O is invisible.** Waiting 200ms on a network call is zero operations.
  Counting finds the N+1 that *causes* 120 round trips, not the latency of any
  one of them.
- **Deterministic tracing is not free.** The implementation I ended up with runs
  ~43x slower than untraced on a call-heavy microbenchmark — roughly 5x
  `cProfile`'s cost. That is fine for a benchmark script in CI and completely
  wrong for production. Sampling profilers own that job and should keep it.

If those trade-offs disqualify your use case, they disqualify it. I would rather
say so than discover it in your issue tracker.

## Prior art

This is not a new idea outside Python. Rust's [iai](https://github.com/bheisler/iai)
uses instruction counts via Cachegrind for exactly this reason.
[CodSpeed](https://codspeed.io) brings the approach to Python with Valgrind and
does it well — it is commercial and hosted, which is right for a lot of teams
and wrong for anyone who wants the check to run locally with no account.

I wanted the local, no-account version, so I built
[PyxTrace](https://github.com/AbhineetSaha/pyxTrace):

```bash
pip install pyxtrace

pyxtrace benchmarks/workload.py -o before.pyxt
# ... your change ...
pyxtrace benchmarks/workload.py -o after.pyxt
pyxtrace diff before.pyxt after.pyxt        # exit 1 on regression
```

Run files are small sorted JSON, so you commit `baseline.pyxt` and the diff shows
up in code review as plain text.

It is early and I am the only user, which is the main thing wrong with it. If you
try it on a real repo I would genuinely like to know what breaks — particularly
whether the 43x overhead is tolerable on a benchmark you actually care about, and
whether the N+1 detection fires on anything real or just on my example.

---

## Pre-publish checklist

- [ ] Re-run `python benchmarks/overhead.py`; update the 43x, 3.1% and 3196 figures
- [ ] If you can, measure the same spread on your actual CI provider and use that number
- [ ] Confirm the repo has a description and topics set
- [ ] Confirm CI is green on `main` so the badge is not red on arrival
- [ ] Decide the venue order — Lobsters and r/Python first, HN only if those land
- [ ] Be around for the first few hours to answer replies
