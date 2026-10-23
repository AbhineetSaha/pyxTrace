# Contributing to pyxtrace

Thanks for considering it. pyxtrace is small enough that one person can
understand the whole codebase in an afternoon. This page is the map.

## Ways to help

- **Try it on a real project and tell us what happened.** This is the most
  valuable contribution right now. Did it catch something? Flag something it
  shouldn't have? Was the overhead bearable? Use the *Field report* issue form.
- **Report a bug.** Include the command you ran and, if you can, the two `.pyxt`
  files. They're small JSON files and usually make the problem obvious.
- **Fix a bug or build a feature.** Look for issues labelled
  [`good first issue`](https://github.com/AbhineetSaha/pyxTrace/labels/good%20first%20issue)
  or [`help wanted`](https://github.com/AbhineetSaha/pyxTrace/labels/help%20wanted).
  For anything bigger than a bug fix, please open an issue first so we can agree
  on the approach before you spend time on it.
- **Improve the docs.** If something in the README confused you, it will confuse
  the next person too.

## Set up

You need Python 3.10 or newer, and `git` on your PATH (the tests create small git
repos).

```bash
git clone https://github.com/AbhineetSaha/pyxTrace.git
cd pyxTrace
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before you open a pull request

Run the same checks CI runs:

```bash
ruff check .                     # lint
mypy                             # types
pytest -q                        # tests, a few seconds
python benchmarks/overhead.py    # overhead budget and determinism check
```

If you change anything under `.github/workflows/`, also run
`pip install zizmor && zizmor --offline .github/workflows/`. CI does, and fails on
unpinned actions, broad token permissions, or script injection. Third-party
actions are pinned to a full commit hash, with the version in a comment;
Dependabot keeps those pins up to date.

CI runs these on Linux for Python 3.10 to 3.14 and free-threaded 3.14t, on
Windows and macOS, and also builds the wheel, installs it into a clean
environment, and checks that it catches a regression. If you're a first-time
contributor, a maintainer has to approve the CI run before it starts. That's a
GitHub default, not a judgment on your PR.

## The rules that matter here

Most of these come from bugs this project has actually shipped and fixed.

1. **The counts must be exact.** pyxtrace is only useful because the same code
   produces byte-identical run files every time. If your change affects what
   gets counted, add a test that records the same script twice and compares the
   bytes. `tests/test_hardening.py` has many examples. Watch out for anything
   whose order isn't fixed: set iteration, thread scheduling, dict order built
   from hashing.

2. **A bug fix comes with a test that fails without it.** Write the test first,
   watch it fail on the current code, then fix the code. Say in the test's
   docstring what used to happen. The tests in `tests/test_hardening.py` follow
   this pattern.

3. **Stay inside the overhead budget.** `benchmarks/overhead.py` fails past 150x
   on a deliberately call-heavy workload. `ProfileTracer.__call__` in
   `bytecode.py` runs on every line of the traced program, so even one extra
   function call there shows up. If you touch it, measure before and after, run
   both versions alternately in the same process (timing noise is large), and
   put the numbers in the PR.

4. **Changing the run file format needs a version bump.** If you change what a
   `.pyxt` file contains, bump `FORMAT_VERSION` in `run.py`, so that older files
   are refused with a clear message instead of being misread. Note it under
   *Breaking* in the changelog.

5. **Numbers in the docs are measured.** Every figure in the README came from a
   run someone can repeat. If you change a claim, include the command and output
   that back it up.

6. **Keep it lean.** There is one runtime dependency (`rich`). Adding another
   needs a strong reason.

## How the code is organised

| File | What it does |
|---|---|
| `src/pyxtrace/bytecode.py` | `ProfileTracer`, the `sys.settrace` hook. Counts calls, lines and caller→callee edges, per thread. The hot path. |
| `src/pyxtrace/core.py` | `TraceSession`: runs a script under the tracer, the way `python script.py` would, then saves the run. |
| `src/pyxtrace/run.py` | The `.pyxt` run file (save, load, validate), git commit lookup, and `diff`, which decides what counts as a regression. |
| `src/pyxtrace/cli.py` | The `pyxtrace` command: argument parsing, the hash-seed relaunch, exit codes. |
| `src/pyxtrace/visual.py` | Terminal output, using `rich`. |
| `tests/` | `test_trace.py` covers core behaviour; `test_hardening.py` has one regression test per bug fixed. |
| `benchmarks/overhead.py` | The overhead budget and determinism check that CI runs. |
| `examples/` | `orders.py` contains the regression the README demonstrates. |

A few things that will save you time in the tests:

- `tests/conftest.py` fixes the hash seed, so calling `cli.main()` in-process
  doesn't relaunch Python. To test the real command end to end, use the `_cli()`
  helper in `test_hardening.py`, which runs `python -m pyxtrace` in a subprocess.
- Tests write small scripts into `tmp_path` and trace them. That's usually the
  quickest way to reproduce a bug.

## Pull requests

- Keep each PR to one change. Two small PRs get reviewed faster than one big one.
- Explain what was wrong and how you checked the fix. For behaviour changes, a
  before/after run of the command helps a lot.
- Add a line under **Unreleased** in `CHANGELOG.md` for anything a user would
  notice.
- Don't bump the version; that happens at release time.

## Releases (maintainers)

Bump the version in `pyproject.toml` and the fallback `__version__` in
`src/pyxtrace/__init__.py` (a test checks they match), move the **Unreleased**
changelog entries under the new version, and merge to `main`. The publish
workflow then waits for a maintainer to approve the upload to PyPI.

## Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind and
assume good intent. For security problems, see [SECURITY.md](SECURITY.md)
instead of opening a public issue.
