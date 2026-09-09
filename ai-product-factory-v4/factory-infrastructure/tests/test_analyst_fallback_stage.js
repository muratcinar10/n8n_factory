const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const workflow = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json"), "utf8")
);
assert.equal(workflow.nodes.length, 64);

const recovery = workflow.nodes.find((n) => n.name === "Recovery Controller");
const router = workflow.nodes.find((n) => n.name === "Recovery Router");
const chainEntry = workflow.nodes.find((n) => n.name === "Provider Chain Entry");
const attach = workflow.nodes.find((n) => n.name === "Attach Provider Success Metadata");
const sprintControl = workflow.nodes.find((n) => n.name === "Sprint Control Router");

const recoveryKeys = router.parameters.rules.values.map((r) => r.outputKey);
assert.equal(recoveryKeys[0], "RECOVER_FROM_START");
assert.equal(recoveryKeys[1], "TRY_ALTERNATE_DEVELOPER");
assert.equal(recoveryKeys.includes("CONTINUE_FROM_COMPLETED_ANALYST"), true);
assert.equal(recoveryKeys.includes("FAILOVER_NEXT_ANALYST"), true);
assert.equal(
  workflow.connections["Recovery Router"].main[recoveryKeys.indexOf("RECOVER_FROM_START")][0].node,
  "Analyst"
);
assert.equal(
  workflow.connections["Recovery Router"].main[recoveryKeys.indexOf("CONTINUE_FROM_COMPLETED_ANALYST")][0].node,
  "Attach Provider Success Metadata"
);
assert.equal(
  workflow.connections["Recovery Router"].main[recoveryKeys.indexOf("FAILOVER_NEXT_ANALYST")][0].node,
  "Provider Dispatch Router"
);
assert.equal(
  workflow.connections["Recovery Router"].main[recoveryKeys.indexOf("TRY_ALTERNATE_DEVELOPER")][0].node,
  "Developer Dispatcher"
);
assert(sprintControl.parameters.rules.values.some((r) => r.outputKey === "QA_REFINE"));

const validAnalyst = {
  objective: "Create the Hangman shell",
  scope: ["index.html"],
  requirements: ["visible shell"],
  constraints: ["no paid APIs"],
  acceptance_criteria: ["Application renders a visible Hangman shell"],
  known_context: ["browser game"],
  status: "READY",
  blocker: "NONE",
};

function nvidiaBody(payload) {
  return { body: { choices: [{ message: { content: JSON.stringify(payload) } }] } };
}

async function run(node, incoming, extras = {}) {
  const context = {
    $input: { first: () => ({ json: incoming }) },
    $prevNode: { name: extras.prevName || "Normalize Technical Failure" },
    $execution: { id: "119" },
    $: (name) => ({
      isExecuted: extras[name] != null,
      last: () => ({ json: extras[name] || {} }),
    }),
  };
  return (await new vm.Script(`(async()=>{${node.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

(async () => {
  const primaryOk = await run(
    attach,
    { content: JSON.stringify(validAnalyst) },
    {
      prevName: "Normalize Analyst Result",
      "Provider Chain Entry": {
        _provider_role: "ANALYST",
        _provider_node: "Analyst",
        _provider_name: "OLLAMA_LOCAL",
        _provider_model: "qwen3:8b",
      },
      "Provider Dispatch Router": {},
    }
  );
  assert.equal(primaryOk._provider_completed_role, "ANALYST");
  assert.equal(primaryOk._provider_next_role, "SPECIALIST");
  const afterPrimary = await run(chainEntry, primaryOk, { prevName: "Provider Success Router" });
  assert.equal(afterPrimary._provider_role, "SPECIALIST");
  assert.equal(afterPrimary._provider_route, "SPECIALIST_NVIDIA_ULTRA");
  console.log("CASE A primary success → Specialist: PASS");

  const nvidiaSuccess = await run(
    recovery,
    {
      technical_failure_record: {
        failed_role: "ANALYST",
        failed_node: "Normalize Analyst Result",
        failure_type: "MALFORMED_MODEL_OUTPUT",
        safe_to_retry: true,
        message: "stale primary failure still present",
      },
      technical_failures: [{ failed_node: "Analyst", failure_type: "TIMEOUT", failed_role: "ANALYST" }],
      recovery_restart_count: 0,
    },
    { "Analyst NVIDIA Fallback": nvidiaBody(validAnalyst) }
  );
  assert.equal(nvidiaSuccess.recovery_action, "CONTINUE_FROM_COMPLETED_ANALYST");
  assert.equal(nvidiaSuccess._analyst_stage, "COMPLETE");
  assert.equal(nvidiaSuccess._provider_completed_role, "ANALYST");
  assert.equal(nvidiaSuccess._provider_next_role, "SPECIALIST");
  assert.equal(nvidiaSuccess.accepted_provider, "NVIDIA");
  assert.equal(nvidiaSuccess.failover_used, true);
  assert.notEqual(nvidiaSuccess.recovery_action, "RECOVER_FROM_START");
  const afterFallback = await run(chainEntry, nvidiaSuccess, { prevName: "Provider Success Router" });
  assert.equal(afterFallback._provider_role, "SPECIALIST");
  assert.notEqual(afterFallback._provider_route, "ANALYST_QWEN");
  console.log("CASE B primary fail + fallback success → Specialist: PASS");
  console.log("CASE B primary re-entry prevented: PASS");

  const bothFail = await run(
    recovery,
    {
      technical_failure_record: {
        failed_role: "ANALYST",
        failed_node: "Analyst Groq Fallback",
        failure_type: "TIMEOUT",
        safe_to_retry: true,
        message: "groq timeout",
      },
      technical_failures: [
        { failed_node: "Analyst", failure_type: "TIMEOUT", failed_role: "ANALYST" },
        { failed_node: "Analyst NVIDIA Fallback", failure_type: "TIMEOUT", failed_role: "ANALYST" },
      ],
      recovery_restart_count: 0,
    },
    {
      "Analyst NVIDIA Fallback": { error: "timeout" },
      "Analyst Groq Fallback": { error: "timeout" },
    }
  );
  assert.equal(bothFail.recovery_action, "RECOVER_FROM_START");
  assert.notEqual(bothFail._analyst_stage, "COMPLETE");
  console.log("CASE C all Analyst providers fail: PASS");

  const stale = await run(
    recovery,
    {
      technical_failure_record: {
        failed_role: "ANALYST",
        failed_node: "Analyst",
        failure_type: "TIMEOUT",
        safe_to_retry: true,
        message: "The connection was aborted, perhaps the server is offline",
      },
      technical_failures: [{ failed_node: "Analyst", failure_type: "TIMEOUT", failed_role: "ANALYST" }],
      recovery_restart_count: 1,
    },
    {
      "Analyst NVIDIA Fallback": nvidiaBody(validAnalyst),
      "Normalize Analyst Result": { content: JSON.stringify(validAnalyst) },
    }
  );
  assert.equal(stale.recovery_action, "CONTINUE_FROM_COMPLETED_ANALYST");
  assert.equal(stale._provider_next_role, "SPECIALIST");
  assert.notEqual(stale.recovery_action, "RECOVER_FROM_START");
  console.log("CASE D stale Ollama failure cannot override NVIDIA success: PASS");

  const refineKeys = sprintControl.parameters.rules.values.map((r) => r.outputKey);
  assert(refineKeys.includes("QA_REFINE"));
  const developerFailover = await run(recovery, {
    technical_failure_record: {
      failed_role: "DEVELOPER",
      failed_node: "Cursor Developer",
      failure_type: "TIMEOUT",
      safe_to_retry: true,
      developer_route: "CURSOR",
      task_id: "T01",
      task_attempt: 1,
      message: "timeout",
    },
    developer_candidates: [
      { rank: 1, provider: "CURSOR", configured: true },
      { rank: 2, provider: "CODEX", configured: true },
    ],
    developer_attempt_history: [{ provider: "CURSOR", underlying_task_key: "T01", status: "DISPATCHED", meaningful_attempt: true }],
    developer_exclusions: [],
    tasks: [{ task_id: "T01", objective: "shell", status: "RUNNING" }],
  });
  assert.equal(developerFailover.recovery_action, "TRY_ALTERNATE_DEVELOPER");
  assert.equal(developerFailover.developer_route, "CODEX");
  console.log("CASE E legitimate developer refinement/failover preserved: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
