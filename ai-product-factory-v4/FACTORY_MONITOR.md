# Local Factory Monitor

The Factory Monitor is a local-only page at [http://127.0.0.1:8787](http://127.0.0.1:8787). Read-only factory telemetry stays separate from the narrow Director Console write surface on the same origin. See `PROJECT_ORCHESTRATOR.md`.

Start both services from `ai-product-factory-v4`:

```bash
./factory-infrastructure/start_factory_console.sh
```

Or the monitor alone:

```bash
python3 factory-infrastructure/monitor/factory_monitor.py
```

The page polls `/api/status` every five seconds. Node timestamps use Europe/Istanbul. Node 61 writes only an allowlisted envelope to `/data/factory-monitor/latest.json`. The monitor binds `127.0.0.1` and does not expose credentials, prompts, or chain-of-thought.

Allowed writes (cookie + same-origin only): `VALIDATE PLAN`, `START PROJECT`, and optional pause/resume of a persisted project. PUT/PATCH/DELETE return 405. There is no workflow, shell, filesystem, credential, model, or editor control.
