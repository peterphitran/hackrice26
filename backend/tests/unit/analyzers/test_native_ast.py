"""Native analyzer returns stable contracts without running untrusted source."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from contracts import Evidence, Finding
from lou.analyzers import analyze_python_sources, native_ast

DEMO_FINDING = (
    Path(__file__).parents[3] / "contracts" / "fixtures" / "demo_checkout" / "finding.json"
)
N_PLUS_ONE = """\
def checkout(cursor, cart):
    total = 0
    for product_id in cart:
        cursor.execute("SELECT price FROM products WHERE id = %s", (product_id,))
        total += cursor.fetchone()[0]
    return total
"""
BATCHED = """\
def checkout(cursor, cart):
    product_ids = list(cart)
    cursor.execute("SELECT price FROM products WHERE id = ANY(%s)", (product_ids,))
    prices = cursor.fetchall()
    for price in prices:
        total = price
    return total
"""


def _source(repository: Path, text: str, relative: str = "checkout/service.py") -> Path:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_looped_database_call_matches_demo_contract_shape(tmp_path: Path) -> None:
    _source(tmp_path, N_PLUS_ONE)
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-1",
        phase="candidate",
    )

    assert result.status == "succeeded"
    assert result.tool_exit_status == 0
    assert result.completeness == 1.0
    assert len(result.findings) == len(result.evidence) == 1
    finding = Finding.model_validate(result.findings[0].model_dump())
    demo = Finding.model_validate_json(DEMO_FINDING.read_text())
    assert set(finding.model_dump()) == set(demo.model_dump())
    assert finding.fingerprint == demo.fingerprint
    assert finding.symbol_key == demo.symbol_key
    assert finding.category == demo.category == "database-query-regression"
    assert finding.file_path == demo.file_path == "checkout/service.py"
    assert finding.phase == "candidate"
    assert finding.metadata["line_numbers"] == [4]

    evidence = Evidence.model_validate(result.evidence[0].model_dump())
    assert evidence.artifact_uri == ".lou/artifacts/run-1/static-analyzer.json"
    assert evidence.artifact_uri is not None
    raw_bytes = (tmp_path / evidence.artifact_uri).read_bytes()
    assert evidence.artifact_sha256 == sha256(raw_bytes).hexdigest()
    assert result.artifact_sha256 == evidence.artifact_sha256
    raw = json.loads(raw_bytes)
    assert raw["files"][0]["matches"][0]["symbol_key"] == finding.symbol_key


def test_batched_query_is_clean_but_still_a_successful_scan(tmp_path: Path) -> None:
    _source(tmp_path, BATCHED)
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-2",
        phase="candidate",
    )

    assert result.status == "succeeded"
    assert result.tool_exit_status == 0
    assert result.completeness == 1.0
    assert result.findings == ()
    assert len(result.evidence) == 1
    assert result.evidence[0].summary["finding_fingerprints"] == []


def test_syntax_error_lowers_completeness_and_is_not_clean(tmp_path: Path) -> None:
    _source(tmp_path, N_PLUS_ONE)
    _source(tmp_path, "def broken(:\n", "checkout/broken.py")
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py", "checkout/broken.py"],
        analysis_run_id="run-3",
        phase="candidate",
    )

    assert result.status == "failed"
    assert result.tool_exit_status == 1
    assert result.completeness == 0.5
    assert [error.code for error in result.errors] == ["syntax_error"]
    assert len(result.findings) == 1
    assert len(result.evidence) == 2
    assert result.artifact_uri is not None
    raw = json.loads((tmp_path / result.artifact_uri).read_text())
    assert raw["status"] == "failed"
    assert raw["errors"][0]["code"] == "syntax_error"


def test_fingerprint_and_raw_artifact_are_repeatable(tmp_path: Path) -> None:
    _source(tmp_path, N_PLUS_ONE)
    first = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-4",
        phase="candidate",
    )
    first_bytes = (tmp_path / first.artifact_uri).read_bytes() if first.artifact_uri else b""
    second = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-4",
        phase="candidate",
    )

    assert first.findings[0].fingerprint == second.findings[0].fingerprint
    assert first.findings[0].finding_id == second.findings[0].finding_id
    assert first.artifact_sha256 == second.artifact_sha256
    assert second.artifact_uri is not None
    assert (tmp_path / second.artifact_uri).read_bytes() == first_bytes


def test_while_loop_and_receiver_filter(tmp_path: Path) -> None:
    _source(
        tmp_path,
        """\
def checkout(connection, worker):
    while worker.pending():
        connection.execute("SELECT 1")
        worker.execute("not a database call")
""",
    )
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-5",
        phase="candidate",
    )

    assert len(result.findings) == 1
    assert result.findings[0].metadata["line_numbers"] == [3]


def test_disabled_adapter_does_not_scan_or_write(tmp_path: Path) -> None:
    _source(tmp_path, N_PLUS_ONE)
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-6",
        phase="candidate",
        enabled=False,
    )

    assert result.status == "disabled"
    assert result.tool_exit_status is None
    assert result.findings == ()
    assert result.evidence == ()
    assert not (tmp_path / ".lou").exists()


def test_path_outside_repository_is_a_failed_scan(tmp_path: Path) -> None:
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["../outside.py"],
        analysis_run_id="run-7",
        phase="candidate",
    )

    assert result.status == "failed"
    assert result.tool_exit_status == 1
    assert result.completeness == 0.0
    assert result.errors[0].code == "analysis_error"


def test_analyzer_crash_is_reported_with_incomplete_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source(tmp_path, N_PLUS_ONE)

    def crash(_: object, __: object) -> None:
        raise RuntimeError("unexpected AST visitor failure")

    monkeypatch.setattr(native_ast._LoopQueryVisitor, "visit", crash)
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-crash",
        phase="candidate",
    )

    assert result.status == "failed"
    assert result.tool_exit_status == 1
    assert result.completeness == 0.0
    assert result.errors[0].code == "analyzer_crash"
    assert result.artifact_uri is not None
    assert (tmp_path / result.artifact_uri).is_file()


def test_symlinked_artifact_path_is_rejected(tmp_path: Path) -> None:
    _source(tmp_path, N_PLUS_ONE)
    outside = tmp_path.parent / f"{tmp_path.name}-artifact-outside"
    outside.mkdir()
    (tmp_path / ".lou").symlink_to(outside, target_is_directory=True)
    result = analyze_python_sources(
        repository_root=tmp_path,
        file_paths=["checkout/service.py"],
        analysis_run_id="run-symlink",
        phase="candidate",
    )

    assert result.status == "failed"
    assert result.errors[-1].code == "artifact_write_error"
    assert result.findings == ()
    assert not (outside / "artifacts").exists()
