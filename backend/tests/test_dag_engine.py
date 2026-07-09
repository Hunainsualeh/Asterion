"""DAG engine: scheduling, parallelism, retries, timeouts, cancellation,
cycle prevention, dynamic expansion, and failure recovery."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.dag.engine import (
    DagRun,
    DagSpec,
    DagValidationError,
    Expansion,
    ExecutionContext,
    NodeSpec,
    RetryPolicy,
)


async def _noop_publish(*args, **kwargs):
    return None


def _run_of(nodes, executors, query="q", **kw) -> DagRun:
    return DagRun("proj-test", DagSpec(nodes), query, executors, _noop_publish, **kw)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_cycle_detected():
    with pytest.raises(DagValidationError, match="cycle"):
        DagSpec([
            NodeSpec(id="a", agent="x", deps=["b"]),
            NodeSpec(id="b", agent="x", deps=["a"]),
        ])


def test_self_dependency_rejected():
    with pytest.raises(DagValidationError, match="itself"):
        DagSpec([NodeSpec(id="a", agent="x", deps=["a"])])


def test_unknown_dependency_rejected():
    with pytest.raises(DagValidationError, match="unknown"):
        DagSpec([NodeSpec(id="a", agent="x", deps=["ghost"])])


def test_duplicate_id_rejected():
    with pytest.raises(DagValidationError, match="duplicate"):
        DagSpec([NodeSpec(id="a", agent="x"), NodeSpec(id="a", agent="x")])


def test_topo_order_respects_deps():
    spec = DagSpec([
        NodeSpec(id="summarize", agent="x", deps=["research", "analyze"]),
        NodeSpec(id="research", agent="x", deps=["plan"]),
        NodeSpec(id="analyze", agent="x", deps=["plan"]),
        NodeSpec(id="plan", agent="x"),
    ])
    order = spec.topo_order
    assert order.index("plan") < order.index("research")
    assert order.index("plan") < order.index("analyze")
    assert order.index("research") < order.index("summarize")
    assert order.index("analyze") < order.index("summarize")


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_parallel_execution_of_independent_nodes():
    """research and analyze overlap in time; summarize waits for both."""
    windows: dict[str, tuple[float, float]] = {}

    def worker(delay: float):
        async def _exec(ctx: ExecutionContext):
            start = time.monotonic()
            await asyncio.sleep(delay)
            windows[ctx.node.id] = (start, time.monotonic())
            return f"{ctx.node.id}-out"
        return _exec

    run = _run_of(
        [
            NodeSpec(id="plan", agent="w"),
            NodeSpec(id="research", agent="w", deps=["plan"]),
            NodeSpec(id="analyze", agent="w", deps=["plan"]),
            NodeSpec(id="summarize", agent="w", deps=["research", "analyze"], is_final=True),
        ],
        {"w": worker(0.15)},
    )
    await run.execute()

    assert run.status == "succeeded"
    r0, r1 = windows["research"]
    a0, a1 = windows["analyze"]
    assert r0 < a1 and a0 < r1, "research/analyze should overlap (parallel)"
    s0, _ = windows["summarize"]
    assert s0 >= max(r1, a1) - 0.01, "summarize must wait for both branches"
    assert run.final_output == "summarize-out"


@pytest.mark.asyncio
async def test_outputs_flow_downstream():
    async def produce(ctx):
        return {"n": 21}

    async def consume(ctx):
        return ctx.dep_outputs()["a"]["n"] * 2

    run = _run_of(
        [NodeSpec(id="a", agent="p"), NodeSpec(id="b", agent="c", deps=["a"], is_final=True)],
        {"p": produce, "c": consume},
    )
    await run.execute()
    assert run.final_output == 42


@pytest.mark.asyncio
async def test_retry_then_success():
    calls = {"n": 0}

    async def flaky(ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    run = _run_of(
        [NodeSpec(id="a", agent="f", retry=RetryPolicy(max_attempts=3, backoff_base_s=0.01), is_final=True)],
        {"f": flaky},
    )
    await run.execute()
    assert run.status == "succeeded"
    assert run.nodes["a"].attempts == 3


@pytest.mark.asyncio
async def test_timeout_fails_node():
    async def slow(ctx):
        await asyncio.sleep(5)

    run = _run_of(
        [NodeSpec(id="a", agent="s", timeout_s=0.05, retry=RetryPolicy(max_attempts=1))],
        {"s": slow},
    )
    await run.execute()
    assert run.nodes["a"].status == "failed"
    assert "TimeoutError" in run.nodes["a"].error


@pytest.mark.asyncio
async def test_failure_skips_dependents_and_run_fails():
    async def bad(ctx):
        raise ValueError("nope")

    async def good(ctx):
        return "fine"

    run = _run_of(
        [
            NodeSpec(id="a", agent="bad", retry=RetryPolicy(max_attempts=1)),
            NodeSpec(id="b", agent="good", deps=["a"], is_final=True),
        ],
        {"bad": bad, "good": good},
    )
    await run.execute()
    assert run.nodes["a"].status == "failed"
    assert run.nodes["b"].status == "skipped"
    assert run.status == "failed"
    assert run.final_output is None


@pytest.mark.asyncio
async def test_allow_failed_deps_degrades_gracefully():
    async def bad(ctx):
        raise ValueError("nope")

    async def good(ctx):
        return "research-out"

    async def summarize(ctx):
        got = ctx.dep_outputs()
        return f"summary from {sorted(got)}"

    run = _run_of(
        [
            NodeSpec(id="research", agent="good"),
            NodeSpec(id="analyze", agent="bad", retry=RetryPolicy(max_attempts=1)),
            NodeSpec(id="summarize", agent="sum", deps=["research", "analyze"],
                     allow_failed_deps=True, is_final=True),
        ],
        {"good": good, "bad": bad, "sum": summarize},
    )
    await run.execute()
    assert run.nodes["summarize"].status == "succeeded"
    assert run.status == "succeeded"  # degraded but delivered
    assert run.final_output == "summary from ['research']"


@pytest.mark.asyncio
async def test_cancellation_stops_pending_and_running():
    started = asyncio.Event()

    async def slow(ctx):
        started.set()
        await asyncio.sleep(10)

    run = _run_of(
        [NodeSpec(id="a", agent="s"), NodeSpec(id="b", agent="s", deps=["a"])],
        {"s": slow},
    )
    task = asyncio.create_task(run.execute())
    await asyncio.wait_for(started.wait(), timeout=2)
    run.cancel()
    await asyncio.wait_for(task, timeout=2)
    assert run.status == "cancelled"
    assert run.nodes["a"].status == "cancelled"
    assert run.nodes["b"].status == "cancelled"


@pytest.mark.asyncio
async def test_dynamic_expansion_from_planner():
    async def planner(ctx):
        return Expansion(
            nodes=[
                NodeSpec(id="s1", agent="w"),
                NodeSpec(id="s2", agent="w"),
                NodeSpec(id="join", agent="joiner", deps=["s1", "s2"], is_final=True),
            ],
            result="plan",
        )

    async def worker(ctx):
        return f"{ctx.node.id}-done"

    async def joiner(ctx):
        return ",".join(sorted(str(v) for v in ctx.dep_outputs().values()))

    run = _run_of([NodeSpec(id="plan", agent="planner")], {"planner": planner, "w": worker, "joiner": joiner})
    await run.execute()
    assert run.status == "succeeded"
    assert set(run.nodes) == {"plan", "s1", "s2", "join"}
    assert run.nodes["plan"].output == "plan"
    assert run.final_output == "s1-done,s2-done"


@pytest.mark.asyncio
async def test_expansion_with_cycle_is_rejected_not_crashed():
    async def planner(ctx):
        return Expansion(nodes=[
            NodeSpec(id="x", agent="w", deps=["y"]),
            NodeSpec(id="y", agent="w", deps=["x"]),
        ])

    async def worker(ctx):
        return "?"

    run = _run_of(
        [NodeSpec(id="plan", agent="planner", retry=RetryPolicy(max_attempts=1))],
        {"planner": planner, "w": worker},
    )
    await run.execute()
    assert run.nodes["plan"].status == "failed"
    assert "cycle" in run.nodes["plan"].error or "expansion" in run.nodes["plan"].error


@pytest.mark.asyncio
async def test_unknown_executor_fails_cleanly():
    run = _run_of([NodeSpec(id="a", agent="ghost")], {})
    await run.execute()
    assert run.nodes["a"].status == "failed"
    assert "no executor" in run.nodes["a"].error
