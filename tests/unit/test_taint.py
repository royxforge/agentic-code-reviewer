"""Taint-analysis tests (spec: taint engine cases)."""

import ast

from agentic_code_reviewer.analysis.taint import (
    build_cross_file_summaries,
    taint_scan,
)


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


# ---------------------------------------------------------------------------
# Cross-file taint
# ---------------------------------------------------------------------------


def _cross_results(
    changed_src: str,
    other_modules: dict[str, str],
    changed_path: str = "app.py",
) -> list:
    """Taint-scan ``changed_src`` with summaries built from ``other_modules``."""
    asts: dict[str, ast.Module] = {}
    for path, src in other_modules.items():
        asts[path] = ast.parse(src)
    summaries = build_cross_file_summaries(asts)
    return taint_scan(
        changed_path,
        changed_src.splitlines(),
        ast.parse(changed_src),
        cross_file_summaries=summaries,
    )


def test_cross_file_helper_to_sink():
    """A tainted value passed to a helper defined in another module hits the sink."""
    handler = (
        "from queries import run_query\n"
        "def handler(request):\n"
        '    q = request.get("q", "")\n'
        "    return run_query(q)\n"
    )
    queries = (
        "def run_query(sql):\n"
        '    return db.execute(f"SELECT * FROM t WHERE n={sql}")\n'
    )
    kinds = {r.sink_kind for r in _cross_results(handler, {"queries.py": queries})}
    assert "sql" in kinds


def test_cross_file_chain_two_helpers():
    """Taint flows through multiple helpers across modules to a shell sink."""
    handler = (
        "from helpers import first\n"
        "def handler(request):\n"
        '    raw = request.get("u", "")\n'
        "    return first(raw)\n"
    )
    helpers = (
        "def first(v):\n"
        "    return second(v)\n"
        "\n"
        "def second(x):\n"
        "    return subprocess.run(x, shell=True)\n"
    )
    kinds = {r.sink_kind for r in _cross_results(handler, {"helpers.py": helpers})}
    assert "shell" in kinds


def test_cross_file_returns_param_keeps_taint():
    """A helper that returns its argument keeps the taint flowing onward."""
    handler = (
        "from util import passthrough\n"
        "def handler(request):\n"
        '    name = request.get("n", "")\n'
        "    safe = passthrough(name)\n"
        "    return os.system(safe)\n"
    )
    util = "def passthrough(x):\n    return x\n"
    kinds = {r.sink_kind for r in _cross_results(handler, {"util.py": util})}
    assert "shell" in kinds


def test_cross_file_without_summaries_stays_silent():
    """Without cross-file summaries the same flow is NOT reported (honest scope)."""
    handler = (
        "from queries import run_query\n"
        "def handler(request):\n"
        '    q = request.get("q", "")\n'
        "    return run_query(q)\n"
    )
    assert _results(handler) == []


def test_cross_file_sanitizer_in_helper_clears_taint():
    """A sanitizer inside the cross-file helper stops the flow."""
    handler = (
        "from util import scrub\n"
        "def handler(request):\n"
        '    q = request.get("q", "")\n'
        "    return scrub(q)\n"
    )
    util = (
        "def scrub(v):\n"
        "    safe = v.replace(';', '')\n"
        "    return db.execute('SELECT 1')\n"
    )
    # The helper's own body reaches no sink with its parameter: nothing reported.
    assert _cross_results(handler, {"util.py": util}) == []
