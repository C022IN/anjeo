"""The hub: one namespace over every downstream MCP server.

Each server's tools are listed once, prefixed with the server id
(`linkedin__linkedin_create_post`), and cached until `refresh()`. Calls open a
short-lived session per call. That costs a handshake, but it means a server
that restarts, redeploys or drops a session never leaves the hub holding a
dead connection - the right trade for a personal control plane where a call is
a human-scale event, not a hot loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, StdioServerParameters, types
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from anjeo.config import ServerConfig

logger = logging.getLogger("anjeo.hub")

SEP = "__"
_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")
# Claude and most MCP clients cap tool names at 64 characters.
MAX_TOOL_NAME = 64

Connector = Callable[[ServerConfig], AbstractAsyncContextManager[Client]]


@dataclass
class ToolInfo:
    name: str  # namespaced, model-safe
    server: str
    original: str  # the name the downstream server knows
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "server": self.server,
            "original": self.original,
            "description": self.description,
            "input_schema": self.input_schema,
            "annotations": self.annotations,
        }


@dataclass
class ServerState:
    config: ServerConfig
    status: str = "unknown"  # unknown | online | offline | disabled
    error: str | None = None
    tools: list[ToolInfo] = field(default_factory=list)
    checked_at: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.config.id,
            "name": self.config.name,
            "transport": "http" if self.config.url else "stdio",
            "status": self.status,
            "error": self.error,
            "tool_count": len(self.tools),
            "checked_at": self.checked_at,
        }


class ToolNotFound(KeyError):
    pass


def namespaced(server_id: str, tool: str) -> str:
    name = f"{server_id}{SEP}{_UNSAFE.sub('_', tool)}"
    return name[:MAX_TOOL_NAME]


def default_connector(cfg: ServerConfig) -> AbstractAsyncContextManager[Client]:
    if cfg.url:
        http = create_mcp_http_client(headers=cfg.resolved_headers())
        return Client(streamable_http_client(cfg.url, http_client=http))
    assert cfg.command is not None
    params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.resolved_env() or None)
    return Client(params)


def result_to_json(result: types.CallToolResult) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            content.append({"type": "text", "text": block.text})
        else:
            content.append(block.model_dump(mode="json", exclude_none=True))
    return {
        "is_error": bool(result.is_error),
        "content": content,
        "structured": result.structured_content,
    }


class Hub:
    def __init__(self, servers: list[ServerConfig], connector: Connector = default_connector) -> None:
        self._connector = connector
        self.servers: dict[str, ServerState] = {s.id: ServerState(config=s) for s in servers}
        self._index: dict[str, ToolInfo] = {}
        self._lock = asyncio.Lock()

    # ── discovery ──────────────────────────────────────────────────────────
    async def refresh(self, server_id: str | None = None) -> None:
        async with self._lock:
            targets = [self.servers[server_id]] if server_id else list(self.servers.values())
            await asyncio.gather(*(self._refresh_one(s) for s in targets))
            self._index = {t.name: t for s in self.servers.values() for t in s.tools}

    async def _refresh_one(self, state: ServerState) -> None:
        state.checked_at = time.time()
        if not state.config.enabled:
            state.status, state.tools, state.error = "disabled", [], None
            return
        try:
            async with self._connector(state.config) as client:
                tools: list[types.Tool] = []
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    cursor = page.next_cursor
                    if not cursor:
                        break
            infos: dict[str, ToolInfo] = {}
            for t in tools:
                info = self._to_info(state.config.id, t)
                if info.name in infos:  # two names collapsed to one after sanitising/truncating
                    logger.warning("server %s: tool %r collides as %s; skipped", state.config.id, t.name, info.name)
                    continue
                infos[info.name] = info
            state.tools = list(infos.values())
            state.status, state.error = "online", None
        except Exception as exc:  # boundary: one bad server must not take the hub down
            logger.warning("server %s unreachable: %s", state.config.id, exc)
            state.status, state.error, state.tools = "offline", f"{type(exc).__name__}: {exc}", []

    @staticmethod
    def _to_info(server_id: str, tool: types.Tool) -> ToolInfo:
        ann = tool.annotations.model_dump(exclude_none=True) if tool.annotations else {}
        return ToolInfo(
            name=namespaced(server_id, tool.name),
            server=server_id,
            original=tool.name,
            description=tool.description or tool.title or "",
            input_schema=tool.input_schema or {"type": "object", "properties": {}},
            annotations=ann,
        )

    # ── lookup & execution ─────────────────────────────────────────────────
    def tools(self) -> list[ToolInfo]:
        return list(self._index.values())

    def get(self, name: str) -> ToolInfo:
        try:
            return self._index[name]
        except KeyError:
            raise ToolNotFound(name) from None

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        cfg = self.servers[tool.server].config
        async with self._connector(cfg) as client:
            result = await client.call_tool(tool.original, arguments)
        return result_to_json(result)
