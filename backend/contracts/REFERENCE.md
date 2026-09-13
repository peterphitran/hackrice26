# Contract Reference

## M5 version-two assessments

The additive M5 contracts in `risk.py` use `schema_version: "2"` and reject
unknown fields. `DebtAssessment` records principal, interest, debt risk,
confidence, missing inputs, scenario bounds, and each contributing feature.
`RemediationRisk` records patch risk, confidence, missing inputs, hard risk flags,
and the same feature details. `AutonomyDecision` binds both assessments to the
expected-value result, requested and evaluated policy revisions, evidence level,
organization and product ceilings, final action, and limiting reasons. A4 and
A5 are recognized as policy levels but the contract rejects a product ceiling
above A3. `CostEstimates` and `RiskSignals` are version-two input records;
unmeasured values remain `null`. Expected value is in engineering hours over
an explicit day horizon, and its bounds are labeled scenarios until calibrated
outcome data exists.

Version-one `LouDecision` remains the persistence and CLI envelope. Its
`metadata.m5` field contains the complete version-two `AutonomyDecision`.

This is the field-level reference for Lou's version-one shared contracts. Use it
when producing, consuming, or reviewing data passed between workstreams.

The Python source of truth is `models.py`. This document explains the intended
meaning of each field; it does not replace Pydantic validation.

## Conventions

- Every payload includes `schema_version: "1"` and rejects unknown top-level
  fields.
- IDs are opaque strings. A producer creates them; consumers preserve them.
- `analysis_run_id` connects every record produced during one analysis.
- `phase` identifies the code state: `baseline` is known-good, `candidate` is
  the change under review, and `fix` is a proposed repair.
- `confidence`, completeness, and risk values are decimals from `0` to `1`.
- `metadata` is for small, JSON-serializable, tool-specific details. Never put
  credentials, unrestricted prompts, or large logs in it.
- Large output belongs in a local artifact referenced by `artifact_uri` and
  protected by `artifact_sha256`.

## Contract Ownership

| Contract | Typical producer | Typical consumer |
|---|---|---|
| `AnalysisJob` | CLI or API | Repository and verification services |
| `RepositoryChange` | Diff parser | Graph builder and report |
| `RepositoryContext` | Repository intelligence | Agent and decision services |
| `WorkloadSelection` | Workload selector | Verification service |
| `Finding` | Analyzer or comparator | Persistence, agent, report |
| `Evidence` | Execution or analyzer | Persistence and decision services |
| `VerificationResult` | Verification service | Agent, decision, report |
| `AgentResult` | Agent workflow | Patch verification and report |
| `PatchArtifact` | Patch service | Verification and publisher |
| `LouDecision` | Decision engine | CLI, API, report, publisher |

## Shared Field

All contracts inherit this field.

| Field | Required | Meaning | Allowed value |
|---|---|---|---|
| `schema_version` | No; defaults to `"1"` | Version of the payload shape. | Exactly `"1"` |

## `AnalysisJob`

Describes one requested baseline-versus-candidate analysis.

| Field | Required | Meaning |
|---|---|---|
| `analysis_run_id` | Yes | Stable ID assigned to this analysis attempt. |
| `repository_id` | Yes | Stable ID of the repository being analyzed. |
| `repository_path` | Yes | Repository-relative or configured local path to analyze. |
| `base_commit_sha` | Yes | Immutable commit used as the comparison baseline. |
| `candidate_commit_sha` | Yes | Immutable commit containing the change under review. |
| `policy_revision` | No; `"1"` | Revision of policy or autonomy rules applied to the run. |
| `toolchain_revision` | No; `"1"` | Revision of analyzer/executor tooling used for reproducibility. |
| `verification_plan` | No; `{}` | Requested workloads, phases, and execution settings. |
| `resource_limits` | No; `{}` | Requested limits such as timeout or memory budget. |

## `RepositoryChange`

Normalizes the Git-level change before graph or impact analysis.

| Field | Required | Meaning |
|---|---|---|
| `repository_id` | Yes | Repository containing the change. |
| `base_commit_sha` | Yes | Commit before the change. |
| `candidate_commit_sha` | Yes | Commit after the change. |
| `added_files` | No; `[]` | Repository-relative paths newly added. |
| `modified_files` | No; `[]` | Repository-relative paths modified in place. |
| `deleted_files` | No; `[]` | Repository-relative paths deleted. |
| `renamed_files` | No; `{}` | Mapping from old path to new path. |
| `changed_symbols` | No; `[]` | Stable keys for changed functions, methods, or classes. |
| `completeness` | No; `1` | Fraction of expected extraction completed, from `0` to `1`. |
| `metadata` | No; `{}` | Extractor-specific details, such as parser name. |

## `RepositoryContext`

Explains what code and workloads might be affected by a change.

| Field | Required | Meaning |
|---|---|---|
| `repository_id` | Yes | Repository that supplied the context. |
| `commit_sha` | Yes | Commit from which context was extracted. |
| `changed_symbols` | No; `[]` | Symbols directly touched by the change. |
| `affected_symbols` | No; `[]` | Related callers, callees, or dependencies selected by traversal. |
| `affected_tests` | No; `[]` | Test identifiers or paths selected as relevant. |
| `affected_endpoints` | No; `[]` | HTTP endpoints affected by the selected symbols. |
| `affected_data_dependencies` | No; `[]` | Tables, queries, or data dependencies reached by traversal. |
| `selected_workload_ids` | No; `[]` | IDs of recommended verification workloads. |
| `selection_reasons` | No; `{}` | Mapping from selected item to the reason it was selected. |
| `unresolved_relationships` | No; `[]` | Relationships extraction could not establish. |
| `completeness` | No; `0` | Confidence that the context is sufficiently complete, from `0` to `1`. |
| `metadata` | No; `{}` | Extractor-specific structured details. |

## `WorkloadSelection`

Requests one checked-in verification workload.

| Field | Required | Meaning |
|---|---|---|
| `workload_id` | Yes | Stable workload identifier. |
| `workload_type` | Yes | One of `pytest`, `k6`, `semgrep`, or `custom`. |
| `definition_path` | Yes | Repository-relative location of the checked-in workload definition. |
| `phase` | Yes | Code state to execute: `baseline`, `candidate`, or `fix`. |
| `reason` | Yes | Human-readable reason for selecting the workload. |
| `confidence` | Yes | Selection confidence from `0` to `1`. |
| `metadata` | No; `{}` | Selector or execution-specific details. |

## `Finding`

Represents a normalized issue detected by an analyzer or comparison.

| Field | Required | Meaning |
|---|---|---|
| `finding_id` | Yes | Unique ID for this detected issue. |
| `analysis_run_id` | Yes | Analysis run that produced the finding. |
| `fingerprint` | Yes | Stable signature used to deduplicate the same issue within a run. |
| `source` | Yes | Producing tool or component, such as `lou-comparison` or `semgrep`. |
| `category` | Yes | Machine-readable problem class, such as `database-query-regression`. |
| `severity` | Yes | One of `info`, `low`, `medium`, `high`, or `critical`. |
| `confidence` | Yes | Confidence that this is a real issue, from `0` to `1`. |
| `phase` | Yes | State where observed: `baseline`, `candidate`, or `fix`. |
| `title` | Yes | Short, human-readable finding summary. |
| `message` | Yes | Detailed explanation of the observation. |
| `file_path` | No; `null` | Repository-relative file associated with the finding. |
| `symbol_key` | No; `null` | Stable function, method, or class key associated with it. |
| `metadata` | No; `{}` | Tool-specific values, such as a query-count delta. |

## `Evidence`

Stores an immutable observation that supports a finding or decision.

| Field | Required | Meaning |
|---|---|---|
| `evidence_id` | Yes | Unique ID for the observation. |
| `analysis_run_id` | Yes | Run in which it was collected. |
| `phase` | Yes | `baseline`, `candidate`, `fix`, or `comparison`. |
| `kind` | Yes | Evidence class, such as `k6-summary`, `pytest-output`, or `graph-context`. |
| `source` | Yes | Tool or component that collected it. |
| `collected_at` | Yes | Time the observation was collected, encoded as an ISO 8601 timestamp. |
| `summary` | No; `{}` | Small structured summary suitable for a report or database row. |
| `artifact_uri` | No; `null` | Local URI or path to full output. |
| `artifact_sha256` | No; `null` | SHA-256 hash of the full artifact; supply it whenever `artifact_uri` is supplied. |
| `metadata` | No; `{}` | Tool-specific structured details. |

## `VerificationResult`

Records the result of executing one verification workload or aggregate phase.

| Field | Required | Meaning |
|---|---|---|
| `verification_run_id` | Yes | Unique ID for this execution attempt. |
| `analysis_run_id` | Yes | Parent analysis run. |
| `phase` | Yes | `baseline`, `candidate`, or `fix`. |
| `commit_sha` | Yes | Immutable commit that was executed. |
| `status` | Yes | `passed`, `failed`, or `inconclusive`. |
| `workload_id` | No; `null` | Workload that produced this result; omit for an aggregate result. |
| `metrics` | No; `{}` | Numeric measurements such as p95 latency or error rate. |
| `findings` | No; `[]` | IDs of findings produced by this execution. |
| `evidence_ids` | No; `[]` | IDs of evidence records collected by this execution. |
| `artifact_uri` | No; `null` | Local path or URI for full execution output. |
| `metadata` | No; `{}` | Executor-specific details. |

## `AgentResult`

Records the bounded agent workflow outcome without embedding a raw prompt.

| Field | Required | Meaning |
|---|---|---|
| `agent_run_id` | Yes | Unique ID for this agent attempt. |
| `analysis_run_id` | Yes | Parent analysis run. |
| `status` | Yes | `succeeded`, `failed`, or `abandoned`. |
| `diagnosis` | No; `null` | Concise explanation of the suspected issue. |
| `plan` | No; `null` | Concise proposed remediation plan. |
| `confidence` | No; `0` | Agent confidence from `0` to `1`. |
| `metadata` | No; `{}` | Provider-neutral metadata such as retries or token count. |

## `PatchArtifact`

Describes one immutable candidate patch stored as a separate artifact.

| Field | Required | Meaning |
|---|---|---|
| `patch_id` | Yes | Unique ID for the proposed patch. |
| `analysis_run_id` | Yes | Parent analysis run. |
| `base_commit_sha` | Yes | Commit the patch was generated against. |
| `patch_sha256` | Yes | SHA-256 hash of the patch content. |
| `artifact_uri` | Yes | Local path or URI to the unified diff artifact. |
| `files_changed` | Yes | Number of files changed; must be zero or greater. |
| `lines_added` | Yes | Number of added lines; must be zero or greater. |
| `lines_deleted` | Yes | Number of deleted lines; must be zero or greater. |
| `metadata` | No; `{}` | Patch-generation details, budgets, or rejection reasons. |

## `LouDecision`

Represents Lou's final evidence-backed action recommendation.

| Field | Required | Meaning |
|---|---|---|
| `decision_id` | Yes | Unique ID for the final decision record. |
| `analysis_run_id` | Yes | Run on which the decision is based. |
| `debt_risk` | Yes | Estimated technical-debt risk from `0` to `1`. |
| `remediation_risk` | No; `null` | Estimated risk of applying a repair, from `0` to `1`. |
| `confidence` | Yes | Confidence in the decision from `0` to `1`. |
| `autonomy_level` | Yes | Integer from `0` to `3`, representing the allowed automation level. |
| `action` | Yes | One of `report`, `recommend`, `generate_patch`, or `open_pr`. |
| `rationale` | No; `{}` | Small structured explanation linking the decision to evidence and risk features. |
| `metadata` | No; `{}` | Policy or decision-engine-specific details. |

Autonomy levels have these agreed meanings:

| Level | Meaning |
|---|---|
| `0` / A0 | Report only; do not recommend a code change. |
| `1` / A1 | Recommend a repair for human review. |
| `2` / A2 | Generate a local candidate patch for verification. |
| `3` / A3 | Open a pull request only through a trusted publisher after passed verification. |

## Fixtures and Validation

`fixtures/` contains one valid version-one example for every contract. Treat
these as the baseline hand-off payloads when developing a producer or consumer.
When a contract changes, update its fixture and this reference in the same pull
request.

Validate data at a boundary before consuming it:

```python
from contracts import Finding

finding = Finding.model_validate_json(payload)
```

If a field's meaning changes, introduce a new schema version and fixture rather
than reusing the existing field with a different meaning.
