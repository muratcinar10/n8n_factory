const STATES=new Set(['WAITING','RUNNING','COMPLETED','FAILED','RETRYING','SKIPPED','NEEDS_REVIEW','DEFERRED']);
const STATUS_TR={PENDING:'BEKLİYOR',READY:'HAZIR',RUNNING:'ÇALIŞIYOR',DONE:'TAMAMLANDI',FAILED:'BAŞARISIZ',NEEDS_REVIEW:'İNCELEME GEREKİYOR',DEFERRED:'ERTELENDİ',BLOCKED:'BLOKE',WAITING:'BEKLİYOR',COMPLETED:'TAMAMLANDI',SKIPPED:'ATLANDI',RETRYING:'YENİDEN DENENİYOR',VALIDATED:'DOĞRULANDI',PAUSED:'DURAKLATILDI',COMPLETED_WITH_OPEN_ITEMS:'AÇIK MADDELERLE TAMAMLANDI'};
const icons={WAITING:'⚪',RUNNING:'🟢',COMPLETED:'✅',FAILED:'❌',RETRYING:'🟡',SKIPPED:'⏭',NEEDS_REVIEW:'🔎',DEFERRED:'⛔'};
const $=id=>document.getElementById(id);
let projectId=null;
const labelStatus=value=>STATUS_TR[value]||value||'—';
function text(value,fallback='—'){return typeof value==='string'&&value.trim()?value.trim():fallback}
function localTime(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.valueOf())?'—':new Intl.DateTimeFormat('tr-TR',{timeZone:'Europe/Istanbul',dateStyle:'short',timeStyle:'medium'}).format(d)}
function count(counts,key){return Number.isInteger(counts?.[key])?counts[key]:0}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.reason||body.error||`HTTP ${response.status}`);return body}

function renderDiagnosis(state){
  const problem=state.work_units.find(unit=>['NEEDS_REVIEW','FAILED','DEFERRED'].includes(unit.status));
  const panel=$('diagnosis-panel');
  if(!problem||!problem.diagnosis){panel.hidden=true;return;}
  const d=problem.diagnosis;
  panel.hidden=false;
  $('diagnosis-root').textContent=`Kök sorun: ${text(d.summary_tr||d.summary)}. QA, Denetçi ve sonraki bağımlı işlerin durması bu ilk sorunun sonucudur.`;
  $('diag-id').textContent=`${problem.id} — ${text(problem.title)}`;
  $('diag-status').textContent=labelStatus(problem.status);
  $('diag-stage').textContent=text(d.stage);
  $('diag-last').textContent=text(d.last_success_stage);
  $('diag-attempt').textContent=`${problem.attempts||0} / ${state.max_attempts||3}`;
  $('diag-exec').textContent=text(d.execution_id||problem.factory_execution_id,'Yok');
  $('diag-qa').textContent=text(d.qa_status||problem.qa_status);
  $('diag-inspector').textContent=Number.isFinite(d.inspector_score)?String(d.inspector_score):text(problem.inspector_score);
  $('diag-why').textContent=text(d.why_not_done||d.summary_tr);
  $('diag-root-sig').textContent=text(d.root_failure);
  $('diag-affected').textContent=(d.affected_work_unit_ids||[]).join(', ')||'—';
}

function renderProject(state){
  if(!state||!state.project_id)return;
  projectId=state.project_id;
  const counts=state.counts||{};
  $('project-title').textContent=`${text(state.project_title)} (${text(state.project_id)})`;
  $('project-status').textContent=labelStatus(state.status);
  $('project-progress').textContent=`${count(counts,'DONE')} / ${state.total||0} TAMAMLANDI`;
  $('count-ready').textContent=count(counts,'READY');
  $('count-running').textContent=count(counts,'RUNNING');
  $('count-done').textContent=count(counts,'DONE');
  $('count-failed').textContent=count(counts,'FAILED');
  $('count-review').textContent=count(counts,'NEEDS_REVIEW');
  $('count-deferred').textContent=count(counts,'DEFERRED');
  $('count-blocked').textContent=count(counts,'BLOCKED');
  $('count-pending').textContent=count(counts,'PENDING');
  $('project-started').textContent=localTime(state.started_at);
  $('project-updated').textContent=localTime(state.updated_at);
  const current=state.current_work_unit||state.work_units?.find(unit=>unit.id===state.current_work_unit_id);
  $('current-work-unit').textContent=current?`${current.id}\n${text(current.title)}`:'—';
  $('current-attempt').textContent=current?`${current.attempts||0} / ${state.max_attempts||3}`:'—';
  $('current-started').textContent=current?localTime(current.started_at):'—';
  $('ids-review').textContent=(state.open_ids?.NEEDS_REVIEW||[]).join(', ')||'—';
  $('ids-deferred').textContent=(state.open_ids?.DEFERRED||[]).join(', ')||'—';
  $('ids-failed').textContent=(state.open_ids?.FAILED||[]).join(', ')||'—';
  $('ids-blocked').textContent=(state.open_ids?.BLOCKED||[]).join(', ')||'—';
  $('start').disabled=state.status!=='VALIDATED';
  $('pause').disabled=state.status!=='RUNNING';
  $('resume').disabled=state.status!=='PAUSED';
  renderDiagnosis(state);
  $('unit-rows').replaceChildren(...(state.work_units||[]).map(unit=>{
    const tr=document.createElement('tr');
    const blocked=unit.status==='BLOCKED'&&unit.diagnosis?`BLOKE (${unit.diagnosis.blocking_unit_id||'bağımlılık'} — ${labelStatus(unit.diagnosis.blocking_unit_status)})`:labelStatus(unit.status);
    for(const [cls,value] of [['wu-id',unit.id],['wu-title',text(unit.title)],['wu-state',blocked],['wu-attempts',String(unit.attempts||0)],['wu-deps',(unit.dependencies||[]).join(', ')||'—'],['updated',localTime(unit.updated_at)]]){
      const td=document.createElement('td');td.className=cls+(cls==='wu-state'?` ${unit.status}`:'');td.textContent=value;tr.appendChild(td);
    }
    return tr;
  }));
}

function renderTelemetry(data){
  const status=STATES.has(data.factory_status)?data.factory_status:'WAITING';
  const project=data.project&&typeof data.project==='object'?data.project:{};
  $('factory-status').textContent=labelStatus(status);$('factory-status').className=`status ${status}`;
  if(!projectId&&project.project_id)projectId=project.project_id;
  if(!projectId){
    $('project-title').textContent=text(project.project_title,'Proje yüklenmedi');
    $('project-status').textContent=labelStatus(project.status);
    $('project-progress').textContent=`${Number.isInteger(project.done)?project.done:0} / ${Number.isInteger(project.total)?project.total:0} TAMAMLANDI`;
  }
  $('current-stage').textContent=text(data.current_stage);
  $('sprint-stage').textContent=text(data.current_stage);
  $('sprint-id').textContent=text(data.sprint_id,'Henüz sprint gözlenmedi');
  $('updated-at').textContent=localTime(data.updated_at);
  $('factory-score').textContent=Number.isFinite(data.inspector?.sprint_score)?`${data.inspector.sprint_score} / 100 (${text(data.inspector.health_band)})`:'—';
  const workUnit=text(data.work_unit_id||project.current_work_unit_id,'');
  const rows=Array.isArray(data.nodes)?data.nodes:[];
  $('node-count').textContent=String(rows.length);
  $('node-rows').replaceChildren(...rows.map(node=>{
    const tr=document.createElement('tr');
    const state=STATES.has(node.status)?node.status:'WAITING';
    const activity=text(node.activity,'Henüz sprint telemetrisi gözlenmedi');
    const tagged=workUnit?`${icons[state]} ${labelStatus(state)} — ${workUnit} — ${activity}`:`${icons[state]} ${labelStatus(state)} — ${activity}`;
    for(const [cls,value] of [['node-name',text(node.name,'Bilinmeyen aşama')],['activity',tagged],['updated',localTime(node.updated_at)]]){
      const td=document.createElement('td');td.className=cls+(cls==='activity'?` ${state}`:'');td.textContent=value;tr.appendChild(td);
    }
    return tr;
  }));
  $('connection-state').textContent=`Salt okunur yerel telemetri • 5 saniyede bir yenilenir • ${rows.length} aşama`;
}

async function refresh(){
  try{
    const data=await api('/api/status');
    renderTelemetry(data);
    const id=projectId||data.project?.project_id;
    if(id)renderProject(await api(`/api/projects/${encodeURIComponent(id)}`));
  }catch{
    $('connection-state').textContent='Yerel telemetri şu anda kullanılamıyor.';
  }
}

$('validate').onclick=async()=>{
  try{
    const plan=JSON.parse($('manifest').value);
    const result=await api('/api/projects/validate',{method:'POST',body:JSON.stringify(plan)});
    projectId=result.project_id;
    $('start').disabled=false;
    $('validation').textContent=[
      'PLAN GEÇERLİ — üretim başlatılmadı.',
      `Proje: ${result.project_name}`,
      `Proje kimliği: ${result.project_id}`,
      `İş Birimi sayısı: ${result.work_unit_count}`,
      `Bağımlılık doğrulaması: ${result.dependency_validation||'PASS'}`,
      `Tahmini sıra: ${(result.estimated_order||[]).join(' → ')||'—'}`,
      `Pending until dependencies: ${(result.pending_until_dependencies||[]).join(', ')||'—'}`
    ].join('\n');
  }catch(error){
    $('start').disabled=true;
    $('validation').textContent=`PLAN REDDEDİLDİ: ${error.message}`;
  }
};

$('start').onclick=async()=>{
  try{
    const plan=JSON.parse($('manifest').value);
    const state=await api('/api/projects/start',{method:'POST',body:JSON.stringify(plan)});
    renderProject(state);
    $('validation').textContent='PROJE BAŞLATILDI. Uygun İş Birimleri fabrikaya teker teker gönderilecek.';
  }catch(error){
    $('validation').textContent=`BAŞLATMA REDDEDİLDİ: ${error.message}`;
  }
};

$('pause').onclick=async()=>{
  if(!projectId)return;
  try{
    renderProject(await api(`/api/projects/${encodeURIComponent(projectId)}/pause`,{method:'POST',body:'{}'}));
    $('validation').textContent='Duraklatma istendi; çalışan İş Birimi kesilmez.';
  }catch(error){$('validation').textContent=error.message}
};

$('resume').onclick=async()=>{
  if(!projectId)return;
  try{
    renderProject(await api(`/api/projects/${encodeURIComponent(projectId)}/resume`,{method:'POST',body:'{}'}));
    $('validation').textContent='Proje bir sonraki uygun İş Biriminden sürdürüldü.';
  }catch(error){$('validation').textContent=error.message}
};

refresh();setInterval(refresh,5000);
