"""Sandboxed command execution for a project's workspace.

Docker is off the table on this box (owner constraint), so isolation is
process-level, enforced in layers:

  * cwd pinned inside `workspace/<pid>/repo` — same containment root the
    agents' fs tools enforce.
  * scrubbed environment — the child sees PATH/system basics only, never the
    backend's secrets (.env Groq keys etc. are NOT inherited).
  * a command deny-list for the obviously destructive/system-level classes
    (formatting disks, killing the machine, registry edits, privilege tools).
  * hard wall-clock timeout per session; background sessions get a longer
    leash but still auto-kill. Entire *process trees* are killed (taskkill /T
    on Windows) so an npm dev-server's children don't outlive it.
  * bounded output: per-line cap, total line cap, Redis stream cap.
  * bounded concurrency per project.

Every output line is appended to a per-project Redis stream, so the UI tails
logs live over SSE, and the full transcript survives for later inspection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.redis.client import get_redis, key
from app.tools.registry import ToolContext

log = logging.getLogger("asterion.sandbox")

MAX_LINE_CHARS = 2000
MAX_LINES = 3000
MAX_CONCURRENT_PER_PROJECT = 3
BACKGROUND_MAX_S = 30 * 60
STREAM_MAXLEN = 5000

_DENY_PATTERNS = [
    r"\bformat\b", r"\bshutdown\b", r"\brestart-computer\b", r"\breg(\.exe)?\s+(add|delete)\b",
    r"\bbcdedit\b", r"\bdiskpart\b", r"\bcipher\s+/w\b", r"\bvssadmin\b",
    r"\brm\s+(-\w*\s+)*/\b", r"\bdel\s+/[sq]\s+[a-z]:\\\\", r"\brd\s+/s\b.*[a-z]:\\\\",
    r"\bnet\s+user\b", r"\brunas\b", r"\bschtasks\b", r"\btakeown\b", r"\bicacls\b",
    r":\(\)\s*\{\s*:\|:\s*&\s*\}", r"\bmkfs\b", r"\bdd\s+if=",
]
_DENY_RE = re.compile("|".join(_DENY_PATTERNS), re.IGNORECASE)

# Dev servers (Next, Vite, CRA, http.server, uvicorn…) announce a local URL on
# startup — capture it so the UI can offer a live preview of the running app
# instead of just a static-HTML file. 0.0.0.0 isn't browsable; normalize to
# localhost.
_SERVE_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])(?::(\d+))?", re.IGNORECASE
)


def _detect_serve_url(line: str) -> str | None:
    m = _SERVE_URL_RE.search(line)
    if not m:
        return None
    port = m.group(1)
    return f"http://localhost:{port}" if port else "http://localhost"

# Env vars the child process is allowed to inherit. Everything else —
# including every *_KEY / secret loaded for the backend — is withheld.
_ENV_ALLOW = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "HOMEDRIVE", "HOMEPATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "LANG", "PYTHONIOENCODING",
)


class SandboxDenied(RuntimeError):
    pass


def _stream_key(pid: str) -> str:
    return key("sandbox", pid)


def _sessions_key(pid: str) -> str:
    return key("sandbox_sessions", pid)


@dataclass
class Session:
    id: str
    project_id: str
    command: str
    background: bool
    timeout_s: int
    status: str = "running"          # running | exited | killed | timeout | error
    returncode: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    lines: int = 0
    url: str | None = None           # dev-server URL detected from output, if any
    proc: subprocess.Popen | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "background": self.background,
            "status": self.status,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "lines": self.lines,
            "url": self.url,
        }


_sessions: dict[str, dict[str, Session]] = {}  # pid -> sid -> session


def _scrubbed_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOW}
    # Force child Python processes to flush stdout/stderr immediately. Otherwise
    # they block-buffer when writing to a pipe (not a TTY), which hides a dev
    # server's "serving at http://localhost:PORT" banner — and all its logs —
    # until the process exits.
    env["PYTHONUNBUFFERED"] = "1"
    return env


async def _emit(pid: str, session: Session, stream: str, line: str) -> None:
    r = await get_redis()
    entry = {
        "session": session.id,
        "stream": stream,          # stdout | stderr | system
        "line": line[:MAX_LINE_CHARS],
        "ts": time.time(),
    }
    await r.xadd(_stream_key(pid), {"e": json.dumps(entry)}, maxlen=STREAM_MAXLEN, approximate=True)


async def _save_session(session: Session) -> None:
    r = await get_redis()
    await r.hset(_sessions_key(session.project_id), session.id, json.dumps(session.snapshot()))


async def list_sessions(pid: str) -> list[dict[str, Any]]:
    r = await get_redis()
    raw = await r.hgetall(_sessions_key(pid))
    out = []
    for v in raw.values():
        out.append(json.loads(v.decode() if isinstance(v, bytes) else v))
    # live objects override stale snapshots
    for s in _sessions.get(pid, {}).values():
        out = [x for x in out if x["id"] != s.id] + [s.snapshot()]
    out.sort(key=lambda s: s["started_at"], reverse=True)
    return out


async def read_log(pid: str, last_id: str = "0-0", block_ms: int = 15000, count: int = 200):
    r = await get_redis()
    result = await r.xread({_stream_key(pid): last_id}, count=count, block=block_ms)
    out: list[tuple[str, dict[str, Any]]] = []
    if result:
        for _stream, entries in result:
            for entry_id, fields in entries:
                raw = fields.get(b"e") or fields.get("e")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                out.append((eid, json.loads(raw)))
    return out


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and all its children."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # taskkill /T takes the whole tree down (dev servers spawn children).
        os.system(f"taskkill /F /T /PID {proc.pid} >nul 2>&1")
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def start(pid: str, command: str, *, timeout_s: int = 120, background: bool = False) -> Session:
    command = command.strip()
    if not command:
        raise SandboxDenied("empty command")
    if _DENY_RE.search(command):
        raise SandboxDenied("command blocked by sandbox policy (destructive/system-level operation)")

    live = _sessions.setdefault(pid, {})
    running = [s for s in live.values() if s.status == "running"]
    if len(running) >= MAX_CONCURRENT_PER_PROJECT:
        raise SandboxDenied(f"limit of {MAX_CONCURRENT_PER_PROJECT} concurrent sandbox sessions reached")

    repo_dir = ToolContext(project_id=pid, agent="sandbox").repo_dir
    timeout_s = min(timeout_s, BACKGROUND_MAX_S if background else 1800)

    session = Session(
        id=f"sbx-{uuid.uuid4().hex[:8]}",
        project_id=pid,
        command=command,
        background=background,
        timeout_s=timeout_s,
    )
    live[session.id] = session

    # Synchronous Popen driven by threads — NOT asyncio.create_subprocess_*.
    # Under `--reload`/`--workers` uvicorn runs the server on a Windows
    # SelectorEventLoop, which raises NotImplementedError for asyncio
    # subprocesses. Popen + reader threads work on any event loop, so the
    # sandbox no longer depends on how the server was launched.
    loop = asyncio.get_running_loop()
    try:
        proc = subprocess.Popen(  # noqa: S602 (shell is the point of a sandbox)
            command,
            shell=True,
            cwd=str(repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=_scrubbed_env(),
            bufsize=0,
        )
    except OSError as exc:
        session.status = "error"
        session.finished_at = time.time()
        await _emit(pid, session, "system", f"[failed to start: {exc}]")
        await _save_session(session)
        raise SandboxDenied(f"could not start command: {exc}") from exc

    session.proc = proc
    await _emit(pid, session, "system", f"$ {command}")
    await _save_session(session)

    # Each pipe is drained by its own daemon thread (blocking readline); lines
    # are handed back to the event loop to touch Redis/shared state safely.
    threading.Thread(target=_reader_thread, args=(session, proc.stdout, "stdout", loop), daemon=True).start()
    threading.Thread(target=_reader_thread, args=(session, proc.stderr, "stderr", loop), daemon=True).start()
    asyncio.create_task(_supervise(session))
    return session


def _reader_thread(session: Session, reader: Any, stream: str, loop: asyncio.AbstractEventLoop) -> None:
    """Blocking pipe drain, in a dedicated thread. Hands each line to the loop
    and waits on it for light backpressure so we never outrun Redis."""
    try:
        for raw in iter(reader.readline, b""):
            text = raw.decode(errors="replace").rstrip("\r\n")
            try:
                fut = asyncio.run_coroutine_threadsafe(_on_line(session, stream, text), loop)
                fut.result(timeout=15)
            except Exception:  # noqa: BLE001 — loop gone / redis blip: keep draining
                pass
    except (ValueError, OSError):
        pass
    finally:
        try:
            reader.close()
        except Exception:  # noqa: BLE001
            pass


async def _on_line(session: Session, stream: str, text: str) -> None:
    session.lines += 1
    if session.lines > MAX_LINES:
        if session.lines == MAX_LINES + 1:
            await _emit(session.project_id, session, "system",
                        f"[output cap of {MAX_LINES} lines reached — suppressing further output]")
        return
    await _emit(session.project_id, session, stream, text)
    # First time a local server URL appears, surface it for live preview.
    if session.url is None:
        url = _detect_serve_url(text)
        if url:
            session.url = url
            await _emit(session.project_id, session, "system", f"[app is serving at {url} — open the Preview]")
            await _save_session(session)


async def _supervise(session: Session) -> None:
    proc = session.proc
    assert proc is not None
    deadline = time.time() + session.timeout_s
    timed_out = False
    # Poll for exit (works on any loop; subprocess I/O is on the reader threads).
    while proc.poll() is None:
        if time.time() >= deadline:
            timed_out = True
            _kill_tree(proc)
            await asyncio.sleep(0.2)
            break
        await asyncio.sleep(0.3)
    if proc.poll() is None:
        _kill_tree(proc)

    session.returncode = proc.poll()
    # Don't clobber a status a concurrent kill() already set.
    if session.status == "running":
        session.status = "timeout" if timed_out else "exited"
    session.finished_at = time.time()
    with_timeout = f" (killed after {session.timeout_s}s timeout)" if timed_out else ""
    await _emit(session.project_id, session, "system",
                f"[process finished: {session.status}, exit code {session.returncode}{with_timeout}]")
    await _save_session(session)


async def kill(pid: str, sid: str) -> bool:
    session = _sessions.get(pid, {}).get(sid)
    if session is None or session.proc is None or session.status != "running":
        return False
    _kill_tree(session.proc)
    session.status = "killed"
    session.finished_at = time.time()
    await _emit(pid, session, "system", "[killed by user]")
    await _save_session(session)
    return True
