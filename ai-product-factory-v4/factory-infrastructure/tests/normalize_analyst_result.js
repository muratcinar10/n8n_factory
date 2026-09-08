const x = $input.first().json || {};
function analystText(item) {
  if (typeof item.content === "string" && item.content.trim()) return item.content;
  const nested = item.body && typeof item.body === "object" ? item.body : item;
  const fromChoices = nested?.choices?.[0]?.message?.content ?? nested?.message?.content;
  if (typeof fromChoices === "string" && fromChoices.trim()) return fromChoices;
  return item.content;
}
function stringList(value, field) {
  if (value == null || value === "") return [];
  if (typeof value === "string") return value.trim() ? [value.trim()] : [];
  if (!Array.isArray(value)) throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response violates output contract at " + field);
  const out = [];
  for (const item of value) {
    if (typeof item !== "string") throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response violates output contract at " + field);
    if (item.trim()) out.push(item.trim());
  }
  return out;
}
const raw = analystText(x);
if (typeof raw !== "string" || !raw.trim()) throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response missing content");
const clean = raw.replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/\x60{3}json/gi, "").replace(/\x60{3}/g, "").trim();
const a = clean.indexOf("{"),
  b = clean.lastIndexOf("}");
if (a < 0 || b < a) throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response is not JSON");
let parsed;
try {
  parsed = JSON.parse(clean.slice(a, b + 1));
} catch {
  throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response contains malformed JSON");
}
const required = { objective: "string", scope: "array", requirements: "array", acceptance_criteria: "array", constraints: "array", status: "string", blocker: "string" };
for (const [key, type] of Object.entries(required)) {
  if (!(key in parsed) || (type === "array" ? !Array.isArray(parsed[key]) : typeof parsed[key] !== type))
    throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response violates output contract at " + key);
}
for (const key of ["assumptions", "ambiguities", "known_context"]) parsed[key] = stringList(parsed[key], key);
if (!("recovery_context_acknowledged" in parsed)) parsed.recovery_context_acknowledged = null;
else if (parsed.recovery_context_acknowledged !== null && typeof parsed.recovery_context_acknowledged !== "object" && typeof parsed.recovery_context_acknowledged !== "string")
  throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response violates output contract at recovery_context_acknowledged");
parsed.status = String(parsed.status).toUpperCase();
if (!["READY", "BLOCKED"].includes(parsed.status)) throw new Error("MALFORMED_MODEL_OUTPUT: Analyst response violates output contract at status");
const project_id = x.project_id;
const work_unit_id = x.work_unit_id;
return [{ json: { ...x, content: JSON.stringify(parsed), known_context: parsed.known_context, project_id, work_unit_id } }];
