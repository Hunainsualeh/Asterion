"""Discover and parse `SKILL.md` files.

Layout (the AgentSkills convention, so skills written for other agent runtimes
drop in unchanged):

    .agents/skills/<skill-name>/SKILL.md     preferred — can carry sibling files
    .agents/skills/<skill-name>.md           legacy flat form

Frontmatter is YAML between `---` fences. `name` and `description` are the only
required fields, exactly as in the AgentSkills standard:

    ---
    name: python-testing
    description: How this repo writes and runs pytest suites. Use when adding
      or fixing tests, or when a test fails.
    triggers: [pytest, test, testing, unit test]
    agents: [developer, test, debugger]
    ---

    <the body — the part that costs tokens, loaded only when triggered>

Loading mode is *inferred* rather than declared, because every way of declaring
it invites the two failure modes we care about: a skill that quietly never
fires, and a skill that quietly costs tokens on every call.

    triggers present         -> KEYWORD
    paths present            -> PATH
    always: true             -> ALWAYS   (explicit opt-in; it is the costly one)
    neither                  -> ON_DEMAND
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import SKILLS_DIR
from app.skills.models import ALL_AGENTS, Loading, Skill, _normalize

log = logging.getLogger("asterion.skills")

_FENCE = "---"
# A body this long defeats the point of progressive disclosure — it will not
# fit alongside a transcript inside a 6-8K-token request on the small models.
_BODY_WARN_CHARS = 12_000


class SkillParseError(ValueError):
    pass


def _split_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.lstrip().splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            meta = yaml.safe_load(raw) or {}
            if not isinstance(meta, dict):
                raise SkillParseError("frontmatter must be a YAML mapping")
            return meta, body
    raise SkillParseError("unterminated frontmatter: no closing '---'")


def _as_tuple(value) -> tuple[str, ...]:
    """Accept `triggers: api` and `triggers: [api, rest]` alike — a scalar is
    the single most common way to write a one-item list by hand."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def parse_skill(text: str, *, source: Path | None = None, fallback_name: str = "") -> Skill:
    meta, body = _split_frontmatter(text)

    name = str(meta.get("name") or fallback_name).strip()
    if not name:
        raise SkillParseError("skill needs a `name` (or a filename to derive one from)")

    description = str(meta.get("description") or "").strip()
    if not description:
        raise SkillParseError(f"skill '{name}' needs a `description` — it is the only thing the model sees when deciding whether to load the skill")
    description = " ".join(description.split())  # YAML block scalars carry newlines

    triggers = tuple(_normalize(t) for t in _as_tuple(meta.get("triggers")) if t.strip())
    paths = _as_tuple(meta.get("paths"))
    agents = _as_tuple(meta.get("agents")) or (ALL_AGENTS,)

    if meta.get("always"):
        loading = Loading.ALWAYS
    elif triggers:
        loading = Loading.KEYWORD
    elif paths:
        loading = Loading.PATH
    else:
        loading = Loading.ON_DEMAND

    body = body.strip()
    if not body:
        raise SkillParseError(f"skill '{name}' has an empty body")
    if loading is Loading.ALWAYS and len(body) > _BODY_WARN_CHARS:
        log.warning(
            "skill '%s' is always-on and %d chars — it will be re-sent on every "
            "LLM call and may 413 the smaller models. Give it triggers instead.",
            name, len(body),
        )

    return Skill(
        name=name,
        description=description,
        body=body,
        loading=loading,
        triggers=triggers,
        paths=paths,
        agents=agents,
        source=source,
    )


def _discover(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files = sorted(root.glob("*/SKILL.md"))
    # Legacy flat skills, minus anything that is really a directory's README.
    files += sorted(p for p in root.glob("*.md") if p.name != "README.md")
    return files


@lru_cache(maxsize=8)
def _load_cached(root_str: str, fingerprint: tuple) -> dict[str, Skill]:
    """Cached per (dir, fingerprint-of-mtimes): editing a SKILL.md takes effect
    on the next call without a process restart, matching how
    `app/llm/routing.py` treats litellm_config.yaml."""
    root = Path(root_str)
    skills: dict[str, Skill] = {}
    for path in _discover(root):
        fallback = path.parent.name if path.name == "SKILL.md" else path.stem
        try:
            skill = parse_skill(path.read_text(encoding="utf-8"), source=path, fallback_name=fallback)
        except (SkillParseError, yaml.YAMLError) as exc:
            # One broken skill must not take down every agent in the process.
            log.error("Skipping skill %s: %s", path, exc)
            continue
        if skill.name in skills:
            log.warning("Duplicate skill '%s' at %s — keeping the first", skill.name, path)
            continue
        skills[skill.name] = skill

    if skills:
        by_mode: dict[str, int] = {}
        for s in skills.values():
            by_mode[s.loading.value] = by_mode.get(s.loading.value, 0) + 1
        log.info("Skills loaded: %d from %s (%s)", len(skills), root, by_mode)
    else:
        log.info("No skills found in %s", root)
    return skills


def _fingerprint(root: Path) -> tuple:
    try:
        return tuple(sorted((str(p), p.stat().st_mtime) for p in _discover(root)))
    except OSError:
        return ()


def load_skills(root: Path | None = None) -> dict[str, Skill]:
    """Every skill on disk, keyed by name. Cheap to call: cached on mtimes."""
    root = root or SKILLS_DIR
    return _load_cached(str(root), _fingerprint(root))
