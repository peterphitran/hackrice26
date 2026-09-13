# Engineer 2 deterministic decisions

Implements **AD-001, AD-002, and AD-006** only. These pure functions require no
network, database, agent provider, executor, or GitHub credentials. They select
actions; they do not perform them or determine verification pass/fail.

## Public API and contract compatibility

```python
from lou.scoring import DebtInputs, RemediationInputs, score_debt, score_remediation
from lou.decision import decide_autonomy
from lou.policies import AutonomyPolicy
```

The shared contracts have no normalized scoring input/output models or policy
configuration model. The small local DTOs inherit `contracts.models.ContractModel`
and retain `schema_version="1"`, rejecting unknown fields and other versions.
All numeric inputs must be finite numbers in **[0, 1]**; strings, booleans,
out-of-range numbers, NaN, and infinity are rejected. `None` means unobserved;
explicit zero is an observation. Input DTOs are frozen.

`decide_autonomy` accepts these observations, recalculates the scores, and returns
the existing **`contracts.LouDecision`**. It consumes `PatchArtifact` and
`VerificationResult` directly. No shared schema or fixture has been changed.
Caller-supplied decision/run IDs make repeated calls deterministic; there are
no generated timestamps, random IDs, or mutations of the inputs.

Engineer 4 can render these fields directly:

| Field | Contents |
| --- | --- |
| `debt_risk`, `remediation_risk`, `confidence` | Independent normalized scores; remediation can be null |
| `autonomy_level`, `action` | A0/report, A1/recommend, A2/generate_patch, A3/open_pr |
| `metadata.scores.debt` | Principal, interest, debt risk, confidence, source inputs, missing inputs, rule revision |
| `metadata.scores.remediation` | Patch risk, confidence, source inputs, missing inputs, adjustments, rule revision |
| `metadata.scores.*.components` | Component values and per-feature contributions |
| `*.contributions[]` | Feature, observed value, effective normalized value, weight, contribution, missing flag, explanation |
| `rationale` | Summary, reasons, missing inputs, and structured gates with observed/required values |
| `rationale.declined`, `rationale.decline_reasons` | Explicit A0 decline on identity mismatch, with check code and expected/actual values |
| `metadata.signals` | Debt and remediation raw/normalized values by signal, plus missing-input list |
| `metadata` | Rule/policy revision, pre-policy level, policy snapshot, patch ID, expected fix commit, workload plan, verification results |

`model_dump(mode="json")` returns JSON-ready values. Prose is supplemental;
no consumer needs to parse an explanation to obtain a feature or decision gate.
For protective features, `observed_value` is the positive protection value and
`normalized_value` is its transformed **risk** value, including any migration floor.

## Debt rule: `debt-v1`

The first two formulas come from `docs/hackathon/README.md`:

```text
P = .35 complexity + .35 coverage_deficit + .30 estimated_patch_size
G = .30 churn + .25 graph_centrality + .25 runtime_impact + .20 path_criticality
I = P × G
D = (P + I) / 2
confidence = (observed debt features / 7) × evidence_confidence
```

P is principal, I is interest, and D is debt risk. All are normalized debt points,
not dollars, hours, probabilities, or time-calibrated forecasts. D's aggregation
is a **local v1 assumption**: P and I are each bounded by one, so dividing their
sum by two preserves the shared contract's [0, 1] range.

Every missing debt feature contributes zero, with no weight redistribution. This
is a **lower-bound estimate**, not evidence of no debt. Removing evidence can
never increase debt priority. Missing `evidence_confidence` makes confidence zero.
All missing fields are listed. `input_completeness` counts only the seven features;
the evidence confidence factor is recorded separately in the source inputs.

Complexity, estimated repair size, churn, centrality, observed runtime harm, and
path criticality increase with burden. Coverage deficit is `1 - coverage` for
the affected code. The seven inputs are supplied already normalized by their
producers; this module does not guess raw-count denominators or extract code.

## Remediation rule: `remediation-v1`

```text
R = .20 blast_radius
  + .15 criticality
  + .15 (1 - coverage)
  + .15 irreversibility
  + .15 (1 - verification_strength)
  + .10 patch_size
  + .05 schema_migration_risk
  + .05 data_migration_risk

irreversibility = 1 - reversibility
if either migration risk is positive or unknown:
    irreversibility = max(irreversibility, .50)

confidence = (observed risk features / 8)
           × context_completeness
           × evidence_confidence
```

These weights and the migration floor are **local v1 assumptions**, not calibrated
failure probabilities. Each missing risk feature contributes its worst-case
normalized risk of one. Unknown coverage, reversibility, and verification strength
therefore supply no protection. Missing context/evidence confidence is zero.
An explicitly partial context also reduces confidence, even if all eight risk
features were supplied. The migration adjustment keeps a schema/data change from
being described as trivially reversible despite a caller's optimistic estimate.

Blast radius means the normalized scope of affected code; criticality means
the consequence of disruption; patch size means the proposed change's normalized
size. Coverage applies to affected code. Reversibility measures demonstrated ease
of rollback. Verification strength describes the breadth/relevance of independent
checks, **not their verdict**. Schema and data migration risk are distinct;
zero explicitly asserts no such risk. `context_completeness` includes graph
extraction completeness and other unresolved context.

Debt's `estimated_patch_size` estimates today's repair effort; remediation's
`patch_size` evaluates the proposed change. The two are deliberately independent.

Products, complements, and component sums use rational arithmetic over the decimal
representation of inputs before returning contract floats. This avoids basic
decimal threshold drift (for example, .35 × .4 + .35 × .4 + .30 × .4 = .4)
without using an epsilon that could admit an actually sub-threshold observation.

## Autonomy rule: `autonomy-v1`

Decision confidence is the minimum of debt and remediation confidence. It is zero
when remediation has not been assessed. Each failed gate supplies
a ceiling; the lowest ceiling wins. The following rules are **local v1 assumptions**:

| Level | Requirements in addition to lower-level requirements |
| --- | --- |
| A0 report | Always available |
| A1 recommend | Debt risk ≥ .20 and confidence ≥ .50 |
| A2 generate local patch | Remediation assessed; no missing scoring inputs; context completeness = 1; confidence ≥ .75; remediation risk ≤ .40; blast radius, criticality, and patch size each < .80; reversibility ≥ .50; both migration risks = 0 |
| A3 open PR | Confidence ≥ .90; remediation risk ≤ .20; coverage, reversibility, and verification strength each ≥ .80; nonempty patch; all expected fix workloads passed with matching run, fix commit, and patch hash |

The organization policy is applied **last**, as `min(evidence_level, max_autonomy)`.
An explicitly supplied `AutonomyPolicy()` has ceiling **A2** and revision `local-v1`;
omitting policy declines to A0 so removing a restrictive policy cannot raise autonomy. A ceiling
of A3 only permits consideration of A3; it never overrides a failed evidence gate.
Migration and extreme individual-risk gates prevent averages from hiding danger.
High supplied confidence cannot override missing features or partial context.

### Verification handoff assumption

Supply only the independently produced results for the **final fix attempt** under
consideration, plus `required_workload_ids` from the trusted complete verification
plan, `expected_fix_commit_sha`, and `expected_verification_attempt_id` from the
trusted controller. Pass the existing `AnalysisJob` and the exact `patch_content`
bytes that a later trusted publisher would consume. Earlier levels can be selected
before verification exists when policy is explicitly supplied. Empty plans and
partial results cannot permit A3.

The shared `VerificationResult` has no patch identifier. The smallest compatible
binding uses **`VerificationResult.metadata["patch_sha256"]`** and
**`metadata["verification_attempt_id"]`**, populated by the independent verifier.
The decision recomputes SHA-256 from `patch_content`, compares it to the patch
artifact and each result, and compares the patch base commit with the job's
candidate commit. A read-only local Git ancestry check requires the fix commit to
descend from or equal that candidate commit. Every supplied result must be
`phase="fix"`, belong to the patch's analysis run, match the expected fix commit
and attempt ID, and identify the exact patch hash. Required workloads must match
the job's verification plan and each must have a result.
Any supplied `failed` or `inconclusive` status prevents A3, even alongside passes.
Candidate/baseline passes cannot permit A3. Stale/wrong-run/wrong-patch results
produce A0 with `rationale.declined=true` and one structured `decline_reasons`
entry per mismatch: `code`, `expected`, and `actual`.

This layer trusts the verifier's status and the controller's plan; it does not
inspect metrics, run workloads, choose verification thresholds, validate patch
semantics, or determine whether the fix improved behavior. Those remain Engineer 3's
and the later patch-validation tasks' responsibilities. The trusted publisher must
reuse or rehash the same `patch_content` bytes immediately before publication;
this decision cannot attest to a future write. Verification metadata is an
integration convention requiring agreement, not an independently signed record.

## Runnable example and boundary examples

From `backend/`, with the development environment active:

```python
from lou.scoring import DebtInputs, RemediationInputs
from lou.decision import decide_autonomy
from lou.policies import AutonomyPolicy

debt = DebtInputs(
    complexity=0.8,
    coverage_deficit=0.4,
    estimated_patch_size=0.2,
    churn=0.6,
    graph_centrality=0.7,
    runtime_impact=0.9,
    path_criticality=1.0,
    evidence_confidence=1.0,
)
remediation = RemediationInputs(
    blast_radius=0.1,
    criticality=0.1,
    coverage=0.9,
    reversibility=1.0,
    verification_strength=1.0,
    patch_size=0.1,
    schema_migration_risk=0.0,
    data_migration_risk=0.0,
    context_completeness=1.0,
    evidence_confidence=1.0,
)
decision = decide_autonomy(
    decision_id="example-1",
    analysis_run_id="run-1",
    debt_inputs=debt,
    remediation_inputs=remediation,
    policy=AutonomyPolicy(),
)
assert decision.autonomy_level == 2
assert decision.debt_risk == 0.4272
assert decision.remediation_risk == 0.06
print(decision.model_dump_json(indent=2))
```

These boundary cases are covered in the owned unit tests:

| Inputs | P | I | D | Debt confidence |
| --- | --- | --- | --- | --- |
| Seven debt features zero, evidence confidence 1 | 0 | 0 | 0 | 1 |
| Seven debt features .5, evidence confidence 1 | .5 | .25 | .375 | 1 |
| Seven debt features 1, evidence confidence 1 | 1 | 1 | 1 | 1 |
| All debt inputs missing | 0 | 0 | 0 | 0 |
| Runnable example above | .48 | .3744 | .4272 | 1 |

| Patch/decision case | Expected result |
| --- | --- |
| All risk factors 0, protections 1, context/confidence 1 | R = 0, confidence = 1 |
| All risk factors 1, protections 0, context/confidence 1 | R = 1, confidence = 1 |
| All patch inputs missing | R = 1, confidence = 0, A0 |
| Runnable example, no fix verification, explicit local policy | R = .06, A2 |
| Same example, policy omitted | A0 |
| Same example, valid bound passed fix evidence and A3 policy | A3 |
| Same example, failed or inconclusive fix | At most A2 |
| Same example, any missing risk input or partial context | At most A1 |
| Safe patch except schema migration risk 1, claimed reversibility 1 | R = .125, effective irreversibility = .5, at most A1 |

## Integration still needed later

1. **Engineer 1:** supply normalized complexity, churn, centrality, impact scope,
   and graph completeness with agreed normalization scales and evidence provenance.
   Unresolved relationships must reduce supplied context completeness; a nonempty
   graph does not imply complete extraction.
2. **Engineer 3:** supply affected-code coverage, normalized runtime harm,
   verification strength, and independently passed/failed/inconclusive fix results
   tied to the patch hash, workload plan, and exact fix commit.
3. **Engineer 4/team:** approve these heuristic thresholds/weights, normalization
   scales, policy revision/ceiling, and patch-hash metadata convention; wire the
   shared decision into the report and application service. The frozen fixtures
   contain neither all normalized signals nor a bound passed fix; missing values
   must stay missing rather than being inferred from their prose or arbitrary metrics.
4. **Later Engineer 2 work:** bounded context bundle, deterministic mock adapter,
   validated patch artifacts, and bounded orchestration (AD-003–005 and AD-007).
   Those are intentionally outside this deterministic-first implementation.

The fixture compatibility test demonstrates using `Finding.confidence` and
`RepositoryContext.completeness` directly with explicit sample normalized features.
It consumes the existing failed candidate verification unchanged with explicit policy
and produces A1 because the fixture graph is only .95 complete. It does not claim those illustrative
feature values were derived from the fixture.

## Verification commands

Run from `backend/` with its virtual environment active:

```text
python -m pytest
ruff check .
ruff format --check .
mypy apps contracts lou
```
