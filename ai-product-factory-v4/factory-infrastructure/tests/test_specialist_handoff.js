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

const dispatchNode = workflow.nodes.find((n) => n.name === "Provider Dispatch Router");
const dispatchKeys = dispatchNode.parameters.rules.values.map((r) => r.outputKey);
const dispatchConns = workflow.connections["Provider Dispatch Router"].main;

function dest(routeKey) {
  const index = dispatchKeys.indexOf(routeKey);
  assert(index >= 0, "missing dispatch route " + routeKey);
  return dispatchConns[index][0];
}

function requireHandoff(routeKey, nodeName) {
  const edge = dest(routeKey);
  assert.equal(edge.node, nodeName);
  assert.equal(
    edge.index,
    0,
    `missing handoff: ${routeKey} must enter ${nodeName} on input 0, got dest index ${edge.index}`
  );
}

requireHandoff("SPECIALIST_NVIDIA_ULTRA", "Specialist");
requireHandoff("SPECIALIST_NVIDIA_LIGHTNING", "Specialist Fallback");
requireHandoff("SPECIALIST_GROQ", "Specialist Groq Fallback");
requireHandoff("PLANNER_NVIDIA_LIGHTNING", "Planner");

for (const provider of ["Specialist", "Specialist Fallback", "Specialist Groq Fallback"]) {
  const success = workflow.connections[provider].main[0][0];
  assert.equal(success.node, "Normalize Specialist Result");
  assert.equal(success.index, 0, `missing handoff: ${provider} success must enter Normalize Specialist Result input 0`);
}

const normalizeOut = workflow.connections["Normalize Specialist Result"].main[0][0];
assert.equal(normalizeOut.node, "Attach Provider Success Metadata");

const successRouter = workflow.nodes.find((n) => n.name === "Provider Success Router");
const successKeys = successRouter.parameters.rules.values.map((r) => r.outputKey);
const specialistRoute = workflow.connections["Provider Success Router"].main[successKeys.indexOf("SPECIALIST")][0];
assert.equal(specialistRoute.node, "Provider Chain Entry");
assert.equal(dest("PLANNER_NVIDIA_LIGHTNING").node, "Planner");
assert.equal(dest("PLANNER_NVIDIA_LIGHTNING").index, 0);

const normalize = workflow.nodes.find((n) => n.name === "Normalize Specialist Result");

async function runNormalize(item, prevName = "Specialist") {
  const director = {
    product_identity: "Five Lives Hangman",
    objective: "Build a shell",
    sprint_id: "SPRINT-1",
    task_id: "SPRINT-1:SPECIALIST",
    constraints: ["No MI"],
    acceptance_criteria: ["Page renders"],
    requirements: ["Render a page"],
    scope: ["W001"],
  };
  const analyst = {
    objective: "Build a shell",
    constraints: ["No MI"],
    acceptance_criteria: ["Page renders"],
    requirements: ["Render a page"],
    scope: ["W001"],
  };
  const context = {
    $input: { first: () => ({ json: item }) },
    $prevNode: { name: prevName },
    $execution: { id: "test" },
    $: (name) => {
      if (name === "Director Sprint Input") return { json: director, item: { json: director } };
      if (name === "Normalize Analyst Result") {
        return { json: { content: JSON.stringify(analyst) }, item: { json: { content: JSON.stringify(analyst) } } };
      }
      return { isExecuted: false, last: () => ({ json: null }) };
    },
  };
  return (await new vm.Script(`(async()=>{${normalize.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

(async () => {
  await assert.rejects(() => runNormalize({ content: "" }), /SPECIALIST_INVALID_CONTRACT/);
  await assert.rejects(() => runNormalize({ body: { choices: [{ message: { content: "not-json" } }] } }), /SPECIALIST_INVALID_CONTRACT/);

  const specialistBroken = dest("SPECIALIST_NVIDIA_ULTRA");
  if (specialistBroken.index !== 0) {
    assert.fail("missing handoff: Specialist dest index " + specialistBroken.index);
  }

  console.log("Specialist handoff fixtures: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
