const assert=require('assert');
const fs=require('fs');
const vm=require('vm');
const path=require('path');
const workflow=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json'),'utf8'));
const node=workflow.nodes.find(n=>n.name==='Factory Inspector');
const code=node.parameters.jsCode;
async function run(state,executed=[]){const global={};const context={$input:{first:()=>({json:state})},$getWorkflowStaticData:()=>global,$:name=>({isExecuted:executed.includes(name)}),Buffer,console};const result=await new vm.Script(`(async()=>{${code}})()`).runInNewContext(context);return {state:result[0].json,global};}
(async()=>{
  const goodTask={task_id:'T1',status:'COMPLETED',deterministic_qa_verdict:'PASS',qa_lead_status:'QA_APPROVED',developer_attempts:1,qa_hard_failures:[]};
  const good=(await run({sprint_id:'S1',sprint_report:{sprint_id:'S1'},tasks:[goodTask],technical_failures:[],REVIEW_BACKLOG:[],recovery_restart_count:0})).state.factory_inspector;
  assert.equal(good.sprint_score,100);assert.equal(good.health_band,'HEALTHY');
  const missing=(await run({sprint_id:'S2',sprint_report:{sprint_id:'S2'},tasks:[],technical_failures:[],REVIEW_BACKLOG:[]})).state.factory_inspector;
  assert.equal(missing.sprint_score,0);assert.equal(missing.health_band,'INVESTIGATE');assert.equal(missing.dimensions.task_correctness.status,'NOT_OBSERVED');
  const weak=(await run({sprint_id:'S3',sprint_report:{sprint_id:'S3'},tasks:[{...goodTask,status:'DEFERRED',deterministic_qa_verdict:'FAIL',qa_lead_status:'QA_INCOMPLETE',developer_attempts:3}],technical_failures:[{failed_node:'Planner'}],REVIEW_BACKLOG:[{kind:'QA_CRITICAL_FAILURE'}],recovery_restart_count:2})).state.factory_inspector;
  assert(weak.sprint_score<good.sprint_score);assert.equal(weak.health_band,'INVESTIGATE');assert.equal(weak.control_plane_mutation_allowed,false);
  assert.equal(workflow.nodes.length,61);assert.equal(workflow.active,false);
  const qa=workflow.nodes.find(n=>n.name==='Deterministic QA Gate').parameters.jsCode;
  assert(qa.includes('coverage_matrix'));
  assert(qa.includes('NON_CODEX_PROPOSAL_NOT_APPLIED'));
  const lead=workflow.nodes.find(n=>n.name==='Normalize QA Lead Result').parameters.jsCode;
  assert(lead.includes('auditQaCoverage'));
  assert(lead.includes('qa_passes_rejected_for_insufficient_evidence'));
  console.log('Inspector deterministic fixtures: PASS');
})().catch(error=>{console.error(error);process.exit(1)});
