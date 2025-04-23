<div align="center">

<!-- Title block -->
<img src="https://raw.githubusercontent.com/AbhineetSaha/pyxtrace/main/docs/logo.svg" alt="PyXTrace logo" width="340"/>

<h1>PyXTrace<br/>
<sub><em>🩺 Your program under the microscope – in real&nbsp;time</em></sub>
</h1>

<!-- Shields.io badges -->
<p>
  <a href="https://pypi.org/project/pyxtrace/"><img src="https://img.shields.io/pypi/v/pyxtrace?style=for-the-badge&logo=python" alt="PyPI"></a>
  <a href="https://github.com/AbhineetSaha/pyxtrace/blob/main/LICENSE"><img src="https://img.shields.io/github/license/AbhineetSaha/pyxtrace?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/AbhineetSaha/pyxtrace/actions"><img src="https://img.shields.io/github/actions/workflow/status/AbhineetSaha/pyxtrace/ci.yml?style=for-the-badge&label=CI" alt="CI"></a>
  <a href="https://pepy.tech/project/pyxtrace"><img src="https://img.shields.io/pypi/dm/pyxtrace?style=for-the-badge" alt="Downloads"></a>
</p>

<sup>Byte‑code • Memory • (optional) Sys‑call tracing &nbsp;|&nbsp; Rich CLI + Live Dash dashboard</sup>

<br/><br/>
<a href="#-quick-start"><img src="https://raw.githubusercontent.com/AbhineetSaha/pyxtrace/main/docs/demo.gif" alt="demo" width="760"></a>
</div>

---

## 🗺️ Table of Contents
- [✨ Features](#-features)
- [🚀 Installation](#-installation)
- [🕹️ Quick start](#-quick-start)
- [📂 Project layout](#-project-layout)
- [🛣️ Road‑map](#️-road-map)
- [👩‍💻 Contributing](#-contributing)
- [⚖️ License](#️-license)
- [🙏 Acknowledgements](#-acknowledgements)

---

## ✨ Features

| 🔍 What you see          | 💡 Under the hood                  | 📊 Visualised as               |
|--------------------------|-----------------------------------|--------------------------------|
| **Byte‑code timeline**   | `sys.settrace` → JSON Lines       | (upcoming) call‑stack heat‑map |
| **Memory utilisation**   | polling `tracemalloc`             | Live heap‑usage line chart     |
| **Sys‑calls ( Linux)**    | `strace - c - p …`                  | Histogram (top hitters)        |
| **Smart commentary**     | heuristic summary per second      | Green console‑style panel      |

*Works out‑of‑the‑box on macOS & Linux.  
Windows → byte‑code + memory tracing (no `strace`).*

---

## 🚀 Installation

```bash
# stable
pip install pyxtrace

# nightly / pre‑releases
pip install --pre pyxtrace

# extras
pip install "pyxtrace[dashboard]"   # Dash + Plotly
pip install "pyxtrace[dev]"         # black, ruff, mypy, …
Note – On Linux you’ll also need strace if you want syscall tracing
(sudo apt install strace, yay -S strace, …).

**Linux users**: `strace` requires root permissions to trace system calls. You have two options:
1. Run with sudo: `sudo pyxtrace trace examples/fibonacci.py`
2. Run without syscall tracing: `pyxtrace trace --no-syscalls examples/fibonacci.py`

## 🕹️ Quick Start

```bash
# Basic usage
pyxtrace trace examples/fibonacci.py           # Rich table only
pyxtrace trace examples/fibonacci.py --dash    # + live dashboard

# Pass arguments to your script
pyxtrace trace train.py --dash -- --epochs 10 --lr 3e-4
```

## 📂 Project Layout

```
src/pyxtrace/
├─ cli.py          ← Typer CLI entry‑points
├─ core.py         ← orchestration (spawns child, tails JSONL, …)
├─ bytecode.py     ← BytecodeTracer  ➜ writes events
├─ memory.py       ← MemoryTracer    ➜ writes events
├─ syscalls        ← SyscallTracer   ➜ queues live events (Linux)
   ├─ darwin.py
   ├─ linux.py
   ├─ windows.py
├─ visual.py       ← Rich table + Dash dashboard
└─ …
```

*Note: Everything under `dist/`, `*.egg-info`, `__pycache__`, `coverage`, build artifacts is ignored via `.gitignore`.*

## 🛣️ Roadmap

- 🔥 Flame‑graph view (Chrome‑style)
- ⏱ CPU sample profiler (perf integration)
- 🌎 Remote dashboard via websockets
- 🧩 VS Code extension

## 👩‍💻 Contributing

```bash
# Setup development environment
git clone https://github.com/AbhineetSaha/pyxtrace.git
cd pyxtrace && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
pre-commit install        # auto-format on commit

# Development workflow
1. Fork & branch – one PR per feature/bug-fix
2. Run tests: pytest -q && ruff check . && mypy src/
3. Submit a pull-request ❤️
```

## ⚖️ License

Released under the MIT License – see LICENSE.

## 🙏 Acknowledgements

| Tool / Lib | Why it's awesome |
|------------|------------------|
| Rich & Typer | Beautiful CLIs with zero boiler‑plate |
| Dash / Plotly | Reactive dashboards in pure Python |
| tracemalloc | The unsung hero of the stdlib |
| strace | Decades‑old but still magical |

And you – for trying, starring ⭐ and contributing!
