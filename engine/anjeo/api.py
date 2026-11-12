"""HTTP API the Anjeo app talks to.

GET  /health                         liveness, no auth
GET  /servers                        connected MCP servers and their state
POST /servers/refresh                re-discover tools (all, or ?server=id)
GET  /tools                          every tool, with the policy Anjeo applies to it
POST /tools/call                     run a tool through the gateway
POST /ask                            one sentence -> Claude plans across all servers
GET  /approvals?status=pending       the inbox
POST /approvals/{id}/approve|reject  decide
GET  /activity                       the ledger
GET/PUT/DELETE /memory[/{key}]       what the digital self knows about you
GET  /mandates                       standing permissions and today's usage
GET  /routines                       Anjeo's scheduled thinking
POST /routines/{id}/run              think now
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from anjeo import __version__
from anjeo.agent import Agent
from anjeo.config import Settings, load_settings
from anjeo.gateway import ApprovalError, Gateway
from anjeo.hub import Connector, Hub, default_connector
from anjeo.routines import Routines
from anjeo.store import Store

logger = logging.getLogger("anjeo.api")


class CallBody(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AskBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


class MemoryBody(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


def create_app(
    settings: Settings | None = None,
    *,
    connector: Connector = default_connector,
    agent: Agent | None = None,
    start_routines: bool = True,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.database)
        await store.open()
        hub = Hub(settings.servers, connector=connector)
        await hub.refresh()
        gateway = Gateway(hub, store, settings.policy, settings.mandates)
        app.state.gateway = gateway
        app.state.agent = agent or Agent(gateway, settings.agent)
        if agent is not None:
            agent.gateway = gateway
        routines = Routines(app.state.agent, settings.routines)
        app.state.routines = routines
        if start_routines:
            routines.start()
        for m in settings.mandates:
            if m.enabled and m.tool in {t.name for t in hub.tools()} and gateway.mandate_for(m.tool) is None:
                logger.warning("mandate %s cannot act: %s is owner-only or not an approval tool", m.id, m.tool)
        if not settings.api_token:
            logger.warning("ANJEO_TOKEN is not set: the API is open to anyone who can reach it. Local use only.")
        try:
            yield
        finally:
            await routines.stop()
            await store.close()

    app = FastAPI(title="Anjeo", version=__version__, lifespan=lifespan)
    # Expo web runs on another origin in development; auth is the bearer token, not cookies.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def require_token(request: Request) -> None:
        if not settings.api_token:
            return
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), settings.api_token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong bearer token")

    def gw(request: Request) -> Gateway:
        return request.app.state.gateway

    auth = [Depends(require_token)]

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/servers", dependencies=auth)
    async def servers(g: Gateway = Depends(gw)) -> list[dict[str, Any]]:
        return [s.public() for s in g.hub.servers.values()]

    @app.post("/servers/refresh", dependencies=auth)
    async def refresh(server: str | None = None, g: Gateway = Depends(gw)) -> list[dict[str, Any]]:
        if server and server not in g.hub.servers:
            raise HTTPException(404, f"no server {server!r}")
        await g.hub.refresh(server)
        return [s.public() for s in g.hub.servers.values()]

    @app.get("/tools", dependencies=auth)
    async def tools(g: Gateway = Depends(gw)) -> list[dict[str, Any]]:
        out = []
        for t in sorted(g.hub.tools(), key=lambda t: t.name):
            d = g.decision(t.name)
            entry = {**t.public(), "policy": d.action, "policy_reason": d.reason}
            if m := g.mandate_for(t.name):
                entry["policy"], entry["policy_reason"] = "mandate", f"runs inside mandate {m.id}, else asks you"
            out.append(entry)
        return out

    @app.post("/tools/call", dependencies=auth)
    async def call(body: CallBody, g: Gateway = Depends(gw)) -> dict[str, Any]:
        outcome = await g.call(body.tool, body.arguments, source="app")
        if outcome["status"] == "unknown_tool":
            raise HTTPException(404, f"no tool {body.tool!r}; refresh servers?")
        return outcome

    @app.post("/ask", dependencies=auth)
    async def ask(body: AskBody, request: Request) -> dict[str, Any]:
        return await request.app.state.agent.ask(body.prompt)

    @app.get("/approvals", dependencies=auth)
    async def approvals(status: str | None = "pending", limit: int = 50, g: Gateway = Depends(gw)):
        return await g.store.list_approvals(status=status or None, limit=min(limit, 200))

    @app.post("/approvals/{approval_id}/approve", dependencies=auth)
    async def approve(approval_id: str, g: Gateway = Depends(gw)) -> dict[str, Any]:
        try:
            return await g.approve(approval_id)
        except ApprovalError:
            raise HTTPException(409, "approval is not pending (already decided, expired, or unknown)") from None

    @app.post("/approvals/{approval_id}/reject", dependencies=auth)
    async def reject(approval_id: str, g: Gateway = Depends(gw)) -> dict[str, Any]:
        try:
            return await g.reject(approval_id)
        except ApprovalError:
            raise HTTPException(409, "approval is not pending (already decided, expired, or unknown)") from None

    @app.get("/activity", dependencies=auth)
    async def activity(limit: int = 100, g: Gateway = Depends(gw)):
        return await g.store.activity(limit=min(limit, 500))

    @app.get("/memory", dependencies=auth)
    async def memory(g: Gateway = Depends(gw)):
        return await g.store.memory()

    @app.put("/memory/{key}", dependencies=auth)
    async def remember(key: str, body: MemoryBody, g: Gateway = Depends(gw)) -> dict[str, Any]:
        if not (0 < len(key) <= 80):
            raise HTTPException(422, "key must be 1-80 characters")
        await g.store.remember(key, body.value)
        await g.store.log("memory", "set", {"key": key})
        return {"key": key, "value": body.value}

    @app.delete("/memory/{key}", dependencies=auth)
    async def forget(key: str, g: Gateway = Depends(gw)) -> dict[str, Any]:
        if not await g.store.forget(key):
            raise HTTPException(404, f"no memory {key!r}")
        await g.store.log("memory", "deleted", {"key": key})
        return {"deleted": key}

    @app.get("/mandates", dependencies=auth)
    async def mandates(g: Gateway = Depends(gw)):
        return await g.mandates_public()

    @app.get("/routines", dependencies=auth)
    async def routines_list(request: Request):
        return request.app.state.routines.public()

    @app.post("/routines/{routine_id}/run", dependencies=auth)
    async def routine_run(routine_id: str, request: Request) -> dict[str, Any]:
        routines: Routines = request.app.state.routines
        if routine_id not in routines.routines:
            raise HTTPException(404, f"no routine {routine_id!r}")
        return await routines.run_once(routine_id)

    return app
