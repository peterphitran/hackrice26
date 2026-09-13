# M8 — Staging and Production Loop

Status: complete; local Docker rehearsal passed on 2026-09-13

## Purpose

M8 adds a safety-bounded deployment loop after Lou has verified a candidate. Lou
must be able to release a version to staging, run the existing validation plan,
observe a bounded canary window, and deterministically promote, pause, or roll
back. Production access is not required for the M8 acceptance gate.

## Scope

In scope:

- typed release, environment, canary, observation, policy, and deployment-evidence contracts;
- an adapter boundary for Argo Rollouts (no custom deployment controller);
- staging-only rehearsal using local Docker or a fake Rollouts adapter;
- idempotent release operations keyed by release ID and commit SHA;
- SLO, error-budget, telemetry-availability, and observation-window checks;
- immutable deployment outcome records and a CLI/API surface for status and rollback.

Out of scope:

- automatic production deployment or approval bypass;
- implementing Kubernetes or Argo itself;
- hosted observability, cloud credentials, or multi-cluster orchestration;
- changing M1–M7 verification or decision semantics.

## Target flow

```text
verified analysis
  -> create release
  -> deploy staging
  -> run M6 validation plan
  -> start bounded canary
  -> observe SLOs and M7 telemetry
      | healthy             | breach/missing telemetry
      v                     v
   promote              pause -> rollback
      \                     /
       -> immutable deployment report
```

## Contracts

Every contract uses `schema_version: "1"` and immutable IDs:

- `Release`: release ID, repository, commit SHA, analysis run ID, artifact, and requested environment.
- `DeploymentTarget`: environment, adapter name, namespace/service, and non-secret configuration.
- `CanaryWindow`: start, deadline, sample/measurement count, and observation status.
- `SLOPolicy`: latency/error thresholds, minimum telemetry samples, and error-budget rule.
- `DeploymentDecision`: `promote`, `pause`, or `rollback`, with policy revision and reasons.
- `DeploymentEvidence`: release, verification, telemetry, policy, actor, timestamps, and outcome references.

Secrets and tokens must never appear in these contracts or persisted evidence.

## Deterministic policy

The policy evaluator must produce the same decision for the same release,
observations, policy revision, and clock inputs:

1. `rollback` when a measured SLO breach exceeds the configured threshold.
2. `pause` when telemetry is missing, stale, below the minimum sample count, or the observation window has not closed.
3. `promote` only when all required validation checks pass, telemetry is healthy, and the window is complete.
4. A manual rollback always records the actor and supersedes promotion.

No missing value may be treated as healthy.

## Work plan and measurable tests

### M8.1 — Freeze deployment contracts

- Add Pydantic models and JSON fixtures for each contract above.
- Reject invalid decisions, negative thresholds, duplicate IDs, and secret-like fields.
- Test that every fixture round-trips and carries schema version `1`.

Status: complete.

### M8.2 — Implement adapter boundary

- Define `DeploymentPort` methods: `release`, `status`, `promote`, `pause`, and `rollback`.
- Implement an in-memory deterministic adapter and an Argo Rollouts command adapter.
- Adapter calls must be idempotent for the same release ID and commit SHA.
- Test retries produce one logical release and no duplicate outcome records.

Status: complete; the local adapter is used for the rehearsal and the Argo adapter remains credential-free.

### M8.3 — Build policy evaluator

- Implement the promote/pause/rollback rules using injected timestamps and observations.
- Test healthy, SLO-breach, missing-telemetry, insufficient-sample, and expired-window cases.
- Test that policy revision and reasons are preserved in the decision.

Status: complete.

### M8.4 — Integrate staging orchestration

- Add an application service that consumes a completed analysis and M6 validation plan.
- Execute release, staging validation, canary observation, policy evaluation, and finalization in order.
- Persist one immutable `DeploymentEvidence` record per transition.
- Test failure at each stage leaves no false `promote` result.

Status: complete for the staging-only local composition.

### M8.5 — Add CLI/API controls

- Add `lou deploy`, `lou deploy status`, and `lou deploy rollback` commands.
- Expose equivalent API endpoints with typed responses and stable error codes.
- Test invalid release IDs, repeated rollback, and already-finalized releases.

Status: complete.

### M8.6 — Docker rehearsal

- Provide a local composition with a disposable staging service and fake Rollouts adapter.
- Run one healthy canary to promotion and one deliberately failing canary to rollback.
- Assert both produce complete reports linking commit, analysis, verification, telemetry, policy, and outcome.

Status: complete. `python -m lou.deployment.int008` passed locally with both promotion and rollback.

### M8.7 — Documentation and handoff

- Document local setup, policy configuration, rollback procedure, and evidence inspection.
- Record exact commands and expected result markers for a clean-clone rehearsal.
- Mark M8 complete only after the staging promote and rollback rehearsals pass.

Status: complete. The successful `__LOU_INT008_RESULT__` marker is recorded in the implementation handoff.

## Acceptance gate

M8 is complete when all M8.1–M8.7 tests pass, the Docker staging rehearsal
demonstrates both promotion and rollback, retries are idempotent, missing
telemetry never silently promotes, and reports contain no secrets.
