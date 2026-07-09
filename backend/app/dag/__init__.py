"""DAG-based agent orchestration.

`engine`     — generic async DAG executor (parallelism, retries, timeouts,
               cancellation, node-level status, cycle prevention, history).
`workflows`  — intent-specific DAG builders + node executors (planner,
               research, analyze, summarize, weather, code, answer).
`task_runner`— the task-lane counterpart of `app.orchestration.runner`:
               launches a DAG run for a project, streams progress events,
               persists the final result.
"""
