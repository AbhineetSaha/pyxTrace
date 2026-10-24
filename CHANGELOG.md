# Changelog

## Unreleased

- **A branch is compared from where it diverged.** With no recorded runs,
  `pyxtrace diff main HEAD` now starts from the merge-base, as a pull request
  does. Before, commits that landed on `main` after the branch was cut were
  counted as part of the change, reversed: a speed-up on `main` read as this
  branch regressing. `--no-merge-base` keeps the old behaviour.
- `diff` accepts git ranges: `main...HEAD` (from the merge-base) and
  `main..HEAD` (from `main` itself), for recorded runs too.
- **Run file format 3.** Runs from earlier versions are refused with a message
  saying to record them again; if CI caches `.pyxtrace/`, change the cache key.
  Two reasons:
  - **The default root is the git checkout**, not the script's directory. A
    script in `benchmarks/` used to trace only itself, so the gate passed
    whatever the package did. Names are now relative to the checkout
    (`examples/orders.py::main`). Virtualenvs and `site-packages` inside the
    root are skipped, whatever they are called.
  - **Python 3.10 gets qualified names.** It has none on code objects, so two
    classes' `__init__` shared one entry; the names are now read from the
    source.
- **Faster test-suite diffs on Python 3.12+.** Tests are counted with
  `sys.monitoring` (PEP 669): code outside the root is switched off on its first
  event instead of being filtered on every call. Each side of a diff took
  14.2 s → 10.8 s on jinja's suite and 12.5 s → 9.8 s on rich's. Lines,
  calls and callers match settrace exactly on the bundled example and on
  loops, comprehensions, exceptions, recursion and decorators, except that a
  generator resuming is not a new call. Run files record the tracer (`tracer`)
  and runs from different tracers are refused. Python 3.10 and 3.11, and
  free-threaded builds for now, keep settrace. `PYXTRACE_TRACER=settrace`
  forces it.
- **Fewer test counts move between identical runs.** Each test now starts
  from `random.seed(0)` and runs with garbage collection held off. On jinja,
  5 of 911 tests drew from unseeded `random` and moved by up to 18% between
  two runs of the same commit, enough to fail the gate; across three runs,
  none move now. On rich, 1 to 3 of 948 tests still move by under 0.1%, under
  either tracer: their work follows the clock (timestamps in log lines) or a
  refresh timer. Collecting before every test as well was measured too, and
  dropped: it made jinja's traced suite 5x slower.
- **Each test file runs in its own pytest process, several at once.** A test's
  counts depended on which tests ran before it: caches they warmed, modules
  they imported. On rich, 298 of 947 tests counted differently when their file
  ran alone (one by 47x). A file's counts now depend only on that file, which
  is what lets results be cached per file, and the files run in parallel:
  each side of a diff took 6 s → 2.7 s on jinja and 6 s → 4.2 s on rich
  (8 cores). `-j 1` runs one file at a time, for tests that cannot run side
  by side.
- **`--stability-runs N`** runs the head's test files N times and leaves out
  any test whose counts differ between runs, naming it, so a test driven by
  the clock or a timer thread cannot fail the gate. Three diffs of rich's
  unchanged `main` against itself with `--stability-runs 2` all passed, each
  leaving out 2 to 4 such tests (`test_live_empty` every time). The CI recipe
  in the README now uses it.
- **The base side is counted once per test file and kept.** Results go to
  `.pyxtrace/tests/`, keyed by a hash of the base commit, the test file, every
  conftest and test helper, the Python version, the tracer, the installed
  packages and pyxTrace itself. A second diff against the same base runs only
  the head (rich: 10.5 s → 6.0 s), and editing one test file counts only that
  file again. A kept entry is byte-identical to a fresh count. The README's CI
  recipe caches the directory.
- **Only the test files a change can reach run on the head.** Each test's
  base results already list the functions it ran, so a file runs on the head
  only when one of its tests ran a changed function on the base, or failed
  there. A change outside any function (an import, a module constant, a class
  attribute), a method added to or removed from a class, a change to a
  non-Python file, or a deleted module runs every file and says why; prose, `.github/` and a version bump in `pyproject.toml` do not.
  `--all-tests` runs every file. The report says how many ran. Files left out
  count on the head what they counted on the base, so a function's growth is
  judged against the whole suite either way.
- **`--max-test-calls N`** (default 5,000,000): a test that makes more calls
  stops being counted and runs on untraced, and is named in the report rather
  than compared. On sqlglot one stress test makes 70M calls; with the limit,
  its test file traces in 41 s instead of 227 s (17 s untraced). The
  largest test on jinja makes 324,000 calls, on rich 55,000.
- **`--file-timeout SECONDS`** (default 600): a test file still running after
  that long is stopped and named, and its tests are not compared. A pull
  request that fixes an infinite loop and adds a test for it used to hang
  the diff for good, because the head's test runs on the base's unfixed code
  (found on rich's `7338cb9`). A file that timed out is not kept in
  `.pyxtrace/tests/`, since how long it takes depends on the machine.
- **Nothing is left out silently.** Tests that fail on the head are named.
  Changed functions the gate could not compare are split into "no test runs
  it" (existing or new) and "run only by tests new in this change, so there is
  no baseline yet", under a line saying how many changed functions were
  compared. Opt in to failing on them with `--require-covered` and
  `--fail-on-test-failure`.
- Comparing against the working tree includes new files not yet added to git.
- A ref that is not a commit here suggests `origin/<ref>` when that exists,
  and says when the checkout is a shallow clone missing the history.
- **`pyxtrace diff main HEAD` works with no recorded runs.** It runs the pytest
  suite on both commits in temporary git worktrees, compares the tests that
  passed on both, marks each regression with the changed functions it traces
  back to, and lists changed functions no test ran.
- Contributor guide, code of conduct, security policy, and issue and pull
  request templates.
- CI and publishing hardened: actions pinned to commit hashes, read-only
  tokens by default, no credentials left on disk after checkout, one release
  at a time, and a zizmor check that keeps it that way. CodeQL scanning runs
  through GitHub's default setup, on the Python code and the workflows.

## 3.0.0

A hardening release: every change below fixes a way the gate could give a wrong
or missing answer. Each has a regression test in `tests/test_hardening.py`.

### Breaking

- **Run file format 2.** Functions are keyed by qualified name, so
  `orders.py::execute` is now `orders.py::Cursor.execute`. Runs recorded by
  2.x are refused with a message saying to record them again. If CI caches
  `.pyxtrace/`, change the cache key.

### Counts that were not exact

- **Hash randomisation changed counts between runs.** Set and dict iteration
  order over strings differed per process, so the same code ran a different
  number of lines. The `pyxtrace` command now runs scripts with
  `PYTHONHASHSEED=0`; the Python API warns when it cannot.
- **Threads the script did not join were cut off mid-run**, giving different
  counts every run. Runs now finish after Python's own thread shutdown, exactly
  as `python app.py` waits; daemon threads still running are reported.
- **Counters were shared between threads.** On free-threaded Python this lost
  about 40% of counts under load. Each thread now has its own counters.
- **Same-named functions merged.** Two classes' `__init__`, or every list
  comprehension in a file, shared one entry (3.11+; 3.10 has no qualified names).
- **A file reached through a symlink** could overwrite its own entry.

### Wrong answers from `diff`

- `--threshold 0` reported every unchanged function as a regression.
- `--threshold nan` switched the gate off: a +300% regression passed. A
  negative, `nan` or `inf` setting is now an error.
- Growth of exactly the threshold failed the gate, though the help, the docs
  and the PASS line all say "more than". It now passes.
- Runs from different Python versions are compared with a warning: line counts
  shift between minor versions (3.12 inlined comprehensions).
- A run with uncommitted changes overwrote the clean run of the commit beneath
  it. It is now saved as `<sha>-dirty.pyxt`, and `pyxtrace diff BASE` compares
  against the current checkout.

### Crashes and silent failures

- A script ending in `sys.exit(0)` recorded nothing and exited 0.
- A failing script now records nothing and says so; `run` exits with its status.
- A corrupt, truncated, or hand-edited run file raised a traceback deep inside
  `diff`; it is now refused with a reason (exit 2). Saves are atomic.
- A directory named like a git ref (`main/`) crashed `diff`.
- `pyxtrace app.py | head` lost the run to a broken pipe.
- Output a Windows console could not encode crashed the command.
- A path with square brackets (`[legacy]/mod.py`) was read as styling: the
  report named a file that does not exist, and `[/]` crashed it.
- A bad `-o` path is rejected before the script runs, not after the workload.
- A relative `-o` path landed wherever the script had `chdir`ed to.
- Embedding: the caller's tracer (coverage, a debugger) and `__main__` are
  restored after a run.
- git failures such as CI's "dubious ownership" are reported instead of
  silently filing runs as `untracked`.

### Added

- `--version`, and options before the script (`pyxtrace -o x.pyxt app.py`).
- Each run file records the Python version it was made with.

### Project

- CI: ruff and mypy, Windows and macOS, Python 3.14 and free-threaded 3.14t, a
  check that the built wheel installs and catches a regression, and a gate that
  requires `diff` to exit exactly 1 (it used to accept a usage error).
- Publishing runs the tests first, and a PyPI outage stops the job instead of
  reading as "this version is not published yet".
- The tracer is as fast as 2.1.0's despite the per-thread counters.
- License metadata moved to the SPDX form (setuptools stops accepting the old
  one in 2027). Removed: the unused `poetry.lock`, a stale internal handoff
  note, and a CI check for dashboard dependencies dropped in 2.0.
