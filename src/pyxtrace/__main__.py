"""
python -m pyxtrace  →  behaves like the `pyxtrace` console-script stub.

The only job here is convenience: `pyxtrace app.py` should work without typing
`run`.  Everything else is Typer's — there is deliberately no second argument
parser in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from pyxtrace.cli import app

_COMMANDS = {"run", "diff"}


def _looks_like_script(arg: str) -> bool:
    return arg.endswith(".py") or Path(arg).exists()


def main(argv: List[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `pyxtrace app.py …` → `pyxtrace run app.py …`
    if argv and argv[0] not in _COMMANDS and not argv[0].startswith("-"):
        if _looks_like_script(argv[0]):
            argv.insert(0, "run")

    app(args=argv, prog_name="pyxtrace")


if __name__ == "__main__":  # pragma: no cover
    main()
