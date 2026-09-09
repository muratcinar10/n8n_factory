const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const workflow = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json"), "utf8")
);
assert.equal(workflow.nodes.length, 64);

const recovery = workflow.nodes.find((n) => n.name === "Recovery Controller");
const attach = workflow.nodes.find((n) => n.name === "Attach Provider Success Metadata");
const chainEntry = workflow.nodes.find((n) => n.name === "Provider Chain Entry");
const normalizeSpecialist = workflow.nodes.find((n) => n.name === "Normalize Specialist Result");
const normalizePlanner = workflow.nodes.find((n) => n.name === "Normalize Planner Result");
const normalizeConsistency = workflow.nodes.find((n) => n.name === "Normalize Consistency Result");
const dispatcher = workflow.nodes.find((n) => n.name === "Developer Dispatcher");

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

const validSpecialist = {
  product_identity: "Five Lives Hangman",
  sprint_goal: "Create the Hangman shell",
  task_id: "SPRINT-1:SPECIALIST",
  mode: "UI",
  status: "READY",
  blocker: "NONE",
  approved_scope: ["index.html", "W001"],
  constraints: ["no paid APIs", "No MI"],
  requirements: ["visible shell", "Render a page"],
  acceptance_criteria: ["Application renders a visible Hangman shell", "Page renders"],
  known_facts: ["browser game"],
  unknowns: [],
  planner_handoff: ["keep the visible shell"],
};

const validPlanner = {
  tasks: [
    {
      task_id: "T01",
      objective: "Create the Hangman shell",
      expected_result: "Playable shell",
      acceptance_criteria: ["Application renders a visible Hangman shell"],
      test_requirements: ["npm test --silent"],
      dependencies: [],
    },
  ],
};

const validConsistency = { status: "CONSISTENT", valid_task_ids: ["T01"], mismatches: [] };

const director = {
  body: {
    sprint_id: "SPRINT-1",
    task_id: "SPRINT-1:SPECIALIST",
    project_id: "PROJECT-HANGMAN-PILOT-001",
    work_unit_id: "W001",
    product_identity: "Five Lives Hangman",
    objective: "Create the Hangman shell",
    scope: ["W001"],
    approved_scope: ["index.html"],
    constraints: ["No MI"],
    acceptance_criteria: ["Page renders"],
    requirements: ["Render a page"],
    cursor_available: true,
    codex_available: true,
    laguna_available: true,
  },
};

function storeFrom(map) {
  return (name) => {
    const json = map[name];
    if (json == null) return { isExecuted: false, last: () => ({ json: null }), item: { json: {} } };
    return {
      isExecuted: true,
      last: () => ({ json }),
      item: { json },
      json,
    };
  };
}

async function run(node, incoming, extras = {}) {
  const map = { ...extras.nodes };
  const context = {
    $input: { first: () => ({ json: incoming }) },
    $prevNode: { name: extras.prevName || "Normalize Specialist Result" },
    $execution: { id: "125" },
    $: storeFrom(map),
  };
  return (await new vm.Script(`(async()=>{${node.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

function nvidiaBody(payload) {
  return { body: { choices: [{ message: { content: JSON.stringify(payload) } }] } };
}

(async () => {
  const primaryAttach = await run(
    attach,
    { content: JSON.stringify(validAnalyst), _provider_role: "ANALYST" },
    {
      prevName: "Normalize Analyst Result",
      nodes: {
        "Provider Chain Entry": {
          _provider_role: "ANALYST",
          _provider_node: "Analyst",
          _provider_name: "OLLAMA_LOCAL",
          _provider_model: "qwen3:8b",
        },
        "Provider Dispatch Router": {},
      },
    }
  );
  assert.equal(primaryAttach._provider_completed_role, "ANALYST");
  assert.ok(primaryAttach._accepted_analyst_content);
  assert.equal(JSON.parse(primaryAttach._accepted_analyst_content).objective, validAnalyst.objective);
  const afterPrimary = await run(chainEntry, primaryAttach, { prevName: "Provider Success Router" });
  assert.equal(afterPrimary._provider_role, "SPECIALIST");
  assert.ok(afterPrimary._accepted_analyst_content);
  console.log("CASE A Primary Analyst → Specialist: PASS");

  const nvidiaSuccess = await run(
    recovery,
    {
      technical_failure_record: {
        failed_role: "ANALYST",
        failed_node: "Normalize Analyst Result",
        failure_type: "MALFORMED_MODEL_OUTPUT",
        safe_to_retry: true,
        message: "stale primary failure",
      },
      technical_failures: [{ failed_node: "Analyst", failure_type: "TIMEOUT", failed_role: "ANALYST" }],
      recovery_restart_count: 0,
      project_id: "PROJECT-HANGMAN-PILOT-001",
      work_unit_id: "W001",
    },
    {
      prevName: "Normalize Technical Failure",
      nodes: { "Analyst NVIDIA Fallback": nvidiaBody(validAnalyst) },
    }
  );
  assert.equal(nvidiaSuccess.recovery_action, "CONTINUE_FROM_COMPLETED_ANALYST");
  assert.equal(nvidiaSuccess._analyst_stage, "COMPLETE");
  assert.ok(nvidiaSuccess.content);
  assert.ok(nvidiaSuccess._accepted_analyst_content);
  assert.deepEqual(JSON.parse(nvidiaSuccess.content).requirements, validAnalyst.requirements);

  const fallbackAttach = await run(attach, nvidiaSuccess, {
    prevName: "Recovery Router",
    nodes: {
      "Provider Chain Entry": nvidiaSuccess,
      "Provider Dispatch Router": {},
    },
  });
  assert.ok(fallbackAttach._accepted_analyst_content);
  const afterFallback = await run(chainEntry, fallbackAttach, { prevName: "Provider Success Router" });
  assert.equal(afterFallback._provider_role, "SPECIALIST");
  assert.equal(JSON.parse(afterFallback._accepted_analyst_content).status, "READY");
  console.log("CASE B Fallback Analyst → Specialist: PASS");

  const specialistOk = await run(
    normalizeSpecialist,
    nvidiaBody(validSpecialist),
    {
      prevName: "Specialist",
      nodes: {
        "Director Sprint Input": director,
        "Recovery Controller": nvidiaSuccess,
        "Attach Provider Success Metadata": fallbackAttach,
        "Normalize Analyst Result": { error: "MALFORMED_MODEL_OUTPUT" },
        "Analyst NVIDIA Fallback": nvidiaBody(validAnalyst),
        "Provider Failure Classifier": {},
      },
    }
  );
  assert.ok(specialistOk.content);
  assert.equal(JSON.parse(specialistOk.content).mode, "UI");
  assert.ok(specialistOk.constraints.includes("no paid APIs"));
  console.log("CASE C Specialist normalization: PASS");

  const plannerOk = await run(normalizePlanner, { choices: [{ message: { content: JSON.stringify(validPlanner) } }] }, {
    prevName: "Planner",
    nodes: {
      "Attach Provider Success Metadata": {
        ...fallbackAttach,
        _accepted_specialist_content: specialistOk.content,
        _provider_completed_role: "SPECIALIST",
      },
      "Provider Chain Entry": afterFallback,
      "Normalize Specialist Result": specialistOk,
    },
  });
  assert.ok(plannerOk._accepted_analyst_content);
  assert.ok(plannerOk._accepted_specialist_content);
  assert.equal(JSON.parse(plannerOk.content).tasks[0].task_id, "T01");
  console.log("CASE D Planner handoff: PASS");

  const consistencyOk = await run(normalizeConsistency, { choices: [{ message: { content: JSON.stringify(validConsistency) } }] }, {
    prevName: "Consistency Reviewer",
    nodes: {
      "Attach Provider Success Metadata": plannerOk,
      "Provider Chain Entry": plannerOk,
      "Normalize Planner Result": plannerOk,
    },
  });
  assert.ok(consistencyOk._accepted_analyst_content);
  assert.ok(consistencyOk._accepted_specialist_content);
  assert.ok(consistencyOk._accepted_planner_content);
  assert.equal(JSON.parse(consistencyOk.content).status, "CONSISTENT");
  console.log("CASE E Consistency handoff: PASS");

  const dispatch = await run(
    dispatcher,
    consistencyOk,
    {
      prevName: "Normalize Consistency Result",
      nodes: {
        "Director Sprint Input": director,
        "Recovery Controller": nvidiaSuccess,
        "Attach Provider Success Metadata": {
          ...consistencyOk,
          _accepted_specialist_content: specialistOk.content,
          _accepted_planner_content: plannerOk.content,
        },
        "Normalize Analyst Result": { error: "MALFORMED" },
        "Normalize Specialist Result": specialistOk,
        "Normalize Planner Result": plannerOk,
        "Normalize Consistency Result": consistencyOk,
        "Analyst NVIDIA Fallback": nvidiaBody(validAnalyst),
      },
    }
  );
  assert.equal(dispatch.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(dispatch.work_unit_id, "W001");
  assert.deepEqual(dispatch.analyst_report.requirements, validAnalyst.requirements);
  assert.deepEqual(dispatch.analyst_report.acceptance_criteria, validAnalyst.acceptance_criteria);
  assert.ok(dispatch.constraints.length);
  assert.equal(dispatch.tasks[0].task_id, "T01");
  console.log("CASE F Developer Dispatcher input: PASS");

  const replaced = await run(
    attach,
    nvidiaBody({ ping: true }),
    {
      prevName: "Specialist",
      nodes: {
        "Provider Chain Entry": {
          ...afterFallback,
          _accepted_analyst_content: JSON.stringify(validAnalyst),
        },
        "Provider Dispatch Router": {},
      },
    }
  );
  assert.ok(replaced._accepted_analyst_content);
  assert.equal(JSON.parse(replaced._accepted_analyst_content).objective, validAnalyst.objective);
  console.log("CASE G Provider-root replacement: PASS");

  await assert.rejects(
    () =>
      run(
        normalizeSpecialist,
        nvidiaBody({ mode: "UI" }),
        {
          prevName: "Specialist",
          nodes: {
            "Director Sprint Input": director,
            "Recovery Controller": nvidiaSuccess,
            "Attach Provider Success Metadata": fallbackAttach,
            "Normalize Analyst Result": { content: JSON.stringify(validAnalyst) },
            "Provider Failure Classifier": {},
          },
        }
      ),
    /SPECIALIST_INVALID_CONTRACT/
  );
  console.log("CASE H Malformed Specialist rejected: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
