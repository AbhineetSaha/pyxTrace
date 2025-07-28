#!/usr/bin/env python3
"""Run tests and linters for developers."""
import shlex
import subprocess
import sys

steps = [
    "pytest -q",
    "ruff check src/pyxtrace/visual.py",
    "mypy src/",
]

for cmd in steps:
    print(f"\n--> {cmd}")
    if subprocess.call(shlex.split(cmd)) != 0:
        sys.exit(1)

print("\nAll checks passed!")
