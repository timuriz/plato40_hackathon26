import React, { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Html, Line, OrbitControls, Text } from '@react-three/drei';
import { Box, RotateCcw, Layers3, Building2, ChevronDown, MousePointer2, Radio, Sparkles, ZoomIn, ZoomOut, Bot, UserRound, DoorOpen, MapPin, Clock3, Gauge } from 'lucide-react';
import * as THREE from 'three';
import { ArchitecturalFloor, BuildingEnvelope, TechnicalAssets } from './BuildingArchitecture.jsx';
import { buildRoomLayout as layoutRooms, buildAssetLayout, STORY_HEIGHT, ROOM_OUTER_Z, halfWidth } from './buildingLayout.js';

const STATUS = {
  clean: { color: '#4ed8c7', label: 'Clean reported' },
  occupied: { color: '#5fa8ff', label: 'Occupied' },
  vacant_clean: { color: '#4ed8c7', label: 'Vacant · clean reported' },
  cleaning: { color: '#f5c451', label: 'Cleaning' },
  dirty: { color: '#f59e42', label: 'Dirty' },
  attention: { color: '#ff7a8a', label: 'Inspection required' },
  unknown: { color: '#6b7280', label: 'Unknown' },
};
const SEVERITY = { critical: '#ff5e7a', high: '#ff5e7a', medium: '#ff5e7a', low: '#ff5e7a' };
const ACTIVE_STATUSES = new Set(['open','active','acknowledged','in_progress','investigating','new','detected','escalated']);
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

function CameraAnimator({ controlsRef, mode, selected, half, floorCount, gap, hasLobby, groundFloorSelected, resetVersion, transitioning }) {
  const { camera, invalidate, size } = useThree();
  const target = useRef(new THREE.Vector3());
  const position = useRef(new THREE.Vector3());
  useEffect(()=>{
    if (selected) {
      target.current.set(selected.x,selected.y,selected.z);
      position.current.set(selected.x+5.0,selected.y+4.8,selected.z+6.5);
    } else {
      const aspect=Math.max(.4,size.width/Math.max(size.height,1));
      const vFov=THREE.MathUtils.degToRad(camera.fov||35), tan=Math.tan(vFov/2);
      const groundLobby=mode==='floor'&&hasLobby&&groundFloorSelected;
      const width=(half+(mode==='floor'?(groundLobby?3.35:1.4):3.3))*2, height=mode==='floor'?3.4:Math.max(4,(floorCount-1)*gap+STORY_HEIGHT+1.6);
      const distance=Math.max(width/(2*tan*aspect),height/(2*tan))*1.08+3.0;
      const direction=(mode==='floor'?new THREE.Vector3(.74,1.35,1.05):new THREE.Vector3(1.05,.82,1.38)).normalize();
      target.current.set(groundLobby?1.05:mode==='floor'?0:.75,mode==='floor'?.35:.55,0);
      position.current.copy(target.current).addScaledVector(direction,distance);
    }
    transitioning.current=true;invalidate();
  },[mode,selected?.id,half,floorCount,gap,hasLobby,groundFloorSelected,size.width,size.height,resetVersion]);
  useFrame((_,delta)=>{
    if(!transitioning.current)return;
    camera.position.lerp(position.current,1-Math.exp(-3.5*delta));
    const controls=controlsRef.current;
    if(controls){controls.target.lerp(target.current,1-Math.exp(-2.8*delta));controls.update()}
    if(camera.position.distanceTo(position.current)<.025&&(!controls||controls.target.distanceTo(target.current)<.025)){
      camera.position.copy(position.current);if(controls){controls.target.copy(target.current);controls.update()}transitioning.current=false;
    }else invalidate();
  });
  return null;
}

function IncidentPin({ incident, index, height, onClick }) {
  const color=SEVERITY[String(incident.priority||incident.severity||'medium').toLowerCase()]||'#fbbf24';
  return <group position={[(index-0.5)*0.52,height+0.75,0]} onClick={(event)=>{event.stopPropagation();onClick(incident)}}>
    <IncidentPulse color={color}/>
    <mesh position={[0,0.05,0]}><cylinderGeometry args={[0.018,0.018,0.8,8]}/><meshBasicMaterial color={color}/></mesh>
    <mesh position={[0,0.48,0]}><sphereGeometry args={[0.19,16,16]}/><meshBasicMaterial color={color} transparent opacity={0.12}/></mesh>
    <Html position={[0,0.8,0]} center distanceFactor={20} style={{pointerEvents:'none'}}><div className="twin-incident-label">{incident.issue_type||incident.event_id||'Incident'}</div></Html>
  </group>;
}

function BuildingModel({ rooms, floors, mode, selectedFloor, incidents, selectedRoomId, onSelectRoom, onIncident, setHovered, controlsRef, selectedRoom, payload, statusLayer, showExits, showResources, showRoof, selectedResourceId, onSelectResource, selectedAsset, onSelectAsset, showAssets, resetVersion }) {
  const transitionRef=useRef(false);
  const positioned=useMemo(()=>layoutRooms(rooms),[rooms]);
  const visibleFloors=mode==='floor'?floors.filter(f=>Number(f)===Number(selectedFloor)):floors;
  const gap=mode==='exploded'?3.35:STORY_HEIGHT;
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
  const activeIncidents=incidents.filter(item=>ACTIVE_STATUSES.has(String(item.status||'').toLowerCase()));
  const globalIncidents=activeIncidents.filter(item=>(item.affected_assets||[]).includes('BUILDING'));
  const modelHeight=mode==='floor'?0:(floors.length-1)*gap/2;
  const tracking=showResources?(payload?.resource_tracking?.resources||[]).filter(r=>r.last_known_location):[];
  const half=Math.max(1.5,...[...positioned.values()].map(halfWidth));
  const placements=buildAssetLayout(payload?.building?.assets||[],floors,half,gap,mode);
  const assetPlacement=placements.find(item=>item.asset.id===selectedAsset);
  const assetPosition=assetPlacement?{id:`asset:${selectedAsset}`,x:assetPlacement.position[0],y:assetPlacement.position[1],z:assetPlacement.position[2]}:null;
  const commonAreas=payload?.building?.common_areas||[];
  const maxDistance=Math.max(45,(half+4)*8,(floors.length*gap+6)*4);
  const groundY=mode==='floor'?-.24:-modelHeight-.38;
  const groundSize=Math.max(30,half*5);
  return <>
    <color attach="background" args={['#0d1016']}/>
    <ambientLight intensity={1.0}/><hemisphereLight args={['#e4edf4','#3a4652',1.25]}/>
    <directionalLight position={[9,17,12]} intensity={2.0} color="#fff5e7" castShadow shadow-mapSize-width={1024} shadow-mapSize-height={1024} shadow-camera-left={-20} shadow-camera-right={20} shadow-camera-top={20} shadow-camera-bottom={-20} shadow-bias={-.0002}/>
    <mesh rotation={[-Math.PI/2,0,0]} position={[0,groundY-.005,0]} receiveShadow>
      <planeGeometry args={[groundSize,groundSize]}/><meshStandardMaterial color="#131b25" roughness={1}/>
    </mesh>
    <gridHelper args={[groundSize,30,'#25333e','#1a242e']} position={[0,groundY+.003,0]}/>
    {visibleFloors.map((floor,index)=>{
      const allIndex=floors.indexOf(Number(floor));
      const y=typeof sceneFloorY==='function'?sceneFloorY(floor,allIndex):0;
      return <ArchitecturalFloor key={floor} floor={floor} entries={positioned.get(Number(floor))||[]} baseY={y} half={half} mode={mode} isGroundFloor={Number(floor)===Number(floors[0])} isTopFloor={Number(floor)===Number(floors[floors.length-1])} hasLobby={commonAreas.includes('lobby')} buildingName={payload?.building?.name} showRoof={showRoof} incidents={incidentByAsset} selectedRoomId={selectedRoomId} onSelectRoom={onSelectRoom} setHovered={setHovered}
        showExits={showExits} resources={tracking.filter(r=>(Number(r.last_known_location.floor)===Number(floor)&&r.last_known_location.kind!=='entrance')||(mode==='floor'&&Number(floor)===Number(floors[0])&&r.last_known_location.kind==='entrance'))}
        selectedResourceId={selectedResourceId} onSelectResource={onSelectResource} statusOf={room=>roomStatus(room,statusLayer)} statuses={STATUS} severity={SEVERITY}/>;
    })}
    {mode!=='floor'&&<BuildingEnvelope half={half} bottom={-modelHeight} top={modelHeight} mode={mode} floorCount={floors.length} showRoof={showRoof&&mode==='building'} showExits={showExits}
      entranceResources={tracking.filter(r=>r.last_known_location.kind==='entrance')} selectedResourceId={selectedResourceId} onSelectResource={onSelectResource} hasLobby={commonAreas.includes('lobby')}/>}
    {showAssets&&<TechnicalAssets placements={placements} selectedAsset={selectedAsset} incidents={activeIncidents} onSelectAsset={onSelectAsset} bottom={-modelHeight} half={half} mode={mode} selectedFloor={selectedFloor}/>}
    {globalIncidents.map((incident,index)=><IncidentPin key={incident.event_id||index} incident={incident} index={index} height={modelHeight+1.4} onClick={onIncident}/>)}
    <CameraAnimator controlsRef={controlsRef} mode={mode} selected={roomPosition||assetPosition} half={half} floorCount={floors.length} gap={gap} hasLobby={commonAreas.includes('lobby')} groundFloorSelected={Number(selectedFloor)===Number(floors[0])} resetVersion={resetVersion} transitioning={transitionRef}/>
    <OrbitControls ref={controlsRef} makeDefault onStart={()=>{transitionRef.current=false}} enableDamping dampingFactor={0.09} rotateSpeed={0.62} zoomSpeed={0.72} panSpeed={0.58} minDistance={5.5} maxDistance={maxDistance} minPolarAngle={0.28} maxPolarAngle={Math.PI*0.485} screenSpacePanning enablePan/>
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

function AssetDirectory({ assets, selectedId, payload, onSelect, onOpenIncident }) {
  const selected=assets.find(asset=>asset.id===selectedId);
  const context=payload?.operational_context||{}, readings={...context.equipment,...context.logistics};
  const quality={...context.equipment_quality,...context.logistics_quality};
  const measured=selected?Object.entries(readings).filter(([key])=>key.startsWith(`${selected.id}.`)):[];
  const stamp=value=>value?new Intl.DateTimeFormat('de-AT',{timeZone:'Europe/Vienna',dateStyle:'short',timeStyle:'short'}).format(new Date(value)):'Time unavailable';
  const special=selected?.type==='pv'?[['PV output',payload?.metrics?.pv_kw,'kW']]:selected?.type==='battery'?[['Battery charge',payload?.metrics?.battery_soc_kwh,'kWh']]:selected?.type==='water_meter'?[['Water flow',payload?.metrics?.water_main_flow_lph,'L/h']]:[];
  const linked=selected?(payload?.incidents||[]).filter(incident=>(incident.affected_assets||[]).includes(selected.id)):[];
  return <div className="resource-directory asset-directory"><div className="section-eyebrow">BUILDING EQUIPMENT · SCHEMATIC LOCATIONS</div><h3>Building assets</h3><p>Equipment comes from building configuration. Positions in the model are illustrative and are not surveyed.</p>
    {selected&&<div className="twin-asset-detail"><span className="section-eyebrow">SELECTED EQUIPMENT</span><h4>{selected.id}</h4><p>{selected.name||human(selected.type)}{selected.floor!=null?` · Floor ${selected.floor}`:selected.location?` · ${human(selected.location)}`:' · Location not supplied'}</p>
      {selected.serves&&<p>{selected.serves}</p>}
      <div className="twin-asset-readings">{measured.map(([key,value])=><div key={key}><small>{human(key.slice(selected.id.length+1))}</small><b>{value==null?'—':fmt(value,2)}</b><em>{quality[key]||'quality unavailable'} · {stamp(key in (context.logistics||{})?context.logistics_timestamp_utc:context.equipment_timestamp_utc)}</em></div>)}{special.map(([label,value,unit])=><div key={label}><small>{label}</small><b>{value==null?'—':`${fmt(value,2)} ${unit}`}</b><em>At replay time</em></div>)}</div>
      {!measured.length&&!special.length&&<p>No current measurements are connected for this asset.</p>}
      <div className="twin-mini-heading"><span>RELATED INCIDENTS</span><b>{linked.length}</b></div>{linked.length?linked.slice(0,4).map((incident,index)=><button key={incident.episode_id||incident.event_id||index} className="twin-related-row" onClick={()=>onOpenIncident?.(incident)}><i style={{background:SEVERITY[String(incident.priority||incident.severity||'medium').toLowerCase()]||'#ff7a8a'}}/><span><strong>{incident.title||human(incident.issue_type)}</strong><small>{incident.status||'Status not supplied'}</small></span><Radio size={13}/></button>):<p>No linked incident at this replay time.</p>}
    </div>}
    <div className="twin-asset-list">{assets.length?assets.map(asset=><button key={asset.id} className={selectedId===asset.id?'selected':''} onClick={()=>onSelect(asset)}><span className="twin-asset-icon"><Gauge size={15}/></span><span><strong>{asset.id}</strong><small>{asset.name||human(asset.type)}</small></span><em>{asset.floor!=null?`F${asset.floor}`:asset.location?human(asset.location):'Schematic'}</em></button>):<div className="muted-empty">No equipment metadata available from the connected API.</div>}</div>
  </div>;
}

export default function BuildingTwin({rooms=[],payload,selectedRoomId,setSelectedRoomId,selectedDecisionId,setSelectedDecisionId,onJumpIncident,onOpenIncident,selectedAsset,onSelectAsset}) {
  const [mode,setMode]=useState('building');
  const [selectedFloor,setSelectedFloor]=useState('1');
  const [hovered,setHovered]=useState(null);
  const [selectedIncident,setSelectedIncident]=useState(null);
  const [statusLayer,setStatusLayer]=useState('occupancy');
  const [showExits,setShowExits]=useState(true);
  const [showResources,setShowResources]=useState(true);
  const [showRoof,setShowRoof]=useState(true);
  const [showAssets,setShowAssets]=useState(true);
  const [resetVersion,setResetVersion]=useState(0);
  const [inspectorTab,setInspectorTab]=useState('room');
  const [selectedResourceId,setSelectedResourceId]=useState(null);
  const controlsRef=useRef();
  const trackedResources=payload?.resource_tracking?.resources||[];
  const knownLocations=trackedResources.filter(r=>r.last_known_location);
  useEffect(()=>{if(selectedRoomId)setInspectorTab('room')},[selectedRoomId]);
  useEffect(()=>{if(selectedAsset){setInspectorTab('assets');setShowAssets(true);const asset=payload?.building?.assets?.find(item=>item.id===selectedAsset);if(String(asset?.location||'').toLowerCase()==='lobby'){setMode('floor');setSelectedFloor(String(floors[0]||1))}else if(mode==='floor'&&asset?.floor==null)setMode('building');else if(mode==='floor'&&asset?.floor!=null)setSelectedFloor(String(asset.floor))}},[selectedAsset]);
  const selectedRoom=rooms.find(room=>room.id===selectedRoomId)||null;
  useEffect(()=>{
    if(selectedRoom && mode==='floor') setSelectedFloor(String(selectedRoom.floor));
  },[selectedRoom?.id,mode]);
  const floors=useMemo(()=>[...new Set(rooms.filter(room=>room.floor!=null).map(room=>Number(room.floor)).filter(Number.isFinite))].sort((a,b)=>a-b),[rooms]);
  const incidents=payload?.incidents||[], decisions=payload?.decisions||[];
  const attachedIncidents=selectedRoom?incidents.filter(item=>(item.affected_assets||[]).includes(selectedRoom.id)):[];
  const attachedDecisions=selectedRoom?decisions.filter(item=>(item.affected_assets||[]).includes(selectedRoom.id)):[];
  const incidentKey=item=>item?.episode_id||item?.incident_id||item?.event_id;
  const activeIncident=(selectedIncident&&incidents.find(item=>incidentKey(item)===incidentKey(selectedIncident)))||attachedIncidents[0]||null;
  const currentStamp=payload?.replay?.timestamp_utc;
  const resetCamera=()=>{setSelectedRoomId(null);onSelectAsset?.(null);setSelectedIncident(null);setMode('building');setSelectedFloor(String(floors[0]||1));setResetVersion(v=>v+1)};
  const zoomBy=scale=>{
    const controls=controlsRef.current;
    if(!controls)return;
    const camera=controls.object;
    const offset=camera.position.clone().sub(controls.target);
    const distance=clamp(offset.length()*scale,controls.minDistance,controls.maxDistance);
    camera.position.copy(controls.target.clone().add(offset.normalize().multiplyScalar(distance)));
    controls.update();
  };
  const selectRoom=(room)=>{onSelectAsset?.(null);setSelectedRoomId(room.id);setSelectedIncident(null);setSelectedResourceId(null);setInspectorTab('room')};
  const selectResource=resource=>{
    onSelectAsset?.(null);setSelectedResourceId(resource.resource_id);setInspectorTab('team');
    const location=resource.last_known_location;
    if(location?.floor!=null){setSelectedFloor(String(location.floor));setMode('floor');setSelectedRoomId(null)}
    else {setMode('building');setSelectedRoomId(null)}
  };
  const selectAsset=asset=>{setSelectedRoomId(null);setSelectedIncident(null);setShowAssets(true);setInspectorTab('assets');if(String(asset.location||'').toLowerCase()==='lobby'){setMode('floor');setSelectedFloor(String(floors[0]||1))}else if(mode==='floor'&&asset.floor==null)setMode('building');else if(mode==='floor'&&asset.floor!=null)setSelectedFloor(String(asset.floor));onSelectAsset?.(asset.id)};
  const selectFloor=floor=>{onSelectAsset?.(null);setMode('floor');setSelectedFloor(String(floor));setSelectedRoomId(null)};
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
      <article className="twin-viewport panel"><div className="twin-toolbar"><div className="twin-mode-switch" role="group" aria-label="Building view"><button className={mode==='building'?'active':''} onClick={()=>{onSelectAsset?.(null);setMode('building');setSelectedRoomId(null)}}><Building2 size={14}/>Building</button><button className={mode==='floor'?'active':''} onClick={()=>{onSelectAsset?.(null);setMode('floor');setSelectedRoomId(null)}}><Box size={14}/>Floor</button><button className={mode==='exploded'?'active':''} onClick={()=>{onSelectAsset?.(null);setMode('exploded');setSelectedRoomId(null)}}><Layers3 size={14}/>Exploded</button></div><div className="twin-toolbar-right">{mode==='floor'&&<label className="twin-floor-select"><span>FLOOR</span><select value={selectedFloor} onChange={e=>setSelectedFloor(e.target.value)}>{floors.map(f=><option key={f}>{f}</option>)}</select><ChevronDown size={12}/></label>}<button className="icon-quiet" onClick={()=>zoomBy(0.78)} aria-label="Zoom in" title="Zoom in"><ZoomIn size={15}/></button><button className="icon-quiet" onClick={()=>zoomBy(1.28)} aria-label="Zoom out" title="Zoom out"><ZoomOut size={15}/></button><button className="icon-quiet" onClick={resetCamera} aria-label="Reset camera" title="Reset camera"><RotateCcw size={15}/></button></div></div>
        <div className="twin-layer-toolbar"><div className="twin-layer-select" role="group" aria-label="Room color layer"><button aria-pressed={statusLayer==='occupancy'} onClick={()=>setStatusLayer('occupancy')}>Room status</button><button aria-pressed={statusLayer==='housekeeping'} onClick={()=>setStatusLayer('housekeeping')}>Housekeeping</button></div><div className="twin-layer-toggles"><button aria-pressed={showResources} onClick={()=>setShowResources(v=>!v)}><Bot size={13}/>Team</button><button aria-pressed={showAssets} onClick={()=>setShowAssets(v=>!v)}><Gauge size={13}/>Assets</button><button aria-pressed={showExits} onClick={()=>setShowExits(v=>!v)}><DoorOpen size={13}/>Exits</button>{mode==='building'&&<button aria-pressed={showRoof} onClick={()=>setShowRoof(v=>!v)}><Layers3 size={13}/>Roof</button>}</div></div>
        <div className="twin-status-summary"><span><i style={{background:STATUS.occupied.color}}/><b>{rooms.filter(r=>r.occupancy?.occupied===true).length}</b> occupied</span><span><i style={{background:STATUS.clean.color}}/><b>{rooms.filter(r=>r.readiness?.state==='clean').length}</b> clean reported</span><span><i style={{background:STATUS.cleaning.color}}/><b>{rooms.filter(r=>r.readiness?.state==='in_progress').length}</b> cleaning</span><span><i style={{background:STATUS.attention.color}}/><b>{rooms.filter(r=>['dirty','inspection_required'].includes(r.readiness?.state)).length}</b> to service</span></div>
        <div className="twin-canvas-wrap"><Canvas shadows dpr={[1,1.5]} camera={{position:[15,13,18],fov:35}} gl={{antialias:true,powerPreference:'low-power'}} frameloop="demand" fallback={<SchematicFallback rooms={rooms} selectedRoomId={selectedRoomId} onSelectRoom={selectRoom}/>}><Suspense fallback={null}><BuildingModel rooms={rooms} floors={floors} mode={mode} selectedFloor={Number(selectedFloor)} incidents={incidents} selectedRoomId={selectedRoomId} onSelectRoom={selectRoom} onIncident={focusIncident} setHovered={setHovered} controlsRef={controlsRef} selectedRoom={selectedRoom} payload={payload} statusLayer={statusLayer} showExits={showExits} showResources={showResources} showRoof={showRoof} selectedResourceId={selectedResourceId} onSelectResource={selectResource} selectedAsset={selectedAsset} onSelectAsset={selectAsset} showAssets={showAssets} resetVersion={resetVersion}/></Suspense></Canvas>
          {selectedAsset&&<div className="twin-selected-asset"><Radio size={13}/> SELECTED ASSET · {selectedAsset}</div>}
          <div className="twin-watermark"><span>ARCHITECTURAL SCHEMATIC</span><span>EXITS &amp; STRUCTURE ILLUSTRATIVE</span></div>
          {hovered&&<div className="twin-hover-card"><strong>{hovered.id}</strong><span>Floor {hovered.floor} · {STATUS[roomStatus(hovered,statusLayer)].label}</span>{hovered.signals?.temperature_c?.value!=null&&<small>{Number(hovered.signals.temperature_c.value).toFixed(1)} °C</small>}</div>}
          <div className="twin-scene-caption"><span><i className="staff-dot"/>Housekeeping</span><span><i className="robot-dot"/>Robot</span><small>Last reported positions</small></div>
          <div className="twin-canvas-hint"><MousePointer2 size={12}/> Drag to orbit · scroll to zoom · right drag to pan</div>
        </div>
        <div className="twin-legend">{Object.entries(STATUS).filter(([key])=>statusLayer==='housekeeping'?!['occupied','vacant_clean'].includes(key):key!=='clean').map(([key,value])=><span key={key}><i style={{background:value.color}}/>{value.label}</span>)}<span><i className="incident-legend-dot"/>Detected incident</span>{mode!=='building'&&<span>Room dots: occupancy · housekeeping</span>}</div>
      </article>
      <aside className="twin-inspector panel">
        <div className="twin-inspector-tabs" role="group" aria-label="Inspector content"><button aria-pressed={inspectorTab==='room'} onClick={()=>setInspectorTab('room')}><Box size={13}/> Rooms</button><button aria-pressed={inspectorTab==='team'} onClick={()=>setInspectorTab('team')}><Bot size={14}/> Team <span>{trackedResources.length||'—'}</span></button><button aria-pressed={inspectorTab==='assets'} onClick={()=>setInspectorTab('assets')}><Gauge size={14}/> Assets <span>{payload?.building?.assets?.length||'—'}</span></button></div>
        {inspectorTab==='team'?<ResourceDirectory resources={trackedResources} available={!!payload?.resource_tracking} selectedId={selectedResourceId} onSelect={selectResource}/>:inspectorTab==='assets'?<AssetDirectory assets={payload?.building?.assets||[]} selectedId={selectedAsset} payload={payload} onSelect={selectAsset} onOpenIncident={onOpenIncident}/>:<>
        <div className="twin-inspector-head"><div><div className="section-eyebrow">ROOM INSPECTOR</div><h3>{selectedRoom?.id||'Select a room'}</h3></div>{selectedRoom&&<span className={`state-pill ${roomStatus(selectedRoom)}`}>{STATUS[roomStatus(selectedRoom)].label}</span>}</div>
        {activeIncident&&<div className="twin-focused-incident" style={{'--incident-color':incidentColor}}><span><Radio size={13}/> FOCUSED DETECTION</span><strong>{activeIncident.issue_type||activeIncident.title||'Incident record'}</strong><small>{(activeIncident.affected_assets||[]).join(', ')||'Affected asset unspecified'} · {activeIncident.priority||'priority unspecified'} · {activeIncident.status||'status unspecified'}</small></div>}
        {selectedRoom?<>
          <div className="room-facts-grid"><div><small>FLOOR · AREA</small><b>{selectedRoom.floor} · {selectedRoom.area_m2??'—'} m²</b></div><div><small>OCCUPANCY</small><b>{selectedRoom.occupancy?.occupied===true?'Occupied':selectedRoom.occupancy?.occupied===false?'Vacant':'Unknown'}</b><em>{selectedRoom.occupancy?.known?'Known from events':'No occupancy event'}</em></div><div><small>READINESS</small><b>{selectedRoom.readiness?.state?human(selectedRoom.readiness.state):'Unknown'}</b><em>{selectedRoom.readiness?.quality||'missing'} quality</em></div><div><small>TEMPERATURE · SETPOINT</small><b>{signal('temperature_c')?.value==null?'—':`${fmt(signal('temperature_c').value)}°`} / {signal('setpoint_c')?.value==null?'—':`${fmt(signal('setpoint_c').value)}°`}</b></div><div><small>CO₂ · HUMIDITY</small><b>{signal('co2_ppm')?.value==null?'—':`${fmt(signal('co2_ppm').value,0)} ppm`} · {signal('humidity_pct')?.value==null?'—':`${fmt(signal('humidity_pct').value)}%`}</b></div><div><small>PRESENCE · WATER</small><b>{signal('presence')?.value==null?'—':signal('presence').value?'Motion detected':'No motion'} · {signal('water_flow_lph')?.value==null?'—':`${fmt(signal('water_flow_lph').value)} L/h`}</b></div></div>
          <div className="room-trend"><div className="twin-mini-heading"><span>ROOM TEMPERATURE</span><span>24H WINDOW</span></div>{points?<svg viewBox="0 0 100 40" preserveAspectRatio="none"><line x1="0" x2="100" y1="34" y2="34"/><polyline points={points}/></svg>:<div className="trend-unavailable">Room history is unavailable for this replay window.</div>}{points&&<div className="trend-range"><span>{fmt(minTemp)}°</span><span>{fmt(maxTemp)}°</span></div>}</div>
          <div className="inspector-list"><div className="twin-mini-heading"><span>RELATED INCIDENTS</span><b>{attachedIncidents.length||'—'}</b></div>{attachedIncidents.length?attachedIncidents.slice(0,3).map((incident,index)=><button className="twin-related-row" key={incident.event_id||index} onClick={()=>focusIncident(incident)}><i style={{background:SEVERITY[String(incident.priority||incident.severity||'medium').toLowerCase()]||'#fbbf24'}}/><span><strong>{incident.issue_type||incident.title||'Incident'}</strong><small>{incident.priority||incident.severity||'priority unspecified'} · {incident.status||'status unspecified'}</small></span><Radio size={13}/></button>):<div className="trend-unavailable">No incident record linked to this room.</div>}</div>
          <div className="inspector-list"><div className="twin-mini-heading"><span>AI DECISIONS</span><b>{attachedDecisions.length||'—'}</b></div>{attachedDecisions.length?attachedDecisions.slice(0,3).map((decision,index)=><button className="twin-related-row" key={decision.event_id||index} onClick={()=>setSelectedDecisionId(decision.event_id)}><i className="decision-legend-dot"/><span><strong>{decision.action||'Decision'}</strong><small>{decision.priority||'priority unspecified'}{decision.requires_human_approval?' · approval required':''}</small></span><Sparkles size={13}/></button>):<div className="trend-unavailable">Decision log unavailable or no decision linked to this room.</div>}</div>
        </>:<div className="building-directory"><p>Select a room to inspect its replay state, or open a floor for a closer look.</p><div className="building-floor-directory">{[...floors].reverse().map(floor=>{
            const floorRooms=rooms.filter(room=>Number(room.floor)===floor);
            return <button key={floor} onClick={()=>selectFloor(floor)}><span className="floor-number">{String(floor).padStart(2,'0')}</span><span><strong>Floor {floor}</strong><small>{floorRooms.length} rooms · {floorRooms.filter(r=>r.occupancy?.occupied===true).length} occupied</small></span><div className="floor-state-dots">{floorRooms.map(room=><i key={room.id} title={`${room.id}: ${STATUS[roomStatus(room,statusLayer)].label}`} style={{background:STATUS[roomStatus(room,statusLayer)].color}}/>)}</div></button>
          })}</div><div className="building-people-summary"><div><UserRound size={17}/><strong>{knownLocations.length} / {trackedResources.length||'—'}</strong></div><span>resources with a last reported position</span><button onClick={()=>setInspectorTab('team')}>Inspect team &amp; robots →</button></div><div className="building-plan-note"><DoorOpen size={15}/><span>Lobby, stairs and the marked emergency exit are schematic. This is not an evacuation plan.</span></div></div>}
        </>}
        <div className="agent-unavailable"><span>AGENT ACTIVITY</span><b>Not connected to an agent event stream</b></div>
      </aside>
    </div>
    <div className="twin-footnote"><span>3D SCHEMATIC · NOT A SURVEYED BUILDING MODEL</span><span>Room IDs, floors, orientations and relative areas come from configuration. Architecture, exits and asset positions are schematic; staff positions are last reported.</span></div>
  </section>;
}
