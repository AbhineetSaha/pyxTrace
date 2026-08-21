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
            f"{s['own_time'] * 1e3:.1f}",
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

        if f["callees"]:
            c.print("   [dim]Attributed to:[/]")
            for row in f["callees"][:5]:
                c.print(
                    f"     {row['name']}   {row['before']:,} → {row['after']:,} calls"
                    f"   [dim]{_fmt_pct(row['pct'])}[/]"
                )

        npo = f["n_plus_one"]
        if npo:
            caller = f["name"].split("::")[-1]
            callee = npo["callee"].split("::")[-1]
            c.print(
                f"   [yellow]Pattern detected: N+1[/] — {caller}() runs "
                f"{npo['parent_calls']:,}x and calls {callee}() "
                f"[bold]{npo['per_call_after']:.0f}x each[/]"
            )
            c.print(
                f"     [dim]{npo['total_after']:,} total calls to {callee}(), "
                f"was {npo['total_before']:,}. Batch it outside the loop.[/]"
            )

    c.print(
        f"\n[bold red]✗ FAIL[/] — {len(findings)} function(s) grew by more than "
        f"{threshold:g}%"
    )


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
