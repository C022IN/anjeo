"""Mandates: let Anjeo act alone, but only inside limits you wrote down.

A mandate names one tool and the bounds on its arguments, an optional daily
count, and live checks (read a number from a read tool, require it in range).
`evaluate()` returns every violation. No violations means the call may run
without asking; any violation sends it to the Inbox with the reasons.

Mandates fail closed. A missing argument the limit needs, a number that won't
parse, a check tool that errors or returns something unreadable: each is a
violation, never a pass.
"""

from __future__ import annotations

import json
import math
from typing import Any

from anjeo.config import ArgLimit, Mandate, MandateCheck
from anjeo.hub import Hub
from anjeo.store import Store


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _check_arg(name: str, limit: ArgLimit, arguments: dict[str, Any]) -> list[str]:
    if name not in arguments or arguments[name] in (None, ""):
        return [f"{name} is required"] if limit.required or limit.min is not None or limit.max is not None else []
    value = arguments[name]
    problems: list[str] = []
    if limit.min is not None or limit.max is not None:
        n = _number(value)
        if n is None:
            return [f"{name}={value!r} is not a number"]
        if limit.min is not None and n < limit.min:
            problems.append(f"{name} {n:g} < {limit.min:g}")
        if limit.max is not None and n > limit.max:
            problems.append(f"{name} {n:g} > {limit.max:g}")
    if limit.one_of is not None:
        allowed = {str(x).upper() for x in limit.one_of}
        if str(value).upper() not in allowed:
            problems.append(f"{name}={value} not in {sorted(allowed)}")
    return problems


def _dig(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def read_path(result: dict[str, Any], path: str) -> Any:
    """Find `path` in a tool result: structured content first, then a JSON text block."""
    structured = result.get("structured")
    if isinstance(structured, dict):
        found = _dig(structured, path)
        # FastMCP-style servers wrap plain returns as {"result": ...}.
        if found is None and "result" in structured:
            found = _dig(structured["result"], path)
        if found is not None:
            return found
    for block in result.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            found = _dig(json.loads(block["text"]), path)
        except (ValueError, TypeError):
            continue
        if found is not None:
            return found
    return None


async def _run_check(check: MandateCheck, hub: Hub) -> list[str]:
    label = check.label or f"{check.tool}:{check.path}"
    try:
        result = await hub.call(check.tool, check.arguments)
    except Exception as exc:  # boundary: an unreachable check is a failed check
        return [f"could not verify {label} ({type(exc).__name__})"]
    if result.get("is_error"):
        return [f"could not verify {label} (tool error)"]
    n = _number(read_path(result, check.path))
    if n is None:
        return [f"could not verify {label} (no number at {check.path!r})"]
    problems = []
    if check.min is not None and n < check.min:
        problems.append(f"{label} {n:g} < {check.min:g}")
    if check.max is not None and n > check.max:
        problems.append(f"{label} {n:g} > {check.max:g}")
    return problems


async def evaluate(mandate: Mandate, arguments: dict[str, Any], hub: Hub, store: Store) -> list[str]:
    violations: list[str] = []
    for name, limit in mandate.args.items():
        violations += _check_arg(name, limit, arguments)
    if mandate.max_per_day is not None:
        used = await store.mandate_executions_today(mandate.id)
        if used >= mandate.max_per_day:
            violations.append(f"daily limit reached ({used}/{mandate.max_per_day})")
    # Cheap argument checks first; only reach out to live checks if the call could otherwise pass.
    if not violations:
        for check in mandate.checks:
            violations += await _run_check(check, hub)
    return violations
