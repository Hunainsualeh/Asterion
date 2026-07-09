"""Phase 0 smoke test: verify config, Redis (or fallback), and Groq connectivity.

Run:  backend/.venv/Scripts/python.exe -m scripts.smoke_test    (from backend/)
"""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")


async def main() -> None:
    from app.config import get_settings
    from app.redis.client import get_redis, backend_kind
    from app.llm.groq_client import health_check

    settings = get_settings()
    print("\n--- CONFIG ---")
    print(f"Groq key present : {bool(settings.groq_api_key)}")
    print(f"Groq model       : {settings.groq_model}")
    print(f"Redis URL        : {settings.redis_url}")
    print(f"Workspace        : {settings.workspace_dir}")

    print("\n--- REDIS ---")
    r = await get_redis()
    await r.set(key_test := "asterion:smoke:test", b"hello")
    val = await r.get(key_test)
    await r.delete(key_test)
    print(f"Backend          : {backend_kind()}")
    print(f"Round-trip       : {val!r}")

    print("\n--- GROQ ---")
    try:
        info = await health_check()
        print(f"Model            : {info['model']}")
        print(f"Reply            : {info['reply']!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"Groq call FAILED : {exc}")

    print("\nPhase 0 smoke test complete.\n")


if __name__ == "__main__":
    asyncio.run(main())
