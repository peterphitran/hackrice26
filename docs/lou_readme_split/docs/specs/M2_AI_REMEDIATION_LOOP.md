# M2 — AI Remediation Loop

Status: proposed  
Priority: P0  
Depends on: M1 runtime regression detection, EV-007 independent fix verification  
Extends: `RemediationOrchestrator`, `FixVerifier`, `PatchArtifact`, `LouDecision`, and persisted evidence

## 1. Goal

Given a persisted Lou analysis run with a verified candidate finding, Lou can produce a bounded patch
proposal, independently verify it in a fresh worktree, make a policy-bounded decision, and prepare or
publish a pull request only when the evidence permits it.

```text
saved analysis run
→ assemble trusted remediation inputs
→ bounded context bundle
→ diagnosis
→ patch proposal
→ patch validation
→ independent fix verification
→ deterministic decision
→ dry-run or trusted PR publisher
```

The AI proposes. The verifier and policy engine determine whether the proposal is trustworthy.

## 2. Current baseline

The repository already contains most of M2's domain building blocks:

- `AgentContextBundle` applies file, byte, token, and unresolved-relationship budgets.
- `RemediationOrchestrator` implements resumable context → diagnose → patch → validate → verify → decide
  states with attempt, wall-time, token, cost, and duplicate-patch limits.
- `DeterministicMockProvider` and `GeminiProvider` implement one provider contract.
- `validate_patch` constrains patch paths and change budgets.
- `FixVerifier` creates a clean worktree, applies the patch, reruns selected workloads, and classifies the
  result as improvement, regression, no material change, or inconclusive.
- `PersistedFixVerifier` retains fix verification evidence.
- INT-003 proves a good recorded patch passes and a deliberately ineffective patch fails.

M2 is incomplete because those pieces are not yet assembled from an arbitrary persisted analysis run by a
user-facing CLI/API workflow. Orchestration snapshots, agent attempts, patch artifacts, and publication
attempts are also not durable first-class records.

## 3. Scope

### In scope

- A user-facing local remediation workflow for the supported Python fixture/repository profile.
- Recorded mock and optional live provider adapters with identical input/output contracts.
- Durable remediation state, attempt history, patch metadata, and verification evidence.
- CLI and FastAPI composition around the existing remediation service.
- A credential-free PR publication dry run.
- Optional GitHub App publisher guarded by verification and policy.
- Reproducible good-patch and bad-patch end-to-end rehearsals.

### Out of scope

- Auto-merge, auto-deploy, or autonomy above A3.
- Agent-created shell commands, agent-controlled GitHub credentials, or agent-controlled policies.
- Multi-language automatic repair.
- Long-running distributed workflow infrastructure beyond what a bounded local run requires.
- UI work; the CLI, API, and Markdown evidence report are the M2 interfaces.

## 4. User-facing contracts

### CLI

```bash
lou remediate --run <analysis-run-id> --provider mock --output json
lou remediate --run <analysis-run-id> --provider gemini --output json
lou remediation-status --remediation <agent-run-id> --output json
lou publish-remediation --remediation <agent-run-id> --dry-run
lou publish-remediation --remediation <agent-run-id> --publish
```

`--provider mock` is the default local mode. `--provider gemini` is opt-in and must fail safely when the
SDK, key, quota, or network is unavailable. `--publish` must require an explicit acknowledgement flag in
addition to a valid A3 decision; it is never implied by `lou remediate`.

### API

```text
POST /analysis/{analysis_run_id}/remediations
GET  /remediations/{agent_run_id}
GET  /remediations/{agent_run_id}/report
POST /remediations/{agent_run_id}/publication-dry-run
POST /remediations/{agent_run_id}/publish
```

The create endpoint returns `202` with a versioned `agent_run_id`, current stage, status, and whether an
identical remediation was reused. It must not expose raw provider errors, repository content, credentials,
or database sessions. Initial local composition may run synchronously behind the endpoint, but its response
and durable state must support a future worker without a contract change.

## 5. Domain contracts and persistence

Existing shared contracts remain the public interchange format. Add versioned contracts only where the
existing models cannot represent durable remediation identity.

| Contract / record | Required fields | Purpose |
|---|---|---|
| `RemediationRequest` | analysis run ID, provider mode, limits, policy revision, force token | immutable request identity and deduplication |
| `AgentRun` | agent run ID, analysis run ID, input fingerprint, stage, status, limits, usage, timestamps | durable orchestration state |
| `RemediationAttempt` | attempt number, diagnosis result ID, patch hash, validation result, termination reason | append-only audit trail |
| `PatchArtifact` | existing fields plus retained artifact hash/path where applicable | validated patch identity |
| `PublicationPlan` | source commit, branch name, title, body, patch hash, evidence report hash | credential-free intended PR |
| `PublicationResult` | dry-run/published/denied, provider reference, decision ID, timestamp | trusted publishing evidence |

Add migrations only for durable state that cannot be reconstructed from existing evidence. Expected tables:

```text
agent_runs
remediation_attempts
patch_artifacts
publication_attempts
```

Store provider metadata, usage, hashes, and typed abandonment reasons. Do not store provider API keys,
unredacted environment values, or raw chain-of-thought. Retain full prompts only if they are explicitly
classified, redacted, access-controlled artifacts; this is not required for M2.

## 6. Trusted composition

### 6.1 Remediation input assembler

Implement a read-only service that loads and validates the following from one `analysis_run_id`:

```text
analysis run identity
repository context
candidate verification result
candidate finding
selected workload definitions
baseline and candidate observations
current policy revision
artifact root and repository root
```

It must reject a request before creating `agent_runs` when:

- the analysis run does not exist;
- it has no candidate finding or comparable candidate evidence;
- its repository path is unavailable or no longer resolves to the expected commit;
- selected workloads are absent, untrusted, or inconsistent with the saved plan;
- required artifacts fail SHA-256 validation; or
- the policy revision is unsupported.

### 6.2 Orchestration service

Do not rewrite the existing `RemediationOrchestrator` decision and verification logic. Add a thin
application service that:

1. creates or reuses an `AgentRun` using a stable request fingerprint;
2. saves the JSON-serializable state after every orchestration stage;
3. invokes `RemediationOrchestrator.step()` until stopped, cancelled, or handed to a future worker;
4. persists provider usage, patch validation, verification results, and final `LouDecision` atomically at
   their respective stage boundaries; and
5. returns a safe status view without re-running completed work.

LangGraph, when introduced, is an adapter that schedules those stage transitions and checkpoints the same
state. It must not duplicate patch validation, scoring, verification, or publishing rules.

### 6.3 Provider selection

```text
mock provider     default; no network, no key, deterministic fixture behavior
live provider     explicit opt-in; bounded timeout, retries, cost, and schema validation
unavailable       typed abandoned state; no fallback to uncontrolled behavior
```

All providers receive only `AgentContextBundle.provider_data_json()`. Repository text remains marked as
untrusted data. Providers return diagnosis/plan and a structured patch proposal; trusted code produces or
validates the unified diff and determines artifact hashes.

### 6.4 Independent verification

`FixVerifier` remains separate from the provider. For every accepted patch it must:

1. create a clean detached worktree at the candidate commit;
2. run `git apply --check` and apply the exact validated bytes;
3. create a verifier-owned commit with hooks disabled;
4. rerun the saved selected workloads under the saved limits;
5. compare baseline → fix and fix → candidate; and
6. persist fix evidence before the decision stage receives a verdict.

## 7. Decision and publication policy

| Result | Maximum action | Publication behavior |
|---|---|---|
| invalid patch, failed verification, regression | `report` | denied |
| inconclusive verification or missing evidence | `recommend` | denied |
| verified improvement but A0–A2 policy | `generate_patch` | dry-run allowed; publish denied |
| verified improvement and explicit A3 policy | `open_pr` | explicit trusted publish may proceed |

The publisher is a trusted adapter. It receives a validated patch, verified fix evidence, immutable
publication plan, and `LouDecision`; it never receives an agent prompt or allows the provider to choose a
repository, branch target, credentials, or PR permissions.

The initial GitHub implementation must use a GitHub App or a dedicated least-privilege token that can write
only the configured repository. Webhook handling, installation credential refresh, and event-driven CI
checks are M1/integration work; M2 publishing may begin as an explicit local command.

## 8. Work breakdown

### M2.1 — Contract and migration freeze

- Define `RemediationRequest`, `AgentRun`, `RemediationAttempt`, `PublicationPlan`, and `PublicationResult`.
- Add JSON fixtures, model validation, and backward-compatible persistence migrations.
- Add unique keys for identical remediation input and append-only constraints for attempts.

Done when contract fixtures round-trip and concurrent identical requests create one durable agent run.

### M2.2 — Persisted input assembly and state store

- Implement the read-only assembler and `AgentRunRepository`.
- Convert persisted context/evidence/workload data into `OrchestrationInputs` without fixture-only helpers.
- Save and resume orchestration snapshots with immutable input fingerprints.

Done when a process restart resumes from each stage without recreating prior verification evidence.

### M2.3 — CLI and API remediation adapters

- Add `lou remediate`, `lou remediation-status`, and versioned FastAPI endpoints.
- Support mock provider first and report `agent_run_id`, stage, attempt, costs, verdict, and decision.
- Map invalid inputs to `400`, missing records to `404`, policy denials to `409`, and operational failures to
  safe `503` responses.

Done when CLI and API invoke the same application service and report byte-identical persisted evidence.

### M2.4 — Live provider hardening

- Wire explicit provider configuration, dependency checks, timeout, schema validation, and typed error mapping.
- Enforce token/cost budgets when provider usage is available; otherwise record `usage_unavailable` and apply
  the configured conservative ceiling.
- Add recorded responses for malformed JSON, timeout, quota, unsafe target, unchanged source, and retry.

Done when every provider failure ends in a typed `abandoned` outcome without persisting a patch attempt.

### M2.5 — Independent remediation rehearsal

- Drive good and deliberately bad patches through the user-facing composition rather than INT-003 helpers.
- Verify both selected workloads, persist all evidence, and render the normal evidence report.
- Add cancellation, resume, duplicate-patch, budget, and policy-denial scenarios.

Done when a clean clone passes the rehearsal with Docker and no network/API key.

### M2.6 — Publication dry run and GitHub adapter

- Produce an immutable PR title/body/branch plan from the verified report.
- Implement `--dry-run` without credentials.
- Add GitHub publisher with branch-collision handling, idempotency key, protected base branch, and evidence link.
- Require explicit publish acknowledgement plus A3 decision and passed verification.

Done when a mocked GitHub adapter proves allowed/denied behavior and a disposable test repository receives one
PR only for the verified-good patch.

## 9. Test plan

| Layer | Required proof |
|---|---|
| contracts | fixtures validate; schema revisions and unknown fields fail safely |
| persistence | duplicate/resume/concurrent agent-run requests are idempotent; attempts are append-only |
| orchestration | each stage resumes; max attempt/time/token/cost budgets stop safely |
| provider | mock and live adapters normalize output; malformed/unavailable responses abandon safely |
| patch validation | path escape, binary, secret, workflow, oversized, and unchanged patches are rejected |
| verification | good patch improves both workloads; bad patch fails; tool failure is inconclusive |
| decision | only passed independent evidence can reach A3/open PR |
| publisher | dry-run is credential-free; denied actions never call the GitHub client; repeated publish is idempotent |
| end to end | Docker/Postgres clean-clone rehearsal produces report, hashes, and result marker |

## 10. Definition of done

M2 is complete when all statements are true:

- A user can run `lou remediate --run <id> --provider mock` against a persisted regression run.
- The system can resume safely after a process restart at every stage boundary.
- A good recorded patch is independently verified as an improvement and a bad patch is rejected.
- Reports show diagnosis metadata, patch hash, verification evidence, decision, and publication status.
- Live-provider failure is safe, bounded, typed, and does not prevent mock-mode use.
- A publication dry run works without GitHub credentials.
- A real PR can only be published by a trusted adapter after explicit A3 authorization and passed verification.
- Unit, Docker/Postgres integration, and clean-clone rehearsal commands are documented and passing.

## 11. Follow-on boundaries

M2 creates verified remediation records for M3–M10. It does not need semantic retrieval, production traces,
canary deployment, learned models, or a React UI. Those later features consume M2 evidence; they must not
weaken the M2 verifier/policy boundary.
