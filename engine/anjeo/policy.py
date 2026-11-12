"""Policy: decide, per tool, whether Anjeo may act alone.

  allow    - run immediately (reads, lookups)
  approve  - park the call until the owner approves it from the app
  deny     - never run

Order of precedence, first match wins:
  0. the server marks the tool owner-only        -> approve  (a deny rule still wins)
  1. an explicit rule in anjeo.toml (fnmatch on the namespaced name)
  2. the server says destructive_hint            -> approve
  3. the tool's name contains an action verb     -> approve  (send_, post_, delete_ ...)
  4. the server says read_only_hint              -> allow
  5. the tool's name is a lookup verb            -> allow    (get_, list_, search_ ...)
  6. the configured default ("approve" unless you change it)

A server's annotations can always make a tool *stricter*, but a read_only_hint
cannot lift a tool whose own name says it acts (`send_email` with a wrong hint
still waits for you). A rule in your own file wins over everything except an
owner-only mark, which a rule can tighten to deny but never loosen.

Mandates (see mandate.py) sit on top: they can turn an "approve" into an
execution when a call is inside limits you wrote. Never an owner-only tool.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from anjeo.config import Action, PolicyConfig
from anjeo.hub import ToolInfo

_READ_VERB = re.compile(
    r"(^|_)(get|list|read|search|find|fetch|lookup|describe|status|diagnose|query|count|preview|whoami)(_|$)"
)
_WRITE_VERB = re.compile(
    r"(^|_)(send|post|create|publish|delete|remove|update|reply|invite|comment|react|upload|write|"
    r"schedule|cancel|transfer|pay|buy|sell|trade|order|approve|submit|set|put|patch|move|archive|"
    r"close|kill|arm|disarm|pause|resume|modify|execute|withdraw|deposit)(_|$)"
)


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def decide(tool: ToolInfo, policy: PolicyConfig) -> Decision:
    for rule in policy.rules:
        if fnmatch.fnmatchcase(tool.name, rule.match):
            if tool.owner_only and rule.action != "deny":
                return Decision("approve", "owner only (a rule cannot loosen it)")
            return Decision(rule.action, f"rule {rule.match!r}")

    if tool.owner_only:
        return Decision("approve", "owner only")

    ann = tool.annotations
    name = tool.original.lower()
    if ann.get("destructive_hint"):
        return Decision("approve", "server marks it destructive")
    # A write verb anywhere outranks a read verb: `get_and_delete_x` is a write.
    if _WRITE_VERB.search(name):
        return Decision("approve", "name reads as an action")
    if ann.get("read_only_hint"):
        return Decision("allow", "server marks it read-only")
    if _READ_VERB.search(name):
        return Decision("allow", "name reads as a lookup")
    return Decision(policy.default, "default policy")
