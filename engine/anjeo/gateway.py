"""The gateway: the only path from intent to action.

Everything - a tap in the app, a step the agent takes - goes through `call()`.
Policy decides; reads run; actions wait in the inbox; denials stop. Approving
an inbox item runs exactly the arguments that were shown to the owner, never a
re-generated version of them.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from anjeo.config import PolicyConfig
from anjeo.hub import Hub, ToolNotFound
from anjeo.policy import Decision, decide
from anjeo.store import Store

logger = logging.getLogger("anjeo.gateway")

Source = Literal["app", "agent"]


class ApprovalError(Exception):
    """The approval does not exist, was already decided, or has expired."""


class Gateway:
    def __init__(self, hub: Hub, store: Store, policy: PolicyConfig) -> None:
        self.hub = hub
        self.store = store
        self.policy = policy

    def decision(self, tool_name: str) -> Decision:
        return decide(self.hub.get(tool_name), self.policy)

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        source: Source,
        intent: str | None = None,
    ) -> dict[str, Any]:
        try:
            tool = self.hub.get(tool_name)
        except ToolNotFound:
            return {"status": "unknown_tool", "tool": tool_name}

        decision = decide(tool, self.policy)

        if decision.action == "deny":
            await self.store.log("call", "denied", {"reason": decision.reason, "source": source}, tool=tool_name)
            return {"status": "denied", "tool": tool_name, "reason": decision.reason}

        if decision.action == "approve":
            approval = await self.store.create_approval(
                tool=tool_name,
                server=tool.server,
                arguments=arguments,
                reason=decision.reason,
                source=source,
                intent=intent,
                ttl_hours=self.policy.approval_ttl_hours,
            )
            await self.store.log(
                "approval", "pending", {"approval_id": approval["id"], "source": source}, tool=tool_name
            )
            return {"status": "pending_approval", "tool": tool_name, "approval": approval}

        return await self._execute(tool_name, arguments, source=source)

    async def _execute(self, tool_name: str, arguments: dict[str, Any], *, source: str) -> dict[str, Any]:
        try:
            result = await self.hub.call(tool_name, arguments)
        except Exception as exc:  # boundary: a downstream fault becomes a recorded result, not a 500
            logger.exception("call %s failed", tool_name)
            await self.store.log("call", "failed", {"error": str(exc), "source": source}, tool=tool_name)
            return {"status": "failed", "tool": tool_name, "error": f"{type(exc).__name__}: {exc}"}
        status = "failed" if result["is_error"] else "executed"
        await self.store.log("call", status, {"source": source}, tool=tool_name)
        return {"status": status, "tool": tool_name, "result": result}

    # ── the inbox ──────────────────────────────────────────────────────────
    async def approve(self, approval_id: str) -> dict[str, Any]:
        if not await self.store.claim_approval(approval_id, "executing"):
            raise ApprovalError(approval_id)
        approval = await self.store.get_approval(approval_id)
        assert approval is not None
        outcome = await self._execute(approval["tool"], approval["arguments"], source=f"approval:{approval_id}")
        final = "executed" if outcome["status"] == "executed" else "failed"
        await self.store.finish_approval(approval_id, final, outcome)
        return {**outcome, "approval_id": approval_id}

    async def reject(self, approval_id: str) -> dict[str, Any]:
        if not await self.store.claim_approval(approval_id, "rejected"):
            raise ApprovalError(approval_id)
        await self.store.log("approval", "rejected", {"approval_id": approval_id})
        return {"status": "rejected", "approval_id": approval_id}
