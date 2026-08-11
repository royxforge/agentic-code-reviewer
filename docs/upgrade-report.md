# Upgrade Implementation Report

Date: 2026-08-11
Scope: capability upgrade to the **agentic-code-reviewer** (`acr`).
Baseline: 230 passing tests. After: **374 passing tests**, ruff clean, mypy clean (92 source files).

---

## 1. Files changed

| File | Change |
|---|---|
| `src/agentic_code_reviewer/models/findings.py` | `EvidenceStatus` enum (`confirmed` / `strongly_supported` / `insufficient_evidence` / `rejected`), `evidence_status`, `rejection_reason`, `evidence_layers`, `change_type` (NEW_API / CHANGED_API / REMOVED_API / BEHAVIOR_CHANGE / CALLER_BREAKAGE), `related_categories`, severity-model factors (`likelihood`, `blast_radius`, `exploitability`, `impact_factor`), `rule_id` |
| `src/agentic_code_reviewer/models/review.py` | `ALLOWED_CHECKS` 17 → **23** categories |
| `src/agentic_code_reviewer/models/schemas.py` | `VerificationResult` extended with `evidence_status`, `rejection_reason`, `evidence_layers`, `method` |
| `src/agentic_code_reviewer/analysis/rules.py` | Merges `CATEGORY_PATTERNS_EXTRA` + `SECURITY_PATTERNS_EXTRA` into the deterministic rule sets |
| `src/agentic_code_reviewer/analysis/aggregation.py` | Evidence-aware `filter_findings` (strictness: strict / balanced / lenient), `cluster_findings`, `interaction_findings`, `stabilize_severities` |
| `src/agentic_code_reviewer/agents/verifier.py` | Layered evidence pipeline (regex → AST → symbol → taint), rejection with reason, both status fields populated |
| `src/agentic_code_reviewer/agents/dead_code.py` | Symbol/AST checks layered on the deterministic checker (unused locals, unreachable branches  -  statically reliable only) |
| `src/agentic_code_reviewer/agents/aggregator.py` | Evidence-strictness filtering + clustering + severity stabilisation |
| `src/agentic_code_reviewer/agents/base.py` | `$HISTORY$` token injected into prompts; `_collect` forwards extra kwargs |
| `src/agentic_code_reviewer/agents/testing.py`, `agents/api_contract.py` | Upgraded to the behavioral-matrix / caller-based prompts |
| `src/agentic_code_reviewer/orchestration/state.py` | `ReviewState.snapshot` field (shared `RepositorySnapshot`) |
| `src/agentic_code_reviewer/orchestration/workflow.py` | Snapshot build, six new agents registered, correlation stage, per-category model selection |
| `src/agentic_code_reviewer/config/settings.py` | New toggles: `ast_analysis`, `taint_analysis`, `verification_strictness`, `history_analysis`, per-category ablation switches |
| `src/agentic_code_reviewer/llm/prompts.py` | `$HISTORY$` support; prompt loader |
| `src/agentic_code_reviewer/llm/mock_client.py` | Six new stages in the deterministic mock |
| `src/agentic_code_reviewer/cli/exporters.py` | Evidence status, related categories, change type in JSON/Markdown/SARIF exports |
| `src/agentic_code_reviewer/ui/screens/results.py`, `ui/screens/categories.py` | Evidence display; docstring/README updated to 23 categories |
| `README.md` | Documented the 23-category surface |

## 2. New modules / components

- `analysis/snapshot.py`  -  **`RepositorySnapshot`** shared, read-only index built once per review: file index, AST index, symbol index (defs), reference index (loads/calls), import index, route/API index, configuration index (env vars), test index, dependency graph, optional read-only git history (log/blame). Never raises; degrades to empty.
- `analysis/ast_rules.py`  -  deterministic **AST verification**: mutable default arguments, dangerous calls (os.system/eval/subprocess), SQL f-string construction, bare/swallowed excepts, blocking calls in async, unchecked indexing (bounds-aware), signature changes, unused imports.
- `analysis/taint.py`  -  **lightweight taint engine**: sources (request/params/headers/env/queue payloads) → propagation (assignments, containers, calls, f-strings, same-file function summaries) → sinks (SQL, shell, filesystem, HTTP, deserialization, logs). Sanitizers and parameterised SQL clear taint; honest scope (intra-file only).
- `analysis/severity.py`  -  **deterministic severity model**: `derive_severity(impact, likelihood, blast_radius, exploitability)`; confidence is deliberately excluded.
- `analysis/category_rules.py`  -  deterministic patterns for the six new categories plus **SSRF** (dynamic URL, private/cloud-metadata targets) and **path traversal** (open/send_file/shutil) security rules.
- `retrieval/history.py`  -  read-only git history context (`git log` / `git blame`, guarded, `errors="replace"`).
- `agents/authorization.py`, `agents/reliability.py`, `agents/architecture.py`, `agents/compatibility.py`, `agents/configuration.py`, `agents/resource_lifecycle.py`  -  six new reviewer agents.
- `prompts/authorization/v1.txt`, `prompts/reliability/v1.txt`, `prompts/architecture/v1.txt`, `prompts/compatibility/v1.txt`, `prompts/configuration/v1.txt`, `prompts/resource_lifecycle/v1.txt`, `prompts/testing/v2.txt`, `prompts/api_contract/v2.txt`.

## 3. New review categories

1. **Authorization**  -  IDOR/BOLA, missing ownership checks, privilege escalation, tenant isolation, late role checks, insecure defaults; grounded in the repository's existing authz architecture; no subjective preferences.
2. **Reliability / Resilience**  -  retry storms, retries without backoff, retries of non-idempotent operations (e.g. `for attempt in range(3): charge(card)`), missing idempotency, duplicate processing, cascading failures, ack/commit problems, timeout mismatches, missing graceful degradation.
3. **Architecture / Design**  -  bypassed abstractions, module-boundary violations, circular dependencies, business logic in the wrong layer, duplicated capabilities, module-level mutable globals, excessive coupling. Not a general "I don't like this design" reviewer.
4. **Compatibility**  -  schema/serialization changes, renamed config/env vars, changed CLI args, feature-flag flips, cache-format changes, rolling-deployment coexistence.
5. **Configuration / Deployment**  -  new required env vars, config references, deployment-manifest mismatches, broken health/readiness probes, startup-order and migration-sequencing problems; correlated with the repository's config schemas and manifests.
6. **Resource Lifecycle**  -  file/socket/connection leaks, locks without release, timers/listeners never cleaned up, unclosed transactions, temp files, background jobs outliving scope.

## 4. New deterministic rules

- **Security additions**: `ssrf-dynamic-url`, `ssrf-private-target` (localhost/loopback/private ranges/169.254.169.254), `path-traversal-open`, `path-traversal-send`, `path-traversal-shutil`.
- **Authorization**: `authz-check-present`, `authz-tenant-scope`.
- **Reliability**: `retry-loop`, `retry-without-backoff`, `idempotency-evidence`, `non-idempotent-sink`, `ack-after-failure`.
- **Architecture**: `module-mutable-global`, `bypass-service-layer`, `business-in-route`.
- **Compatibility**: `schema-shape-change`, `renamed-config`, `flag-flip`.
- **Configuration**: `env-var-read`, `config-reference`, `deployment-manifest`, `env-default-mismatch`.
- **Resource lifecycle**: `open-no-with`, `lock-acquired`, `timer-registered`, `listener-subscribed`, `temp-file-created`, `transaction-started`.

## 5. New semantic-analysis capabilities

- **AST verification** for patterns previously only regex-based (see §2)  -  Python.
- **Symbol/reference layer**: resolves imports, definitions, call sites, references; used by API-contract (find *actual* callers before reporting breakage), dead-code, architecture, authorization, compatibility and regression evidence.
- **Lightweight taint analysis** with sources → propagation → sinks and sanitizer/parameterization awareness (Python).
- **Cross-category correlation**: `cluster_findings` normalises by file, changed lines, root cause and affected symbols; overlapping cross-category findings collapse into one canonical finding with `primary_category` + `related_categories`; genuinely distinct defects are never merged.
- **Cross-change interaction analysis**: documented category-pair rules (Security+Caching → cross-user exposure; Concurrency+Data Integrity → corruption; API Contract+Regression → broken callers; Performance+Reliability → failure amplification; Compatibility+Data Integrity → rolling-deployment mismatch; Dependencies+Security) that only fire on co-located, strongly-evidenced findings.
- **Evidence status**: `confirmed` (deterministic rule), `strongly_supported` (multiple concrete facts), `insufficient_evidence`, `rejected` (contradicts repository state, with a reason). Filtering default keeps only confirmed + strongly_supported.
- **Severity model**: LLM-provided severity is kept unless the agent supplies the four factors; then severity is derived deterministically. Confidence never inflates severity.
- **Git-history context**: `git log` / `git blame` injected as evidence (never authority) into regression, correctness, API-contract, data-integrity, architecture, compatibility and requirement-alignment prompts.
- **API-contract**: changed signatures/routes/exports are classified (NEW/CHANGED/REMOVED/BEHAVIOR/CALLER_BREAKAGE) and only reported as breaking when actual repository callers exist.
- **Testing reviewer**: behavioral-matrix framing (happy path / empty / invalid / boundary / failure), detects missing negative and boundary cases, tests that don't exercise the change, mirror tests, and skipped/empty tests  -  without demanding tests for trivial changes.

## 6. Tests added (144 new)

- `test_taint.py`  -  source→direct sink, source→one function→sink, source→multiple functions→sink, sanitized source, untrusted source with parameterized API, path traversal, sensitive-data leaks, f-string SQL, clean-code negatives.
- `test_evidence.py`  -  legacy mapping, status filtering per strictness, regex confirmation, line-outside-hunks rejection, symbol-contradiction rejection, empty-index safety.
- `test_snapshot.py`  -  file/AST/symbol/reference/route/env/import indexes, history graceful degradation, never-raises parsing, `has_index` semantics.
- `test_ast_rules.py`  -  mutable defaults, dangerous calls, exception handling, signature changes, imports, async blocking, unchecked vs len-bounded indexing, SQL construction.
- `test_severity.py`  -  full severity ladder, factor extraction, confidence exclusion.
- `test_category_rules.py`  -  positive/negative/edge for every new-category rule and the SSRF/path-traversal rules.
- `test_clustering.py`  -  identical dedupe, overlapping merge with related categories, distinct-finding separation, interaction analysis gating, severity stabilisation.
- `test_new_categories.py`  -  3 positive / 2 negative / 1 edge per new category at the verifier level, registration (23 categories), prompt installation, full-workflow stage dispatch under the mock provider.

## 7. Existing tests executed

- Full suite before the change: **230 passed**.
- Full suite after: **374 passed** (no warnings).
- `ruff check src/ tests/`  -  clean. `mypy src/agentic_code_reviewer`  -  clean (92 files).

## 8. Known limitations

- **Taint analysis is intra-file only** (with same-file function summaries). Cross-module flows, reflection and dynamic dispatch are deliberately not claimed.
- **AST and symbol layers are Python-only** (the repository's AST ecosystem). Other languages get regex verification.
- **Interaction analysis** covers only the six documented category pairs, and only when findings co-locate on evidence.
- **Dead-code unused-local detection** is conservative: only statically reliable cases are reported; reflection/dynamic-import/plugin uncertainty suppresses the report.
- **Git history** requires a local repository path; it degrades to empty (never errors) otherwise. Commit messages are treated as evidence, never intent.
- The mock provider returns canned, empty findings for the new stages  -  it exercises the pipeline but claims no real detection.

## 9. Architectural decisions

- **One shared `RepositorySnapshot` per review** (spec §16): reviewers never re-read or re-parse files; the verifier, dead-code checker, API analysis and taint engine all query it.
- **Layered verification** in one deterministic stage: LLM claim → evidence resolver → regex → AST → symbol → taint → status. Never claims `confirmed` without deterministic evidence; never fabricates confirmation.
- **Rejection against a real index only**: an empty snapshot proves nothing, so claims are never rejected on an empty index.
- **Findings carry both statuses** (`evidence_status` and legacy `verification_status`) kept in sync, so downstream consumers and exports stay backward compatible.
- **Configurability**: every capability (AST, taint, history, strictness, per-category agents, model per category) is a setting; mock/offline modes unchanged.
- New categories and rules live in separate modules (`category_rules.py`, own agent/prompt files) so the original rule set stays intact and auditable.

## 10. Features intentionally deferred

- **JSX accessibility AST rules**  -  deferred: the repository has no JS/JSX AST toolchain; the spec's "where applicable" clause applies (regex `innerHTML`/`dangerouslySetInnerHTML` rules remain).
- **Cross-language taint and symbol resolution**  -  would require a multi-language AST dependency; Python is the supported ecosystem for now.
- **Semantic verification beyond Python**  -  not pretended where unsupported (per spec §2).
- **Reflection-aware dead-code analysis**  -  heuristic suppression exists; full reflection models are out of scope.
