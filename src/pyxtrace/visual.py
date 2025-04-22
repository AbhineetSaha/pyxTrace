"""
visual.py – text summary with Rich + optional live Dash dashboard
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from rich.console import Console

# --------------------------------------------------------------------------- #
# Internal flag:  makes sure we start _one_ Dash server per interpreter
# --------------------------------------------------------------------------- #
_DASH_RUNNING = False          # will be set to True inside TraceVisualizer.dash


# ─────────────────────────── TraceVisualizer ─────────────────────────────── #
class TraceVisualizer:
    """
    * static mode – read the finished JSONL file and print a Rich summary
    * live   mode – run a Dash server, read the JSONL incrementally and
                    update two graphs + a textual commentary every second
    """

    # ------------------------------------------------------------------ #
    def __init__(self, path: str | Path, *, live: bool = False):
        self.path: Path = Path(path)
        self.live: bool = live
        self.events: List[dict] = []

        if not live:                               # ← static / classic mode
            # Windows can leave half‑written JSONL rows → be tolerant
            with self.path.open(encoding="utf‑8") as fp:
                for raw in fp:
                    try:
                        self.events.append(json.loads(raw))
                    except json.JSONDecodeError:
                        # skip garbage / partially‑written line
                        continue

    # ------------------------------------------------------------------ #
    @classmethod
    def from_jsonl(cls, path: str | Path) -> "TraceVisualizer":
        """Factory method used by core.py (non‑live summary)"""
        return cls(path, live=False)

    # ------------------------------------------------------------------ #
    # 1) Rich summary (printed once the trace finished) ----------------- #
    def render(self) -> None:
        console = Console()
        console.rule("[bold blue]pyxtrace summary")

        syscall_cnt = sum(1 for e in self.events if e["kind"] == "SyscallEvent")
        bc_cnt      = sum(1 for e in self.events if e["kind"] == "BytecodeEvent")
        mem_cnt     = sum(1 for e in self.events if e["kind"] == "MemoryEvent")

        console.print(
            f"[green]syscalls   [/]: {syscall_cnt}\n"
            f"[cyan]byte‑ops   [/]: {bc_cnt}\n"
            f"[magenta]mem samples[/]: {mem_cnt}"
        )
        console.rule()

    # ------------------------------------------------------------------ #
    # 2) Live Dash dashboard ------------------------------------------- #
    def dash(self, *, host: str = "127.0.0.1", port: int = 8050) -> None:
        """
        Start a Dash server immediately.  Every second the `_tick` callback:

            • seeks to the position where we stopped reading last time,
            • parses any newly appended JSONL rows,
            • updates the memory‑usage line plot,
            • updates the syscall histogram,
            • emits a short textual “insight” string.
        """
        global _DASH_RUNNING
        if _DASH_RUNNING:                         # already serving → bail out
            return
        _DASH_RUNNING = True

        # ––– import Dash / Plotly lazily so users who never `--dash`
        #     don’t need those hefty dependencies installed
        from dash import Dash, dcc, html
        from dash.dependencies import Input, Output, State
        import plotly.graph_objects as go

        # Plotly trace templates ---------------------------------------
        mem_trace = go.Scatter(x=[], y=[], mode="lines",  name="heap (kB)")
        sys_trace = go.Bar    (x=[], y=[], name="syscalls")

        # mutable cursor (keeps the last read file offset)
        cursor_pos = [0]

        # ––– build layout --------------------------------------------
        app = Dash(__name__)
        app.layout = html.Div(
            [
                dcc.Graph(id="mem-usage",
                          figure=go.Figure(data=[mem_trace])),
                dcc.Graph(id="syscall-hist",
                          figure=go.Figure(data=[sys_trace])),

                html.Pre(id="insight-box",
                         style={"background": "#111",
                                "color":      "#0f0",
                                "padding":    "0.5em",
                                "font-size":  "14px"}),

                dcc.Interval(id="pulse", interval=1_000, n_intervals=0),
            ]
        )

        # ––– single callback – no other outputs, no “mem.figure” left –
        @app.callback(
            Output("mem-usage",    "figure"),
            Output("syscall-hist", "figure"),
            Output("insight-box",  "children"),
            Input ("pulse",        "n_intervals"),
            State ("mem-usage",    "figure"),
            State ("syscall-hist", "figure"),
            prevent_initial_call=False,
        )
        def _tick(tick, mem_fig, sys_fig):
            """Read new events and extend the figures in‑place."""
            new_rows: list[dict] = []

            with self.path.open(encoding="utf‑8") as fp:
                fp.seek(cursor_pos[0])                 # jump to previous EOF
                while True:
                    raw = fp.readline()
                    if not raw:                        # no more bytes yet
                        break
                    try:
                        new_rows.append(json.loads(raw))
                    except json.JSONDecodeError:       # half‑written line
                        break                          # try again next tick
                cursor_pos[0] = fp.tell()              # remember new EOF

            # ── reuse existing trace data ─────────────────────────────
            mem_x = mem_fig["data"][0]["x"]
            mem_y = mem_fig["data"][0]["y"]
            sys_x = sys_fig["data"][0]["x"]
            sys_y = sys_fig["data"][0]["y"]
            sys_idx = {name: i for i, name in enumerate(sys_x)}

            for ev in new_rows:
                if ev.get("kind") == "MemoryEvent":
                    mem_x.append(ev["ts"])
                    mem_y.append(ev["payload"]["current_kb"])
                elif ev.get("kind") == "SyscallEvent":
                    name  = ev["payload"]["name"]
                    count = ev["payload"]["count"]
                    if name in sys_idx:
                        sys_y[sys_idx[name]] = count
                    else:
                        sys_idx[name] = len(sys_x)
                        sys_x.append(name)
                        sys_y.append(count)

            insight = (
                f"⏱ {tick}s  |  heap {(mem_y[-1]/1024):.1f} MB" if mem_y
                else "waiting for data…"
            )

            return (
                go.Figure(data=[go.Scatter(x=mem_x, y=mem_y, mode="lines",
                                           name="heap (kB)")]),
                go.Figure(data=[go.Bar(x=sys_x, y=sys_y, name="syscalls")]),
                insight,
            )

        # ––– run the server –––
        app.run(host=host, port=port, debug=False)