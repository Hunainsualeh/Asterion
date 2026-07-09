"""Conversational system control.

Lets the user drive the app itself from chat ("open settings", "new chat",
"delete this chat", "switch theme"). The backend only ever *resolves* an
intent into a whitelisted action and emits it; the client executes it, showing
a confirmation dialog for destructive ones. Every action is audited.

Security model (see actions.py): nothing runs that isn't in the registry, the
server re-derives the `destructive` flag rather than trusting the client, and
low-confidence guesses fall through to plain chat instead of firing.
"""
