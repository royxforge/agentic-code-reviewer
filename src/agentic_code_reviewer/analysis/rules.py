"""Deterministic verification rules.

The verifier uses conservative, evidence-backed heuristics: a security finding
is only marked ``verified`` if a concrete risky pattern appears on a changed
line of the file it points at. Patterns are deliberately narrow to avoid
labelling benign code  -  absence of a pattern match means the finding stays at a
lower verification status, never that it is wrong.
"""

from __future__ import annotations

import re
from re import Pattern

from agentic_code_reviewer.analysis.category_rules import (
    CATEGORY_PATTERNS_EXTRA,
    SECURITY_PATTERNS_EXTRA,
)

SECURITY_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("sql-concat", re.compile(r"(execute|executemany|query|raw_sql|session\.execute)\s*\(\s*f?[\"']", re.IGNORECASE)),
    (
        "sql-f-string",
        re.compile(r"(cursor|conn|db|session)\.execute\(\s*f[\"']", re.IGNORECASE),
    ),
    (
        "sql-interpolated",
        re.compile(r"f[\"'][^\"]*(SELECT|INSERT|UPDATE|DELETE|DROP)[^\"]*%\{[^}]+\}[^\"]*[\"']", re.IGNORECASE),
    ),
    ("eval-exec", re.compile(r"\b(eval|exec)\s*\(", re.IGNORECASE)),
    ("os-system", re.compile(r"\bos\.system\s*\(")),
    (
        "subprocess-shell",
        re.compile(r"subprocess\s*\.\s*(call|run|Popen)\s*\([^)]*shell\s*=\s*True", re.IGNORECASE),
    ),
    ("shell-true", re.compile(r"shell\s*=\s*True", re.IGNORECASE)),
    ("inner-html", re.compile(r"(innerHTML|outerHTML|dangerouslySetInnerHTML)\s*=", re.IGNORECASE)),
    ("pickle-loads", re.compile(r"pickle\s*\.\s*loads?\s*\(")),
    ("yaml-load", re.compile(r"yaml\s*\.\s*load\s*\((?![^)]*Loader\s*=)[^)]*\)", re.IGNORECASE)),
    (
        "str-format-sql",
        re.compile(r"(SELECT|INSERT|UPDATE|DELETE).{0,200}%(s|d)\s*[,)]", re.IGNORECASE | re.DOTALL),
    ),
    (
        "path-join-user",
        re.compile(r"os\.path\.join\s*\(\s*[^,]+,\s*(request|user_input|name|filename|path)", re.IGNORECASE),
    ),
    ("subprocess-untrusted", re.compile(r"subprocess\s*\.\s*(call|run|Popen)\s*\([^)]*", re.IGNORECASE)),
    ("deserialize", re.compile(r"(loads|load)\s*\(\s*(request|data|body|content|input)", re.IGNORECASE)),
    (
        "hardcoded-secret",
        re.compile(r"(password|secret|api_key|token)\s*=\s*[\"'][A-Za-z0-9_\-]{8,}[\"']", re.IGNORECASE),
    ),
    ("crypto-md5", re.compile(r"hashlib\s*\.\s*md5\s*\(")),
    ("crypto-sha1", re.compile(r"hashlib\s*\.\s*sha1\s*\(")),
]

ERROR_HANDLING_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("bare-except", re.compile(r"^\s*except\s*:", re.MULTILINE)),
    (
        "swallowed-except",
        re.compile(r"except\s+[^:]+:\s*\n\s*(pass|return\s+None|continue|break)", re.MULTILINE),
    ),
    (
        "broad-except",
        re.compile(r"except\s+(Exception|BaseException|RuntimeError)\s*(as\s+\w+)?\s*:", re.MULTILINE),
    ),
    (
        "no-timeout-http",
        re.compile(r"(requests|httpx|urllib)\.(get|post|put|delete|request)\((?![^)]*timeout=)[^)]*\)", re.IGNORECASE),
    ),
    (
        "resource-open",
        re.compile(r"^\s*(?!with\b)(?:[A-Za-z_]\w*\s*=\s*)?\bopen\s*\([^)]*\)(?!\s*as\b)", re.IGNORECASE),
    ),
]

CORRECTNESS_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "off-by-one",
        re.compile(r"range\s*\(\s*[^)]*<\s*len\([^)]*\)\s*\)|range\s*\(\s*len\([^)]*\)\s*\+\s*1", re.MULTILINE),
    ),
    ("float-equality", re.compile(r"\b\d+\.\d+\b\s*==\s*[^=]|\b[a-z_]\w*\s*==\s*\d+\.\d+", re.MULTILINE)),
    ("assignment-in-condition", re.compile(r"if\s+[^=!<>]*(?:=|:=)[^=]", re.MULTILINE)),
    ("unchecked-index", re.compile(r"\[[^]]*\]\s*\n.{0,120}index|items\[[^]]*\]", re.MULTILINE)),
    ("mutable-default", re.compile(r"def\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|set\(\))\s*[,)]", re.MULTILINE)),
]

TESTING_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "skipped-test",
        re.compile(r"@pytest\.mark\.skip|pytest\.skip\s*\(|unittest\.skip|@unittest\.skip", re.MULTILINE),
    ),
    ("empty-test", re.compile(r"def\s+test_\w+\s*\([^)]*\):\s*\n\s*(pass|\.\.\.)", re.MULTILINE)),
    ("assert-true", re.compile(r"assert\s+[^=!<>]+\s*==\s*True|assertTrue\s*\(", re.MULTILINE)),
    ("time-independent", re.compile(r"assert\s+.{0,60}(time|datetime|now|date)\.", re.MULTILINE)),
]

PERFORMANCE_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "query-in-loop",
        re.compile(
            r"for\s+[^\n]*:\s*\n\s*[^\n]*\b(?:db|session|conn|cursor)\."
            r"[^\n]*\b(?:execute|filter|query|find|get)\s*\(",
            re.MULTILINE,
        ),
    ),
    # Query executed directly inside a loop body (one-liner form).
    (
        "query-in-loop-inline",
        re.compile(r"for\s+[^\n]*:\s*[^\n]*\.\b(?:filter|query|find)\s*\(", re.MULTILINE),
    ),
    (
        "blocking-in-async",
        re.compile(
            r"async\s+def\s+\w+[^\n]*:\s*\n\s*[^\n]*"
            r"\b(?:time\.sleep|requests\.|httpx\.|socket\.)\b",
            re.MULTILINE,
        ),
    ),
    (
        "blocking-in-async-inline",
        re.compile(
            r"async\s+def\s+\w+[^\n]*:\s*[^\n]*"
            r"\b(?:time\.sleep|requests\.)\b",
            re.MULTILINE,
        ),
    ),
    ("lru-cache-unbounded", re.compile(r"@lru_cache(?!\s*\(\s*maxsize)", re.MULTILINE)),
    (
        "len-in-loop-condition",
        re.compile(r"while\s+[^\n]*len\([^\n]*\)\s*[<>!=]+", re.MULTILINE),
    ),
]

MAINTAINABILITY_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("todo-fixme", re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)),
    (
        "module-mutable-global",
        re.compile(r"^[A-Z_]{2,}\s*=\s*(\[\]|\{\}|set\(\))", re.MULTILINE),
    ),
]

OBSERVABILITY_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("debug-print", re.compile(r"^\s*(?:\w+\s*=\s*)?print\s*\(", re.MULTILINE)),
    (
        "silent-except",
        re.compile(r"except\s+[^:]+:\s*\n\s*(pass|continue|return\s+None)", re.MULTILINE),
    ),
]

DATA_INTEGRITY_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "mutate-while-iterate",
        re.compile(
            r"for\s+[^\n]*:\s*\n\s*[^\n]*"
            r"\b(?:remove|pop|discard)\s*\s*",
            re.MULTILINE,
        ),
    ),
    ("destructive-alter", re.compile(r"\bDROP\s+(?:COLUMN|TABLE)\b|ALTER\s+TABLE[^\n]*\bDROP\b", re.IGNORECASE)),
    ("unchecked-json", re.compile(r"json\s*\.\s*loads?\s*\("""
                                r"\s*(?:request|data|body|content|input)\s*\)", re.IGNORECASE)),
]

ACCESSIBILITY_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("img-without-alt", re.compile(r"<img\b(?![^>]*\balt=)[^>]*>", re.IGNORECASE)),
    ("clickable-div", re.compile(r"<div\b[^>]*\bonclick=", re.IGNORECASE)),
    (
        "input-without-label",
        re.compile(
            r"<input\b(?![^>]*\btype=[\"']?(?:hidden|submit|button|reset)[\"']?)"
            r"(?![^>]*\b(?:aria-label|aria-labelledby)=)[^>]*>",
            re.IGNORECASE,
        ),
    ),
    (
        "onclick-without-keys",
        re.compile(
            r"<(?:div|span)\b[^>]*\bon(?:Click|click)\s*=\s*\{[^}]*\}"
            r"(?![^>]*\bonKeyDown)",
            re.IGNORECASE,
        ),
    ),
]

CONCURRENCY_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "check-then-act",
        re.compile(
            r"if\s+[^\n]*\b(?:in|not in)\s+[^\n]+:\s*\n\s*[^\n]*"
            r"\b(?:remove|pop|discard|add|append)\s*",
            re.MULTILINE,
        ),
    ),
    (
        "thread-pool-in-loop",
        re.compile(r"for\s+[^\n]*:\s*\n\s*[^\n]*ThreadPoolExecutor", re.MULTILINE),
    ),
    ("shared-counter", re.compile(r"^\s*\w+\s*\+=\s*1\s*$", re.MULTILINE)),
]

DEPENDENCIES_PATTERNS: list[tuple[str, Pattern[str]]] = [
    # A bare package name line in requirements.txt with no version specifier.
    ("unpinned-requirements", re.compile(r"^[A-Za-z0-9_.\-]+$", re.MULTILINE)),
    # A lower-bound-only range (no upper bound) in a dependency manifest.
    ("broad-version-range", re.compile(r"[\w.\-]+\s*>=\s*\d+(?:\.\d+)*")),
    ("npm-wildcard", re.compile(r"[\"'][^\"']+[\"']\s*:\s*[\"']\*[\"']")),
    ("npm-latest", re.compile(r"[\"'][^\"']+[\"']\s*:\s*[\"']latest[\"']", re.IGNORECASE)),
]

PRIVACY_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "log-sensitive-field",
        # The sensitive term must be used as a value (followed by =, :, } or a
        # closing paren), never a prose mention like "password reset email sent".
        re.compile(
            r"(?:logger|log|logging)\.\w+\([^)]*\b(?:password|passwd|api[_-]?key|"
            r"secret|token|ssn|credit[_-]?card|email)\b\s*[=:})]",
            re.IGNORECASE,
        ),
    ),
    (
        "sensitive-in-exception",
        re.compile(
            r"raise\s+\w+\([^)]*\b(?:password|api[_-]?key|secret|token|ssn)\b",
            re.IGNORECASE,
        ),
    ),
    ("vars-dump", re.compile(r"(?:print|logger\.\w+)\s*\([^)]*\b(?:vars|locals)\s*\(")),
]

I18N_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "hardcoded-date-format",
        re.compile(
            r"(?:format|formatDate|strftime|dayjs|moment)\([^)]*[\"'][^\"']*"
            r"(?:YYYY|YY|MM|DD|%Y|%m|%d)[^\"']*[\"']"
        ),
    ),
    # A JSX text node of real words  -  a hardcoded user-facing string.
    ("unlocalized-jsx-text", re.compile(r">\s*[A-Za-z][A-Za-z ]{8,}\s*<")),
    (
        "hardcoded-attr-string",
        re.compile(
            r"(?:label|title|placeholder|aria-label|alt)\s*=\s*[\"'][A-Za-z][^\"']{3,}[\"']"
        ),
    ),
]

API_CONTRACT_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("added-export", re.compile(r"^\s*export\s+(?:function|const|class|default|type|interface|\{)", re.MULTILINE)),
    ("added-route", re.compile(r"@\w*\.(?:get|post|put|delete|patch|route)\s*\(", re.IGNORECASE)),
    (
        "added-signature",
        re.compile(
            r"^\s*(?:def\s+\w+\s*\(|function\s+\w+\s*\(|[\w$.]+\s*=\s*\([^)]*\)\s*=>)",
            re.MULTILINE,
        ),
    ),
]

DEAD_CODE_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("import-statement", re.compile(r"^\s*(?:import|from)\s+\S+", re.MULTILINE)),
]

CATEGORY_PATTERNS: dict[str, list[tuple[str, Pattern[str]]]] = {
    "security": SECURITY_PATTERNS + SECURITY_PATTERNS_EXTRA,
    "error_handling": ERROR_HANDLING_PATTERNS,
    "correctness": CORRECTNESS_PATTERNS,
    "testing": TESTING_PATTERNS,
    "performance": PERFORMANCE_PATTERNS,
    "maintainability": MAINTAINABILITY_PATTERNS,
    "observability": OBSERVABILITY_PATTERNS,
    "data_integrity": DATA_INTEGRITY_PATTERNS,
    "accessibility": ACCESSIBILITY_PATTERNS,
    "concurrency": CONCURRENCY_PATTERNS,
    "dependencies": DEPENDENCIES_PATTERNS,
    "privacy": PRIVACY_PATTERNS,
    "i18n": I18N_PATTERNS,
    "api_contract": API_CONTRACT_PATTERNS,
    "dead_code": DEAD_CODE_PATTERNS,
    **CATEGORY_PATTERNS_EXTRA,
}


def match_category_patterns(category: str, line_text: str) -> list[str]:
    """Return the rule ids matching ``line_text`` for a category."""
    hits = []
    for rule_id, pattern in CATEGORY_PATTERNS.get(category, []):
        if pattern.search(line_text):
            hits.append(rule_id)
    return hits


def token_overlap(a: str, b: str) -> set[str]:
    """Identifier-level token overlap between two texts (case-insensitive)."""
    import re as _re

    tokens_a = {t.lower() for t in _re.findall(r"[A-Za-z_]\w{2,}", a)}
    tokens_b = {t.lower() for t in _re.findall(r"[A-Za-z_]\w{2,}", b)}
    return tokens_a & tokens_b
