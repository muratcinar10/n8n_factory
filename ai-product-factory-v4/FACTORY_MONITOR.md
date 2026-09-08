# Local Factory Monitor

The monitor now displays persisted project title/status, Work Units done/total, and the current Work Unit above the existing 61-stage telemetry. It links back to the Local Director Console. Missing project evidence is shown as unavailable rather than inferred.

The Factory Monitor is a small Python standard-library service serving a read-only page at [http://127.0.0.1:8787](http://127.0.0.1:8787).

It displays persisted project title/status, Work Units done/total, and the current Work Unit above the existing 61-stage telemetry. It links back to the Local Director Console. Missing project evidence is shown as unavailable rather than inferred.

Start it from `ai-product-factory-v4`:

```bash
python3 factory-infrastructure/monitor/factory_monitor.py
```

The page polls `/api/status` every five seconds and displays:

- node/stage name
- current or last factual activity
- last-updated time in Europe/Istanbul
- current/most-recent sprint and Inspector health

Node 61, `Factory Telemetry Publisher`, writes only an allowlisted telemetry envelope to the fixed `/data/factory-monitor/latest.json` path. n8n receives only the dedicated telemetry directory, not a general filesystem mount. Telemetry contains no prompts, credentials, secrets, or model chain-of-thought.

The monitor provides only `GET /`, static assets, `GET /api/status`, and `GET /health`. POST, PUT, PATCH, and DELETE return 405. It has no workflow, retry, execution, shell, filesystem, credential, model, or editor controls.

The current design publishes at Sprint Report completion. The page can display live `RUNNING` telemetry when a safe publisher updates the file, but the parent currently publishes a completion snapshot rather than per-node streaming. This keeps the architecture small and prevents monitor logic from affecting execution.
