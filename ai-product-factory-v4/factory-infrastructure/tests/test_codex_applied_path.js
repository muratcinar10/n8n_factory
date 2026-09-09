const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const workflow = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json"), "utf8")
);
assert.equal(workflow.nodes.length, 64);
const b04 = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Codex-Executor-B04.json"), "utf8"));
assert.equal(b04.id, "y5gVRIcXPvXoTQic");
const executor = workflow.nodes.find((n) => n.name === "Codex Executor");
assert.equal(executor.parameters.workflowId.value, "y5gVRIcXPvXoTQic");

const dispatcher = workflow.nodes.find((n) => n.name === "Developer Dispatcher");
const qaGate = workflow.nodes.find((n) => n.name === "Deterministic QA Gate");
const normalizeCursor = workflow.nodes.find((n) => n.name === "Normalize Cursor Developer Result");
const safety = b04.nodes.find((n) => n.name === "Codex Task Safety Gate");
const buildJob = b04.nodes.find((n) => n.name === "Build Host Writer Job");
assert(buildJob.parameters.jsCode.includes("PRODUCTION:'/data/host-writer'"));
assert(buildJob.parameters.jsCode.includes("SMOKE_FIXTURE:'/data/smoke-host-writer'"));
assert(buildJob.parameters.jsCode.includes("job_path:`${queueRoot}/inbox/${id}.json`"));
assert(dispatcher.parameters.jsCode.includes("writer_target:String(director.writer_target"));
assert(qaGate.parameters.jsCode.includes("implementation_applied:['CODEX','CURSOR'].includes(developer)"));
assert(qaGate.parameters.jsCode.includes("NON_CODEX_PROPOSAL_NOT_APPLIED"));

async function run(node, incoming, prevName, extras = {}) {
  const context = {
    $input: { first: () => ({ json: incoming }) },
    $prevNode: { name: prevName },
    $execution: { id: "103" },
    $: (name) => ({
      isExecuted: Boolean(extras[name]),
      last: () => ({ json: extras[name] || {} }),
      item: { json: extras[name] || {} },
    }),
  };
  return (await new vm.Script(`(async()=>{${node.parameters.jsCode}})()`).runInNewContext(context))[0].json;
}

function planningExtras(body) {
  return {
    "Director Sprint Input": { body },
    "Normalize Analyst Result": {
      content: JSON.stringify({
        objective: "Create the Hangman shell",
        scope: ["index.html"],
        requirements: ["visible shell"],
        constraints: ["c"],
        acceptance_criteria: ["visible shell"],
        known_context: ["k"],
        status: "READY",
        blocker: "NONE",
      }),
    },
    "Normalize Specialist Result": {
      content: JSON.stringify({
        mode: "UI",
        requirements: ["visible shell"],
        constraints: ["c"],
        acceptance_criteria: ["visible shell"],
        known_context: ["k"],
        approved_scope: ["index.html"],
      }),
    },
    "Normalize Planner Result": {
      content: JSON.stringify({
        tasks: [
          {
            task_id: "T01",
            objective: "Create the Hangman shell",
            expected_result: "shell",
            acceptance_criteria: ["visible shell"],
            test_requirements: ["renders"],
          },
        ],
      }),
    },
  };
}

const hangmanBody = {
  sprint_id: "PROJECT-HANGMAN-PILOT-001-W001-4",
  objective: "Create the Hangman shell",
  project_id: "PROJECT-HANGMAN-PILOT-001",
  work_unit_id: "W001",
  writer_target: "PRODUCTION",
  requested_target: "PRODUCTION",
  cursor_available: true,
  codex_available: true,
  minimax_available: false,
  laguna_available: true,
};

(async () => {
  const consistency = { content: JSON.stringify({ status: "MATCH", valid_task_ids: ["T01"] }) };

  const approvedCursor = await run(dispatcher, consistency, "Normalize Consistency Result", planningExtras(hangmanBody));
  assert.equal(approvedCursor.developer_route, "CURSOR");
  assert.notEqual(approvedCursor.developer_route, "CODEX");
  assert.notEqual(approvedCursor.developer_route, "MINIMAX");
  assert.notEqual(approvedCursor.developer_route, "LAGUNA");
  assert.equal(approvedCursor.writer_target, "PRODUCTION");
  assert.equal(approvedCursor.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(approvedCursor.work_unit_id, "W001");
  console.log("A Approved project Cursor primary: PASS");

  const approved = await run(
    dispatcher,
    consistency,
    "Normalize Consistency Result",
    planningExtras({ ...hangmanBody, cursor_available: false, minimax_available: true })
  );
  assert.equal(approved.developer_route, "CODEX");
  assert.notEqual(approved.developer_route, "MINIMAX");
  assert.notEqual(approved.developer_route, "LAGUNA");
  assert.equal(approved.codex_available, true);
  assert.equal(approved.writer_target, "PRODUCTION");
  assert.equal(approved.project_id, "PROJECT-HANGMAN-PILOT-001");
  assert.equal(approved.work_unit_id, "W001");
  console.log("C Codex second selection: PASS");

  const blockedTarget = await run(safety, {
    ...approved,
    writer_target: "/tmp/arbitrary",
    director_brief: { ...hangmanBody, writer_target: "/tmp/arbitrary" },
    current_task: approved.current_task,
    codex_available: true,
  }, "Codex Task Input");
  assert.equal(blockedTarget.child_route, "BLOCKED");
  assert.match(blockedTarget.child_block_reason, /PRODUCTION or SMOKE_FIXTURE/);
  const blockedMissing = await run(safety, {
    current_task: { task_id: "T01", objective: "x", expected_result: "y" },
    writer_target: "PRODUCTION",
    codex_available: true,
  }, "Codex Task Input");
  assert.equal(blockedMissing.child_route, "SAFE");
  const refused = await run(safety, {
    current_task: { task_id: "T01", objective: "x", expected_result: "y" },
    writer_target: "CUSTOM_REPO",
    director_brief: { writer_target: "CUSTOM_REPO" },
    codex_available: true,
  }, "Codex Task Input");
  assert.equal(refused.child_route, "BLOCKED");
  const noCodexFlag = await run(safety, {
    current_task: { task_id: "T01", objective: "x", expected_result: "y" },
    writer_target: "PRODUCTION",
    codex_available: false,
  }, "Codex Task Input");
  assert.equal(noCodexFlag.child_route, "BLOCKED");
  const productionOk = await run(safety, {
    current_task: approved.current_task,
    writer_target: "PRODUCTION",
    codex_available: true,
  }, "Codex Task Input");
  assert.equal(productionOk.child_route, "SAFE");
  console.log("B Unapproved target refused: PASS");

  const unavailable = await run(
    dispatcher,
    consistency,
    "Normalize Consistency Result",
    planningExtras({ ...hangmanBody, cursor_available: false, codex_available: false, minimax_available: true })
  );
  assert.equal(unavailable.developer_route, "LAGUNA");
  assert.notEqual(unavailable.developer_route, "CODEX");
  assert.notEqual(unavailable.developer_route, "MINIMAX");
  console.log("E Cursor and Codex unavailable Laguna fallback: PASS");

  const cursorIncoming = {
    ...approvedCursor,
    executor_kind: "CURSOR",
    execution_status: "SUCCESS",
    ok: true,
    task_completed: true,
    cursor_exit_code: 0,
    implementation_applied: true,
    changed_files: ["index.html"],
    new_files: [],
    deleted_files: [],
    summary: "Created the Hangman shell.",
    tests: ["renders"],
    test_outputs: ["0"],
    runtime_evidence: ["index.html contains an HTML document"],
  };
  const qaCursor = await run(qaGate, cursorIncoming, "Normalize Cursor Developer Result", {
    "Developer Dispatcher": approvedCursor,
    "Normalize Non-Codex Developer Result": {},
  });
  assert.equal(qaCursor.qa_result.developer, "CURSOR");
  assert.equal(qaCursor.qa_result.developer_completed, true);
  assert.equal(qaCursor.qa_result.implementation_applied, true);
  assert.deepEqual(qaCursor.qa_result.changed_files, ["index.html"]);
  assert(!qaCursor.qa_result.missing_evidence.includes("NON_CODEX_PROPOSAL_NOT_APPLIED"));
  console.log("D Cursor applied evidence reaches QA: PASS");

  const recoveredCursor = await run(
    normalizeCursor,
    {
      implementation_applied: false,
      changed_files: [],
      cursor_exit_code: 0,
      tests_executed: ["npm test"],
      test_exit_codes: [0],
      tests_passed: true,
      runtime_checks: ["index.html contains an HTML document"],
      developer_summary: "no new diff",
      failure_class: null,
      recovery_verification: {
        eligible: true,
        mode: "PRIOR_APPLY_VERIFICATION",
        prior_execution_id: "113",
        prior_implementation_applied: true,
        prior_changed_files: ["app.js", "index.html", "package.json"],
        workspace_fingerprint_matched: true,
      },
    },
    "Cursor Developer",
    { "Developer Dispatcher": approvedCursor }
  );
  assert.equal(recoveredCursor.implementation_applied, false);
  assert.equal(recoveredCursor.recovery_verification.prior_execution_id, "113");
  assert.deepEqual(recoveredCursor.changed_files, ["app.js", "index.html", "package.json"]);
  assert.equal(recoveredCursor.execution_status, "SUCCESS");
  const qaRecovered = await run(qaGate, recoveredCursor, "Normalize Cursor Developer Result", {
    "Developer Dispatcher": approvedCursor,
    "Normalize Non-Codex Developer Result": {},
  });
  assert(qaRecovered.qa_result.missing_evidence.includes("CURSOR_NO_APPLIED_CHANGE"));
  assert.notEqual(qaRecovered.qa_result.verdict, "PASS");
  console.log("D Cursor recovery verification does not auto-pass QA: PASS");

  const appliedIncoming = {
    ...approved,
    executor_kind: "CODEX",
    execution_status: "SUCCESS",
    ok: true,
    task_completed: true,
    returncode: 0,
    acceptance_status: "awaiting_acceptance",
    audit_recorded: true,
    changed_files: ["index.html"],
    new_files: [],
    deleted_files: [],
    summary: "Created the Hangman shell.",
    tests: ["renders"],
    test_outputs: ["ok"],
    runtime_evidence: ["served locally"],
    acceptance_criterion_results: [{ criterion: "visible shell", status: "PASS", evidence: ["index.html exists"] }],
  };
  const qaApplied = await run(qaGate, appliedIncoming, "Codex Executor", {
    "Developer Dispatcher": approved,
    "Normalize Non-Codex Developer Result": {},
  });
  assert.equal(qaApplied.qa_result.developer, "CODEX");
  assert.equal(qaApplied.qa_result.developer_completed, true);
  assert.equal(qaApplied.qa_result.implementation_applied, true);
  assert.deepEqual(qaApplied.qa_result.changed_files, ["index.html"]);
  assert(!qaApplied.qa_result.missing_evidence.includes("NON_CODEX_PROPOSAL_NOT_APPLIED"));
  console.log("D Codex applied evidence reaches QA: PASS");

  const proposalIncoming = {
    ...unavailable,
    developer_route: "LAGUNA",
    developer_result: {
      task_completed: true,
      status: "COMPLETED_PROPOSAL",
      summary: "implemented hangman shell",
      target_files: ["index.html"],
      proposed_changes: [{ path: "index.html", content: "<html/>" }],
      tests: ["renders"],
      evidence: ["I implemented the shell"],
      unverified: [],
      constraint_compliance: ["c"],
    },
  };
  const qaProposal = await run(qaGate, proposalIncoming, "Normalize Non-Codex Developer Result", {
    "Developer Dispatcher": unavailable,
    "Normalize Non-Codex Developer Result": proposalIncoming,
  });
  assert.equal(qaProposal.qa_result.developer, "LAGUNA");
  assert.equal(qaProposal.qa_result.implementation_applied, false);
  assert(qaProposal.qa_result.missing_evidence.some((item) => item === "NON_CODEX_PROPOSAL_NOT_APPLIED"));
  assert.notEqual(qaProposal.qa_result.verdict, "PASS");
  console.log("F Non-Codex proposal cannot fake applied: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
