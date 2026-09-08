const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const workflow = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json"), "utf8")
);
assert.equal(workflow.nodes.length, 61);
const b04 = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Codex-Executor-B04.json"), "utf8"));
assert.equal(b04.id, "y5gVRIcXPvXoTQic");
const names = workflow.nodes.map((n) => n.name);
assert(names.indexOf("Codex Executor") < names.indexOf("MiniMax Developer"));
assert(names.indexOf("MiniMax Developer") < names.indexOf("Laguna Developer"));
assert(workflow.nodes.find((n) => n.name === "Normalize Specialist Result").parameters.jsCode.includes("reattachStructural"));
assert(workflow.nodes.find((n) => n.name === "Normalize Analyst Result").parameters.jsCode.includes("stringList"));

const classifier = workflow.nodes.find((n) => n.name === "Provider Failure Classifier");
const chainEntry = workflow.nodes.find((n) => n.name === "Provider Chain Entry");
const attach = workflow.nodes.find((n) => n.name === "Attach Provider Success Metadata");
const dispatch = workflow.nodes.find((n) => n.name === "Provider Dispatch Router");
const dispatchKeys = dispatch.parameters.rules.values.map((r) => r.outputKey);

function dest(route) {
  return workflow.connections["Provider Dispatch Router"].main[dispatchKeys.indexOf(route)][0].node;
}

async function run(node, incoming, prevName, extras = {}) {
  const context = {
    $input: { first: () => ({ json: incoming }) },
    $prevNode: { name: prevName },
    $execution: { id: "97" },
    $: (name) => ({
      isExecuted: Boolean(extras[name]),
      last: () => ({ json: extras[name] || {} }),
    }),
  };
  return (await new vm.Script(`(async()=>{${node.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

(async () => {
  const dispatchState = {
    sprint_id: "SPRINT-1",
    _provider_role: "ANALYST",
    _provider_route: "ANALYST_QWEN",
    _provider_node: "Analyst",
    _provider_name: "OLLAMA_LOCAL",
    _provider_model: "qwen3:8b",
    provider_health: {},
    provider_failures: [],
  };

  const abort = await run(
    classifier,
    { error: "The connection was aborted, perhaps the server is offline" },
    "Analyst",
    { "Provider Dispatch Router": dispatchState }
  );
  assert.equal(abort._provider_action, "FAILOVER");
  assert.equal(abort._provider_route, "ANALYST_NVIDIA");
  assert.equal(abort.final_failure_class, "PROVIDER_NETWORK_ERROR");
  assert.equal(abort.provider_chain_exhausted, false);
  assert.equal(abort._provider_timeout_state, "PRIMARY_TIMEOUT");
  assert.equal(dest("ANALYST_NVIDIA"), "Analyst NVIDIA Fallback");

  const timeout = await run(
    classifier,
    { error: "Reduce context after repeated 300000ms timeout." },
    "Analyst",
    { "Provider Dispatch Router": dispatchState }
  );
  assert.equal(timeout._provider_action, "FAILOVER");
  assert.equal(timeout._provider_route, "ANALYST_NVIDIA");
  assert.notEqual(timeout._provider_route, "RECOVERY");

  const nvidiaFail = await run(
    classifier,
    { error: "The connection was aborted, perhaps the server is offline" },
    "Analyst NVIDIA Fallback",
    {
      "Provider Dispatch Router": {
        ...dispatchState,
        _provider_node: "Analyst NVIDIA Fallback",
        _provider_name: "NVIDIA",
        _provider_model: "nvidia/nemotron-3.5-lightning-30b-a3b",
        provider_health: {
          "MODEL:qwen3:8b": { temporary_unavailable: true, consecutive_availability_failures: 1 },
        },
        provider_failures: [{ role: "ANALYST", model: "qwen3:8b" }],
      },
    }
  );
  assert.equal(nvidiaFail._provider_route, "ANALYST_GROQ");
  assert.equal(nvidiaFail.provider_chain_exhausted, false);

  const groqFail = await run(
    classifier,
    { error: "The connection was aborted, perhaps the server is offline" },
    "Analyst Groq Fallback",
    {
      "Provider Dispatch Router": {
        ...dispatchState,
        _provider_node: "Analyst Groq Fallback",
        _provider_name: "GROQ",
        _provider_model: "openai/gpt-oss-120b",
        provider_health: {
          "MODEL:qwen3:8b": { temporary_unavailable: true, consecutive_availability_failures: 1 },
          "MODEL:nvidia/nemotron-3.5-lightning-30b-a3b": { temporary_unavailable: true, consecutive_availability_failures: 1 },
        },
        provider_failures: [
          { role: "ANALYST", model: "qwen3:8b" },
          { role: "ANALYST", model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
        ],
      },
    }
  );
  assert.equal(groqFail._provider_route, "RECOVERY");
  assert.equal(groqFail.provider_chain_exhausted, true);
  assert.equal(groqFail._provider_timeout_state, "CHAIN_EXHAUSTED");

  const refused = await run(
    classifier,
    { error: "connect ECONNREFUSED 127.0.0.1:11434" },
    "Analyst",
    { "Provider Dispatch Router": dispatchState }
  );
  assert.equal(refused._provider_action, "FAILOVER");
  assert.equal(refused._provider_route, "ANALYST_NVIDIA");

  const afterSuccess = await run(
    chainEntry,
    {
      _provider_completed_role: "ANALYST",
      _provider_next_role: "SPECIALIST",
      _provider_retry_role: "ANALYST",
      provider_failures: [{ role: "ANALYST", model: "qwen3:8b", failure_class: "PROVIDER_NETWORK_ERROR" }],
    },
    "Provider Success Router"
  );
  assert.equal(afterSuccess._provider_role, "SPECIALIST");
  assert.equal(afterSuccess._provider_route, "SPECIALIST_NVIDIA_ULTRA");

  const afterPrimaryTimeout = await run(
    chainEntry,
    {
      _provider_retry_role: "ANALYST",
      provider_recovery_cycles: { x: 1 },
      _provider_recovery_cycle: 1,
      provider_failures: [{ role: "ANALYST", model: "qwen3:8b" }],
      provider_health: { "MODEL:qwen3:8b": { temporary_unavailable: true } },
    },
    "Provider Recovery Wait"
  );
  assert.equal(afterPrimaryTimeout._provider_route, "ANALYST_NVIDIA");
  assert.notEqual(afterPrimaryTimeout._provider_route, "ANALYST_QWEN");

  const primaryOk = await run(
    attach,
    {
      project_id: "PROJECT-HANGMAN-PILOT-001",
      work_unit_id: "W001",
      content: JSON.stringify({
        objective: "x",
        scope: ["x"],
        requirements: ["r"],
        constraints: ["c"],
        acceptance_criteria: ["a"],
        known_context: ["k"],
        status: "READY",
        blocker: "NONE",
      }),
    },
    "Normalize Analyst Result",
    {
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
  assert.equal(primaryOk._provider_retry_role, null);
  assert.equal(primaryOk._provider_timeout_state, "PRIMARY_SUCCESS");
  assert.equal(primaryOk.failover_used, false);

  const fallbackOk = await run(
    attach,
    {
      project_id: "PROJECT-HANGMAN-PILOT-001",
      work_unit_id: "W001",
      known_context: ["k"],
      content: JSON.stringify({
        objective: "x",
        scope: ["x"],
        requirements: ["r"],
        constraints: ["c"],
        acceptance_criteria: ["a"],
        known_context: ["k"],
        status: "READY",
        blocker: "NONE",
      }),
    },
    "Normalize Analyst Result",
    {
      "Provider Chain Entry": {
        _provider_role: "ANALYST",
        _provider_node: "Analyst NVIDIA Fallback",
        _provider_name: "NVIDIA",
        _provider_model: "nvidia/nemotron-3.5-lightning-30b-a3b",
      },
      "Provider Dispatch Router": {},
    }
  );
  assert.equal(fallbackOk.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(fallbackOk.work_unit_id, "W001");
  assert.deepEqual(JSON.parse(fallbackOk.content).known_context, ["k"]);
  assert.deepEqual(JSON.parse(fallbackOk.content).requirements, ["r"]);
  assert.deepEqual(JSON.parse(fallbackOk.content).constraints, ["c"]);
  assert.deepEqual(JSON.parse(fallbackOk.content).acceptance_criteria, ["a"]);
  assert.equal(fallbackOk.failover_used, true);
  assert.equal(fallbackOk._provider_timeout_state, "FALLBACK_SUCCESS");
  assert.equal(fallbackOk._provider_next_role, "SPECIALIST");

  const recovery = workflow.nodes.find((n) => n.name === "Provider Recovery Controller");
  const waitRetry = await run(recovery, groqFail, "Provider Failure Classifier");
  assert.equal(waitRetry._provider_recovery_action, "WAIT_RETRY");
  assert.equal(waitRetry.provider_chain_exhausted, false);
  assert.equal(waitRetry._provider_retry_role, "ANALYST");

  const firstCycle = await run(chainEntry, { provider_health: {}, provider_failures: [] }, "Prepare First-Cycle Context");
  assert.equal(firstCycle._provider_route, "ANALYST_QWEN");
  assert.equal(firstCycle.provider_chain_exhausted, false);

  console.log("Analyst timeout fallback fixtures: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
