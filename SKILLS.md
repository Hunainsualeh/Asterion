# Agent skills

Asterion ships four **Claude Code skills** in `.claude/skills/`. Each is a
`SKILL.md` with YAML frontmatter (`name`, `description`); Claude reads the
descriptions, decides which one applies, and loads the body on demand. They are
progressive disclosure for a codebase whose hard parts are not visible from its
file names.

| Skill | Use it when |
|---|---|
| [`run-asterion`](.claude/skills/run-asterion/SKILL.md) | Starting, restarting, or smoke-testing the stack; verifying a change in the running app rather than by reading code. |
| [`llm-provider`](.claude/skills/llm-provider/SKILL.md) | Touching `backend/app/llm/`, `litellm_config.yaml`, or Settings › Models: adding a provider or model, changing routing or fallback chains, or debugging why a run used an unexpected model. |
| [`agent-tool`](.claude/skills/agent-tool/SKILL.md) | Adding or debugging a tool an agent can call — anything under `backend/app/tools/`, the per-agent allowlist, or terminal tools. |
| [`debug-run`](.claude/skills/debug-run/SKILL.md) | Triaging a failed, stuck, or wrong-looking run: a stalled gate, a looping agent, a project that 404s, a confusing chat error. |

Project-wide conventions that apply to *every* task live in
[`CLAUDE.md`](CLAUDE.md) — read that first. `frontend/AGENTS.md` adds a rule for
frontend work specifically.

## What makes these worth loading

They exist to carry knowledge that is expensive to rediscover and invisible in
the code:

- Running `uvicorn --reload` silently kills every agent run, because the
  reloader watches the directory the agents write into.
- `groq/compound` is a Groq model whose id starts with `groq/`, and
  `deepseek-r1-distill-llama-70b` is a DeepSeek model hosted by Groq. Any
  provider router that splits on the first slash is wrong.
- DeepSeek is prepaid: a zero-balance key authenticates, lists its models, then
  returns HTTP 402 on every completion.
- A tool in a module that `app/tools/__init__.py` doesn't import simply does not
  exist, and nothing says so.
- Two tests fail on a clean tree. A third failure is yours.

A skill that only restates what the file tree already shows costs context and
earns nothing. If you add one, make it carry a fact that cost someone an hour.

## Adding a skill

```
.claude/skills/<name>/SKILL.md
```

```markdown
---
name: <kebab-case-name>
description: <when to load this — written for the model choosing between skills, not for a human browsing a list>
---

# <Title>
...
```

The `description` is the whole retrieval mechanism: it is the only text Claude
sees when deciding whether to open the skill. Name the symptoms and the file
paths that should trigger it. Then add a row to the table above.
