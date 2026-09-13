import pytest

from contracts import RepositoryChange
from lou.application import AnalysisRequest
from lou.verification.int002 import StubCheckoutWorkloadSelector, StubGraphContextIntelligence


def test_int002_substitutions_are_explicit_and_contract_compatible(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = AnalysisRequest("fixture", tmp_path, "a" * 40, "b" * 40)
    monkeypatch.setattr(
        "lou.verification.int002.parse_repository_changes",
        lambda **_: RepositoryChange(
            repository_id="fixture", base_commit_sha="a" * 40, candidate_commit_sha="b" * 40
        ),
    )

    context = StubGraphContextIntelligence().inspect(request, "run-1")[1]
    workloads = StubCheckoutWorkloadSelector().select(context)

    assert context.metadata["stub"] is True
    assert context.completeness < 1
    assert all("[STUB:" in reason for reason in context.selection_reasons.values())
    assert [item.workload_id for item in workloads] == ["checkout-pytest", "checkout-k6"]
    assert all(
        item.metadata["stub"] is True and "[STUB: RI-005]" in item.reason for item in workloads
    )
