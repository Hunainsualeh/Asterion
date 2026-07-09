"""Phase 2 test: drive the whole pipeline through the HTTP API + verify SSE.

Requires the server running on :8000.
Run from backend/:  .venv/Scripts/python.exe -m scripts.test_api
"""
from __future__ import annotations

import asyncio
import json

import httpx

BASE = "http://127.0.0.1:8000"


async def wait_settled(client: httpx.AsyncClient, pid: str, timeout: float = 30.0) -> dict:
    for _ in range(int(timeout / 0.2)):
        r = (await client.get(f"/api/projects/{pid}")).json()
        settled = not r["running"] and (
            r.get("pending_gate") or r["status"] in ("complete", "error")
        )
        if settled:
            return r
        await asyncio.sleep(0.2)
    raise TimeoutError(f"project {pid} did not settle")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        health = (await client.get("/health")).json()
        print("health:", health)

        pid = (await client.post("/api/projects", json={"idea": "A todo app for teams."})).json()["project_id"]
        print("started:", pid)

        responses = [
            ("approve", {"action": "reject", "feedback": "Add users + metric."}),
            ("approve", {"action": "approve"}),
            ("approve", {"action": "approve"}),
            ("approve", {"action": "approve"}),
            ("test", {"result": "fail", "feedback": "500 on /health"}),
            ("test", {"result": "pass"}),
            ("test", {"result": "pass"}),
        ]

        for endpoint, body in responses:
            detail = await wait_settled(client, pid)
            gate = detail.get("pending_gate")
            print(f"  gate={gate:22} -> POST /{endpoint} {body}")
            resp = await client.post(f"/api/projects/{pid}/{endpoint}", json=body)
            assert resp.status_code == 200, f"{endpoint} failed: {resp.status_code} {resp.text}"

        # Wait for completion.
        for _ in range(150):
            d = (await client.get(f"/api/projects/{pid}")).json()
            if d["status"] == "complete":
                break
            await asyncio.sleep(0.2)
        d = (await client.get(f"/api/projects/{pid}")).json()
        print("final status:", d["status"])

        tickets = (await client.get(f"/api/projects/{pid}/tickets")).json()
        print("tickets:", [t["id"] for t in tickets["tickets"]])

        # Sample the SSE stream (replays history).
        seen = 0
        async with client.stream("GET", f"/api/projects/{pid}/events") as s:
            async for line in s.aiter_lines():
                if line.startswith("data:"):
                    ev = json.loads(line[5:].strip())
                    if seen < 4:
                        print(f"  SSE {ev['kind']:14} {ev['agent']:9} {ev['message']}")
                    seen += 1
                    if seen >= 8:
                        break
        print(f"SSE events received: >= {seen}")

        assert d["status"] == "complete"
        print("\nPhase 2 PASSED: API drives the full pipeline and SSE streams events.\n")


if __name__ == "__main__":
    asyncio.run(main())
