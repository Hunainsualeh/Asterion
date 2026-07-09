"""FastAPI entrypoint for the Asterion multi-agent pipeline."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    approvals,
    artifacts,
    chat,
    control,
    models,
    notifications,
    projects,
    sandbox,
    stream,
    tasks,
    tickets,
    uploads,
)
from app.config import get_settings
from app.redis.client import backend_kind, close_redis, get_redis

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("asterion")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await get_redis()  # warm the connection / trigger fallback early
    from app.orchestration.graph import get_graph
    from app.tasks import store as task_store

    get_graph()
    await task_store.init()  # create tasks.db / schema

    # Rehydrate the user's model choice before the first request: routing reads
    # it synchronously, so an unloaded override would silently route the first
    # few calls at the YAML default.
    from app.llm import selection

    override = await selection.load()

    # Background scheduler: fires reminders, sweeps missed tasks, expands
    # recurrences. Restart-safe — it rehydrates its due-queue from SQLite.
    from app.tasks import scheduler

    scheduler.start()
    log.info(
        "Asterion up. Redis backend=%s, model=%s",
        backend_kind(),
        override or f"{settings.groq_model} (per-agent routing)",
    )
    yield
    await scheduler.stop()
    from app.llm import deepseek_client

    await deepseek_client.aclose()
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Asterion Multi-Agent Pipeline", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(tickets.router, prefix="/api")
    app.include_router(stream.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(sandbox.router, prefix="/api")
    app.include_router(uploads.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.include_router(control.router, prefix="/api")
    app.include_router(models.router, prefix="/api")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "redis": backend_kind()}

    return app


app = create_app()
