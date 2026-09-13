from types import SimpleNamespace
from typing import cast

from contracts import VerificationResult
from lou.agents.orchestration import ValidatedPatch
from lou.application.analysis import VerificationBundle
from lou.application.remediation import PersistedFixVerifier


class Store:
    def __init__(self) -> None:
        self.bundles: list[VerificationBundle] = []

    def record_verification(self, run_id: str, bundle: VerificationBundle) -> None:
        assert run_id == "run-1"
        self.bundles.append(bundle)


class Verifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(self, patch: object) -> VerificationResult:
        self.calls += 1
        return VerificationResult(
            verification_run_id="run-1:attempt:1:checkout-load",
            analysis_run_id="run-1",
            phase="fix",
            commit_sha="fixed",
            status="passed",
            workload_id="checkout-load",
            metrics={"query_count": 2},
        )


def test_persisted_fix_verifier_records_one_immutable_fix_evidence_bundle() -> None:
    store = Store()
    verifier = Verifier()
    service = PersistedFixVerifier(verifier=verifier, store=store)  # type: ignore[arg-type]
    patch = cast(
        ValidatedPatch,
        SimpleNamespace(
            analysis_job=SimpleNamespace(analysis_run_id="run-1"),
            patch_artifact=SimpleNamespace(patch_sha256="a" * 64),
        ),
    )

    first = service.verify(patch)
    second = service.verify(patch)

    assert first.status == second.status == "passed"
    assert verifier.calls == 2
    assert len(store.bundles) == 1
    bundle = store.bundles[0]
    assert bundle.evidence[0].kind == "fix-verification-verdict"
    assert bundle.evidence[0].summary["patch_sha256"] == "a" * 64
