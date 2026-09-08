# Director Bridge

The Bridge also hosts the Local Director Console and deterministic Project Orchestrator. See `DIRECTOR_CONSOLE.md` and `PROJECT_ORCHESTRATOR.md`. The original narrow `/v1/start_sprint` API remains intact; external ChatGPT direct invocation is an optional future enhancement, not a first-product blocker.

The Director Bridge is a local-only, narrow adapter between a Director-facing client and the fixed AI Product Factory webhook. It is not an n8n proxy and exposes no workflow IDs, shell, filesystem, credentials, Market Intelligence, or arbitrary HTTP access.

It also hosts the Local Director Console and deterministic Project Orchestrator. See `DIRECTOR_CONSOLE.md` and `PROJECT_ORCHESTRATOR.md`. The original narrow `/v1/start_sprint` API remains intact; external ChatGPT direct invocation is an optional future enhancement, not a first-product blocker.

## Methods

- `POST /v1/start_sprint` validates a strict Sprint Brief and returns a deterministic `sprint_id`.
- `GET /v1/sprints/<sprint_id>/status` returns status only for bridge-created sprints.
- `GET /v1/sprints/<sprint_id>/result` returns the result only for bridge-created sprints.

The only currently allowed `requested_target` is `SMOKE_FIXTURE`. Allowed actions are `READ_PROJECT`, `CREATE_FILES`, `MODIFY_FILES`, and `RUN_TESTS`. Unknown fields, targets, actions, path-like targets, oversized bodies, and conflicting duplicate request IDs are rejected.

## Security boundary

The server binds to `127.0.0.1:8765` and requires `FACTORY_BRIDGE_TOKEN` from the process environment. No token is stored in source or audit records. Factory execution is disabled unless `FACTORY_EXECUTION_ENABLED=true`; when enabled, the destination remains the compiled-in local Product Factory webhook. The workflow ID and destination URL cannot be supplied by a caller.

Start locally with a token supplied only in the process environment:

```bash
FACTORY_BRIDGE_TOKEN='<local-secret>' python3 factory-infrastructure/bridge/director_bridge.py
```

The parent workflow must be explicitly activated by a human before production webhook execution is possible. This repository does not activate it.

## External ChatGPT requirement

The local backend is ready, but this ChatGPT conversation has no proven authenticated connector capable of calling the machine's localhost bridge. A human must configure one secure, narrowly scoped ChatGPT Action/MCP/connector route to these three methods and provide its bearer token without exposing n8n. Until that is done, external ChatGPT invocation is not ready.

Audit records contain only request/sprint identifiers, timestamps, state, factory execution identifier when available, result status, target, and result. Credentials are never recorded.
