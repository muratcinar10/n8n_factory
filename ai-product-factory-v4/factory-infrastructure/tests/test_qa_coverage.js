const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const {
  auditQaCoverage,
} = require("./qa_coverage_audit");

const workflow = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json"), "utf8")
);
assert.equal(workflow.nodes.length, 64);
const qaGate = workflow.nodes.find((n) => n.name === "Deterministic QA Gate").parameters.jsCode;
const leadCode = workflow.nodes.find((n) => n.name === "Normalize QA Lead Result").parameters.jsCode;
const leadPrompt = workflow.nodes.find((n) => n.name === "QA Lead").parameters.jsonBody;
assert(qaGate.includes("coverage_matrix"));
assert(qaGate.includes("buildCoverageMatrix"));
assert(qaGate.includes("NON_CODEX_PROPOSAL_NOT_APPLIED"));
assert(leadCode.includes("auditQaCoverage"));
assert(leadCode.includes("qa_passes_rejected_for_insufficient_evidence"));
assert(leadPrompt.includes("ANALYST_CONTRACT"));
assert(leadPrompt.includes("Do not reinterpret Analyst requirements"));
assert(!leadPrompt.includes("exactly 6 lives"));

function dispatcherWith(criterion) {
  return {
    current_task_id: "T01",
    current_task: {
      task_id: "T01",
      objective: "Verify Hangman lives",
      acceptance_criteria: [criterion],
    },
    tasks: [
      {
        task_id: "T01",
        objective: "Verify Hangman lives",
        acceptance_criteria: [criterion],
      },
    ],
    analyst_report: {
      requirements: [criterion],
      acceptance_criteria: [criterion],
      constraints: [criterion],
    },
  };
}

function approveAll() {
  return {
    task_id: "T01",
    status: "QA_APPROVED",
    missing_acceptance_criteria: [],
    missing_edge_cases: [],
    missing_tests: [],
    weak_evidence: [],
    reason: "LLM attempted to approve from Developer claims.",
  };
}

async function runNormalize(llmJson, qaResult, dispatcher) {
  const input = { choices: [{ message: { content: JSON.stringify(llmJson) } }] };
  const context = {
    $input: { first: () => ({ json: input }) },
    $: (name) => {
      if (name === "Deterministic QA Gate") {
        return {
          item: {
            json: {
              qa_result: qaResult,
              content: JSON.stringify(qaResult),
              developer_evidence_envelope: { acceptance_criteria: dispatcher.current_task.acceptance_criteria },
              _sprint_state: dispatcher,
            },
          },
        };
      }
      if (name === "Developer Dispatcher") return { item: { json: dispatcher } };
      throw new Error("unexpected node " + name);
    },
  };
  const result = await new vm.Script(`(async()=>{${leadCode}})()`).runInNewContext(context);
  return result[0].json.qa_lead_result;
}

function matrixRow(overrides) {
  return Object.assign(
    {
      id: "TASK-AC-1",
      source: "TASK_ACCEPTANCE",
      criterion: "Every new round must start with exactly 5 lives.",
      in_current_task_scope: true,
      developer_claim: "I implemented exactly 5 lives.",
      qa_test: null,
      observed_result: null,
      evidence: [],
      qa_verdict: "NOT_TESTED",
      discriminating: false,
    },
    overrides
  );
}

(async () => {
  const lives = "The Hangman game must have exactly 5 lives.";
  const dup = "Duplicate wrong guesses must not consume another life.";

  const a = await runNormalize(
    approveAll(),
    {
      verdict: "PASS",
      coverage_matrix: [
        matrixRow({
          criterion: lives,
          qa_test: "Made four incorrect guesses. The game was still running.",
          observed_result: "game still running after 4 wrong guesses",
          evidence: ["four wrong guesses, still active"],
          qa_verdict: "PASS",
        }),
      ],
    },
    dispatcherWith(lives)
  );
  assert.equal(a.status, "QA_INCOMPLETE");
  assert.equal(a.final_verdict, "QA_INCOMPLETE");
  assert.equal(a.coverage_complete, false);
  assert(a.weak_tests.length >= 1 || a.qa_passes_rejected_for_insufficient_evidence.length >= 1);

  const bAudit = auditQaCoverage(
    {
      verdict: "PASS",
      coverage_matrix: [
        matrixRow({
          criterion: lives,
          qa_test: "Wrong unique guesses through the fifth-life boundary",
          observed_result: { lives_sequence: [5, 4, 3, 2, 1, 0], outcome: "LOSS" },
          evidence: ["initial 5", "fifth unique wrong guess reached 0 + LOSS"],
          qa_verdict: "PASS",
          discriminating: true,
        }),
      ],
    },
    dispatcherWith(lives)
  );
  assert.equal(bAudit.mandatory_criteria_adequately_tested, 1);
  const b = await runNormalize(
    approveAll(),
    {
      verdict: "PASS",
      coverage_matrix: [
        matrixRow({
          criterion: lives,
          qa_test: "Wrong unique guesses through the fifth-life boundary",
          observed_result: { lives_sequence: [5, 4, 3, 2, 1, 0], outcome: "LOSS" },
          evidence: ["initial 5", "fifth unique wrong guess reached 0 + LOSS"],
          qa_verdict: "PASS",
          discriminating: true,
        }),
      ],
    },
    dispatcherWith(lives)
  );
  assert.equal(b.coverage_complete, true);
  assert.equal(b.status, "QA_APPROVED");
  assert.equal(b.final_verdict, "QA_APPROVED");

  const c = await runNormalize(
    approveAll(),
    {
      verdict: "PASS",
      coverage_matrix: [
        matrixRow({
          id: "TASK-AC-1",
          criterion: dup,
          qa_test: "Pressed A twice. A appeared only once on screen.",
          observed_result: "letter A visually appeared once",
          evidence: ["duplicate key visually appears once"],
          qa_verdict: "PASS",
        }),
      ],
    },
    dispatcherWith(dup)
  );
  assert.equal(c.status, "QA_INCOMPLETE");
  assert.equal(c.final_verdict, "QA_INCOMPLETE");

  const d = await runNormalize(
    approveAll(),
    {
      verdict: "PASS",
      coverage_matrix: [
        matrixRow({
          criterion: dup,
          qa_test: "Observe lives before first wrong A, after first wrong A, and after duplicate wrong A",
          observed_result: { before_duplicate: 5, after_first_wrong: 4, after_duplicate: 4 },
          evidence: ["before=5", "first wrong=4", "duplicate still=4"],
          qa_verdict: "PASS",
          discriminating: true,
        }),
      ],
    },
    dispatcherWith(dup)
  );
  assert.equal(d.coverage_complete, true);
  assert.equal(d.mandatory_criteria_adequately_tested, 1);
  assert.equal(d.status, "QA_APPROVED");

  const e = await runNormalize(
    approveAll(),
    {
      verdict: "NOT_VERIFIED",
      coverage_matrix: [
        matrixRow({
          criterion: lives,
          developer_claim: "all acceptance criteria implemented",
          qa_test: null,
          observed_result: null,
          evidence: [],
          qa_verdict: "NOT_TESTED",
        }),
      ],
    },
    dispatcherWith(lives)
  );
  assert.equal(e.status, "QA_INCOMPLETE");
  assert.equal(e.final_verdict, "QA_INCOMPLETE");
  assert(e.developer_claims_without_sufficient_qa.length >= 1 || e.untested_criteria.length >= 1);

  const f = await runNormalize(
    approveAll(),
    {
      verdict: "PASS",
      coverage_matrix: [
        matrixRow({
          criterion: lives,
          qa_test: "Developer said 5 lives so QA labeled PASS",
          observed_result: "no observable boundary test",
          evidence: ["developer claim only"],
          qa_verdict: "PASS",
        }),
      ],
    },
    dispatcherWith(lives)
  );
  assert.equal(f.status, "QA_INCOMPLETE");
  assert(f.qa_passes_rejected_for_insufficient_evidence.length >= 1);

  const g = await runNormalize(
    {
      task_id: "T01",
      status: "QA_APPROVED",
      missing_acceptance_criteria: [],
      missing_edge_cases: [],
      missing_tests: [],
      weak_evidence: [],
      reason: "Should perhaps have 6 lives instead of 5.",
    },
    {
      verdict: "PASS",
      coverage_matrix: [
        matrixRow({
          criterion: lives,
          qa_test: "Wrong unique guesses through the fifth-life boundary",
          observed_result: { lives_sequence: [5, 4, 3, 2, 1, 0], outcome: "LOSS" },
          evidence: ["0 + LOSS on fifth unique wrong guess"],
          qa_verdict: "PASS",
          discriminating: true,
        }),
      ],
    },
    dispatcherWith(lives)
  );
  assert(!/6 lives/.test(JSON.stringify(g.coverage_matrix.map((row) => row.criterion))));
  assert.equal(g.coverage_matrix[0].criterion, lives);
  assert(!/should perhaps have 6/.test(String(g.reason).toLowerCase()));
  assert.equal(g.analyst_requirement_preserved, true);

  console.log("QA coverage contract fixtures: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
