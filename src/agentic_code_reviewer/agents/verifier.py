"""Evidence-verification agent (deterministic).

Layered evidence pipeline (spec: evidence status + deterministic upgrade):

    LLM finding
        -> evidence resolver (file + changed line + repository snapshot)
        -> regex verification
        -> AST verification          (Python, structurally understood patterns)
        -> symbol/reference checks   (claims naming non-existent symbols are rejected)
        -> taint verification        (sources -> propagation -> sinks)
        -> evidence status

The outcome is one of ``confirmed / strongly_supported / insufficient_evidence /
rejected``. We never claim ``confirmed`` without deterministic repository
evidence, we never fabricate confirmation, and claims that contradict the
repository state are explicitly ``rejected`` with a reason.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, BaseAgent
from agentic_code_reviewer.analysis.ast_rules import ast_check
from agentic_code_reviewer.analysis.diff import DiffFile, parse_diff
from agentic_code_reviewer.analysis.rules import match_category_patterns, token_overlap
from agentic_code_reviewer.models.findings import (
    EvidenceStatus,
    ReviewFinding,
    verification_from_evidence,
)
from agentic_code_reviewer.models.schemas import VerificationResult, VerifierOutput
from agentic_code_reviewer.orchestration.state import ReviewState

_STOPWORDS = {
    "the", "and", "this", "that", "with", "from", "line", "should", "does",
    "when", "into", "using", "after", "before", "their", "there", "these",
    "those", "would", "could", "might", "must", "will", "have", "been", "being",
}


class VerifierAgent(BaseAgent):
    name = "verifier"
    prompt_version = "v1"  # documented policy; the stage itself is deterministic

    def run(self, state: ReviewState, *args: object) -> AgentRun:
        files: dict[str, DiffFile] = {}
        try:
            files = {f.path: f for f in parse_diff(state.request.diff_text)}
        except Exception:  # noqa: BLE001 - verification degrades gracefully
            pass
        self._taint_cache: dict[str, list] = {}
        results: list[VerificationResult] = []
        for finding in state.findings:
            result = self._verify(finding, files, state)
            finding.verification_status = result.status
            finding.evidence_status = result.evidence_status
            finding.rejection_reason = result.rejection_reason
            finding.evidence_layers = result.evidence_layers
            results.append(result)
        return AgentRun(result=VerifierOutput(results=results))

    # ------------------------------------------------------------------
    def _verify(
        self,
        finding: ReviewFinding,
        files: dict[str, DiffFile],
        state: ReviewState,
    ) -> VerificationResult:
        diff_file = files.get(finding.file_path)
        if diff_file is None:
            # Finding points at a file that is not part of the diff.
            if finding.file_path in state.request.repo_files:
                return self._result(
                    finding,
                    EvidenceStatus.INSUFFICIENT_EVIDENCE,
                    "file-exists-outside-diff",
                    f"{finding.file_path} exists in the snapshot but was not modified.",
                )
            return self._result(
                finding,
                EvidenceStatus.REJECTED,
                "file-not-in-diff",
                "Referenced file is not part of the reviewed change and is not in the repository snapshot.",
            )

        changed_lines = set(diff_file.changed_line_numbers)
        line = finding.start_line

        if line is None:
            return self._result(
                finding,
                EvidenceStatus.INSUFFICIENT_EVIDENCE,
                "no-line-reference",
                f"File {finding.file_path} is changed but no line was given.",
            )
        if line not in changed_lines:
            return self._result(
                finding,
                EvidenceStatus.REJECTED,
                "line-outside-hunks",
                f"Line {line} is not a changed line in {finding.file_path}  -  the claim contradicts the diff.",
            )

        line_text = self._changed_line_text(diff_file, line, state)
        snapshot = getattr(state, "snapshot", None)
        removed_text = self._removed_text(diff_file)

        regex_hits = match_category_patterns(finding.category, line_text)
        ast_hits: list[tuple[str, str]] = []
        if self.settings.ast_analysis:
            ast_hits = ast_check(
                finding.category,
                diff_file.path,
                line,
                state.request.repo_files.get(diff_file.path, "").splitlines(),
                snapshot,
                removed_text=removed_text,
            )
        taint_hits: list[tuple[str, str]] = []
        if self.settings.taint_analysis and snapshot is not None:
            taint_hits = self._taint_on_line(snapshot, diff_file.path, line, state)

        overlap = token_overlap(f"{finding.title} {finding.description}", line_text)

        # -- rejection: claim contradicts repository state -----------------
        rejection = self._contradiction(finding, line_text, snapshot)
        if rejection and not (regex_hits or ast_hits or taint_hits or overlap):
            return self._result(
                finding,
                EvidenceStatus.REJECTED,
                "contradicts-repository",
                rejection,
            )

        evidence_parts = [f"Line {line} in {finding.file_path} is a changed line."]
        if line_text:
            evidence_parts.append(f"Changed line: {line_text.strip()[:160]}")
        layers: list[str] = []
        if regex_hits:
            evidence_parts.append(f"Matched regex rule(s): {', '.join(regex_hits)}")
            layers.extend(f"regex:{r}" for r in regex_hits)
        if ast_hits:
            evidence_parts.append(f"Matched AST rule(s): {', '.join(rid for rid, _ in ast_hits)}")
            layers.extend(f"ast:{rid}" for rid, _ in ast_hits)
        if taint_hits:
            evidence_parts.append(f"Taint flow(s): {', '.join(rid for rid, _ in taint_hits)}")
            layers.extend(f"taint:{rid}" for rid, _ in taint_hits)
        if overlap:
            evidence_parts.append(f"Identifier overlap: {', '.join(sorted(overlap)[:5])}")

        if regex_hits or ast_hits or taint_hits:
            return self._result(
                finding,
                EvidenceStatus.CONFIRMED,
                "deterministic-rule-match",
                " ".join(evidence_parts),
                layers=layers,
            )
        if overlap:
            return self._result(
                finding,
                EvidenceStatus.STRONGLY_SUPPORTED,
                "changed-line-and-identifier-overlap",
                " ".join(evidence_parts),
                layers=["line-overlap"],
            )
        return self._result(
            finding,
            EvidenceStatus.STRONGLY_SUPPORTED,
            "line-inside-changed-hunk",
            " ".join(evidence_parts),
            layers=["changed-hunk"],
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _contradiction(
        finding: ReviewFinding, line_text: str, snapshot
    ) -> str:
        """A deterministic reason the claim contradicts repository state, or \"\"."""
        if snapshot is None:
            return ""
        # The finding names specific symbols; if none of them exist anywhere in
        # the repository snapshot and none appear on the changed line, the
        # claim is not grounded in this repository.
        import re

        named = {
            t
            for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", f"{finding.title} {finding.description}")
            if t not in _STOPWORDS
        }
        if not named:
            return ""
        # An empty symbol index proves nothing about the repository: only
        # reject against a real index whose symbols contradict the claim.
        if not snapshot.has_index():
            return ""
        present = [n for n in named if snapshot.has_symbol(n)]
        if not present:
            return (
                f"Finding names symbols ({', '.join(sorted(named)[:4])}) that do not "
                "exist anywhere in the repository snapshot."
            )
        return ""

    def _taint_on_line(self, snapshot, path: str, line: int, state) -> list[tuple[str, str]]:
        """Taint results for ``path`` (cached per verifier run), filtered to line."""
        if path not in self._taint_cache:
            self._taint_cache[path] = self._taint_for_file(snapshot, path, state)
        hits: list[tuple[str, str]] = []
        for result in self._taint_cache[path]:
            if result.line == line:
                hits.append((f"{result.sink_kind}-{result.source}", result.chain))
        return hits[:4]

    def _taint_for_file(self, snapshot, path: str, state) -> list:
        from agentic_code_reviewer.analysis.taint import taint_scan

        module = snapshot._asts.get(path)  # noqa: SLF001 - internal index
        if module is None:
            return []
        source_lines = state.request.repo_files.get(path, "").splitlines()
        return taint_scan(path, source_lines, module)

    @staticmethod
    def _removed_text(diff_file: DiffFile) -> str:
        return "\n".join(ln.text for ln in diff_file.removed_lines)

    @staticmethod
    def _result(
        finding: ReviewFinding,
        evidence_status: EvidenceStatus,
        method: str,
        evidence: str,
        *,
        layers: list[str] | None = None,
        rejection_reason: str = "",
    ) -> VerificationResult:
        # Rejected findings always carry the deterministic reason on
        # ``rejection_reason`` (it doubles as the evidence text).
        if not rejection_reason and evidence_status == EvidenceStatus.REJECTED:
            rejection_reason = evidence
        return VerificationResult(
            finding_id=_finding_id(finding),
            status=verification_from_evidence(evidence_status),
            method=method,
            evidence=evidence,
            evidence_status=evidence_status,
            rejection_reason=rejection_reason,
            evidence_layers=layers or [],
        )

    @staticmethod
    def _changed_line_text(diff_file: DiffFile, line: int, state: ReviewState) -> str:
        """Prefer the actual added line from the diff; fall back to snapshot."""
        for hunk in diff_file.hunks:
            for diff_line in hunk.lines:
                if diff_line.new_line == line:
                    return diff_line.text
        content = state.request.repo_files.get(diff_file.path, "")
        if content:
            lines = content.splitlines()
            if 1 <= line <= len(lines):
                return lines[line - 1]
        return ""


def _finding_id(finding: ReviewFinding) -> str:
    return f"{finding.file_path}:{finding.start_line}:{finding.category}"
