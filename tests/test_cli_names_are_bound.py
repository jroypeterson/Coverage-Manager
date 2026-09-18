"""`cli.py` must not reference a name it never binds.

**The bug this pins.** `cli.py` used `sys.stderr` six times and never imported
`sys`. Every one of those six was inside a *warning or error* path -- the
`s1-watch` "INCONCLUSIVE, not 'no IPO filings this week'" message, the
`form10-watch` "not 'no spin-offs this week'" message, the `symbol-directory`
"reported as INCONCLUSIVE, not as a quiet week" message, and three
`s1-watch` diagnostics. So the happy path was fine and the code that exists to
tell you the lane learned nothing raised `NameError` instead.

Measured 2026-09-18: SEC full-text search returned HTTP 500, `s1-watch` correctly
returned a non-`ok` status, and the CLI died in the print that was about to
explain why. The weekly pipeline logged `failed: S-1/F-1 search unavailable`
because `weekly_universe` calls the module directly, so the *scheduled* path was
honest -- but every manual `python cli.py s1-watch` retry ended in a traceback
about `sys` rather than the diagnosis, which is the "guard that crashes is worse
than one that is absent" shape this repo has hit before.

**Why a whole-file name check and not `assert "import sys" in source`.** A string
test pins one name and the next unimported module in the next warning path sails
through. This asserts the property -- *every* loaded global resolves to something
the file binds -- so it cannot go stale the way an enumeration does.

The check is deliberately **flow-insensitive**: `cli.py` imports inside branches
(`from pathlib import Path as _Path`), so a binding anywhere in the file counts
as available. That is exactly the sensitivity needed here -- it costs no false
positives on this file's style, and `sys`, bound nowhere at all, still fails.
"""
from __future__ import annotations

import ast
import builtins
import pathlib

CLI = pathlib.Path(__file__).resolve().parent.parent / "cli.py"

# Names every module gets for free from the import machinery. They are not in
# `dir(builtins)`, so without this `__file__` reads as unbound -- a false
# positive, and a check that cries wolf is one the next reader turns off.
MODULE_DUNDERS = frozenset({
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__",
})


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name the file binds, at any scope. Flow-insensitive by design."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            bound.update(node.names)
    return bound


def test_every_loaded_name_in_cli_is_bound_or_builtin():
    tree = ast.parse(CLI.read_text(encoding="utf-8"), filename=str(CLI))
    bound = _bound_names(tree) | set(dir(builtins)) | MODULE_DUNDERS

    unbound: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in bound:
                unbound.setdefault(node.id, node.lineno)

    assert not unbound, (
        "cli.py loads name(s) it never binds -- these raise NameError the moment "
        "the line runs, and in this file such lines have historically been the "
        "warning paths nobody exercises: "
        + ", ".join(f"{n!r} (first use line {ln})" for n, ln in sorted(unbound.items()))
    )


def test_the_sys_stderr_diagnostics_still_exist():
    """Guard the guard: the check above passes trivially if the prints are deleted.

    These six messages are the only thing that distinguishes "the lane found
    nothing" from "the lane could not look", which is the distinction three
    modules in this repo are built around. If a refactor drops them, the name
    check has nothing left to protect and would still be green.
    """
    source = CLI.read_text(encoding="utf-8")
    assert source.count("file=sys.stderr") >= 6, (
        "cli.py's inconclusive-vs-quiet-week diagnostics have been removed or "
        "rerouted; test_every_loaded_name_in_cli_is_bound_or_builtin would now "
        "pass without protecting anything"
    )
