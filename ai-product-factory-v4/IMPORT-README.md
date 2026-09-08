# AI Product Factory V4 — Import Guide

All files are new workflows and are exported inactive. Nothing in this package was imported or executed while it was built.

## Import order

1. Import `AI-Product-Factory-V4-Codex-Executor.json` as a new workflow.
2. Copy the imported child workflow ID from its n8n URL.
3. Import `AI-Product-Factory-V4.json` as a new workflow.
4. In the main workflow, verify `Codex Executor` still references the already configured child workflow ID `v5qVRIcXPvXoTQic`. Do not enable Codex routing if the imported child receives a different ID; update only that reference after review.
5. Import `AI-Product-Factory-V4-Opportunity-Research.json` as a new workflow.
6. Verify the six native n8n Data Tables described below. The workflow selects them by exact name; no internal table ID is hard-coded.
7. Confirm the existing `Ollama account` and `Telegram account` references resolve locally. Do not paste credentials into workflow JSON.
8. In n8n, create one OpenAI-compatible credential for NVIDIA NIM: enter the API key only in n8n's secure credential UI and set Base URL to `https://integrate.api.nvidia.com/v1`. Select that same credential on `Consistency Reviewer` and `QA Lead`. Do not place the key or a fabricated credential ID in any workflow file.
9. Confirm all imported workflows remain inactive. Do not publish or activate them before Director/ChatGPT static review and the separately authorized controlled smoke test.

## Director sprint input

The main entry point is a POST webhook at `ai-product-factory-v4-sprint`. A minimum input is:

```json
{
  "sprint_id": "SPRINT-001",
  "objective": "One bounded sprint objective",
  "known_context": [],
  "constraints": [],
  "codex_available": false
}
```

Keep `codex_available` false until the child workflow ID, Host Writer mount, and worker are verified. The main webhook responds from `Terminal Output for Director` after the sprint ends.

## Host Writer contract

The child writes one `{"prompt": "..."}` file to `/data/host-writer/inbox/<fresh-job-id>.json`, then polls `/data/host-writer/results/<same-job-id>.json` every 15 seconds. It times out after 25 minutes (100 polls), longer than the 20-minute worker timeout. A missing file follows the node's error output and cannot loop forever. Polling never returns to `Host Writer Submit`.

Success, preflight drift, and failure use the proven result contract. `stdout` is not propagated. There is no callback, automatic retry, automatic resubmission, or automatic acceptance.

## Native Data Table setup

Create these Data Tables in the same n8n project before activation:

| Data Table | Required user columns |
|---|---|
| `product_factory_opportunities` | `record_key` String, `title` String, `opportunity_score` Number, `confidence` String, `discovered_at` String, `payload_json` String |
| `product_factory_review_backlog` | `record_key` String, `sprint_id` String, `item_count` Number, `payload_json` String |
| `product_factory_blockers` | `record_key` String, `sprint_id` String, `item_count` Number, `payload_json` String |
| `product_factory_codex_audit` | `record_key` String, `sprint_id` String, `item_count` Number, `payload_json` String |
| `product_factory_sprint_history` | `record_key` String, `sprint_id` String, `item_count` Number, `payload_json` String |
| `product_factory_lessons` | `record_key` String, `lesson_type` String, `source_node` String, `task_id` String, `sprint_id` String, `mistake_signature` String, `lesson_text` String, `prevention_rule` String, `evidence_json` String, `occurrence_count` Number, `last_seen_at` String |

Do not create a custom `id` column; n8n supplies its own row ID and timestamps. Column names and types must match exactly.

The main workflow performs four sequential upserts after `Sprint Report`. Each table keeps one current snapshot per `sprint_id`, so different sprints accumulate without deleting earlier rows. Reusing a `sprint_id` intentionally updates that sprint's row instead of creating a duplicate.

These are native n8n metadata tables. They have no MI database credential, query, repository path, or product-code write capability.

The lessons table stores advisory mistake memory only. Both workflows read at most five high-occurrence rows. Main-workflow lessons are routed from real consistency/QA/blocker evidence. Opportunity lessons are written only for source-backed quality rejections. Deterministic `record_key`/`mistake_signature` matching prevents duplicate rows; a repeated loaded signature increases `occurrence_count` and updates `last_seen_at`. Lessons contain no source code or secrets and cannot authorize writes or override safety.

## Opportunity workflow

The schedule is every two days at 10:00 using workflow timezone `Europe/Istanbul`. It reads public Hacker News and Reddit endpoints, treats source content as untrusted data, and validates score, confidence, problem concreteness, URL quality, recency, repeated-signal claims, and unsupported market/payment claims. Only accepted opportunities are upserted into `product_factory_opportunities`. The deterministic key is derived from normalized title plus the first evidence URL, so later runs update the same opportunity without deleting unrelated rows. Rejections contain reason codes and may produce a lessons-table update only when source evidence exists. The workflow has no connection to the Product Factory and sends no Telegram message.

## Before activation

- Review node prompts and repository scope.
- Verify Ollama exposes `qwen3:8b`.
- Verify `Consistency Reviewer` and `QA Lead` both use `nvidia/nemotron-3.5-lightning-30b-a3b` and resolve the same NVIDIA OpenAI-compatible credential.
- Verify the Host Writer bind mount exists only where intended.
- Verify the Telegram chat target is appropriate.
- Confirm all six Data Table selectors resolve and their column schemas match this guide.
- Do not activate the opportunity schedule until its public-source policy is acceptable.
