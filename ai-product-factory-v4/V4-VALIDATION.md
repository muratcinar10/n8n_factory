# AI Product Factory V4 — Static Validation

Overall result: **PASS**

Validation was static only. No workflow was imported, activated, or executed.

Additional static checks:

- All Code node JavaScript bodies compiled with `new Function(...)`: **PASS**.
- Every named `$('<node>')` reference resolves to a node in the same workflow: **PASS**.

## Computed workflow results

| Workflow file | Nodes | Triggers | Duplicate names | Duplicate IDs | Disconnected | Legacy | Active | Result |
|---|---:|---:|---:|---:|---:|---:|---|---|
| `AI-Product-Factory-V4.json` | 27 | 1 | 0 | 0 | 0 | 0 | `false` | PASS |
| `AI-Product-Factory-V4-Codex-Executor.json` | 15 | 1 | 0 | 0 | 0 | 0 | `false` | PASS |
| `AI-Product-Factory-V4-Opportunity-Research.json` | 10 | 1 | 0 | 0 | 0 | 0 | `false` | PASS |

## Safety invariants

- `all_json_parsed` = `true` — PASS
- `all_workflows_inactive` = `true` — PASS
- `main_base_orchestration_nodes` = `20` — includes the two approved Nemotron response normalizers
- `main_persistence_nodes` = `4` — allowed persistence-only increase
- `main_quality_lessons_nodes` = `3` — shared read, lesson routing, deterministic upsert
- `opportunity_base_nodes` = `6` — PASS
- `opportunity_persistence_nodes` = `1` — allowed persistence-only increase
- `opportunity_quality_lessons_nodes` = `2` — shared read and conditional rejection-lesson persistence
- `same_job_write_resubmit` = `false` — PASS
- `automatic_codex_acceptance` = `false` — PASS
- `opportunity_auto_starts_product` = `false` — PASS
- `telegram_waits_for_reply` = `false` — PASS
- `blocker_halts_independent_work` = `false` — PASS
- `native_data_table_nodes_configured` = `true` — PASS
- `product_factory_lessons_schema_verified` = `true` — PASS
- `mi_database_or_repository_targeted_by_persistence` = `false` — PASS
- `lessons_per_agent_cap` = `5` — PASS
- `low_score_low_confidence_rejected` = `true` — PASS
- `stale_2023_signal_rejected` = `true` — PASS
- `rejected_opportunity_reaches_pool` = `false` — PASS
- `lesson_record_key_is_deterministic` = `true` — PASS
- `lesson_upsert_matches_record_key` = `true` — PASS


## Graph observations

- Main: one webhook trigger; all 27 nodes are reachable. One shared Data Table read loads at most five high-occurrence lessons. `Consistency Reviewer` and `QA Lead` use the approved NVIDIA Nemotron model; their normalizers accept the supported response shapes, validate JSON/output contracts, and fail closed. The other six model roles remain on local Qwen. `Sprint Report` emits the state first and evidence-backed lesson records separately; `Lesson Output Router` preserves the original sprint persistence path and sends only lesson items to the lesson upsert.
- Codex child: one sub-workflow trigger; the polling loop returns only to `Host Writer Wait`, never to submission.
- Opportunity: one schedule trigger; all ten nodes are reachable. Accepted opportunities alone pass through `Opportunity Pool Adapter`. `Opportunity Lesson Gate` sends only evidence-backed quality lessons to the static lesson upsert; the no-lesson output ends without a Data Table operation. No developer, Host Writer, Product Factory, or Telegram path exists.
- Every persistence node is `n8n-nodes-base.dataTable` v1.1 with operation `upsert` and a table resource selected by name.
- Main persistence stores one idempotent snapshot per sprint and table. Opportunity persistence stores one row per deterministic opportunity key.
- The only retry loop in the main workflow is task-level and capped at three attempts. Every Codex attempt enters the child anew and therefore creates a fresh job ID.
- QA-only refinement is capped at two passes.
- A static regression fixture matching the bad live class (`2023`, score `0`, confidence `LOW`, one old signal) produced zero accepted opportunities and reason codes `LOW_SCORE`, `LOW_CONFIDENCE`, and `STALE_SIGNAL`.

## Known validation boundary

Import/runtime compatibility was checked against locally installed n8n 2.37.9 node definitions and by JSON/graph inspection. The installation exposes Data Table node v1.1 with native get/upsert support and an OpenAI-compatible node whose Base URL belongs in its credential. The six Data Tables, including the verified `product_factory_lessons` schema, exist in local n8n. The NVIDIA credential is intentionally not embedded or assigned a fabricated ID; after import, the human must securely create/select one credential with Base URL `https://integrate.api.nvidia.com/v1` for `Consistency Reviewer` and `QA Lead`. No workflow was imported, activated, or executed by this patch. External HTTP availability, Ollama/NVIDIA output quality, workflow table writes, Telegram delivery, and Host Writer operation remain runtime checks. Existing low-quality opportunity rows, if any, are not retroactively deleted by this non-destructive patch.
