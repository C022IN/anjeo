"""The gateway: the only path from intent to action.

Everything goes through `call()`: a tap in the app, a step the agent takes, a
routine's thought. Policy decides; reads run; actions wait in the inbox unless a
mandate covers them; denials stop. Approving an inbox item runs exactly the
arguments that were shown to the owner, never a re-generated version of them.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from anjeo.config import Mandate, PolicyConfig
from anjeo.hub import Hub, ToolNotFound
from anjeo.mandate import evaluate
from anjeo.policy import Decision, decide
from anjeo.store import Store

logger = logging.getLogger("anjeo.gateway")


class ApprovalError(Exception):
    """The approval does not exist, was already decided, or has expired."""


class Gateway:
    def __init__(self, hub: Hub, store: Store, policy: PolicyConfig, mandates: list[Mandate] | None = None) -> None:
        self.hub = hub
        self.store = store
        self.policy = policy
        self.mandates: dict[str, Mandate] = {m.tool: m for m in (mandates or []) if m.enabled}
        # One evaluation at a time per mandate, so two parallel calls can't both take the last daily slot.
        self._mandate_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def decision(self, tool_name: str) -> Decision:
        return decide(self.hub.get(tool_name), self.policy)

    def mandate_for(self, tool_name: str) -> Mandate | None:
        """The mandate that may act for this tool, or None. Owner-only and denied tools never have one."""
        mandate = self.mandates.get(tool_name)
        if mandate is None:
            return None
        tool = self.hub.get(tool_name)
        if tool.owner_only or decide(tool, self.policy).action != "approve":
            return None
        return mandate

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        source: str,
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

        if decision.action == "allow":
            return await self._execute(tool_name, arguments, source=source)

        mandate = self.mandate_for(tool_name)
        ttl_hours = self.policy.approval_ttl_hours
        if mandate is not None:
            async with self._mandate_locks[mandate.id]:
                violations = await evaluate(mandate, arguments, self.hub, self.store)
                if not violations:
                    return await self._execute(tool_name, arguments, source=source, mandate=mandate.id)
            decision = Decision("approve", f"outside mandate {mandate.id}: " + "; ".join(violations))
            if mandate.approval_ttl_minutes:
                ttl_hours = mandate.approval_ttl_minutes / 60

        approval = await self.store.create_approval(
            tool=tool_name,
            server=tool.server,
            arguments=arguments,
            reason=decision.reason,
            source=source,
            intent=intent,
            ttl_hours=ttl_hours,
        )
        await self.store.log(
            "approval",
            "pending",
            {"approval_id": approval["id"], "source": source, "reason": decision.reason},
            tool=tool_name,
        )
        return {"status": "pending_approval", "tool": tool_name, "approval": approval, "reason": decision.reason}

    async def _execute(
        self, tool_name: str, arguments: dict[str, Any], *, source: str, mandate: str | None = None
    ) -> dict[str, Any]:
        detail: dict[str, Any] = {"source": source}
        if mandate:
            detail["mandate"] = mandate
        try:
            result = await self.hub.call(tool_name, arguments)
        except Exception as exc:  # boundary: a downstream fault becomes a recorded result, not a 500
            logger.exception("call %s failed", tool_name)
            await self.store.log("call", "failed", {**detail, "error": str(exc)}, tool=tool_name)
            return {"status": "failed", "tool": tool_name, "error": f"{type(exc).__name__}: {exc}"}
        status = "failed" if result["is_error"] else "executed"
        await self.store.log("call", status, detail, tool=tool_name)
        out: dict[str, Any] = {"status": status, "tool": tool_name, "result": result}
        if mandate:
            out["mandate"] = mandate
        return out

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

    # ── mandates, for the app ──────────────────────────────────────────────
    async def mandates_public(self) -> list[dict[str, Any]]:
        out = []
        for m in self.mandates.values():
            try:
                active = self.mandate_for(m.tool) is not None
                inactive_reason = None if active else "tool is owner-only or not an approval tool"
            except ToolNotFound:
                active, inactive_reason = False, "tool not connected"
            out.append(
                {
                    **m.model_dump(),
                    "active": active,
                    "inactive_reason": inactive_reason,
                    "used_today": await self.store.mandate_executions_today(m.id),
                }
            )
        return out
