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
- **Test counts no longer move between identical runs.** Each test now
  starts from `random.seed(0)` and runs with garbage collection held off. On
  jinja and rich, 6 of 1,859 tests used to change counts between two runs of
  the same commit (by up to 18%, enough to fail the gate); across three runs
  each, none do now. Collecting before every test as well was measured too,
  and dropped: it made jinja's traced suite 5x slower.
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
