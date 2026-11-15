"""B2 upper-layer API tests: auth, read-only, CAS, approval, replay, contract."""

from __future__ import annotations

import http.client
import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from molt.agent.chat import MockAdapter
from molt.agent.probe import build_index
from molt.agent.server import ROUTES, AgentApp, create_server, load_openapi
from molt.agent.upper import UPPER_SCENARIO_REF, UpperService
from molt.upper.graph import UpperGraphStore
from molt.upper.registry import UpperRegistry

REPO = Path(__file__).resolve().parents[1]
TOKEN = "test-token-upper-only"
CONTRACT = REPO / "contracts" / "openapi-upper-v1.json"
ABS_PATH_RE = re.compile(r"/(?:home|Users|root|tmp|var|etc|opt|srv|run)/[^\s\"',\]\)}]+")


def _fake_seed_base(tmp_path: Path) -> Path:
    base = tmp_path / "seed-base"
    lesson = base / "docs" / "lessons" / "2026-08-23-chengdu-public-ip-ipv6.md"
    lesson.parent.mkdir(parents=True)
    lesson.write_text(
        "---\ntitle: safe ops lesson\ndate: 2026-08-23\n---\nUse a bounded timeout.\n",
        encoding="utf-8",
    )
    dev_lesson = base / "docs" / "lessons" / "2026-09-05-python-curses-tui.md"
    dev_lesson.write_text(
        "---\ntitle: safe dev lesson\n---\nPrefer explicit terminal state.\n",
        encoding="utf-8",
    )
    skill = base / "opencode" / "skills" / "router-ops" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# router ops\n\nRead-only diagnostics.\n", encoding="utf-8")
    dev_skill = base / "opencode" / "skills" / "memory" / "SKILL.md"
    dev_skill.parent.mkdir(parents=True)
    dev_skill.write_text("# memory\n\nRecord lessons.\n", encoding="utf-8")
    return base


class UpperHarness:
    def __init__(self, tmp_path: Path, *, replay_runner=None) -> None:
        self.state = tmp_path / "serve-state"
        self.service = UpperService(
            self.state,
            repo_root=REPO,
            seed_base=_fake_seed_base(tmp_path),
            replay_runner=replay_runner,
        )
        self.app = AgentApp(
            index=build_index(REPO),
            adapter=MockAdapter(),
            token=TOKEN,
            allowed_origins=(),
            openapi=load_openapi(REPO),
            data_dir=REPO / "web" / "data",
            root=REPO,
            upper=self.service,
        )
        self.server = create_server(self.app, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        token: str | None = TOKEN,
        headers: dict[str, str] | None = None,
        raw_host: str | None = None,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        payload = raw_body
        request_headers = dict(headers or {})
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        if raw_host is None:
            connection.request(method, path, body=payload, headers=request_headers)
        else:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", raw_host)
            for key, value in request_headers.items():
                connection.putheader(key, value)
            if payload is not None:
                connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders()
            if payload is not None:
                connection.send(payload)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def get_json(self, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        status, _, body = self.request("GET", path, **kwargs)
        return status, json.loads(body.decode("utf-8"))

    def post_json(self, path: str, body: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        status, _, raw = self.request("POST", path, body=body, **kwargs)
        return status, json.loads(raw.decode("utf-8"))


@pytest.fixture()
def harness(tmp_path: Path):
    instance = UpperHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _graph(harness: UpperHarness) -> dict[str, Any]:
    status, payload = harness.get_json("/api/upper/graph")
    assert status == 200, payload
    return payload


def _branch(graph: dict[str, Any], label: str) -> dict[str, Any]:
    matches = [branch for branch in graph["branches"] if branch["label"] == label]
    assert len(matches) == 1
    return matches[0]


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def _assert_safe(payload: Any, *, forbidden: tuple[str, ...]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    assert not ABS_PATH_RE.search(text)
    for item in forbidden:
        assert item not in text
    for key in _walk(payload):
        assert key not in {"source_path", "absolute_path", "raw", "raw_text"}
    for value in _walk(payload):
        if isinstance(value, str):
            assert "token=" not in value.lower()
            assert "password=" not in value.lower()


def test_requires_bearer_and_rejects_bad_host_origin(harness: UpperHarness) -> None:
    status, headers, raw = harness.request("GET", "/api/upper/graph", token=None)
    assert status == 401
    assert headers.get("WWW-Authenticate") == "Bearer"
    assert json.loads(raw.decode())["error"]["code"] == "unauthorized"

    status, _, raw = harness.request("GET", "/api/upper/graph", token="wrong-token")
    assert status == 401
    assert json.loads(raw.decode())["error"]["code"] == "unauthorized"

    status, _, raw = harness.request(
        "GET", "/api/upper/graph", headers={"Origin": "https://evil.example"}
    )
    assert status == 403
    assert json.loads(raw.decode())["error"]["code"] == "forbidden_origin"

    status, _, raw = harness.request("GET", "/api/upper/graph", raw_host="evil.example")
    assert status == 400
    assert json.loads(raw.decode())["error"]["code"] == "bad_request"

    status, _, raw = harness.request("OPTIONS", "/api/upper/graph", token=None)
    assert status == 204
    assert raw == b""


def test_get_graph_is_read_only_and_projection_is_redacted(harness: UpperHarness) -> None:
    graph = _graph(harness)
    assert graph["revision"] >= 0
    assert graph["active"]["branch_id"]
    assert {branch["label"] for branch in graph["branches"]} == {"ops", "dev"}
    for branch in graph["branches"]:
        assert set(branch) == {
            "branch_id",
            "label",
            "permission_scope",
            "tip_node_id",
            "tip_hash",
        }
        assert branch["tip_hash"] == branch["tip_node_id"]
    for node in graph["nodes"]:
        assert set(node) == {
            "node_id",
            "parents",
            "kind",
            "origin",
            "eval_state",
            "context_hash",
            "hash_short",
            "created_at",
        }
        if node["origin"] in {"seed_import", "synthetic"}:
            assert node["eval_state"] == "not_evaluated"
    assert "upper_decision" not in json.dumps(graph)
    _assert_safe(graph, forbidden=(str(REPO), str(harness.state), TOKEN))

    state_upper = harness.state / "upper"
    before = {
        path.name: path.read_bytes()
        for path in sorted(state_upper.iterdir())
        if path.is_file()
    }
    for _ in range(2):
        status, assets = harness.get_json("/api/upper/assets")
        assert status == 200
    assert {
        path.name: path.read_bytes()
        for path in sorted(state_upper.iterdir())
        if path.is_file()
    } == before

    kinds = {asset["kind"] for asset in assets["assets"]}
    assert {"policy_memory", "skill", "mcp_profile"} <= kinds
    for asset in assets["assets"]:
        assert "source_path" not in asset
        assert "content" not in asset
        assert "summary" in asset
        assert "seed_identity_hash" in asset
        assert "record_hash" in asset
    mcp = [asset for asset in assets["assets"] if asset["kind"] == "mcp_profile"]
    assert mcp and mcp[0]["approval_level"] == "strict"
    assert mcp[0]["pending_permission"] is not None
    _assert_safe(assets, forbidden=(str(REPO), str(harness.state), TOKEN))


def test_bad_requests_413_404_and_405(harness: UpperHarness) -> None:
    status, payload = harness.post_json("/api/upper/replay", {"scenario": "other"})
    assert status == 400
    assert payload["error"]["code"] == "bad_request"

    status, payload = harness.post_json(
        "/api/upper/branches/nope/switch", {"expected_revision": 0}
    )
    assert status == 400
    assert payload["error"]["code"] == "bad_request"

    status, payload = harness.post_json(
        "/api/upper/rollback",
        {"branch_id": "br-1", "target_node_id": "n-1", "expected_revision": 0, "extra": 1},
    )
    assert status == 400
    assert payload["error"]["code"] == "bad_request"

    status, payload = harness.get_json("/api/upper/evaluations/does-not-exist")
    assert status == 404
    assert payload["error"]["code"] == "not_found"

    graph = _graph(harness)
    ops = _branch(graph, "ops")
    status, payload = harness.post_json(
        f"/api/upper/branches/{ops['branch_id']}/switch",
        {"expected_revision": graph["revision"], "expected_tip": ops["tip_node_id"]},
        token=None,
    )
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"

    status, _, _ = harness.request("GET", "/api/upper/rollback")
    assert status == 405

    huge = b'{"pad":"' + b"x" * (70 * 1024) + b'"}'
    status, payload = harness.post_json(
        "/api/upper/replay", None, raw_body=huge, headers={"Content-Type": "application/json"}
    )
    assert status == 413
    assert payload["error"]["code"] == "payload_too_large"


@pytest.mark.parametrize(
    "path",
    [
        "/api/upper/replay",
        "/api/upper/branches/br-x/switch",
        "/api/upper/rollback",
    ],
)
def test_post_rejects_non_json_content_type(harness: UpperHarness, path: str) -> None:
    status, payload = harness.post_json(
        path, None, raw_body=b"{}", headers={"Content-Type": "text/plain"}
    )
    assert status == 415
    assert payload["error"]["code"] == "unsupported_media_type"


def test_upper_service_requires_state_outside_repo(tmp_path: Path) -> None:
    service = UpperService(tmp_path / "upper-state", repo_root=REPO)
    assert service.state_dir.is_dir()
    assert service.upper_dir.is_dir()
    assert (service.state_dir.stat().st_mode & 0o777) == 0o700
    assert (service.upper_dir.stat().st_mode & 0o777) == 0o700

    with pytest.raises(ValueError):
        UpperService(REPO / "state", repo_root=REPO)


def test_switch_cas_and_rollback_keep_pointer_and_history(harness: UpperHarness) -> None:
    graph = _graph(harness)
    ops = _branch(graph, "ops")
    dev = _branch(graph, "dev")
    current_path = harness.state / "upper" / "current.json"
    before_pointer = current_path.read_bytes()

    status, payload = harness.post_json(
        f"/api/upper/branches/{dev['branch_id']}/switch",
        {
            "expected_revision": max(graph["revision"] - 1, 0),
            "expected_tip": dev["tip_node_id"],
        },
    )
    assert status == 409
    assert payload["error"]["code"] == "stale_revision"
    assert current_path.read_bytes() == before_pointer

    status, payload = harness.post_json(
        f"/api/upper/branches/{dev['branch_id']}/switch",
        {"expected_revision": graph["revision"], "expected_tip": ops["tip_node_id"]},
    )
    assert status == 409
    assert payload["error"]["code"] == "stale_tip"
    assert current_path.read_bytes() == before_pointer

    status, payload = harness.post_json(
        f"/api/upper/branches/{dev['branch_id']}/switch",
        {"expected_revision": graph["revision"], "expected_tip": dev["tip_node_id"]},
    )
    assert status == 200, payload
    assert payload["changed"] is True
    assert payload["active"]["branch_id"] == dev["branch_id"]
    assert payload["revision"] == graph["revision"] + 1

    status, payload = harness.post_json(
        f"/api/upper/branches/{dev['branch_id']}/switch",
        {"expected_revision": graph["revision"] + 1, "expected_tip": dev["tip_node_id"]},
    )
    assert status == 200, payload
    assert payload["changed"] is False
    assert payload["revision"] == graph["revision"] + 1

    graph = _graph(harness)
    ops = _branch(graph, "ops")
    nodes_before = graph["nodes"]
    root = [node for node in graph["nodes"] if node["kind"] == "root"][0]
    status, payload = harness.post_json(
        "/api/upper/rollback",
        {
            "branch_id": ops["branch_id"],
            "target_node_id": root["node_id"],
            "expected_revision": graph["revision"],
        },
    )
    assert status == 200, payload
    assert payload["history_retained"] is True
    assert payload["active"]["context_node_id"] == root["node_id"]

    graph_after = _graph(harness)
    assert len(graph_after["nodes"]) == len(nodes_before)
    assert {node["node_id"] for node in graph_after["nodes"]} == {
        node["node_id"] for node in nodes_before
    }
    assert graph_after["active"]["context_node_id"] == payload["target_node_id"]
    events = [
        json.loads(line)
        for line in (harness.state / "upper" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event.get("op") == "rollback" for event in events)


def test_switch_rejects_draft_asset_and_keeps_pointer(harness: UpperHarness) -> None:
    graph = _graph(harness)
    ops = _branch(graph, "ops")
    state_dir = harness.state
    registry = UpperRegistry(state_dir, repo_root=REPO)
    store = UpperGraphStore(state_dir, repo_root=REPO, registry=registry)
    seed_nodes = [node for node in store.nodes() if node.origin == "seed_import"]
    assert seed_nodes, "fake seed fixture must create a seed context node"
    guarded = store.fork(
        "guarded",
        seed_nodes[0].node_id,
        author="tests",
        permission_scope="read:seed",
        approval_level="standard",
    )
    # Approve then roll back the referenced asset so the branch tip is stale.
    for ref in seed_nodes[0].asset_refs:
        registry.mark_evaluated(ref["asset_id"])
        registry.approve(ref["asset_id"], approved_by="tests")
        registry.activate(ref["asset_id"])
        registry.rollback(ref["asset_id"], reason="approval revoked", rolled_back_by="tests")

    graph = _graph(harness)
    guarded_payload = _branch(graph, "guarded")
    current_path = state_dir / "upper" / "current.json"
    before_pointer = current_path.read_bytes()
    status, payload = harness.post_json(
        f"/api/upper/branches/{guarded.branch_id}/switch",
        {
            "expected_revision": graph["revision"],
            "expected_tip": guarded_payload["tip_node_id"],
        },
    )
    assert status == 422
    assert payload["error"]["code"] == "approval_required"
    assert current_path.read_bytes() == before_pointer
    assert _graph(harness)["active"]["branch_id"] == ops["branch_id"]


def test_rollback_rejects_non_ancestor(harness: UpperHarness) -> None:
    graph = _graph(harness)
    ops = _branch(graph, "ops")
    seed_nodes = [node for node in graph["nodes"] if node["origin"] == "seed_import"]
    assert seed_nodes
    current_path = harness.state / "upper" / "current.json"
    before_pointer = current_path.read_bytes()
    status, payload = harness.post_json(
        "/api/upper/rollback",
        {
            "branch_id": ops["branch_id"],
            "target_node_id": seed_nodes[0]["node_id"],
            "expected_revision": graph["revision"],
        },
    )
    assert status == 422
    assert payload["error"]["details"]["reason"] == "target_not_ancestor"
    assert current_path.read_bytes() == before_pointer


def test_replay_is_fixed_and_records_keep_rollback(harness: UpperHarness) -> None:
    graph = _graph(harness)
    status, replay = harness.post_json("/api/upper/replay", {})
    assert status == 200, replay
    assert replay["mode"] == "replay"
    assert replay["mode_label"] == "REPLAY"
    assert replay["calls_model"] is False
    assert replay["upper_scenario_id"] == UPPER_SCENARIO_REF
    assert replay["graph_rollback"]["target_node_id"]

    transfer = {item["asset_id"]: item for item in replay["phases"]["transfer"]}
    regression = {item["asset_id"]: item for item in replay["phases"]["regression"]}
    assert transfer["mem-c1b-gen2"]["upper_decision"] == "keep"
    assert transfer["mem-c1b-gen4"]["upper_decision"] == "keep"
    assert regression["mem-c1b-gen2"]["upper_decision"] == "rollback"
    assert regression["mem-c1b-gen4"]["upper_decision"] == "keep"
    assert transfer["mem-c1b-gen2"]["candidate"]["passed"] > 0

    status, evaluation = harness.get_json(
        "/api/upper/evaluations/eval-c1b-memory-v1-regression-mem-c1b-gen2"
    )
    assert status == 200, evaluation
    assert evaluation["task_set"] == "regression"
    assert evaluation["mode"] == "replay"
    assert evaluation["mode_label"] == "REPLAY"
    assert {task["task_id"] for task in evaluation["tasks"]} == {"probe-order-x8"}
    for task in evaluation["tasks"]:
        assert task["cost_usd"] == 0.0
        assert task["retrieval"]["mode"] == "deterministic-exact-prefix"
    assert evaluation["decision"]["upper_decision"] == "rollback"

    status, assets = harness.get_json("/api/upper/assets")
    assert status == 200
    statuses = {asset["asset_id"]: asset["status"] for asset in assets["assets"]}
    assert statuses["mem-c1b-gen2"] == "rolled_back"
    assert statuses["mem-c1b-gen4"] == "active"
    mcp = [asset for asset in assets["assets"] if asset["asset_id"] == "mcp-c1b-probe-read"][0]
    assert mcp["pending_permission"] is not None

    for payload in (graph, replay, evaluation, assets):
        _assert_safe(payload, forbidden=(str(REPO), str(harness.state), TOKEN))
    assert _graph(harness)["revision"] == graph["revision"]


def test_replay_is_single_flight(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_runner(**_: Any) -> dict[str, Any]:
        started.set()
        assert release.wait(5)
        return {
            "mode": "replay",
            "calls_model": False,
            "upper_scenario_id": UPPER_SCENARIO_REF,
            "upper_scenario_version": "upper-c1b-1",
            "scenario_ref": "upper_scenarios/c1b-memory-v1/scenario.json",
            "phases": {"transfer": [], "regression": []},
            "decisions": [],
            "graph": {},
            "graph_rollback": None,
            "final_state": {},
            "notes": [],
        }

    instance = UpperHarness(tmp_path, replay_runner=slow_runner)
    first: dict[str, Any] = {}

    def call_first() -> None:
        first["status"], first["body"] = instance.post_json("/api/upper/replay", {})

    thread = threading.Thread(target=call_first, daemon=True)
    try:
        assert instance.get_json("/api/upper/graph")[0] == 200
        thread.start()
        assert started.wait(5)
        status, payload = instance.post_json("/api/upper/replay", {})
        assert status == 409
        assert payload["error"]["code"] == "upper_busy"
        assert payload["error"]["details"]["reason"] == "upper_busy"
        release.set()
        thread.join(timeout=10)
        assert first["status"] == 200
        assert first["body"]["mode"] == "replay"
    finally:
        release.set()
        instance.close()


def test_contract_paths_match_routes() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = {
        ("GET", "/api/upper/graph"),
        ("GET", "/api/upper/assets"),
        ("POST", "/api/upper/replay"),
        ("GET", "/api/upper/evaluations/{evaluation_id}"),
        ("POST", "/api/upper/branches/{branch_id}/switch"),
        ("POST", "/api/upper/rollback"),
    }
    assert set(contract["paths"]) == {path for _, path in expected}
    implemented = {
        (method, _route_template(pattern.pattern))
        for method, pattern, name in ROUTES
        if name.startswith("upper_")
    }
    assert implemented == expected


def _route_template(pattern: str) -> str:
    template = re.sub(r"\(\?P<([a-z_]+)>\[\^/\]\+\)", r"{\1}", pattern)
    template = template.removeprefix("^").removesuffix("$")
    return template
