#!/usr/bin/env python3
"""
Regenerate docs/demo/index.html, the page the README demo is recorded from:

    python tools/make_demo.py
    python -m http.server 8765 --directory docs/demo &
    npx -y voila-recorder record http://localhost:8765/ --steps tools/demo.steps.yaml
    ffmpeg -i demo.mp4 -vf "fps=8,scale=820:-1,split[a][b];[a]palettegen[p];[b][p]paletteuse" docs/demo.gif

A terminal-styled page of the run → change → diff story, filled with the real
CLI output, so the demo cannot drift out of date the way a hand-recorded
screencast does. tools/demo.steps.yaml is the voila recipe (captions +
narration) that turns the page into docs/demo.mp4.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pyxtrace.core import TraceSession  # noqa: E402
from pyxtrace.run import diff  # noqa: E402
from pyxtrace.visual import render_diff, render_run  # noqa: E402

EXAMPLE = ROOT / "examples" / "orders.py"
PAGE = ROOT / "docs" / "demo" / "index.html"

PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>PyxTrace demo</title>
<style>
  body {{ margin: 0; background: #0f1117; color: #e6e6e6;
         font-family: -apple-system, system-ui, sans-serif; }}
  main {{ max-width: 1040px; margin: 0 auto; padding: 56px 24px 120px; }}
  h1 {{ font-weight: 500; font-size: 30px; margin: 0 0 6px; }}
  .lead {{ color: #9aa3b2; margin: 0 0 48px; font-size: 17px; }}
  section {{ margin: 0 0 64px; }}
  h2 {{ font-size: 15px; font-weight: 500; color: #9aa3b2; margin: 0 0 12px;
        text-transform: uppercase; letter-spacing: .06em; }}
  .term {{ background: #161a23; border: 1px solid #262b38; border-radius: 10px;
           padding: 18px 22px; overflow-x: auto; }}
  .term pre {{ margin: 0; font: 13.5px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
               white-space: pre; }}
</style></head><body><main>
<h1>PyxTrace</h1>
<p class="lead">Catch Python performance regressions in CI with deterministic operation counts.</p>
<section id="run"><h2>1 · Record a run</h2><div class="term">{run}</div></section>
<section id="change"><h2>2 · Make a change, record again</h2><div class="term">{change}</div></section>
<section id="diff"><h2>3 · Compare the two commits</h2><div class="term">{diff}</div></section>
</main></body></html>
"""


def _record(tmp: Path, name: str, n_plus_one: bool) -> dict:
    os.environ["PYXTRACE_NPLUSONE"] = "1" if n_plus_one else "0"
    return TraceSession(EXAMPLE, out=tmp / name).run()


def _term() -> Console:
    """A console that records for HTML export and prints nothing to the terminal."""
    return Console(record=True, width=90, file=io.StringIO(), force_terminal=True)


def _html(c: Console) -> str:
    # MONOKAI: the default export theme renders "red" as #800000, invisible on a dark page
    return c.export_html(theme=MONOKAI, inline_styles=True, code_format="<pre>{code}</pre>")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        before = _record(tmp, "before.pyxt", n_plus_one=False)
        after = _record(tmp, "after.pyxt", n_plus_one=True)
    findings = diff(before, after)

    run = _term()
    run.print("[bold green]$[/] pyxtrace examples/orders.py")
    run.print("[dim]\\[pyxTrace] ➜ profiling 'examples/orders.py' → .pyxtrace/3f2a9c1.pyxt[/]")
    render_run(before, console=run)

    change = _term()
    change.print('[bold green]$[/] git commit -am "look up each order\'s customer as we go"')
    change.print("[dim]\\[main 8b41e7d] look up each order's customer as we go[/]\n")
    change.print("[bold green]$[/] pyxtrace examples/orders.py")
    change.print("[dim]\\[pyxTrace] ➜ profiling 'examples/orders.py' → .pyxtrace/8b41e7d.pyxt[/]")
    render_run(after, console=change)

    d = _term()
    d.print("[bold green]$[/] pyxtrace diff main HEAD")
    render_diff(findings, threshold=10.0, console=d)
    d.print("\n[dim]exit code 1 — the pull request fails[/]")

    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(PAGE_TEMPLATE.format(run=_html(run), change=_html(change), diff=_html(d)))
    print(f"wrote {PAGE.relative_to(ROOT)}")

    if not findings:
        print("ERROR: the example produced no findings — demo would be empty")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
