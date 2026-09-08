# Project Orchestrator

A **Project** is a durable queue of Work Units. A **Sprint** is one factory execution of **one** Work Unit. The Product Factory must never receive an entire product as one giant development task.

ChatGPT / the Director decomposes the product. This orchestrator is a deterministic scheduler: validate, persist, package, submit one eligible unit, record evidence, continue.

## Accepted Director path

ChatGPT prepares a Project Master Plan → human pastes once at [http://127.0.0.1:8787](http://127.0.0.1:8787) → Local Project Orchestrator → one Work Unit → existing 61-node Product Factory → result → next eligible unit.

## Optional future path

Authenticated Director Bridge (`127.0.0.1:8765`) may submit the **same** Master Plan contract. External ChatGPT → localhost is not required for the first real product.

## Schema `1.0`

Required: `schema_version`, `project_id`, `work_units`, `global_constraints`, `global_forbidden_actions`, plus a title (`project_title` or `project_name`), a goal (`project_goal` or string `global_context`), and completion criteria (`completion_criteria` or `definition_of_done`).

Each Work Unit requires: `work_unit_id`, `title`, goal (`goal` or `objective`), `context`, `dependencies`, `acceptance_criteria`, `test_requirements`.

Optional: `allowed_files` / `allowed_scope`, `forbidden_files` / `forbidden_scope`, unit `forbidden_actions`, `related_components`, `notes`, `priority`.

Rejected: duplicate/self/missing/cyclic dependencies, privileged control fields (`workflow_id`, `shell_command`, credentials, Host Writer, MI), absolute/`..` paths, oversized plans.

See `EXAMPLE_PROJECT_MASTER_PLAN.json`.

## State machine

`PENDING` → `READY` → `RUNNING` → `DONE` | `NEEDS_REVIEW` | `DEFERRED`

Technical `FAILED` with the same failure signature is retried until **3** factory submissions, then `NEEDS_REVIEW`. There is no fourth attempt. Factory `RECOVERY_EXHAUSTED` becomes `DEFERRED` immediately. Dependents of unresolved units become `BLOCKED`; independent units continue. Sequence order, one `RUNNING` unit, no parallelism.

Project: `VALIDATED` → `RUNNING` → `COMPLETED` | `COMPLETED_WITH_OPEN_ITEMS` | `NEEDS_REVIEW` | `FAILED` | `PAUSED`.

## Persistence / resume

Atomic JSON: `factory-infrastructure/bridge/state/<project_id>.json` and immutable `state/plans/<project_id>.json`. Survives browser and service restart. Unknown `RUNNING` after crash → `NEEDS_REVIEW` + `PAUSED` (not redispatched). DONE units are never resubmitted.

## Director Console

Open `http://127.0.0.1:8787` (bind `127.0.0.1` only). Paste plan → **VALIDATE PLAN** → **START PROJECT**. Optional pause-after-current / resume. No n8n editing, Terminal, curl, per-unit clicks, shell, credentials, or workflow IDs.

Start services: `./factory-infrastructure/start_factory_console.sh`.

Factory webhook is fixed: `http://127.0.0.1:5678/webhook/ai-product-factory-v4-sprint`. Packaged briefs include `project_id` and `work_unit_id`, not the full Master Plan. Target remains allowlisted (`SMOKE_FIXTURE` until a later human-authorized change). Inspector observes; it does not steer the queue.
