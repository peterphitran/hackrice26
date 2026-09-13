# M5 risk and policy integration

`lou.decision.decide_m5` is the version-two decision entry point. It runs the
existing exact-patch and independent-verification gates first, then calculates
the M5 debt and remediation assessments, expected value, and policy ceiling.
The returned `LouDecision` remains compatible with version-one persistence and
CLI consumers; `metadata.m5` contains the version-two `AutonomyDecision` record.

The version-two records live in `contracts/risk.py`: `DebtAssessment`,
`RemediationRisk`, and `AutonomyDecision`. Each feature records its value,
contribution, source, evidence ID, and missing reason. Scores and bounds are
normalized to `[0, 1]`. Bounds marked `scenario` are missing-input bounds, **not
statistically calibrated confidence intervals**. The debt assessment retains
the v1 principal formula and adds incident burden and ownership gap to a
versioned v2 interest formula. Missing values are unknown, never measured zero.
Feature contributions sum to the displayed debt or remediation risk score.

```text
principal = .35 complexity + .35 coverage_deficit + .30 estimated_patch_size
interest_factor = .20 churn + .18 graph_centrality + .20 runtime_impact
                + .18 path_criticality + .14 incident_burden + .10 ownership_gap
interest = principal × interest_factor
debt_risk = (principal + interest) / 2
```

Remediation risk retains the `remediation-v1` weighted formula and adds M5
hard flags and a confidence penalty when sensitive-file status is unknown.
A hard flag imposes a visible 0.5 risk floor, recorded as a feature
contribution when it raises the score.

Expected value uses independently supplied engineering-hour estimates over an
explicit day horizon. `CostEstimates.sources` must identify each of the five
estimates. When a value or source is missing, the output is
`insufficient_evidence`. The low and high values vary benefit and cost using
the declared `uncertainty_fraction`; they are scenarios, not learned forecasts.
The risk points are never converted into hours.

The local evaluator and `OpaPolicy` implement the same `PolicyEvaluator`
interface. OPA uses its Data API, posting `{"input": ...}` and reading the
`result` document. The included `lou.rego` provides a default-deny example at
`/v1/data/lou/decision`. The selected OPA URL and revision are deployment
configuration; OPA is not required for the local deterministic path. Malformed
responses, transport errors, and revision mismatches cap the action at A0.
Local migration, sensitive-path, coverage, verification, and value gates run
even when OPA is selected, so an OPA response cannot lift those limits.

The policy model recognizes A0 through A5. The product ceiling is A3. A4
(auto-merge) and A5 (auto-deploy) cannot be selected. A3 additionally needs
matching passed fix verification and a positive lower expected-value scenario.
Unknown expected value permits at most A2, which allows a verified local patch
without pretending that a PR is economically justified. The agent orchestrator
uses M5 for accepted patches and terminal decisions; its pre-patch retry check
still uses the v1 decision because no patch exists to assess at that boundary.

The first fixture adapter accepts optional `risk_signals` and `cost_estimates`
objects in request configuration and persists the resulting M5 record. Its
report prints expected value, policy revisions, ceilings, and limiting reasons.
The trusted PR publisher revalidates the persisted M5 record, policy revision,
patch bytes, validation, and independent fix verdicts before any publication.
Legacy v1 decisions cannot publish through this boundary. Incident, ownership,
and cost producers are not yet present in the fixture pipeline; those inputs
remain explicitly unknown until supplied by a trusted producer. The current
fixture therefore stops below A3 by default.
