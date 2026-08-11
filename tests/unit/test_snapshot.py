"""RepositorySnapshot tests: shared indexing (spec: performance + symbols)."""

from agentic_code_reviewer.analysis.snapshot import RepositorySnapshot

FILES = {
    "app.py": (
        "import os\n"
        "from db import search_users\n"
        "import helpers as h\n"
        "\n"
        "@app.get('/users')\n"
        "def handle(request):\n"
        '    q = request.get("q", "")\n'
        "    return search_users(q)\n"
    ),
    "db.py": (
        "def search_users(query):\n"
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        "    return _db.execute(sql, (query,))\n"
    ),
    "helpers.py": "def unused_helper():\n    return 1\n",
}


def test_file_and_ast_indexes():
    snap = RepositorySnapshot(FILES, changed_files=["app.py"])
    assert snap.repo_files["db.py"]
    assert snap._asts.get("db.py") is not None
    assert snap._asts.get("app.py") is not None


def test_symbol_definitions():
    snap = RepositorySnapshot(FILES)
    search = snap.symbols_named("search_users")
    assert any(d.kind == "function" for d in search)
    assert any(d.kind == "import" for d in snap.symbols_named("search_users"))
    # parameter symbols are indexed
    assert any(d.kind == "parameter" and d.name == "query" for d in snap.symbols_named("query"))


def test_callers_of_resolves_call_sites():
    snap = RepositorySnapshot(FILES)
    callers = snap.callers_of("search_users")
    assert any(path == "app.py" for path, _ in callers)
    # the definition site itself is excluded
    assert all(not (p == "db.py" and ln <= 1) for p, ln in callers)


def test_signature_change_without_callers_has_no_breakage_evidence():
    # Spec: "Do NOT report a breaking API merely because a signature changed".
    # A function that changed its signature but has zero call sites gives the
    # api-contract layer no caller evidence to classify as CALLER_BREAKAGE.
    files = {
        "lib.py": "def foo(x, timeout=30):\n    return x\n",
        "app.py": "x = 1\n",
    }
    snap = RepositorySnapshot(files)
    assert snap.callers_of("foo") == []
    assert snap.definitions_in_file("lib.py")  # the definition still exists


def test_signature_change_with_callers_exposes_breakage_evidence():
    files = {
        "lib.py": "def foo(x, timeout=30):\n    return x\n",
        "app.py": "def run():\n    return foo(1, timeout=30)\n",
    }
    snap = RepositorySnapshot(files)
    callers = snap.callers_of("foo")
    assert any(path == "app.py" for path, _ in callers)


def test_route_index():
    snap = RepositorySnapshot(FILES)
    routes = snap.routes()
    assert any(r.handler == "handle" and r.method == "get" and r.path == "/users" for r in routes)


def test_env_vars_indexed():
    snap = RepositorySnapshot({"c.py": 'import os\nx = os.environ.get("MY_SECRET_KEY")\ny = os.getenv("OTHER")\n'})
    assert snap.env_vars() == {"MY_SECRET_KEY", "OTHER"}


def test_added_imports_on_changed_lines():
    content = "import os\nimport sys\n\nx = 1\n"
    diff = "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1,2 +1,3 @@\n import os\n+import sys\n x = 1\n"
    from agentic_code_reviewer.analysis.diff import parse_diff

    changed = set(parse_diff(diff)[0].changed_line_numbers)
    snap = RepositorySnapshot({"c.py": content}, changed_files=["c.py"])
    imports = snap.added_imports("c.py", changed)
    assert ("sys", 2) in imports
    assert ("os", 1) not in imports  # os is a context line, not added


def test_unused_import_detection():
    content = "import os\nimport sys\n\nx = os.getcwd()\n"
    diff = "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1,2 +1,3 @@\n import os\n+import sys\n x = 1\n"
    from agentic_code_reviewer.analysis.diff import parse_diff

    changed = set(parse_diff(diff)[0].changed_line_numbers)
    snap = RepositorySnapshot({"c.py": content}, changed_files=["c.py"])
    added = snap.added_imports("c.py", changed)
    assert ("sys", 2) in added
    refs = snap.references_named("sys")
    assert not refs  # sys is never loaded


def test_snapshot_never_raises_on_bad_files():
    bad = {"broken.py": "def ( :\n", "a.py": "ok = 1\n"}
    snap = RepositorySnapshot(bad)
    assert snap.repo_files["broken.py"]  # content kept
    assert snap._asts.get("broken.py") is None  # parse skipped
    assert snap._asts.get("a.py") is not None


def test_history_degrades_gracefully_without_local_path():
    snap = RepositorySnapshot(FILES, changed_files=["app.py"])
    assert snap.history_for("app.py") == []
    assert snap.blame_for("app.py", 1) == ""


def test_history_for_unknown_git_dir_is_empty():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snap = RepositorySnapshot(FILES, changed_files=["app.py"], local_path=tmp, history_enabled=True)
        assert snap.history_for("app.py") == []


def test_index_sharing_has_index_flag():
    snap = RepositorySnapshot(FILES)
    assert snap.has_index() is True
    empty = RepositorySnapshot({"readme.md": "no python here"})
    assert empty.has_index() is False
