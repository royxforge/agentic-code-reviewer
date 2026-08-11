from agentic_code_reviewer.analysis.rules import match_category_patterns, token_overlap
from agentic_code_reviewer.evaluation.benchmark import BenchmarkEntry
from agentic_code_reviewer.evaluation.metrics import (
    aggregate,
    finding_matches,
    score_entry,
)
from agentic_code_reviewer.models.findings import ReviewFinding


def test_security_patterns():
    hits = match_category_patterns(
        "security", 'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"'
    )
    assert any(r in hits for r in ("sql-f-string", "sql-concat", "sql-interpolated"))
    assert "sql-interpolated" in hits
    assert match_category_patterns("security", "return cart_total(items)") == []
    assert "eval-exec" in match_category_patterns("security", "result = eval(data)")
    assert "shell-true" in match_category_patterns(
        "security", "subprocess.run(cmd, shell=True)"
    )


def test_error_handling_patterns():
    assert "bare-except" in match_category_patterns("error_handling", "except:\n    pass")
    assert "swallowed-except" in match_category_patterns(
        "error_handling", "except ValueError:\n    pass"
    )
    assert match_category_patterns("error_handling", "except ValueError:\n    raise") == []


def test_performance_patterns():
    query_in_loop = """for user in users:
    db.query(User).get(user.id)"""
    hits = match_category_patterns("performance", query_in_loop)
    assert "query-in-loop" in hits
    blocking_async = """async def fetch():
    time.sleep(1)"""
    assert "blocking-in-async" in match_category_patterns("performance", blocking_async)
    assert "lru-cache-unbounded" in match_category_patterns(
        "performance", "@lru_cache\ndef f(): ..."
    )
    assert match_category_patterns("performance", "return sum(items)") == []


def test_maintainability_and_observability_patterns():
    assert "todo-fixme" in match_category_patterns("maintainability", "# TODO: handle edge")
    assert "module-mutable-global" in match_category_patterns(
        "maintainability", "CACHE = {}"
    )
    assert "debug-print" in match_category_patterns("observability", "print(debug)")
    silent = """except ValueError:
    pass"""
    assert "silent-except" in match_category_patterns("observability", silent)
    assert match_category_patterns("observability", "logger.error('boom')") == []


def test_data_integrity_patterns():
    assert "destructive-alter" in match_category_patterns(
        "data_integrity", "ALTER TABLE users DROP COLUMN email"
    )
    mutate = """for item in items:
    items.remove(item)"""
    assert "mutate-while-iterate" in match_category_patterns("data_integrity", mutate)


def test_accessibility_patterns():
    assert "img-without-alt" in match_category_patterns(
        "accessibility", "<img src='/logo.png'>"
    )
    assert "img-without-alt" not in match_category_patterns(
        "accessibility", "<img src='/logo.png' alt='logo'>"
    )
    assert "clickable-div" in match_category_patterns(
        "accessibility", "<div onClick={handler}>"
    )


def test_concurrency_patterns():
    check_then_act = """if key in cache:
    cache.remove(key)"""
    assert "check-then-act" in match_category_patterns("concurrency", check_then_act)
    pool_in_loop = """for url in urls:
    ThreadPoolExecutor().submit(f)"""
    assert "thread-pool-in-loop" in match_category_patterns("concurrency", pool_in_loop)


def test_dependencies_patterns():
    assert "unpinned-requirements" in match_category_patterns(
        "dependencies", "requests"
    )
    assert match_category_patterns("dependencies", "requests==2.31.0") == []
    assert "broad-version-range" in match_category_patterns(
        "dependencies", "click>=8.0"
    )
    assert "npm-latest" in match_category_patterns(
        "dependencies", '"lodash": "latest"'
    )
    assert "npm-wildcard" in match_category_patterns(
        "dependencies", '"lodash": "*"'
    )
    assert match_category_patterns("dependencies", "return load(items)") == []


def test_privacy_patterns():
    assert "log-sensitive-field" in match_category_patterns(
        "privacy", 'logger.info(f"user email={user.email}")'
    )
    assert "log-sensitive-field" not in match_category_patterns(
        "privacy", "logger.info('request completed')"
    )
    assert "sensitive-in-exception" in match_category_patterns(
        "privacy", 'raise AuthError(f"token {token} invalid")'
    )
    assert "vars-dump" in match_category_patterns("privacy", "print(vars(user))")


def test_i18n_patterns():
    assert "hardcoded-date-format" in match_category_patterns(
        "i18n", 'format(date, "YYYY-MM-DD")'
    )
    assert "unlocalized-jsx-text" in match_category_patterns(
        "i18n", ">Save your changes<"
    )
    assert "hardcoded-attr-string" in match_category_patterns(
        "i18n", 'placeholder="Search projects"'
    )
    assert match_category_patterns("i18n", 't("save.changes")') == []


def test_api_contract_patterns():
    assert "added-export" in match_category_patterns(
        "api_contract", "export function run() {}"
    )
    assert "added-route" in match_category_patterns(
        "api_contract", '@app.post("/users")'
    )
    assert "added-signature" in match_category_patterns(
        "api_contract", "def fetch_users(limit: int) -> list[User]:"
    )
    assert match_category_patterns("api_contract", "x = compute(items)") == []


def test_dead_code_patterns():
    assert "import-statement" in match_category_patterns(
        "dead_code", "import unused_module"
    )
    assert match_category_patterns("dead_code", "print(x)") == []


def test_token_overlap():
    assert token_overlap("SQL injection in search_users", "search_users builds sql") >= {"sql"}


def _entry(**overrides):
    base = dict(
        entry_id="e1",
        repository="o/r",
        commit="abc",
        diff="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-x\n+y\n",
        affected_files=["a.py"],
        bug_description="SQL injection: user input interpolated into SQL query",
        bug_fix="use parameterized queries",
        category="security",
        severity="high",
    )
    base.update(overrides)
    return BenchmarkEntry(**base)


def _finding(**overrides):
    base = dict(
        category="security",
        severity="high",
        confidence=0.9,
        title="SQL injection",
        description="user-controlled query interpolated into sql statement",
        file_path="a.py",
        start_line=2,
    )
    base.update(overrides)
    return ReviewFinding(**base)


def test_finding_matches_gold():
    entry = _entry()
    assert finding_matches(entry, _finding())
    # Wrong file -> no match.
    assert not finding_matches(entry, _finding(file_path="b.py"))
    # Wrong topic -> no match.
    assert not finding_matches(entry, _finding(title="Unrelated formatting", description="style"))


def test_score_entry_and_aggregate():
    entry = _entry()
    score = score_entry(
        entry,
        [_finding(), _finding(title="Style nit", description="line length")],
        system="agentic",
        latency_seconds=12.0,
        cost_usd=0.05,
        tokens=1000,
    )
    assert score.true_positives == 1
    assert score.false_positives == 1
    assert score.detected
    assert score.severity_correct  # finding severity matches entry severity

    miss = score_entry(entry, [], system="agentic")
    assert not miss.detected

    metrics = aggregate([score, miss])
    assert metrics.tp == 1
    assert metrics.fp == 1
    assert metrics.fn == 1
    assert metrics.detected == 1
    assert metrics.entries == 2
    assert metrics.precision == 0.5
    assert metrics.recall == 0.5
    assert metrics.f1 == 0.5
    assert metrics.bug_detection_rate == 0.5
    assert metrics.completion_rate == 1.0


def test_negative_entry_produces_false_positives():
    entry = _entry(bug_description="", category="", severity="")
    score = score_entry(entry, [_finding(), _finding()], system="agentic")
    assert score.true_positives == 0
    assert score.false_positives == 2
    assert not score.detected
