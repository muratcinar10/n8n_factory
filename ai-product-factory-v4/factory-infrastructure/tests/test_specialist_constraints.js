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

const normalize = workflow.nodes.find((n) => n.name === "Normalize Specialist Result");
assert(normalize.parameters.jsCode.includes("inheritedConstraints"));

const director = {
  product_identity: "Five Lives Hangman",
  objective: "Build a shell",
  sprint_id: "SPRINT-1",
  task_id: "SPRINT-1:SPECIALIST",
  project_id: "PROJECT-HANGMAN-PILOT-001",
  work_unit_id: "W001",
  constraints: ["constraint A", "constraint B"],
  acceptance_criteria: ["Page renders"],
  requirements: ["Render a page"],
  scope: ["W001"],
};
const analyst = {
  objective: "Build a shell",
  constraints: ["constraint A", "The constraint B"],
  acceptance_criteria: ["Page renders"],
  requirements: ["Render a page"],
  scope: ["W001"],
};

function specialistContract(overrides = {}) {
  return {
    product_identity: "Five Lives Hangman",
    sprint_goal: "Build a shell",
    task_id: "SPRINT-1:SPECIALIST",
    mode: "UI",
    approved_scope: ["W001"],
    requirements: ["Render a page"],
    constraints: ["constraint A", "constraint B"],
    acceptance_criteria: ["Page renders"],
    known_facts: ["Hangman uses five lives"],
    unknowns: ["Exact layout"],
    planner_handoff: ["Keep the shell local"],
    status: "READY",
    blocker: "",
    ...overrides,
  };
}

async function runNormalize({ payload, prevName = "Specialist", extra = {} }) {
  const item = {
    project_id: "PROJECT-HANGMAN-PILOT-001",
    work_unit_id: "W001",
    ...extra,
    body: { choices: [{ message: { content: JSON.stringify(payload) } }] },
  };
  const context = {
    $input: { first: () => ({ json: item }) },
    $prevNode: { name: prevName },
    $execution: { id: "test" },
    $: (name) => {
      if (name === "Director Sprint Input") return { json: director, item: { json: director } };
      if (name === "Normalize Analyst Result") {
        const json = { content: JSON.stringify(analyst) };
        return { json, item: { json } };
      }
      return { isExecuted: false, last: () => ({ json: null }) };
    },
  };
  return (await new vm.Script(`(async()=>{${normalize.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

(async () => {
  const canonical = ["constraint A", "constraint B"];

  const primary = await runNormalize({ payload: specialistContract(), prevName: "Specialist" });
  assert.deepEqual(JSON.parse(primary.content).constraints, canonical);
  assert.deepEqual(primary.constraints, canonical);

  const fallback = await runNormalize({ payload: specialistContract(), prevName: "Specialist Fallback" });
  assert.deepEqual(JSON.parse(fallback.content).constraints, canonical);
  assert.equal(fallback.specialist_failover_used, true);

  const omitted = { ...specialistContract() };
  delete omitted.constraints;
  const recovered = await runNormalize({ payload: omitted, prevName: "Specialist" });
  assert.deepEqual(JSON.parse(recovered.content).constraints, canonical);

  const mutated = await runNormalize({
    payload: specialistContract({ constraints: ["MUTATED", "unrelated"] }),
    prevName: "Specialist Groq Fallback",
  });
  assert.deepEqual(JSON.parse(mutated.content).constraints, canonical);
  assert.equal(mutated.specialist_failover_used, true);

  await assert.rejects(
    () => runNormalize({ payload: specialistContract({ constraints: { nested: true } }) }),
    /constraints must be an array/
  );
  await assert.rejects(
    () => runNormalize({ payload: specialistContract({ constraints: [1, 2] }) }),
    /array of strings/
  );

  const ids = await runNormalize({
    payload: specialistContract(),
    extra: { project_id: "PROJECT-HANGMAN-PILOT-001", work_unit_id: "W001" },
  });
  assert.equal(ids.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(ids.work_unit_id, "W001");
  assert.deepEqual(ids.constraints, canonical);

  console.log("Specialist inherited constraints fixtures: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
