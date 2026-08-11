from agentic_code_reviewer.analysis.diff import (
    LineKind,
    diff_to_text,
    parse_diff,
)

SIMPLE = """\
diff --git a/db.py b/db.py
index abc..def 100644
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


def test_parse_single_file():
    files = parse_diff(SIMPLE)
    assert len(files) == 1
    f = files[0]
    assert f.path == "db.py"
    assert f.status == "modified"
    assert f.language == "python"
    assert not f.is_new and not f.is_deleted


def test_line_classification_and_numbers():
    f = parse_diff(SIMPLE)[0]
    hunk = f.hunks[0]
    assert hunk.old_start == 10 and hunk.new_start == 10
    kinds = [ln.kind for ln in hunk.lines]
    assert kinds == [LineKind.CONTEXT, LineKind.REMOVED, LineKind.REMOVED,
                     LineKind.ADDED, LineKind.ADDED, LineKind.CONTEXT]
    removed = [ln for ln in hunk.lines if ln.kind == LineKind.REMOVED]
    added = [ln for ln in hunk.lines if ln.kind == LineKind.ADDED]
    assert removed[0].old_line == 11
    assert removed[1].old_line == 12
    assert added[0].new_line == 11
    assert added[1].new_line == 12
    # The added SQL f-string line must be detected as changed.
    assert f.line_is_changed(11)
    assert 11 in f.changed_line_numbers


def test_added_and_deleted_files():
    added = """\
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+def hello():
+    return "world"
"""
    files = parse_diff(added)
    assert len(files) == 1
    assert files[0].is_new and files[0].status == "added"
    assert files[0].added_lines[0].new_line == 1

    deleted = """\
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def gone():
-    pass
"""
    files = parse_diff(deleted)
    assert len(files) == 1
    assert files[0].is_deleted and files[0].status == "deleted"


def test_multiple_files_and_roundtrip():
    multi = SIMPLE + """\
diff --git a/search.py b/search.py
--- a/search.py
+++ b/search.py
@@ -5,5 +5,5 @@ def find_first(items, threshold):
-    while low <= high:
+    while low < high:
"""
    files = parse_diff(multi)
    assert [f.path for f in files] == ["db.py", "search.py"]
    text = diff_to_text(files)
    assert "diff --git a/search.py b/search.py" in text
    assert "+    while low < high:" in text
    # Round-trip must re-parse without error.
    assert parse_diff(text)


def test_empty_input():
    assert parse_diff("") == []


def test_hunk_ranges():
    f = parse_diff(SIMPLE)[0]
    ranges = f.hunks[0].changed_ranges
    assert ranges, "expected at least one changed range"
    start, end = ranges[0]
    assert start <= end
    assert start == 10  # context line 10 begins the range
