"""
visual.py – Rich terminal output: run summary and regression report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from rich.console import Console
from rich.table import Table

from pyxtrace.run import top


# ───────────────────────────── run summary ───────────────────────────
def render_run(run: dict, path: str | Path | None = None, console: Console | None = None) -> None:
    """Ranked hot-function table for a .pyxt run."""
    c = console or Console()
    functions = run.get("functions", {})
    if not functions:
        c.print(
            "[yellow]No functions traced.[/] Only code under the script's own "
            "directory is profiled — check that the script is where you expect."
        )
        return

    tbl = Table(title=f"pyxTrace – {run.get('script', '')}", title_style="bold blue")
    tbl.add_column("function", style="cyan", no_wrap=False)
    tbl.add_column("calls", justify="right", style="green")
    tbl.add_column("lines", justify="right", style="magenta")
    tbl.add_column("own ms", justify="right")

    for name, s in top(run, 10):
        tbl.add_row(
            name,
            f"{s['calls']:,}",
            f"{s['lines']:,}",
            f"{s.get('own_time', 0.0) * 1e3:.1f}",
        )

    c.print(tbl)
    c.print(
        f"[dim]{len(functions)} functions · sorted by lines executed "
        f"(deterministic) · own ms is timing, and varies run to run[/]"
    )
    # The entry script is always traced, so the "no functions" case above never
    # fires for the common real failure: an installed or out-of-tree library
    # sits outside the script's directory and is filtered out silently.
    if {name.split("::", 1)[0] for name in functions} <= {run.get("script", "")}:
        c.print(
            "[yellow]Warning: only the entry script was traced.[/] pyxTrace "
            "profiles code under the script's own directory, so a library "
            "installed in site-packages or living outside that directory "
            "recorded nothing. This run is effectively empty, and a baseline "
            "saved from it will never detect a regression. Pass --root "
            "<package dir> to profile it."
        )
    if path:
        c.print(f"[dim]run saved to {path}[/]")


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    if pct == float("inf"):
        return "new"
    return f"{pct:+,.0f}%"


def render_diff(findings: list, *, threshold: float, console: Console | None = None) -> None:
    """Print the regression report.  Empty findings → a clean PASS."""
    c = console or Console()

    if not findings:
        c.print(f"[green]✓ PASS[/] — no function grew by more than {threshold:g}%")
        return

    for f in findings:
        if f["delta"] > 0:
            head = "new" if f["is_new"] else _fmt_pct(f["pct"])
            detail = f"[red]{head} operations[/]  ([dim]{f['lines_before']:,} → {f['lines_after']:,}[/])"
        else:
            # flagged for its call pattern, not its own operation count
            added = sum(r["after"] - r["before"] for r in f["callees"])
            detail = f"[red]+{added:,} calls to other functions[/]  [dim](same code, more work)[/]"
        c.print(f"\n[bold red]⚠[/]  [bold]{f['name']}[/]  {detail}")

        # State the rate, not a diagnosis. Whether this is an N+1 or simply more
        # work per call cannot be told apart at one input size, so pyxtrace
        # reports the counts and leaves the reading to the author.
        if f.get("delegated") and f["callees"] and f["calls_after"]:
            top = max(f["callees"], key=lambda r: r["after"] - r["before"])
            per_call = (top["after"] - top["before"]) / f["calls_after"]
            c.print(
                f"   [yellow]{f['name'].split('::')[-1]}() runs {f['calls_after']:,}x[/]"
                f" and makes [bold]{per_call:,.0f} more call(s) to "
                f"{top['name'].split('::')[-1]}()[/] each time"
            )

        if f["callees"]:
            c.print("   [dim]Attributed to:[/]")
            for row in f["callees"][:5]:
                c.print(
                    f"     {row['name']}   {row['before']:,} → {row['after']:,} calls"
                    f"   [dim]{_fmt_pct(row['pct'])}[/]"
                )

    c.print(f"\n[bold red]✗ FAIL[/] — {len(findings)} function(s) regressed")


# ───────────────────────────── CLI summary ───────────────────────────
class TraceVisualizer:
    """Summary of a raw JSONL event stream (the --events path)."""

    def __init__(self, path: str | Path, *, live: bool = False):
        self.path = Path(path)
        self.live = live
        self.events: List[dict] = []
        if not live:
            with self.path.open(encoding="utf-8") as fp:
                for raw in fp:
                    try:
                        self.events.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "TraceVisualizer":
        return cls(path, live=False)

    def render(self) -> None:
        c = Console()
        c.rule("[bold blue]pyxTrace summary")
        bc = sum(1 for e in self.events if e.get("kind") == "BytecodeEvent")
        mc = sum(1 for e in self.events if e.get("kind") == "MemoryEvent")
        c.print(f"[cyan]events     [/]: {bc}")
        c.print(f"[magenta]mem samples[/]: {mc}")
        c.rule()


__all__ = ["TraceVisualizer", "render_run", "render_diff"]
