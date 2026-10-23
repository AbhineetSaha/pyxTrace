## What and why

<!-- What was wrong or missing, and what this changes. Link the issue: "Fixes #123". -->

## How I checked it

<!-- The test you added, and the command or output that shows the change works.
     If you touched ProfileTracer.__call__ in bytecode.py, include before/after overhead numbers. -->

## Checklist

- [ ] `ruff check .`, `mypy` and `pytest -q` pass
- [ ] Bug fixes include a test that fails without the fix
- [ ] Run files stay byte-identical across runs (or the change has a test proving it)
- [ ] User-visible changes have a line under **Unreleased** in `CHANGELOG.md`
