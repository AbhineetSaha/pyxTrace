"""
pyxtrace.replay  –  stream a recorded JSONL trace in real-time.

Example
-------
$ pyxtrace replay pyxtrace-20250516-052117.jsonl --fps 20
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterator

import typer
from pyxtrace.visual import serve_dashboard


app = typer.Typer(add_completion=False, help="Replay a .jsonl trace in real time")


def _iter_events(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8") as fp:
        for raw in fp:
            # skip partially-written or invalid rows
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                continue
            yield raw


@app.command()
def run(
    trace: Path = typer.Argument(..., readable=True, exists=True, resolve_path=True),
    fps: float = typer.Option(
        30.0, "--fps", help="Frames / events per second (≈ delay between rows)"
    ),
    loop: bool = typer.Option(
        False, "--loop", help="Replay forever until Ctrl-C"
    ),
    port: int = typer.Option(
        8050, "--port", help="Dashboard port (default 8050)"
    ),
):
    """
    Stream TRACE.jsonl to a *temporary* file at the chosen FPS and launch the
    live Dash dashboard to visualize it.
    """
    delay = 1.0 / max(fps, 1e-3)

    # make a temp copy so we don't overwrite the original
    tmp_dir = Path(tempfile.mkdtemp(prefix="pyxtrace-replay-"))
    stream_path = tmp_dir / trace.name
    stream_path.touch()

    print(f"[replay] ▶ {trace}  →  {stream_path}  ({fps:.1f} fps)")

    # launch dashboard in a background process
    import multiprocessing as mp

    dash_proc = mp.Process(
        target=serve_dashboard,
        args=(str(stream_path),),
        kwargs=dict(port=port),
        daemon=True,
    )
    dash_proc.start()
    time.sleep(0.4)  # give Flask a head-start

    try:
        while True:
            with stream_path.open("a", encoding="utf-8") as fp_dst:
                for raw in _iter_events(trace):
                    fp_dst.write(raw)
                    fp_dst.flush()
                    time.sleep(delay)
            if not loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[replay] stopping dashboard …")
        dash_proc.terminate()
        dash_proc.join()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("[replay] done.")


if __name__ == "__main__":
    app()
