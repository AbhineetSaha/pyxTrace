"""
names.py – qualified function names on Python 3.10, which has no ``co_qualname``.

Without it, two classes' ``__init__`` in one file would share an entry, and so
would every list comprehension. The tracer keys a 3.10 code object by its bare
name and first line instead, and the run is labelled here, from the source, with
the name 3.11+ would have given it: ``Cursor.execute``,
``outer.<locals>.<listcomp>``.
"""

from __future__ import annotations

import ast
from typing import Dict, Tuple

from pyxtrace.bytecode import SEP

_COMPREHENSIONS = {
    ast.ListComp: "<listcomp>",
    ast.SetComp: "<setcomp>",
    ast.DictComp: "<dictcomp>",
    ast.GeneratorExp: "<genexpr>",
}

_cache: Dict[str, Dict[Tuple[str, int], str]] = {}


def qualnames(source: str) -> Dict[Tuple[str, int], str]:
    """(bare name, first line) → qualified name, for every scope in *source*.

    The first line is ``co_firstlineno``: a decorated function or class starts
    at its first decorator.
    """
    found: Dict[Tuple[str, int], str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qual = f"{prefix}{node.name}"
            first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            found.setdefault((node.name, first), qual)
            # decorators, defaults, annotations and bases run in the enclosing scope
            outer: list = list(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                outer += [*node.bases, *node.keywords]
                inner = f"{qual}."
            else:
                outer += [node.args, node.returns]
                inner = f"{qual}.<locals>."
            for n in outer:
                if n is not None:
                    visit(n, prefix)
            for stmt in node.body:
                visit(stmt, inner)
            return
        name = "<lambda>" if isinstance(node, ast.Lambda) else _COMPREHENSIONS.get(type(node))
        if name is None:
            for child in ast.iter_child_nodes(node):
                visit(child, prefix)
            return
        qual = f"{prefix}{name}"
        found.setdefault((name, node.lineno), qual)  # type: ignore[attr-defined]
        if isinstance(node, ast.Lambda):
            visit(node.args, prefix)
            visit(node.body, f"{qual}.<locals>.")
            return
        # unlike a function, a comprehension adds no `<locals>` to what it contains
        inner = f"{qual}."
        gens = node.generators  # type: ignore[attr-defined]
        # the first iterable is evaluated outside the comprehension, the rest inside
        visit(gens[0].iter, prefix)
        for i, g in enumerate(gens):
            if i:
                visit(g.iter, inner)
            for part in (g.target, *g.ifs):
                visit(part, inner)
        for part in ("elt", "key", "value"):
            if hasattr(node, part):
                visit(getattr(node, part), inner)

    try:
        visit(ast.parse(source), "")
    except (SyntaxError, ValueError, RecursionError):
        pass
    return found


def resolve(filename: str, key: str) -> str:
    """The qualified name for a 3.10 key ``name<SEP>firstlineno`` in *filename*."""
    name, _, line = key.partition(SEP)
    names = _cache.get(filename)
    if names is None:
        try:
            with open(filename, encoding="utf-8") as f:
                names = qualnames(f.read())
        except (OSError, UnicodeDecodeError):
            names = {}
        _cache[filename] = names
    return names.get((name, int(line or 0)), name)
