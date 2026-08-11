from __future__ import annotations

import pytest

from agentic_code_reviewer.config.settings import Settings

SQL_INJECTION_DIFF = """\
diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -10,7 +10,7 @@ def search_users(query):
     \"\"\"Return users whose name contains the query.\"\"\"
-    sql = "SELECT * FROM users WHERE name LIKE ?"
-    params = (f"%{query}%",)
+    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
+    params = ()
     return _db.execute(sql, params)
"""


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        LLM_PROVIDER="mock",
        EMBEDDING_PROVIDER="local",
        LOG_FORMAT="text",
        LOG_LEVEL="WARNING",
    )


@pytest.fixture
def sql_injection_diff() -> str:
    return SQL_INJECTION_DIFF


@pytest.fixture
def sample_repo_files() -> dict[str, str]:
    return {
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
