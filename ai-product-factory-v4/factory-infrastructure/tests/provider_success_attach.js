const x = $input.first().json || {};
function last(name) {
  try {
    return $(name).isExecuted ? $(name).last().json : null;
  } catch {
    return null;
  }
}
const CANONICAL = ["ANALYST", "SPECIALIST", "PLANNER", "CONSISTENCY_REVIEWER", "QA_LEAD"];
const NODE_TO_ROLE = {
  Analyst: "ANALYST",
  "Analyst NVIDIA Fallback": "ANALYST",
  "Analyst Groq Fallback": "ANALYST",
  "Normalize Analyst Result": "ANALYST",
  Specialist: "SPECIALIST",
  "Specialist Fallback": "SPECIALIST",
  "Specialist Groq Fallback": "SPECIALIST",
  "Normalize Specialist Result": "SPECIALIST",
  Planner: "PLANNER",
  "Planner NVIDIA Alternate": "PLANNER",
  "Planner Groq Fallback": "PLANNER",
  "Normalize Planner Result": "PLANNER",
  "Consistency Reviewer": "CONSISTENCY_REVIEWER",
  "Consistency NVIDIA Alternate": "CONSISTENCY_REVIEWER",
  "Consistency Groq Fallback": "CONSISTENCY_REVIEWER",
  "Normalize Consistency Result": "CONSISTENCY_REVIEWER",
  "QA Lead": "QA_LEAD",
  "QA Lead NVIDIA Alternate": "QA_LEAD",
  "QA Lead Groq Fallback": "QA_LEAD",
  "Normalize QA Lead Result": "QA_LEAD",
};
const NEXT_ROLE = {
  ANALYST: "SPECIALIST",
  SPECIALIST: "PLANNER",
  PLANNER: "CONSISTENCY_REVIEWER",
  CONSISTENCY_REVIEWER: null,
  QA_LEAD: null,
};
const PRIMARY = {
  ANALYST: { provider: "OLLAMA_LOCAL", model: "qwen3:8b", node: "Analyst" },
  SPECIALIST: { provider: "NVIDIA", model: "nvidia/nemotron-3-ultra-550b-a55b", node: "Specialist" },
  PLANNER: { provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b", node: "Planner" },
  CONSISTENCY_REVIEWER: { provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b", node: "Consistency Reviewer" },
  QA_LEAD: { provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b", node: "QA Lead" },
};
const chain = last("Provider Chain Entry") || {};
const dispatch = last("Provider Dispatch Router") || {};
const source = String($prevNode?.name || "");
const map = {};
for (const [role, items] of Object.entries({
  ANALYST: [
    { route: "ANALYST_QWEN", node: "Analyst", provider: "OLLAMA_LOCAL", model: "qwen3:8b" },
    { route: "ANALYST_NVIDIA", node: "Analyst NVIDIA Fallback", provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
    { route: "ANALYST_GROQ", node: "Analyst Groq Fallback", provider: "GROQ", model: "openai/gpt-oss-120b" },
  ],
  SPECIALIST: [
    { route: "SPECIALIST_NVIDIA_ULTRA", node: "Specialist", provider: "NVIDIA", model: "nvidia/nemotron-3-ultra-550b-a55b" },
    { route: "SPECIALIST_NVIDIA_LIGHTNING", node: "Specialist Fallback", provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
    { route: "SPECIALIST_GROQ", node: "Specialist Groq Fallback", provider: "GROQ", model: "openai/gpt-oss-120b" },
  ],
  PLANNER: [
    { route: "PLANNER_NVIDIA_LIGHTNING", node: "Planner", provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
    { route: "PLANNER_NVIDIA_ULTRA", node: "Planner NVIDIA Alternate", provider: "NVIDIA", model: "nvidia/nemotron-3-ultra-550b-a55b" },
    { route: "PLANNER_GROQ", node: "Planner Groq Fallback", provider: "GROQ", model: "openai/gpt-oss-120b" },
  ],
  CONSISTENCY_REVIEWER: [
    { route: "CONSISTENCY_NVIDIA_LIGHTNING", node: "Consistency Reviewer", provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
    { route: "CONSISTENCY_NVIDIA_ULTRA", node: "Consistency NVIDIA Alternate", provider: "NVIDIA", model: "nvidia/nemotron-3-ultra-550b-a55b" },
    { route: "CONSISTENCY_GROQ", node: "Consistency Groq Fallback", provider: "GROQ", model: "openai/gpt-oss-120b" },
  ],
  QA_LEAD: [
    { route: "QA_LEAD_NVIDIA_LIGHTNING", node: "QA Lead", provider: "NVIDIA", model: "nvidia/nemotron-3.5-lightning-30b-a3b" },
    { route: "QA_LEAD_NVIDIA_ULTRA", node: "QA Lead NVIDIA Alternate", provider: "NVIDIA", model: "nvidia/nemotron-3-ultra-550b-a55b" },
    { route: "QA_LEAD_GROQ", node: "QA Lead Groq Fallback", provider: "GROQ", model: "openai/gpt-oss-120b" },
  ],
}))
  for (const c of items) map[c.node] = { role, ...c };
function canonicalRole(value) {
  const role = String(value || "").toUpperCase();
  if (["NVIDIA", "GROQ", "MINIMAX", "LAGUNA", "CODEX", "OLLAMA_LOCAL", "UNKNOWN", ""].includes(role)) return "";
  return CANONICAL.includes(role) ? role : "";
}
const infoFromNode = map[source] || map[chain._provider_node] || map[dispatch._provider_node] || map[x._provider_node];
const role =
  canonicalRole(NODE_TO_ROLE[source]) ||
  canonicalRole(infoFromNode && infoFromNode.role) ||
  canonicalRole(chain._provider_role) ||
  canonicalRole(dispatch._provider_role) ||
  canonicalRole(x._provider_role);
const info = infoFromNode || {
  role: role || "UNKNOWN",
  provider: String(chain._provider_name || dispatch._provider_name || "UNKNOWN"),
  model: String(chain._provider_model || dispatch._provider_model || "UNKNOWN"),
  node: source || String(chain._provider_node || dispatch._provider_node || "UNKNOWN"),
};
if (role) info.role = role;
else info.role = "UNKNOWN";
const base = { ...chain, ...dispatch };
const failures = Array.isArray(base.provider_failures) ? base.provider_failures : [];
const roleFailures = failures.filter((f) => f.role === info.role);
const health = base.provider_health && typeof base.provider_health === "object" ? JSON.parse(JSON.stringify(base.provider_health)) : {};
for (const key of ["MODEL:" + info.model, "PROVIDER:" + info.provider]) {
  const h = health[key] || { provider: info.provider, model: key.startsWith("MODEL:") ? info.model : null };
  h.consecutive_availability_failures = 0;
  h.temporary_unavailable = false;
  h.last_success_at = new Date().toISOString();
  health[key] = h;
}
const cycles = base.provider_recovery_cycles && typeof base.provider_recovery_cycles === "object" ? base.provider_recovery_cycles : {};
const cycleCount = Math.max(0, ...Object.entries(cycles).filter(([k]) => k.includes("|" + info.role + "|")).map(([, v]) => Number(v) || 0));
const primary = PRIMARY[info.role] || { provider: null, model: null, node: null };
const meta = {
  selected_provider: info.provider,
  selected_model: info.model,
  primary_provider: primary.provider,
  primary_model: primary.model,
  failover_used: Boolean(primary.node) && info.node !== primary.node,
  provider_attempt_count: roleFailures.length + 1,
  provider_failures: roleFailures,
  recovery_cycle_count: cycleCount,
  failure_signature: base.failure_signature || null,
  final_failure_class: info.role === "UNKNOWN" ? "PROVIDER_ROLE_UNKNOWN" : null,
  provider_chain_exhausted: false,
};
const roleResults = { ...(base.provider_role_results || {}), [info.role]: meta };
let out = {
  ...base,
  ...x,
  provider_health: health,
  provider_failures: failures,
  technical_failures: base.technical_failures || [],
  provider_role_results: roleResults,
  ...meta,
  _provider_role: info.role === "UNKNOWN" ? canonicalRole(chain._provider_role) || "UNKNOWN" : info.role,
  _provider_completed_role: info.role,
  _provider_next_role: NEXT_ROLE[info.role] || null,
};
if (info.role === "UNKNOWN") {
  out.error = "Completed factory role could not be determined after a successful provider result.";
  out.failure_signature = "PROVIDER_ROLE_UNKNOWN";
  out.final_failure_class = "PROVIDER_ROLE_UNKNOWN";
}
if (info.role === "SPECIALIST") {
  let p;
  try {
    p = JSON.parse(String(x.content));
  } catch {
    p = null;
  }
  if (p) {
    p.specialist_primary_model = "nvidia/nemotron-3-ultra-550b-a55b";
    p.specialist_selected_model = info.model;
    p.specialist_failover_used = meta.failover_used;
    p.specialist_attempt_count = meta.provider_attempt_count;
    p.specialist_provider_failures = roleFailures;
    out.content = JSON.stringify(p);
    out.specialist_report = p;
  }
  out.specialist_primary_model = "nvidia/nemotron-3-ultra-550b-a55b";
  out.specialist_selected_model = info.model;
  out.specialist_failover_used = meta.failover_used;
  out.specialist_attempt_count = meta.provider_attempt_count;
  out.specialist_provider_failures = roleFailures;
}
if (info.role === "QA_LEAD" && x._sprint_state) {
  out._sprint_state = {
    ...x._sprint_state,
    provider_health: health,
    provider_failures: failures,
    technical_failures: base.technical_failures || [],
    provider_role_results: roleResults,
  };
}
return [{ json: out }];
