"""Taint-analysis tests (spec: taint engine cases)."""

import ast

from agentic_code_reviewer.analysis.taint import taint_scan


def _results(src: str) -> list:
    return taint_scan("app.py", src.splitlines(), ast.parse(src))


def test_source_to_direct_sink():
    src = (
        "def handler(request):\n"
        '    name = request.args.get("name")\n'
        '    return os.system(name)\n'
    )
    kinds = {r.sink_kind for r in _results(src)}
    assert "shell" in kinds


def test_source_through_one_function_to_sink():
    src = (
        "def handler(request):\n"
        '    query = request.get("q", "")\n'
        "    return run_query(query)\n"
        "\n"
        "def run_query(q):\n"
        '    return db.execute(f"SELECT * FROM t WHERE n={q}")\n'
    )
    kinds = {r.sink_kind for r in _results(src)}
    assert "sql" in kinds


def test_source_through_multiple_functions_to_sink():
    src = (
        "def handler(request):\n"
        '    raw = request.get("u", "")\n'
        "    return stage2(raw)\n"
        "\n"
        "def stage2(value):\n"
        "    return stage3(value)\n"
        "\n"
        "def stage3(v):\n"
        "    return subprocess.run(v, shell=True)\n"
    )
    kinds = {r.sink_kind for r in _results(src)}
    assert "shell" in kinds


def test_sanitized_source_does_not_trigger_sink():
    src = (
        "import html\n"
        "def handler(request):\n"
        '    name = request.args.get("name")\n'
        "    safe = html.escape(name)\n"
        "    return render(safe)\n"
    )
    assert _results(src) == []


def test_untrusted_source_with_safe_parameterized_api():
    src = (
        "def handler(request):\n"
        '    name = request.args.get("name")\n'
        '    return db.execute("SELECT * FROM t WHERE n = %s", (name,))\n'
    )
    assert _results(src) == []


def test_path_traversal_through_file_open():
    src = (
        "def handler(request):\n"
        '    fname = request.files.get("f")\n'
        "    with open(fname) as f:\n"
        "        return f.read()\n"
    )
    kinds = {r.sink_kind for r in _results(src)}
    assert "fs" in kinds


def test_sensitive_data_leak_into_logs():
    src = (
        "def handler(request):\n"
        "    password = request.get(\"pw\")\n"
        "    logger.info(\"login failed for %s\", password)\n"
    )
    kinds = {r.sink_kind for r in _results(src)}
    assert "log" in kinds


def test_sql_f_string_construction_flagged():
    src = (
        "def handler(request):\n"
        '    q = request.args.get("q")\n'
        '    sql = f"SELECT * FROM users WHERE name LIKE \'%{q}%\'"\n'
        "    return db.execute(sql)\n"
    )
    kinds = {r.sink_kind for r in _results(src)}
    assert "sql" in kinds


def test_safe_code_has_no_taint_results():
    src = (
        "def helper(items):\n"
        "    return [i * 2 for i in items]\n"
        "\n"
        "def main():\n"
        "    result = helper([1, 2, 3])\n"
        "    return sum(result)\n"
    )
    assert _results(src) == []
