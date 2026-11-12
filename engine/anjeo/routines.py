"""Routines: Anjeo thinking on a schedule.

A routine is a standing prompt ("check the gold setup and act within the
trading mandate") that runs every N minutes through the same agent and the same
gateway as anything you ask by hand. Its reads run; its actions run only inside
a mandate; everything else lands in your Inbox. Each run is recorded in the
activity log with the answer, so you can see what Anjeo thought and why.

Runs never overlap: a routine that is still thinking when its next tick comes
skips that tick instead of stacking.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from anjeo.agent import Agent
from anjeo.config import Routine

logger = logging.getLogger("anjeo.routines")


class Routines:
    def __init__(self, agent: Agent, routines: list[Routine]) -> None:
        self.agent = agent
        self.routines = {r.id: r for r in routines}
        self.last_run: dict[str, float] = {}  # when a run actually started (shown to the owner)
        self._clock: dict[str, float] = {}  # what the schedule counts from: last run, or engine start
        self.last_result: dict[str, dict[str, Any]] = {}
        self._running: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._runs: set[asyncio.Task[Any]] = set()  # hold references so in-flight runs aren't collected

    async def run_once(self, routine_id: str) -> dict[str, Any]:
        routine = self.routines[routine_id]
        if routine_id in self._running:
            return {"skipped": "still running"}
        self._running.add(routine_id)
        self.last_run[routine_id] = self._clock[routine_id] = time.time()
        try:
            result = await self.agent.ask(routine.prompt, source=f"routine:{routine.id}")
        except Exception as exc:  # boundary: one bad run must not kill the scheduler
            logger.exception("routine %s failed", routine_id)
            result = {"error": f"{type(exc).__name__}: {exc}"}
            await self.agent.gateway.store.log("routine", "failed", {"routine": routine_id, **result})
        finally:
            self._running.discard(routine_id)
        self.last_result[routine_id] = result
        return result

    def due(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        return [
            r.id
            for r in self.routines.values()
            if r.enabled and r.id not in self._running and now - self._clock.get(r.id, 0.0) >= r.every_minutes * 60
        ]

    async def _loop(self, tick_seconds: float) -> None:
        while True:
            for routine_id in self.due():
                task = asyncio.create_task(self.run_once(routine_id))
                self._runs.add(task)
                task.add_done_callback(self._runs.discard)
            await asyncio.sleep(tick_seconds)

    def start(self, tick_seconds: float = 30.0) -> None:
        if any(r.enabled for r in self.routines.values()) and self._task is None:
            # First run waits one full interval: starting the engine is not a reason to trade.
            now = time.time()
            for r in self.routines.values():
                self._clock.setdefault(r.id, now)
            self._task = asyncio.create_task(self._loop(tick_seconds))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for task in list(self._runs):
            task.cancel()

    def public(self) -> list[dict[str, Any]]:
        return [
            {
                **r.model_dump(),
                "running": r.id in self._running,
                "last_run": self.last_run.get(r.id),
                "last_answer": (self.last_result.get(r.id) or {}).get("answer"),
            }
            for r in self.routines.values()
        ]
