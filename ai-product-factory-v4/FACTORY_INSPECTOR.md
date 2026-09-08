# Factory Inspector

`Factory Inspector` is Node 60 in the 61-node infrastructure artifact. It observes the completed Sprint Report state and cannot alter prompts, providers, routing, QA thresholds, credentials, or security settings.

## Deterministic score

The 100-point score uses:

- 35: task correctness from observed deterministic-QA verdicts
- 20: downstream acceptance from observed QA Lead verdicts
- 15: constraint compliance from recorded critical findings
- 10: first-pass success from completed-task attempt counts
- 10: reliability from recorded technical failures
- 10: efficiency from recorded Developer attempts

An unavailable dimension is marked `NOT_OBSERVED`, contributes zero points, and retains its evidence counters. The Inspector never invents a value. Bands are `HEALTHY` (80–100), `WATCH` (60–79), and `INVESTIGATE` (0–59).

The result includes role scores, weakest link, first-pass rate, failure/recovery counts, QA evidence, recommendations, and a five-sprint rolling score. Rolling history uses n8n workflow static data and is capped at five completed Inspector observations.

Recommendations are advisory codes only. `control_plane_mutation_allowed` is always `false`.

The existing deterministic QA node is byte-for-byte unchanged. Developer order remains Codex → MiniMax → Laguna.
