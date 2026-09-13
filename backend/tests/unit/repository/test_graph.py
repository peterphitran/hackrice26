from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import RepositoryChange
from lou.core.errors import GitExecutionError, InvalidCommitError
from lou.repository import (
    GraphLimits,
    RepositoryGraphError,
    RepositoryGraphSnapshot,
    build_repository_graph,
    extract_changed_symbols,
    parse_repository_changes,
)
from lou.repository import graph as graph_module


def _write(repository: Path, relative: str, content: str) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(repository: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", message], check=True)
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _change(repository: Path, base: str, candidate: str) -> RepositoryChange:
    parsed = parse_repository_changes(
        repository_id="repo-store",
        repository_path=repository,
        base_revision=base,
        candidate_revision=candidate,
    )
    return extract_changed_symbols(repository_path=repository, change=parsed)


@pytest.fixture
def store_history(git_repository: Path) -> tuple[Path, RepositoryChange]:
    baseline = """
from fastapi import FastAPI

class Store:
    def checkout(self, cursor):
        cursor.execute("SELECT id FROM broken_store.cart_items")
        cursor.execute("SELECT id FROM broken_store.products")
        return 2

store = Store()
app = FastAPI()

@app.post("/checkout")
def checkout():
    return store.checkout(None)
""".lstrip()
    candidate = baseline.replace(
        'cursor.execute("SELECT id FROM broken_store.products")',
        'cursor.execute("SELECT id FROM broken_store.products WHERE id = 1")',
    )
    _write(git_repository, "store/app.py", baseline)
    _write(
        git_repository,
        "tests/test_checkout.py",
        """
import store.app as store_app

class TestCheckout:
    def test_checkout_receipt(self):
        return store_app.Store().checkout(None)
""".lstrip(),
    )
    _write(
        git_repository,
        "loadtests/checkout.js",
        'import http from "k6/http";\nhttp.post(`${__ENV.BASE_URL}/checkout`);\n',
    )
    base = _commit(git_repository, "baseline")
    _write(git_repository, "store/app.py", candidate)
    candidate_sha = _commit(git_repository, "candidate")
    return git_repository, _change(git_repository, base, candidate_sha)


def _edge_set(snapshot: RepositoryGraphSnapshot) -> set[tuple[str, str, str]]:
    return {
        (source, edge_type, target) for source, target, edge_type in snapshot.graph.edges(keys=True)
    }


def test_builds_all_demo_node_types_and_connects_ri001_ri002_ri003(
    store_history: tuple[Path, RepositoryChange], tmp_path: Path
) -> None:
    repository, change = store_history
    snapshot = build_repository_graph(
        repository_path=repository,
        change=change,
        analysis_run_id="run-graph-1",
        artifact_root=tmp_path / "artifacts",
    )

    assert change.changed_symbols == ["store.app.Store.checkout"]
    node_types = {attributes["node_type"] for _, attributes in snapshot.graph.nodes(data=True)}
    assert node_types == {
        "FILE",
        "FUNCTION",
        "CLASS",
        "TEST",
        "ENDPOINT",
        "DATABASE_TABLE",
        "LOAD_SCENARIO",
    }
    assert snapshot.graph.nodes["function:store.app.Store.checkout"]["changed"] is True
    edges = _edge_set(snapshot)
    assert (
        "function:store.app.checkout",
        "CALLS",
        "function:store.app.Store.checkout",
    ) in edges
    test_id = "test:tests/test_checkout.py::TestCheckout.test_checkout_receipt"
    assert ("function:store.app.Store.checkout", "TESTED_BY", test_id) in edges
    assert ("function:store.app.checkout", "SERVES_ENDPOINT", "endpoint:POST /checkout") in edges
    assert (
        "function:store.app.Store.checkout",
        "READS_FROM",
        "table:broken_store.products",
    ) in edges
    assert (
        "endpoint:POST /checkout",
        "VALIDATED_BY",
        "load_scenario:loadtests/checkout.js",
    ) in edges
    assert (
        "file:tests/test_checkout.py",
        "IMPORTS",
        "file:store/app.py",
    ) in edges
    assert snapshot.completeness == 1.0
    assert snapshot.diagnostics == ()
    assert snapshot.artifact_uri == "run-graph-1/candidate/repository-graph.json"
    assert hashlib.sha256(snapshot.artifact_path.read_bytes()).hexdigest() == (
        snapshot.artifact_sha256
    )


def test_snapshot_is_canonical_reusable_and_ignores_dirty_worktree(
    store_history: tuple[Path, RepositoryChange], tmp_path: Path
) -> None:
    repository, change = store_history
    artifact_root = tmp_path / "artifacts"
    first = build_repository_graph(
        repository_path=repository,
        change=change,
        analysis_run_id="run-stable",
        artifact_root=artifact_root,
    )
    _write(repository, "store/app.py", '@app.delete("/uncommitted")\ndef uncommitted(): pass\n')
    second = build_repository_graph(
        repository_path=repository,
        change=change,
        analysis_run_id="run-stable",
        artifact_root=artifact_root,
    )

    assert first.artifact_sha256 == second.artifact_sha256
    payload = json.loads(first.artifact_path.read_text(encoding="utf-8"))
    assert payload["commit_sha"] == change.candidate_commit_sha
    assert all("uncommitted" not in node["id"] for node in payload["nodes"])
    assert payload["nodes"] == sorted(payload["nodes"], key=lambda node: node["id"])


def test_refuses_to_overwrite_a_different_completed_artifact(
    store_history: tuple[Path, RepositoryChange], tmp_path: Path
) -> None:
    repository, change = store_history
    target = tmp_path / "artifacts" / "run-conflict" / "candidate" / "repository-graph.json"
    target.parent.mkdir(parents=True)
    target.write_text("different", encoding="utf-8")

    with pytest.raises(RepositoryGraphError, match="different completed"):
        build_repository_graph(
            repository_path=repository,
            change=change,
            analysis_run_id="run-conflict",
            artifact_root=tmp_path / "artifacts",
        )


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", ".", "", "a/b"])
def test_rejects_unsafe_artifact_run_ids(
    store_history: tuple[Path, RepositoryChange], tmp_path: Path, run_id: str
) -> None:
    repository, change = store_history
    with pytest.raises(RepositoryGraphError, match="unsafe"):
        build_repository_graph(
            repository_path=repository,
            change=change,
            analysis_run_id=run_id,
            artifact_root=tmp_path / "artifacts",
        )


@pytest.mark.skipif(os.name == "nt", reason="symlink behavior differs on Windows")
def test_rejects_symlinked_artifact_boundaries(
    store_history: tuple[Path, RepositoryChange], tmp_path: Path
) -> None:
    repository, change = store_history
    real = tmp_path / "real"
    real.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(RepositoryGraphError, match="root cannot be a symlink"):
        build_repository_graph(
            repository_path=repository,
            change=change,
            analysis_run_id="run-link",
            artifact_root=root_link,
        )

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "run-link").symlink_to(real, target_is_directory=True)
    with pytest.raises(RepositoryGraphError, match="passes through a symlink"):
        build_repository_graph(
            repository_path=repository,
            change=change,
            analysis_run_id="run-link",
            artifact_root=artifact_root,
        )


def test_reports_dynamic_and_unresolved_relationships(git_repository: Path, tmp_path: Path) -> None:
    _write(git_repository, "local.py", "def known():\n    return 1\n")
    _write(
        git_repository,
        "app.py",
        """
from fastapi import FastAPI
import local

app = FastAPI()
route = "/dynamic"

@app.api_route(route, methods=["GET"])
def handler(cursor, sql):
    local.missing()
    cursor.execute(sql)
""".lstrip(),
    )
    _write(
        git_repository,
        "loadtests/dynamic.js",
        'import http from "k6/http";\nhttp.get(__ENV.URL);\nhttp.get("/missing");\n',
    )
    base = _commit(git_repository, "base")
    _write(git_repository, "local.py", "def known():\n    return 2\n")
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-incomplete",
        artifact_root=tmp_path,
    )

    codes = {diagnostic.code for diagnostic in snapshot.diagnostics}
    assert codes == {
        "dynamic_endpoint",
        "dynamic_load_endpoint",
        "dynamic_sql",
        "unresolved_call",
        "unresolved_load_endpoint",
    }
    assert snapshot.completeness < 1
    assert "endpoint:GET /missing" not in snapshot.graph


@pytest.mark.parametrize(
    ("limits", "expected_code"),
    [
        (GraphLimits(max_files=1), "file_limit"),
        (GraphLimits(max_blob_bytes=5), "blob_size_limit"),
        (GraphLimits(max_total_bytes=5), "total_size_limit"),
        (GraphLimits(max_ast_nodes=1), "ast_node_limit"),
    ],
)
def test_limits_lower_completeness_without_crashing(
    store_history: tuple[Path, RepositoryChange],
    tmp_path: Path,
    limits: GraphLimits,
    expected_code: str,
) -> None:
    repository, change = store_history
    snapshot = build_repository_graph(
        repository_path=repository,
        change=change,
        analysis_run_id=f"run-{expected_code}",
        artifact_root=tmp_path,
        limits=limits,
    )
    assert snapshot.completeness < 1
    assert expected_code in {diagnostic.code for diagnostic in snapshot.diagnostics}


def test_parse_failure_and_removed_changed_symbol_are_explicit(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(git_repository, "broken.py", "def removed():\n    return 1\n")
    base = _commit(git_repository, "base")
    _write(git_repository, "broken.py", "def broken(:\n")
    candidate = _commit(git_repository, "candidate")
    change = _change(git_repository, base, candidate)
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=change,
        analysis_run_id="run-broken",
        artifact_root=tmp_path,
    )

    codes = {diagnostic.code for diagnostic in snapshot.diagnostics}
    assert "source_parse_error" in codes
    assert "changed_symbol_missing_from_candidate" in codes
    assert snapshot.completeness == 0


def test_invalid_commit_identity_is_rejected(
    store_history: tuple[Path, RepositoryChange], tmp_path: Path
) -> None:
    repository, change = store_history
    change.candidate_commit_sha = "HEAD"
    with pytest.raises(InvalidCommitError):
        build_repository_graph(
            repository_path=repository,
            change=change,
            analysis_run_id="run-invalid",
            artifact_root=tmp_path,
        )


def test_graph_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        GraphLimits(max_files=0)


def test_supports_api_route_async_methods_sql_writes_and_duplicate_evidence(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(git_repository, "pkg/__init__.py", "from pkg.service import Service\n")
    _write(
        git_repository,
        "pkg/service.py",
        """
from fastapi import APIRouter
from pkg import *
from .ignored import ignored

class Service:
    def helper(self):
        return 1

    def helper(self):
        return 2

    async def update(self, cursor):
        self.helper()
        self.helper()
        cursor.executemany("INSERT INTO public.items VALUES (1)")
        cursor.execute("UPDATE public.items SET value = 2")
        cursor.execute("DELETE FROM public.old_items")
        cursor.execute("SELECT * FROM public.items JOIN public.audit ON true")

router: APIRouter = APIRouter()

@router.api_route("/items", methods=["POST", "GET", "INVALID", 1])
async def items(cursor):
    def inner():
        return Service()
    inner()
    await Service().update(cursor)
""".lstrip(),
    )
    _write(
        git_repository,
        "consumer.py",
        "import pkg.service\n\ndef construct():\n    return pkg.service.Service()\n",
    )
    base = _commit(git_repository, "base")
    _write(
        git_repository,
        "pkg/service.py",
        (git_repository / "pkg/service.py").read_text().replace("return 1", "return 2"),
    )
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-variants",
        artifact_root=tmp_path,
    )

    edges = _edge_set(snapshot)
    assert "endpoint:GET /items" in snapshot.graph
    assert "endpoint:POST /items" in snapshot.graph
    assert "table:public.items" in snapshot.graph
    assert "table:public.old_items" in snapshot.graph
    assert "table:public.audit" in snapshot.graph
    assert "function:pkg.service.Service.helper#2" in snapshot.graph
    assert "function:pkg.service.items.inner" in snapshot.graph
    assert ("file:consumer.py", "IMPORTS", "file:pkg/service.py") in edges
    assert (
        "function:pkg.service.items",
        "CALLS",
        "function:pkg.service.items.inner",
    ) in edges
    assert (
        "function:pkg.service.Service.update",
        "CALLS",
        "function:pkg.service.Service.helper#2",
    ) in edges
    evidence = snapshot.graph["function:pkg.service.Service.update"][
        "function:pkg.service.Service.helper#2"
    ]["CALLS"]["evidence"]
    assert len(evidence) == 2


def test_relative_load_url_and_unsupported_source_encoding_are_diagnostics(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(git_repository, "valid.py", "def value():\n    return 1\n")
    _write(git_repository, "loadtest/relative.ts", 'http.get("relative");\n')
    base = _commit(git_repository, "base")
    (git_repository / "invalid.py").write_bytes(b"# coding: unknown-codec\ndef x(): pass\n")
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-encoding",
        artifact_root=tmp_path,
    )

    codes = {item.code for item in snapshot.diagnostics}
    assert "source_encoding_error" in codes
    assert "dynamic_load_endpoint" in codes


def test_empty_repository_graph_is_complete(git_repository: Path, tmp_path: Path) -> None:
    _write(git_repository, "README.md", "base\n")
    base = _commit(git_repository, "base")
    _write(git_repository, "README.md", "candidate\n")
    candidate = _commit(git_repository, "candidate")
    change = RepositoryChange(
        repository_id="empty",
        base_commit_sha=base,
        candidate_commit_sha=candidate,
    )
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=change,
        analysis_run_id="run-empty",
        artifact_root=tmp_path,
    )
    assert snapshot.completeness == 1
    assert len(snapshot.graph) == 0


def test_git_tree_and_blob_failures_are_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = SimpleNamespace(returncode=2, stdout=b"", stderr=b"failed")
    monkeypatch.setattr(graph_module, "_execute_git", lambda *_args, **_kwargs: failed)
    with pytest.raises(GitExecutionError):
        graph_module._tree_entries(tmp_path, "a" * 40)

    malformed = SimpleNamespace(returncode=0, stdout=b"bad\0", stderr=b"")
    monkeypatch.setattr(graph_module, "_execute_git", lambda *_args, **_kwargs: malformed)
    with pytest.raises(GitExecutionError) as malformed_error:
        graph_module._tree_entries(tmp_path, "a" * 40)
    assert malformed_error.value.stderr == "Invalid ls-tree entry"

    bad_object = SimpleNamespace(
        returncode=0,
        stdout=b"100644 blob invalid\tfile.py\0",
        stderr=b"",
    )
    monkeypatch.setattr(graph_module, "_execute_git", lambda *_args, **_kwargs: bad_object)
    with pytest.raises(GitExecutionError) as object_error:
        graph_module._tree_entries(tmp_path, "a" * 40)
    assert object_error.value.stderr == "Invalid Git object ID"


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        ([SimpleNamespace(returncode=1, stdout=b"", stderr=b"size")], "size graph blob"),
        ([SimpleNamespace(returncode=0, stdout=b"bad", stderr=b"")], "Invalid blob size"),
        (
            [
                SimpleNamespace(returncode=0, stdout=b"1", stderr=b""),
                SimpleNamespace(returncode=1, stdout=b"", stderr=b"read"),
            ],
            "read graph blob",
        ),
        (
            [
                SimpleNamespace(returncode=0, stdout=b"2", stderr=b""),
                SimpleNamespace(returncode=0, stdout=b"x", stderr=b""),
            ],
            "Unexpected blob size",
        ),
    ],
)
def test_blob_git_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    responses: list[SimpleNamespace],
    message: str,
) -> None:
    iterator = iter(responses)
    monkeypatch.setattr(graph_module, "_execute_git", lambda *_args, **_kwargs: next(iterator))
    entry = graph_module._TreeEntry("100644", "blob", "a" * 40, "file.py")
    with pytest.raises(GitExecutionError) as captured:
        graph_module._read_blob(
            tmp_path,
            entry,
            GraphLimits(),
            graph_module._Progress(),
        )
    assert message in {captured.value.operation, captured.value.stderr}


def test_atomic_writer_handles_identical_and_conflicting_races(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "graph.json"

    def identical(source: Path, destination: Path) -> None:
        del source
        destination.write_bytes(b"same")
        raise FileExistsError

    monkeypatch.setattr("lou.repository.graph.os.link", identical)
    graph_module._write_once(target, b"same")
    target.unlink()

    def different(source: Path, destination: Path) -> None:
        del source
        destination.write_bytes(b"different")
        raise FileExistsError

    monkeypatch.setattr("lou.repository.graph.os.link", different)
    with pytest.raises(RepositoryGraphError, match="different completed"):
        graph_module._write_once(target, b"expected")


def test_symbol_keys_match_ri002_without_path_collisions(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(git_repository, "a.b.py", "def same():\n    return 1\n")
    _write(git_repository, "a/b.py", "def same():\n    return 1\n")
    _write(git_repository, "pkg/__init__.py", "def initialize():\n    return 1\n")
    _write(git_repository, "consumer.py", "import a.b\n")
    base = _commit(git_repository, "base")
    for path in ("a.b.py", "a/b.py", "pkg/__init__.py"):
        _write(git_repository, path, (git_repository / path).read_text().replace("1", "2"))
    candidate = _commit(git_repository, "candidate")
    change = _change(git_repository, base, candidate)

    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=change,
        analysis_run_id="run-collision-safe",
        artifact_root=tmp_path,
    )

    expected = {
        "function:a%2Eb.same",
        "function:a.b.same",
        "function:pkg.__init__.initialize",
    }
    assert expected.issubset(snapshot.graph)
    assert all(snapshot.graph.nodes[node]["changed"] for node in expected)
    assert "ambiguous_import" in {item.code for item in snapshot.diagnostics}


def test_guarded_definitions_are_indexed_and_only_test_named_methods_are_tests(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(
        git_repository,
        "guarded.py",
        "if True:\n    def guarded():\n        return 1\n"
        "try:\n    class Optional:\n        def run(self):\n            return 1\n"
        "except ImportError:\n    pass\n",
    )
    _write(
        git_repository,
        "tests/test_lifecycle.py",
        "class TestLifecycle:\n"
        "    def setup_method(self):\n        pass\n"
        "    def helper(self):\n        pass\n"
        "    def test_real(self):\n        def nested_helper():\n            pass\n"
        "        nested_helper()\n",
    )
    base = _commit(git_repository, "base")
    _write(
        git_repository,
        "guarded.py",
        (git_repository / "guarded.py").read_text().replace("return 1", "return 2", 1),
    )
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-guarded",
        artifact_root=tmp_path,
    )

    assert "function:guarded.guarded" in snapshot.graph
    assert "class:guarded.Optional" in snapshot.graph
    assert "function:guarded.Optional.run" in snapshot.graph
    assert (
        snapshot.graph.nodes["test:tests/test_lifecycle.py::TestLifecycle.test_real"]["node_type"]
        == "TEST"
    )
    for name in ("setup_method", "helper", "test_real.nested_helper"):
        assert (
            snapshot.graph.nodes[f"function:tests.test_lifecycle.TestLifecycle.{name}"]["node_type"]
            == "FUNCTION"
        )


def test_router_prefixes_default_method_and_shared_endpoint_evidence(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(
        git_repository,
        "routes.py",
        "from fastapi import APIRouter, FastAPI\n"
        "router = APIRouter(prefix='/v1')\n"
        "app = FastAPI()\n"
        "app.include_router(router, prefix='/api')\n"
        "@router.get('/users')\n"
        "def first(): pass\n"
        "@router.get('/users')\n"
        "def second(): pass\n"
        "@app.api_route('/health')\n"
        "def health(): pass\n"
        "def FastAPI(): return object()\n"
        "fake = FastAPI()\n"
        "@fake.get('/fake')\n"
        "def fake_handler(): pass\n",
    )
    base = _commit(git_repository, "base")
    _write(git_repository, "README.md", "candidate\n")
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-prefixes",
        artifact_root=tmp_path,
    )

    endpoint = snapshot.graph.nodes["endpoint:GET /api/v1/users"]
    assert len(endpoint["evidence"]) == 2
    assert len(endpoint["locations"]) == 2
    assert "endpoint:GET /health" in snapshot.graph
    assert "endpoint:GET /fake" not in snapshot.graph


def test_relative_imports_and_shadowed_parameters_do_not_create_false_calls(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(git_repository, "pkg/service.py", "class Service:\n    def run(self): pass\n")
    _write(
        git_repository,
        "pkg/consumer.py",
        "from .service import Service\n"
        "def invoke():\n    Service().run()\n"
        "def shadowed(Service):\n    Service().run()\n",
    )
    base = _commit(git_repository, "base")
    _write(git_repository, "README.md", "candidate\n")
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-relative",
        artifact_root=tmp_path,
    )

    edges = _edge_set(snapshot)
    assert ("file:pkg/consumer.py", "IMPORTS", "file:pkg/service.py") in edges
    expected = (
        "function:pkg.consumer.invoke",
        "CALLS",
        "function:pkg.service.Service.run",
    )
    assert expected in edges
    assert (
        "function:pkg.consumer.shadowed",
        "CALLS",
        "function:pkg.service.Service.run",
    ) not in edges


def test_load_urls_only_allow_a_static_path_after_base_url(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(
        git_repository,
        "app.py",
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/users')\ndef users(): pass\n",
    )
    _write(
        git_repository,
        "loadtests/users.js",
        "http.get(`${__ENV.BASE_URL}/users`);\n"
        "http.get(`/users/${id}`);\n"
        "http.get(`/users?filter=${value}`);\n"
        "http.get(__ENV.URL);\n",
    )
    base = _commit(git_repository, "base")
    _write(git_repository, "README.md", "candidate\n")
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-load-static",
        artifact_root=tmp_path,
    )

    edge = (
        "endpoint:GET /users",
        "VALIDATED_BY",
        "load_scenario:loadtests/users.js",
    )
    assert edge in _edge_set(snapshot)
    assert sum(item.code == "dynamic_load_endpoint" for item in snapshot.diagnostics) == 3
    assert 0 < snapshot.completeness < 1


def test_changed_symbols_require_valid_ri002_provenance(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(git_repository, "source.py", "def changed(): return 1\n")
    base = _commit(git_repository, "base")
    _write(git_repository, "source.py", "def changed(): return 2\n")
    candidate = _commit(git_repository, "candidate")
    change = _change(git_repository, base, candidate)
    change.metadata.pop("symbol_extraction")

    with pytest.raises(RepositoryGraphError, match="RI-002 provenance"):
        build_repository_graph(
            repository_path=git_repository,
            change=change,
            analysis_run_id="run-no-provenance",
            artifact_root=tmp_path,
        )


def test_tree_parser_rejects_truncation_and_non_ascii_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for output, message in (
        (b"100644 blob " + b"a" * 40 + b"\tfile.py", "Truncated"),
        (b"100644 bl\xffb " + b"a" * 40 + b"\tfile.py\0", "Invalid ls-tree header"),
    ):
        result = SimpleNamespace(returncode=0, stdout=output, stderr=b"")
        monkeypatch.setattr(graph_module, "_execute_git", lambda *_args, **_kwargs: result)
        with pytest.raises(GitExecutionError) as captured:
            graph_module._tree_entries(tmp_path, "a" * 40)
        assert captured.value.stderr == message or message in captured.value.stderr


def test_existing_artifact_size_is_checked_before_reading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "graph.json"
    target.write_bytes(b"unexpected-large")
    monkeypatch.setattr(
        graph_module.os,
        "read",
        lambda *_args, **_kwargs: pytest.fail("size mismatch must not read content"),
    )
    assert graph_module._existing_artifact_matches(target, b"small") is False


def test_supports_every_declared_endpoint_verb_and_rejects_dynamic_prefixes(
    git_repository: Path, tmp_path: Path
) -> None:
    decorators = "\n".join(
        f"@app.{method}('/{method}')\ndef handle_{method}(): pass"
        for method in sorted(graph_module._HTTP_METHODS)
    )
    _write(
        git_repository,
        "verbs.py",
        "from fastapi import APIRouter, FastAPI\n"
        "app = FastAPI()\n"
        "prefix = '/dynamic'\n"
        "router = APIRouter(prefix=prefix)\n"
        f"{decorators}\n",
    )
    base = _commit(git_repository, "base")
    _write(git_repository, "README.md", "candidate\n")
    candidate = _commit(git_repository, "candidate")
    snapshot = build_repository_graph(
        repository_path=git_repository,
        change=_change(git_repository, base, candidate),
        analysis_run_id="run-verbs",
        artifact_root=tmp_path,
    )

    for method in graph_module._HTTP_METHODS:
        assert f"endpoint:{method.upper()} /{method}" in snapshot.graph
    assert "dynamic_router_prefix" in {item.code for item in snapshot.diagnostics}
    assert snapshot.completeness < 1
