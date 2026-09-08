# AI Product Factory V4 Constitution

## Authority

The Director is external to n8n and supplies exactly one sprint objective. The factory must never invent or automatically start the next sprint. A new sprint begins only from a new Director request.

## Scope and truth

- Analyst, Specialist, Planner, Consistency Reviewer, Developer, QA, and QA Lead stay inside the current sprint objective.
- Analyst extracts every explicit requirement, separates assumptions and ambiguities, and produces measurable acceptance criteria.
- Specialist records concrete edge cases and validation/error states without introducing unnecessary architecture.
- Planner creates at most eight non-duplicate, testable tasks with valid earlier-task dependencies.
- Consistency Reviewer is the primary mistake hunter. It identifies who diverged, the exact mismatch, and affected task IDs before allowing only independent valid tasks to proceed.
- Model output is untrusted until parsed and checked. Missing evidence is reported as unknown or not verified.
- Static review is never represented as runtime proof.
- The single Specialist selects UI, backend, or integration behavior inside one node.
- Inconsistent planner tasks do not reach a developer. They enter `REVIEW_BACKLOG`; independent consistent work continues.

## Development safety

- Codex repository writes may occur only through the existing Host Writer filesystem contract.
- Every Host Writer attempt receives a fresh job ID. Polling never resubmits the same job.
- No automatic acceptance, deployment, production database mutation, migration, destructive Git, secret access, Docker socket, privileged mode, or sandbox weakening is permitted.
- Critical production, payment, credential, destructive, and production-database tasks enter `REVIEW_BACKLOG` without execution.
- Qwen is the fallback developer when Codex is unavailable. Its output receives `QWEN CODE REVIEW`, normal QA, and a non-blocking `CODEX_AUDIT_QUEUE` record.
- The current Ollama node has no repository-write tool. Qwen must not claim that proposed patches were applied or tests ran without evidence.

## Failure and QA

- A developer task has at most three attempts. A fourth attempt is impossible in the controller.
- After three failed or unverified attempts, the task becomes a `DEFERRED_BLOCKER`; independent work continues.
- `NEEDS_REVIEW` enters `REVIEW_BACKLOG` and does not halt independent work.
- QA returns only `PASS`, `FAIL`, or `NOT_VERIFIED` and receives Director, Analyst, Specialist, Planner task, and developer evidence.
- Every QA result maps acceptance criteria to explicit evidence. Static inspection is not runtime proof; missing runtime proof is `NOT_VERIFIED`.
- QA Lead returns only `QA_APPROVED` or `QA_INCOMPLETE`. Missing-test refinement is bounded to two passes.
- QA Lead audits acceptance-criterion coverage, edge cases, and evidence strength and cannot approve weak proof.
- No Playwright or browser automation is included in V4.0.

## State

Only these business collections exist: `REVIEW_BACKLOG`, `DEFERRED_BLOCKERS`, `CODEX_AUDIT_QUEUE`, `SPRINT_HISTORY`, and lightweight `LESSONS`.

V4 uses native n8n Data Tables for small, durable Product Factory metadata. The main workflow upserts one snapshot per `sprint_id` into `product_factory_review_backlog`, `product_factory_blockers`, `product_factory_codex_audit`, and `product_factory_sprint_history`. Opportunity Research upserts individual accepted opportunities into `product_factory_opportunities` using a deterministic evidence-based key. Both workflows share `product_factory_lessons`.

Lessons are structured mistake memory, not model training. They may be created only from observed reviewer/QA/blocker/research-rejection evidence. `mistake_signature` and `record_key` are deterministic; upsert updates the same logical lesson and its occurrence count rather than creating endless duplicates. At most five high-occurrence lessons enter any applicable prompt under `PAST LESSONS`. Lessons are advisory: they never override the Director, weaken safety, authorize writes, approve work, modify credentials, alter Host Writer security, or change model configuration.

These tables belong to n8n, not the Market Intelligence database. Persistence nodes must never receive MI database credentials, repository paths, or production data mutations. The six tables and their documented columns must exist in n8n before workflow activation.

## Communication and opportunities

- Telegram sends one informational sprint summary and never waits for a reply.
- Opportunity Research runs separately every two days at 10:00 in `Europe/Istanbul`.
- Opportunity evidence must include a public URL, title, and snippet. Weak or unsupported ideas are rejected.
- Persistence requires score >= 50, confidence `MEDIUM` or `HIGH`, a concrete problem, at least one valid URL-backed signal, and non-stale evidence. Recurring claims require at least two distinct supporting URLs.
- Unsupported market-gap or willingness-to-pay claims are rejected. Unevidenced market fields remain `UNKNOWN`, and unevidenced product lists remain empty.
- Rejections carry structured reason codes. Only accepted opportunities reach `product_factory_opportunities`; evidence-backed quality rejections may update `product_factory_lessons`.
- Opportunity Research never sends Telegram, creates a sprint, or reaches any developer path.
