import React, { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Html, Line, OrbitControls, Text } from '@react-three/drei';
import { Box, RotateCcw, Layers3, Building2, ChevronDown, MousePointer2, Radio, Sparkles, ZoomIn, ZoomOut, Bot, UserRound, DoorOpen, MapPin, Clock3 } from 'lucide-react';
import * as THREE from 'three';
import { ArchitecturalFloor, BuildingEnvelope, STORY_HEIGHT, halfWidth } from './BuildingArchitecture.jsx';

const STATUS = {
  clean: { color: '#32cbb6', label: 'Clean reported' },
  occupied: { color: '#4b93b5', label: 'Occupied' },
  vacant_clean: { color: '#32cbb6', label: 'Vacant · clean reported' },
  cleaning: { color: '#fbbf24', label: 'Cleaning' },
  dirty: { color: '#e5a843', label: 'Dirty' },
  attention: { color: '#fb7185', label: 'Inspection required' },
  unknown: { color: '#455667', label: 'Unknown' },
};
const SEVERITY = { critical: '#fb7185', high: '#fb7185', medium: '#fbbf24', low: '#42d9c8' };
const ACTIVE_STATUSES = new Set(['open','active','acknowledged','in_progress','investigating','new']);
const clamp = (value,min,max) => Math.min(max,Math.max(min,value));
const fmt = (value,digits=1) => value==null||!Number.isFinite(Number(value))?'—':Number(value).toLocaleString('de-AT',{maximumFractionDigits:digits});
const human = value => String(value??'unknown').replaceAll('_',' ').replace(/\b\w/g,match=>match.toUpperCase());

function roomStatus(room, layer = 'occupancy') {
  if (layer === 'housekeeping') return ({ clean:'clean', in_progress:'cleaning', dirty:'dirty', inspection_required:'attention' })[room.readiness?.state] || 'unknown';
  if (room.occupancy?.occupied === true) return 'occupied';
  if (room.readiness?.state === 'in_progress') return 'cleaning';
  if (room.readiness?.state === 'dirty') return 'dirty';
  if (room.readiness?.state === 'inspection_required') return 'attention';
  if (room.readiness?.state === 'clean' && room.occupancy?.known) return 'vacant_clean';
  return 'unknown';
}

function layoutRooms(rooms) {
  const groups = new Map();
  rooms.forEach((room) => {
    const key = Number(room.floor);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(room);
  });
  return new Map([...groups].map(([floor, items]) => {
    const north = items.filter(room => room.orientation === 'N');
    const south = items.filter(room => room.orientation === 'S');
    const other = items.filter(room => !['N','S'].includes(room.orientation));
    const sides = [['N',north],['S',south]];
    other.forEach((room,index) => sides[index % 2][1].push(room));
    const entries = [];
    sides.forEach(([side,list]) => list.forEach((room,index) => {
      const x = (index - (list.length - 1) / 2) * 2.12;
      const z = side === 'S' ? 1.72 : -1.72;
      const width = clamp(1.52 + (Number(room.area_m2 || 26) - 22) * 0.023, 1.5, 1.82);
      entries.push({ room, x, z, width, side });
    }));
    return [floor,entries];
  }));
}

function IncidentPulse({ color }) {
  const ref = useRef();
  useFrame(({ clock }) => {
    if (ref.current) {
      const pulse = 1 + Math.sin(clock.elapsedTime * 2.8) * 0.08;
      ref.current.scale.setScalar(pulse);
      ref.current.material.emissiveIntensity = 1.3 + (Math.sin(clock.elapsedTime * 2.8) + 1) * 0.35;
    }
  });
  return <mesh ref={ref} position={[0,0.36,0]}>
    <sphereGeometry args={[0.13,20,20]} />
    <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.5} toneMapped={false} />
  </mesh>;
}

function CameraAnimator({ controlsRef, mode, selected, floorY }) {
  const { camera, invalidate } = useThree();
  const target = useRef(new THREE.Vector3(0,0,0));
  const position = useRef(new THREE.Vector3(15,13,18));
  const transitioning = useRef(false);
  useEffect(()=>{
    if (selected) {
      target.current.set(selected.x,selected.y,selected.z);
      position.current.set(selected.x+5.8,selected.y+4.1,selected.z+7.8);
    } else if (mode==='floor') {
      target.current.set(0,floorY,0);
      position.current.set(12,floorY+9,14);
    } else {
      target.current.set(0,0,0);
      position.current.set(15,13,18);
    }
    transitioning.current=true;
    invalidate();
  },[mode,selected?.id,floorY]);
  useFrame((_,delta)=>{
    if(!transitioning.current)return;
    camera.position.lerp(position.current,1-Math.exp(-3.2*delta));
    const controls=controlsRef.current;
    if (controls) { controls.target.lerp(target.current,1-Math.exp(-2.5*delta)); controls.update(); }
    const cameraSettled=camera.position.distanceTo(position.current)<0.018;
    const targetSettled=!controls||controls.target.distanceTo(target.current)<0.018;
    if(cameraSettled&&targetSettled){camera.position.copy(position.current);if(controls){controls.target.copy(target.current);controls.update()}transitioning.current=false;}
    else invalidate();
  });
  return null;
}

function IncidentPin({ incident, index, height, onClick }) {
  const color=SEVERITY[String(incident.priority||'medium').toLowerCase()]||'#fbbf24';
  return <group position={[(index-0.5)*0.52,height+0.75,0]} onClick={(event)=>{event.stopPropagation();onClick(incident)}}>
    <IncidentPulse color={color}/>
    <mesh position={[0,0.05,0]}><cylinderGeometry args={[0.018,0.018,0.8,8]}/><meshBasicMaterial color={color}/></mesh>
    <mesh position={[0,0.48,0]}><sphereGeometry args={[0.19,16,16]}/><meshBasicMaterial color={color} transparent opacity={0.12}/></mesh>
    <Html position={[0,0.8,0]} center distanceFactor={20} style={{pointerEvents:'none'}}><div className="twin-incident-label">{incident.issue_type||incident.event_id||'Incident'}</div></Html>
  </group>;
}

function BuildingModel({ rooms, floors, mode, selectedFloor, incidents, selectedRoomId, onSelectRoom, onIncident, setHovered, controlsRef, selectedRoom, payload, statusLayer, showExits, showResources, showRoof, selectedResourceId, onSelectResource }) {
  const positioned=useMemo(()=>layoutRooms(rooms),[rooms]);
  const visibleFloors=mode==='floor'?floors.filter(f=>Number(f)===Number(selectedFloor)):floors;
  const gap=mode==='exploded'?3.25:STORY_HEIGHT;
  const sceneFloorY=mode==='floor'?0:(floor,index)=> (index-(floors.length-1)/2)*gap;
  const incidentByAsset=useMemo(()=>{
    const map=new Map();
    incidents.filter(incident=>ACTIVE_STATUSES.has(String(incident.status||'').toLowerCase())).forEach(incident=>(incident.affected_assets||[]).forEach(asset=>{
      if(rooms.some(room=>room.id===asset)&&!map.has(asset)) map.set(asset,incident);
    }));
    return map;
  },[incidents,rooms]);
  const roomPosition=selectedRoom?(()=>{
    const entries=positioned.get(Number(selectedRoom.floor))||[];
    const entry=entries.find(x=>x.room.id===selectedRoom.id);
    if(!entry)return null;
    const floorIndex=floors.indexOf(Number(selectedRoom.floor));
    const y=mode==='floor'?0:(floorIndex-(floors.length-1)/2)*gap;
    return {id:selectedRoom.id,x:entry.x,y:y+0.5,z:entry.z};
  })():null;
  const knownAssets=new Set(payload?.building?.assets?.map(asset=>asset.id)||[]);
  const globalIncidents=incidents.filter(item=>ACTIVE_STATUSES.has(String(item.status||'').toLowerCase())&&(item.affected_assets||[]).some(asset=>asset==='BUILDING'||(knownAssets.has(asset)&&!rooms.some(room=>room.id===asset))));
  const modelHeight=mode==='floor'?0:(floors.length-1)*gap/2;
  const tracking=showResources?(payload?.resource_tracking?.resources||[]).filter(r=>r.last_known_location):[];
  const half=Math.max(5.7,...[...positioned.values()].map(halfWidth));
  return <>
    <color attach="background" args={['#0b1017']}/>
    <fog attach="fog" args={['#0b1017',24,44]}/>
    <ambientLight intensity={1.2}/><hemisphereLight args={['#d8f0ff','#12202b',1.15]}/>
    <directionalLight position={[9,15,11]} intensity={2.5} color="#dff7ff" castShadow shadow-mapSize-width={1024} shadow-mapSize-height={1024}/>
    <pointLight position={[-6,6,-4]} intensity={28} color="#42d9c8" distance={22}/><pointLight position={[7,1,8]} intensity={12} color="#60a5fa" distance={18}/>
    <gridHelper args={[30,30,'#233341','#15222d']} position={[0,-modelHeight-0.5,0]}/>
    {visibleFloors.map((floor,index)=>{
      const allIndex=floors.indexOf(Number(floor));
      const y=typeof sceneFloorY==='function'?sceneFloorY(floor,allIndex):0;
      return <ArchitecturalFloor key={floor} floor={floor} entries={positioned.get(Number(floor))||[]} baseY={y} incidents={incidentByAsset} selectedRoomId={selectedRoomId} onSelectRoom={onSelectRoom} setHovered={setHovered}
        detailed={mode!=='building'} showExits={showExits} resources={tracking.filter(r=>Number(r.last_known_location.floor)===Number(floor)&&r.last_known_location.kind!=='entrance')}
        selectedResourceId={selectedResourceId} onSelectResource={onSelectResource} statusOf={room=>roomStatus(room,statusLayer)} statuses={STATUS} severity={SEVERITY}/>;
    })}
    {mode!=='floor'&&<BuildingEnvelope half={half} bottom={-modelHeight} top={modelHeight} showRoof={showRoof&&mode==='building'} showExits={showExits}
      entranceResources={tracking.filter(r=>r.last_known_location.kind==='entrance')} selectedResourceId={selectedResourceId} onSelectResource={onSelectResource}/>}
    {globalIncidents.map((incident,index)=><IncidentPin key={incident.event_id||index} incident={incident} index={index} height={modelHeight+1.4} onClick={onIncident}/>)}
    <CameraAnimator controlsRef={controlsRef} mode={mode} selected={roomPosition} floorY={0}/>
    <OrbitControls ref={controlsRef} makeDefault enableDamping dampingFactor={0.09} rotateSpeed={0.62} zoomSpeed={0.72} panSpeed={0.58} minDistance={8} maxDistance={32} minPolarAngle={0.28} maxPolarAngle={Math.PI*0.485} screenSpacePanning enablePan/>
  </>;
}

function SchematicFallback({rooms,selectedRoomId,onSelectRoom}) {
  const positioned=layoutRooms(rooms);
  const floors=[...positioned.keys()].sort((a,b)=>a-b);
  return <div className="twin-fallback"><div className="fallback-note">WEBGL UNAVAILABLE · SCHEMATIC FLOOR VIEW</div>{floors.map(floor=><div className="fallback-floor" key={floor}><small>FLOOR {floor}</small><div>{positioned.get(floor).map(entry=><button key={entry.room.id} className={`room-tile ${roomStatus(entry.room)} ${selectedRoomId===entry.room.id?'selected':''}`} onClick={()=>onSelectRoom(entry.room)}>{entry.room.id}</button>)}</div></div>)}</div>;
}

function ResourceDirectory({ resources, available, selectedId, onSelect }) {
  const stamp=value=>value?new Intl.DateTimeFormat('de-AT',{timeZone:'Europe/Vienna',dateStyle:'short',timeStyle:'short'}).format(new Date(value)):'—';
  const age=minutes=>minutes<60?`${Math.round(minutes)} min ago`:minutes<1440?`${Math.floor(minutes/60)} h ago`:`${Math.floor(minutes/1440)} d ago`;
  return <div className="resource-directory"><div className="section-eyebrow">LAST REPORTED · NOT LIVE TRACKING</div><h3>Team &amp; robots</h3><p>Positions are based on cleaning reports and granted door access. A scheduled shift does not establish presence.</p>
    {!available?<div className="muted-empty">Resource tracking is unavailable from the connected API. Restart the updated replay server.</div>:resources.map(resource=>{
      const location=resource.last_known_location, task=resource.last_task_report, selected=selectedId===resource.resource_id;
      return <div className={`tracked-resource ${selected?'selected':''}`} key={resource.resource_id}><button disabled={!location} onClick={()=>onSelect(resource)}>
        {resource.role==='robot'?<Bot size={19}/>:<UserRound size={19}/>}<span><strong>{resource.resource_id}</strong><small>{human(resource.role)} · {human(resource.shift_status)}</small></span><b>{location?.label||'Not located'}</b>
        <em>{location?`${age(location.age_minutes)} · ${stamp(location.timestamp_utc)}`:'No location observation by this replay time'}</em>
      </button>{selected&&location&&<div className="resource-observation"><span>Source: {location.source} · {human(location.event_type)}</span>{task&&<span>Last task report: {human(task.type)} · {stamp(task.timestamp_utc)}</span>}<span>Marker anchors to the reported room or area; no measured coordinates are available.</span></div>}</div>;
    })}
  </div>;
}

export default function BuildingTwin({rooms=[],payload,selectedRoomId,setSelectedRoomId,selectedDecisionId,setSelectedDecisionId,onJumpIncident,onOpenIncident}) {
  const [mode,setMode]=useState('building');
  const [selectedFloor,setSelectedFloor]=useState('1');
  const [hovered,setHovered]=useState(null);
  const [selectedIncident,setSelectedIncident]=useState(null);
  const [statusLayer,setStatusLayer]=useState('occupancy');
  const [showExits,setShowExits]=useState(true);
  const [showResources,setShowResources]=useState(true);
  const [showRoof,setShowRoof]=useState(true);
  const [inspectorTab,setInspectorTab]=useState('room');
  const [selectedResourceId,setSelectedResourceId]=useState(null);
  const controlsRef=useRef();
  const trackedResources=payload?.resource_tracking?.resources||[];
  const knownLocations=trackedResources.filter(r=>r.last_known_location);
  useEffect(()=>{if(selectedRoomId)setInspectorTab('room')},[selectedRoomId]);
  const selectedRoom=rooms.find(room=>room.id===selectedRoomId)||null;
  const floors=useMemo(()=>[...new Set(rooms.map(room=>Number(room.floor)).filter(Number.isFinite))].sort((a,b)=>a-b),[rooms]);
  const incidents=payload?.incidents||[], decisions=payload?.decisions||[];
  const hasActiveIncidents=incidents.some(item=>ACTIVE_STATUSES.has(String(item.status||'').toLowerCase()));
  const attachedIncidents=selectedRoom?incidents.filter(item=>(item.affected_assets||[]).includes(selectedRoom.id)):[];
  const attachedDecisions=selectedRoom?decisions.filter(item=>(item.affected_assets||[]).includes(selectedRoom.id)):[];
  const activeIncident=selectedIncident||attachedIncidents[0]||null;
  const currentStamp=payload?.replay?.timestamp_utc;
  const resetCamera=()=>{setSelectedRoomId(null);setSelectedIncident(null);setMode('building');setSelectedFloor(String(floors[0]||1));controlsRef.current?.reset?.()};
  const zoomBy=scale=>{
    const controls=controlsRef.current;
    if(!controls)return;
    const camera=controls.object;
    const offset=camera.position.clone().sub(controls.target);
    const distance=clamp(offset.length()*scale,8,32);
    camera.position.copy(controls.target.clone().add(offset.normalize().multiplyScalar(distance)));
    controls.update();
  };
  const selectRoom=(room)=>{setSelectedRoomId(room.id);setSelectedIncident(null);setSelectedResourceId(null);setInspectorTab('room')};
  const selectResource=resource=>{
    setSelectedResourceId(resource.resource_id);setInspectorTab('team');
    const location=resource.last_known_location;
    if(location?.floor!=null){setSelectedFloor(String(location.floor));setMode('floor');setSelectedRoomId(null)}
    else {setMode('building');setSelectedRoomId(null)}
  };
  const selectFloor=floor=>{setMode('floor');setSelectedFloor(String(floor));setSelectedRoomId(null)};
  const focusIncident=(incident)=>{
    setSelectedIncident(incident);
    onOpenIncident?.(incident);
    const affected=rooms.find(room=>(incident.affected_assets||[]).includes(room.id));
    if(affected){setSelectedRoomId(affected.id);setMode('building')}
  };
  const incidentColor=activeIncident?(SEVERITY[String(activeIncident.priority||'medium').toLowerCase()]||'#fbbf24'):null;
  const history=payload?.room_history||[];
  const tempValues=history.map(row=>row.temperature_c).filter(Number.isFinite);
  const minTemp=tempValues.length?Math.min(...tempValues):0,maxTemp=tempValues.length?Math.max(...tempValues):1;
  const points=tempValues.map((v,i)=>`${i*100/Math.max(1,tempValues.length-1)},${34-(v-minTemp)/(maxTemp-minTemp||1)*27}`).join(' ');
  const signal=key=>selectedRoom?.signals?.[key];
  return <section className="twin-feature" id="building">
    <div className="twin-section-head"><div><div className="section-eyebrow">PLATO-40 · SYNTHETIC REFERENCE BUILDING</div><h2>Building digital twin</h2><p>Interactive schematic · actual room IDs and replay state · {rooms.length} mapped rooms</p></div><span className="replay-badge"><span className="replay-dot"/> HISTORICAL REPLAY · {currentStamp?new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Vienna',dateStyle:'medium',timeStyle:'short'}).format(new Date(currentStamp)):'—'}</span></div>
    <div className="twin-layout">
      <article className="twin-viewport panel"><div className="twin-toolbar"><div className="twin-mode-switch" role="group" aria-label="Building view"><button className={mode==='building'?'active':''} onClick={()=>{setMode('building');setSelectedRoomId(null)}}><Building2 size={14}/>Building</button><button className={mode==='floor'?'active':''} onClick={()=>{setMode('floor');setSelectedRoomId(null)}}><Box size={14}/>Floor</button><button className={mode==='exploded'?'active':''} onClick={()=>{setMode('exploded');setSelectedRoomId(null)}}><Layers3 size={14}/>Exploded</button></div><div className="twin-toolbar-right">{mode==='floor'&&<label className="twin-floor-select"><span>FLOOR</span><select value={selectedFloor} onChange={e=>setSelectedFloor(e.target.value)}>{floors.map(f=><option key={f}>{f}</option>)}</select><ChevronDown size={12}/></label>}<button className="icon-quiet" onClick={()=>zoomBy(0.78)} aria-label="Zoom in" title="Zoom in"><ZoomIn size={15}/></button><button className="icon-quiet" onClick={()=>zoomBy(1.28)} aria-label="Zoom out" title="Zoom out"><ZoomOut size={15}/></button><button className="icon-quiet" onClick={resetCamera} aria-label="Reset camera" title="Reset camera"><RotateCcw size={15}/></button></div></div>
        <div className="twin-layer-toolbar"><div className="twin-layer-select" role="group" aria-label="Room color layer"><button aria-pressed={statusLayer==='occupancy'} onClick={()=>setStatusLayer('occupancy')}>Room status</button><button aria-pressed={statusLayer==='housekeeping'} onClick={()=>setStatusLayer('housekeeping')}>Housekeeping</button></div><div className="twin-layer-toggles"><button aria-pressed={showResources} onClick={()=>setShowResources(v=>!v)}><Bot size={13}/>Team</button><button aria-pressed={showExits} onClick={()=>setShowExits(v=>!v)}><DoorOpen size={13}/>Exits</button>{mode==='building'&&<button aria-pressed={showRoof} onClick={()=>setShowRoof(v=>!v)}><Layers3 size={13}/>Roof</button>}</div></div>
        <div className="twin-status-summary"><span><i style={{background:STATUS.occupied.color}}/><b>{rooms.filter(r=>r.occupancy?.occupied===true).length}</b> occupied</span><span><i style={{background:STATUS.clean.color}}/><b>{rooms.filter(r=>r.readiness?.state==='clean').length}</b> clean reported</span><span><i style={{background:STATUS.cleaning.color}}/><b>{rooms.filter(r=>r.readiness?.state==='in_progress').length}</b> cleaning</span><span><i style={{background:STATUS.attention.color}}/><b>{rooms.filter(r=>['dirty','inspection_required'].includes(r.readiness?.state)).length}</b> to service</span></div>
        <div className="twin-canvas-wrap"><Canvas shadows dpr={[1,1.5]} camera={{position:[15,13,18],fov:35}} gl={{antialias:true,powerPreference:'low-power'}} frameloop={hasActiveIncidents?'always':'demand'} fallback={<SchematicFallback rooms={rooms} selectedRoomId={selectedRoomId} onSelectRoom={selectRoom}/>}><Suspense fallback={null}><BuildingModel rooms={rooms} floors={floors} mode={mode} selectedFloor={Number(selectedFloor)} incidents={incidents} selectedRoomId={selectedRoomId} onSelectRoom={selectRoom} onIncident={focusIncident} setHovered={setHovered} controlsRef={controlsRef} selectedRoom={selectedRoom} payload={payload} statusLayer={statusLayer} showExits={showExits} showResources={showResources} showRoof={showRoof} selectedResourceId={selectedResourceId} onSelectResource={selectResource}/></Suspense></Canvas>
          <div className="twin-watermark"><span>ARCHITECTURAL SCHEMATIC</span><span>EXITS &amp; STRUCTURE ILLUSTRATIVE</span></div>
          {hovered&&<div className="twin-hover-card"><strong>{hovered.id}</strong><span>Floor {hovered.floor} · {STATUS[roomStatus(hovered,statusLayer)].label}</span>{hovered.signals?.temperature_c?.value!=null&&<small>{Number(hovered.signals.temperature_c.value).toFixed(1)} °C</small>}</div>}
          <div className="twin-scene-caption"><span><i className="staff-dot"/>Housekeeping</span><span><i className="robot-dot"/>Robot</span><small>Last reported positions</small></div>
          <div className="twin-canvas-hint"><MousePointer2 size={12}/> Drag to orbit · scroll to zoom · right drag to pan</div>
        </div>
        <div className="twin-legend">{Object.entries(STATUS).filter(([key])=>statusLayer==='housekeeping'?!['occupied','vacant_clean'].includes(key):key!=='clean').map(([key,value])=><span key={key}><i style={{background:value.color}}/>{value.label}</span>)}<span><i className="incident-legend-dot"/>Detected incident</span></div>
      </article>
      <aside className="twin-inspector panel">
        <div className="twin-inspector-tabs" role="group" aria-label="Inspector content"><button aria-pressed={inspectorTab==='room'} onClick={()=>setInspectorTab('room')}><Box size={13}/> Rooms</button><button aria-pressed={inspectorTab==='team'} onClick={()=>setInspectorTab('team')}><Bot size={14}/> Team &amp; robots <span>{trackedResources.length||'—'}</span></button></div>
        {inspectorTab==='team'?<ResourceDirectory resources={trackedResources} available={!!payload?.resource_tracking} selectedId={selectedResourceId} onSelect={selectResource}/>:<>
        <div className="twin-inspector-head"><div><div className="section-eyebrow">ROOM INSPECTOR</div><h3>{selectedRoom?.id||'Select a room'}</h3></div>{selectedRoom&&<span className={`state-pill ${roomStatus(selectedRoom)}`}>{STATUS[roomStatus(selectedRoom)].label}</span>}</div>
        {activeIncident&&<div className="twin-focused-incident" style={{'--incident-color':incidentColor}}><span><Radio size={13}/> FOCUSED DETECTION</span><strong>{activeIncident.issue_type||activeIncident.title||'Incident record'}</strong><small>{(activeIncident.affected_assets||[]).join(', ')||'Affected asset unspecified'} · {activeIncident.priority||'priority unspecified'} · {activeIncident.status||'status unspecified'}</small></div>}
        {selectedRoom?<>
          <div className="room-facts-grid"><div><small>FLOOR · AREA</small><b>{selectedRoom.floor} · {selectedRoom.area_m2??'—'} m²</b></div><div><small>OCCUPANCY</small><b>{selectedRoom.occupancy?.occupied===true?'Occupied':selectedRoom.occupancy?.occupied===false?'Vacant':'Unknown'}</b><em>{selectedRoom.occupancy?.known?'Known from events':'No occupancy event'}</em></div><div><small>READINESS</small><b>{selectedRoom.readiness?.state?human(selectedRoom.readiness.state):'Unknown'}</b><em>{selectedRoom.readiness?.quality||'missing'} quality</em></div><div><small>TEMPERATURE · SETPOINT</small><b>{signal('temperature_c')?.value==null?'—':`${fmt(signal('temperature_c').value)}°`} / {signal('setpoint_c')?.value==null?'—':`${fmt(signal('setpoint_c').value)}°`}</b></div><div><small>CO₂ · HUMIDITY</small><b>{signal('co2_ppm')?.value==null?'—':`${fmt(signal('co2_ppm').value,0)} ppm`} · {signal('humidity_pct')?.value==null?'—':`${fmt(signal('humidity_pct').value)}%`}</b></div><div><small>PRESENCE · WATER</small><b>{signal('presence')?.value==null?'—':signal('presence').value?'Motion detected':'No motion'} · {signal('water_flow_lph')?.value==null?'—':`${fmt(signal('water_flow_lph').value)} L/h`}</b></div></div>
          <div className="room-trend"><div className="twin-mini-heading"><span>ROOM TEMPERATURE</span><span>24H WINDOW</span></div>{points?<svg viewBox="0 0 100 40" preserveAspectRatio="none"><line x1="0" x2="100" y1="34" y2="34"/><polyline points={points}/></svg>:<div className="trend-unavailable">Room history is unavailable for this replay window.</div>}{points&&<div className="trend-range"><span>{fmt(minTemp)}°</span><span>{fmt(maxTemp)}°</span></div>}</div>
          <div className="inspector-list"><div className="twin-mini-heading"><span>RELATED INCIDENTS</span><b>{attachedIncidents.length||'—'}</b></div>{attachedIncidents.length?attachedIncidents.slice(0,3).map((incident,index)=><button className="twin-related-row" key={incident.event_id||index} onClick={()=>focusIncident(incident)}><i style={{background:SEVERITY[String(incident.priority||'medium').toLowerCase()]||'#fbbf24'}}/><span><strong>{incident.issue_type||incident.title||'Incident'}</strong><small>{incident.priority||'priority unspecified'} · {incident.status||'status unspecified'}</small></span><Radio size={13}/></button>):<div className="trend-unavailable">No incident record linked to this room.</div>}</div>
          <div className="inspector-list"><div className="twin-mini-heading"><span>AI DECISIONS</span><b>{attachedDecisions.length||'—'}</b></div>{attachedDecisions.length?attachedDecisions.slice(0,3).map((decision,index)=><button className="twin-related-row" key={decision.event_id||index} onClick={()=>setSelectedDecisionId(decision.event_id)}><i className="decision-legend-dot"/><span><strong>{decision.action||'Decision'}</strong><small>{decision.priority||'priority unspecified'}{decision.requires_human_approval?' · approval required':''}</small></span><Sparkles size={13}/></button>):<div className="trend-unavailable">Decision log unavailable or no decision linked to this room.</div>}</div>
        </>:<div className="building-directory"><p>Select a room to inspect its replay state, or open a floor for a closer look.</p><div className="building-floor-directory">{[...floors].reverse().map(floor=>{
            const floorRooms=rooms.filter(room=>Number(room.floor)===floor);
            return <button key={floor} onClick={()=>selectFloor(floor)}><span className="floor-number">{String(floor).padStart(2,'0')}</span><span><strong>Floor {floor}</strong><small>{floorRooms.length} rooms · {floorRooms.filter(r=>r.occupancy?.occupied===true).length} occupied</small></span><div className="floor-state-dots">{floorRooms.map(room=><i key={room.id} title={`${room.id}: ${STATUS[roomStatus(room,statusLayer)].label}`} style={{background:STATUS[roomStatus(room,statusLayer)].color}}/>)}</div></button>
          })}</div><div className="building-people-summary"><div><UserRound size={17}/><strong>{knownLocations.length} / {trackedResources.length||'—'}</strong></div><span>resources with a last reported position</span><button onClick={()=>setInspectorTab('team')}>Inspect team &amp; robots →</button></div><div className="building-plan-note"><DoorOpen size={15}/><span>Green EXIT signs identify illustrative stair cores. Their placement is schematic.</span></div></div>}
        </>}
        <div className="agent-unavailable"><span>AGENT ACTIVITY</span><b>Not connected to an agent event stream</b></div>
      </aside>
    </div>
    <div className="twin-footnote"><span>3D SCHEMATIC · NOT A SURVEYED BUILDING MODEL</span><span>Room states use replay data. Architecture and exits are illustrative; staff positions show the latest recorded observation.</span></div>
  </section>;
}
