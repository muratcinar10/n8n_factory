const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const workflow = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json"), "utf8")
);
const node = workflow.nodes.find((n) => n.name === "Normalize Analyst Result");
assert.equal(workflow.nodes.length, 61);
const b04 = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Codex-Executor-B04.json"), "utf8"));
assert.equal(b04.id, "y5gVRIcXPvXoTQic");
const names = workflow.nodes.map((n) => n.name);
assert(names.indexOf("Codex Executor") < names.indexOf("MiniMax Developer"));
assert(names.indexOf("MiniMax Developer") < names.indexOf("Laguna Developer"));

const base = {
  objective: "Build a Hangman shell",
  scope: ["W001"],
  requirements: ["Render a page"],
  acceptance_criteria: ["Page renders"],
  constraints: ["No MI"],
  status: "READY",
  blocker: "NONE",
};

async function run(item) {
  const context = { $input: { first: () => ({ json: item }) } };
  return (await new vm.Script(`(async()=>{${node.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

function parsed(item) {
  return JSON.parse(item.content);
}

(async () => {
  const a = await run({
    project_id: "PROJECT-HANGMAN-PILOT-001",
    work_unit_id: "W001",
    content: JSON.stringify({ ...base, known_context: ["Director brief summary", "Five lives per round"] }),
  });
  assert.deepEqual(parsed(a).known_context, ["Director brief summary", "Five lives per round"]);
  assert.deepEqual(a.known_context, ["Director brief summary", "Five lives per round"]);

  const b = await run({ content: JSON.stringify({ ...base }) });
  assert.deepEqual(parsed(b).known_context, []);

  const c = await run({
    content: JSON.stringify({
      ...base,
      known_context: "This is the first real product produced through AI Product Factory V4 Project Orchestrator.",
    }),
  });
  assert.deepEqual(parsed(c).known_context, [
    "This is the first real product produced through AI Product Factory V4 Project Orchestrator.",
  ]);

  await assert.rejects(
    () => run({ content: JSON.stringify({ ...base, known_context: { nested: true } }) }),
    /known_context/
  );
  await assert.rejects(
    () => run({ content: JSON.stringify({ ...base, known_context: [1, 2] }) }),
    /known_context/
  );

  const primary = await run({
    project_id: "PROJECT-HANGMAN-PILOT-001",
    work_unit_id: "W001",
    content: JSON.stringify({ ...base, known_context: ["primary analyst"] }),
  });
  assert.deepEqual(parsed(primary).known_context, ["primary analyst"]);
  assert.equal(primary.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(primary.work_unit_id, "W001");

  const fallback = await run({
    project_id: "PROJECT-HANGMAN-PILOT-001",
    work_unit_id: "W001",
    body: {
      choices: [{ message: { content: JSON.stringify({ ...base, known_context: ["fallback analyst"] }) } }],
    },
  });
  assert.deepEqual(parsed(fallback).known_context, ["fallback analyst"]);
  assert.equal(fallback.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(fallback.work_unit_id, "W001");

  console.log("Normalize Analyst known_context fixtures: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
