"""Async Redis checkpointer for LangGraph.

Mirrors the storage model of the reference `InMemorySaver` (checkpoints,
per-channel blobs, and pending writes) but persists everything to Redis so the
pipeline can pause at a human gate and resume later — even across restarts when
a native Redis is used. Works over the in-process fakeredis fallback too.

Only the async methods are implemented, because the whole backend drives the
graph via `ainvoke`/`astream`/`aget_state`. The sync methods intentionally
raise, so a stray blocking call is caught loudly during development rather than
silently blocking the event loop.

Redis layout (all keys namespaced, e.g. `asterion:...`):
  cp:{thread}:{ns}:{cid}     HASH  cp_type, cp_data, meta_type, meta_data, parent
  cpids:{thread}:{ns}        SET   checkpoint ids in this (thread, ns)
  blobs:{thread}:{ns}        HASH  field "{channel}\x1f{version}" -> packed blob
  writes:{thread}:{ns}:{cid} HASH  field "{task_id}\x1f{idx}"     -> packed write
  ns:{thread}                SET   checkpoint namespaces seen for this thread
  threads                    SET   all thread ids
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

from app.config import get_settings
from app.redis.client import get_redis

SEP = b"\x1f"  # unit separator; never appears in channel/type/task strings


def _prefix() -> str:
    return get_settings().redis_namespace


def _pack_typed(typed: tuple[str, bytes]) -> bytes:
    """Pack a (type, data) serde tuple into a single bytes blob."""
    type_str, data = typed
    return type_str.encode() + SEP + data


def _unpack_typed(blob: bytes) -> tuple[str, bytes]:
    type_bytes, _, data = blob.partition(SEP)
    return type_bytes.decode(), data


def _pack_write(task_id: str, channel: str, typed: tuple[str, bytes], task_path: str) -> bytes:
    type_str, data = typed
    head = SEP.join([task_id.encode(), channel.encode(), type_str.encode(), task_path.encode()])
    return head + SEP + data


def _unpack_write(blob: bytes) -> tuple[str, str, tuple[str, bytes], str]:
    task_id, channel, type_str, task_path, data = blob.split(SEP, 4)
    return (
        task_id.decode(),
        channel.decode(),
        (type_str.decode(), data),
        task_path.decode(),
    )


def _parent_config(thread_id: str, checkpoint_ns: str, parent_id: str | None) -> RunnableConfig | None:
    if not parent_id:
        return None
    return {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": parent_id,
        }
    }


class RedisSaver(BaseCheckpointSaver[int]):
    """LangGraph checkpointer backed by async Redis (native or fakeredis)."""

    # ---- key builders ----
    def _cp_key(self, t: str, ns: str, cid: str) -> str:
        return f"{_prefix()}:cp:{t}:{ns}:{cid}"

    def _cpids_key(self, t: str, ns: str) -> str:
        return f"{_prefix()}:cpids:{t}:{ns}"

    def _blobs_key(self, t: str, ns: str) -> str:
        return f"{_prefix()}:blobs:{t}:{ns}"

    def _writes_key(self, t: str, ns: str, cid: str) -> str:
        return f"{_prefix()}:writes:{t}:{ns}:{cid}"

    def _ns_key(self, t: str) -> str:
        return f"{_prefix()}:ns:{t}"

    def _threads_key(self) -> str:
        return f"{_prefix()}:threads"

    # ---- blob helpers ----
    async def _aload_blobs(self, t: str, ns: str, versions: ChannelVersions) -> dict[str, Any]:
        if not versions:
            return {}
        r = await get_redis()
        blobs_key = self._blobs_key(t, ns)
        fields = [f"{ch}\x1f{ver}" for ch, ver in versions.items()]
        raw = await r.hmget(blobs_key, fields)
        result: dict[str, Any] = {}
        for ch, blob in zip(versions.keys(), raw):
            if blob is None:
                continue
            typed = _unpack_typed(blob)
            if typed[0] == "empty":
                continue
            result[ch] = self.serde.loads_typed(typed)
        return result

    async def _aload_writes(self, t: str, ns: str, cid: str) -> list[tuple[str, str, Any]]:
        r = await get_redis()
        raw = await r.hgetall(self._writes_key(t, ns, cid))
        writes: list[tuple[str, str, Any]] = []
        for blob in raw.values():
            task_id, channel, typed, _ = _unpack_write(blob)
            writes.append((task_id, channel, self.serde.loads_typed(typed)))
        return writes

    async def _atuple_for(self, t: str, ns: str, cid: str) -> CheckpointTuple | None:
        r = await get_redis()
        data = await r.hgetall(self._cp_key(t, ns, cid))
        if not data:
            return None
        cp_typed = (data[b"cp_type"].decode(), data[b"cp_data"])
        meta_typed = (data[b"meta_type"].decode(), data[b"meta_data"])
        parent = data.get(b"parent") or b""
        parent_id = parent.decode() or None

        checkpoint: Checkpoint = self.serde.loads_typed(cp_typed)
        checkpoint = {
            **checkpoint,
            "channel_values": await self._aload_blobs(t, ns, checkpoint["channel_versions"]),
        }
        return CheckpointTuple(
            config={"configurable": {"thread_id": t, "checkpoint_ns": ns, "checkpoint_id": cid}},
            checkpoint=checkpoint,
            metadata=self.serde.loads_typed(meta_typed),
            parent_config=_parent_config(t, ns, parent_id),
            pending_writes=await self._aload_writes(t, ns, cid),
        )

    # ---- async API used by the graph ----
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        t = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        cid = get_checkpoint_id(config)
        if cid is None:
            r = await get_redis()
            ids = await r.smembers(self._cpids_key(t, ns))
            if not ids:
                return None
            cid = max(x.decode() for x in ids)  # uuid6 ids sort chronologically
        return await self._atuple_for(t, ns, cid)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        r = await get_redis()
        if config is not None:
            thread_ids = [config["configurable"]["thread_id"]]
        else:
            thread_ids = [x.decode() for x in await r.smembers(self._threads_key())]
        cfg_ns = config["configurable"].get("checkpoint_ns") if config else None
        cfg_id = get_checkpoint_id(config) if config else None
        before_id = get_checkpoint_id(before) if before else None
        remaining = limit

        for t in thread_ids:
            namespaces = [x.decode() for x in await r.smembers(self._ns_key(t))]
            for ns in namespaces:
                if cfg_ns is not None and ns != cfg_ns:
                    continue
                ids = sorted((x.decode() for x in await r.smembers(self._cpids_key(t, ns))), reverse=True)
                for cid in ids:
                    if cfg_id and cid != cfg_id:
                        continue
                    if before_id and cid >= before_id:
                        continue
                    tup = await self._atuple_for(t, ns, cid)
                    if tup is None:
                        continue
                    if filter and not all(tup.metadata.get(k) == v for k, v in filter.items()):
                        continue
                    if remaining is not None:
                        if remaining <= 0:
                            return
                        remaining -= 1
                    yield tup

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        t = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        cid = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id") or ""

        c = checkpoint.copy()
        values: dict[str, Any] = c.pop("channel_values")  # type: ignore[misc]

        r = await get_redis()
        pipe = r.pipeline(transaction=False)

        if new_versions:
            blob_mapping: dict[str, bytes] = {}
            for ch, ver in new_versions.items():
                typed = self.serde.dumps_typed(values[ch]) if ch in values else ("empty", b"")
                blob_mapping[f"{ch}\x1f{ver}"] = _pack_typed(typed)
            pipe.hset(self._blobs_key(t, ns), mapping=blob_mapping)

        cp_typed = self.serde.dumps_typed(c)
        meta_typed = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        pipe.hset(
            self._cp_key(t, ns, cid),
            mapping={
                "cp_type": cp_typed[0].encode(),
                "cp_data": cp_typed[1],
                "meta_type": meta_typed[0].encode(),
                "meta_data": meta_typed[1],
                "parent": parent_id.encode(),
            },
        )
        pipe.sadd(self._cpids_key(t, ns), cid)
        pipe.sadd(self._ns_key(t), ns)
        pipe.sadd(self._threads_key(), t)
        await pipe.execute()

        return {"configurable": {"thread_id": t, "checkpoint_ns": ns, "checkpoint_id": cid}}

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        t = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        cid = config["configurable"]["checkpoint_id"]
        wkey = self._writes_key(t, ns, cid)

        r = await get_redis()
        mapping: dict[str, bytes] = {}
        for idx, (channel, value) in enumerate(writes):
            widx = WRITES_IDX_MAP.get(channel, idx)
            field = f"{task_id}\x1f{widx}"
            # Regular writes (idx >= 0) are idempotent: don't overwrite.
            if widx >= 0 and await r.hexists(wkey, field):
                continue
            typed = self.serde.dumps_typed(value)
            mapping[field] = _pack_write(task_id, channel, typed, task_path)
        if mapping:
            await r.hset(wkey, mapping=mapping)

    async def adelete_thread(self, thread_id: str) -> None:
        r = await get_redis()
        namespaces = [x.decode() for x in await r.smembers(self._ns_key(thread_id))]
        keys: list[str] = [self._ns_key(thread_id)]
        for ns in namespaces:
            cpids_key = self._cpids_key(thread_id, ns)
            ids = [x.decode() for x in await r.smembers(cpids_key)]
            keys.append(cpids_key)
            keys.append(self._blobs_key(thread_id, ns))
            for cid in ids:
                keys.append(self._cp_key(thread_id, ns, cid))
                keys.append(self._writes_key(thread_id, ns, cid))
        if keys:
            await r.delete(*keys)
        await r.srem(self._threads_key(), thread_id)

    # ---- sync methods deliberately disabled (async-only backend) ----
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:  # noqa: D102
        raise NotImplementedError("RedisSaver is async-only; use aget_tuple / aget_state.")

    def put(self, *args: Any, **kwargs: Any) -> RunnableConfig:  # noqa: D102
        raise NotImplementedError("RedisSaver is async-only; use aput / ainvoke.")

    def put_writes(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        raise NotImplementedError("RedisSaver is async-only; use aput_writes.")

    def list(self, *args: Any, **kwargs: Any):  # noqa: D102
        raise NotImplementedError("RedisSaver is async-only; use alist.")


_saver: RedisSaver | None = None


def get_checkpointer() -> RedisSaver:
    global _saver
    if _saver is None:
        _saver = RedisSaver()
    return _saver
