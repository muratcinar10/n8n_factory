const STATES=new Set(['WAITING','RUNNING','COMPLETED','FAILED','RETRYING','SKIPPED','NEEDS_REVIEW','DEFERRED']);
const icons={WAITING:'⚪',RUNNING:'🟢',COMPLETED:'✅',FAILED:'❌',RETRYING:'🟡',SKIPPED:'⏭',NEEDS_REVIEW:'🔎',DEFERRED:'⛔'};
const $=id=>document.getElementById(id);
function text(value,fallback='—'){return typeof value==='string'&&value.trim()?value.trim():fallback}
function localTime(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.valueOf())?'—':new Intl.DateTimeFormat('tr-TR',{timeZone:'Europe/Istanbul',dateStyle:'short',timeStyle:'medium'}).format(d)}
function render(data){
  const status=STATES.has(data.factory_status)?data.factory_status:'WAITING';
  const project=data.project&&typeof data.project==='object'?data.project:{};
  $('factory-status').textContent=status;$('factory-status').className=`status ${status}`;
  $('project-title').textContent=text(project.project_title,'No project loaded');
  $('project-status').textContent=text(project.status);
  $('project-progress').textContent=`${Number.isInteger(project.done)?project.done:0} / ${Number.isInteger(project.total)?project.total:0} DONE`;
  $('current-work-unit').textContent=project.current_work_unit_id?`${text(project.current_work_unit_id)} — ${text(project.current_work_unit_title)}`:'—';
  $('project-health').textContent=Number.isFinite(project.health_score)?`${project.health_score} / 100`:'—';
  $('project-failures').textContent=Array.isArray(project.failure_ids)&&project.failure_ids.length?project.failure_ids.join(', '):'—';
  $('sprint-id').textContent=text(data.sprint_id,'No sprint observed');$('current-stage').textContent=text(data.current_stage);$('updated-at').textContent=localTime(data.updated_at);
  $('factory-score').textContent=Number.isFinite(data.inspector?.sprint_score)?`${data.inspector.sprint_score} / 100 (${text(data.inspector.health_band)})`:'—';
  const rows=Array.isArray(data.nodes)?data.nodes:[];
  $('node-rows').replaceChildren(...rows.map(node=>{const tr=document.createElement('tr');const state=STATES.has(node.status)?node.status:'WAITING';for(const [cls,value] of [['node-name',text(node.name,'Unknown stage')],['activity',`${icons[state]} ${state} — ${text(node.activity,'No factual activity recorded.')}`],['updated',localTime(node.updated_at)]]){const td=document.createElement('td');td.className=cls+(cls==='activity'?` ${state}`:'');td.textContent=value;tr.appendChild(td)}return tr}));
  $('connection-state').textContent=`Read-only local telemetry • refresh every 5 seconds • ${rows.length} stages`;
}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw new Error('status');render(await r.json())}catch{$('connection-state').textContent='Local telemetry is temporarily unavailable.'}}
refresh();setInterval(refresh,5000);
