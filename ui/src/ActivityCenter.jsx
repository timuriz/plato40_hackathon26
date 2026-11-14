import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, ArrowRight, Bot, CalendarDays, CheckCircle2, CircleHelp, Clock3, Cpu, Database, DoorOpen, Filter, MessageSquare, ShieldCheck, Sparkles, UserCheck, X } from 'lucide-react';
import { human, dateLabel, recordId } from './OperationalIntelligence.jsx';

const CATEGORIES = [
  ['all','All activity',Activity], ['events','Historical events',Clock3], ['requests','Guest requests',MessageSquare],
  ['anomalies','Sensor anomalies',AlertTriangle], ['incidents','Incidents',AlertTriangle], ['decisions','AI decisions',Cpu],
  ['actions','Actions',Bot], ['approvals','Approvals',UserCheck], ['verification','Verification',CheckCircle2],
];
const byType = Object.fromEntries(CATEGORIES.map(([key,label,icon])=>[key,{label,icon}]));
const fmt = v => v==null?'—':String(v);
const emptyFilters={asset:'',severity:'',issue_type:'',status:'',from_date:'',to_date:''};
const fields = ['asset','severity','issue_type','status','from_date','to_date'];

async function getActivity(at,options={}) {
  const params=new URLSearchParams({at,limit:'50'});
  Object.entries(options).forEach(([key,value])=>{if(value!=null&&value!=='')params.set(key,String(value))});
  const response=await fetch(`/api/activity?${params}`,{headers:{Accept:'application/json'}});
  const contentType=response.headers.get('content-type')||'';
  if(!contentType.toLowerCase().includes('application/json')) {
    const detail=response.status===404
      ? 'Activity API not found at this address'
      : 'Replay server returned a web page instead of activity data';
    throw new Error(detail);
  }
  let data;
  try { data=await response.json(); }
  catch { throw new Error('Replay server returned invalid activity JSON. Restart the updated replay server.'); }
  if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
  return data;
}
function Content({value,empty='Not supplied by this source.'}) {
  if(value==null||value===''||(Array.isArray(value)&&!value.length))return <span className="activity-muted">{empty}</span>;
  if(Array.isArray(value))return <ul className="activity-evidence-list">{value.map((item,i)=><li key={i}><Content value={item}/></li>)}</ul>;
  if(typeof value==='object')return <dl className="activity-object">{Object.entries(value).map(([key,item])=><div key={key}><dt>{human(key)}</dt><dd><Content value={item}/></dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}
function Field({label,value,empty}){return <div className="activity-field"><span>{label}</span><div><Content value={value} empty={empty}/></div></div>}
function statusText(entry) {
  if(entry?.type==='decisions') return human(entry.metadata?.lifecycle?.stage||'proposed_action');
  if(entry?.type==='verification'&&['verified','passed'].includes(entry.status)&&!entry.metadata?.verification_evidence?.length)return 'Unverified · evidence not supplied';
  if(entry?.type==='actions'&&['simulated','simulated_completed'].includes(entry.status))return 'Simulated execution';
  if(entry?.type==='events')return entry.status?human(entry.status):'Historical record';
  if(entry?.type==='requests')return entry.status?human(entry.status):'Received';
  return entry?.status?human(entry.status):'Not reported';
}
function Priority({value}){return value?<span className={`activity-priority ${String(value).toLowerCase()}`}>{human(value)}</span>:null}

function FeedEntry({entry,selected,onSelect,compact=false}) {
  const {label,icon:Icon}=byType[entry.type]||{label:human(entry.type),icon:CircleHelp};
  return <button className={`activity-entry ${entry.type} ${selected?'selected':''} ${compact?'compact':''}`} onClick={()=>onSelect(entry)} aria-pressed={selected}>
    <span className={`activity-icon ${entry.type}`}><Icon size={compact?15:17}/></span>
    <span className="activity-entry-body"><span className="activity-entry-heading"><strong>{entry.title}</strong><Priority value={entry.severity}/></span>
      <span className="activity-entry-summary">{entry.description||((entry.asset_ids||[]).length?entry.asset_ids.join(' · '):'Building-wide record')}</span>
      <span className="activity-entry-meta"><b>{label}</b><i/> {entry.source} <i/> {(entry.asset_ids||[]).join(', ')||'Asset unspecified'} <i/> {statusText(entry)}</span>
    </span><span className="activity-entry-time"><time dateTime={entry.timestamp_utc}>{dateLabel(entry.timestamp_utc)}</time><ArrowRight size={14}/></span>
  </button>;
}

export function RecentActivity({payload,onOpen}) {
  const [result,setResult]=useState(null), [error,setError]=useState('');
  const at=payload?.replay?.timestamp_utc;
  useEffect(()=>{
    if(!at){setResult(null);return}
    let active=true;setResult(null);setError('');
    getActivity(at,{limit:5}).then(data=>{if(active)setResult(data)}).catch(e=>{if(active)setError(e.message)});
    return()=>{active=false};
  },[at]);
  return <section className="panel recent-activity" aria-label="Recent activity"><div className="recent-heading"><div><span className="section-eyebrow">BUILDING TIMELINE</span><h2>Recent activity</h2><p>Events and decisions known at the selected replay time.</p></div><a href="#activity" className="activity-link">View activity <ArrowRight size={14}/></a></div>
    {error?<div className="activity-empty">Activity unavailable: {error}. Restart the updated replay server.</div>:!result?<div className="activity-empty">Loading activity…</div>:(result.highlights||result.items).length?<div className="recent-activity-list">{(result.highlights||result.items).map(entry=><FeedEntry key={entry.id} entry={entry} onSelect={item=>onOpen(item)} compact/>)}</div>:<div className="activity-empty">No activity is recorded by this replay time.</div>}
  </section>;
}

function RelatedAssets({entry,payload,onViewAsset}) {
  const assets=entry.asset_ids||[], rooms=payload?.rooms||[], selectedRoom=rooms.find(room=>assets.includes(room.id));
  const readings=selectedRoom?.signals||{};
  return <div className="activity-asset-box"><div className="activity-card-heading"><DoorOpen size={15}/> Building context</div>{assets.length?<div className="activity-asset-buttons">{assets.map(asset=><button key={asset} onClick={()=>onViewAsset(asset)}>View {asset} <ArrowRight size={12}/></button>)}</div>:<p className="activity-muted">No affected asset was supplied.</p>}
    {selectedRoom&&<div className="activity-room-facts"><div><small>OCCUPANCY</small><strong>{selectedRoom.occupancy?.occupied===true?'Occupied':selectedRoom.occupancy?.occupied===false?'Vacant':'Unknown'}</strong></div><div><small>READINESS</small><strong>{human(selectedRoom.readiness?.state)}</strong></div><div><small>TEMPERATURE</small><strong>{readings.temperature_c?.value==null?'—':`${readings.temperature_c.value} °C`}</strong></div><div><small>WATER FLOW</small><strong>{readings.water_flow_lph?.value==null?'—':`${readings.water_flow_lph.value} L/h`}</strong></div></div>}
    <p className="activity-muted">Building context reflects the selected replay time. Reported room readiness is not a release authorization.</p>
  </div>;
}

function Detail({entry,payload,onClose,onViewAsset,onRelated,detailRef}) {
  if(!entry)return <aside className="activity-detail activity-detail-empty"><span className="activity-detail-empty-icon"><Sparkles size={22}/></span><h3>See the whole story.</h3><p>Select an activity to inspect the source, affected assets, evidence and any linked decisions.</p><div className="activity-detail-empty-rule"/><small>Everything reflects the selected historical replay time.</small></aside>;
  const meta=entry.metadata||{}, incidents=payload?.incidents||[], decisions=payload?.decisions||[];
  const incident=incidents.find(i=>entry.incident_id&&(i.episode_id===entry.incident_id||i.incident_id===entry.incident_id||i.event_id===entry.incident_id));
  const directDecision=decisions.find(d=>entry.decision_id&&d.event_id===entry.decision_id);
  const relatedDecisions=directDecision?[directDecision]:decisions.filter(d=>incident?.episode_id&&d.episode_id===incident.episode_id);
  const decision=relatedDecisions[0], life=decision?.lifecycle||{}, proof=life.verification_evidence||[];
  const matches=(incident?.episode_id||entry.incident_id);
  const safeEntry=entry.type==='decisions'?{...entry,metadata:directDecision||{},status:directDecision?.lifecycle?.stage}:entry;
  const relatedEvents=(payload?.operational_context?.events||[]).filter(event=>(entry.asset_ids||[]).includes(event.room_id||event.asset_id)).slice(-5).reverse();
  const stages=[['Detected',!!incident],['Evaluated',!!(incident?.reason||incident?.confidence!=null)],['Decided',!!decision],['Approved',life.approval==='approved'],['Simulated',['simulated','simulated_completed'].includes(life.execution)],['Verified',life.stage==='verified_outcome'&&proof.length>0]];
  const evidence=entry.type==='decisions'?(decision?.evidence||incident?.evidence||incident?.features):(incident?.evidence||incident?.features||decision?.evidence||meta.evidence||meta.verification_evidence);
  return <aside ref={detailRef} className="activity-detail" aria-label="Activity detail"><div className="activity-detail-top"><div><div className="section-eyebrow">{(byType[entry.type]||{}).label||human(entry.type)} · DETAIL</div><h3>{entry.title}</h3></div><button className="activity-close" onClick={onClose} aria-label="Close activity details"><X size={17}/></button></div>
    <div className="activity-detail-sub"><time>{dateLabel(entry.timestamp_utc)}</time><span>{entry.source}</span><Priority value={entry.severity}/></div>{entry.available_at_utc&&entry.available_at_utc!==entry.timestamp_utc&&<p className="activity-availability">Available at {dateLabel(entry.available_at_utc)} · Europe/Vienna</p>}
    <div className="activity-status-line"><i/>{statusText(safeEntry)}</div>
    <RelatedAssets entry={entry} payload={payload} onViewAsset={onViewAsset}/>
    {(incident||decision)&&<div className="activity-stage-flow" aria-label="Decision stages">{stages.map(([label,done])=><span className={done?'done':''} key={label}><i/>{label}</span>)}</div>}
    <details className="activity-detail-context"><summary>Additional building measurements &amp; context</summary><p>Readings and records at the selected replay time are context; they are not automatically evidence for this entry.</p><Field label="Main and room water balance" value={payload?.operational_context?.water_balance}/><Field label="Water balance limitations" value={payload?.operational_context?.water_note}/><Field label="Weather" value={payload?.operational_context?.weather}/><Field label="Plant readings" value={payload?.operational_context?.equipment}/><Field label="Plant data quality" value={payload?.operational_context?.equipment_quality}/><Field label="Published forecast · next records" value={payload?.operational_context?.forecast?.slice(0,4)}/><Field label="Published tariff · next records" value={payload?.operational_context?.tariff?.slice(0,4)}/><Field label="Related guest requests · last 48h" value={(payload?.operational_context?.guest_requests||[]).filter(request=>(entry.asset_ids||[]).includes(request.room_id)).slice(-5)}/><Field label="Relevant work orders" value={(payload?.operational_context?.work_orders||[]).filter(order=>(entry.asset_ids||[]).includes(order.asset_id)).slice(-5)}/></details>
    <div className="activity-detail-group"><h4><Database size={15}/> Evidence</h4><Field label="Source / reported at" value={`${entry.source} · ${dateLabel(entry.timestamp_utc)}`}/><Field label="Supporting observations" value={evidence} empty="No evidence record was supplied for this entry."/>{relatedEvents.length>0&&<details><summary>Recent events for these assets · context, not necessarily evidence</summary><ul className="activity-evidence-list">{relatedEvents.map((event,index)=><li key={event.event_id||index}>{dateLabel(event.timestamp_utc)} · {human(event.type)}</li>)}</ul></details>}</div>
    {(incident||decision)&&<><div className="activity-detail-group"><h4><AlertTriangle size={15}/> Risk & uncertainty</h4><Field label="Issue and priority" value={`${human(incident?.issue_type||decision?.issue_type)} · ${incident?.severity||incident?.priority||decision?.priority||'Not supplied'}`}/><Field label="Reason / assessment" value={incident?.reason||decision?.reason}/><Field label="Potential impact" value={incident?.impact||incident?.potential_impact}/><Field label="Confidence" value={incident?.confidence!=null?`${Math.round(incident.confidence*100)}% · detector estimate`:decision?.confidence!=null?`${Math.round(decision.confidence*100)}% · estimate`:null}/><Field label="Limitations" value={incident?.uncertainty||incident?.limitations||incident?.quality_flags} empty="No uncertainty assessment supplied. Check source quality and alternative explanations."/></div>
    <div className="activity-detail-group"><h4><Cpu size={15}/> Decision & autonomy</h4>{decision?<><Field label="Selected action" value={decision.action}/><Field label="Reason" value={decision.reason}/><Field label="Constraints / dependencies" value={decision.constraints||decision.dependencies}/><Field label="Autonomy" value={decision.autonomy}/><Field label="Human approval" value={decision.requires_human_approval===true?`Required · ${human(life.approval)}`:decision.requires_human_approval===false?'Not required by decision record':'Not specified'}/><Field label="Guest consent" value={decision.requires_guest_consent===true?`Required · ${human(life.consent)}`:decision.requires_guest_consent===false?'Not required by decision record':'Not specified'}/><Field label="Execution" value={human(life.execution||'not_recorded')}/>{relatedDecisions.length>1&&<div className="activity-related">{relatedDecisions.map(item=><button key={item.event_id} onClick={()=>onRelated('decisions',item.event_id)}>{human(item.action)} <ArrowRight size={12}/></button>)}</div>}</>:<p className="activity-muted">No decision explicitly linked to this incident. A historical event is not automatically an AI decision.</p>}</div>
    <div className="activity-detail-group"><h4><ShieldCheck size={15}/> Verification</h4><Field label="Expected outcome · plan" value={decision?.expected_outcome}/><Field label="Verification plan" value={decision?.verification_plan||incident?.verification_plan}/><Field label="Verification status" value={decision?human(life.verification||'not_recorded'):null}/><Field label="Actual evidence" value={proof} empty="No verified outcome recorded."/></div></>}
    {!incident&&!decision&&['actions','approvals','verification'].includes(entry.type)&&<div className="activity-detail-group"><h4><ShieldCheck size={15}/> Recorded state</h4><Field label="Approval" value={meta.approval_status}/><Field label="Guest consent" value={meta.consent_status}/><Field label="Execution" value={meta.execution_status}/><Field label="Verification" value={meta.verification_status}/><Field label="Verification evidence" value={meta.verification_evidence}/></div>}
    {entry.description&&<div className="activity-detail-group"><h4><MessageSquare size={15}/> Original description</h4><p>{entry.description}</p></div>}
    <div className="activity-related">{incident&&entry.type!=='incidents'&&<button onClick={()=>onRelated('incidents',recordId(incident))}>Open linked incident <ArrowRight size={12}/></button>}{decision&&entry.type!=='decisions'&&<button onClick={()=>onRelated('decisions',decision.event_id)}>Open linked decision <ArrowRight size={12}/></button>}{matches&&<span>Episode {matches}</span>}</div>
    <details className="activity-raw"><summary>Original source record</summary><pre>{JSON.stringify(entry.type==='decisions'?(directDecision||{}):entry.type==='incidents'?(incident||{}):meta,null,2)}</pre></details>
    <p className="activity-boundary">Read-only replay. Proposed actions and expected outcomes are not execution or verification.</p>
  </aside>;
}

export default function ActivityCenter({payload,focusTarget,onFocusHandled,onSelectAsset,onViewAsset,onExport}) {
  const [category,setCategory]=useState('all'), [draft,setDraft]=useState(emptyFilters), [filters,setFilters]=useState(emptyFilters);
  const [result,setResult]=useState(null), [error,setError]=useState(''), [loading,setLoading]=useState(false), [selected,setSelected]=useState(null), [selectedAt,setSelectedAt]=useState(null);
  const version=useRef(0), detailRef=useRef(null), at=payload?.replay?.timestamp_utc;
  useEffect(()=>{if(selected&&window.matchMedia('(max-width: 800px)').matches)detailRef.current?.scrollIntoView({behavior:'smooth',block:'start'})},[selected?.id]);
  const filterKey=JSON.stringify(filters);
  useEffect(()=>{
    if(!at){setResult(null);return}
    const current=++version.current;setResult(null);setLoading(true);setError('');
    getActivity(at,{type:category,...filters}).then(data=>{if(current===version.current)setResult(data)})
      .catch(e=>{if(current===version.current)setError(e.message)}).finally(()=>{if(current===version.current)setLoading(false)});
    return()=>{version.current++};
  },[at,category,filterKey]);
  useEffect(()=>{if(!result||!selected||!at)return;const fresh=result.items.find(row=>row.id===selected.id);if(fresh){setSelected(fresh);setSelectedAt(at)}},[result,at]);
  const showEntry=entry=>{setSelected(entry);setSelectedAt(at);const room=(entry.asset_ids||[]).find(asset=>payload?.rooms?.some(r=>r.id===asset));if(room)onSelectAsset(room);else if(entry.asset_ids?.length)onSelectAsset(entry.asset_ids[0]);};
  const showRelated=(type,id)=>{
    const source=type==='incidents'?payload?.incidents:payload?.decisions;
    const raw=source?.find(row=>type==='incidents'?recordId(row)===id:row.event_id===id);
    if(!raw)return;
    const origin=raw.source||payload?.incidents?.find(item=>item.episode_id&&item.episode_id===raw.episode_id)?.source;
    const entry={id:`linked:${type}:${id}`,type,source:type==='incidents'?(raw.detector||'incident output'):(origin?`decision pipeline · ${origin}`:'decision output'),title:raw.title||human(raw.action||raw.issue_type),
      timestamp_utc:raw.timestamp_utc,available_at_utc:raw.available_at||raw.available_at_utc||raw.published_utc||raw.timestamp_utc,asset_ids:raw.affected_assets||[],severity:raw.severity||raw.priority,
      status:raw.status||raw.lifecycle?.stage,issue_type:raw.issue_type,incident_id:raw.episode_id||raw.incident_id,decision_id:type==='decisions'?raw.event_id:null,description:raw.reason||'',metadata:raw};
    showEntry(entry);
  };
  useEffect(()=>{
    if(!focusTarget)return;
    if(focusTarget.entry)showEntry(focusTarget.entry);
    else if(focusTarget.issueType){const next={...emptyFilters,issue_type:focusTarget.issueType};setDraft(next);setFilters(next);setCategory('incidents');setSelected(null);}
    else if(focusTarget.type&&focusTarget.id)showRelated(focusTarget.type,focusTarget.id);
    onFocusHandled?.();
  },[focusTarget]);
  const selectedVisible=selected&&at&&selectedAt===at&&new Date(selected.timestamp_utc)<=new Date(at)&&new Date(selected.available_at_utc||selected.timestamp_utc)<=new Date(at)?selected:null;
  const loadMore=async()=>{if(!result?.next_cursor||loading)return;const current=version.current;setLoading(true);try{const more=await getActivity(at,{type:category,...filters,cursor:result.next_cursor});if(current===version.current)setResult(previous=>({...more,items:[...previous.items,...more.items]}))}catch(e){if(current===version.current)setError(e.message)}finally{if(current===version.current)setLoading(false)}};
  const setField=(key,value)=>setDraft(previous=>({...previous,[key]:value}));
  return <section className="activity-center" id="activity"><div className="activity-page-heading"><div><div className="section-eyebrow">OPERATIONS · CHRONOLOGICAL RECORD</div><h2>Activity &amp; Decisions</h2><p>One timeline for building events, observations, detections and decisions.</p></div><div className="activity-header-actions"><button className="activity-export" disabled={!payload?.decisions?.length} onClick={onExport}>Export decisions ↗</button><span className="activity-asof"><i/> AS OF {dateLabel(at)} · VIENNA</span></div></div>
    <div className="activity-toolbar panel"><div className="activity-categories" role="group" aria-label="Activity type">{CATEGORIES.map(([key,label,Icon])=><button key={key} aria-pressed={category===key} onClick={()=>setCategory(key)}><Icon size={14}/>{label}</button>)}</div>
      <form className="activity-filters" onSubmit={e=>{e.preventDefault();setFilters({...draft})}}><span><Filter size={14}/> Refine</span><label>Asset<input value={draft.asset} onChange={e=>setField('asset',e.target.value)} placeholder="Room or asset"/></label><label>Priority<select value={draft.severity} onChange={e=>setField('severity',e.target.value)}><option value="">Any</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label><label>Issue type<input value={draft.issue_type} onChange={e=>setField('issue_type',e.target.value)} placeholder="Any issue"/></label><label>Status<input value={draft.status} onChange={e=>setField('status',e.target.value)} placeholder="Any status"/></label><label>From<input type="date" value={draft.from_date} onInput={e=>setField('from_date',e.currentTarget.value)}/></label><label>To<input type="date" value={draft.to_date} onInput={e=>setField('to_date',e.currentTarget.value)}/></label><button type="submit">Apply</button><button type="button" className="activity-filter-clear" onClick={()=>{setDraft(emptyFilters);setFilters(emptyFilters);setCategory('all')}}>Clear</button></form>
      <div className="activity-toolbar-foot"><span><CalendarDays size={13}/> Event times in Europe/Vienna · data known by replay time only</span><span>{result?`${result.total} records`:'—'}</span></div>
    </div>
    <div className="activity-workspace"><div className="activity-feed panel"><div className="activity-feed-head"><div><span className="section-eyebrow">HISTORICAL RECORD</span><h3>Building timeline</h3></div><span>{result?.total??'—'} entries</span></div>
      {error?<div className="activity-empty">Activity is unavailable: {error}. Restart the updated replay server.</div>:!result&&loading?<div className="activity-empty">Loading activity…</div>:result?.items.length?<div className="activity-feed-list">{result.items.map(entry=><FeedEntry key={entry.id} entry={entry} selected={selectedVisible?.id===entry.id} onSelect={showEntry}/>)}</div>:<div className="activity-empty">No activity matches these filters at the selected replay time.</div>}
      {result?.next_cursor&&<button className="activity-load-more" disabled={loading} onClick={loadMore}>{loading?'Loading…':'Load older activity'}</button>}
      {result&&!result.availability.actions&&<div className="activity-source-note">No action, approval or verification audit connected. Proposed decisions are shown separately.</div>}
      {result&&(!result.availability.incidents||!result.availability.decisions)&&<div className="activity-source-note">{!result.availability.incidents?'Incident output not connected. ':''}{!result.availability.decisions?'Decision output not connected.':''}</div>}
    </div><Detail detailRef={detailRef} entry={selectedVisible} payload={payload} onClose={()=>setSelected(null)} onViewAsset={onViewAsset} onRelated={showRelated}/></div>
  </section>;
}
