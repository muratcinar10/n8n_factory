# Local Director Console

The Director Console is the accepted one-time human handoff between ChatGPT's project planning and the local Product Factory.

1. Ask ChatGPT to produce the complete Master Project Plan as the manifest described in `PROJECT_ORCHESTRATOR.md`.
2. Open `http://127.0.0.1:8787`.
3. Paste the manifest once. Pasting alone performs no operation.
4. Select **Planı Doğrula**. This checks the complete plan without persisting or executing it.
5. Select **Projeyi Yükle**. This persists the immutable plan and separate mutable state, but starts nothing.
6. Inspect the loaded project, then select **Projeyi Başlat** once. Only this action authorizes the first eligible Work Unit dispatch.
7. Watch the project ledger and the Turkish, read-only 61-stage Factory view on the same `http://127.0.0.1:8787` page. Use **Projeyi Sürdür** only for a persisted `PAUSED_HUMAN_AUTH` project.

The Console shows counts for every Work Unit state, the current Work Unit, exact failed/review/blocked/deferred IDs, Inspector evidence, last result, update time, filters, and the Factory Floor output beneath the input area. **Pause after current** never interrupts an executing unit; it prevents dispatch of the next unit. **Resume** restarts deterministic selection.

Start both local services with `./factory-infrastructure/start_factory_console.sh`. Stop only services managed by that launcher with `./factory-infrastructure/stop_factory_console.sh`.

Both services bind to `127.0.0.1`. Port 8787 is the only browser control surface; port 8765 is an internal bridge backend. Starting the console does not activate or execute the n8n workflow. A project starts only after the Director presses **Projeyi Başlat**, and the approved parent workflow must separately be active for its production webhook to accept that request.

Browser mutations require a private, process-local HttpOnly session cookie and same-origin request. The older bearer-token bridge API remains available for a future direct integration.

`EXTERNAL_CHATGPT_DIRECT_INVOCATION: OPTIONAL_FUTURE_ENHANCEMENT`
