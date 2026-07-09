"""Project registry rename/delete."""
from __future__ import annotations

from app.services import project_store as store


async def test_rename_and_delete_roundtrip():
    pid = "proj-test-crud"
    await store.create_project(pid, "original idea")
    assert (await store.get_project(pid)) is not None

    await store.rename_project(pid, "  New Name  ")
    proj = await store.get_project(pid)
    assert proj is not None and proj["title"] == "New Name"
    assert pid in [p["project_id"] for p in await store.list_projects()]

    await store.delete_project(pid)
    assert await store.get_project(pid) is None
    assert pid not in [p["project_id"] for p in await store.list_projects()]
