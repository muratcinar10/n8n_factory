# Project Orchestrator

## Architecture

Director → ChatGPT Master Plan → `127.0.0.1:8787` Director Control → immutable plan → mutable queue → one eligible Work Unit → existing 61-node Factory → QA/QA Lead/Inspector evidence → next eligible Work Unit.

ChatGPT decomposes the product. The local controller only validates, persists, selects, packages, dispatches, and records. It does not use an LLM, split/merge units, invent requirements, or rewrite acceptance criteria.

## Schema 1.0

The exact project fields are `schema_version`, `project_id`, `project_name`, `project_goal`, `global_context`, and `work_units`. `global_context` contains `summary`, `architecture`, `constraints`, `forbidden_actions`, and `completion_criteria`.

Each unit contains `work_unit_id`, `title`, `objective`, `context`, `dependencies`, `allowed_scope`, `forbidden_scope`, `acceptance_criteria`, `test_requirements`, `expected_artifacts`, and numeric `priority`. Priority 1 is highest. Eligible units are selected by ascending priority, then ascending normalized Work Unit ID.

Validation rejects missing/unknown fields, duplicate or malformed IDs, unknown/self/circular dependencies, empty acceptance criteria, invalid priority, unsafe absolute/parent/protected paths, workflow/writer/shell control fields, empty plans, more than 500 units, or input larger than 256 KiB. Text is data, never executable authority.

See `EXAMPLE_PROJECT_MASTER_PLAN.json`.

## Four controls

1. **VALIDATE** parses and validates only. It persists and executes nothing.
2. **LOAD** requires the same valid plan, writes an immutable canonical plan under `bridge/state/plans`, and creates separate mutable state under `bridge/state`. It does not start production and fails closed if the ID conflicts.
3. **START** accepts only the loaded `project_id`, starts it once, and never rewrites the immutable plan.
4. **RESUME** applies only to `PAUSED_HUMAN_AUTH`; it skips completed/unresolved units and never blindly duplicates an ambiguous running dispatch.

## State and completion

Unit states: `PENDING`, `READY`, `RUNNING`, `DONE`, `FAILED`, `NEEDS_REVIEW`, `DEFERRED`, `BLOCKED`.

Dependencies must all be `DONE`. Multiple units may be ready, but conservative mode permits one running project and one active Work Unit globally. A failed branch blocks its dependents; independent eligible units continue.

`DONE` requires Factory `COMPLETED`, deterministic QA `PASS`, QA Lead `QA_APPROVED`, and zero open items. Terminal technical failure becomes `FAILED`; ambiguous or incomplete evidence becomes `NEEDS_REVIEW`. Factory's bounded maximum-three recovery remains authoritative; the controller adds no retry loop. `DEFERRED` and review/failure evidence remain durable.

Project results are `COMPLETED`, `COMPLETED_WITH_OPEN_ITEMS`, or `FAILED_NO_PROGRESS`. Exact unresolved IDs remain visible.

## Bounded package and evidence

Each dispatch contains identity, unit objective/scope, bounded summary/architecture, global constraints, direct dependency outcomes, acceptance criteria, tests, expected artifacts, and fixed allowed/forbidden action policy. It excludes the full Master Plan, unrelated history, prompts, and chain-of-thought.

Mutable state retains timestamps, stable dispatch ID, attempt count, factory execution ID when returned, QA and QA Lead state, Inspector score, bounded acceptance evidence, and failure reason. Atomic writes use `fsync` and same-filesystem replace.

On restart, an ambiguous `RUNNING` unit becomes `NEEDS_REVIEW` and the project becomes `PAUSED_HUMAN_AUTH`. It is not redispatched.

## Local UI and security

The combined Turkish Director Control, project dashboard, Work Unit table, and read-only 61-node Factory view are at `http://127.0.0.1:8787`. The Bridge backend remains at `127.0.0.1:8765`. Start both with `factory-infrastructure/start_factory_console.sh`.

The monitor proxies only allowlisted project operations to the same controller. Browser requests use a process-local HttpOnly SameSite cookie; the internal bearer token is never placed in HTML, JavaScript, telemetry, or localStorage. There is no workflow selector, shell, filesystem browser, arbitrary HTTP, credential, model, prompt, MI, or raw n8n endpoint.

Future authenticated ChatGPT integration can feed the same validation/controller contract. It is not required for current operation.
