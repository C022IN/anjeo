"""Configuration: one TOML file describes every MCP server Anjeo routes to.

Secrets never live in the file. A server's `headers` may reference environment
variables as `${NAME}`; they are expanded at connect time, and a missing variable
is an error rather than an empty header.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Action = Literal["allow", "approve", "deny"]

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")
_SERVER_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class ServerConfig(BaseModel):
    id: str
    name: str = ""
    # Exactly one transport: a Streamable HTTP `url`, or a local `command`.
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        # The id becomes a tool-name prefix the model sees, so keep it short and safe.
        if not _SERVER_ID.match(v):
            raise ValueError("server id must match ^[a-z][a-z0-9_]{0,23}$")
        return v

    @model_validator(mode="after")
    def _one_transport(self) -> ServerConfig:
        if bool(self.url) == bool(self.command):
            raise ValueError(f"server {self.id!r}: set exactly one of `url` or `command`")
        if not self.name:
            self.name = self.id
        return self

    def resolved_headers(self) -> dict[str, str]:
        return {k: _expand(v) for k, v in self.headers.items()}

    def resolved_env(self) -> dict[str, str]:
        return {k: _expand(v) for k, v in self.env.items()}


class PolicyRule(BaseModel):
    match: str  # fnmatch pattern over the namespaced tool name, e.g. "linkedin__*_list_*"
    action: Action


class PolicyConfig(BaseModel):
    # What happens to a tool nothing else classifies. "approve" fails safe.
    default: Action = "approve"
    rules: list[PolicyRule] = Field(default_factory=list)
    approval_ttl_hours: float = 24.0


class AgentConfig(BaseModel):
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_steps: int = 12
    owner_name: str = "the owner"


class Settings(BaseModel):
    servers: list[ServerConfig] = Field(default_factory=list)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    database: str = "anjeo.db"
    # Bearer token the app must present. Read from ANJEO_TOKEN, never the file.
    api_token: str | None = None

    @field_validator("servers")
    @classmethod
    def _unique_ids(cls, v: list[ServerConfig]) -> list[ServerConfig]:
        ids = [s.id for s in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate server ids: {sorted(dupes)}")
        return v


def _expand(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in os.environ:
            raise KeyError(f"environment variable {name} is referenced in anjeo.toml but not set")
        return os.environ[name]

    return _ENV_REF.sub(repl, value)


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.environ.get("ANJEO_CONFIG", "anjeo.toml"))
    data: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    settings = Settings.model_validate(data)
    settings.api_token = os.environ.get("ANJEO_TOKEN") or None
    if db := os.environ.get("ANJEO_DATABASE"):
        settings.database = db
    return settings
