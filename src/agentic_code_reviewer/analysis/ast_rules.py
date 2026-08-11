"""AST-based deterministic verification rules.

Layered on top of the regex rules: for patterns that are structurally
understood (Python), we verify against the parsed syntax tree of the changed
file rather than a string search. A rule hit here is treated as *confirmed*
evidence by the verifier.

Supported (Python) checks, per the upgrade spec:

* mutable default arguments
* dangerous calls (eval/exec, os.system, subprocess shell=True, pickle/yaml)
* exception handling (bare / swallowed except)
* function signatures (changed signatures on changed lines)
* imports / exports (newly added, unused imports)
* async/sync usage (blocking calls inside async functions)
* unchecked indexing (statically identifiable only)
* SQL construction (f-string / concatenation / %-formatting into execute)

Semantic verification is *not* claimed beyond what the AST can prove  -  the
verifier stays honest about its evidence layers.
"""

from __future__ import annotations

import ast

from agentic_code_reviewer.analysis.snapshot import RepositorySnapshot

_DANGEROUS_CALLS = {"eval", "exec"}
_DANGEROUS_ATTRS = {
    "system": "os",
    "popen": "os",
    "loads": "pickle",
}
_SQL_CALLS = {"execute", "executemany", "query", "raw_sql"}
_BLOCKING_ATTRS = {"sleep", "get", "post", "put", "delete", "request", "read"}
_BLOCKING_MODULES = {"time", "requests", "httpx", "urllib", "socket"}


def ast_check(
    category: str,
    path: str,
    line: int,
    source_lines: list[str],
    snapshot: RepositorySnapshot | None,
    *,
    removed_text: str = "",
) -> list[tuple[str, str]]:
    """Return ``[(rule_id, detail)]`` for the changed line's enclosing construct.

    The rules are conservative: when the AST does not clearly support a claim,
    no rule id is returned (the finding keeps a lower evidence status).
    """
    if snapshot is None:
        return []
    module = snapshot._asts.get(path)  # noqa: SLF001 - internal index
    if module is None:
        return []
    hits: list[tuple[str, str]] = []
    func = _function_at_line(module, line)

    if category in (
        "correctness",
        "maintainability",
        "data_integrity",
        "security",
        "performance",
        "reliability",
        "resource_lifecycle",
    ):
        if func is not None:
            hits += _check_mutable_default(func, line)
            hits += _check_sql_construction(func, line)
            hits += _check_unchecked_index(func, line, path, source_lines)
        hits += _check_dangerous_calls(module, line, path, source_lines)
    if category in ("error_handling", "observability", "reliability"):
        hits += _check_exception_handling(module, line, path, source_lines)
    if category in ("performance", "reliability", "compatibility"):
        hits += _check_async_blocking(module, line, path, source_lines)
    if category == "api_contract" and removed_text:
        hits += _check_signature_change(func, line, removed_text, path, source_lines)
    if category in ("dead_code", "api_contract", "maintainability"):
        hits += _check_added_import(module, line, path, source_lines, snapshot)
    return hits


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _function_at_line(module: ast.Module, line: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    found: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= line <= (node.end_lineno or node.lineno):
                if found is None or node.lineno >= found.lineno:
                    found = node
    return found


def _line_has(module: ast.Module, line: int, path: str, source_lines: list[str]) -> str:
    if 1 <= line <= len(source_lines):
        return source_lines[line - 1]
    return ""


def _check_mutable_default(func: ast.AST, line: int) -> list[tuple[str, str]]:
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    if line != func.lineno:
        return []
    for default in func.args.defaults + func.args.kw_defaults:
        if default is None:
            continue
        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
            return [
                (
                    "ast:mutable-default",
                    f"{func.name} uses a mutable default argument (line {func.lineno})",
                )
            ]
    return []


def _check_dangerous_calls(
    module: ast.Module, line: int, path: str, source_lines: list[str]
) -> list[tuple[str, str]]:
    text = _line_has(module, line, path, source_lines)
    if not text:
        return []
    hits: list[tuple[str, str]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_CALLS:
                hits.append(("ast:eval-exec", f"{node.func.id}() call on line {line}"))
            if isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr in ("system", "popen")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ):
                    hits.append(("ast:os-command", f"os.{node.func.attr}() on line {line}"))
                if (
                    node.func.attr == "loads"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "pickle"
                ):
                    hits.append(("ast:pickle-loads", f"pickle.loads() on line {line}"))
    # subprocess with shell=True on the same line (AST sees keyword)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in ("call", "run", "Popen")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        hits.append(("ast:subprocess-shell", f"subprocess.{node.func.attr}(shell=True) on line {line}"))
    return hits


def _check_sql_construction(func: ast.AST, line: int) -> list[tuple[str, str]]:
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    hits: list[tuple[str, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _SQL_CALLS or not node.args:
            continue
        arg = node.args[0]
        # f-string SQL
        if isinstance(arg, ast.JoinedStr):
            hits.append(("ast:sql-f-string", f"f-string SQL in {node.func.attr}() (line {node.lineno})"))
        # string concatenation building SQL
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            hits.append(("ast:sql-concat", f"string concatenation in {node.func.attr}() (line {node.lineno})"))
        # %-formatting on a constant-ish SQL string
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
            hits.append(("ast:sql-format", f"%-formatting in {node.func.attr}() (line {node.lineno})"))
    return hits


def _check_exception_handling(
    module: ast.Module, line: int, path: str, source_lines: list[str]
) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for node in ast.walk(module):
        if isinstance(node, ast.ExceptHandler) and node.lineno <= line <= (node.end_lineno or node.lineno):
            if node.type is None:
                hits.append(("ast:bare-except", f"bare except on line {node.lineno}"))
            body = node.body or []
            if body and isinstance(body[-1], (ast.Pass, ast.Continue, ast.Break)):
                hits.append(("ast:swallowed-except", f"except swallows error on line {node.lineno}"))
            if body and isinstance(body[-1], ast.Return) and body[-1].value is None:
                hits.append(("ast:swallowed-except", f"except returns None on line {node.lineno}"))
    return hits


def _check_async_blocking(
    module: ast.Module, line: int, path: str, source_lines: list[str]
) -> list[tuple[str, str]]:
    func = _function_at_line(module, line)
    if not isinstance(func, ast.AsyncFunctionDef):
        return []
    hits: list[tuple[str, str]] = []
    for node in ast.walk(func):
        if getattr(node, "lineno", None) != line:
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in _BLOCKING_ATTRS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in _BLOCKING_MODULES
            ):
                hits.append(
                    (
                        "ast:blocking-in-async",
                        f"blocking {node.func.value.id}.{node.func.attr}() in async {func.name}",
                    )
                )
    return hits


def _check_unchecked_index(
    func: ast.AST, line: int, path: str, source_lines: list[str]
) -> list[tuple[str, str]]:
    """Flag ``coll[i + k]`` inside ``for i in range(...)`` where the bound is
    not provably ``len(coll)``  -  a statically identifiable off-by-bound risk."""
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    hits: list[tuple[str, str]] = []
    for node in ast.walk(func):
        if getattr(node, "lineno", None) != line or not isinstance(node, ast.Subscript):
            continue
        index = node.slice
        # a plain literal index (``items[0]``) is statically in bounds
        if isinstance(index, ast.Constant):
            continue
        subscripted = _name_of(node.value)
        if not subscripted:
            continue
        # walk enclosing loops; the index must derive from the loop variable
        for parent in _enclosing_loops(func, node):
            target = parent.target
            if not isinstance(target, ast.Name):
                continue
            if not _expr_uses(target.id, index):
                continue
            # ``for i in range(len(coll))`` is provably bounded -> safe
            if _loop_bounds_by_len(parent.iter, subscripted):
                continue
            hits.append(
                (
                    "ast:unchecked-index",
                    f"{_expr_text(node)} may exceed bounds (loop over {target.id})",
                )
            )
            return hits
    return hits


def _expr_uses(name: str, expr: ast.AST | None) -> bool:
    """True when ``name`` appears anywhere in the expression tree."""
    if expr is None:
        return False
    if isinstance(expr, ast.Name):
        return expr.id == name
    return any(_expr_uses(name, child) for child in ast.iter_child_nodes(expr))


def _enclosing_loops(func: ast.AST, node: ast.AST) -> list[ast.For]:
    """``for`` loops in the function that contain ``node`` (only ``for`` loops
    have a ``target``/``iter`` pair, which is what the bound check needs)."""
    loops: list[ast.For] = []
    for parent in ast.walk(func):
        if isinstance(parent, ast.For):
            for child in ast.walk(parent):
                if child is node:
                    loops.append(parent)
                    break
    return loops


def _loop_bounds_by_len(iter_node: ast.AST, collection: str) -> bool:
    if isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Name) and iter_node.func.id == "range":
        for arg in iter_node.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "len":
                inner = _name_of(arg.args[0]) if arg.args else None
                if inner == collection:
                    return True
    return False


def _name_of(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _expr_text(node: ast.AST) -> str:
    try:
        import ast as _ast

        return _ast.unparse(node)
    except Exception:  # noqa: BLE001 - unparse is best-effort
        return "<expression>"


def _check_signature_change(
    func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    line: int,
    removed_text: str,
    path: str,
    source_lines: list[str],
) -> list[tuple[str, str]]:
    """If the function on this changed line changed its signature, record it.

    Reporting *breakage* is left to the caller analysis (callers must exist);
    this rule only confirms that a signature change happened.
    """
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    if line != func.lineno:
        return []
    new_sig = _signature_of(func)
    # look for the old signature among the removed lines of this file
    for removed_line in removed_text.splitlines():
        stripped = removed_line.lstrip(" -")
        if stripped.startswith(("def ", "async def ")) and func.name in stripped:
            old_sig = stripped.strip()
            if new_sig not in old_sig:
                return [("ast:signature-change", f"signature of {func.name} changed on line {line}")]
    return []


def _signature_of(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return ast.unparse(func.args)
    except Exception:  # noqa: BLE001
        return ""


def _check_added_import(
    module: ast.Module, line: int, path: str, source_lines: list[str], snapshot: RepositorySnapshot
) -> list[tuple[str, str]]:
    """Newly added import on this line whose symbol is referenced nowhere."""
    text = _line_has(module, line, path, source_lines)
    if not text.strip().startswith(("import ", "from ")):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    hits: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                # referenced anywhere (a Load / call site) beyond the import?
                refs = [r for r in snapshot.references_named(name) if r.line != line]
                if not refs:
                    hits.append(("ast:unused-import", f"{name} imported on line {line} is never referenced"))
    return hits
