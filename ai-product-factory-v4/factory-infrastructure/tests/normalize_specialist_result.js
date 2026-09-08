const x = $input.first().json || {};
const rawResponse = x.body ?? x;
function last(name) {
  try {
    return $(name).isExecuted ? $(name).last().json : null;
  } catch {
    return null;
  }
}
const previous = String($prevNode?.name || "Specialist");
const failoverState = last("Provider Failure Classifier");
const failoverUsed = previous !== "Specialist";
const primaryModel = "nvidia/nemotron-3-ultra-550b-a55b";
const fallbackModel = previous === "Specialist Groq Fallback" ? "openai/gpt-oss-120b" : "nvidia/nemotron-3.5-lightning-30b-a3b";
const raw = rawResponse?.choices?.[0]?.message?.content ?? rawResponse?.message?.content ?? rawResponse?.content;
if (typeof raw !== "string" || !raw.trim()) throw new Error("SPECIALIST_INVALID_CONTRACT: response missing content");
let clean = raw.replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/\x60{3}json/gi, "").replace(/\x60{3}/g, "").trim();
const a = clean.indexOf("{"),
  b = clean.lastIndexOf("}");
if (a < 0 || b < a) throw new Error("SPECIALIST_INVALID_CONTRACT: response is not JSON");
let parsed;
try {
  parsed = JSON.parse(clean.slice(a, b + 1));
} catch {
  throw new Error("SPECIALIST_INVALID_CONTRACT: malformed JSON");
}
const rawDirector = $("Director Sprint Input").item.json,
  director = rawDirector.body ?? rawDirector;
const analystRaw = $("Normalize Analyst Result").item.json.content;
let analyst = {};
try {
  analyst = JSON.parse(String(analystRaw));
} catch {
  throw new Error("SPECIALIST_INVALID_CONTRACT: normalized Analyst contract unavailable");
}
function resolveTargetProduct(d) {
  for (const key of ["product_identity", "product_name", "product"]) {
    const value = String(d?.[key] ?? "").trim();
    if (value) return value;
  }
  return "UNSPECIFIED_PRODUCT";
}
const targetProductIdentity = resolveTargetProduct(director);
const sprintGoal = String(director.objective || analyst.objective || "").trim();
const taskId = String(director.task_id || (director.sprint_id || "SPRINT-" + $execution.id) + ":SPECIALIST");
function arr(v) {
  return Array.isArray(v) ? v.map((y) => String(y).replace(/\s+/g, " ").trim()).filter(Boolean) : null;
}
function norm(v) {
  return String(v ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9\u00c0-\u024f\u1e00-\u1eff]+/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}
function constraintKey(v) {
  return norm(v).replace(/^(the|a|an) /, "");
}
function stringListOrThrow(value, field) {
  if (value == null || value === "") return [];
  if (!Array.isArray(value)) throw new Error("SPECIALIST_INVALID_CONTRACT: " + field + " must be an array");
  const out = [];
  for (const item of value) {
    if (typeof item !== "string") throw new Error("SPECIALIST_INVALID_CONTRACT: " + field + " must be an array of strings");
    if (item.trim()) out.push(item.replace(/\s+/g, " ").trim());
  }
  return out;
}
function inheritedList(fromDirector, fromAnalyst, field) {
  const out = [];
  const seen = new Set();
  for (const item of [...(Array.isArray(fromDirector[field]) ? fromDirector[field] : []), ...(Array.isArray(fromAnalyst[field]) ? fromAnalyst[field] : [])]) {
    const text = String(item).replace(/\s+/g, " ").trim();
    if (!text) continue;
    const key = constraintKey(text);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(text);
  }
  return out;
}
function reattachStructural(field, allowDerived) {
  const inherited = inheritedList(director, analyst, field);
  if (field === "constraints" && !inherited.length) throw new Error("SPECIALIST_INVALID_CONTRACT: inherited constraints missing");
  if (field === "acceptance_criteria" && !inherited.length) throw new Error("SPECIALIST_INVALID_CONTRACT: inherited acceptance criteria missing");
  const extras = stringListOrThrow(parsed[field], field);
  if (!allowDerived) return inherited;
  const seen = new Set(inherited.map(constraintKey));
  const out = inherited.slice();
  for (const item of extras) {
    const key = constraintKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}
stringListOrThrow(parsed.constraints, "constraints");
stringListOrThrow(parsed.acceptance_criteria, "acceptance_criteria");
stringListOrThrow(parsed.requirements, "requirements");
const requiredArrays = ["approved_scope", "known_facts", "unknowns", "planner_handoff"];
for (const key of requiredArrays) {
  const value = arr(parsed[key]);
  if (value === null) throw new Error("SPECIALIST_INVALID_CONTRACT: " + key + " must be an array");
  parsed[key] = value;
}
if (String(parsed.product_identity || "").trim() !== targetProductIdentity) throw new Error("SPECIALIST_INVALID_CONTRACT: product identity drift");
parsed.factory_identity = "AI Product Factory V4";
parsed.target_product_identity = targetProductIdentity;
if (!sprintGoal || norm(parsed.sprint_goal) !== norm(sprintGoal)) throw new Error("SPECIALIST_INVALID_CONTRACT: sprint goal drift");
if (String(parsed.task_id || "").trim() !== taskId) throw new Error("SPECIALIST_INVALID_CONTRACT: task identity drift");
parsed.mode = String(parsed.mode || "").toUpperCase();
if (!["UI", "BACKEND", "INTEGRATION"].includes(parsed.mode)) throw new Error("SPECIALIST_INVALID_CONTRACT: invalid mode");
parsed.status = String(parsed.status || "").toUpperCase();
if (!["READY", "NEEDS_REVIEW"].includes(parsed.status)) throw new Error("SPECIALIST_INVALID_CONTRACT: invalid status");
if (typeof parsed.blocker !== "string") throw new Error("SPECIALIST_INVALID_CONTRACT: blocker must be a string");
parsed.constraints = reattachStructural("constraints", false);
parsed.acceptance_criteria = reattachStructural("acceptance_criteria", true);
parsed.requirements = reattachStructural("requirements", true);
const expectedScope = [...(Array.isArray(director.approved_scope) ? director.approved_scope : []), ...(Array.isArray(director.scope) ? director.scope : []), ...(Array.isArray(analyst.scope) ? analyst.scope : [])].map(String).filter(Boolean);
const missingScope = expectedScope.filter((v) => !parsed.approved_scope.some((x) => norm(x) === norm(v)));
if (missingScope.length) throw new Error("SPECIALIST_INVALID_CONTRACT: approved scope drift: " + missingScope.join(" | ").slice(0, 500));
const claims = [...parsed.known_facts, ...parsed.planner_handoff].join(" ");
const forbidden = /(i|we)\s+(implemented|modified|deployed|committed|pushed|ran\s+(the\s+)?tests?|executed\s+(the\s+)?tests?)|tests?\s+(were\s+)?(run|executed|passed)|files?\s+(were\s+)?(modified|created|deleted)/i;
if (forbidden.test(claims)) throw new Error("SPECIALIST_INVALID_CONTRACT: fabricated implementation or test claim");
parsed.specialist_primary_model = primaryModel;
parsed.specialist_selected_model = failoverUsed ? fallbackModel : primaryModel;
parsed.specialist_failover_used = failoverUsed;
parsed.specialist_attempt_count = failoverUsed ? 2 : 1;
parsed.specialist_provider_failures = Array.isArray(failoverState?.provider_failures) ? failoverState.provider_failures.filter((f) => f.role === "SPECIALIST") : [];
parsed.validation = { contract_version: "SPECIALIST_V4.3", missing_constraints: [], missing_acceptance_criteria: [], unknown_count: parsed.unknowns.length, fail_closed: true };
const project_id = x.project_id ?? director.project_id;
const work_unit_id = x.work_unit_id ?? director.work_unit_id;
return [{ json: { ...x, content: JSON.stringify(parsed), specialist_report: parsed, raw_specialist_response: rawResponse, specialist_primary_model: parsed.specialist_primary_model, specialist_selected_model: parsed.specialist_selected_model, specialist_failover_used: parsed.specialist_failover_used, specialist_attempt_count: parsed.specialist_attempt_count, specialist_provider_failures: parsed.specialist_provider_failures, project_id, work_unit_id, constraints: parsed.constraints, acceptance_criteria: parsed.acceptance_criteria } }];
