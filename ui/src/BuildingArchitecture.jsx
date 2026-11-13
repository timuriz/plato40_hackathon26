import React, { useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Billboard, Html, Line, Text } from '@react-three/drei';
import { Bot, UserRound } from 'lucide-react';
import * as THREE from 'three';

// Architectural elements illustrate the room topology; no surveyed plan is supplied.
export const STORY_HEIGHT = 1.85;
export const ROBOT_COLOR = '#70d9ff';
export const STAFF_COLOR = '#f6ca83';
export const halfWidth = entries => Math.max(5.7, ...entries.map(({ x, width }) => Math.abs(x) + width / 2 + 0.45));

function Solid({ at, size, color = '#637785', emissive, intensity = 0, opacity = 1 }) {
  return <mesh position={at} castShadow={opacity === 1} receiveShadow>
    <boxGeometry args={size}/>
    <meshStandardMaterial color={color} roughness={0.55} metalness={0.22} emissive={emissive || color}
      emissiveIntensity={intensity} transparent={opacity < 1} opacity={opacity} depthWrite={opacity === 1}/>
  </mesh>;
}

function ExitSign({ x }) {
  return <group position={[x,1.25,0.42]}>
    <Solid at={[0,0,0]} size={[0.67,0.26,0.04]} color="#167a59" emissive="#45e9a5" intensity={0.35}/>
    <Text position={[0,0,0.025]} fontSize={0.13} color="#e9fff5">{x < 0 ? '← EXIT' : 'EXIT →'}</Text>
  </group>;
}

function StairCore({ x, showExits, floor }) {
  return <group position={[x,0,0]}>
    <Solid at={[0,0.79,-1.1]} size={[1.16,1.56,0.12]} color="#3b5263"/>
    <Solid at={[Math.sign(x) * 0.57,0.79,0]} size={[0.1,1.56,2.3]} color="#607784" opacity={0.4}/>
    {Array.from({ length: 9 }, (_, i) => <Solid key={i} at={[0,0.21+i*0.16,0.8-i*0.2]} size={[0.91,0.1,0.28]} color="#8fa3ad"/>)}
    <Line points={[[-0.5,0.76,0.85],[-0.5,1.9,-0.75]]} color="#bbcbd2" lineWidth={1.3}/>
    <Solid at={[0,0.83,1.12]} size={[1.16,1.35,0.045]} color="#91adbc" opacity={0.09}/>
    <Text position={[0,0.34,1.16]} fontSize={0.3} color="#b5c7d2">{String(floor).padStart(2,'0')}</Text>
    {showExits && <ExitSign x={0}/>}
  </group>;
}

function Room({ entry, status, color, selected, incidentColor, detailed, onSelect, onHover }) {
  const { room, x, z, width, side } = entry;
  const sign = side === 'S' ? 1 : -1;
  return <group position={[x,0.16,z]}>
    <Solid at={[0,0.03,0]} size={[width,0.06,2.04]} color="#243642"/>
    <Solid at={[0,0.068,0]} size={[width-0.08,0.012,1.96]} color={color} intensity={0.16} opacity={0.3}/>
    {[-1,1].map(s => <Solid key={s} at={[s*width/2,0.39,0]} size={[0.065,0.7,2.06]} color="#657b8a" opacity={0.65}/>)}
    {/* Low partition, room door and a simple furnished silhouette aid spatial reading. */}
    <Solid at={[0,0.37,-sign*1.02]} size={[width,0.68,0.07]} color="#415967"/>
    <Solid at={[0.23,0.38,-sign*1.065]} size={[0.38,0.64,0.025]} color="#9b8d75"/>
    <Solid at={[0.45,0.38,-sign*1.085]} size={[0.025,0.12,0.025]} color="#d1b687"/>
    <Solid at={[-0.19,0.2,0.28]} size={[width*0.6,0.24,1.05]} color="#8fa3ad"/>
    <Solid at={[-0.19,0.34,0.47]} size={[width*0.54,0.055,0.58]} color="#c9d5d6"/>
    <Solid at={[-0.19,0.35,-0.08]} size={[width*0.44,0.06,0.2]} color="#e0e6e1"/>
    {/* Facade: solid sill, glazed window, mullions and a separate status rail. */}
    <Solid at={[0,0.2,sign*1.25]} size={[2.12,0.4,0.14]} color="#506572"/>
    <Solid at={[0,0.91,sign*1.25]} size={[1.92,0.98,0.045]} color="#79afc4" opacity={0.18}/>
    {[-1,0,1].map(s => <Solid key={s} at={[s*0.98,0.91,sign*1.28]} size={[0.045,1.04,0.065]} color="#9caeb7"/>)}
    <Solid at={[0,1.43,sign*1.27]} size={[2.12,0.08,0.14]} color="#8d9fa9"/>
    <Solid at={[0,0.42,sign*1.34]} size={[1.72,0.045,0.035]} color={incidentColor || color} intensity={selected?1.3:0.65}/>
    <group position={[0,0.2,sign*1.335]} rotation={[0,sign===-1?Math.PI:0,0]}>
      <Text fontSize={0.19} color={selected?'#ffffff':'#d5e2e8'}>{room.id}</Text>
    </group>
    {detailed && <Billboard position={[0,1.07,0]}>
      <Text fontSize={0.18} color="#f0f5f8" outlineWidth={0.012} outlineColor="#101a25">{room.id}</Text>
      <Text position={[0,-0.23,0]} fontSize={0.105} color={color} outlineWidth={0.008} outlineColor="#101a25">{status}</Text>
    </Billboard>}
    {selected && <Line points={[[-width/2,0.09,-1.04],[width/2,0.09,-1.04],[width/2,0.09,1.04],[-width/2,0.09,1.04],[-width/2,0.09,-1.04]]} color="#e8fbff" lineWidth={2}/>}
    {incidentColor && <mesh position={[width/2-0.12,1.22,0]}><sphereGeometry args={[0.12,12,12]}/><meshStandardMaterial color={incidentColor} emissive={incidentColor} emissiveIntensity={1.5}/></mesh>}
    <mesh position={[0,0.7,0]} onClick={e=>{e.stopPropagation();onSelect(room)}} onPointerOver={e=>{e.stopPropagation();onHover(room)}} onPointerOut={()=>onHover(null)}>
      <boxGeometry args={[2.06,1.44,2.65]}/><meshBasicMaterial transparent opacity={0} depthWrite={false}/>
    </mesh>
  </group>;
}

function ResourceMarker({ resource, position, selected, onSelect }) {
  const robot = resource.role === 'robot';
  const color = robot ? ROBOT_COLOR : STAFF_COLOR;
  const old = resource.last_known_location.age_minutes > 60 || resource.shift_status !== 'scheduled';
  return <group position={position} onClick={e=>{e.stopPropagation();onSelect(resource)}}>
    <mesh rotation={[-Math.PI/2,0,0]} position={[0,0.05,0]}><ringGeometry args={[0.2,0.25,24]}/><meshBasicMaterial color={color} opacity={old?0.35:0.9} transparent side={THREE.DoubleSide}/></mesh>
    {robot ? <>
      <mesh position={[0,0.15,0]}><cylinderGeometry args={[0.19,0.23,0.2,20]}/><meshStandardMaterial color="#a8c8d8" metalness={0.65} roughness={0.25}/></mesh>
      <Solid at={[0,0.26,0]} size={[0.19,0.06,0.13]} color={color} intensity={0.8}/>
    </> : <>
      <mesh position={[0,0.52,0]}><sphereGeometry args={[0.11,12,12]}/><meshStandardMaterial color="#f3d3a1"/></mesh>
      <mesh position={[0,0.29,0]}><capsuleGeometry args={[0.105,0.22,4,8]}/><meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.1}/></mesh>
    </>}
    <Html position={[0,robot?0.55:0.82,0]} center zIndexRange={[3,0]}>
      <button className={`twin-resource-pin ${robot?'robot':'staff'} ${selected?'selected':''} ${old?'historical':''}`} onClick={e=>{e.stopPropagation();onSelect(resource)}} title={`Last report: ${resource.last_known_location.label} · ${resource.last_known_location.timestamp_utc}`}>
        {robot?<Bot size={11}/>:<UserRound size={11}/>}<span>{resource.resource_id.replaceAll('_',' ')}</span>
      </button>
    </Html>
  </group>;
}

export function ArchitecturalFloor({ floor, entries, baseY, incidents, selectedRoomId, onSelectRoom, setHovered, detailed, showExits, resources, selectedResourceId, onSelectResource, statusOf, statuses, severity }) {
  const ref = useRef();
  const invalidate = useThree(s=>s.invalidate);
  useFrame((_,dt)=>{ if(ref.current){ref.current.position.y=THREE.MathUtils.damp(ref.current.position.y,baseY,5,dt);if(Math.abs(ref.current.position.y-baseY)>0.005)invalidate();} });
  const half = halfWidth(entries);
  return <group ref={ref} position={[0,baseY,0]}>
    <Solid at={[0,0,0]} size={[half*2+2.8,0.18,6.2]} color="#6e8491"/>
    <Solid at={[0,0.11,0]} size={[half*2,0.035,6.04]} color="#23313c"/>
    <Solid at={[0,0.14,0]} size={[half*2,0.02,1.31]} color="#66706c"/>
    <Line points={[[-half,0.16,0],[half,0.16,0]]} color="#a7bfba" lineWidth={0.8} dashed dashSize={0.2} gapSize={0.16}/>
    {[-1,1].map(s=><StairCore key={s} x={s*(half+0.7)} showExits={showExits} floor={floor}/>)}
    {[-1,1].flatMap(s=>[-1,1].map(z=><Solid key={`${s}-${z}`} at={[s*(half+1.25),0.87,z*3]} size={[0.12,1.6,0.12]} color="#8da1ad"/>))}
    {entries.map(entry=>{
      const status=statusOf(entry.room), incident=incidents.get(entry.room.id);
      return <Room key={entry.room.id} entry={entry} status={statuses[status].label} color={statuses[status].color} selected={selectedRoomId===entry.room.id} detailed={detailed}
        incidentColor={incident?(severity[String(incident.priority||'medium').toLowerCase()]||'#fbbf24'):null} onSelect={onSelectRoom} onHover={setHovered}/>;
    })}
    {resources.map((resource,index)=>{
      const location=resource.last_known_location;
      const entry=entries.find(e=>e.room.id===location.room_id);
      // An icon is anchored to the reported room; it is not a measured coordinate.
      const peers=resources.filter(r=>r.last_known_location.room_id===location.room_id);
      const offset=(peers.findIndex(r=>r.resource_id===resource.resource_id)-(peers.length-1)/2)*0.5;
      return <ResourceMarker key={resource.resource_id} resource={resource} position={[entry?entry.x+offset:(index-1)*0.65,0.2,entry?Math.sign(entry.z)*0.5:0]}
        selected={selectedResourceId===resource.resource_id} onSelect={onSelectResource}/>;
    })}
  </group>;
}

export function BuildingEnvelope({ half, bottom, top, showExits, showRoof, entranceResources, selectedResourceId, onSelectResource }) {
  return <>
    <group position={[0,bottom-0.22,0]}>
      <Solid at={[0,-0.1,0.6]} size={[half*2+4,0.25,8.6]} color="#273846"/>
      <Solid at={[0,0.02,3.95]} size={[3.7,0.05,1.25]} color="#92a2a9"/>
      <Solid at={[0,0.8,3.21]} size={[2.1,1.5,0.045]} color="#8fc3d6" opacity={0.24}/>
      {[-1,1].map(s=><Solid key={s} at={[s*1.1,0.83,3.35]} size={[0.07,1.65,0.07]} color="#b3c2c7"/>)}
      <Solid at={[0,1.65,3.66]} size={[3.6,0.11,1.6]} color="#8c9ea8"/>
      <Solid at={[0,1.57,4.45]} size={[3.15,0.025,0.035]} color="#f8e0b5" intensity={0.8}/>
      <Text position={[0,1.87,3.42]} fontSize={0.25} color="#d5e5eb" letterSpacing={0.14}>PLATO — 40</Text>
      <Text position={[0,0.1,4.82]} rotation={[-Math.PI/2,0,0]} fontSize={0.18} color="#aec1cc">ENTRANCE</Text>
      {showExits&&[-1,1].map(s=><group key={s} position={[s*(half+0.7),0,1.5]}>
        <Solid at={[0,0.7,0]} size={[0.72,1.3,0.07]} color="#205448"/>
        <ExitSign x={0}/>
      </group>)}
      {entranceResources.map((resource,index)=><ResourceMarker key={resource.resource_id} resource={resource} position={[(index-(entranceResources.length-1)/2)*0.65,0.08,4]}
        selected={selectedResourceId===resource.resource_id} onSelect={onSelectResource}/>)}
    </group>
    {showRoof&&<group position={[0,top+STORY_HEIGHT-0.08,0]}>
      <Solid at={[0,0,0]} size={[half*2+2.8,0.13,6.3]} color="#6f919e" opacity={0.12}/>
      {[-1,1].map(s=><Solid key={s} at={[0,0.06,s*3.11]} size={[half*2+2.9,0.2,0.13]} color="#91a4b0"/>)}
      {[-1,1].map(s=><Solid key={s} at={[s*(half+1.38),0.06,0]} size={[0.13,0.2,6.3]} color="#91a4b0"/>)}
      {[-1,1].map(s=><Solid key={s} at={[s*(half+0.7),0.23,0]} size={[1.45,0.35,2.5]} color="#4c616f"/>)}
    </group>}
  </>;
}
