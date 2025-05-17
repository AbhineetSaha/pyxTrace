"""
visual.py – Rich CLI summary + slick dark Dash dashboard
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import List

from rich.console import Console

_DASH_RUNNING = False


# ───────────────────────────── CLI summary ───────────────────────────
class TraceVisualizer:
    """Static Rich table  +  live Dash UI"""

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
        sc = sum(1 for e in self.events if e.get("kind") == "SyscallEvent")
        bc = sum(1 for e in self.events if e.get("kind") == "BytecodeEvent")
        mc = sum(1 for e in self.events if e.get("kind") == "MemoryEvent")
        c.print(f"[green]syscalls   [/]: {sc}")
        c.print(f"[cyan]byte-ops   [/]: {bc}")
        c.print(f"[magenta]mem samples[/]: {mc}")
        c.rule()

    # ─────────────────────────── Dash UI ────────────────────────────
    def dash(self, *, host="127.0.0.1", port=8050) -> None:
        global _DASH_RUNNING
        if _DASH_RUNNING:
            return
        _DASH_RUNNING = True

        dash   = importlib.import_module("dash")
        dcc    = importlib.import_module("dash.dcc")
        html   = importlib.import_module("dash.html")
        deps   = importlib.import_module("dash.dependencies")
        go     = importlib.import_module("plotly.graph_objects")

        # Try Bootstrap – fall back to plain CSS
        try:
            dbc = importlib.import_module("dash_bootstrap_components")
            EXT = [dbc.themes.CYBORG]
        except ModuleNotFoundError:
            dbc = None
            EXT = ["https://cdn.jsdelivr.net/npm/bootswatch@5.3.2/dist/cyborg/bootstrap.min.css"]

        Dash = dash.Dash
        Input, Output, State = deps.Input, deps.Output, deps.State
        NO = dash.no_update

        # empty initial figs
        heap0 = go.Figure(
            data=[go.Scatter(x=[], y=[], mode="lines", name="heap (kB)")],
            layout=go.Layout(title_text="Heap usage (kB)", margin=dict(t=40)),
        )
        evt0 = go.Figure(
            data=[go.Scatter(x=[], y=[], mode="lines", name="byte-code evts"),
                  go.Scatter(x=[], y=[], mode="lines", name="syscalls")],
            layout=go.Layout(title_text="Cumulative events", margin=dict(t=40)),
        )

        cursor = [0]       # file offset
        LINES  = [10]     # rows per tick – mutable via slider

        app = Dash(__name__, external_stylesheets=EXT,
                   suppress_callback_exceptions=True)

        # ---------- Layout -------------------------------------------------
        def card_body(text):
            return dbc.CardBody(text, className="p-2") if dbc else html.Div(text)

        header = (
            dbc.Navbar(dbc.Container(dbc.NavbarBrand(self.path.name, className="fs-5")))
            if dbc else
            html.Div(self.path.name, style={"fontWeight": "600",
                                            "padding": "8px 14px"})
        )
        controls = (
            dbc.Row(
                [
                    dbc.Col(dbc.Button("▶ Start", id="start",
                                       color="success", className="me-2")),
                    dbc.Col(dbc.Button("▶ Restart", id="restart",
                                       color="success", className="me-2")),
                    dbc.Col(
                        dcc.Slider(id="speed", min=10, max=500, step=10,
                                   value=100, tooltip={"placement": "bottom"},
                                   marks=None)
                    ),
                ],
                className="gx-2 gy-1 flex-nowrap"
            )
            if dbc else
            html.Div(
                [
                    html.Button("▶ Start", id="start",
                                style={"padding": "6px 18px",
                                       "marginRight": "10px"}),
                    dcc.Slider(id="speed", min=10, max=500, step=10,
                               value=100, tooltip={"placement": "bottom"})
                ],
                style={"display": "flex", "alignItems": "center",
                       "gap": "8px", "padding": "6px 14px"}
            )
        )
        stats_box = html.Pre(id="info",
                             style={"background": "#111", "color": "#0f0",
                                    "padding": "6px", "fontSize": "14px",
                                    "minHeight": "38px"})

        graph_row = (
            dbc.Row(
                [
                    dbc.Col(dbc.Card(dbc.CardBody(dcc.Graph(id="heap", figure=heap0)),
                                     className="mb-2"), lg=6),
                    dbc.Col(dbc.Card(dbc.CardBody(dcc.Graph(id="evt", figure=evt0)),
                                     className="mb-2"), lg=6),
                ],
                className="gx-2"
            )
            if dbc else
            html.Div(
                [
                    html.Div(dcc.Graph(id="heap", figure=heap0),
                             style={"width": "50%", "padding": "4px"}),
                    html.Div(dcc.Graph(id="evt", figure=evt0),
                             style={"width": "50%", "padding": "4px"}),
                ],
                style={"display": "flex", "flexWrap": "wrap"}
            )
        )

        app.layout = html.Div(
            [
                header,
                controls,
                stats_box,
                graph_row,
                dcc.Store(id="running", data=False),
                dcc.Interval(id="tick", interval=1_000, n_intervals=0,
                             disabled=True),
            ],
            style={"padding": "8px"} if not dbc else {}
        )

        # ---------- Callbacks ----------------------------------------------
        Input, Output, State = deps.Input, deps.Output, deps.State

        # Speed slider
        @app.callback(
            Output("speed", "value", allow_duplicate=True),
            Input("speed", "value"),
            prevent_initial_call=True,
        )
        def _upd_speed(val):
            LINES[0] = val
            return NO

        # ▶ Start
        @app.callback(
            Output("running", "data", allow_duplicate=True),
            Output("tick",    "disabled", allow_duplicate=True),
            Output("tick",    "n_intervals", allow_duplicate=True),
            Input("start",    "n_clicks"),
            State("running",  "data"),
            prevent_initial_call=True,
        )
        def _start(_, running):
            return (NO, NO, NO) if running else (True, False, 0)

        # ─────────── Restart button (rewind + pause) ─────────── #
        @app.callback(
            Output("running", "data", allow_duplicate=True),
            Output("tick",    "disabled", allow_duplicate=True),
            Output("tick",    "n_intervals", allow_duplicate=True),   # ← add this
            Input("restart",  "n_clicks"),
            prevent_initial_call=True,
        )
        def _restart(_):
            cursor[0] = 0  # rewind JSONL to the beginning

            # Clean the graphs by resetting their data
            # This will be picked up by the next _update call
            heap0 = go.Figure(
            data=[go.Scatter(x=[], y=[], mode="lines", name="heap (kB)")],
            layout=go.Layout(title_text="Heap usage (kB)", margin=dict(t=40)),
            )
            evt0 = go.Figure(
            data=[go.Scatter(x=[], y=[], mode="lines", name="byte-code evts"),
                  go.Scatter(x=[], y=[], mode="lines", name="syscalls")],
            layout=go.Layout(title_text="Cumulative events", margin=dict(t=40)),
            )

            # Optionally, you can store these in dcc.Store or reset via callback outputs
            # But here, just return to reset running state and interval
            return False, True, 0 
        
        # Live update
        @app.callback(
            Output("heap", "figure"),
            Output("evt",  "figure"),
            Output("info", "children"),
            Input("tick",  "n_intervals"),
            State("running", "data"),
            State("heap", "figure"),
            State("evt",  "figure"),
        )
        def _update(_, running, hfig, efig):
            if not running:
                return hfig, efig, "press ▶ Start"

            hx, hy = hfig["data"][0]["x"], hfig["data"][0]["y"]
            bcx, bcy = efig["data"][0]["x"], efig["data"][0]["y"]
            scx, scy = efig["data"][1]["x"], efig["data"][1]["y"]
            heap = hy[-1] if hy else 0
            bc   = bcy[-1] if bcy else 0
            sc   = scy[-1] if scy else 0

            added = 0
            with self.path.open() as fp:
                fp.seek(cursor[0])
                while added < LINES[0] and (row := fp.readline()):
                    added += 1
                    try:
                        ev = json.loads(row)
                    except json.JSONDecodeError:
                        break
                    k, ts = ev.get("kind"), ev.get("ts")
                    if k == "MemoryEvent":
                        heap = ev["payload"]["current_kb"]
                        hx.append(ts); hy.append(heap)
                    elif k == "BytecodeEvent":
                        bc += 1
                        bcx.append(ts); bcy.append(bc)
                    elif k == "SyscallEvent":
                        sc += ev["payload"].get("count", 1)
                        scx.append(ts); scy.append(sc)
                cursor[0] = fp.tell()

            heap_fig = go.Figure(
                data=[go.Scatter(x=hx, y=hy, mode="lines", name="heap (kB)")],
                layout=go.Layout(title_text="Heap usage (kB)", margin=dict(t=40)),
            )
            evt_fig = go.Figure(
                data=[
                    go.Scatter(x=bcx, y=bcy, mode="lines", name="byte-code evts"),
                    go.Scatter(x=scx, y=scy, mode="lines", name="syscalls"),
                ],
                layout=go.Layout(title_text="Cumulative events", margin=dict(t=40)),
            )
            info = f"heap {heap/1024:.2f} MB | byte-code {bc} | syscalls {sc}"
            return heap_fig, evt_fig, info

        Console().print(f"[bold green]▶ dashboard: http://{host}:{port}[/]")
        app.run(host=host, port=port, debug=False)


# ───────────────────────── helpers ──────────────────────────────
def serve_dashboard(path: str | Path, *, host="127.0.0.1", port=8050):
    TraceVisualizer(path, live=True).dash(host=host, port=port)


def launch_dashboard(path: str | Path):
    TraceVisualizer.from_jsonl(path).render()


__all__ = ["TraceVisualizer", "serve_dashboard", "launch_dashboard"]
