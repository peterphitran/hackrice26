"""INT-002 must rely on the real local application composition."""

from lou.application import FixtureRepositoryIntelligence, FixtureWorkloadSelector
from lou.verification import int002


def test_int002_has_no_stubbed_graph_or_workload_components() -> None:
    assert "StubGraphContextIntelligence" not in int002.__dict__
    assert "StubCheckoutWorkloadSelector" not in int002.__dict__
    assert FixtureRepositoryIntelligence.__module__ == "lou.application.live"
    assert FixtureWorkloadSelector.__module__ == "lou.application.live"
