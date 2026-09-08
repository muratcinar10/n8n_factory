function list(v){return Array.isArray(v)?v:[];}
function strings(v){return list(v).map(x=>typeof x==='string'?x:JSON.stringify(x)).filter(Boolean);}
function norm(v){return String(v??'').toLowerCase().replace(/[^a-z0-9/_.-]+/g,' ').replace(/\s+/g,' ').trim();}
function uniqueKeep(arr){const out=[],seen=new Set();for(const x of arr){const k=norm(x);if(!k||seen.has(k))continue;seen.add(k);out.push(String(x));}return out;}
function blobOf(v){return JSON.stringify(v??{}).toLowerCase();}
function criterionKind(text){
  const t=norm(text);
  if(/exactly (5|five) lives|start with exactly five lives|exactly five lives/.test(t))return 'EXACT_LIVES_5';
  if(/(duplicate|repeated) (wrong )?guess/.test(t)&&/(life|lives|consum)/.test(t))return 'DUPLICATE_WRONG_GUESS_NO_LIFE';
  return 'GENERIC';
}
function claimOnly(match){
  if(!match)return true;
  if(match.claim_only===true)return true;
  const evidence=list(match.evidence);
  const observed=match.observed??match.observations??match.observed_result??null;
  const outputs=list(match.test_outputs).concat(list(match.runtime_evidence));
  if(!evidence.length&&observed==null&&!outputs.length)return true;
  const blob=blobOf(match);
  if(/developer claim|claimed implemented|i implemented/.test(blob)&&!outputs.length&&(observed==null||typeof observed==='string'&&!/\d/.test(observed)))return true;
  return false;
}
function weakLives(match){
  const blob=blobOf(match);
  return /four (incorrect|wrong)|4 (incorrect|wrong)/.test(blob)&&!/lives?\s*(=|:)?\s*0/.test(blob)&&!/\bloss\b/.test(blob);
}
function weakDuplicate(match){
  const blob=blobOf(match);
  return (/appeared only once|on screen|visual/.test(blob)||/pressed .{0,8} twice/.test(blob))&&!/\blives?\b/.test(blob);
}
function livesSequence(match){
  const obs=match&&typeof match.observed==='object'?match.observed:(match&&match.observations)||{};
  return list(obs.lives_sequence||(match&&match.lives_sequence)).map(Number);
}
function discriminating(kind,match){
  if(!match||claimOnly(match))return false;
  const blob=blobOf(match);
  const obs=typeof match.observed==='object'&&match.observed?match.observed:(match.observations||{});
  if(kind==='EXACT_LIVES_5'){
    if(weakLives(match))return false;
    const seq=livesSequence(match);
    if(seq.join(',')==='5,4,3,2,1,0'&&/loss/.test(blob))return true;
    if(/fifth unique wrong|wrong guess #5|guess #5/.test(blob)&&/lives?\s*(=|:)?\s*0/.test(blob)&&/loss/.test(blob))return true;
    return false;
  }
  if(kind==='DUPLICATE_WRONG_GUESS_NO_LIFE'){
    if(weakDuplicate(match))return false;
    const before=obs.before_duplicate??obs.before??match.before_lives;
    const afterFirst=obs.after_first_wrong??obs.after_first??match.after_first_wrong_lives;
    const afterDup=obs.after_duplicate??match.after_duplicate_lives;
    if(before!=null&&afterFirst!=null&&afterDup!=null)return Number(afterFirst)===Number(before)-1&&Number(afterDup)===Number(afterFirst);
    if(/before[^\d]{0,12}5/.test(blob)&&/first[^\d]{0,24}4/.test(blob)&&/(duplicate|again|same)[^\d]{0,24}4/.test(blob)&&/lives/.test(blob))return true;
    return false;
  }
  const status=String(match.status||match.verdict||match.qa_verdict||'').toUpperCase();
  const evidence=list(match.evidence).concat(list(match.test_outputs),list(match.runtime_evidence));
  return status==='PASS'&&evidence.length>0&&!claimOnly(match);
}
function findResult(criterion,criterionResults){
  return list(criterionResults).find(r=>r&&norm(r.criterion||r.id||r.requirement)===norm(criterion))||null;
}
function developerClaimFor(criterion,developer){
  const summary=String(developer?.summary||'');
  const compliance=strings(developer?.constraint_compliance);
  const blob=[summary,...compliance].join('\n');
  if(!blob.trim())return 'NONE';
  if(norm(blob).includes(norm(criterion).slice(0,48)))return summary.slice(0,240)||compliance.find(x=>norm(x).includes(norm(criterion).slice(0,24)))||'CLAIM_PRESENT';
  if(/all acceptance criteria implemented|implemented exactly/.test(blob.toLowerCase()))return summary.slice(0,240)||'all acceptance criteria implemented';
  return 'NONE';
}
function buildCoverageMatrix({analystAcceptance,analystRequirements,taskCriteria,criterionResults,developer}){
  const mandatory=uniqueKeep([...taskCriteria]);
  const matrix=[];
  const seen=new Set();
  function pushRow(id,source,criterion,inScope){
    const key=norm(criterion);
    if(!key||seen.has(source+':'+key))return;
    seen.add(source+':'+key);
    const match=findResult(criterion,criterionResults);
    const kind=criterionKind(criterion);
    const qaVerdict=match?String(match.status||match.verdict||match.qa_verdict||'NOT_VERIFIED').toUpperCase():'NOT_TESTED';
    const disc=inScope&&discriminating(kind,match);
    matrix.push({
      id,source,criterion,in_current_task_scope:inScope,
      developer_claim:developerClaimFor(criterion,developer),
      qa_test:match?(match.test||match.test_performed||match.qa_test||null):null,
      observed_result:match?(match.observed||match.observations||match.observed_result||null):null,
      evidence:match?list(match.evidence):[],
      qa_verdict:qaVerdict==='PASS'||qaVerdict==='FAIL'||qaVerdict==='NOT_VERIFIED'||qaVerdict==='NOT_TESTED'?qaVerdict:(match?'NOT_VERIFIED':'NOT_TESTED'),
      discriminating:!!disc,
      kind
    });
  }
  mandatory.forEach((criterion,i)=>pushRow('TASK-AC-'+(i+1),'TASK_ACCEPTANCE',criterion,true));
  uniqueKeep(analystAcceptance).forEach((criterion,i)=>{
    const inScope=mandatory.some(x=>norm(x)===norm(criterion));
    pushRow('ANALYST-AC-'+(i+1),'ANALYST_ACCEPTANCE',criterion,inScope);
  });
  uniqueKeep(analystRequirements).forEach((criterion,i)=>{
    const inScope=mandatory.some(x=>norm(x)===norm(criterion));
    pushRow('ANALYST-REQ-'+(i+1),'ANALYST_REQUIREMENT',criterion,inScope);
  });
  return matrix;
}
function auditQaCoverage(qa,dispatcher){
  const analyst=dispatcher?.analyst_report&&typeof dispatcher.analyst_report==='object'?dispatcher.analyst_report:{};
  const task=(dispatcher?.tasks||[]).find(t=>String(t.task_id)===String(dispatcher?.current_task_id))||dispatcher?.current_task||{};
  let matrix=Array.isArray(qa?.coverage_matrix)?qa.coverage_matrix.slice():[];
  if(!matrix.length){
    matrix=buildCoverageMatrix({
      analystAcceptance:strings(analyst.acceptance_criteria||dispatcher?.acceptance_criteria),
      analystRequirements:strings(analyst.requirements||dispatcher?.requirements),
      taskCriteria:strings(task.acceptance_criteria),
      criterionResults:list(qa?.acceptance_criterion_results),
      developer:{summary:qa?.evidence_summary||dispatcher?.developer_summary,constraint_compliance:[]}
    });
  }
  const inScope=matrix.filter(row=>row.in_current_task_scope!==false);
  const weakTests=[],untested=[],failed=[],notVerified=[],claimsWithoutQa=[],qaPassesRejected=[];
  let adequate=0;
  for(const row of inScope){
    const kind=row.kind||criterionKind(row.criterion);
    const synthetic={
      status:row.qa_verdict,verdict:row.qa_verdict,qa_verdict:row.qa_verdict,
      test:row.qa_test,qa_test:row.qa_test,observed:row.observed_result,observations:row.observed_result,
      evidence:row.evidence,lives_sequence:row.observed_result&&row.observed_result.lives_sequence,
      before_lives:row.observed_result&&row.observed_result.before_duplicate,
      after_first_wrong_lives:row.observed_result&&row.observed_result.after_first_wrong,
      after_duplicate_lives:row.observed_result&&row.observed_result.after_duplicate,
      claim_only:row.developer_claim&&row.developer_claim!=='NONE'&&(!row.qa_test&&!(row.evidence||[]).length)
    };
    const disc=row.discriminating===true||discriminating(kind,Object.assign({},row,synthetic));
    const verdict=String(row.qa_verdict||'NOT_TESTED').toUpperCase();
    if(verdict==='FAIL')failed.push(row.id||row.criterion);
    if(!row.qa_test&&row.observed_result==null&&verdict!=='FAIL'&&!disc)untested.push(row.id||row.criterion);
    if(verdict==='NOT_VERIFIED')notVerified.push(row.id||row.criterion);
    if(row.developer_claim&&row.developer_claim!=='NONE'&&!disc)claimsWithoutQa.push(row.id||row.criterion);
    const weak=(kind==='EXACT_LIVES_5'&&weakLives(Object.assign({},row,{test:row.qa_test,observed:row.observed_result,evidence:row.evidence})))||(kind==='DUPLICATE_WRONG_GUESS_NO_LIFE'&&weakDuplicate(Object.assign({},row,{test:row.qa_test,observed:row.observed_result,evidence:row.evidence})));
    if(verdict==='PASS'&&(!disc||weak||claimOnly(synthetic))){
      qaPassesRejected.push(row.id||row.criterion);
      weakTests.push(row.id||row.criterion);
    }else if(weak||(row.qa_test&&!disc&&verdict!=='FAIL')){
      weakTests.push(row.id||row.criterion);
    }else if(disc&&verdict!=='FAIL'){
      adequate+=1;
    }
  }
  const coverageComplete=inScope.length>0&&adequate===inScope.length&&!weakTests.length&&!failed.length&&!untested.length&&!notVerified.length&&!qaPassesRejected.length;
  const analystText=JSON.stringify(analyst)+JSON.stringify(matrix.map(r=>r.criterion));
  if(/\b6 lives\b/.test(analystText)&&!/exactly 6/.test(JSON.stringify(analyst))){
    /* never rewrite Analyst 5-lives intent */
  }
  const finalVerdict=coverageComplete&&String(qa?.verdict||'').toUpperCase()==='PASS'?'QA_APPROVED':'QA_INCOMPLETE';
  let reason;
  if(finalVerdict==='QA_APPROVED')reason='Every in-scope mandatory criterion has discriminating observable evidence; Developer claims were not treated as proof; Analyst requirements were not reinterpreted.';
  else if(qaPassesRejected.length)reason='QA PASS labels were rejected because the supplied tests/evidence do not prove the Analyst/task criterion.';
  else if(claimsWithoutQa.length&&!inScope.some(r=>r.qa_test))reason='Developer claims are not deterministic evidence; no observable tests were supplied.';
  else if(weakTests.length)reason='Deterministic QA tests were not discriminating enough to prove the Analyst/task criteria.';
  else if(String(qa?.verdict||'').toUpperCase()!=='PASS')reason='Deterministic QA did not return PASS; coverage is incomplete.';
  else reason='Mandatory criterion coverage is incomplete.';
  return {
    coverage_complete:coverageComplete,
    mandatory_criteria_total:inScope.length,
    mandatory_criteria_adequately_tested:adequate,
    weak_tests:uniqueKeep(weakTests),
    untested_criteria:uniqueKeep(untested),
    failed_criteria:uniqueKeep(failed),
    not_verified_criteria:uniqueKeep(notVerified),
    developer_claims_without_sufficient_qa:uniqueKeep(claimsWithoutQa),
    qa_passes_rejected_for_insufficient_evidence:uniqueKeep(qaPassesRejected),
    final_verdict:finalVerdict,
    reason,
    coverage_matrix:matrix,
    analyst_requirement_preserved:true
  };
}
module.exports={list,strings,norm,uniqueKeep,criterionKind,claimOnly,weakLives,weakDuplicate,discriminating,buildCoverageMatrix,auditQaCoverage};
