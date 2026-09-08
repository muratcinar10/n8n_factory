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

const dispatcher = workflow.nodes.find((n) => n.name === "Developer Dispatcher");
const recovery = workflow.nodes.find((n) => n.name === "Recovery Controller");
const normalize = workflow.nodes.find((n) => n.name === "Normalize Technical Failure");
const router = workflow.nodes.find((n) => n.name === "Developer Router");
const routerKeys = router.parameters.rules.values.map((r) => r.outputKey);
const minimax = workflow.nodes.find((n) => n.name === "MiniMax Developer");
const laguna = workflow.nodes.find((n) => n.name === "Laguna Developer");

function dest(route) {
  return workflow.connections["Developer Router"].main[routerKeys.indexOf(route)][0].node;
}

assert.equal(dest("CODEX"), "Codex Executor");
assert.equal(dest("MINIMAX"), "MiniMax Developer");
assert.equal(dest("LAGUNA"), "Laguna Developer");
assert.equal(minimax.parameters.jsonBody.includes("minimax/minimax-m3:free"), true);
assert.equal(laguna.parameters.jsonBody.includes("poolside/laguna-s-2.1:free"), true);
assert.equal(workflow.connections["MiniMax Developer"].main[0][0].node, "Normalize Non-Codex Developer Result");
assert.equal(workflow.connections["Laguna Developer"].main[0][0].node, "Normalize Non-Codex Developer Result");

const recoveryKeys = workflow.nodes.find((n) => n.name === "Recovery Router").parameters.rules.values.map((r) => r.outputKey);
assert.equal(recoveryKeys[1], "TRY_ALTERNATE_DEVELOPER");
assert.equal(workflow.connections["Recovery Router"].main[1][0].node, "Developer Dispatcher");
assert.equal(workflow.connections["Laguna Developer"].main[1][0].node, "Laguna Failure Context");

const candidates = [
  { rank: 1, slot: "PRIMARY", provider: "CODEX", configured: false },
  { rank: 2, slot: "FALLBACK_1", provider: "MINIMAX", configured: true },
  { rank: 3, slot: "FALLBACK_2", provider: "LAGUNA", configured: true },
];

function task() {
  return {
    task_id: "T01",
    objective: "Create the Hangman shell",
    status: "IN_PROGRESS",
    attempt: 1,
    underlying_task_key: "T01:create the hangman shell",
    dependencies: [],
    approaches_tried: [],
    errors: [],
    acceptance_criteria: ["visible shell"],
    test_requirements: ["renders"],
  };
}

function baseState(overrides = {}) {
  const current = task();
  return {
    sprint_id: "PROJECT-HANGMAN-PILOT-001-W001-1",
    objective: "Create the Hangman shell",
    project_id: "PROJECT-HANGMAN-PILOT-001",
    work_unit_id: "W001",
    requirements: ["r"],
    constraints: ["c"],
    acceptance_criteria: ["a"],
    tasks: [current],
    current_task_id: "T01",
    current_task: current,
    developer_candidates: candidates,
    developer_exclusions: ["CODEX"],
    developer_attempt_history: [
      {
        task_id: "T01",
        underlying_task_key: current.underlying_task_key,
        attempt: 1,
        provider: "MINIMAX",
        status: "DISPATCHED",
        meaningful_attempt: true,
      },
    ],
    developer_failure_history: [],
    developer_failovers: 0,
    technical_failures: [],
    REVIEW_BACKLOG: [],
    DEFERRED_BLOCKERS: [],
    CODEX_AUDIT_QUEUE: [],
    SPRINT_HISTORY: [],
    completed_task_ids: [],
    changed_files: [],
    ...overrides,
  };
}

async function run(node, incoming, prevName, extras = {}) {
  const context = {
    $input: { first: () => ({ json: incoming }) },
    $prevNode: { name: prevName },
    $execution: { id: "99" },
    $: (name) => ({
      isExecuted: Boolean(extras[name]),
      last: () => ({ json: extras[name] || {} }),
      item: { json: extras[name] || {} },
    }),
  };
  return (await new vm.Script(`(async()=>{${node.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

(async () => {
  const analystContent = JSON.stringify({
    objective: "x",
    scope: ["x"],
    requirements: ["r"],
    constraints: ["c"],
    acceptance_criteria: ["a"],
    known_context: ["k"],
    status: "READY",
    blocker: "NONE",
  });
  const specialistContent = JSON.stringify({
    mode: "UI",
    requirements: ["r"],
    constraints: ["c"],
    acceptance_criteria: ["a"],
    known_context: ["k"],
  });
  const plannerContent = JSON.stringify({
    tasks: [{ task_id: "T01", objective: "Create the Hangman shell", expected_result: "shell", acceptance_criteria: ["a"], test_requirements: ["t"] }],
  });
  const consistencyContent = JSON.stringify({ status: "MATCH", valid_task_ids: ["T01"] });

  const extrasFirst = {
    "Director Sprint Input": { body: { sprint_id: "S1", objective: "Create the Hangman shell", project_id: "PROJECT-HANGMAN-PILOT-001", work_unit_id: "W001", codex_available: true, minimax_available: true, laguna_available: true } },
    "Normalize Analyst Result": { content: analystContent },
    "Normalize Specialist Result": { content: specialistContent },
    "Normalize Planner Result": { content: plannerContent },
  };

  const codexOk = await run(dispatcher, { content: consistencyContent }, "Normalize Consistency Result", extrasFirst);
  assert.equal(codexOk.developer_route, "CODEX");
  assert.notEqual(codexOk.developer_route, "MINIMAX");
  assert.notEqual(codexOk.developer_route, "LAGUNA");

  const extrasNoCodex = {
    ...extrasFirst,
    "Director Sprint Input": { body: { sprint_id: "S1", objective: "Create the Hangman shell", project_id: "PROJECT-HANGMAN-PILOT-001", work_unit_id: "W001", codex_available: false, minimax_available: true, laguna_available: true } },
  };
  const minimaxOk = await run(dispatcher, { content: consistencyContent }, "Normalize Consistency Result", extrasNoCodex);
  assert.equal(minimaxOk.developer_route, "MINIMAX");
  assert.notEqual(minimaxOk.developer_route, "LAGUNA");
  assert.deepEqual(minimaxOk.acceptance_criteria || minimaxOk.current_task.acceptance_criteria, ["a"]);

  const notFound = await run(
    normalize,
    {
      error: "This model is unavailable for free. The paid version is available now - use this slug instead: minimax/minimax-m3",
      _failure_source: "MiniMax",
      _failure_role: "DEVELOPER",
      _failure_provider: "MINIMAX",
      _provider_failure: {
        failure_class: "PROVIDER_NOT_FOUND",
        availability_failure: false,
        human_authority_required: true,
        sanitized_error: "This model is unavailable for free.",
        http_status: 404,
      },
      _authoritative_state: baseState({ developer_route: "MINIMAX" }),
    },
    "MiniMax Failure Context"
  );
  assert.equal(notFound.failure_type, "MODEL_UNAVAILABLE");
  assert.equal(notFound.safe_to_retry, true);
  assert.equal(notFound.configuration_failure, false);
  assert.equal(notFound.developer_route, "MINIMAX");

  const afterNotFound = await run(recovery, { ...baseState({ developer_route: "MINIMAX" }), technical_failure_record: notFound.technical_failure_record }, "Normalize Technical Failure");
  assert.equal(afterNotFound.recovery_action, "TRY_ALTERNATE_DEVELOPER");
  assert.equal(afterNotFound.developer_route, "LAGUNA");
  assert.equal(afterNotFound.provider_chain_exhausted, false);
  assert.deepEqual(afterNotFound.developer_exclusions, ["CODEX", "MINIMAX"]);
  assert.equal(afterNotFound.tasks[0].status, "RETRY");

  const timeoutNorm = await run(
    normalize,
    {
      error: "timeout of 300000ms exceeded",
      _failure_source: "MiniMax",
      _failure_role: "DEVELOPER",
      _failure_provider: "MINIMAX",
      _provider_failure: { failure_class: "PROVIDER_TIMEOUT", availability_failure: true, human_authority_required: false, sanitized_error: "300000ms timeout" },
      _authoritative_state: baseState({ developer_route: "MINIMAX" }),
    },
    "MiniMax Failure Context"
  );
  const afterTimeout = await run(recovery, { ...baseState({ developer_route: "MINIMAX" }), technical_failure_record: timeoutNorm.technical_failure_record }, "Normalize Technical Failure");
  assert.equal(afterTimeout.recovery_action, "TRY_ALTERNATE_DEVELOPER");
  assert.equal(afterTimeout.developer_route, "LAGUNA");

  const exhaustedState = baseState({
    developer_exclusions: ["CODEX", "MINIMAX"],
    developer_attempt_history: [
      { task_id: "T01", underlying_task_key: "T01:create the hangman shell", provider: "LAGUNA", status: "DISPATCHED", meaningful_attempt: true },
    ],
    developer_candidates: candidates.map((c) => ({ ...c, configured: c.provider === "LAGUNA" })),
  });
  const lagunaFail = await run(recovery, {
    ...exhaustedState,
    technical_failure_record: {
      failed_role: "DEVELOPER",
      failure_type: "PROVIDER_NOT_FOUND",
      developer_route: "LAGUNA",
      task_id: "T01",
      underlying_task_key: "T01:create the hangman shell",
      safe_to_retry: true,
      message: "Laguna unavailable",
    },
  }, "Normalize Technical Failure");
  assert.equal(lagunaFail.recovery_action, "CONTINUE_NEXT_TASK");
  assert.equal(lagunaFail.provider_chain_exhausted, true);
  assert.equal(lagunaFail.developer_route, "SPRINT_END");
  assert(lagunaFail.developer_exclusions.includes("LAGUNA"));

  const falseExhaust = await run(recovery, { ...baseState({ developer_route: "MINIMAX" }), technical_failure_record: notFound.technical_failure_record }, "Normalize Technical Failure");
  assert.notEqual(falseExhaust.recovery_action, "END_SPRINT");
  assert.notEqual(falseExhaust.developer_route, "SPRINT_END");
  assert.equal(falseExhaust.developer_route, "LAGUNA");

  const lagunaDispatch = await run(dispatcher, afterNotFound, "Recovery Controller", extrasNoCodex);
  assert.equal(lagunaDispatch.developer_route, "LAGUNA");
  assert.equal(lagunaDispatch.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(lagunaDispatch.work_unit_id, "W001");
  assert.deepEqual(lagunaDispatch.requirements, ["r"]);
  assert.deepEqual(lagunaDispatch.constraints, ["c"]);
  assert.deepEqual(lagunaDispatch.acceptance_criteria, ["a"]);

  const firstMeta = await run(dispatcher, { content: consistencyContent }, "Normalize Consistency Result", extrasNoCodex);
  assert.equal(firstMeta.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(firstMeta.work_unit_id, "W001");
  assert.deepEqual(firstMeta.requirements, ["r"]);
  assert.deepEqual(firstMeta.constraints, ["c"]);
  assert.deepEqual(firstMeta.acceptance_criteria, ["a"]);

  const nonCodex = workflow.nodes.find((n) => n.name === "Normalize Non-Codex Developer Result");
  const proposal = {
    task_completed: true,
    status: "COMPLETED_PROPOSAL",
    summary: "shell",
    target_files: ["index.html"],
    proposed_changes: [],
    tests: [],
    evidence: ["ok"],
    assumptions: [],
    unverified: [],
    constraint_compliance: ["c"],
  };
  const preserved = await run(
    nonCodex,
    {
      project_id: "PROJECT-HANGMAN-PILOT-001",
      work_unit_id: "W001",
      requirements: ["r"],
      constraints: ["c"],
      acceptance_criteria: ["a"],
      body: { choices: [{ message: { content: JSON.stringify(proposal) } }] },
    },
    "Laguna Developer",
    { "Developer Dispatcher": lagunaDispatch }
  );
  assert.equal(preserved.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(preserved.work_unit_id, "W001");
  assert.deepEqual(preserved.requirements, ["r"]);
  assert.deepEqual(preserved.constraints, ["c"]);
  assert.deepEqual(preserved.acceptance_criteria, ["a"]);
  assert.equal(preserved.selected_developer, "LAGUNA");

  console.log("A Codex success: PASS");
  console.log("B Codex unavailable MiniMax success path: PASS");
  console.log("C MiniMax PROVIDER_NOT_FOUND Laguna: PASS");
  console.log("D MiniMax timeout Laguna: PASS");
  console.log("E all Developers unavailable exhaustion: PASS");
  console.log("F false chain-exhaustion prevention: PASS");
  console.log("G Developer metadata preservation: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
