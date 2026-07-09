## Quality-assurance standards
Verify behaviour; never rubber-stamp:
- **Meets the spec** — satisfies every acceptance criterion, not just "something exists".
- **Complete** — no truncated/missing pieces; runs as-is (imports, wiring, entry point).
- **Correct** — trace the logic; test the paths a happy-path demo skips.
- **Test design** — cover boundary values, invalid/empty input, and error paths.
- **Non-functional** — weigh usability, accessibility, performance, security.
- **Severity** — separate blocking defects (wrong result, crash, security) from advisory polish; be specific and actionable.
- **Evidence** — prefer running the code/tests over eyeballing; only pass what genuinely holds up.
