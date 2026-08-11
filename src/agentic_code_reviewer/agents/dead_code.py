"""Deterministic dead-code checker (no LLM call).

Scans the diff for newly-added import statements whose symbol is never
referenced anywhere in the repository snapshot, and emits low-severity
findings directly. Conservative by design: any reference anywhere suppresses
the finding, so we only report symbols that are provably unreferenced.

The checker is deliberately language-narrow (Python + JS/TS): those cover the
vast majority of changed imports, and unparseable import forms (multi-line
parenthesized imports, dynamic imports) are silently skipped rather than
misreported.
"""

from __future__ import annotations

import ast
import re

from agentic_code_reviewer.agents.base import AgentRun, BaseAgent
from agentic_code_reviewer.analysis.diff import DiffFile, parse_diff
from agentic_code_reviewer.models.findings import ReviewFinding, Severity
from agentic_code_reviewer.models.schemas import AnalysisResult
from agentic_code_reviewer.orchestration.state import ReviewState

# language -> ordered list of (pattern, group) rules. The first matching rule
# wins; group 0 captures the import clause to split into symbol names.
_IMPORT_RULES: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*import\s+(.+)$"),
        re.compile(r"^\s*from\s+[\w.]+\s+import\s+(.+)$"),
    ],
    "javascript": [
        re.compile(r"import\s+\*\s+as\s+([\w$]+)\s+from\s+['\"]"),
        re.compile(r"import\s+(?:type\s+)?\{([^}]+)\}\s*from\s+['\"]"),
        re.compile(r"import\s+([\w$]+)(?:\s*,\s*[^'\"]*)?\s*from\s+['\"]"),
    ],
    "typescript": [
        re.compile(r"import\s+\*\s+as\s+([\w$]+)\s+from\s+['\"]"),
        re.compile(r"import\s+(?:type\s+)?\{([^}]+)\}\s*from\s+['\"]"),
        re.compile(r"import\s+([\w$]+)(?:\s*,\s*[^'\"]*)?\s*from\s+['\"]"),
    ],
}

_IDENT = re.compile(r"[A-Za-z_]\w*")


class DeadCodeChecker(BaseAgent):
    name = "dead_code"
    category = "dead_code"
    prompt_version = "v1"  # documented policy; the stage itself is deterministic

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        try:
            files = parse_diff(diff_text)
        except Exception:  # noqa: BLE001 - never let diff issues kill the stage
            return self._empty("could not parse diff")
        repo_files = state.request.repo_files
        snapshot = getattr(state, "snapshot", None)
        findings: list[ReviewFinding] = []
        for diff_file in files:
            if diff_file.path not in repo_files:
                continue
            changed_lines = set(diff_file.changed_line_numbers)
            for symbol, new_line in self._added_imports(diff_file):
                if self._referenced(symbol, diff_file.path, new_line, repo_files):
                    continue
                findings.append(
                    ReviewFinding(
                        category="dead_code",
                        severity=Severity.LOW,
                        confidence=0.8,
                        title=f"Unused import: {symbol}",
                        description=(
                            f"`{symbol}` is newly imported in {diff_file.path} but is "
                            "never referenced anywhere in the repository snapshot. "
                            "Remove the import to keep the change free of dead code."
                        ),
                        file_path=diff_file.path,
                        start_line=new_line,
                        evidence=f"import of `{symbol}` on line {new_line} is unreferenced",
                        impact=(
                            "Dead import that adds noise and may indicate an unfinished "
                            "refactor. Note: reference analysis covers the repository "
                            "snapshot (bounded on large repos)  -  confirm the symbol is "
                            "not used in files outside the snapshot."
                        ),
                        recommendation=f"Remove the unused `{symbol}` import, or use it.",
                        rule_id="dead-code-import",
                    )
                )
            if snapshot is not None and self.settings.ast_analysis:
                findings += self._unused_locals(diff_file.path, changed_lines, snapshot)
                findings += self._unreachable_code(diff_file.path, changed_lines, snapshot)
        return AgentRun(
            result=AnalysisResult(
                agent="dead_code",
                summary=(
                    f"checked {len(files)} changed file(s) for unreferenced imports, "
                    "unused locals and unreachable code"
                ),
                findings=findings,
                notes=[],
            )
        )

    @staticmethod
    def _unused_locals(
        path: str, changed_lines: set[int], snapshot
    ) -> list[ReviewFinding]:
        """Unused local symbols in functions touched by the diff (statically reliable)."""
        module = snapshot._asts.get(path)  # noqa: SLF001 - internal index
        if module is None:
            return []
        out: list[ReviewFinding] = []

        def consider(name: str, line: int) -> None:
            if name in loaded or name.startswith("_"):
                return
            out.append(
                ReviewFinding(
                    category="dead_code",
                    severity=Severity.LOW,
                    confidence=0.85,
                    title=f"Unused local: {name}",
                    description=(
                        f"`{name}` is assigned/imported in a changed function in {path} "
                        "but is never read anywhere in that function."
                    ),
                    file_path=path,
                    start_line=line,
                    evidence=f"local `{name}` on line {line} has no load sites in its function",
                    impact="Dead code that signals an incomplete refactor or copy-paste residue.",
                    recommendation=f"Remove `{name}`, or use it.",
                    rule_id="dead-code-unused-local",
                )
            )

        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end = node.end_lineno or node.lineno
            if not any(cl is not None and node.lineno <= cl <= end for cl in changed_lines):
                continue
            loaded = {
                n.id
                for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
            for sub in ast.walk(node):
                if sub is node:
                    continue  # never flag the function we are scanning
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    consider(sub.name, sub.lineno)
                elif isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            consider(target.id, sub.lineno)
                elif isinstance(sub, ast.Import):
                    for alias in sub.names:
                        consider(alias.asname or alias.name.split(".")[0], sub.lineno)
        return out

    @staticmethod
    def _unreachable_code(
        path: str, changed_lines: set[int], snapshot
    ) -> list[ReviewFinding]:
        """Newly added statements that can never execute (after a terminal stmt)."""
        module = snapshot._asts.get(path)  # noqa: SLF001 - internal index
        if module is None:
            return []
        out: list[ReviewFinding] = []
        seen_blocks: set[int] = set()
        for node in ast.walk(module):
            for attr in ("body", "orelse", "finalbody"):
                block = getattr(node, attr, None)
                if not isinstance(block, list) or not block:
                    continue
                if id(block) in seen_blocks:
                    continue
                seen_blocks.add(id(block))
                for i in range(len(block) - 1):
                    prev = block[i]
                    nxt = block[i + 1]
                    if isinstance(prev, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                        if (
                            isinstance(nxt, ast.Expr)
                            and isinstance(nxt.value, ast.Constant)
                            and isinstance(nxt.value.value, str)
                        ):
                            continue  # docstring / module string literal
                        if nxt.lineno in changed_lines:
                            out.append(
                                ReviewFinding(
                                    category="dead_code",
                                    severity=Severity.LOW,
                                    confidence=0.85,
                                    title="Unreachable code added",
                                    description=(
                                        f"Line {nxt.lineno} in {path} is unreachable: it follows a "
                                        f"terminal {type(prev).__name__.lower()} statement and can never execute."
                                    ),
                                    file_path=path,
                                    start_line=nxt.lineno,
                                    evidence=f"{type(prev).__name__} on a previous line makes this code unreachable",
                                    impact="Dead code that will never run  -  usually a bug in the new change.",
                                    recommendation="Remove the unreachable statement or restructure the control flow.",
                                    rule_id="dead-code-unreachable",
                                )
                            )
        return out

    @staticmethod
    def _empty(reason: str) -> AgentRun:
        return AgentRun(result=AnalysisResult(agent="dead_code", summary=reason, findings=[], notes=[]))

    @classmethod
    def _added_imports(cls, diff_file: DiffFile) -> list[tuple[str, int]]:
        rules = _IMPORT_RULES.get(diff_file.language)
        if not rules:
            return []
        out: list[tuple[str, int]] = []
        for diff_line in diff_file.added_lines:
            if not diff_line.new_line:
                continue
            for rule in rules:
                m = rule.search(diff_line.text)
                if not m:
                    continue
                clause = m.group(1)
                for symbol in cls._split_clause(clause, diff_file.language):
                    out.append((symbol, diff_line.new_line))
                break  # first matching import rule wins
        return out

    @staticmethod
    def _split_clause(clause: str, language: str) -> list[str]:
        clause = clause.strip()
        if not clause:
            return []
        if language in ("javascript", "typescript"):
            # Namespace (import * as ns) already resolved to the alias.
            if clause.startswith("*"):
                return []
            if language == "typescript" and clause.startswith("type "):
                clause = clause[len("type ") :]
        # A bare module import (`import os.path` or `import os, sys`) binds the
        # top-level name(s); dotted names bind their first segment. A clause like
        # `a` from `from x import a` also lands here (no " as ") and correctly
        # resolves to itself.
        if language == "python" and " as " not in clause and "from " not in clause:
            names = [p.strip().split(".")[0] for p in clause.split(",") if p.strip()]
            return [n for n in names if _IDENT.fullmatch(n)]
        symbols: list[str] = []
        for part in clause.split(","):
            part = part.strip().rstrip(")")
            if " as " in part:
                part = part.rsplit(" as ", 1)[1].strip()
            part = part.strip().lstrip("*")
            if _IDENT.fullmatch(part):
                symbols.append(part)
        return symbols

    @staticmethod
    def _referenced(symbol: str, path: str, line: int, repo_files: dict[str, str]) -> bool:
        """True when ``symbol`` appears anywhere in the snapshot (except the
        import line itself). Conservative: comments/strings count, so we never
        report a symbol we cannot prove is unused."""
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        for other_path, content in repo_files.items():
            if other_path == path:
                content = "\n".join(
                    ln for i, ln in enumerate(content.splitlines(), 1) if i != line
                )
            if pattern.search(content):
                return True
        return False
