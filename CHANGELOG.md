# Changelog

All notable changes to **agentic-code-reviewer** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-08-21

### Added

- **Knowledgebase**: persistent, cross-run repository knowledge injected into
  analysis prompts. Three kinds of entries, stored as JSONL under the user
  config dir (`config_dir()/knowledge/`):
  - **Curated rules / conventions** (`acr knowledge add`) - e.g. "this repo
    uses SQLAlchemy 2.0 style".
  - **Auto-captured verified findings** (repo memory, opt-in via
    `KNOWLEDGE_AUTO_CAPTURE=true`): confirmed / strongly-supported findings
    from past reviews are persisted and injected into future reviews.
  - **Indexed docs** (`acr knowledge index-docs <path>`): markdown sections
    become searchable entries.
  - Retrieval is deterministic token-overlap (no LLM call, no embedding
    endpoint), so the knowledgebase adds zero token cost and works offline.
  - New CLI: `acr knowledge add|list|remove|search|index-docs`.
  - New settings: `KNOWLEDGE_ENABLED`, `KNOWLEDGE_DIR`, `KNOWLEDGE_TOP_K`,
    `KNOWLEDGE_MAX_CHARS`, `KNOWLEDGE_AUTO_CAPTURE`.

### Changed

- **Token-usage reduction**:
  - Analysis agents now receive a **category-focused diff** (`diff_text_for_category`):
    each agent only sees the hunks its deterministic patterns care about,
    falling back to the full diff when nothing matches - the single biggest
    per-agent token cut (previously every agent re-read the whole change).
  - **Compact plan / change-summary JSON** in prompts (only the fields agents
    read), cutting tokens across all parallel analysis calls.
  - **Anthropic prompt caching**: the shared system block is marked with
    `cache_control` (opt-out via `USE_PROMPT_CACHING=false`), so the 22
    parallel analysis agents pay one full read + cache reads instead of 22.
  - **Per-agent output cap**: analysis agents default to `ANALYSIS_MAX_TOKENS`
    (4096) instead of the global `MAX_TOKENS` (8192).
- Analysis prompts now render a `$KNOWLEDGE$` block (empty when the
  knowledgebase has nothing relevant).
- **Dismiss-to-knowledge feedback loop**: dismissing a finding in interactive
  triage records a `dismissed` knowledge entry for the repository; later
  reviews suppress findings that match it (same category + strong token
  overlap, or same file + category). The knowledgebase now learns from user
  decisions, not just auto-capture.
- **LLM response cache**: content-addressed disk cache keyed by
  `(prompt content, model, temperature, max_tokens)` - repeat reviews of
  identical diffs (CI) skip the provider entirely. Opt-in via
  `LLM_CACHE_ENABLED=true` (off by default because it changes reproducibility
  semantics); `llm_call_count` / `estimated_cost_usd` reflect only real calls.
- **Cross-file taint propagation**: function summaries are built across the
  whole repository snapshot, so a changed file calling a helper defined in
  another module now reports sinks that previously required same-file flows.
- **Aggregator top-N cap**: `AGGREGATOR_MAX_FINDINGS` (default 60) limits the
  findings serialized into the aggregator prompt, keeping huge finding sets
  from blowing the LLM budget; the full ranked set remains the fallback.
- **Prompt regression suite**: golden tests pin the canonical fixture-diff
  findings through the full workflow, so a prompt-template or prompt-assembly
  edit can never silently drop a confirmed finding.

### Fixed

- **Category model override for OpenAI-compatible provider**: the provider
  lookup compared the hyphenated provider string (`openai-compatible`) against
  the underscore key (`openai_compatible`), silently falling back to the wrong
  settings field. The provider string is now normalised before lookup.
- **`yaml-load` verification regex**: the negative lookahead for `Loader=` was
  placed after the closing paren, so safe calls like
  `yaml.load(data, Loader=SafeLoader)` still matched and produced
  false-positive security confirmations. The lookahead is now inside the
  call arguments.
- **Aggregator fallback double-ranking**: `_fallback_review` applied
  `rank_findings` twice; the redundant second call is removed.
- **SARIF `informationUri`**: pointed to a non-existent GitHub org; corrected
  to `https://github.com/royxforge/agentic-code-reviewer`.
- **README**: test-count badge and references updated from 386 to 432.

## [0.2.1] - 2026-08-12

### Added

- **Boot splash screen**: bare `acr` (or `acr <path>`) now opens with a
  full-screen brand moment (block-letter logo, product name, version, animated
  spinner and progress bar) that auto-dismisses after ~1.8s or skips on any
  key. Skipped automatically in headless runs so tests and screenshot
  capture are unaffected.

### Fixed

- **CI test run**: the `dev` extra now includes the OpenAI SDK so the
  GitHub Actions `pip install ".[dev]"` step has everything the OpenAI
  client tests need (previously 12 tests failed on CI with
  `ModuleNotFoundError: No module named 'openai'`).
- **Test-suite hang**: the splash-screen integration tests were made fully
  hermetic (mock provider + non-git temp path) so they can never start a
  real review against a live provider. A timing race in one test could
  previously fall through to the launcher's "Review this directory" action
  and block forever on a live Ollama call, hanging the whole suite.

## [0.2.0] - 2026-08-11

### Added

- **First-run onboarding**: when no provider is configured, bare `acr` (or
  `acr <path>`) auto-opens a welcome screen that funnels into the provider
  wizard, offers a keyless mock-provider tour, or can be dismissed. It never
  reappears once any provider is configured or set via the environment.
- **Published to PyPI**: `agentic-code-reviewer` is now installable with
  `pip install agentic-code-reviewer`; the 30 prompt templates ship inside
  the wheel via explicit package data.
- **Automatic releases via GitHub Actions**: pushing a `v*` tag runs the
  test suite, verifies the tag matches `__version__`, builds the sdist and
  wheel, and uploads to PyPI using the `PYPI_API_TOKEN` repository secret.
- **README badges**: PyPI version, PyPI downloads, and GitHub Actions
  publish-status badges in the header row.

### Changed

- **No `.env` file anywhere**: all configuration (providers, models, API
  keys) is done from the CLI or TUI; the `.env.example` file and all
  `.env` references were removed from code, docs, and UI copy.
- The provider status label "env" was renamed to "environment" in the CLI
  table, TUI Providers screen, and README.
- Package metadata modernized: PEP 639 `license = "MIT"` with
  `license-files`, SPDX-aligned classifiers, and the author field now
  matches `CITATION.cff`.

### Fixed

- Prompt templates are now bundled in the built wheel (previously they were
  omitted, which would have broken reviews installed from PyPI).

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
