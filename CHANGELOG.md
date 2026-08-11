# Changelog

All notable changes to **agentic-code-reviewer** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **First-run onboarding**: when no provider is configured, bare `acr` (or
  `acr <path>`) auto-opens a welcome screen that funnels into the provider
  wizard, offers a keyless mock-provider tour, or can be dismissed. It never
  reappears once any provider is configured or set via the environment.

## [0.1.0] - 2026-08-11

Initial release of **agentic-code-reviewer** (`acr`), a production-grade
agentic code reviewer with a terminal UI, CLI, and reproducible benchmark
harness.

### Added

- **23 review categories**: the original 17 (correctness, security, error
  handling, testing, regression, performance, maintainability, observability,
  data integrity, accessibility, concurrency, dependencies, privacy, i18n,
  api contract, requirement alignment, dead code) plus six new ones:
  authorization, reliability / resilience, architecture / design,
  compatibility, configuration / deployment, and resource lifecycle.
- **Shared repository snapshot**: one file / AST / symbol / reference / route /
  env-var / test index built per review and queried by every stage, so
  reviewers never re-read or re-parse files.
- **Layered evidence verification**: LLM findings flow through regex, AST,
  symbol and taint layers to an evidence status of `confirmed`,
  `strongly_supported`, `insufficient_evidence`, or `rejected` (with reason).
  Only confirmed and strongly supported findings normally reach the user.
- **AST verification rules**: mutable default arguments, dangerous calls,
  exception handling, function signature changes, imports, async blocking
  calls, unchecked indexing, and SQL construction (Python).
- **Symbol-aware analysis**: callers, definitions, and references are resolved
  so API-contract findings only report caller breakage when real callers
  exist, and dead-code detection is grounded in actual references.
- **Lightweight taint analysis**: sources (request data, headers, env, queue
  payloads) propagated to sinks (SQL, shell, filesystem, HTTP, deserialization,
  logs), with sanitizer and parameterized-query awareness.
- **SSRF and path traversal rules**: dynamic URLs, private / loopback / cloud
  metadata targets, and user-controlled paths reaching filesystem operations.
- **Cross-category correlation**: overlapping findings are clustered into
  canonical findings with `primary_category` and `related_categories`;
  genuinely distinct defects are never merged.
- **Cross-change interaction analysis**: documented category-pair rules detect
  materially new defects such as caching amplifying a security issue or a
  schema change breaking a rolling deployment.
- **Deterministic severity model**: severity derives from impact, likelihood,
  blast radius, and exploitability; confidence never inflates severity.
- **Git history context**: read-only `git log` and `git blame` enrich the
  reviewers that benefit most, treated as evidence, not authority.
- **Upgraded testing reviewer**: a behavioral matrix (happy path, empty,
  invalid, boundary, failure) instead of a simple "are there tests?" check.
- **Upgraded API-contract reviewer**: changes are classified as new, changed,
  removed, behavior change, or caller breakage using actual repository callers.
- **Interactive TUI** with a launcher, provider configuration, review history,
  per-category findings browser, command palette, and a results screen with
  evidence display.
- **Mock and offline modes** for keyless local runs and reproducible
  benchmarks, including a deterministic adaptive mock for end-to-end tests.
- **Configuration from the CLI**: providers, models, and API keys can be set
  directly from the CLI and TUI.

### Changed

- `ALLOWED_CHECKS` grew from 17 to 23 categories; the categories screen and
  home screen now reflect the full set automatically.
- Findings carry both `evidence_status` and the legacy `verification_status`,
  kept in sync, so existing exporters and consumers remain compatible.

### Fixed

- Taint analysis now scans `return` statements (previously missed sinks such
  as `return os.system(x)`) and resolves cross-function flows through
  same-file summaries.
- The verifier stores the rejection reason on `rejection_reason` so rejected
  findings always explain themselves.
- `os.environ.get("X")` environment variables are indexed alongside
  `os.getenv` and `os.environ["X"]`.
- Unchecked-index detection now fires on fixed-range loops and stays silent
  for `range(len(coll))` bounded loops.

### Security

- No runtime execution: the reviewer never imports or runs reviewed
  application code.
- All repository content (diffs, code, comments, docs, commit messages) is
  treated as untrusted data in every prompt; repository content can never
  override reviewer instructions.
- API keys and secrets are never logged; log redaction masks secret-looking
  fields.
