"""
visual.py – Rich CLI summary + Streamlit dashboard
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import List

from rich.console import Console


# ───────────────────────────── CLI summary ───────────────────────────
class TraceVisualizer:
    """Static Rich table  +  live Streamlit UI"""

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

    # ────────────────────────── Streamlit UI ──────────────────────────
    def dash(self, *, host: str = "127.0.0.1", port: int = 8050) -> None:
        """Launch Streamlit dashboard."""
        from streamlit.web import bootstrap

        opts = {
            "server.headless": True,
            "server.address": host,
            "server.port": port,
        }
        bootstrap.run(__file__, False, [str(self.path)], opts)


# ------------------------ Streamlit dashboard ------------------------ #
def _streamlit_main(path: str) -> None:
    import plotly.graph_objects as go
    import streamlit as st

    trace_path = Path(path)
    st.set_page_config(page_title=trace_path.name, layout="wide")

    if "cursor" not in st.session_state:
        st.session_state.cursor = 0
        st.session_state.running = False
        st.session_state.hx = []
        st.session_state.hy = []
        st.session_state.bcx = []
        st.session_state.bcy = []
        st.session_state.scx = []
        st.session_state.scy = []
        st.session_state.speed = 100

    st.title(trace_path.name)

    col_start, col_restart, col_slider = st.columns([1, 1, 6])
    if col_start.button("▶ Start"):
        st.session_state.running = True
    if col_restart.button("↻ Restart"):
        st.session_state.cursor = 0
        st.session_state.running = False
        st.session_state.hx.clear()
        st.session_state.hy.clear()
        st.session_state.bcx.clear()
        st.session_state.bcy.clear()
        st.session_state.scx.clear()
        st.session_state.scy.clear()
    st.session_state.speed = col_slider.slider(
        "Speed", 10, 500, st.session_state.speed, step=10
    )

    info_box = st.empty()
    col_heap, col_evt = st.columns(2)
    heap_box = col_heap.empty()
    evt_box = col_evt.empty()

    if st.session_state.running:
        added = 0
        with trace_path.open() as fp:
            fp.seek(st.session_state.cursor)
            while added < st.session_state.speed and (row := fp.readline()):
                added += 1
                try:
                    ev = json.loads(row)
                except json.JSONDecodeError:
                    break
                ts = ev.get("ts")
                kind = ev.get("kind")
                if kind == "MemoryEvent":
                    st.session_state.hx.append(ts)
                    heap = ev["payload"]["current_kb"]
                    st.session_state.hy.append(heap)
                elif kind == "BytecodeEvent":
                    st.session_state.bcx.append(ts)
                    st.session_state.bcy.append(len(st.session_state.bcy) + 1)
                elif kind == "SyscallEvent":
                    count = ev["payload"].get("count", 1)
                    prev = st.session_state.scy[-1] if st.session_state.scy else 0
                    st.session_state.scx.append(ts)
                    st.session_state.scy.append(prev + count)
            st.session_state.cursor = fp.tell()

        heap_fig = go.Figure(
            data=[
                go.Scatter(x=st.session_state.hx, y=st.session_state.hy, mode="lines", name="heap (kB)")
            ],
            layout=go.Layout(title_text="Heap usage (kB)", margin=dict(t=40)),
        )
        evt_fig = go.Figure(
            data=[
                go.Scatter(x=st.session_state.bcx, y=st.session_state.bcy, mode="lines", name="byte-code evts"),
                go.Scatter(x=st.session_state.scx, y=st.session_state.scy, mode="lines", name="syscalls"),
            ],
            layout=go.Layout(title_text="Cumulative events", margin=dict(t=40)),
        )

        heap_box.plotly_chart(heap_fig, use_container_width=True)
        evt_box.plotly_chart(evt_fig, use_container_width=True)
        heap = st.session_state.hy[-1] if st.session_state.hy else 0
        bc = len(st.session_state.bcy)
        sc = st.session_state.scy[-1] if st.session_state.scy else 0
        info_box.write(f"heap {heap/1024:.2f} MB | byte-code {bc} | syscalls {sc}")

        time.sleep(1.0)
        st.experimental_rerun()
    else:
        if st.session_state.hy or st.session_state.bcy:
            heap_fig = go.Figure(
                data=[
                    go.Scatter(x=st.session_state.hx, y=st.session_state.hy, mode="lines", name="heap (kB)")
                ],
                layout=go.Layout(title_text="Heap usage (kB)", margin=dict(t=40)),
            )
            evt_fig = go.Figure(
                data=[
                    go.Scatter(x=st.session_state.bcx, y=st.session_state.bcy, mode="lines", name="byte-code evts"),
                    go.Scatter(x=st.session_state.scx, y=st.session_state.scy, mode="lines", name="syscalls"),
                ],
                layout=go.Layout(title_text="Cumulative events", margin=dict(t=40)),
            )
            heap_box.plotly_chart(heap_fig, use_container_width=True)
            evt_box.plotly_chart(evt_fig, use_container_width=True)
        info_box.write("press ▶ Start")


# ───────────────────────── helpers ──────────────────────────────
def serve_dashboard(path: str | Path, *, host: str = "127.0.0.1", port: int = 8050):
    TraceVisualizer(path, live=True).dash(host=host, port=port)


def launch_dashboard(path: str | Path):
    TraceVisualizer.from_jsonl(path).render()


__all__ = ["TraceVisualizer", "serve_dashboard", "launch_dashboard"]


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) > 1:
        _streamlit_main(sys.argv[1])
    else:
        Console().print("Usage: streamlit run -m pyxtrace.visual <trace.jsonl>")

