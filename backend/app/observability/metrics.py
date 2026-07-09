"""Execution metrics: token usage, latency, tool calls, cost estimates.

Every LLM completion and every tool dispatch in the system reports here.
Aggregates live in Redis hashes (per project + global) and a capped list of
recent raw calls per project supports the failure-analysis / timeline views.

Costs: Groq free tier bills $0; the table below carries Groq's posted paid
per-token prices so the "what would this cost" number is real if the owner
moves off the free tier. Unknown models cost 0 rather than guessing.
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any

from app.redis.client import get_redis, key

RECENT_CAP = 300

# $ per 1M tokens (input, output) — Groq posted on-demand pricing.
_PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.75),
    "openai/gpt-oss-20b": (0.10, 0.50),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "meta-llama/llama-4-scout-17b-16e-instruct": (0.11, 0.34),
    "groq/compound": (0.59, 0.79),  # billed as underlying models; approximation
}


def estimated_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = _PRICES.get(model, (0.0, 0.0))
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


def _metrics_key(pid: str | None) -> str:
    return key("metrics", pid) if pid else key("metrics", "global")


def _recent_key(pid: str) -> str:
    return key("llmcalls", pid)


def _tool_recent_key(pid: str) -> str:
    return key("toolcalls", pid)


async def record_llm_call(
    project_id: str | None,
    agent: str,
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    ok: bool = True,
    error: str = "",
) -> None:
    r = await get_redis()
    cost = estimated_cost_usd(model, prompt_tokens, completion_tokens)
    for k in {_metrics_key(project_id), _metrics_key(None)}:
        pipe = r.pipeline(transaction=False)
        pipe.hincrby(k, "llm_calls", 1)
        pipe.hincrby(k, "prompt_tokens", prompt_tokens)
        pipe.hincrby(k, "completion_tokens", completion_tokens)
        pipe.hincrby(k, "latency_ms_total", latency_ms)
        pipe.hincrbyfloat(k, "est_cost_usd", cost)
        if not ok:
            pipe.hincrby(k, "llm_errors", 1)
        pipe.hincrby(k, f"model:{model}:calls", 1)
        pipe.hincrby(k, f"model:{model}:tokens", prompt_tokens + completion_tokens)
        pipe.hincrby(k, f"agent:{agent}:calls", 1)
        pipe.hincrby(k, f"agent:{agent}:tokens", prompt_tokens + completion_tokens)
        await pipe.execute()
    if project_id:
        entry = {
            "ts": time.time(),
            "agent": agent,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
            "ok": ok,
            "error": error[:300],
        }
        await r.lpush(_recent_key(project_id), json.dumps(entry))
        await r.ltrim(_recent_key(project_id), 0, RECENT_CAP - 1)


async def record_tool_call(project_id: str, agent: str, tool: str, latency_ms: int, ok: bool, detail: str = "") -> None:
    r = await get_redis()
    for k in {_metrics_key(project_id), _metrics_key(None)}:
        pipe = r.pipeline(transaction=False)
        pipe.hincrby(k, "tool_calls", 1)
        if not ok:
            pipe.hincrby(k, "tool_errors", 1)
        pipe.hincrby(k, f"tool:{tool}:calls", 1)
        await pipe.execute()
    entry = {"ts": time.time(), "agent": agent, "tool": tool, "latency_ms": latency_ms, "ok": ok, "detail": detail[:300]}
    await r.lpush(_tool_recent_key(project_id), json.dumps(entry))
    await r.ltrim(_tool_recent_key(project_id), 0, RECENT_CAP - 1)


@asynccontextmanager
async def timed():
    """Measure a block's wall time: `async with timed() as t: ...; t()` → ms."""
    start = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - start) * 1000)

    yield elapsed_ms


def _decode_hash(data: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        ks = k.decode() if isinstance(k, bytes) else k
        vs = v.decode() if isinstance(v, bytes) else v
        try:
            out[ks] = float(vs) if "." in str(vs) else int(vs)
        except (TypeError, ValueError):
            out[ks] = vs
    return out


def _shape(agg: dict[str, Any]) -> dict[str, Any]:
    """Turn flat composite fields into nested per-model/per-agent/per-tool maps."""
    models: dict[str, dict[str, Any]] = {}
    agents: dict[str, dict[str, Any]] = {}
    tools: dict[str, dict[str, Any]] = {}
    top: dict[str, Any] = {}
    for field, value in agg.items():
        parts = field.split(":")
        if parts[0] == "model" and len(parts) == 3:
            models.setdefault(parts[1], {})[parts[2]] = value
        elif parts[0] == "agent" and len(parts) == 3:
            agents.setdefault(parts[1], {})[parts[2]] = value
        elif parts[0] == "tool" and len(parts) == 3:
            tools.setdefault(parts[1], {})[parts[2]] = value
        else:
            top[field] = value
    calls = int(top.get("llm_calls", 0) or 0)
    top["avg_latency_ms"] = int(top.get("latency_ms_total", 0) / calls) if calls else 0
    return {**top, "models": models, "agents": agents, "tools": tools}


async def get_project_metrics(pid: str, *, recent: int = 50) -> dict[str, Any]:
    r = await get_redis()
    agg = _shape(_decode_hash(await r.hgetall(_metrics_key(pid))))
    raw_llm = await r.lrange(_recent_key(pid), 0, recent - 1)
    raw_tools = await r.lrange(_tool_recent_key(pid), 0, recent - 1)
    agg["recent_llm_calls"] = [json.loads(x.decode() if isinstance(x, bytes) else x) for x in raw_llm]
    agg["recent_tool_calls"] = [json.loads(x.decode() if isinstance(x, bytes) else x) for x in raw_tools]
    return agg


async def get_global_metrics() -> dict[str, Any]:
    r = await get_redis()
    return _shape(_decode_hash(await r.hgetall(_metrics_key(None))))
