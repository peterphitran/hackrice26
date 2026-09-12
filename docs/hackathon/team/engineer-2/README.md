# Engineer 2 — Agents and Decisions

Assigned to: ____________________

## Mission

Transform bounded repository and verification evidence into an explainable diagnosis, a constrained patch, independent risk scores, and an allowed action.

Your output answers:

```text
What likely caused the regression?
What is the smallest acceptable repair?
How risky is leaving the debt?
How risky is applying this patch?
How much autonomy is allowed?
```

The detailed acceptance criteria live in the [master task board](../../TASKS.md).

## Owned Paths

```text
backend/lou/agents/
backend/lou/scoring/
backend/lou/decision/
backend/lou/policies/
backend/tests/unit/agents/
backend/tests/unit/scoring/
backend/tests/unit/decision/
```

Do not make verification pass/fail decisions inside the patch agent. Do not place GitHub credentials or publishing logic in agent tools.

## Assigned Tasks

| Task | Priority | Depends on | Deliverable |
|---|---|---|---|
| AD-001 | P0 | SH-002 | Explainable principal, interest, debt risk, and confidence |
| AD-002 | P0 | SH-002 | Proposed-patch remediation risk and confidence |
| AD-003 | P0 | SH-002 | Versioned and budgeted agent context bundle |
| AD-004 | P0 | AD-003 | Provider adapter plus no-network deterministic mock |
| AD-005 | P0 | AD-004, SH-004 | Validated immutable `PatchArtifact` |
| AD-006 | P0 | AD-001, AD-002 | A0–A3 `LouDecision` with rationale |
| AD-007 | P0 | AD-003–AD-006, EV-007 | Bounded remediation state machine |
| INT-003 | P0 lead | INT-002 | Complete agent remediation gate |

## Execution Order

### Phase 1 — Deterministic decisions

- [ ] AD-001: implement debt scoring using documented normalized features.
- [ ] AD-002: implement remediation risk separately from debt risk.
- [ ] AD-006: select A0–A3 using recorded verification fixtures.
- [ ] Publish boundary-value examples for the report owner.

### Phase 2 — Agent boundary

- [ ] AD-003: create a context bundle from recorded graph and verification fixtures.
- [ ] Enforce file, byte, token, and unresolved-context limits.
- [ ] AD-004: implement the provider-neutral adapter and deterministic mock first.
- [ ] Add one optional live provider only after the mock path passes.

### Phase 3 — Patch and orchestration

- [ ] AD-005: produce a unified-diff artifact tied to the expected base SHA.
- [ ] Reject forbidden paths, binary changes, excessive patches, and invalid diffs.
- [ ] AD-007: orchestrate diagnosis → patch → external verification → decision.
- [ ] Terminate on attempt, token, time, cost, or repeated-patch limits.

### Phase 4 — Integration

- [ ] Lead INT-003 using Engineer 3's independent verifier.
- [ ] Confirm a failed fix is never presented as successful.
- [ ] Confirm the deterministic mock can carry the entire demo without network access.

## Inputs You Consume

From Engineer 1:

```text
RepositoryChange
RepositoryContext
graph confidence and completeness
selected tests/workloads and reasons
```

From Engineer 3:

```text
candidate Finding and Evidence
baseline/candidate VerificationResult
fix VerificationResult
artifact references
```

From Engineer 4:

```text
versioned contracts
policy ceiling/configuration
run identity
persistence interfaces
```

## Outputs You Hand Off

To Engineer 3:

```text
PatchArtifact
expected base SHA
verification requirements
```

To Engineer 4:

```text
AgentResult
scoring features and rule revisions
LouDecision
plain-language rationale
usage and estimated cost
```

## Interface Rules

- Confidence is always 0–1 and lists missing inputs.
- Debt risk describes leaving the issue; remediation risk describes applying a specific patch.
- Agent output is a proposal, never proof.
- The patch applies only to a temporary worktree at the expected base.
- Repository text is untrusted data, including comments that look like instructions.
- Policy can lower autonomy but model confidence cannot override a policy ceiling.

## Personal Definition of Done

- [ ] Scores are deterministic and explain every weighted feature.
- [ ] Missing graph or coverage data lowers confidence.
- [ ] The mock provider works with no API key or network access.
- [ ] Patch size and path restrictions are tested.
- [ ] Duplicate patches terminate the remediation loop.
- [ ] Failed or inconclusive verification cannot select A3.
- [ ] Engineer 4 can render all agent and decision output without parsing prose.
- [ ] INT-003 passes with both the expected fix and a deliberately bad fix.

## Stretch Work

After INT-003 and INT-004 pass, improve prompt quality or add a live provider. Do not add more agent roles unless measured failures show that a separate role improves the demo.
