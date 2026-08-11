"""AST verification-rule tests (spec: AST verification)."""


from agentic_code_reviewer.analysis.ast_rules import ast_check
from agentic_code_reviewer.analysis.snapshot import RepositorySnapshot


def _check(src: str, category: str, line: int, *, removed_text: str = ""):
    lines = src.splitlines()
    snapshot = RepositorySnapshot({"x.py": src})
    return ast_check(category, "x.py", line, lines, snapshot, removed_text=removed_text)


def test_mutable_default_argument_detected():
    src = 'def f(items=[]):\n    return len(items)\n'
    hits = _check(src, "correctness", 1)
    assert any(rid == "ast:mutable-default" for rid, _ in hits)


def test_mutable_default_on_other_line_not_flagged():
    src = 'def f(items=[]):\n    x = 1\n    return x\n'
    hits = _check(src, "correctness", 2)
    assert not any(rid == "ast:mutable-default" for rid, _ in hits)


def test_dangerous_calls_detected():
    src = 'import os\nos.system(user_input)\n'
    hits = _check(src, "security", 2)
    assert any(rid == "ast:os-command" for rid, _ in hits)


def test_eval_detected():
    src = 'result = eval(expr)\n'
    hits = _check(src, "security", 1)
    assert any(rid == "ast:eval-exec" for rid, _ in hits)


def test_subprocess_shell_true_detected():
    src = 'import subprocess\nsubprocess.run(cmd, shell=True)\n'
    hits = _check(src, "security", 2)
    assert any(rid == "ast:subprocess-shell" for rid, _ in hits)


def test_sql_f_string_construction_detected():
    src = 'def f(query):\n    return db.execute(f"SELECT * FROM t WHERE n={query}")\n'
    hits = _check(src, "security", 2)
    assert any(rid == "ast:sql-f-string" for rid, _ in hits)


def test_sql_parameterized_not_flagged():
    src = 'def f(query):\n    return db.execute("SELECT * FROM t WHERE n=?", (query,))\n'
    hits = _check(src, "security", 2)
    assert not any(rid.startswith("ast:sql") for rid, _ in hits)


def test_bare_except_detected():
    src = 'try:\n    risky()\nexcept:\n    pass\n'
    hits = _check(src, "error_handling", 3)
    assert any(rid == "ast:bare-except" for rid, _ in hits)


def test_swallowed_exception_detected():
    src = 'try:\n    risky()\nexcept ValueError:\n    pass\n'
    hits = _check(src, "error_handling", 3)
    assert any(rid == "ast:swallowed-except" for rid, _ in hits)


def test_blocking_call_in_async_detected():
    src = 'import time\nasync def f():\n    time.sleep(1)\n'
    hits = _check(src, "performance", 3)
    assert any(rid == "ast:blocking-in-async" for rid, _ in hits)


def test_unchecked_index_detected():
    src = 'def f(items):\n    for i in range(10):\n        x = items[i + 1]\n    return x\n'
    hits = _check(src, "correctness", 3)
    assert any(rid == "ast:unchecked-index" for rid, _ in hits)


def test_len_bounded_loop_not_flagged():
    src = 'def f(items):\n    for i in range(len(items)):\n        x = items[i]\n    return x\n'
    hits = _check(src, "correctness", 3)
    assert not any(rid == "ast:unchecked-index" for rid, _ in hits)


def test_signature_change_detected_with_removed_text():
    src = 'def foo(x, timeout=30):\n    return x\n'
    removed = "def foo(x, timeout):\n    return x\n"
    hits = _check(src, "api_contract", 1, removed_text=removed)
    assert any(rid == "ast:signature-change" for rid, _ in hits)


def test_no_signature_change_when_identical():
    src = 'def foo(x, timeout=30):\n    return x\n'
    removed = "def foo(x, timeout=30):\n    return x\n"
    hits = _check(src, "api_contract", 1, removed_text=removed)
    assert not any(rid == "ast:signature-change" for rid, _ in hits)


def test_unused_import_detected_via_ast():
    src = "import os\nimport unused_mod\n\nx = os.getcwd()\n"
    hits = _check(src, "dead_code", 2)
    assert any(rid == "ast:unused-import" for rid, _ in hits)


def test_used_import_not_flagged():
    src = "import os\n\nx = os.getcwd()\n"
    hits = _check(src, "dead_code", 1)
    assert not any(rid == "ast:unused-import" for rid, _ in hits)


def test_ast_check_returns_empty_without_snapshot():
    lines = ["x = 1"]
    assert ast_check("security", "x.py", 1, lines, None) == []
