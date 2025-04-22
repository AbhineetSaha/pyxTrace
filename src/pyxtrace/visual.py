# """
# visual.py – dead‑simple text/UI renderer + optional Dash dashboard.
# """

# from __future__ import annotations

# import json
# import os
# from pathlib import Path
# from typing import List

# from rich.console import Console
# from rich.panel import Panel
# from rich.progress import Progress


# class TraceVisualizer:
#     def __init__(self, path: str | Path, live: bool = False):
#         self.path   = pathlib.Path(path)
#         self.events = []           # filled lazily in live‑mode
#         self.live   = live
#         if not live:                              # ← original (static) behaviour
#             with self.path.open() as fp:
#                 self.events = [json.loads(r) for r in fp]

#     @classmethod
#     def from_jsonl(cls, path: str | Path) -> "TraceVisualizer":
#         return cls(path)

#     # ------------------------------------------------------------------ #
#     def render(self) -> None:
#         console = Console()
#         console.rule("[bold blue]pyxtrace summary")
#         syscall_cnt = sum(1 for e in self.events if e["kind"] == "syscall")
#         bc_cnt = sum(1 for e in self.events if e["kind"] == "BytecodeEvent")
#         mem_samples = sum(1 for e in self.events if e["kind"] == "MemoryEvent")

#         console.print(
#             f"[green]syscalls[/]: {syscall_cnt}   "
#             f"[cyan]byte‑ops[/]: {bc_cnt}   "
#             f"[magenta]mem samples[/]: {mem_samples}"
#         )
#         console.rule()

#     # ------------------------------------------------------------------ #
#     def dash(self) -> None:
#         from dash import Dash, dcc, html
#         from dash.dependencies import Input, Output, State
#         import pathlib, json, itertools, time
#         import plotly.graph_objects as go

#         _CURSOR   = itertools.count()           # used to remember file position
#         _MEM_LINE = go.Scatter(x=[], y=[], mode="lines", name="heap (kB)")
#         _SYS_BARS = go.Bar     (x=[], y=[], name="syscalls")

#         app = Dash(__name__)
#         app.layout = html.Div(
#             [
#                 dcc.Graph(id="mem-usage",
#                           figure=go.Figure(data=[_MEM_LINE])),
#                 dcc.Graph(id="syscall-hist",
#                           figure=go.Figure(data=[_SYS_BARS])),

#                 # live commentary
#                 html.Pre(id="insight-box",
#                          style={"background":"#111", "color":"#0f0",
#                                 "padding":"0.5em", "font-size":"14px"}),

#                 dcc.Interval(id="pulse", interval=1_000, n_intervals=0)
#             ]
#         )

#         # ── callbacks ────────────────────────────────────────────────

#         @app.callback(
#             Output("mem-usage",   "figure"),
#             Output("syscall-hist","figure"),
#             Output("insight-box", "children"),
#             Input ("pulse",       "n_intervals"),
#             State ("mem-usage",   "figure"),
#             State ("syscall-hist","figure"),
#             prevent_initial_call=False,
#         )
#         def _tick(t, mem_fig, sys_fig):
#             """
#             Every second: read any new JSONL rows *after* the cursor,
#             extend the Plotly traces and produce a short textual blurb.
#             """
#             path  = self.path
#             cur   = next(_CURSOR)          # advance cursor ⇒ remember old value
#             # reopen each time → works even if file is still being written
#             new   = []
#             with path.open() as fp:
#                 fp.seek(cur)
#                 for row in fp:
#                     try:
#                         new.append(json.loads(row))
#                     except json.JSONDecodeError:
#                         break               # line still being written – try later
#                 _CURSOR.__setstate__(fp.tell())   # save current offset

#             # accumulate values -------------------------------------------------
#             for ev in new:
#                 if ev["kind"] == "MemoryEvent":
#                     _MEM_LINE["x"].append(ev["ts"])
#                     _MEM_LINE["y"].append(ev["payload"]["current_kb"])
#                 elif ev["kind"] == "SyscallEvent":
#                     name = ev["payload"]["name"]
#                     count = ev["payload"]["count"]
#                     if name in _SYS_BARS["x"]:
#                         i = _SYS_BARS["x"].index(name)
#                         _SYS_BARS["y"][i] = count
#                     else:
#                         _SYS_BARS["x"].append(name)
#                         _SYS_BARS["y"].append(count)

#             # insight text ------------------------------------------------------
#             txt  = (f"⏱ {t}s  |  heap {(_MEM_LINE['y'][-1]/1024):.1f} MB"
#                     if _MEM_LINE["y"] else "waiting for data…")
#             return (
#                 go.Figure(data=[_MEM_LINE]),
#                 go.Figure(data=[_SYS_BARS]),
#                 txt,
#             )

#         @app.callback(Output("mem", "figure"), Input("timer", "n_intervals"))
#         def update(_):
#             ts, mem = [], []
#             # always read *our* file, ignore old root‑owned ones
#             with self.path.open() as fp:
#                 for line in fp:
#                     rec = json.loads(line)
#                     if rec.get("kind") == "MemoryEvent":
#                         ts.append(rec["ts"])
#                         mem.append(rec["payload"]["current_kb"])
#             return go.Figure(data=[go.Scatter(x=ts, y=mem, mode="lines")])

#         app.run(debug=False, host="127.0.0.1", port=8050)

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
            with self.path.open() as fp:
                self.events = [json.loads(line) for line in fp]

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
            with self.path.open() as fp:
                fp.seek(cursor_pos[0])            # jump to previous EOF
                for raw in fp:
                    try:
                        new_rows.append(json.loads(raw))
                    except json.JSONDecodeError:  # line incomplete
                        break
                cursor_pos[0] = fp.tell()         # remember new EOF

            # reuse existing trace data that the browser sent us back
            mem_x = mem_fig["data"][0]["x"]
            mem_y = mem_fig["data"][0]["y"]
            sys_x = sys_fig["data"][0]["x"]
            sys_y = sys_fig["data"][0]["y"]
            sys_idx = {name: i for i, name in enumerate(sys_x)}

            for ev in new_rows:
                kind = ev.get("kind")
                if kind == "MemoryEvent":
                    mem_x.append(ev["ts"])
                    mem_y.append(ev["payload"]["current_kb"])
                elif kind == "SyscallEvent":
                    name  = ev["payload"]["name"]
                    count = ev["payload"]["count"]
                    if name in sys_idx:
                        sys_y[sys_idx[name]] = count
                    else:
                        sys_idx[name] = len(sys_x)
                        sys_x.append(name)
                        sys_y.append(count)

            insight = (
                f"⏱ {tick}s  |  heap {(mem_y[-1]/1024):.1f} MB"
                if mem_y else
                "waiting for data…"
            )

            # Return refreshed figures (Dash diffs & redraws)
            return (
                go.Figure(data=[go.Scatter(x=mem_x, y=mem_y, mode="lines",
                                           name="heap (kB)")]),
                go.Figure(data=[go.Bar(x=sys_x, y=sys_y,
                                       name="syscalls")]),
                insight,
            )

        # ––– run the server –––
        app.run(host=host, port=port, debug=False)