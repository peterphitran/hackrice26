# M7 — Runtime Correlation and Observability

Status: complete; M7.7 Docker rehearsal verified locally  
Priority: P1  
Depends on: M1 runtime regression detection, M3 repository graph  
Extends: `Evidence`, `VerificationResult`, `EvidenceReport`, repository graph traversal, local Docker rehearsal

## 1. Goal

Give a Lou result a trustworthy answer to: *what executed, for which workload and commit, and which
repository symbols did that runtime behavior correspond to?*

M7 adds structured runtime observation around the existing deterministic path. It does **not** make a
trace collector, a hosted vendor, or an LLM a prerequisite for a verification verdict.

```text
CLI / API request
  -> analysis run + correlation context
  -> workload / sandbox / verification spans
  -> sanitized local trace export (best effort)
  -> runtime-to-symbol correlation
  -> persisted telemetry evidence
  -> evidence report
```

If telemetry is disabled, sampled out, malformed, or unavailable, Lou records that condition as
observability evidence and continues deterministic verification normally.

## 2. Scope and boundaries

### In scope

- Local OpenTelemetry SDK instrumentation and optional local OTLP collector configuration.
- Correlation between an analysis run, commit, workload, verification run, artifact, trace, and graph node.
- Request, workload, sandbox, verification, and database-query span conventions.
- A deterministic runtime-to-repository-symbol mapper with explicit unresolved results.
- Sanitization, bounded payloads, deterministic sampling, retention controls, and outage behavior.
- Persisted telemetry evidence and read-only report output.
- Fixture and Docker-backed local collector rehearsals.

### Out of scope

- Hosted observability vendors, production tracing credentials, dashboards, or alert routing.
- Capturing request/response bodies, SQL values, environment values, secrets, or arbitrary log output.
- Replacing workload measurements with tracing, or inferring a regression solely from a span.
- Automatic deployment, production rollout, or learning/calibration logic from M8 and M9.
- Universal runtime source mapping for every language or framework.

## 3. Non-negotiable invariants

1. Verification remains deterministic and usable with no collector, no network, and no telemetry SDK export.
2. Exported evidence never includes authorization headers, cookies, tokens, passwords, request bodies,
   SQL parameter values, `.env` values, or raw repository source.
3. A trace is supporting evidence. The verification comparator remains the sole authority for a measured
   regression or verified fix.
4. Runtime-to-symbol correlation must be explainable: every mapping has a method, confidence, and source
   span; unresolved observations stay unresolved rather than being guessed.
5. Correlation data has strict size, count, sampling, and retention limits enforced in code.

## 4. Common telemetry model

### 4.1 Correlation context

Every M7 span and persisted observation carries the following safe attributes when known:

| Attribute | Source | Required |
|---|---|---|
| `lou.analysis_run_id` | analysis run | yes |
| `lou.repository_id` | repository record | yes |
| `lou.commit_sha` | phase-specific revision | yes |
| `lou.phase` | baseline/candidate/fix | yes |
| `lou.workload_id` | selected workload | workload spans |
| `lou.verification_run_id` | verification result | verification spans |
| `lou.artifact_id` | retained artifact | when applicable |
| `trace_id` / `span_id` | OpenTelemetry context | when sampled/exported |

No free-form user input, filesystem absolute path, body, header, SQL argument, provider prompt, or secret
may be copied into these attributes.

### 4.2 Span names and safe attributes

| Span | Name | Required safe attributes |
|---|---|---|
| analysis | `lou.analysis` | correlation context, entrypoint (`cli` or `api`) |
| workload | `lou.workload.execute` | workload ID/type, phase, exit status, duration |
| sandbox | `lou.sandbox.execute` | sandbox kind, phase, bounded command label, exit status |
| verification | `lou.verification.compare` | comparison phases, status, classification |
| database | `lou.db.query` | operation type, normalized table name, duration, row count when known |
| correlation | `lou.correlation.resolve` | mapping method, mapped/unresolved status, confidence |

Database spans must use a query fingerprint or operation/table label only. They must never retain raw SQL
when it could contain values or identifiers outside the approved table-name normalization.

### 4.3 Persisted records

Add a versioned `RuntimeObservation` contract and append-only persistence only after M7.1 proves the
existing `Evidence` contract cannot faithfully carry bounded observation summaries. Required fields:

```text
observation_id, analysis_run_id, verification_run_id?, phase, workload_id?,
trace_id?, span_id?, span_name, observed_at, duration_ms?, status,
symbol_key?, graph_node_id?, correlation_method?, correlation_confidence?,
attributes, redaction_count, sampled, exporter_status, retention_expires_at
```

`attributes` is an allowlisted dictionary. Retain only an evidence summary plus an optional local artifact
URI and SHA-256; do not persist a raw trace payload in PostgreSQL.

## 5. Work breakdown

Implementation status: M7.1–M7.7 are implemented. The local rehearsal passed with a healthy collector
and an intentionally unavailable collector; the pinned collector image is
`otel/opentelemetry-collector-contrib:latest@sha256:799dc6cf12c96192af37b5bdba804da8c10b3bc563b43cb90c3f3c58d9572ad6`.

### M7.1 — Telemetry contracts and correlation IDs

Implement:

- A typed `RuntimeCorrelationContext` owned by the application layer.
- A versioned `RuntimeObservation` contract fixture and validation tests.
- A generated W3C-compatible trace ID when no incoming trace context exists.
- A `TelemetryPort` so analysis, CLI, and API depend on an interface rather than an OpenTelemetry vendor.
- A no-op adapter that preserves the same API with zero export behavior.

Acceptance:

- A baseline, candidate, and fix context each carry the correct analysis run, commit, and phase.
- One workload context adds the expected workload and verification-run IDs.
- Invalid IDs, unsupported phases, oversized attribute keys/values, and non-allowlisted attributes are rejected.
- A no-op adapter creates no artifacts and cannot affect a verification status.
- Contract fixtures validate and round-trip without database access.

### M7.2 — Instrumentation helpers

Implement:

- A local OpenTelemetry adapter with explicit span helpers for the six span types in section 4.2.
- Application composition that starts/stops spans around analysis, workload, sandbox, comparison, and
  supported SQL execution boundaries.
- Exception-to-status mapping that records a safe error class, never raw exception text by default.
- Parent/child propagation from an API or CLI analysis span to downstream workload and verification spans.

Acceptance:

- The fixture candidate workload emits analysis, workload, sandbox, verification, and query spans.
- Every child span shares the analysis run ID and its trace ID with the root span.
- Span closure occurs for passed, failed, inconclusive, and exception paths.
- Instrumentation unit tests use an in-memory exporter; Docker is not required.
- Instrumented and no-op modes produce identical deterministic verification classifications.

### M7.3 — Local collector and export behavior

Implement:

- Docker Compose profile or equivalent local-only OTLP collector configuration.
- An exporter chosen solely through explicit settings: `none`, `memory`, or `otlp`.
- Bounded export timeout, retry count, batch size, queue size, and shutdown flush duration.
- A typed export-health record: `disabled`, `healthy`, `sampled_out`, `unavailable`, or `failed`.
- An in-memory exporter for deterministic tests and a local JSON/debug output only for developer inspection.

Acceptance:

- `memory` mode makes collected spans available to tests without a network listener.
- `otlp` mode exports one fixture trace to a disposable local collector.
- A refused collector connection returns `unavailable` evidence within the configured timeout.
- Collector failure neither fails a workload nor changes a comparison verdict.
- No local collector is started implicitly by a normal `lou analyze` invocation.

### M7.4 — Runtime-to-symbol correlation

Implement:

- A deterministic mapper from approved runtime names to `RepositoryGraphSnapshot` nodes.
- Mapping order: exact registered route/symbol key, normalized module/function name, then explicit workload
  declaration; do not use fuzzy or AI-based matching in M7.
- A `RuntimeCorrelation` result with `resolved`, `ambiguous`, or `unresolved` status, method, candidates,
  and confidence.
- An append-only evidence summary for mappings associated with the analysis run and workload.

Acceptance:

- The known fixture checkout request maps to its expected graph symbol and workload with confidence `1.0`.
- A renamed symbol becomes `unresolved` or `ambiguous`; it is never silently mapped to the old symbol.
- A missing graph node produces an unresolved evidence record and does not fail verification.
- Mappings have deterministic ordering and stable output for the same graph and spans.
- Tests cover route, function, table/query, missing, renamed, and ambiguous cases.

### M7.5 — Evidence report and API/CLI read paths

Implement:

- A `Runtime Correlation` section in JSON and Markdown evidence reports.
- Safe summary fields: telemetry availability, trace IDs when retained, workload, span counts, query metrics,
  resolved symbols, unresolved mappings, and confidence.
- Read-only CLI and API access through the existing report architecture; no report command may rerun workloads
  or query a collector.
- A machine-readable report schema/version assertion for consumers.

Acceptance:

- A correlated fixture run displays its trace/workload/commit/symbol linkage in both report renderers.
- An unavailable collector displays a concise observability warning while preserving verification evidence.
- No raw span attributes, SQL text, tokens, bodies, or headers appear in either renderer.
- The report reader works from persisted records after the collector is stopped.
- Snapshot tests prove stable ordering and safe omission behavior.

### M7.6 — Security, sampling, retention, and resilience tests

Implement:

- An allowlist/redaction module applied before export and before persistence.
- Deterministic head sampling keyed by analysis-run ID and configurable rate; verification-critical summary
  observations are retained locally even when detailed spans are sampled out.
- Maximum attribute count, value bytes, span count per run, artifact bytes, and retention duration settings.
- Retention cleanup that deletes only expired local telemetry artifacts and leaves core Lou evidence intact.
- Failure tests for collector outage, export timeout, malformed exporter response, redaction, payload rejection,
  sampling, and expired artifacts.

Acceptance:

- Test inputs containing representative tokens, cookies, authorization headers, SQL values, and request bodies
  cannot appear in exported or persisted telemetry bytes.
- A fixed sampling configuration chooses the same run IDs on repeated executions.
- Limit exceedance yields bounded `telemetry_limited` evidence, not an unbounded artifact or failed workload.
- Expired telemetry artifacts are removable without deleting verification evidence, findings, decisions, or
  remediation records.
- Collector/export failures are visible in reports and never block the baseline/candidate/fix workflow.

### M7.7 — Local end-to-end collector rehearsal

Implement:

- A disposable Docker-backed rehearsal that runs the known runtime regression with `otlp` enabled.
- Assertions that baseline/candidate trace IDs, commit SHAs, workload IDs, query spans, and correlated symbols
  are retained in the persisted report.
- A second execution with an intentionally unavailable collector that proves graceful degradation.
- Clean-clone instructions, result marker, retained artifact location, and teardown behavior.

Acceptance:

- The successful rehearsal links one measured candidate regression to the expected workload, commit, query
  evidence, and repository graph symbol without any hosted service.
- The outage rehearsal records `unavailable` observability evidence while the deterministic verdict remains
  correct.
- Both runs clean up disposable containers, databases, worktrees, and telemetry artifacts on success/failure.
- A clean checkout with Python dependencies and Docker Desktop reproduces both result markers.

## 6. Settings and default limits

Use conservative local defaults, all configurable through typed settings:

| Setting | Default | Reason |
|---|---:|---|
| exporter | `none` | no surprise network or Docker dependency |
| export timeout | 2 seconds | observability must not delay verification materially |
| export retries | 0 | local mode should fail fast and report state |
| sample rate | 1.0 for fixture, 0.1 otherwise | predictable demo, bounded normal use |
| max spans/run | 500 | bounds memory and artifacts |
| max attributes/span | 24 | limits accidental data collection |
| max attribute value | 256 bytes | avoids source/log payload capture |
| max local artifact | 1 MiB | keeps reports portable |
| retention | 7 days | local debugging without permanent trace storage |

No setting may permit recording explicitly denied classes such as credentials, bodies, raw SQL values, or
environment contents.

## 7. Test plan

| Layer | Required proof |
|---|---|
| contracts | contexts and observations validate, reject unsafe fields, and round-trip fixtures |
| instrumentation | in-memory spans have parentage, safe names, correct phase/workload/commit attributes |
| exporter | disabled, memory, OTLP healthy, timeout, and unavailable behavior are typed and bounded |
| correlation | exact, normalized, workload, missing, renamed, and ambiguous mappings are deterministic |
| security | redaction/allowlist, sampling, byte/count limits, and retention cleanup are enforced |
| reporting | persisted runtime summaries render safely and remain available with collector offline |
| end to end | local collector and outage rehearsals preserve deterministic regression verdicts |

## 8. Definition of done

M7 is complete only when all statements are true:

- A local fixture request emits trace and query observations linked to an analysis run, commit, workload, and
  expected repository symbol.
- The evidence report displays correlation state and confidence, including unresolved or unavailable states.
- Sensitive material is absent from trace exports, persisted observations, and rendered reports.
- Sampling, retention, and payload limits are configured and tested.
- Exporter/collector failure is non-blocking and recorded as missing observability, never a verification result.
- A clean-clone Docker rehearsal proves both a healthy local collector path and an outage path.

## 9. Follow-on boundary

M7 produces trustworthy runtime observations for M8 staging gates and M9 calibration. It must not introduce
production deployment access, autonomous rollout decisions, or learned risk scoring. Those stages consume
M7's bounded evidence rather than weakening its safety limits.
