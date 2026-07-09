"""Central configuration for the Asterion multi-agent pipeline.

Reads from environment and the project-root `.env`. The Groq key in this
project is stored as `Asterion_Secret_key`, so we accept that name (and the
conventional `GROQ_API_KEY`) for the same setting.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent      # .../Asterion/backend
PROJECT_ROOT = BACKEND_DIR.parent                          # .../Asterion
WORKSPACE_DIR = BACKEND_DIR / "workspace"                  # where agents build code
UPLOADS_DIR = WORKSPACE_DIR / "_uploads"                   # staged attachment batches
# Agent skills (progressively-disclosed knowledge — see app/skills/). Repo root,
# not backend/, and named per the AgentSkills convention so skills written for
# other agent runtimes drop in unchanged. Distinct from `.claude/skills/`, which
# instructs Claude Code about *this repo*; these instruct *Asterion's own agents*.
SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"


def _dedupe(*keys: str) -> list[str]:
    """Non-empty keys, order preserved, duplicates dropped. A duplicated key in
    a pool is worse than useless: it makes the rotator retry a key that is
    already on cooldown."""
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Project-root .env holds the Groq key; a backend/.env may override.
        env_file=(str(PROJECT_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Groq LLM ----
    # Free/on_demand tier rate limits are per API key, and the caps are tight
    # enough (e.g. 8000 TPM on gpt-oss-120b, 100000 TPD on llama-3.3-70b) that
    # heavy same-day testing exhausts a single key. Three keys from separate
    # Groq accounts/projects are pooled and rotated on rate-limit errors so an
    # agent run doesn't stall just because one key's quota is used up.
    groq_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GROQ_API_KEY", "Asterion_Secret_key"),
    )
    groq_api_key1: str = Field(default="", validation_alias=AliasChoices("GROQ_API_KEY_1", "Asterion_Secret_key1"))
    groq_api_key2: str = Field(default="", validation_alias=AliasChoices("GROQ_API_KEY_2", "Asterion_Secret_key2"))
    groq_api_key3: str = Field(default="", validation_alias=AliasChoices("GROQ_API_KEY_3", "Asterion_Secret_key3"))

    # Strong reasoning model for the "thinking" agents. gpt-oss-120b has
    # materially more reliable native tool-calling on Groq than llama-3.3-70b,
    # which would intermittently emit unparseable inline `<function=...>` text
    # once an agent had more than a few tools available (observed directly
    # while building the Architect/Developer/Reviewer/Debugger loops).
    groq_model: str = Field(default="openai/gpt-oss-120b")
    # Fast/cheap model for lightweight steps (summaries, routing).
    groq_fast_model: str = Field(default="llama-3.1-8b-instant")

    @property
    def groq_api_keys(self) -> list[str]:
        return _dedupe(self.groq_api_key, self.groq_api_key1, self.groq_api_key2, self.groq_api_key3)

    # ---- DeepSeek LLM ----
    # Second provider, selectable per-install from Settings › Models (the
    # selection is a runtime override — see app/llm/selection.py). DeepSeek is
    # prepaid: an account at zero balance still authenticates and still lists
    # its models, but answers every completion with HTTP 402. app/llm/
    # deepseek_client.py maps that to ProviderUnavailable so the routing chain
    # escalates back to Groq instead of stalling.
    #
    # The project .env spells this `Deepseek_Key`; `DEEPSEEK_API_KEY` also works.
    deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "Deepseek_Key"),
    )
    deepseek_api_key1: str = Field(default="", validation_alias=AliasChoices("DEEPSEEK_API_KEY_1", "Deepseek_Key1"))
    deepseek_api_key2: str = Field(default="", validation_alias=AliasChoices("DEEPSEEK_API_KEY_2", "Deepseek_Key2"))

    @property
    def deepseek_api_keys(self) -> list[str]:
        return _dedupe(self.deepseek_api_key, self.deepseek_api_key1, self.deepseek_api_key2)

    # ---- Redis (task queue / memory / pub-sub) ----
    redis_url: str = Field(default="redis://127.0.0.1:6379/0")
    redis_namespace: str = Field(default="asterion")
    # If native Redis is unreachable, fall back to in-process fakeredis so the
    # pipeline still runs during development. Set to False to fail hard instead.
    allow_fakeredis: bool = Field(default=True)

    # ---- Server ----
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)
    cors_origins: str = Field(default="http://localhost:3000")

    # ---- Workspace ----
    workspace_dir: Path = Field(default=WORKSPACE_DIR)

    # ---- Skills ----
    skills_dir: Path = Field(default=SKILLS_DIR)

    # ---- Sub-agents (app/agents/subagent.py, app/tools/delegate.py) ----
    # How many delegated sub-agents may hold an LLM request at the same time.
    # More than 5 is counter-productive on Groq's free tier: the per-key limit
    # is 30 RPM and a sub-agent burns several requests per tool loop, so a wide
    # fan-out just converts into RateLimited -> model escalation -> worse
    # answers from the fallback tier. Spawning more than this is allowed; they
    # queue on a semaphore rather than being rejected.
    max_subagents: int = Field(default=5)
    # A sub-agent may not itself delegate beyond this depth. Without a hard cap,
    # one confused model that keeps calling `delegate` forks the key pool until
    # every key is on cooldown.
    max_subagent_depth: int = Field(default=2)
    # Tool-loop iterations a sub-agent gets before it is cut off. Lower than the
    # parent's: a sub-agent has one narrow job.
    subagent_max_iterations: int = Field(default=6)

    # ---- Context condensation (app/llm/condenser.py) ----
    # Summarize the middle of a transcript once it exceeds this many messages,
    # instead of truncating tool output and losing the information outright.
    condenser_max_messages: int = Field(default=40)
    condenser_keep_first: int = Field(default=3)   # system prompt + original task
    condenser_keep_last: int = Field(default=8)    # recent work, never summarized
    condenser_enabled: bool = Field(default=True)

    # ---- Attachments (uploaded PDFs / images / text the agents read) ----
    uploads_dir: Path = Field(default=UPLOADS_DIR)
    # Per-file and per-batch byte ceilings — big enough for a real doc/screenshot,
    # bounded so an upload can't blow up disk or the extraction step.
    max_attachment_bytes: int = Field(default=15 * 1024 * 1024)   # 15 MB/file
    max_attachment_batch_bytes: int = Field(default=40 * 1024 * 1024)
    max_attachments_per_batch: int = Field(default=6)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    # skills_dir is intentionally NOT created: an absent skills directory is a
    # valid state (the loader returns {}), and silently creating an empty one
    # would hide a mistyped path.
    return settings
