"""Per-agent tool registry.

Every tool an agent can call is registered here with a JSON schema (for Groq
function-calling) and the set of agent names allowed to use it. Agents get
their tool list via `groq_tools_for(agent)` and every call is routed through
`dispatch()`, which re-checks the allowlist so a tool-calling loop can never
reach outside its agent's declared capabilities.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import get_settings


@dataclass
class ToolContext:
    """Everything a tool handler needs about the current call site."""

    project_id: str
    agent: str
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace_dir(self) -> Path:
        """This project's sandboxed workspace root (created on first use)."""
        d = get_settings().workspace_dir / self.project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def repo_dir(self) -> Path:
        """Where the generated project's local git repo lives."""
        d = self.workspace_dir / "repo"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def docs_dir(self) -> Path:
        d = self.workspace_dir / "docs"
        d.mkdir(parents=True, exist_ok=True)
        return d


Handler = Callable[..., Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    agents: frozenset[str]

    def groq_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_TOOLS: dict[str, ToolSpec] = {}


def register(
    name: str,
    description: str,
    parameters: dict[str, Any],
    agents: list[str] | tuple[str, ...] | frozenset[str],
) -> Callable[[Handler], Handler]:
    """Decorator: register an async handler as a tool for the given agents."""

    def _wrap(fn: Handler) -> Handler:
        if name in _TOOLS:
            raise ValueError(f"tool '{name}' already registered")
        _TOOLS[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=fn,
            agents=frozenset(agents),
        )
        return fn

    return _wrap


def groq_tools_for(agent: str) -> list[dict[str, Any]]:
    return [spec.groq_schema() for spec in _TOOLS.values() if agent in spec.agents]


def tool_names_for(agent: str) -> list[str]:
    return [name for name, spec in _TOOLS.items() if agent in spec.agents]


class ToolAccessError(RuntimeError):
    pass


class ToolNotFoundError(RuntimeError):
    pass


async def dispatch(ctx: ToolContext, name: str, args: dict[str, Any]) -> Any:
    """Look up `name`, enforce the caller agent's allowlist, then call it."""
    spec = _TOOLS.get(name)
    if spec is None:
        raise ToolNotFoundError(f"no such tool: {name}")
    if ctx.agent not in spec.agents:
        raise ToolAccessError(f"agent '{ctx.agent}' is not allowed to call '{name}'")
    sig = inspect.signature(spec.handler)
    kwargs = {k: v for k, v in args.items() if k in sig.parameters}
    return await spec.handler(ctx, **kwargs)


def all_tools() -> dict[str, ToolSpec]:
    return dict(_TOOLS)
