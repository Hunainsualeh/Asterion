## Engineering standards
Build to a senior engineer's bar:
- **Separation of concerns** — high cohesion, low coupling; small, well-named functions; split files along real boundaries, never one god-file.
- **Right abstractions & patterns** — model the domain properly; reuse, don't copy-paste; design for change.
- **Robustness** — validate inputs; handle errors and edge cases (empty/missing/large/malformed); never fail silently.
- **Scalable** — mind hot-path complexity; avoid O(n²) over growing data and unindexed lookups.
- **No filler** — no placeholders, TODOs, dead code, or fake data; every path fully implemented and working.
- **Maintainable** — consistent naming/style; comments only where intent isn't obvious; remove duplication as you go.
