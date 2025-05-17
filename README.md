Below is a cleaned-up **README.md** that **omits every reference to a “Restart” button** (or any other control you’ve removed).
Copy-paste it into the project root and commit.

````markdown
<div align="center">

<h1>PyXTrace<br/>
<sub><em>🩺 Your Python program under the microscope – in real&nbsp;time</em></sub>
</h1>

<p>
  <a href="https://pypi.org/project/pyxtrace/"><img alt="PyPI" src="https://img.shields.io/pypi/v/pyxtrace?style=for-the-badge&logo=python"></a>
  <a href="https://github.com/AbhineetSaha/pyxtrace/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/AbhineetSaha/pyxtrace?style=for-the-badge"></a>
  <a href="https://github.com/AbhineetSaha/pyxtrace/actions"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/AbhineetSaha/pyxtrace/ci.yml?style=for-the-badge&label=CI"></a>
  <br>
  <a href="https://pepy.tech/projects/pyxtrace"><img alt="Downloads" src="https://img.shields.io/pypi/dm/pyxtrace?style=for-the-badge"></a>
</p>

<sup>Byte-code • Memory • (optional) Sys-call tracing &nbsp;|&nbsp; Rich CLI + live Dash dashboard</sup>

<br/>
<a href="#-quick-start"><img src="./Demo.gif" alt="Demo animation" width="760"></a>
</div>

---

## 🗺️ Table&nbsp;of&nbsp;Contents
- [✨ Features](#-features)
- [🚀 Installation](#-installation)
- [🕹️ Quick start](#-quick-start)
- [📂 Project layout](#-project-layout)
- [🛣️ Road-map](#️-road-map)
- [👩‍💻 Contributing](#-contributing)
- [⚖️ License](#️-license)
- [🙏 Acknowledgements](#-acknowledgements)

---

## ✨ Features

| 🔍 What you see           | 💡 Captured via            | 📈 Visualised as (Dash) |
|---------------------------|---------------------------|-------------------------|
| **Byte-code timeline**    | `sys.settrace`            | Cumulative line graph   |
| **Heap usage (kB)**       | `tracemalloc` snapshots   | Live line graph         |
| **Sys-calls (Linux)**     | `strace -c -p …`          | Cumulative line graph   |
| **Smart commentary**      | 1 s heuristics            | Green console panel     |

*macOS & Linux fully supported.  
Windows → byte-code + memory tracing (no `strace`).*

---

## 🚀 Installation

```bash
# stable
pip install pyxtrace

# pre-release / nightly
pip install --pre pyxtrace
````

### Optional extras

```bash
pip install "pyxtrace[dashboard]"   # Dash + Plotly live UI
pip install "pyxtrace[dev]"         # black, ruff, mypy, pytest, …
```

> **Linux users** – syscall tracing needs `strace` *and* root:
>
> ```bash
> sudo apt install strace
> sudo pyxtrace --dash your_script.py
> ```
>
> or skip sys-calls with `--no-syscalls`.

---

## 🕹️ Quick start

```bash
# Rich CLI summary only
pyxtrace examples/fibonacci.py

# Live dashboard (opens http://127.0.0.1:8050)
pyxtrace --dash examples/fibonacci.py

# Pass cmd-line args to your script
pyxtrace --dash train.py -- --epochs 10 --lr 3e-4

# Performance presets
pyxtrace --mode demo  script.py   # call/return only   (fastest)
pyxtrace --mode perf  script.py   # skip std-lib lines
pyxtrace --mode full  script.py   # trace everything   (slowest)
```

When the dashboard opens, press **▶ Start** to begin streaming events in
real-time. A speed slider lets you throttle or accelerate the playback
(10 – 500 events / s).

---

## 📂 Project layout

```
src/pyxtrace/
├─ cli.py           ← Typer CLI entry-point
├─ core.py          ← orchestration + JSONL replay
├─ bytecode.py      ← byte-code tracer
├─ memory.py        ← heap sampler (tracemalloc)
├─ syscalls/        ← syscall tracer (Linux / Darwin stubs)
├─ visual.py        ← Rich summary + Dash dashboard
└─ …
```

---

## 🛣️ Road-map

| Status | Item                                   |
| :----: | -------------------------------------- |
|   🔄   | Flame-graph view (Chrome-style)        |
|   🔄   | CPU sample profiler (`perf` hook)      |
|   🔄   | Remote dashboard via websockets        |
|   🔄   | VS Code extension                      |
|   🕓   | **Streamlit dashboard (next release)** |

---

## 👩‍💻 Contributing

```bash
git clone https://github.com/AbhineetSaha/pyxtrace.git
cd pyxtrace
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
pre-commit install
```

1. Create a feature branch from **main**
2. Run `pytest -q && ruff check . && mypy src/`
3. Open a pull-request ❤️

---

## ⚖️ License

Released under the **MIT License** – see [`LICENSE`](LICENSE).

---

## 🙏 Acknowledgements

| Project / Lib     | Why it’s awesome                             |
| ----------------- | -------------------------------------------- |
| **Rich & Typer**  | Beautiful CLIs with zero boiler-plate        |
| **Dash / Plotly** | Interactive dashboards in pure Python        |
| **tracemalloc**   | Built-in heap sampler, criminally underrated |
| **strace**        | Decades-old yet still magical                |

…and **you** – for trying, starring ⭐ and contributing!

