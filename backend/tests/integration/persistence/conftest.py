"""PostgreSQL fixtures for PF-003 persistence integration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session, close_all_sessions

from lou.persistence.database import create_session_factory
from lou.persistence.interfaces import AnalysisRunInput, AnalysisRunView
from lou.persistence.models import (
    AnalysisRunRecord,
    EvidenceRecord,
    FindingRecord,
    LouDecisionRecord,
    RepositoryRecord,
    VerificationRunRecord,
    WorkloadRecord,
)
from lou.persistence.repositories import SqlAlchemyAnalysisRunRepository

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def session_factory() -> Iterator[Any]:
    url = os.environ.get("LOU_TEST_DATABASE_URL")
    if not url:
        pytest.skip("LOU_TEST_DATABASE_URL is required; start Docker and migrate lou_test first")
    factory = create_session_factory(type("TestSettings", (), {"database_url": url})())
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except Exception as error:
        close_all_sessions()
        pytest.skip(f"cannot connect to LOU_TEST_DATABASE_URL: {error}")
    yield factory
    close_all_sessions()


@pytest.fixture()
def seeded_db(session_factory: Any) -> Iterator[tuple[Any, AnalysisRunView, UUID]]:
    with session_factory.begin() as session:
        session.execute(delete(EvidenceRecord))
        session.execute(delete(LouDecisionRecord))
        session.execute(delete(VerificationRunRecord))
        session.execute(delete(FindingRecord))
        session.execute(delete(WorkloadRecord))
        session.execute(delete(AnalysisRunRecord))
        session.execute(delete(RepositoryRecord))
        repository = RepositoryRecord(
            provider="local", repository_name="persistence-test", local_path="/tmp/persistence-test"
        )
        session.add(repository)
        session.flush()
        workload = WorkloadRecord(
            repository_id=repository.id,
            name="checkout-load",
            workload_type="k6",
            definition_path="loadtests/checkout.js",
            definition_sha256="a" * 64,
        )
        session.add(workload)
        session.flush()

    run_repository = SqlAlchemyAnalysisRunRepository(session_factory)
    run, _ = run_repository.create_or_get(
        AnalysisRunInput(
            repository_id=repository.id,
            base_commit_sha="base-persistence",
            candidate_commit_sha="candidate-persistence",
            trigger_type="fixture",
            deduplication_key=f"test-{uuid4()}",
        )
    )
    yield session_factory, run, workload.id


@pytest.fixture()
def db_session(session_factory: Any) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def require_test_database() -> None:
    """Keep the default unit suite local while making explicit DB runs fail clearly."""

    if not os.environ.get("LOU_TEST_DATABASE_URL"):
        pytest.skip("set LOU_TEST_DATABASE_URL to run PostgreSQL integration tests")
