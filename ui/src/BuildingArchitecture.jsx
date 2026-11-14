import React, { useEffect, useRef, useState } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Billboard, Html, Line, Text } from '@react-three/drei';
import { Bot, UserRound } from 'lucide-react';
import * as THREE from 'three';
import { CORRIDOR_WIDTH, ROOM_DEPTH, ROOM_OUTER_Z, STORY_HEIGHT, LOBBY_WIDTH, LOBBY_REAR_Z, LOBBY_FRONT_Z, LOBBY_DEPTH, lobbyCenterX, lobbyCenterZ, halfWidth } from './buildingLayout.js';

export { STORY_HEIGHT, halfWidth };
export const ROBOT_COLOR = '#70d9ff';
export const STAFF_COLOR = '#f6ca83';
const MAT = { steel:'#8194a6', slab:'#566574', roof:'#293743', timber:'#b69b7b', glass:'#a3c1d5', wall:'#e4e8e9', plaster:'#f0efeb', floor:'#728799', corridor:'#a9adb0' };
const FACADE_BOTTOM = .085;
const FACADE_TOP = STORY_HEIGHT - .085;
const FACADE_HEIGHT = FACADE_TOP - FACADE_BOTTOM;
const FACADE_CENTER = (FACADE_BOTTOM + FACADE_TOP) / 2;

function Solid({ at, size, color=MAT.steel, opacity=1, roughness=.75, metalness=.08, emissive, intensity=0, shadow=false }) {
  return <mesh position={at} castShadow={shadow} receiveShadow>
    <boxGeometry args={size}/><meshStandardMaterial color={color} roughness={roughness} metalness={metalness}
      emissive={emissive||color} emissiveIntensity={intensity} transparent={opacity<1} opacity={opacity} depthWrite={opacity===1}/>
  </mesh>;
}

function ExitSign({ x=0, z=0.76 }) {
  return <group position={[x,1.28,z]}>
    <Solid at={[0,0,0]} size={[.6,.22,.04]} color="#266e55" emissive="#70cda3" intensity={.18}/>
    <Text position={[0,0,.026]} fontSize={.12} color="#effff6">EXIT</Text>
  </group>;
}

function StairCore({ x, showExits, mode, floor, isTopFloor, isGroundFloor, hasLobby }) {
  const exterior=mode==='building';
  const mainCore=x>0;
  const lobbyConnection=mainCore&&isGroundFloor&&hasLobby;
  return <group position={[x,0,0]}>
    <Solid at={[0,FACADE_CENTER,-1.05]} size={[1.12,FACADE_HEIGHT,.12]} color={exterior?MAT.plaster:MAT.wall}/>
    <Solid at={[Math.sign(x)*.53,FACADE_CENTER,0]} size={[.11,FACADE_HEIGHT,2.14]} color={exterior?MAT.plaster:MAT.steel}/>
    {exterior&&lobbyConnection?<>
      {[-1,1].map(side=><Solid key={side} at={[side*.51,FACADE_CENTER,1.05]} size={[.08,FACADE_HEIGHT,.1]} color={MAT.plaster}/>)}
      <Solid at={[0,1.77,1.05]} size={[1.08,.39,.1]} color={MAT.plaster}/>
      <Solid at={[0,.1,1.05]} size={[.8,.03,.16]} color={MAT.timber}/>
    </>:exterior?<>
      <Solid at={[0,.31,1.05]} size={[1.08,.45,.08]} color={MAT.plaster}/>
      <Solid at={[0,1.72,1.05]} size={[1.08,FACADE_TOP-1.5,.08]} color={MAT.plaster}/>
      {[-1,1].map(side=><Solid key={side} at={[side*.51,FACADE_CENTER,1.05]} size={[.08,FACADE_HEIGHT,.1]} color={MAT.plaster}/>)}
      <Solid at={[0,1.02,1.09]} size={[.92,.96,.035]} color={MAT.glass} opacity={.38} roughness={.23}/>
      <Solid at={[0,1.02,1.13]} size={[.035,.98,.04]} color={MAT.steel}/>
    </>:<>
      {isTopFloor?<><Solid at={[0,.145,.2]} size={[.86,.12,1.2]} color={MAT.wall}/><Text position={[0,.25,.2]} fontSize={.12} color="#435465">LANDING</Text></>:<>
        {Array.from({length:9},(_,i)=><Solid key={i} at={[mainCore ? .22 : 0,.22+i*.2,.77-i*.18]} size={[mainCore ? .58 : .86,.1,.24]} color={MAT.wall}/>)}
        <Solid at={[mainCore ? .22 : 0,1.91,-.8]} size={[mainCore ? .58 : .86,.12,.8]} color={MAT.wall}/>
        <Line points={[[mainCore ? -.1 : -.47,.7,.84],[mainCore ? -.1 : -.47,1.91,-.68]]} color="#dbe5ea" lineWidth={1.2}/>
      </>}
    </>}
    <Text position={[0,.34,1.13]} fontSize={.2} color={exterior?'#3c4a53':'#e7edf0'}>{String(floor).padStart(2,'0')}</Text>
    {showExits&&!exterior&&!mainCore&&<ExitSign/>}
  </group>;
}

function LobbyWing({ half, mode, buildingName, showRoof }) {
  const x=lobbyCenterX(half), front=LOBBY_FRONT_Z, rear=LOBBY_REAR_Z, exterior=mode==='building';
  const doorWidth=1.1, sideWidth=(LOBBY_WIDTH-doorWidth)/2;
  return <group>
    <Solid at={[x,0,lobbyCenterZ]} size={[LOBBY_WIDTH,.17,LOBBY_DEPTH+.08]} color={MAT.slab} shadow/>
    <Solid at={[x,.102,lobbyCenterZ]} size={[LOBBY_WIDTH-.12,.025,LOBBY_DEPTH-.1]} color="#c9cfcb"/>
    <Solid at={[x,.121,lobbyCenterZ]} size={[1.3,.012,LOBBY_DEPTH-.26]} color="#aabbbd" opacity={.38}/>
    <Solid at={[x-.65,.34,1.63]} size={[.88,.46,.34]} color={MAT.timber}/>
    <Solid at={[x-.65,.6,1.63]} size={[.98,.07,.43]} color={MAT.plaster}/>
    <Solid at={[x+.68,.23,2.18]} size={[.7,.2,.43]} color="#8298a4"/>
    <Solid at={[x+.68,.4,2.38]} size={[.7,.3,.11]} color="#aab9c0"/>
    {exterior?<>
      <Solid at={[x+LOBBY_WIDTH/2,FACADE_CENTER,lobbyCenterZ]} size={[.11,FACADE_HEIGHT,LOBBY_DEPTH]} color={MAT.plaster}/>
      <Solid at={[x+LOBBY_WIDTH/2+.065,1.02,lobbyCenterZ]} size={[.035,.9,LOBBY_DEPTH-.34]} color={MAT.glass} opacity={.28}/>
      <Solid at={[x+.6,FACADE_CENTER,rear]} size={[LOBBY_WIDTH-1.2,FACADE_HEIGHT,.1]} color={MAT.plaster}/>
      {[-1,1].map(side=><group key={side} position={[x+side*(doorWidth+sideWidth)/2,0,front]}>
        <Solid at={[0,.3,0]} size={[sideWidth,.43,.1]} color={MAT.plaster}/>
        <Solid at={[0,1,0]} size={[sideWidth-.12,.98,.035]} color={MAT.glass} opacity={.34} roughness={.22}/>
        <Solid at={[0,1.74,0]} size={[sideWidth,.45,.1]} color={MAT.plaster}/>
      </group>)}
      {[-1,1].map(side=><Solid key={`door-post-${side}`} at={[x+side*doorWidth/2,FACADE_CENTER,front]} size={[.09,FACADE_HEIGHT,.1]} color={MAT.steel}/>)}
      <Solid at={[x,1.82,front]} size={[doorWidth+.1,.28,.12]} color={MAT.plaster}/>
      <Solid at={[x,.91,front+.035]} size={[doorWidth,1.63,.035]} color={MAT.glass} opacity={.42} roughness={.18}/>
      <Solid at={[x,.91,front+.065]} size={[.035,1.63,.04]} color={MAT.steel}/>
      {[-1,1].map(side=><Solid key={`handle-${side}`} at={[x+side*.1,.95,front+.09]} size={[.025,.16,.035]} color={MAT.steel} metalness={.5}/>)}
      {showRoof&&<Solid at={[x,STORY_HEIGHT-.08,lobbyCenterZ]} size={[LOBBY_WIDTH+.2,.18,LOBBY_DEPTH+.17]} color={MAT.roof} shadow/>}
      <Text position={[x,1.84,front+.08]} fontSize={.13} color="#3f505d">{buildingName||'LOBBY'}</Text>
    </>:<>
      <Solid at={[x+LOBBY_WIDTH/2,.43,lobbyCenterZ]} size={[.09,.67,LOBBY_DEPTH]} color={MAT.wall}/>
      <Solid at={[half+.09,.43,(1.2+front)/2]} size={[.09,.67,front-1.2]} color={MAT.wall}/>
      <Solid at={[x+.6,.43,rear]} size={[LOBBY_WIDTH-1.2,.67,.09]} color={MAT.wall}/>
      {[-1,1].map(side=><Solid key={`entry-jamb-${side}`} at={[x+side*doorWidth/2,.43,front]} size={[.09,.67,.1]} color={MAT.steel}/>)}
      <Billboard position={[x,.93,lobbyCenterZ]}><Text fontSize={.22} color="#f8fafb" outlineWidth={.01} outlineColor="#14202a">LOBBY</Text></Billboard>
    </>}
    <Solid at={[x,.1,front+.13]} size={[1.32,.03,.33]} color={MAT.timber}/>
  </group>;
}

function FacadeBay({ entry, color, selected, incidentColor }) {
  const {x,side,slotWidth}=entry, sign=side==='S'?1:-1, z=sign*(ROOM_OUTER_Z+.035);
  const pier=Math.min(.38,slotWidth*.23), window=slotWidth-pier-.12;
  return <group position={[x,0,z]}>
    {/* Opaque white spandrels meet both slabs; glazing stays translucent for room inspection. */}
    <Solid at={[-slotWidth/2+pier/2,FACADE_CENTER,0]} size={[pier,FACADE_HEIGHT,.14]} color={MAT.plaster} shadow/>
    <Solid at={[pier/2,(FACADE_BOTTOM+.55)/2,0]} size={[window,.55-FACADE_BOTTOM,.1]} color={MAT.plaster}/>
    <Solid at={[pier/2,(1.45+FACADE_TOP)/2,0]} size={[window,FACADE_TOP-1.45,.1]} color={MAT.plaster}/>
    <Solid at={[pier/2,1,sign*.018]} size={[window-.08,.9,.035]} color={MAT.glass} opacity={.32} roughness={.23} metalness={.1}/>
    <Solid at={[pier/2,1,sign*.05]} size={[.035,.93,.04]} color={MAT.steel}/>
    <Solid at={[slotWidth/2-.035,FACADE_CENTER,0]} size={[.07,FACADE_HEIGHT,.14]} color={MAT.steel}/>
    <Solid at={[-slotWidth/2+.04,FACADE_CENTER,sign*.085]} size={[.04,FACADE_HEIGHT,.045]} color={MAT.timber}/>
    <Solid at={[pier/2,.55,sign*.065]} size={[Math.max(.35,window-.15),.035,.045]} color={incidentColor||color} emissive={incidentColor||color} intensity={selected?.6:.26}/>
    {incidentColor&&<mesh position={[slotWidth/2-.15,1.56,sign*.15]}><sphereGeometry args={[.105,16,16]}/><meshBasicMaterial color={incidentColor}/></mesh>}
    <group position={[pier/2,.3,sign*.075]} rotation={[0,sign<0?Math.PI:0,0]}><Text fontSize={.14} color="#f1f4f5">{entry.room.id}</Text></group>
  </group>;
}

function Room({ entry, status, color, selected, incidentColor, detailed, onSelect, onHover }) {
  const { room, x, z, width, depth, side }=entry, sign=side==='S'?1:-1;
  const innerZ=-sign*depth/2, door=.45, wallHeight=detailed?.85:1.14, jamb=(width-door)/2;
  const occupancy=room.occupancy?.occupied===true?'#5fa8ff':room.occupancy?.occupied===false?'#4ed8c7':'#6b7280';
  const readiness=({clean:'#4ed8c7',in_progress:'#f5c451',dirty:'#f59e42',inspection_required:'#ff7a8a'})[room.readiness?.state]||'#6b7280';
  return <group position={[x,.08,z]}>
    <Solid at={[0,.045,0]} size={[width,.08,depth]} color={MAT.floor}/>
    <Solid at={[0,.092,0]} size={[width-.08,.012,depth-.08]} color={color} emissive={color} intensity={.12} opacity={selected?.36:.14}/>
    {/* Partitions and a real opening onto the shared corridor. */}
    {[-1,1].map(s=><Solid key={s} at={[s*(width/2-.035),wallHeight/2+.085,0]} size={[.07,wallHeight,depth]} color={MAT.wall} opacity={detailed?.82:.66}/>)}
    {[-1,1].map(s=><Solid key={s} at={[s*(door/2+jamb/2),wallHeight/2+.085,innerZ]} size={[jamb,wallHeight,.07]} color={MAT.wall} opacity={detailed?.9:.72}/>)}
    <Solid at={[0,wallHeight+.07,innerZ]} size={[width,.09,.08]} color={MAT.steel}/>
    <Solid at={[0,.098,innerZ]} size={[door,.025,.16]} color={MAT.timber}/>
    {/* A compact bed and bathroom partition communicate scale; furnishings are schematic. */}
    <Solid at={[-width*.13,.185,sign*.34]} size={[width*.55,.2,depth*.43]} color="#b4c2ca"/>
    <Solid at={[-width*.13,.3075,sign*.42]} size={[width*.49,.045,depth*.31]} color="#e0e5e7"/>
    <Solid at={[-width*.13,.3525,sign*.12]} size={[width*.38,.045,.16]} color="#f3f4ef"/>
    <Solid at={[width*.29,.32,-sign*.55]} size={[width*.28,.48,depth*.25]} color="#bac8cc" opacity={.68}/>
    <Solid at={[width*.29,.59,-sign*.55]} size={[width*.28,.03,depth*.25]} color="#dee4e1"/>
    {detailed&&<Billboard position={[0,wallHeight+.44,0]}>
      <Text fontSize={.24} color="#f6f8fa" outlineWidth={.012} outlineColor="#101a25">{room.id}</Text>
      <Text position={[0,-.23,0]} fontSize={.12} color={color} outlineWidth={.008} outlineColor="#101a25">{status}</Text>
    </Billboard>}
    {detailed&&<group position={[0,.14,innerZ-sign*.16]}><mesh position={[-.11,0,0]} rotation={[-Math.PI/2,0,0]}><circleGeometry args={[.055,12]}/><meshBasicMaterial color={occupancy}/></mesh><mesh position={[.11,0,0]} rotation={[-Math.PI/2,0,0]}><circleGeometry args={[.055,12]}/><meshBasicMaterial color={readiness}/></mesh></group>}
    {selected&&<Line points={[[-width/2,.095,-depth/2],[width/2,.095,-depth/2],[width/2,.095,depth/2],[-width/2,.095,depth/2],[-width/2,.095,-depth/2]]} color="#8fd3ff" lineWidth={2.5}/>}
    {incidentColor&&<mesh position={[width/2-.12,wallHeight+.31,0]}><sphereGeometry args={[.10,12,12]}/><meshBasicMaterial color={incidentColor}/></mesh>}
    <mesh position={[0,.65,0]} onClick={e=>{e.stopPropagation();onSelect(room)}} onPointerOver={e=>{e.stopPropagation();onHover(room)}} onPointerOut={()=>onHover(null)}>
      <boxGeometry args={[width,1.3,depth]}/><meshBasicMaterial transparent opacity={0} depthWrite={false}/>
    </mesh>
  </group>;
}

function ResourceMarker({ resource, position, selected, onSelect }) {
  const robot=resource.role==='robot', color=robot?ROBOT_COLOR:STAFF_COLOR;
  const old=resource.last_known_location.age_minutes>60||resource.shift_status!=='scheduled';
  return <group position={position} onClick={e=>{e.stopPropagation();onSelect(resource)}}>
    <mesh rotation={[-Math.PI/2,0,0]} position={[0,.05,0]}><ringGeometry args={[.18,.23,24]}/><meshBasicMaterial color={color} opacity={old?.35:.9} transparent side={THREE.DoubleSide}/></mesh>
    {robot?<mesh position={[0,.15,0]}><cylinderGeometry args={[.17,.21,.18,16]}/><meshStandardMaterial color="#a8c8d8" metalness={.45} roughness={.4}/></mesh>:<><mesh position={[0,.5,0]}><sphereGeometry args={[.1,12,12]}/><meshStandardMaterial color="#f3d3a1"/></mesh><mesh position={[0,.28,0]}><capsuleGeometry args={[.1,.22,4,8]}/><meshStandardMaterial color={color}/></mesh></>}
    <Html position={[0,robot?.48:.72,0]} center zIndexRange={[3,0]}><button className={`twin-resource-pin ${robot?'robot':'staff'} ${selected?'selected':''} ${old?'historical':''}`} onClick={e=>{e.stopPropagation();onSelect(resource)}} title={`Last report: ${resource.last_known_location.label} · ${resource.last_known_location.timestamp_utc}`}>{robot?<Bot size={11}/>:<UserRound size={11}/>}<span>{resource.resource_id.replaceAll('_',' ')}</span></button></Html>
  </group>;
}

export function ArchitecturalFloor({ floor, entries, baseY, half, mode, isGroundFloor, isTopFloor, hasLobby, buildingName, showRoof, incidents, selectedRoomId, onSelectRoom, setHovered, showExits, resources, selectedResourceId, onSelectResource, statusOf, statuses, severity }) {
  const ref=useRef(), initial=useRef(baseY), invalidate=useThree(s=>s.invalidate), detailed=mode!=='building';
  useEffect(()=>invalidate(),[baseY,invalidate]);
  useFrame((_,dt)=>{if(ref.current){const next=THREE.MathUtils.damp(ref.current.position.y,baseY,5,dt);ref.current.position.y=next;if(Math.abs(next-baseY)>.004)invalidate();else ref.current.position.y=baseY;}});
  const coreX=half+.65;
  const grids=[...new Map(entries.map(e=>[e.index,{x:e.x,w:e.slotWidth}])).values()];
  return <group ref={ref} position={[0,initial.current,0]}>
    <Solid at={[0,0,0]} size={[half*2+.24,.17,ROOM_OUTER_Z*2+.2]} color={MAT.slab} shadow/>
    {[-1,1].map(side=><group key={`core-slab-${side}`}>
      <Solid at={[side*coreX,0,isGroundFloor?0:.4]} size={[1.24,.17,isGroundFloor?2.4:1.6]} color={MAT.slab} shadow/>
      {!isGroundFloor&&mode!=='building'&&[-1,1].map(edge=><Solid key={edge} at={[side*coreX+edge*.56,.37,-.8]} size={[.04,.55,.82]} color={MAT.steel}/>)}
    </group>)}
    {isGroundFloor&&hasLobby&&<LobbyWing half={half} mode={mode} buildingName={buildingName} showRoof={showRoof}/>}
    <Solid at={[0,.105,0]} size={[half*2,.025,ROOM_OUTER_Z*2]} color="#6e7c86"/>
    {mode==='building'&&<>
      {[-1,1].map(side=><group key={`fascia-${side}`}>
        <Solid at={[0,.07,side*(ROOM_OUTER_Z+.14)]} size={[half*2+.28,.19,.14]} color={MAT.plaster}/>
        <Solid at={[0,.19,side*(ROOM_OUTER_Z+.16)]} size={[half*2+.28,.035,.05]} color={MAT.timber}/>
      </group>)}
      {[-1,1].flatMap(xSide=>[-1,1].map(zSide=><Solid key={`corner-${xSide}-${zSide}`} at={[xSide*(half+.1),FACADE_CENTER,zSide*(ROOM_OUTER_Z+.1)]} size={[.16,FACADE_HEIGHT,.16]} color={MAT.steel} metalness={.32}/>))}
    </>}
    <Solid at={[0,.13,0]} size={[half*2,.035,CORRIDOR_WIDTH]} color={MAT.corridor}/>
    <Line points={[[-half,.155,0],[half,.155,0]]} color="#d9e0db" lineWidth={.8} transparent opacity={.42}/>
    {[-1,1].map(s=><StairCore key={s} x={s*coreX} showExits={showExits} mode={mode} floor={floor} isGroundFloor={isGroundFloor} isTopFloor={isTopFloor} hasLobby={hasLobby}/>)}
    {grids.flatMap((grid,i)=>[-1,1].map(sign=><Solid key={`${i}-${sign}`} at={[grid.x-grid.w/2,FACADE_CENTER,sign*(ROOM_OUTER_Z+.07)]} size={[.065,FACADE_HEIGHT,.12]} color={MAT.steel} metalness={.3}/>))}
    {entries.map(entry=>{const status=statusOf(entry.room),incident=incidents.get(entry.room.id),incidentColor=incident?(severity[String(incident.priority||incident.severity||'medium').toLowerCase()]||'#ff7a8a'):null;
      return <group key={entry.room.id}><Room entry={entry} status={statuses[status].label} color={statuses[status].color} selected={selectedRoomId===entry.room.id} detailed={detailed} incidentColor={incidentColor} onSelect={onSelectRoom} onHover={setHovered}/>{mode==='building'&&<FacadeBay entry={entry} color={statuses[status].color} selected={selectedRoomId===entry.room.id} incidentColor={incidentColor}/>}</group>;
    })}
    {resources.map((resource,index)=>{
      const location=resource.last_known_location,entry=entries.find(e=>e.room.id===location.room_id),entrance=location.kind==='entrance'&&isGroundFloor&&hasLobby;
      const peers=resources.filter(r=>entrance?r.last_known_location.kind==='entrance':r.last_known_location.room_id===location.room_id);
      const offset=(peers.findIndex(r=>r.resource_id===resource.resource_id)-(peers.length-1)/2)*.42;
      return <ResourceMarker key={resource.resource_id} resource={resource} position={[entrance?lobbyCenterX(half)+offset:entry?entry.x+offset:(index-1)*.55,.17,entrance?LOBBY_FRONT_Z+.16:entry?Math.sign(entry.z)*.47:0]} selected={selectedResourceId===resource.resource_id} onSelect={onSelectResource}/>;
    })}
  </group>;
}

export function BuildingEnvelope({ half, bottom, top, showExits, showRoof, entranceResources, selectedResourceId, onSelectResource, hasLobby, mode, floorCount }) {
  const full=half+1.25;
  const corridorOpening=CORRIDOR_WIDTH*.72;
  const wallSegment=(ROOM_OUTER_Z*2-corridorOpening)/2;
  const segmentCenter=corridorOpening/2+wallSegment/2;
  return <>
    <group position={[0,bottom-.2,0]}>
      <Solid at={[0,0,.55]} size={[full*2+1,.36,ROOM_OUTER_Z*2+2.2]} color={MAT.roof} shadow/>
      {hasLobby&&<Solid at={[lobbyCenterX(half),0,lobbyCenterZ]} size={[LOBBY_WIDTH+.3,.36,LOBBY_DEPTH+.4]} color={MAT.roof} shadow/>}
      {showExits&&<group position={[-(half+.65),0,1.12]}><Solid at={[0,.65,0]} size={[.68,1.25,.07]} color="#315c50"/><ExitSign/></group>}
      {entranceResources.map((resource,index)=><ResourceMarker key={resource.resource_id} resource={resource} position={[(hasLobby?lobbyCenterX(half):0)+(index-(entranceResources.length-1)/2)*.55,.38,hasLobby?LOBBY_FRONT_Z+.25:ROOM_OUTER_Z+1.1]} selected={selectedResourceId===resource.resource_id} onSelect={onSelectResource}/>)}
    </group>
    {mode==='building'&&Array.from({length:floorCount},(_,index)=>{
      const y=bottom+index*STORY_HEIGHT;
      return <group key={`end-elevation-${index}`} position={[0,y,0]}>
        {[-1,1].map(side=><group key={side} position={[side*(half+.09),0,0]}>
          {[-1,1].map(pane=><Solid key={`wall-${pane}`} at={[0,FACADE_CENTER,pane*segmentCenter]} size={[.11,FACADE_HEIGHT,wallSegment]} color={MAT.plaster}/>)}
          <Solid at={[0,1.75,0]} size={[.11,.43,corridorOpening]} color={MAT.plaster}/>
          {[-1,1].map(pane=><Solid key={pane} at={[side*.065,.99,pane*segmentCenter]} size={[.035,1.2,wallSegment-.22]} color={MAT.glass} opacity={.29} roughness={.24}/>)}
          {[-1,1].map(pane=><Solid key={`mullion-${pane}`} at={[side*.09,.99,pane*segmentCenter]} size={[.055,1.24,.045]} color={MAT.steel} metalness={.32}/>)}
          {[-1,1].map(pane=><Solid key={`sill-${pane}`} at={[side*.09,.31,pane*segmentCenter]} size={[.06,.15,wallSegment]} color={MAT.timber}/>)}
          <Solid at={[side*.09,1.69,0]} size={[.06,.1,ROOM_OUTER_Z*2]} color={MAT.timber}/>
        </group>)}
      </group>;
    })}
    {showRoof&&<group position={[0,top+STORY_HEIGHT-.13,0]}>
      <Solid at={[0,0,0]} size={[half*2+.32,.22,ROOM_OUTER_Z*2+.42]} color={MAT.roof} shadow/>
      {[-1,1].map(s=><Solid key={`core-roof-${s}`} at={[s*(half+.65),0,0]} size={[1.32,.22,2.5]} color={MAT.roof} shadow/>)}
      {[-1,1].map(s=><Solid key={`roof-front-${s}`} at={[0,.09,s*(ROOM_OUTER_Z+.06)]} size={[half*2+.32,.2,.12]} color={MAT.steel}/>)}
      {[-1,1].map(s=><Solid key={`roof-end-${s}`} at={[s*(half+.16),.09,0]} size={[.12,.2,ROOM_OUTER_Z*2+.2]} color={MAT.steel}/>)}
    </group>}
  </>;
}

export function TechnicalAssets({ placements, selectedAsset, incidents, onSelectAsset, bottom, half, mode, selectedFloor }) {
  const [hovered,setHovered]=useState(null);
  const tech=placements.filter(p=>p.group==='technical'), techColumns=Math.max(1,Math.ceil(tech.length/8)), techWidth=1.4+(techColumns-1)*.7;
  return <>
    {mode!=='floor'&&tech.length>0&&<group position={[-half-1.85-(techColumns-1)*.35,bottom,0]}>
      <Solid at={[0,.03,0]} size={[techWidth,.16,5.8]} color="#3c4d58"/>
      <Solid at={[techWidth/2-.06,.4,0]} size={[.08,.8,5.8]} color={MAT.steel} opacity={.75}/>
      <Text position={[0,.12,3.02]} rotation={[-Math.PI/2,0,0]} fontSize={.16} color="#b8cbd5">TECHNICAL · SCHEMATIC</Text>
    </group>}
    {placements.filter(p=>mode!=='floor'||(['floor','lobby'].includes(p.group)&&Number(p.floor)===Number(selectedFloor))).map(({asset,position,group})=>{
      const active=incidents.find(item=>(item.affected_assets||[]).includes(asset.id));
      const selected=selectedAsset===asset.id, tint=active?'#ff7a8a':selected?'#8fd3ff':'#9db7c5';
      return <group key={asset.id} position={position} onClick={e=>{e.stopPropagation();onSelectAsset(asset)}} onPointerOver={e=>{e.stopPropagation();setHovered(asset.id)}} onPointerOut={()=>setHovered(null)}>
        {group==='roof'?<><Solid at={[0,-.09,0]} size={[1.5,.1,1.1]} color="#263e4d"/><Line points={[[-.7,-.02,-.5],[.7,-.02,-.5],[.7,-.02,.5],[-.7,-.02,.5],[-.7,-.02,-.5]]} color="#8aaab8" lineWidth={1}/></>:<Solid at={[0,0,0]} size={[group==='technical'?.58:.38,.55,.43]} color={group==='floor'?'#768c84':'#a4b1b6'} metalness={.3} shadow/>}
        <mesh position={[0,.34,0]}><sphereGeometry args={[selected?.11:.075,12,12]}/><meshBasicMaterial color={tint}/></mesh>
        {(selected||hovered===asset.id||active)&&<Html position={[0,.65,0]} center zIndexRange={[3,0]}><button className={`twin-asset-pin ${selected?'selected':''} ${active?'incident':''}`} onClick={e=>{e.stopPropagation();onSelectAsset(asset)}}>{asset.id}{active?' · DETECTED':''}</button></Html>}
        <mesh position={[0,.05,0]}><boxGeometry args={[.8,.9,.7]}/><meshBasicMaterial transparent opacity={0} depthWrite={false}/></mesh>
      </group>;
    })}
  </>;
}
