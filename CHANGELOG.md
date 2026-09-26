# Changelog

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
- A negative `--threshold` or `--min-ops` was accepted; it is now an error.
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
- Publishing runs the tests first.
- License metadata moved to the SPDX form (setuptools stops accepting the old
  one in 2027). The unused `poetry.lock` is gone.
