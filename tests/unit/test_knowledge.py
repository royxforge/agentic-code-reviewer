from __future__ import annotations

import pytest

from agentic_code_reviewer.analysis.diff import diff_text_for_category, parse_diff
from agentic_code_reviewer.knowledge.capture import capture_review_findings, finding_to_entry
from agentic_code_reviewer.knowledge.dismiss import (
    finding_matches_dismissal,
    finding_to_dismissal_entry,
    suppressed_by_dismissals,
)
from agentic_code_reviewer.knowledge.models import KnowledgeEntry, KnowledgeKind
from agentic_code_reviewer.knowledge.render import render_knowledge
from agentic_code_reviewer.knowledge.store import KnowledgeStore
from agentic_code_reviewer.models.findings import (
    EvidenceStatus,
    ReviewFinding,
    Severity,
)


@pytest.fixture
def store(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(tmp_path)


def _entry(**kw) -> KnowledgeEntry:
    defaults = {
        "kind": KnowledgeKind.RULE,
        "title": "Use parameterized SQL",
        "content": "Never interpolate user input into SQL strings; use ? placeholders.",
        "repository": "o/r",
        "category": "security",
        "tags": ["sql", "security"],
    }
    defaults.update(kw)
    return KnowledgeEntry(**defaults)


def test_add_and_entries_roundtrip(store):
    store.add(_entry())
    entries = store.entries("o/r")
    assert len(entries) == 1
    assert entries[0].title == "Use parameterized SQL"
    assert entries[0].repository == "o/r"


def test_add_deduplicates_by_content(store):
    store.add(_entry())
    store.add(_entry())
    assert len(store.entries("o/r")) == 1


def test_global_entries_apply_to_all_repos(store):
    store.add(_entry(repository=""))
    assert len(store.entries("o/r")) == 1
    assert len(store.entries("other/repo")) == 1


def test_repo_scoped_entries_do_not_leak(store):
    store.add(_entry(repository="o/r"))
    assert len(store.entries("other/repo")) == 0


def test_remove_entry(store):
    entry = _entry()
    store.add(entry)
    assert store.remove("o/r", entry.entry_id()) is True
    assert store.entries("o/r") == []
    assert store.remove("o/r", entry.entry_id()) is False


def test_search_ranks_by_token_overlap(store):
    store.add(_entry())  # security / sql
    store.add(
        _entry(
            title="Auth middleware order",
            content="Run authentication before authorization checks.",
            category="authorization",
        )
    )
    hits = store.search("sql injection parameterized", "o/r", top_k=2)
    assert hits and hits[0].title == "Use parameterized SQL"


def test_search_category_boost(store):
    store.add(_entry())  # security
    store.add(
        _entry(
            title="Auth middleware order",
            content="Run authentication before authorization checks.",
            category="authorization",
        )
    )
    hits = store.search("middleware checks", "o/r", top_k=2, category="authorization")
    assert hits and hits[0].category == "authorization"


def test_render_knowledge_empty_when_nothing_matches(store):
    assert render_knowledge([]) == "(no knowledgebase entries for this repository)"


def test_render_knowledge_compact_and_capped(store):
    entry = _entry()
    block = render_knowledge([entry], max_chars=200)
    assert "Use parameterized SQL" in block
    assert len(block) <= 200


def test_render_knowledge_respects_repo_scope(store):
    entry = _entry(repository="o/r")
    block = render_knowledge([entry], repository="o/r")
    assert "Use parameterized SQL" in block
    block_other = render_knowledge([entry], repository="other/r")
    assert "Use parameterized SQL" not in block_other


def test_finding_to_entry_carries_category_and_recommendation():
    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input interpolated into SQL.",
        evidence="sql = f\"...{query}...\"",
        recommendation="Use parameterized queries.",
        file_path="db.py",
        start_line=11,
    )
    entry = finding_to_entry(finding, "o/r")
    assert entry.kind == KnowledgeKind.FINDING
    assert entry.category == "security"
    assert "Use parameterized queries" in entry.content
    assert entry.repository == "o/r"


def test_finding_to_dismissal_entry_roundtrip(store):
    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection via interpolated query",
        description="User input interpolated into SQL.",
        evidence='sql = f"...{query}..."',
        recommendation="Use parameterized queries.",
        file_path="db.py",
    )
    entry = finding_to_dismissal_entry(finding, "o/r")
    assert entry.kind == KnowledgeKind.DISMISSED
    assert entry.category == "security"
    assert entry.source_file == "db.py"
    store.add(entry)
    stored = [e for e in store.entries("o/r") if e.kind == KnowledgeKind.DISMISSED]
    assert len(stored) == 1


def test_dismissal_matches_same_file_and_category():
    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input interpolated into SQL.",
        file_path="db.py",
    )
    entry = finding_to_dismissal_entry(finding, "o/r")
    assert finding_matches_dismissal(finding, entry, repository="o/r")


def test_dismissal_ignores_different_repo():
    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input interpolated into SQL.",
        file_path="db.py",
    )
    entry = finding_to_dismissal_entry(finding, "o/r")
    other = finding.model_copy(update={"repository": "other/r"})
    assert not finding_matches_dismissal(other, entry, repository="other/r")


def test_dismissal_does_not_match_different_category():
    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input interpolated into SQL.",
        file_path="db.py",
    )
    entry = finding_to_dismissal_entry(finding, "o/r")
    other = finding.model_copy(update={"category": "performance"})
    assert not finding_matches_dismissal(other, entry, repository="o/r")


def test_suppressed_by_dismissals_filters_only_matches():
    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input interpolated into SQL.",
        file_path="db.py",
    )
    unrelated = ReviewFinding(
        category="performance",
        severity=Severity.LOW,
        confidence=0.7,
        title="N+1 query",
        description="Loop executes a query per iteration.",
        file_path="repo.py",
    )
    dismissal = finding_to_dismissal_entry(finding, "o/r")
    kept = suppressed_by_dismissals(
        [finding, unrelated], [dismissal], repository="o/r"
    )
    assert kept == [unrelated]


def test_capture_only_persists_verified_severe_findings(store):
    verified = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input interpolated into SQL.",
        evidence="sql = f\"...\"",
        recommendation="Parameterize.",
        evidence_status=EvidenceStatus.CONFIRMED,
    )
    unverified = verified.model_copy(
        update={"title": "Unverified", "evidence_status": EvidenceStatus.INSUFFICIENT_EVIDENCE}
    )
    low = verified.model_copy(update={"title": "Low", "severity": Severity.LOW})

    written = capture_review_findings(store, [verified, unverified, low], "o/r")
    assert written == 1
    titles = {e.title for e in store.entries("o/r")}
    assert "[high] SQL injection" in titles
    assert "Unverified" not in titles
    assert "Low" not in titles


SQL_INJECTION_DIFF = """\
diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -9,7 +10,7 @@ def search_users(query):
     \"\"\"Return users whose name contains the query.\"\"\"
-    sql = "SELECT * FROM users WHERE name LIKE ?"
+    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
     return _db.execute(sql, params)
diff --git a/readme.txt b/readme.txt
--- a/readme.txt
+++ b/readme.txt
@@ -1,1 +1,1 @@
-docs
+docs updated
"""


def test_diff_text_for_category_keeps_relevant_files():
    files = parse_diff(SQL_INJECTION_DIFF)
    security_text = diff_text_for_category(files, "security")
    assert "db.py" in security_text
    assert "readme.txt" not in security_text  # no security pattern in the doc hunk


def test_diff_text_for_category_falls_back_to_full():
    files = parse_diff(SQL_INJECTION_DIFF)
    testing_text = diff_text_for_category(files, "testing")
    # No testing pattern matched, so the agent still sees the whole change.
    assert "db.py" in testing_text
    assert "readme.txt" in testing_text


def test_diff_text_for_category_unknown_category_full():
    files = parse_diff(SQL_INJECTION_DIFF)
    text = diff_text_for_category(files, "requirement_alignment")
    assert "db.py" in text and "readme.txt" in text


def test_diff_text_for_category_max_chars_truncates():
    files = parse_diff(SQL_INJECTION_DIFF)
    text = diff_text_for_category(files, "security", max_chars=80)
    assert len(text) <= 80


# ---------------------------------------------------------------------------
# Workflow integration
# ---------------------------------------------------------------------------


def _request(sql_diff: str, repo_files: dict[str, str]) -> object:
    from agentic_code_reviewer.orchestration.state import ReviewRequest

    return ReviewRequest(
        repository="o/r",
        diff_text=sql_diff,
        changed_files=["db.py", "app.py"],
        repo_files=repo_files,
        source="inline",
    )


def test_workflow_injects_knowledge_into_analysis_prompts(tmp_path):
    """A relevant knowledge entry lands in the security agent's prompt."""
    from agentic_code_reviewer.config.settings import Settings
    from agentic_code_reviewer.orchestration.workflow import Workflow
    from tests.integration.helpers import AdaptiveMock

    store = KnowledgeStore(tmp_path)
    store.add(
        KnowledgeEntry(
            kind=KnowledgeKind.RULE,
            title="Never use f-string SQL",
            content="This repo requires parameterized SQL everywhere (past finding).",
            repository="o/r",
            category="security",
        )
    )

    seen: list[str] = []

    class RecordingMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            content = "\n".join(m.get("content", "") for m in messages)
            from agentic_code_reviewer.llm.client import current_stage

            if current_stage.get() == "security":
                seen.append(content)
            return super().complete(messages, **kwargs)

    settings = Settings(
        LLM_PROVIDER="mock",
        EMBEDDING_PROVIDER="local",
        LOG_FORMAT="text",
        LOG_LEVEL="WARNING",
        KNOWLEDGE_ENABLED="true",
    )
    workflow = Workflow(settings, client=RecordingMock(), knowledge_store=store)
    workflow.run(
        _request(SQL_INJECTION_DIFF, {
            "db.py": 'def search_users(query):\n    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n',
            "app.py": "from db import search_users\n",
        })
    )
    assert seen, "security agent should have run"
    assert "Never use f-string SQL" in seen[0]


def test_workflow_auto_captures_verified_findings(tmp_path):
    """KNOWLEDGE_AUTO_CAPTURE=true persists verified findings to the store."""
    from agentic_code_reviewer.config.settings import Settings
    from agentic_code_reviewer.orchestration.workflow import Workflow
    from tests.integration.helpers import AdaptiveMock

    store = KnowledgeStore(tmp_path)
    settings = Settings(
        LLM_PROVIDER="mock",
        EMBEDDING_PROVIDER="local",
        LOG_FORMAT="text",
        LOG_LEVEL="WARNING",
        KNOWLEDGE_AUTO_CAPTURE="true",
    )
    workflow = Workflow(settings, client=AdaptiveMock(), knowledge_store=store)
    workflow.run(
        _request(SQL_INJECTION_DIFF, {
            "db.py": 'def search_users(query):\n    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n',
            "app.py": "from db import search_users\n",
        })
    )
    entries = store.entries("o/r")
    assert entries, "expected captured knowledge entries"
    assert any(e.kind == KnowledgeKind.FINDING for e in entries)
