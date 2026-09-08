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
assert(normalize.parameters.jsCode.includes("reattachStructural"));

const directorCriterion = "No blocking runtime error occurs during normal gameplay.";
const director = {
  product_identity: "Five Lives Hangman",
  objective: "Build a shell",
  sprint_id: "SPRINT-1",
  task_id: "SPRINT-1:SPECIALIST",
  project_id: "PROJECT-HANGMAN-PILOT-001",
  work_unit_id: "W001",
  constraints: ["constraint A", "constraint B"],
  acceptance_criteria: [directorCriterion],
  requirements: ["Render a page"],
  scope: ["W001"],
};
const analyst = {
  objective: "Build a shell",
  constraints: ["constraint A", "The constraint B"],
  acceptance_criteria: ["No blocking runtime error is present during normal gameplay."],
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
    acceptance_criteria: [directorCriterion],
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
  const a = await runNormalize({ payload: specialistContract() });
  assert.equal(JSON.parse(a.content).acceptance_criteria[0], directorCriterion);
  assert.equal(a.acceptance_criteria[0], directorCriterion);

  const b = await runNormalize({
    payload: specialistContract({
      acceptance_criteria: ["Gameplay must not hit a blocking runtime error."],
    }),
  });
  assert.equal(JSON.parse(b.content).acceptance_criteria[0], directorCriterion);

  const omitted = { ...specialistContract() };
  delete omitted.acceptance_criteria;
  const c = await runNormalize({ payload: omitted });
  assert.equal(JSON.parse(c.content).acceptance_criteria[0], directorCriterion);
  assert(JSON.parse(c.content).acceptance_criteria.includes(directorCriterion));

  const d = await runNormalize({
    payload: specialistContract({ acceptance_criteria: ["MUTATED CRITERION"] }),
    prevName: "Specialist Fallback",
  });
  assert.equal(JSON.parse(d.content).acceptance_criteria[0], directorCriterion);
  assert.equal(d.specialist_failover_used, true);

  await assert.rejects(
    () => runNormalize({ payload: specialistContract({ acceptance_criteria: { bad: true } }) }),
    /acceptance_criteria must be an array/
  );
  await assert.rejects(
    () => runNormalize({ payload: specialistContract({ acceptance_criteria: [1] }) }),
    /array of strings/
  );

  const f = await runNormalize({
    payload: specialistContract({
      constraints: ["MUTATED"],
      acceptance_criteria: ["paraphrased runtime safety"],
    }),
  });
  assert.deepEqual(JSON.parse(f.content).constraints, ["constraint A", "constraint B"]);
  assert.equal(JSON.parse(f.content).acceptance_criteria[0], directorCriterion);
  assert.equal(f.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(f.work_unit_id, "W001");

  console.log("Specialist inherited acceptance criteria fixtures: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
