"""Cross-chat notification channel.

Unlike the per-project event stream (app/orchestration/events.py), this is a
single global feed per user: reminders, missed-task alerts, and anything else
the assistant needs to surface reach the user here regardless of which chat —
or none — is open. Backed by a Redis Stream (replayable) tailed over SSE, plus
browser Notification API delivery on the client.
"""
