"""Prompt regression suite.

Golden tests that pin known findings for the canonical fixture diffs through
the FULL workflow (planner -> agents -> verifier -> aggregator) using the
deterministic AdaptiveMock. If a prompt edit silently drops a confirmed finding
(or a prompt edit breaks rendering), these tests fail - the tripwire for
prompt-template and prompt-assembly changes.

The mocks are deterministic and offline; nothing here calls a real provider.
"""

from __future__ import annotations

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.orchestration.workflow import Workflow
from tests.integration.helpers import AdaptiveMock


def _settings(**overrides) -> Settings:
    base = {
        "LLM_PROVIDER": "mock",
        "EMBEDDING_PROVIDER": "local",
        "LOG_FORMAT": "text",
        "LOG_LEVEL": "WARNING",
    }
    base.update(overrides)
    return Settings(**base)


SQL_INJECTION_DIFF = """\
diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -9,7 +10,7 @@ def search_users(query):
     \"\"\"Return users whose name contains the query.\"\"\"
-    sql = "SELECT * FROM users WHERE name LIKE ?"
+    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
     return _db.execute(sql, params)
"""

REPO_FILES = {
    "db.py": (
        'import sqlite3\n\n_db = None\n\n\ndef init(path):\n'
        '    global _db\n    _db = sqlite3.connect(path)\n\n\n'
        'def search_users(query):\n'
        '    """Return users whose name contains the query."""\n'
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    params = (f"%{query}%",)\n'
        '    return _db.execute(sql, params)\n'
    ),
    "app.py": "from db import search_users\n\n\ndef handle(request):\n"
    '    q = request.get("q", "")\n    return search_users(q)\n',
}


def _request(diff: str, repo_files: dict[str, str], *, description: str = "") -> ReviewRequest:
    return ReviewRequest(
        repository="o/r",
        diff_text=diff,
        changed_files=["db.py", "app.py"],
        repo_files=repo_files,
        source="inline",
        description=description,
    )


def _run(diff: str, repo_files: dict[str, str], **settings_overrides):
    workflow = Workflow(_settings(**settings_overrides), client=AdaptiveMock())
    return workflow.run(_request(diff, repo_files))


def test_sql_injection_finding_survives_full_workflow():
    """The security agent's SQL-injection finding must reach the final review."""
    result = _run(SQL_INJECTION_DIFF, REPO_FILES)
    titles = [f.title for f in result.review.findings]
    assert any("SQL injection" in t for t in titles), titles


def test_security_finding_is_evidence_confirmed():
    """The golden security finding must be backed by deterministic evidence."""
    result = _run(SQL_INJECTION_DIFF, REPO_FILES)
    security = [f for f in result.review.findings if f.category == "security"]
    assert security
    assert security[0].evidence_status.value in ("confirmed", "strongly_supported")


def test_knowledge_injection_does_not_break_golden_finding(tmp_path):
    """Injecting knowledge must not suppress or corrupt the golden finding."""
    from agentic_code_reviewer.knowledge.models import KnowledgeEntry, KnowledgeKind
    from agentic_code_reviewer.knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path)
    store.add(
        KnowledgeEntry(
            kind=KnowledgeKind.RULE,
            title="Prefer SQLAlchemy",
            content="This repo standardizes on SQLAlchemy for data access.",
            repository="o/r",
            category="security",
        )
    )
    workflow = Workflow(_settings(), client=AdaptiveMock(), knowledge_store=store)
    result = workflow.run(_request(SQL_INJECTION_DIFF, REPO_FILES))
    titles = [f.title for f in result.review.findings]
    assert any("SQL injection" in t for t in titles), titles


def test_dismissal_suppresses_golden_finding(tmp_path):
    """A dismissed entry from a past review suppresses the same finding."""
    from agentic_code_reviewer.knowledge.dismiss import finding_to_dismissal_entry
    from agentic_code_reviewer.knowledge.store import KnowledgeStore
    from agentic_code_reviewer.models.findings import (
        EvidenceStatus,
        ReviewFinding,
        Severity,
    )

    store = KnowledgeStore(tmp_path)
    dismissed = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection via interpolated query",
        description="User-controlled query interpolated into SQL with an f-string.",
        evidence='sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
        recommendation="Use a parameterized query.",
        repository="o/r",
        file_path="db.py",
        start_line=11,
        evidence_status=EvidenceStatus.CONFIRMED,
    )
    store.add(finding_to_dismissal_entry(dismissed, "o/r"))

    workflow = Workflow(_settings(), client=AdaptiveMock(), knowledge_store=store)
    result = workflow.run(_request(SQL_INJECTION_DIFF, REPO_FILES))
    titles = [f.title for f in result.review.findings]
    assert not any("SQL injection" in t for t in titles), titles


def test_clean_diff_has_no_security_finding():
    """A docs-only change must produce no security finding (no hallucination)."""
    docs_diff = """\
diff --git a/readme.md b/readme.md
--- a/readme.md
+++ b/readme.md
@@ -1,1 +1,1 @@
-# Project
+# Project 2
"""
    result = _run(docs_diff, {"readme.md": "# Project 2\n"})
    assert not [f for f in result.review.findings if f.category == "security"]
