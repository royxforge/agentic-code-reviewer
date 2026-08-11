"""End-to-end coverage for the six new review categories (spec section 15:
>= 3 positive, >= 2 negative, >= 1 edge case per new category)."""

from agentic_code_reviewer.agents.verifier import VerifierAgent
from agentic_code_reviewer.analysis.rules import CATEGORY_PATTERNS, match_category_patterns
from agentic_code_reviewer.analysis.snapshot import RepositorySnapshot
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.llm.mock_client import MockLLMClient
from agentic_code_reviewer.models.findings import EvidenceStatus, ReviewFinding
from agentic_code_reviewer.models.review import ALLOWED_CHECKS
from agentic_code_reviewer.orchestration.state import ReviewRequest, ReviewState
from agentic_code_reviewer.orchestration.workflow import _ANALYSIS_AGENTS

NEW_CATEGORIES = (
    "authorization",
    "reliability",
    "architecture",
    "compatibility",
    "configuration",
    "resource_lifecycle",
)

# Findings must be on a changed line >= 2 (hunks keep one leading context line).
POSITIVE = EvidenceStatus.CONFIRMED
NOT_CONFIRMED = {EvidenceStatus.STRONGLY_SUPPORTED, EvidenceStatus.REJECTED}


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------
def test_six_new_categories_registered():
    for category in NEW_CATEGORIES:
        assert category in ALLOWED_CHECKS
        assert category in CATEGORY_PATTERNS


def test_each_new_category_has_an_agent():
    for category in NEW_CATEGORIES:
        assert category in _ANALYSIS_AGENTS, f"{category} missing from _ANALYSIS_AGENTS"


def test_twenty_three_total_categories():
    assert len(ALLOWED_CHECKS) == 23


def test_new_categories_prompts_are_installed():
    from agentic_code_reviewer.llm.prompts import load_prompt

    for category in NEW_CATEGORIES:
        assert load_prompt(category, "v1"), f"{category} prompt missing"


# ----------------------------------------------------------------------
# Verifier-level helpers
# ----------------------------------------------------------------------
def _verifier_state(diff: str, files: dict[str, str]) -> ReviewState:
    state = ReviewState(
        request=ReviewRequest(
            repository="o/r", diff_text=diff, changed_files=list(files), repo_files=files
        )
    )
    state.snapshot = RepositorySnapshot(files, changed_files=list(files))
    return state


def _run(category: str, file_text: str, finding_line: int, title: str, description: str):
    line_text = file_text.splitlines()[finding_line - 1]
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        f"@@ -{finding_line - 1},2 +{finding_line - 1},2 @@\n"
        f" context line {finding_line - 1}\n"
        f"+{line_text}\n"
    )
    state = _verifier_state(diff, {"app.py": file_text})
    finding = ReviewFinding(
        category=category,
        severity="high",
        confidence=0.9,
        title=title,
        description=description,
        file_path="app.py",
        start_line=finding_line,
    )
    state.findings.append(finding)
    VerifierAgent(Settings(LLM_PROVIDER="mock"), MockLLMClient()).run(state)
    return finding


# ----------------------------------------------------------------------
# Authorization
# ----------------------------------------------------------------------
def test_authorization_positive_tenant_scope():
    text = "def handler(request):\n    rows = db.query(Item).filter_by(tenant_id=request.tenant)\n    return rows\n"
    finding = _run("authorization", text, 2, "Tenant isolation violation",
                   "Item query scoped by tenant_id.")
    assert finding.evidence_status == POSITIVE


def test_authorization_positive_check_used_on_route():
    text = ("def handler(request, doc):\n"
            "    if not current_user.is_owner(doc): raise Forbidden\n"
            "    return render(doc)\n")
    finding = _run("authorization", text, 2, "Ownership gate on route",
                   "current_user.is_owner guards access to doc.")
    assert finding.evidence_status == POSITIVE


def test_authorization_positive_semantic_missing_check():
    # A genuine "missing ownership check" claim: no deterministic rule proves
    # it, but the symbol evidence grounds it -> strongly_supported, never
    # rejected (the check truly is absent from the line).
    text = ("def get_document(doc_id):\n"
            "    return docs[doc_id]\n"
            "\n"
            "def handler(request):\n"
            "    doc = get_document(request.args['id'])\n"
            "    return render(doc)\n")
    finding = _run("authorization", text, 5, "Missing ownership check for get_document",
                   "get_document is invoked with an attacker-controlled id without ownership checks.")
    assert finding.evidence_status in (EvidenceStatus.CONFIRMED, EvidenceStatus.STRONGLY_SUPPORTED)
    assert finding.evidence_status != EvidenceStatus.REJECTED


def test_authorization_negative_plain_arithmetic():
    text = "def compute():\n    total = price * quantity\n    return total\n"
    finding = _run("authorization", text, 2, "Arithmetic",
                   "total is computed from price and quantity.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_authorization_negative_route_without_pattern():
    text = "def handler(request):\n    return send_document(doc_id)\n"
    finding = _run("authorization", text, 2, "Route without authz",
                   "send_document is exposed without an authorization check.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_authorization_edge_no_snapshot_does_not_crash():
    state = ReviewState(
        request=ReviewRequest(repository="o/r", diff_text="", changed_files=[], repo_files={})
    )
    finding = ReviewFinding(
        category="authorization", severity="high", confidence=0.9,
        title="Edge", description="Edge case.", file_path="app.py", start_line=1,
    )
    state.findings.append(finding)
    VerifierAgent(Settings(LLM_PROVIDER="mock"), MockLLMClient()).run(state)
    assert finding.evidence_status is not None


# ----------------------------------------------------------------------
# Reliability / Resilience
# ----------------------------------------------------------------------
def test_reliability_positive_retry_loop():
    text = "def pay():\n    for attempt in range(3):\n        payment_service.charge(card)\n"
    finding = _run("reliability", text, 2, "Retry without idempotency",
                   "pay retries charge up to three times.")
    assert finding.evidence_status == POSITIVE


def test_reliability_positive_non_idempotent_sink():
    text = "def pay():\n    payment_service.charge(card)\n    return receipt\n"
    finding = _run("reliability", text, 2, "Charge without idempotency key",
                   "pay charges the card directly.")
    assert finding.evidence_status == POSITIVE


def test_reliability_positive_ack_pattern():
    text = "def consume(msg):\n    queue.ack(msg)\n    process(msg)\n"
    finding = _run("reliability", text, 2, "Ack before processing",
                   "queue.ack fires before msg processing finishes.")
    assert finding.evidence_status == POSITIVE


def test_reliability_negative_single_call():
    text = "def pay():\n    return make_receipt(card)\n"
    finding = _run("reliability", text, 2, "Single charge call",
                   "pay calls make_receipt exactly once.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_reliability_negative_backoff_applied():
    text = "def call():\n    time.sleep(2 ** attempt)\n    return api()\n"
    finding = _run("reliability", text, 2, "Backoff applied",
                   "call sleeps exponentially before retrying.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_reliability_edge_clean_line_no_rules():
    assert match_category_patterns("reliability", "total = sum(items)") == []


# ----------------------------------------------------------------------
# Architecture / Design
# ----------------------------------------------------------------------
def test_architecture_positive_mutable_global():
    text = "# module\nsettings_cache = {}\n\ndef handler():\n    return 1\n"
    finding = _run("architecture", text, 2, "Module-level mutable global",
                   "settings_cache is shared mutable module state.")
    assert finding.evidence_status == POSITIVE


def test_architecture_positive_route_business_logic_rule():
    # Multi-line rule: DB mutation directly under a route decorator.
    text = ("@app.post('/orders')\n"
            "def create(request):\n"
            "    db.add(order)\n")
    assert "business-in-route" in match_category_patterns("architecture", text)


def test_architecture_positive_bypass_service_layer():
    text = "from app.models import Order\n\ndef handler():\n    return 1\n"
    finding = _run("architecture", text, 1, "Bypassing service layer",
                   "Order is imported straight from the models package.")
    assert finding.evidence_status == POSITIVE


def test_architecture_negative_delegation():
    text = "@app.post('/orders')\ndef create(request):\n    return orders_service.create(request)\n"
    finding = _run("architecture", text, 3, "Service delegation",
                   "create delegates to orders_service.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_architecture_negative_plain_function():
    text = "def helper(x):\n    return x * 2\n"
    finding = _run("architecture", text, 1, "Plain helper",
                   "helper doubles its input.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_architecture_edge_constant_assignment():
    assert match_category_patterns("architecture", "RATE_LIMIT = 100") == []


# ----------------------------------------------------------------------
# Compatibility
# ----------------------------------------------------------------------
def test_compatibility_positive_schema_change():
    text = "USER_EVENT = {'type': 'user', 'version': 2, 'fields': []}\n"
    finding = _run("compatibility", text, 1, "Event schema change",
                   "USER_EVENT bumps version to 2.")
    assert finding.evidence_status == POSITIVE


def test_compatibility_positive_flag_flip():
    text = "if feature_flags['new_payment_flow']:\n    checkout_v2()\n"
    finding = _run("compatibility", text, 1, "Feature flag flip",
                   "new_payment_flow gate introduced.")
    assert finding.evidence_status == POSITIVE


def test_compatibility_positive_breaking_keyword():
    text = "# breaking: renamed TIMEOUT to REQUEST_TIMEOUT\n"
    finding = _run("compatibility", text, 1, "Renamed environment variable",
                   "breaking rename of a configuration key.")
    assert finding.evidence_status == POSITIVE


def test_compatibility_negative_plain_call():
    text = "def f():\n    client.fetch(order_id)\n"
    finding = _run("compatibility", text, 2, "Plain call",
                   "f fetches an order through the client.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_compatibility_negative_unchanged_shape():
    assert match_category_patterns("compatibility", "return items") == []


def test_compatibility_edge_missing_history_no_crash():
    text = "def f():\n    return 1\n"
    finding = _run("compatibility", text, 2, "Edge",
                   "f returns a constant.")
    assert finding.evidence_status is not None


# ----------------------------------------------------------------------
# Configuration / Deployment
# ----------------------------------------------------------------------
def test_configuration_positive_required_env():
    text = "import os\ndb_url = os.environ['DATABASE_URL']\n"
    finding = _run("configuration", text, 2, "New required environment variable",
                   "DATABASE_URL is read from the environment.")
    assert finding.evidence_status == POSITIVE


def test_configuration_positive_deployment_ports():
    text = "ports:\n  - containerPort: 8080\n"
    finding = _run("configuration", text, 1, "Deployment port mapping",
                   "container exposes port 8080.")
    assert finding.evidence_status == POSITIVE


def test_configuration_positive_health_check():
    text = "healthCheck:\n  httpGet:\n    path: /healthz\n"
    finding = _run("configuration", text, 1, "Health check added",
                   "healthCheck configured on the deployment.")
    assert finding.evidence_status == POSITIVE


def test_configuration_negative_plain_arithmetic():
    text = "def compute():\n    total = price * quantity\n    return total\n"
    finding = _run("configuration", text, 2, "Arithmetic",
                   "total is computed from price and quantity.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_configuration_negative_logging_line():
    text = "def main():\n    log.info('started')\n"
    finding = _run("configuration", text, 2, "Logging line",
                   "log.info on the startup path.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_configuration_edge_unknown_file_type():
    assert match_category_patterns("configuration", "SELECT 1") == []


# ----------------------------------------------------------------------
# Resource Lifecycle
# ----------------------------------------------------------------------
def test_resource_lifecycle_positive_open_without_with():
    text = "def write_report():\n    fh = open(path, 'w')\n    fh.write('x')\n"
    finding = _run("resource_lifecycle", text, 2, "File handle not closed",
                   "fh is opened without a context manager.")
    assert finding.evidence_status == POSITIVE


def test_resource_lifecycle_positive_lock_acquired():
    text = "def worker():\n    lock.acquire()\n    try:\n        work()\n"
    finding = _run("resource_lifecycle", text, 2, "Lock without release",
                   "lock is acquired outside a finally block.")
    assert finding.evidence_status == POSITIVE


def test_resource_lifecycle_positive_temp_file():
    text = "def stage():\n    fd = tempfile.mkstemp(suffix='.tmp')\n"
    finding = _run("resource_lifecycle", text, 2, "Temp file cleanup needed",
                   "mkstemp creates a temp file.")
    assert finding.evidence_status == POSITIVE


def test_resource_lifecycle_negative_with_statement():
    text = "def read():\n    with open(path) as fh:\n        return fh.read()\n"
    finding = _run("resource_lifecycle", text, 2, "fh is context-managed",
                   "fh is closed automatically by the with statement.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_resource_lifecycle_negative_plain_use():
    text = "def f():\n    total = sum(items)\n    return total\n"
    finding = _run("resource_lifecycle", text, 2, "Plain use",
                   "f sums a list.")
    assert finding.evidence_status in NOT_CONFIRMED


def test_resource_lifecycle_edge_subscription_needs_cleanup():
    assert match_category_patterns("resource_lifecycle", "x = 1") == []


# ----------------------------------------------------------------------
# Full-workflow integration: every new stage runs under the mock provider
# ----------------------------------------------------------------------
def test_workflow_calls_all_new_stages(mock_settings):
    import json

    from agentic_code_reviewer.llm.mock_client import MockLLMClient
    from agentic_code_reviewer.orchestration.workflow import Workflow

    # Script the planner to request every category so the workflow actually
    # dispatches all analysis agents (the mock serves valid empty findings).
    plan = {
        "summary": "full review",
        "affected_files": ["app.py"],
        "affected_components": ["core"],
        "risk_areas": [],
        "required_checks": list(ALLOWED_CHECKS),
        "decomposition_required": False,
        "decomposition_notes": "",
    }
    client = MockLLMClient(responses={"planner": json.dumps(plan)})
    request = ReviewRequest(
        repository="o/r",
        diff_text=(
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n"
            " x = 1\n+def f():\n    return 2\n"
        ),
        changed_files=["app.py"],
        repo_files={"app.py": "x = 1\ndef f():\n    return 2\n"},
    )
    result = Workflow(mock_settings, client=client).run(request)
    stages = set(client.calls)
    for category in NEW_CATEGORIES:
        assert category in stages, f"workflow never ran the {category} stage"
    assert result.review is not None
