"""Lightweight static taint analysis (spec: taint engine).

Tracks common sources -> propagation -> sinks within a single file, using the
AST. Scope is deliberately honest: data flow is resolved intra-file and through
same-file function summaries; cross-module flows, reflection, and dynamic
dispatch are *not* claimed. A sink hit is reported only when a tainted value
can be traced to the sink argument through assignments, calls, containers and
string interpolation.

The verifier treats taint hits as *confirmed* evidence (layer ``taint``) but
only when the flow is real: a sanitizer on the path clears the taint, and
parameterised SQL execution (separate params) is treated as mitigated.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# ----------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------
GLOBAL_SOURCES = {
    "request", "req", "params", "query_params", "query", "body", "json_body",
    "headers", "cookies", "form", "files", "environ", "os.environ", "argv",
    "sys.argv", "message", "msg", "payload", "event", "record", "user_input",
    "input_data", "user", "username",
}

SENSITIVE_SOURCES = {
    "password", "passwd", "pwd", "api_key", "apikey", "secret", "token",
    "access_token", "refresh_token", "ssn", "credit_card", "authorization",
    "auth_header", "cookie",
}

# Parameter names that are treated as attacker-controlled inside handlers.
SOURCE_PARAM_NAMES = {
    "request", "req", "params", "query", "body", "payload", "event", "message",
    "data", "input_data", "args",
}

# ----------------------------------------------------------------------
# Sinks: kind -> (module, attr) / bare-name predicates
# ----------------------------------------------------------------------
SINK_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("sql", ("db", "execute")),
    ("sql", ("db", "executemany")),
    ("sql", ("db", "raw_sql")),
    ("sql", ("cursor", "execute")),
    ("sql", ("conn", "execute")),
    ("sql", ("session", "execute")),
    ("shell", ("os", "system")),
    ("shell", ("os", "popen")),
    ("shell", ("subprocess", "call")),
    ("shell", ("subprocess", "run")),
    ("shell", ("subprocess", "Popen")),
    ("shell", ("os", "spawn")),
    ("fs", ("os", "remove")),
    ("fs", ("os", "unlink")),
    ("fs", ("os", "rename")),
    ("fs", ("shutil", "copy")),
    ("fs", ("shutil", "move")),
    ("fs", ("shutil", "rmtree")),
    ("fs", ("os", "path", "join")),
    ("http", ("requests", "get")),
    ("http", ("requests", "post")),
    ("http", ("requests", "put")),
    ("http", ("requests", "delete")),
    ("http", ("httpx", "get")),
    ("http", ("httpx", "post")),
    ("http", ("httpx", "put")),
    ("http", ("httpx", "delete")),
    ("http", ("urlopen",)),
    ("deserialize", ("pickle", "loads")),
    ("deserialize", ("yaml", "load")),
    ("deserialize", ("marshal", "loads")),
]

BARE_SINKS: dict[str, set[str]] = {
    "shell": {"eval", "exec", "compile"},
    "fs": {"open"},
    "http": {"redirect"},
}

SANITIZERS = {
    "strip", "replace", "lower", "upper", "escape", "quote", "quote_plus",
    "urlencode", "html.escape", "clean", "int", "float", "json.dumps",
    "uuid", "getattr",
}

_SAFE_SQL_ARGS = True  # execute(sql, params) with a separate args tuple is mitigated


@dataclass
class TaintResult:
    sink_kind: str  # sql | shell | fs | html | http | deserialize | log
    source: str
    file_path: str
    line: int
    chain: str


@dataclass
class _Summary:
    # param index -> sink kinds reachable from that parameter
    sink_kinds: dict[int, set[str]] = field(default_factory=dict)
    returns_param: list[bool] = field(default_factory=list)


def build_cross_file_summaries(module_asts: dict[str, ast.Module]) -> dict[str, _Summary]:
    """Function summaries for every module, keyed by function name.

    Lets taint flow across module boundaries: a changed file calling a helper
    defined elsewhere resolves the helper's summary (does its parameter reach a
    sink? does it return a tainted value?) the same way same-file summaries
    work. First definition wins on name collisions (a deliberate, documented
    heuristic - no import-resolution).
    """
    merged: dict[str, _Summary] = {}
    for module in module_asts.values():
        funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in ast.walk(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(node.name, node)
        local = _build_summaries(funcs)
        for name, summary in local.items():
            merged.setdefault(name, summary)
    return merged


def taint_scan(
    path: str,
    source_lines: list[str],
    module: ast.Module,
    cross_file_summaries: dict[str, _Summary] | None = None,
) -> list[TaintResult]:
    """Run taint analysis over one Python file; never raises.

    ``cross_file_summaries`` (from :func:`build_cross_file_summaries`) extends
    the analysis to helpers defined in other modules.
    """
    try:
        return _scan(path, source_lines, module, cross_file_summaries)
    except Exception:  # noqa: BLE001 - taint analysis must never crash a review
        return []


# ----------------------------------------------------------------------
def _scan(
    path: str,
    source_lines: list[str],
    module: ast.Module,
    cross_file_summaries: dict[str, _Summary] | None = None,
) -> list[TaintResult]:
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(node.name, node)

    summaries = _build_summaries(funcs)
    # Merge cross-file summaries last so same-file definitions win. The global
    # map is already fully built, so this can never recurse.
    if cross_file_summaries:
        for name, summary in cross_file_summaries.items():
            summaries.setdefault(name, summary)
    results: list[TaintResult] = []
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            results += _scan_function(path, source_lines, node, funcs, summaries)
        else:
            results += _scan_statement(path, source_lines, node, funcs, summaries, set(), set())
    return results


def _scan_function(
    path: str,
    source_lines: list[str],
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    summaries: dict[str, _Summary],
) -> list[TaintResult]:
    param_names = [a.arg for a in func.args.args]
    is_handler = _is_route_handler(func)
    tainted: set[str] = set()
    sensitive: set[str] = set()
    for p in param_names:
        if p in SOURCE_PARAM_NAMES or is_handler:
            tainted.add(p)
        if p.lower() in SENSITIVE_SOURCES:
            tainted.add(p)
            sensitive.add(p)
    return _scan_body(path, source_lines, func.body, funcs, summaries, tainted, sensitive, func)


def _scan_body(
    path: str,
    source_lines: list[str],
    body: list[ast.stmt],
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    summaries: dict[str, _Summary],
    tainted: set[str],
    sensitive: set[str],
    scope_func: ast.AST | None,
) -> list[TaintResult]:
    results: list[TaintResult] = []
    for stmt in body:
        results += _scan_statement(path, source_lines, stmt, funcs, summaries, tainted, sensitive, scope_func)
    return results


def _scan_statement(
    path: str,
    source_lines: list[str],
    node: ast.stmt | ast.AST,
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    summaries: dict[str, _Summary],
    tainted: set[str],
    sensitive: set[str],
    scope_func: ast.AST | None = None,
) -> list[TaintResult]:
    results: list[TaintResult] = []
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        value = node.value
        if _expr_tainted(value, tainted, sensitive):
            for target in _assign_targets(node):
                tainted.add(target)
                # ``password = request.get("pw")`` keeps the sensitive marker
                # on the assigned name so log/exception sinks can flag it.
                if target.lower() in SENSITIVE_SOURCES:
                    sensitive.add(target)
        elif isinstance(value, ast.Call):
            name = _call_name(value)
            if name and name in summaries:
                summary = summaries[name]
                results += _summary_sink_hits(path, value, tainted, sensitive, summaries)
                if _args_tainted(value.args, tainted, sensitive) and any(summary.returns_param):
                    for target in _assign_targets(node):
                        tainted.add(target)
    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        # bare call statement: ``db.execute(name)``
        results += _check_sink(path, node.value, tainted, sensitive)
        results += _summary_sink_hits(path, node.value, tainted, sensitive, summaries)
    elif isinstance(node, ast.Call):
        results += _check_sink(path, node, tainted, sensitive)
        results += _summary_sink_hits(path, node, tainted, sensitive, summaries)
    elif isinstance(node, (ast.If, ast.While)):
        for child in node.body + getattr(node, "orelse", []):
            results += _scan_statement(path, source_lines, child, funcs, summaries, tainted, sensitive, scope_func)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        for child in node.body + node.orelse:
            results += _scan_statement(path, source_lines, child, funcs, summaries, tainted, sensitive, scope_func)
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        ctx = node.items[0].context_expr if node.items else None
        if isinstance(ctx, ast.Call):
            results += _check_sink(path, ctx, tainted, sensitive)
        elif isinstance(ctx, ast.Tuple):
            for elt in ctx.elts:
                if isinstance(elt, ast.Call):
                    results += _check_sink(path, elt, tainted, sensitive)
        for child in node.body:
            results += _scan_statement(path, source_lines, child, funcs, summaries, tainted, sensitive, scope_func)
    elif isinstance(node, ast.Try):
        for child in node.body + node.orelse + node.finalbody:
            results += _scan_statement(path, source_lines, child, funcs, summaries, tainted, sensitive, scope_func)
        for handler in node.handlers:
            for child in handler.body:
                results += _scan_statement(path, source_lines, child, funcs, summaries, tainted, sensitive, scope_func)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # nested function: analyzed at module level; skip here
        return results
    elif isinstance(node, ast.Return):
        # A returned call is a potential sink (``return db.execute(x)``);
        # cross-function flows additionally ride on the summaries.
        if isinstance(node.value, ast.Call):
            results += _check_sink(path, node.value, tainted, sensitive)
            results += _summary_sink_hits(path, node.value, tainted, sensitive, summaries)
        return results
    return results


# ----------------------------------------------------------------------
def _check_sink(
    path: str, call: ast.Call, tainted: set[str], sensitive: set[str]
) -> list[TaintResult]:
    name = _call_name(call)
    if not name:
        return []
    results: list[TaintResult] = []
    for kind, target in SINK_RULES:
        if _matches(name, target):
            if kind == "sql" and _is_parameterized(call):
                continue
            if _args_tainted(call.args, tainted, sensitive):
                results.append(
                    TaintResult(
                        sink_kind=kind,
                        source=", ".join(sorted(_tainted_names(call.args, tainted, sensitive))),
                        file_path=path,
                        line=call.lineno,
                        chain=f"{name}() receives tainted input",
                    )
                )
    for kind, names in BARE_SINKS.items():
        if name in names and _args_tainted(call.args, tainted, sensitive):
            results.append(
                TaintResult(
                    sink_kind=kind,
                    source=", ".join(sorted(_tainted_names(call.args, tainted, sensitive))),
                    file_path=path,
                    line=call.lineno,
                    chain=f"{name}() receives tainted input",
                )
            )
    # sensitive data leaking into logs / exceptions (``logger.info`` etc.)
    if name.split(".")[-1] in {
        "debug", "info", "warning", "error", "exception", "critical", "log",
    }:
        if _args_tainted(call.args, tainted, sensitive, require_sensitive=True):
            results.append(
                TaintResult(
                    sink_kind="log",
                    source=", ".join(sorted(_sensitive_names(call.args, sensitive))),
                    file_path=path,
                    line=call.lineno,
                    chain=f"{name}() logs sensitive data",
                )
            )
    return results


# ----------------------------------------------------------------------
def _expr_tainted(expr: ast.AST | None, tainted: set[str], sensitive: set[str]) -> bool:
    if expr is None:
        return False
    if isinstance(expr, ast.Name):
        return expr.id in tainted
    if isinstance(expr, ast.Attribute):
        return _expr_tainted(expr.value, tainted, sensitive)
    if isinstance(expr, ast.Subscript):
        return _expr_tainted(expr.value, tainted, sensitive) or _expr_tainted(expr.slice, tainted, sensitive)
    if isinstance(expr, ast.Call):
        name = _call_name(expr)
        if name in SANITIZERS:
            return False  # sanitizer clears the taint on this path
        receiver_tainted = (
            _expr_tainted(expr.func.value, tainted, sensitive)
            if isinstance(expr.func, ast.Attribute)
            else False
        )
        return receiver_tainted or _args_tainted(expr.args, tainted, sensitive) or any(
            _expr_tainted(kw.value, tainted, sensitive) for kw in expr.keywords
        )
    if isinstance(expr, (ast.BinOp, ast.BoolOp, ast.Compare)):
        return _any_expr_tainted(ast.iter_child_nodes(expr), tainted, sensitive)
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        return any(_expr_tainted(e, tainted, sensitive) for e in expr.elts)
    if isinstance(expr, ast.Dict):
        return any(
            _expr_tainted(k, tainted, sensitive) or _expr_tainted(v, tainted, sensitive)
            for k, v in zip(expr.keys, expr.values, strict=False)
            if k is not None
        )
    if isinstance(expr, ast.JoinedStr):
        return any(_expr_tainted(v, tainted, sensitive) for v in expr.values)
    if isinstance(expr, ast.FormattedValue):
        return _expr_tainted(expr.value, tainted, sensitive)
    if isinstance(expr, ast.Constant):
        return False
    return False


def _any_expr_tainted(nodes, tainted: set[str], sensitive: set[str]) -> bool:
    return any(_expr_tainted(n, tainted, sensitive) for n in nodes)


def _args_tainted(
    args: list[ast.expr],
    tainted: set[str],
    sensitive: set[str],
    require_sensitive: bool = False,
) -> bool:
    for arg in args:
        if _expr_tainted(arg, tainted, sensitive):
            if require_sensitive:
                if _expr_sensitive(arg, sensitive):
                    return True
            else:
                return True
    return False


def _expr_sensitive(expr: ast.AST | None, sensitive: set[str]) -> bool:
    if expr is None:
        return False
    if isinstance(expr, ast.Name):
        return expr.id in sensitive
    if isinstance(expr, ast.Attribute):
        return _expr_sensitive(expr.value, sensitive)
    if isinstance(expr, ast.Subscript):
        return _expr_sensitive(expr.value, sensitive)
    return False


def _tainted_names(args: list[ast.expr], tainted: set[str], sensitive: set[str]) -> list[str]:
    names: list[str] = []
    for arg in args:
        names += _names_in(arg, tainted, sensitive)
    return names


def _sensitive_names(args: list[ast.expr], sensitive: set[str]) -> list[str]:
    names: list[str] = []
    for arg in args:
        names += _names_in(arg, set(), sensitive, sensitive_only=True)
    return names


def _names_in(expr: ast.AST | None, tainted: set[str], sensitive: set[str], sensitive_only: bool = False) -> list[str]:
    if expr is None:
        return []
    out: list[str] = []
    if isinstance(expr, ast.Name):
        if (sensitive_only and expr.id in sensitive) or (not sensitive_only and expr.id in tainted):
            out.append(expr.id)
    for child in ast.iter_child_nodes(expr):
        out += _names_in(child, tainted, sensitive, sensitive_only)
    return out


def _assign_targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    out: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            out.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    out.append(elt.id)
    return out


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _matches(name: str, target: tuple[str, ...]) -> bool:
    parts = name.split(".")
    # target like ("os","system") matches "os.system"; bare ("urlopen",) matches "urlopen"
    if len(target) == 1:
        return parts[-1] == target[0]
    if len(target) == 2:
        return len(parts) >= 2 and parts[-2:] == list(target)
    return len(parts) >= 3 and parts[-3:] == list(target)


def _is_parameterized(call: ast.Call) -> bool:
    """execute(sql, params) with params passed separately -> mitigated."""
    return len(call.args) >= 2 or any(kw.arg in ("params", "parameters", "args") for kw in call.keywords)


def _is_route_handler(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in func.decorator_list:
        if isinstance(decorator, ast.Attribute) and decorator.attr.lower() in {
            "get", "post", "put", "delete", "patch", "route",
        }:
            return True
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr.lower() in {"get", "post", "put", "delete", "patch", "route"}
        ):
            return True
    return False


# ----------------------------------------------------------------------
# same-file function summaries (cycle-safe)
# ----------------------------------------------------------------------
def _build_summaries(
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> dict[str, _Summary]:
    """For each function: which params reach a sink / flow out through return."""
    summaries: dict[str, _Summary] = {}
    for name, func in funcs.items():
        params = [a.arg for a in func.args.args]
        summaries[name] = _Summary(returns_param=[False] * len(params))
    # Build callee-first (reverse definition order) so a caller's summary sees
    # the complete summaries of every function it calls.
    for name, func in reversed(list(funcs.items())):
        summary = summaries[name]
        params = [a.arg for a in func.args.args]
        for idx, p in enumerate(params):
            tainted = {p}
            results = _scan_body(name, [], func.body, funcs, summaries, set(tainted), set(), func)
            kinds = {r.sink_kind for r in results if r.sink_kind != "flow"}
            if kinds:
                summary.sink_kinds[idx] = kinds
            for node in ast.walk(func):
                if isinstance(node, ast.Return) and node.value is not None:
                    names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
                    if p in names:
                        summary.returns_param[idx] = True
    return summaries


def _summary_sink_hits(
    path: str,
    call: ast.Call,
    tainted: set[str],
    sensitive: set[str],
    summaries: dict[str, _Summary],
) -> list[TaintResult]:
    """Sink hits that flow through a same-file function call.

    ``run_query(user_input)`` where ``run_query`` routes its first parameter
    into ``db.execute`` reports a real ``sql`` sink at the call site.
    """
    summary = summaries.get(_call_name(call))
    if summary is None:
        return []
    out: list[TaintResult] = []
    for idx, arg in enumerate(call.args):
        kinds = summary.sink_kinds.get(idx)
        if not kinds or not _expr_tainted(arg, tainted, sensitive):
            continue
        for kind in kinds:
            out.append(
                TaintResult(
                    sink_kind=kind,
                    source=", ".join(sorted(_tainted_names([arg], tainted, sensitive))),
                    file_path=path,
                    line=call.lineno,
                    chain=f"{_call_name(call)}() routes tainted input to a sink",
                )
            )
    return out
