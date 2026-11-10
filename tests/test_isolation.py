"""D5：网络三档映射、bash 隔离、deny_paths、降级标记。"""

from __future__ import annotations

import pytest

from molt.core.errors import IsolationError
from molt.core.isolation import (
    PROVIDER_ENDPOINT,
    SANDBOX_UNAVAILABLE_MARKER,
    IsolationPolicy,
    network_mapping,
)
from molt.core.types import NETWORK_LEVELS, NETWORK_TO_LEVEL


def test_mapping_is_exact_and_matches_enum() -> None:
    assert network_mapping() == {
        "none": "sandboxed",
        "provider_only": "provider-sandboxed",
        "open": "permissions-only",
    }
    assert set(NETWORK_TO_LEVEL) == set(NETWORK_LEVELS)
    for network in NETWORK_LEVELS:
        policy = IsolationPolicy.resolve(network)
        assert policy.level == NETWORK_TO_LEVEL[network]
        assert policy.degraded is False
        assert policy.claim_marker is None


def test_sandbox_unavailable_degrades_with_marker() -> None:
    policy = IsolationPolicy.resolve("none", sandbox_available=False)
    assert policy.level == "permissions-only"
    assert policy.network == "open"
    assert policy.degraded is True
    assert policy.sandbox_unavailable is True
    assert policy.claim_marker == SANDBOX_UNAVAILABLE_MARKER


@pytest.mark.parametrize("network", ["none", "provider_only"])
def test_sandbox_unavailable_never_claims_provider_only(network: str) -> None:
    """bwrap 不可用时网络实际开放，不能继续声称 none/provider_only。"""
    policy = IsolationPolicy.resolve(network, sandbox_available=False)
    assert policy.network == "open"
    assert policy.level == "permissions-only"
    assert policy.degraded is True
    assert policy.claim_marker == SANDBOX_UNAVAILABLE_MARKER


def test_explicit_open_is_not_marked_degraded() -> None:
    policy = IsolationPolicy.resolve("open")
    assert policy.level == "permissions-only"
    assert policy.degraded is False
    assert policy.claim_marker is None


def test_network_triple_sandboxed() -> None:
    policy = IsolationPolicy.resolve("none")
    assert policy.allows_model_call() is True
    assert policy.allows_bash_network(PROVIDER_ENDPOINT) is False
    assert policy.allows_bash_network("https://example.com") is False
    with pytest.raises(IsolationError):
        policy.check_command(f"curl {PROVIDER_ENDPOINT}")
    with pytest.raises(IsolationError):
        policy.check_command("curl https://example.com")


def test_provider_only_still_blocks_bash_network() -> None:
    policy = IsolationPolicy.resolve("provider_only")
    assert policy.level == "provider-sandboxed"
    assert policy.allows_model_call() is True
    assert policy.allows_bash_network(PROVIDER_ENDPOINT) is False


def test_permissions_only_is_explicitly_weaker() -> None:
    policy = IsolationPolicy.resolve("open")
    assert policy.allows_bash_network("https://example.com") is True
    policy.check_command("curl https://example.com")
    policy.check_agent_read("bench/holdout/holdout-even/task.json")


def test_deny_paths_cover_bench_web_evidence() -> None:
    policy = IsolationPolicy.resolve("none")
    for path in ("bench", "bench/holdout/x", "web/data/report_results.json", "evidence/x.json"):
        with pytest.raises(IsolationError):
            policy.check_agent_read(path)
    for path in ("molt/core/store.py", "shells/code-repair/harness.py", "benchx/not-denied"):
        policy.check_agent_read(path)


def test_invalid_network_value_rejected() -> None:
    with pytest.raises(IsolationError):
        IsolationPolicy.resolve("offline")
