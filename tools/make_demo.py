#!/usr/bin/env python3
"""
Build the throwaway git repo the README demo is recorded in, and print its path.

    vhs tools/demo.tape        # with pyxtrace and pytest on PATH; writes docs/demo.gif

The repo holds examples/orders.py as shop/orders.py and an ordinary pytest
test. main has the batched customer lookup; the checked-out branch,
customer-lookup, drops it, so `pyxtrace diff main HEAD` catches the N+1.
tools/demo.tape types those commands into a real shell, so every line in the
GIF is what pyxtrace prints today.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "orders.py"

TEST = '''\
from shop.orders import Cursor, handle_request


def test_handle_request():
    assert len(handle_request(Cursor(), batched=True)) == 120
'''

BATCHED = '''\
    customers = None
    if batched:
        customers = fetch_customers(cur, sorted({o["customer_id"] for o in orders}))
'''

# fixed identities and dates make the commit shas the same on every run; the
# user's own git config (signing, aliases, default branch) is kept out
ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "demo",
    "GIT_AUTHOR_EMAIL": "demo@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T12:00:00Z",
    "GIT_COMMITTER_NAME": "demo",
    "GIT_COMMITTER_EMAIL": "demo@example.com",
    "GIT_COMMITTER_DATE": "2026-01-01T12:00:00Z",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=ENV, check=True, capture_output=True)


def main() -> int:
    repo = Path(tempfile.mkdtemp(prefix="pyxtrace-demo-"))
    (repo / "shop").mkdir()
    (repo / "shop" / "__init__.py").touch()
    (repo / "shop" / "orders.py").write_text(EXAMPLE.read_text())
    (repo / "tests").mkdir()
    (repo / "tests" / "test_orders.py").write_text(TEST)
    (repo / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "orders service")

    _git(repo, "switch", "-qc", "customer-lookup")
    orders = repo / "shop" / "orders.py"
    src = orders.read_text()
    assert BATCHED in src, "examples/orders.py changed; update BATCHED to match"
    orders.write_text(src.replace(BATCHED, "    customers = None\n"))
    _git(repo, "commit", "-qam", "look up each order's customer as we go")

    print(repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
