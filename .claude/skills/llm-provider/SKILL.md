---
name: llm-provider
description: Add or change an LLM provider, model, or routing rule in Asterion — new provider client, new model in the picker, per-agent model assignment, fallback chains, or debugging why a run used a different model than expected. Use when touching anything under backend/app/llm/, litellm_config.yaml, or Settings › Models.
---

# Working on Asterion's LLM layer

The layer exists to answer one question — *which model answers this call, and
what happens when it can't?* — in exactly one place. Almost every bug here comes
from someone answering it a second time somewhere else.

## The shape

```
app/llm/
  client.py          chat_completion() — THE entry point. Dispatches on model id.
  catalog.py         Provider enum, ModelInfo, resolve(model_id) -> (provider, native_id)
  errors.py          the provider-neutral exception taxonomy
  groq_client.py     Groq SDK + key-pool rotation + error translation
  deepseek_client.py httpx + response shim + error translation + model discovery
  routing.py         agent -> model chain, from litellm_config.yaml + the override
  selection.py       the user's Settings › Models choice (Redis-persisted)
```

Callers do exactly this, and nothing else:

```python
from app.llm.client import chat_completion
resp = await chat_completion(messages, tools=tools, model="deepseek/deepseek-v4-pro")
```

## Rules that are load-bearing

**Never import `groq` or `httpx` above `app/llm/`.** Both clients translate
their SDK exceptions into `app.llm.errors` at the boundary. Code that catches
`groq.RateLimitError` will silently stop working the moment a call routes to
DeepSeek.

**Never re-implement the retry ladder.** `agents/base.py::_complete_with_retry`
owns it. Each error kind maps to one recovery:

- `MalformedToolCall` → resample the *same* model (feeding the garbled text back
  reinforces it; don't append it to the transcript)
- `OverCapacity` → back off, retry the same model
- `RateLimited` / `KeyPoolExhausted` / `ProviderUnavailable` / `RequestTooLarge`
  → escalate to the next model in the chain, immediately

**Provider resolution is explicit, not prefix-splitting.** Two real ids break
the naive rule and both are live in this repo:

| id | provider | why |
|---|---|---|
| `groq/compound` | Groq | a Groq model whose id starts with `groq/` |
| `deepseek-r1-distill-llama-70b` | Groq | DeepSeek weights, Groq host |
| `deepseek/deepseek-v4-pro` | DeepSeek | explicit prefix |
| `deepseek-v4-flash` | DeepSeek | bare id, but in the DeepSeek catalog |

If you change `resolve()`, `tests/test_llm_providers.py` has a case for each.

## Adding a model to the picker

If the provider already has a client, this is a one-line catalog change:

1. Append a `ModelInfo` to `GROQ_MODELS` or `DEEPSEEK_MODELS` in `catalog.py`.
2. Nothing else. `/api/models` merges the static catalog with the provider's
   live `GET /models`, and Settings › Models renders whatever comes back.

Ids DeepSeek starts serving *after* this file was written already appear in the
picker via `describe_unknown()` — you only add a static entry to give a model a
curated label, description, and tier.

## Adding a whole provider

Copy `deepseek_client.py`; it is the reference implementation for a provider
with no SDK.

1. **Client.** Same signature as `groq_client.chat_completion` — same kwargs,
   same return shape, same exceptions. If the provider isn't OpenAI-compatible,
   the response shim (`_Completion`/`_Choice`/`_Message`/`_ToolCall`) is where
   you normalise it. `agents/base.py` reads
   `resp.choices[0].message.tool_calls[0].function.arguments` off whatever
   answered; that shape is the contract.
2. **Errors.** Map every status code in `_raise_for_status`. Getting
   `ProviderUnavailable` vs `RateLimited` right matters: one tells the user to
   wait, the other tells them to fix their billing.
3. **Catalog.** Add to `Provider`, add a `*_MODELS` tuple, extend `resolve()`
   with an explicit prefix. Namespace the ids.
4. **Dispatch.** One branch in `client.chat_completion`.
5. **Keys.** Add fields to `config.Settings` with `AliasChoices` (this project's
   `.env` uses non-standard names like `Asterion_Secret_key`), plus a
   `*_api_keys` property using `_dedupe`.
6. **API.** Add the provider to the `providers` list in `api/routes/models.py`.
7. **Lifespan.** Close the client in `main.py`'s shutdown.

Pool keys per `(key, model)` — a key rate-limited on one model is usually fine
on another. Rotate only on 429 and timeouts. Everything else (5xx, 402, oversized
request) is a wall every key hits identically; let it propagate so the *model*
chain escalates.

## Routing and the override

`litellm_config.yaml` maps each agent alias to a model + fallback chain. It is
deliberately **Groq-only**.

The user's Settings › Models pick is an override (`selection.py`), **prepended**
to every agent's chain rather than replacing it:

```
selection = deepseek/deepseek-v4-pro
architect chain: deepseek-v4-pro → gpt-oss-120b → llama-3.3-70b → scout → gpt-oss-20b
```

That is the whole safety story. DeepSeek is prepaid; an unpaid account 402s every
completion. The override degrades onto Groq and the run completes. **Do not add
a `deepseek/…` alias to `litellm_config.yaml`** — it would make the choice
permanent and un-revocable, and `tests/test_routing.py` asserts the alias count.

`route_for()` is *sync* and runs on every LLM call, so `selection.current()`
reads an in-process cache. `main.py`'s lifespan calls `selection.load()` once at
startup to rehydrate it from Redis.

## Debugging "it used the wrong model"

- `chain_for(agent)` in a REPL shows the resolved chain, override included.
- The escalation is logged: `agent: <model> unusable (ProviderUnavailable),
  escalating to <next>`. Grep the backend log for `escalating`.
- An explicit `model=` argument beats the override by design — check whether the
  call site pins one (`intent.py`, `summarizer.py`, `attachments.py`,
  `control/actions.py`, `tasks/agent.py`, `tools/research.py` all do).
- `GET /api/models/health` round-trips a 5-token completion through each
  provider. It is the only way to tell a valid key from a valid-but-unfunded one.

## Verify

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/test_llm_providers.py tests/test_routing.py -q
```

Then exercise the real path — resolution and routing tests pass even when a
provider is misconfigured:

```bash
.venv/Scripts/python.exe -c "
import asyncio
from app.llm.client import chat_completion
async def go():
    r = await chat_completion([{'role':'user','content':'Reply with: OK'}], model='<the-model-id>', max_tokens=5)
    print(r.model, '->', r.choices[0].message.content)
asyncio.run(go())"
```
