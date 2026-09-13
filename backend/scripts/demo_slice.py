"""Temporary, labelled end-to-end Lou demonstration on the broken-store fixture."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

from contracts import VerificationResult
from lou.agents import OrchestrationLimits, OrchestrationState, RemediationOrchestrator
from lou.analyzers.native_ast import analyze_python_sources
from lou.repository.changes import parse_repository_changes
from scripts.demo_fixture import (
    DemoProvider,
    RecordedFixVerifier,
    _deleted_test_diff,
    _inputs,
    _record,
    _seed,
)


def _run_case(
    repo: Path, base: str, candidate: str, fix: str, case: str, out: TextIO
) -> OrchestrationState:
    run_id = f"demo-slice-{case}"

    def emit(line: str) -> None:
        print(line, file=out)

    emit(f"\n=== {case.upper()} ===")
    change = parse_repository_changes(
        repository_id="repo_broken_store",
        repository_path=repo,
        base_revision=base,
        candidate_revision=candidate,
    )
    emit(f"CHANGE [LIVE] {', '.join(change.modified_files)} changed; good -> n-plus-one")
    scan = analyze_python_sources(
        repository_root=repo,
        file_paths=["store/app.py"],
        analysis_run_id=run_id,
        phase="candidate",
    )
    if scan.status != "succeeded" or len(scan.findings) != 1:
        raise RuntimeError(f"Expected one live N+1 finding: {scan.model_dump()}")
    finding = scan.findings[0]
    emit(
        f"FINDING [LIVE] {finding.fingerprint} at {finding.file_path}; artifact {scan.artifact_uri}"
    )
    inputs = _inputs(repo, base, candidate, run_id, finding)
    baseline = _record("verification_baseline.json", VerificationResult).model_copy(
        update={"analysis_run_id": run_id, "commit_sha": base}
    )
    measured = inputs.candidate_verification
    emit(
        "EVIDENCE [RECORDED FIXTURE — EV-005/006 pending] "
        f"p95 {baseline.metrics['p95_ms']:g} -> {measured.metrics['p95_ms']:g} ms; "
        f"queries {baseline.metrics['queries_per_checkout']:g} -> "
        f"{measured.metrics['queries_per_checkout']:g}; IDs mapped to local commits"
    )
    provider = DemoProvider(_deleted_test_diff(repo) if case == "delete-test" else None)
    verifier = RecordedFixVerifier(fix, wrong_hash=case == "wrong-hash")
    machine = RemediationOrchestrator(inputs, provider=provider, verifier=verifier)
    state = machine.start(OrchestrationLimits(max_attempts=1))
    while state.stage != "stopped":
        stage = state.stage
        state = machine.step(state)
        if stage == "context":
            count = len(state.current_bundle.items) if state.current_bundle else 0
            emit(f"CONTEXT [LIVE builder + RECORDED RI-003/004/005] {count} items")
        elif stage == "diagnose" and state.current_diagnosis:
            result = state.current_diagnosis.result
            emit(f"DIAGNOSIS [LIVE adapter + RECORDED mock] {result.status}: {result.diagnosis}")
        elif stage == "patch" and state.current_patch_response:
            emit("PATCH [LIVE mock proposal; illustrative and not applied]")
        elif stage == "validate" and state.current_validation:
            validation = state.current_validation
            verdict = "accepted" if validation.valid else "rejected"
            reasons = ", ".join(reason.code for reason in validation.reasons)
            emit(f"VALIDATION [LIVE] {verdict} {reasons}".rstrip())
            if state.current_validation.valid and state.current_patch_response:
                proposal = state.current_patch_response.patch_diff
                assert proposal is not None
                check = subprocess.run(
                    ["git", "-C", str(repo), "apply", "--check", "-"],
                    input=proposal,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                applies = "applies" if check.returncode == 0 else "does not apply to store/app.py"
                emit(f"APPLICABILITY [LIVE git check] {applies}")
        elif stage == "verify" and state.current_verification_results:
            latest = state.current_verification_results[-1]
            emit(
                f"VERIFICATION [RECORDED FIXTURE — EV-007 pending] "
                f"{latest.workload_id}: {latest.status}; no patch was applied or measured"
            )
        elif stage == "decide" and state.decision:
            decision = state.decision
            emit(
                f"DECISION [LIVE rules + RECORDED inputs] A{decision.autonomy_level} "
                f"{decision.action}; {state.termination_reason}"
            )
            m5 = decision.metadata.get("m5")
            if isinstance(m5, dict):
                value = m5.get("expected_value")
                value_status = value.get("status") if isinstance(value, dict) else "unknown"
                emit(
                    f"  M5 policy {m5.get('evaluated_policy_revision')}; "
                    f"organization ceiling A{m5.get('organization_ceiling')}; "
                    f"expected value {value_status}"
                )
                for reason in m5.get("denied_reasons", []):
                    emit(f"  M5 LIMIT {reason}")
            for gate in decision.rationale["gates"]:
                emit(f"  {'PASS' if gate['passed'] else 'FAIL'} {gate['code']}")
            for failure in decision.rationale["decline_reasons"]:
                emit(f"  IDENTITY MISMATCH {failure['code']}")
    if verifier.calls == 0:
        emit("VERIFICATION [SKIPPED] patch did not pass validation")
    verdicts = {
        "delete-test": "REJECTED — patch deletes a checkout test",
        "wrong-hash": "DECLINED — verified patch hash differs from proposal",
        "success": "RECORDED FIX PASSED — M5 permits a local patch; no PR opened",
    }
    emit(f"VERDICT: {verdicts[case]}")
    return state


def run_demo(out: TextIO | None = None) -> dict[str, OrchestrationState]:
    """Run three cases; returned states make the smoke test inspect actual decisions."""
    if out is None:
        out = sys.stdout
    with TemporaryDirectory(prefix="lou-demo-") as directory:
        repo = Path(directory) / "broken-store"
        base, candidate, fix = _seed(repo)
        results = {
            case: _run_case(repo, base, candidate, fix, case, out)
            for case in ("success", "delete-test", "wrong-hash")
        }
    print(
        "\nSTAGES: LIVE change, finding, context builder, provider adapter, patch validator, "
        "orchestrator, decision; RECORDED graph/workloads, baseline/candidate metrics, "
        "mock response, fix verification, scoring assumptions.",
        file=out,
    )
    print(
        "GAPS: graph symbol and verification finding ID differ from the live finding; "
        "workload paths and recorded commit IDs target another layout; mock patch does "
        "not apply; recorded fix verdict is rebound to a different local fix commit and "
        "mock patch hash; scoring inputs are scenario assumptions.",
        file=out,
    )
    return results


if __name__ == "__main__":
    run_demo()
