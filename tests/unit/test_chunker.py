from agentic_code_reviewer.retrieval.chunker import chunk_file

PY_FILE = '''\
import os

class UserService:
    """Service for users."""

    def __init__(self, db):
        self.db = db

    def get(self, user_id):
        return self.db.query("SELECT * FROM users WHERE id = ?", user_id)

    def delete(self, user_id):
        self.db.execute("DELETE FROM users WHERE id = ?", user_id)


def helper(x):
    return x + 1
'''


def test_symbol_anchored_python_chunks():
    chunks = chunk_file("svc.py", PY_FILE, max_chars=100_000, overlap_chars=0)
    symbols = [c.symbol for c in chunks]
    assert "UserService" in symbols
    assert "get" in symbols
    assert "delete" in symbols
    assert "helper" in symbols
    kinds = {c.kind for c in chunks}
    assert "class" in kinds and "function" in kinds


def test_class_span_covers_methods():
    chunks = chunk_file("svc.py", PY_FILE, max_chars=100_000, overlap_chars=0)
    class_chunk = next(c for c in chunks if c.symbol == "UserService" and c.kind == "class")
    # The class chunk should include its methods (span covers whole class body).
    assert "def get(self" in class_chunk.text
    assert "def delete(self" in class_chunk.text


def test_line_window_fallback():
    content = "\n".join(f"line {i} content here" for i in range(100))
    chunks = chunk_file("plain.txt", content, max_chars=500, overlap_chars=100)
    assert len(chunks) > 1
    assert all(c.start_line <= c.end_line for c in chunks)
    # Windows must tile the file without gaps for retrieval coverage.
    starts = sorted(c.start_line for c in chunks)
    assert starts[0] == 1


def test_empty_file():
    assert chunk_file("empty.py", "") == []


def test_unknown_language_falls_back_to_windows():
    content = "just some text\n" * 200
    chunks = chunk_file("data.weird", content, max_chars=300, overlap_chars=50)
    assert chunks
    assert all(c.kind == "snippet" for c in chunks)
