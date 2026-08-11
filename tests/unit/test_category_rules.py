"""Deterministic rule tests for the six new categories (spec sections 1 + 5)."""

from agentic_code_reviewer.analysis.rules import CATEGORY_PATTERNS, match_category_patterns


def _hits(category: str, text: str) -> list[str]:
    return match_category_patterns(category, text)


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------
def test_all_six_new_categories_registered():
    for category in (
        "authorization",
        "reliability",
        "architecture",
        "compatibility",
        "configuration",
        "resource_lifecycle",
    ):
        assert category in CATEGORY_PATTERNS
        assert CATEGORY_PATTERNS[category]  # non-empty rule list


def test_ssrf_rules_merged_into_security():
    ids = {rule_id for rule_id, _ in CATEGORY_PATTERNS["security"]}
    assert "ssrf-dynamic-url" in ids
    assert "ssrf-private-target" in ids
    assert "path-traversal-open" in ids
    assert "path-traversal-send" in ids


# ----------------------------------------------------------------------
# Security additions: SSRF (spec section 5)
# ----------------------------------------------------------------------
def test_ssrf_dynamic_url_positive():
    hits = _hits("security", "return requests.get(url, timeout=5)")
    assert "ssrf-dynamic-url" in hits


def test_ssrf_private_target_positive():
    hits = _hits("security", "resp = httpx.get('http://127.0.0.1/admin')")
    assert "ssrf-private-target" in hits
    hits2 = _hits("security", "url = 'http://169.254.169.254/latest/meta-data/'")
    assert "ssrf-private-target" in hits2


def test_ssrf_negative_plain_call():
    hits = _hits("security", "resp = requests.get(API_URL)")
    assert "ssrf-dynamic-url" not in hits
    assert "ssrf-private-target" not in hits


def test_path_traversal_open_positive():
    hits = _hits("security", "with open(filepath, 'rb') as fh:")
    assert "path-traversal-open" in hits


def test_path_traversal_send_file_positive():
    hits = _hits("security", "return send_file(full_path)")
    assert "path-traversal-send" in hits


def test_path_traversal_negative_literal():
    hits = _hits("security", "with open('/etc/hosts') as f:")
    assert "path-traversal-open" not in hits


# ----------------------------------------------------------------------
# Authorization
# ----------------------------------------------------------------------
def test_authorization_check_present_positive():
    hits = _hits("authorization", "if not current_user.is_owner(doc):")
    assert "authz-check-present" in hits


def test_authorization_tenant_scope_positive():
    hits = _hits("authorization", "rows = db.query(Item).filter_by(tenant_id=request.tenant)")
    assert "authz-tenant-scope" in hits


def test_authorization_negative_unprotected_route():
    hits = _hits("authorization", "return get_document(doc_id)")
    assert hits == []


def test_authorization_negative_plain_arithmetic():
    assert _hits("authorization", "total = price * quantity") == []


# ----------------------------------------------------------------------
# Reliability / Resilience
# ----------------------------------------------------------------------
def test_reliability_retry_loop_positive():
    hits = _hits("reliability", "for attempt in range(3):")
    assert "retry-loop" in hits


def test_reliability_retry_without_backoff_positive():
    hits = _hits("reliability", "for i in range(5):\n    call_api()")
    assert "retry-without-backoff" in hits


def test_reliability_non_idempotent_sink_positive():
    hits = _hits("reliability", "payment_service.charge(card)")
    assert "non-idempotent-sink" in hits


def test_reliability_idempotency_negative():
    hits = _hits(
        "reliability",
        "def pay():\n    key = request.headers['Idempotency-Key']\n"
        "    return charge_with(key)\n",
    )
    assert "idempotency-evidence" in hits


def test_reliability_negative_unrelated_code():
    assert _hits("reliability", "total = sum(items)") == []


# ----------------------------------------------------------------------
# Architecture / Design
# ----------------------------------------------------------------------
def test_architecture_mutable_global_positive():
    hits = _hits("architecture", "cache = {}")
    assert "module-mutable-global" in hits


def test_architecture_business_in_route_positive():
    text = "@app.post('/orders')\ndef create(request):\n    db.add(order)"
    hits = _hits("architecture", text)
    assert "business-in-route" in hits


def test_architecture_negative_service_delegation():
    text = "@app.post('/orders')\ndef create(request):\n    return orders_service.create(request)"
    hits = _hits("architecture", text)
    assert "business-in-route" not in hits


def test_architecture_negative_plain_function():
    assert _hits("architecture", "def helper(x):\n    return x * 2") == []


# ----------------------------------------------------------------------
# Compatibility
# ----------------------------------------------------------------------
def test_compatibility_schema_change_positive():
    hits = _hits("compatibility", "event_schema = {'version': 2, 'fields': [...]}")
    assert "schema-shape-change" in hits


def test_compatibility_renamed_config_positive():
    hits = _hits("compatibility", "# renamed: OLD_TIMEOUT -> NEW_TIMEOUT (breaking)")
    assert "renamed-config" in hits


def test_compatibility_flag_flip_positive():
    hits = _hits("compatibility", "if feature_flags['new_checkout']:")
    assert "flag-flip" in hits


def test_compatibility_negative_unchanged_calls():
    assert _hits("compatibility", "client.fetch(order_id)") == []


# ----------------------------------------------------------------------
# Configuration / Deployment
# ----------------------------------------------------------------------
def test_configuration_env_var_read_positive():
    hits = _hits("configuration", "key = os.environ['DATABASE_URL']")
    assert "env-var-read" in hits


def test_configuration_config_reference_positive():
    hits = _hits("configuration", "port = settings.PORT")
    assert "config-reference" in hits


def test_configuration_deployment_manifest_positive():
    hits = _hits("configuration", "ports:\n  - containerPort: 8080")
    assert "deployment-manifest" in hits


def test_configuration_negative_plain_code():
    assert _hits("configuration", "total = price * quantity") == []


# ----------------------------------------------------------------------
# Resource Lifecycle
# ----------------------------------------------------------------------
def test_resource_open_without_with_positive():
    hits = _hits("resource_lifecycle", "fh = open(path, 'w')")
    assert "open-no-with" in hits


def test_resource_open_with_with_negative():
    hits = _hits("resource_lifecycle", "with open(path, 'w') as fh:")
    assert "open-no-with" not in hits


def test_resource_timer_registered_positive():
    hits = _hits("resource_lifecycle", "timer = Timer(60, cleanup)")
    assert "timer-registered" in hits


def test_resource_transaction_started_positive():
    hits = _hits("resource_lifecycle", "session.begin()")
    assert "transaction-started" in hits


def test_resource_edge_temp_file_created_positive():
    hits = _hits("resource_lifecycle", "fd = tempfile.mkstemp(suffix='.tmp')")
    assert "temp-file-created" in hits


def test_resource_negative_plain_assignment():
    assert _hits("resource_lifecycle", "name = 'fh'") == []
