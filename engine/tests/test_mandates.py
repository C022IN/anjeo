"""Mandates: Anjeo acts alone inside the limits you wrote, and asks you outside them."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from anjeo.agent import Agent
from anjeo.api import create_app
from anjeo.config import (
    AgentConfig,
    ArgLimit,
    Mandate,
    MandateCheck,
    PolicyConfig,
    PolicyRule,
    Routine,
    ServerConfig,
    Settings,
)
from anjeo.gateway import Gateway
from anjeo.hub import Hub
from anjeo.store import Store

TICKETS: list[dict] = []
KILLS: list[str] = []
STATE = {"day_pnl_pct": 0.4, "overview_up": True}


def fx_server() -> MCPServer:
    srv = MCPServer("fx")

    @srv.tool(annotations=ToolAnnotations(read_only_hint=True))
    def overview() -> dict:
        """Aggregated account state."""
        if not STATE["overview_up"]:
            raise RuntimeError("control plane down")
        return {"day_pnl_pct": STATE["day_pnl_pct"], "open_positions": 1}

    @srv.tool()
    def submit_ticket(symbol: str, side: str, stop: float, group: str, risk_pct: float | None = None) -> str:
        """Queue a trade intent."""
        TICKETS.append({"symbol": symbol, "side": side, "risk_pct": risk_pct, "group": group})
        return f"queued {side} {symbol}"

    @srv.tool(meta={"anjeo/owner_only": True})
    def kill_account(account_id: str) -> str:
        """Kill switch."""
        KILLS.append(account_id)
        return "killed"

    return srv


MANDATE = Mandate(
    id="fx-demo",
    tool="fx__submit_ticket",
    max_per_day=2,
    args={
        "risk_pct": ArgLimit(required=True, min=0.05, max=0.5),
        "symbol": ArgLimit(one_of=["XAUUSD", "EURUSD"]),
        "group": ArgLimit(required=True, one_of=["anjeo_demo"]),
    },
    checks=[MandateCheck(tool="fx__overview", path="day_pnl_pct", min=-2.0, label="daily P&L %")],
    approval_ttl_minutes=15,
)

GOOD = {"symbol": "XAUUSD", "side": "BUY", "stop": 2300.0, "group": "anjeo_demo", "risk_pct": 0.25}


@pytest.fixture(autouse=True)
def _reset():
    TICKETS.clear()
    KILLS.clear()
    STATE.update(day_pnl_pct=0.4, overview_up=True)


@pytest.fixture
def fx_settings(tmp_path) -> Settings:
    return Settings(
        servers=[ServerConfig(id="fx", url="http://fx.invalid/mcp")],
        mandates=[MANDATE, Mandate(id="kill-bot", tool="fx__kill_account")],
        policy=PolicyConfig(rules=[PolicyRule(match="fx__kill_account", action="allow")]),
        database=str(tmp_path / "anjeo.db"),
    )


@pytest.fixture
def fx_connector():
    srv = fx_server()
    return lambda cfg: Client(srv)


@pytest.fixture
def fx(fx_settings, fx_connector):
    with TestClient(create_app(fx_settings, connector=fx_connector, start_routines=False)) as c:
        yield c


def submit(c, **overrides):
    return c.post("/tools/call", json={"tool": "fx__submit_ticket", "arguments": {**GOOD, **overrides}}).json()


def test_inside_the_mandate_it_just_acts(fx):
    out = submit(fx)
    assert out["status"] == "executed" and out["mandate"] == "fx-demo"
    assert TICKETS == [{"symbol": "XAUUSD", "side": "BUY", "risk_pct": 0.25, "group": "anjeo_demo"}]
    assert fx.get("/approvals").json() == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"risk_pct": 0.8}, "risk_pct 0.8 > 0.5"),
        ({"risk_pct": None}, "risk_pct is required"),
        ({"risk_pct": "lots"}, "is not a number"),
        ({"symbol": "BTCUSD"}, "symbol=BTCUSD not in"),
        ({"group": "funded_real"}, "group=funded_real not in"),
    ],
)
def test_outside_the_mandate_it_asks_and_says_why(fx, overrides, reason):
    out = submit(fx, **overrides)
    assert out["status"] == "pending_approval"
    assert reason in out["reason"] and out["reason"].startswith("outside mandate fx-demo")
    assert TICKETS == []
    approval = fx.get("/approvals").json()[0]
    # A trade that waits must go stale fast: 15 minutes, not the 24h default.
    assert approval["expires_at"] - approval["created_at"] == pytest.approx(15 * 60, abs=2)


def test_daily_limit(fx):
    assert submit(fx)["status"] == "executed"
    assert submit(fx)["status"] == "executed"
    third = submit(fx)
    assert third["status"] == "pending_approval" and "daily limit reached (2/2)" in third["reason"]
    # Approving it runs it, and an owner-approved run does not consume the autonomous budget.
    fx.post(f"/approvals/{third['approval']['id']}/approve")
    assert len(TICKETS) == 3
    assert fx.get("/mandates").json()[0]["used_today"] == 2


def test_live_loss_check_blocks_after_a_bad_day(fx):
    STATE["day_pnl_pct"] = -2.6
    out = submit(fx)
    assert out["status"] == "pending_approval" and "daily P&L % -2.6 < -2" in out["reason"]
    assert TICKETS == []


def test_unverifiable_check_fails_closed(fx):
    STATE["overview_up"] = False
    out = submit(fx)
    assert out["status"] == "pending_approval" and "could not verify daily P&L %" in out["reason"]
    assert TICKETS == []


def test_kill_switch_always_needs_the_owner(fx):
    # anjeo.toml says allow AND there is a mandate for it; the server's owner-only mark still wins.
    out = fx.post("/tools/call", json={"tool": "fx__kill_account", "arguments": {"account_id": "demo"}}).json()
    assert out["status"] == "pending_approval" and "owner only" in out["reason"]
    assert KILLS == []
    tools = {t["name"]: t for t in fx.get("/tools").json()}
    assert tools["fx__kill_account"]["policy"] == "approve" and tools["fx__kill_account"]["owner_only"]
    assert tools["fx__submit_ticket"]["policy"] == "mandate"
    mandates = {m["id"]: m for m in fx.get("/mandates").json()}
    assert mandates["fx-demo"]["active"] and not mandates["kill-bot"]["active"]


def test_owner_only_can_still_be_denied(fx_settings, fx_connector):
    fx_settings.policy.rules = [PolicyRule(match="fx__kill_account", action="deny")]
    with TestClient(create_app(fx_settings, connector=fx_connector, start_routines=False)) as c:
        out = c.post("/tools/call", json={"tool": "fx__kill_account", "arguments": {"account_id": "x"}}).json()
    assert out["status"] == "denied"


async def test_parallel_calls_cannot_overrun_the_daily_limit(fx_settings, fx_connector):
    store = Store(fx_settings.database)
    await store.open()
    hub = Hub(fx_settings.servers, connector=fx_connector)
    await hub.refresh()
    gw = Gateway(hub, store, fx_settings.policy, fx_settings.mandates)
    outs = await asyncio.gather(*(gw.call("fx__submit_ticket", dict(GOOD), source="agent") for _ in range(5)))
    await store.close()
    assert sorted(o["status"] for o in outs) == ["executed"] * 2 + ["pending_approval"] * 3
    assert len(TICKETS) == 2


# ── routines: Anjeo's own thoughts trigger FXRizon ───────────────────────────


class ScriptedClaude:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


def test_a_routine_trades_inside_the_mandate_and_queues_the_rest(fx_settings, fx_connector):
    b = SimpleNamespace
    claude = ScriptedClaude(
        [
            b(stop_reason="tool_use", content=[b(type="tool_use", id="r", name="fx__overview", input={})]),
            b(
                stop_reason="tool_use",
                content=[
                    b(type="tool_use", id="t1", name="fx__submit_ticket", input=dict(GOOD)),
                    b(type="tool_use", id="t2", name="fx__submit_ticket", input={**GOOD, "risk_pct": 1.0}),
                ],
            ),
            b(stop_reason="end_turn", content=[b(type="text", text="Bought gold at 0.25%; a 1% idea awaits you.")]),
        ]
    )
    fx_settings.routines = [Routine(id="gold-watch", prompt="Watch gold and act within mandate.", every_minutes=15)]
    agent = Agent(gateway=None, config=AgentConfig(), client=claude)  # type: ignore[arg-type]
    with TestClient(create_app(fx_settings, connector=fx_connector, agent=agent, start_routines=False)) as c:
        out = c.post("/routines/gold-watch/run").json()
        assert [s["status"] for s in out["steps"]] == ["executed", "executed", "pending_approval"]
        assert out["steps"][1]["mandate"] == "fx-demo"
        pending = c.get("/approvals").json()[0]
        assert pending["source"] == "routine:gold-watch" and "risk_pct 1 > 0.5" in pending["reason"]
        assert c.get("/routines").json()[0]["last_answer"].startswith("Bought gold")
        assert c.post("/routines/nope/run").status_code == 404
    assert len(TICKETS) == 1
    # The model is told why it was queued, so it doesn't retry or shave the size to sneak under.
    results = claude.requests[2]["messages"][-1]["content"]
    assert "outside mandate" in results[1]["content"]


async def test_routine_schedule_waits_a_full_interval_and_never_overlaps(fx_settings, fx_connector):
    from anjeo.routines import Routines

    started = asyncio.Event()
    release = asyncio.Event()

    class SlowAgent:
        gateway = SimpleNamespace(store=SimpleNamespace(log=lambda *a, **k: asyncio.sleep(0)))

        async def ask(self, prompt, source):
            started.set()
            await release.wait()
            return {"answer": "done"}

    r = Routines(SlowAgent(), [Routine(id="x", prompt="p", every_minutes=5)])  # type: ignore[arg-type]
    r.start(tick_seconds=3600)
    assert r.due() == []  # not on boot
    assert r.public()[0]["last_run"] is None  # booting is not a run
    assert r.due(now=r._clock["x"] + 301) == ["x"]
    run = asyncio.create_task(r.run_once("x"))
    await started.wait()
    assert await r.run_once("x") == {"skipped": "still running"}
    release.set()
    assert (await run)["answer"] == "done"
    await r.stop()
