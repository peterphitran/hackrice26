"""Smoke-test the temporary end-to-end demonstration."""

import pytest

from scripts.demo_slice import run_demo


def test_demo_slice_reaches_all_three_verdicts(capsys: pytest.CaptureFixture[str]) -> None:
    states = run_demo()
    assert states["success"].decision is not None
    assert states["success"].decision.autonomy_level == 3
    assert states["success"].termination_reason == "verified"
    assert states["delete-test"].current_validation is not None
    assert not states["delete-test"].current_validation.valid
    assert states["delete-test"].verification_results == []
    assert states["wrong-hash"].decision is not None
    assert states["wrong-hash"].decision.autonomy_level == 0
    assert any(
        item["code"] == "workload_patch_hash"
        for item in states["wrong-hash"].decision.rationale["decline_reasons"]
    )
    output = capsys.readouterr().out
    assert "FINDING [LIVE]" in output
    assert "APPLICABILITY [LIVE git check] does not apply" in output
    assert "VERIFICATION [RECORDED FIXTURE" in output
    assert "VERDICT: REJECTED" in output
    assert "IDENTITY MISMATCH workload_patch_hash" in output
