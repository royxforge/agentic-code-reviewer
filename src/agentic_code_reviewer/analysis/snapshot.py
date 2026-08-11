"""Shared repository snapshot.

Built **once per review** and queried by every stage, so reviewers never
independently re-read or re-parse the same files (spec: performance
requirements). The snapshot holds:

* file index             -  path -> content
* AST index              -  path -> parsed module (Python only)
* symbol index           -  name -> definitions (functions, classes, params, vars, imports)
* reference index        -  name -> load/reference sites and call sites
* import index           -  path -> imported names
* route/API index        -  decorated HTTP routes (FastAPI/Flask/Express-style)
* configuration index    -  environment variables referenced by the code
* test index             -  test files and test functions
* dependency graph       -  path -> set of module-level imports
* optional git history   -  ``git log`` / ``git blame`` for changed files (read-only)

The snapshot is best-effort: any file that fails to parse is skipped, and the
history layer degrades to empty on any git failure. It never raises during a
review  -  callers use the accessor methods which return empty collections.
"""

from __future__ import annotations

import ast
import threading
from dataclasses import dataclass, field

_MAX_FILE_CHARS = 1_000_000
_MAX_FILES = 4_000

_LANG_EXTS = {".py", ".pyi"}
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "route", "head", "options"}
_ENV_CALLS = {"getenv", "environ"}
_LOG_BACKEND = ("log", "logs", "logger", "logging")


@dataclass
class SymbolDef:
    name: str
    kind: str  # function | class | import | parameter | var | export
    file_path: str
    line: int = 0
    qualname: str = ""
    is_async: bool = False
    has_return: bool = False
    params: list[str] = field(default_factory=list)


@dataclass
class SymbolRef:
    name: str
    kind: str  # call | reference | attribute
    file_path: str
    line: int = 0


@dataclass
class RouteInfo:
    method: str
    path: str
    handler: str
    file_path: str
    line: int = 0


class RepositorySnapshot:
    """Read-only, pre-built view of the repository under review."""

    def __init__(
        self,
        repo_files: dict[str, str],
        changed_files: list[str] | None = None,
        *,
        local_path: str | None = None,
        history_enabled: bool = False,
    ) -> None:
        self.repo_files = {k: v for k, v in repo_files.items() if len(v) <= _MAX_FILE_CHARS}
        self.changed_files = set(changed_files or [])
        self.local_path = local_path
        self.history_enabled = history_enabled

        self._asts: dict[str, ast.Module] = {}
        self._defs: dict[str, list[SymbolDef]] = {}
        self._refs: dict[str, list[SymbolRef]] = {}
        self._imports: dict[str, list[str]] = {}
        self._routes: list[RouteInfo] = []
        self._env_vars: set[str] = set()
        self._test_functions: dict[str, list[str]] = {}
        self._history: dict[str, list[str]] = {}
        self._history_lock = threading.Lock()
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        """Eagerly index every parseable Python file (single pass)."""
        limited = list(self.repo_files.items())[:_MAX_FILES]
        for path, content in limited:
            if not path.endswith((".py", ".pyi")):
                continue
            module = self._parse(path, content)
            if module is None:
                continue
            self._asts[path] = module
            self._walk_module(path, module)

    @staticmethod
    def _parse(path: str, content: str) -> ast.Module | None:
        try:
            return ast.parse(content, filename=path)
        except (SyntaxError, ValueError, TypeError):
            return None

    def _walk_module(self, path: str, module: ast.Module) -> None:
        imports: list[str] = []
        for node in module.body:
            self._record_module_statement(path, node, imports)
        self._imports[path] = sorted(set(imports))

    def _record_module_statement(self, path: str, node: ast.AST, imports: list[str]) -> None:
        """Index one module-level statement and (for containers) its body."""
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imports.append(name)
                self._defs.setdefault(name, []).append(
                    SymbolDef(name=name, kind="import", file_path=path, line=node.lineno)
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args + node.args.kwonlyargs]
            if node.args.vararg:
                params.append(node.args.vararg.arg)
            if node.args.kwarg:
                params.append(node.args.kwarg.arg)
            self._defs.setdefault(node.name, []).append(
                SymbolDef(
                    name=node.name,
                    kind="function",
                    file_path=path,
                    line=node.lineno,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    has_return=self._has_return(node),
                    params=params,
                )
            )
            for arg in node.args.args:
                self._defs.setdefault(arg.arg, []).append(
                    SymbolDef(name=arg.arg, kind="parameter", file_path=path, line=node.lineno)
                )
            if node.name.startswith("test_") or path.split("/")[-1].startswith("test_"):
                self._test_functions.setdefault(path, []).append(node.name)
            self._record_route(path, node)
            for sub in list(getattr(node, "body", [])):
                self._record_module_statement(path, sub, imports)
            for sub in ast.walk(node):
                self._record_call_refs(path, sub)
        elif isinstance(node, ast.ClassDef):
            self._defs.setdefault(node.name, []).append(
                SymbolDef(name=node.name, kind="class", file_path=path, line=node.lineno)
            )
            for sub in list(getattr(node, "body", [])):
                self._record_module_statement(path, sub, imports)
            for sub in ast.walk(node):
                self._record_call_refs(path, sub)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    self._defs.setdefault(target.id, []).append(
                        SymbolDef(name=target.id, kind="var", file_path=path, line=node.lineno)
                    )
            self._record_call_refs(path, node)
            for sub in ast.walk(node):
                self._record_call_refs(path, sub)
        else:
            self._record_call_refs(path, node)
            for sub in ast.walk(node):
                self._record_call_refs(path, sub)

    @staticmethod
    def _has_return(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return any(isinstance(sub, ast.Return) for sub in ast.walk(node))

    def _record_route(self, path: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            method = ""
            path_arg = ""
            if isinstance(decorator, ast.Attribute):
                if decorator.attr.lower() in _HTTP_METHODS:
                    method = decorator.attr.lower()
            elif isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                if decorator.func.attr.lower() in _HTTP_METHODS:
                    method = decorator.func.attr.lower()
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        path_arg = str(decorator.args[0].value)
            if method:
                self._routes.append(
                    RouteInfo(
                        method=method,
                        path=path_arg,
                        handler=node.name,
                        file_path=path,
                        line=node.lineno,
                    )
                )

    def _record_call_refs(self, path: str, node: ast.AST) -> None:
        # environment-variable reads (module- or function-level):
        # ``os.getenv("X")``, ``os.environ.get("X")`` and ``os.environ["X"]``
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr in _ENV_CALLS
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                self._env_vars.add(str(node.args[0].value))
            elif (
                isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "os"
                and node.func.value.attr == "environ"
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                self._env_vars.add(str(node.args[0].value))
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
        ):
            self._env_vars.add(str(node.slice.value))
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                self._refs.setdefault(func.id, []).append(
                    SymbolRef(name=func.id, kind="call", file_path=path, line=node.lineno)
                )
            elif isinstance(func, ast.Attribute):
                self._refs.setdefault(func.attr, []).append(
                    SymbolRef(name=func.attr, kind="call", file_path=path, line=node.lineno)
                )
                if isinstance(func.value, ast.Name):
                    self._refs.setdefault(func.value.id, []).append(
                        SymbolRef(name=func.value.id, kind="attribute", file_path=path, line=node.lineno)
                    )
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            self._refs.setdefault(node.id, []).append(
                SymbolRef(name=node.id, kind="reference", file_path=path, line=node.lineno)
            )

    # ------------------------------------------------------------------
    # Accessors (never raise)
    # ------------------------------------------------------------------
    def symbols_named(self, name: str) -> list[SymbolDef]:
        return self._defs.get(name, [])

    def references_named(self, name: str) -> list[SymbolRef]:
        return self._refs.get(name, [])

    def has_symbol(self, name: str) -> bool:
        """Any definition OR reference anywhere in the repository."""
        return name in self._defs or name in self._refs

    def has_index(self) -> bool:
        """True when the symbol index actually contains repository symbols.

        An empty snapshot (no parseable Python files) proves nothing about the
        repository  -  evidence claims must never be rejected against an empty
        index.
        """
        return bool(self._defs or self._refs)

    def callers_of(self, name: str) -> list[tuple[str, int]]:
        """Call sites of ``name`` excluding its own definition file/line."""
        own = {(d.file_path, d.line) for d in self._defs.get(name, [])}
        out = []
        for ref in self._refs.get(name, []):
            if ref.kind == "call" and (ref.file_path, ref.line) not in own:
                out.append((ref.file_path, ref.line))
        return sorted(out)

    def definitions_in_file(self, path: str) -> list[SymbolDef]:
        out: list[SymbolDef] = []
        for symbol_list in self._defs.values():
            out.extend(item for item in symbol_list if item.file_path == path)
        return out

    def routes(self) -> list[RouteInfo]:
        return list(self._routes)

    def env_vars(self) -> set[str]:
        return set(self._env_vars)

    def test_files(self) -> list[str]:
        return sorted(self._test_functions)

    def test_functions(self, path: str) -> list[str]:
        return self._test_functions.get(path, [])

    def imports_of(self, path: str) -> list[str]:
        return self._imports.get(path, [])

    def source_lines(self, path: str) -> list[str]:
        content = self.repo_files.get(path, "")
        return content.splitlines() if content else []

    def line_text(self, path: str, line: int) -> str:
        lines = self.source_lines(path)
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return ""

    # ------------------------------------------------------------------
    # Git history (read-only, guarded, never raises)
    # ------------------------------------------------------------------
    def history_for(self, path: str) -> list[str]:
        """Recent ``git log`` lines for a file (empty on any failure)."""
        if not self.history_enabled or not self.local_path:
            return []
        with self._history_lock:
            if path not in self._history:
                self._history[path] = self._git_log(path)
            return list(self._history[path])

    def blame_for(self, path: str, line: int) -> str:
        """One-line ``git blame`` summary for a specific line ("" on failure)."""
        if not self.history_enabled or not self.local_path:
            return ""
        return self._git_blame(path, line)

    def _git_log(self, path: str) -> list[str]:
        if not self.local_path:
            return []
        return _run_git(self.local_path, ["log", "-n", "5", "--format=%h %ad %s", "--date=short", "--", path])

    def _git_blame(self, path: str, line: int) -> str:
        if not self.local_path:
            return ""
        out = _run_git(self.local_path, ["blame", "-L", f"{line},{line}", "--porcelain", "--", path])
        if not out:
            return ""
        # First line is "<commit> <orig-line> <final-line> <lines>"  -  take it.
        return out[0].split(" ", 1)[0][:12]

    # ------------------------------------------------------------------
    # Helpers used by evidence / dead-code / API layers
    # ------------------------------------------------------------------
    def added_imports(self, path: str, changed_lines: set[int]) -> list[tuple[str, int]]:
        """(name, line) pairs for import statements on changed lines in ``path``."""
        lines = self.source_lines(path)
        out: list[tuple[str, int]] = []
        for line_no in sorted(changed_lines):
            if not (1 <= line_no <= len(lines)):
                continue
            text = lines[line_no - 1].strip()
            if text.startswith(("import ", "from ")):
                try:
                    module = ast.parse(text)
                except SyntaxError:
                    continue
                for node in ast.walk(module):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        for alias in node.names:
                            out.append((alias.asname or alias.name.split(".")[0], line_no))
        return out

    def symbol_referenced_anywhere(self, name: str) -> bool:
        """True when ``name`` appears in any definition or reference site."""
        return name in self._defs or name in self._refs

    def reference_count(self, name: str, exclude_path: str | None = None) -> int:
        refs = self._refs.get(name, [])
        if exclude_path:
            refs = [r for r in refs if r.file_path != exclude_path]
        return len(refs)


def _run_git(root: str, args: list[str]) -> list[str]:
    """Run a read-only git command; return stdout lines (never raises)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            timeout=10,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]
