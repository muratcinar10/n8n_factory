const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const workflow = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json"), "utf8")
);
const attach = workflow.nodes.find((n) => n.name === "Attach Provider Success Metadata");
const router = workflow.nodes.find((n) => n.name === "Provider Success Router");
assert.equal(workflow.nodes.length, 64);
const b04 = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Codex-Executor-B04.json"), "utf8"));
assert.equal(b04.id, "y5gVRIcXPvXoTQic");

const names = workflow.nodes.map((n) => n.name);
assert(names.indexOf("Codex Executor") < names.indexOf("MiniMax Developer"));
assert(names.indexOf("MiniMax Developer") < names.indexOf("Laguna Developer"));

async function runAttach({ incoming, prevName, chain, dispatch }) {
  const context = {
    $input: { first: () => ({ json: incoming }) },
    $prevNode: { name: prevName },
    $: (name) => ({
      isExecuted: ["Provider Chain Entry", "Provider Dispatch Router"].includes(name),
      last: () => ({ json: name === "Provider Chain Entry" ? chain || {} : dispatch || {} }),
    }),
  };
  return (await new vm.Script(`(async()=>{${attach.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

function routeOf(role) {
  const rules = router.parameters.rules.values.map((r) => r.outputKey);
  const connections = workflow.connections["Provider Success Router"].main;
  const index = rules.indexOf(role);
  assert(index >= 0, "missing router output " + role);
  return connections[index][0].node;
}

(async () => {
  const analystContent = JSON.stringify({
    objective: "x",
    scope: ["x"],
    requirements: ["x"],
    acceptance_criteria: ["x"],
    constraints: ["x"],
    status: "READY",
    blocker: "NONE",
  });

  const a = await runAttach({
    incoming: { content: analystContent },
    prevName: "Normalize Analyst Result",
    chain: { _provider_role: "ANALYST", _provider_node: "Analyst", _provider_name: "OLLAMA_LOCAL", _provider_model: "qwen3:8b" },
    dispatch: {},
  });
  assert.equal(a._provider_completed_role, "ANALYST");
  assert.equal(a._provider_next_role, "SPECIALIST");
  assert.equal(routeOf("ANALYST"), "Provider Chain Entry");

  const b = await runAttach({
    incoming: { content: analystContent },
    prevName: "Normalize Analyst Result",
    chain: {
      _provider_role: "ANALYST",
      _provider_node: "Analyst NVIDIA Fallback",
      _provider_name: "NVIDIA",
      _provider_model: "nvidia/nemotron-3.5-lightning-30b-a3b",
    },
    dispatch: {},
  });
  assert.equal(b._provider_completed_role, "ANALYST");
  assert.equal(b._provider_next_role, "SPECIALIST");
  assert.equal(b.selected_provider, "NVIDIA");
  assert.notEqual(b._provider_completed_role, "NVIDIA");

  const c = await runAttach({
    incoming: { content: JSON.stringify({ mode: "UI" }) },
    prevName: "Normalize Specialist Result",
    chain: { _provider_role: "SPECIALIST", _provider_node: "Specialist", _provider_name: "NVIDIA", _provider_model: "nvidia/nemotron-3-ultra-550b-a55b" },
  });
  assert.equal(c._provider_completed_role, "SPECIALIST");
  assert.equal(c._provider_next_role, "PLANNER");
  assert.equal(routeOf("SPECIALIST"), "Provider Chain Entry");

  const d = await runAttach({
    incoming: { content: "{}" },
    prevName: "Normalize Planner Result",
    chain: { _provider_role: "PLANNER", _provider_node: "Planner", _provider_name: "NVIDIA", _provider_model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
  });
  assert.equal(d._provider_completed_role, "PLANNER");
  assert.equal(d._provider_next_role, "CONSISTENCY_REVIEWER");
  assert.equal(routeOf("PLANNER"), "Provider Chain Entry");

  const e = await runAttach({
    incoming: { content: "{}" },
    prevName: "Normalize Consistency Result",
    chain: { _provider_role: "CONSISTENCY_REVIEWER", _provider_node: "Consistency Reviewer", _provider_name: "NVIDIA", _provider_model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
  });
  assert.equal(e._provider_completed_role, "CONSISTENCY_REVIEWER");
  assert.equal(routeOf("CONSISTENCY_REVIEWER"), "Developer Dispatcher");

  const f = await runAttach({
    incoming: { content: analystContent },
    prevName: "Unrelated Node",
    chain: {},
    dispatch: {},
  });
  assert.equal(f._provider_completed_role, "UNKNOWN");
  assert.equal(f.final_failure_class, "PROVIDER_ROLE_UNKNOWN");
  const unknownIndex = router.parameters.rules.values.findIndex((r) => r.outputKey === "UNKNOWN");
  assert(unknownIndex >= 0);
  assert.equal(workflow.connections["Provider Success Router"].main[unknownIndex][0].node, "Provider Failure Terminal");
  assert.equal(router.parameters.options.fallbackOutput, "extra");
  const fallback = workflow.connections["Provider Success Router"].main[workflow.connections["Provider Success Router"].main.length - 1];
  assert.equal(fallback[0].node, "Provider Failure Terminal");

  console.log("Provider success role routing fixtures: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
