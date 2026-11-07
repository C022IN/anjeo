from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from anjeo.agent import Agent
from anjeo.api import create_app
from anjeo.config import AgentConfig, ServerConfig, load_settings
from anjeo.hub import Hub, namespaced

from .conftest import SENT


@pytest.fixture
def client(settings, connector):
    with TestClient(create_app(settings, connector=connector)) as c:
        yield c


def test_hub_namespaces_tools_and_isolates_a_dead_server(client):
    servers = {s["id"]: s for s in client.get("/servers").json()}
    assert servers["social"]["status"] == "online"
    assert servers["mail"]["status"] == "online"
    assert servers["down"]["status"] == "offline"
    names = {t["name"] for t in client.get("/tools").json()}
    assert {"social__list_posts", "social__send_message", "mail__read_inbox"} <= names


def test_policy_classification(client):
    policy = {t["name"]: t["policy"] for t in client.get("/tools").json()}
    assert policy["social__list_posts"] == "allow"  # lookup verb
    assert policy["mail__read_inbox"] == "allow"
    assert policy["social__send_message"] == "approve"  # action verb
    assert policy["social__mystery"] == "allow"  # read_only_hint
    assert policy["social__delete_everything"] == "approve"  # a read-only hint cannot launder a delete
    assert policy["social__frobnicate"] == "deny"  # explicit rule


def test_read_runs_immediately(client):
    r = client.post("/tools/call", json={"tool": "social__list_posts", "arguments": {"limit": 5}}).json()
    assert r["status"] == "executed"
    assert r["result"]["content"][0]["text"] == "5 posts"


def test_action_waits_for_approval_and_runs_once(client):
    r = client.post("/tools/call", json={"tool": "social__send_message", "arguments": {"to": "ada", "text": "hi"}})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert SENT == []  # nothing left the building

    approval_id = body["approval"]["id"]
    pending = client.get("/approvals").json()
    assert [a["id"] for a in pending] == [approval_id]
    assert pending[0]["arguments"] == {"to": "ada", "text": "hi"}

    done = client.post(f"/approvals/{approval_id}/approve").json()
    assert done["status"] == "executed"
    assert SENT == [{"to": "ada", "text": "hi"}]

    # A second tap (another device, a retry) cannot send it twice.
    assert client.post(f"/approvals/{approval_id}/approve").status_code == 409
    assert len(SENT) == 1
    assert client.get("/approvals").json() == []


def test_reject_never_executes(client):
    a = client.post("/tools/call", json={"tool": "social__send_message", "arguments": {"to": "x", "text": "y"}})
    approval_id = a.json()["approval"]["id"]
    assert client.post(f"/approvals/{approval_id}/reject").json()["status"] == "rejected"
    assert client.post(f"/approvals/{approval_id}/approve").status_code == 409
    assert SENT == []


def test_expired_approval_cannot_run(settings, connector):
    settings.policy.approval_ttl_hours = -1  # already expired on creation
    with TestClient(create_app(settings, connector=connector)) as c:
        a = c.post("/tools/call", json={"tool": "social__send_message", "arguments": {"to": "x", "text": "y"}})
        approval_id = a.json()["approval"]["id"]
        assert c.post(f"/approvals/{approval_id}/approve").status_code == 409
        assert c.get("/approvals", params={"status": "expired"}).json()[0]["id"] == approval_id
    assert SENT == []


def test_denied_and_unknown(client):
    assert client.post("/tools/call", json={"tool": "social__frobnicate"}).json()["status"] == "denied"
    assert client.post("/tools/call", json={"tool": "nope__nothing"}).status_code == 404


def test_memory_roundtrip(client):
    assert client.put("/memory/voice", json={"value": "direct, warm, no buzzwords"}).status_code == 200
    assert client.get("/memory").json()[0]["value"] == "direct, warm, no buzzwords"
    assert client.delete("/memory/voice").status_code == 200
    assert client.delete("/memory/voice").status_code == 404


def test_token_is_enforced(settings, connector):
    settings.api_token = "s3cret"
    with TestClient(create_app(settings, connector=connector)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/tools").status_code == 401
        assert c.get("/tools", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/tools", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_activity_is_recorded(client):
    client.post("/tools/call", json={"tool": "social__list_posts", "arguments": {}})
    client.post("/tools/call", json={"tool": "social__send_message", "arguments": {"to": "a", "text": "b"}})
    kinds = [(a["kind"], a["status"]) for a in client.get("/activity").json()]
    assert ("call", "executed") in kinds
    assert ("approval", "pending") in kinds


def test_config_rejects_bad_servers(tmp_path, monkeypatch):
    p = tmp_path / "anjeo.toml"
    p.write_text('[[servers]]\nid = "x"\nurl = "http://a"\ncommand = "b"\n')
    with pytest.raises(ValueError):
        load_settings(p)
    with pytest.raises(ValueError):
        ServerConfig(id="Bad-Id", url="http://a")


def test_env_refs_must_exist(monkeypatch):
    cfg = ServerConfig(id="li", url="http://a", headers={"Authorization": "Bearer ${LI_TOKEN}"})
    monkeypatch.delenv("LI_TOKEN", raising=False)
    with pytest.raises(KeyError):
        cfg.resolved_headers()
    monkeypatch.setenv("LI_TOKEN", "abc")
    assert cfg.resolved_headers() == {"Authorization": "Bearer abc"}


def test_namespacing_is_model_safe():
    name = namespaced("srv", "weird.name/with spaces" + "x" * 80)
    assert len(name) <= 64 and all(ch.isalnum() or ch in "_-" for ch in name)


# ── the agent, with a scripted model ─────────────────────────────────────────


def _block(**kw):
    return SimpleNamespace(**kw)


class ScriptedClaude:
    """Plays back a fixed sequence of responses and records every request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


def test_agent_reads_freely_and_queues_actions(settings, connector):
    claude = ScriptedClaude(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    _block(type="tool_use", id="t1", name="mail__read_inbox", input={}),
                    _block(type="tool_use", id="t2", name="social__send_message", input={"to": "ada", "text": "hi"}),
                ],
            ),
            SimpleNamespace(
                stop_reason="end_turn",
                content=[_block(type="text", text="Read your inbox. A DM to Ada is waiting for your approval.")],
            ),
        ]
    )
    agent = Agent(gateway=None, config=AgentConfig(owner_name="Coll"), client=claude)  # type: ignore[arg-type]
    with TestClient(create_app(settings, connector=connector, agent=agent)) as c:
        c.put("/memory/voice", json={"value": "warm"})
        out = c.post("/ask", json={"prompt": "Catch me up and say hi to Ada"}).json()

        assert out["stop_reason"] == "end_turn"
        assert [s["status"] for s in out["steps"]] == ["executed", "pending_approval"]
        assert len(out["pending_approvals"]) == 1
        assert SENT == []  # the agent cannot send on its own

        pending = c.get("/approvals").json()[0]
        assert pending["source"] == "agent" and pending["intent"] == "Catch me up and say hi to Ada"

    first, second = claude.requests
    tool_names = [t["name"] for t in first["tools"]]
    assert tool_names == sorted(tool_names)  # stable order keeps the prompt cache warm
    assert "warm" in first["system"][-1]["text"]  # memory reaches the model
    assert first["fallbacks"] == "default"
    results = second["messages"][-1]["content"]
    assert len(results) == 2 and {r["tool_use_id"] for r in results} == {"t1", "t2"}
    assert "pending_approval" in results[1]["content"]


def test_agent_stops_at_max_steps(settings, connector):
    loop = SimpleNamespace(
        stop_reason="tool_use", content=[_block(type="tool_use", id="t", name="social__list_posts", input={})]
    )
    claude = ScriptedClaude([loop] * 3)
    agent = Agent(gateway=None, config=AgentConfig(max_steps=3), client=claude)  # type: ignore[arg-type]
    with TestClient(create_app(settings, connector=connector, agent=agent)) as c:
        out = c.post("/ask", json={"prompt": "loop forever"}).json()
    assert out["stop_reason"] == "max_steps"
    assert len(claude.requests) == 3


async def test_hub_refresh_single_server(settings, connector):
    hub = Hub(settings.servers, connector=connector)
    await asyncio.gather(hub.refresh("social"))
    assert hub.servers["social"].status == "online"
    assert hub.servers["mail"].status == "unknown"
