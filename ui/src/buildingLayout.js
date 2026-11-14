// Schematic topology derived from room metadata. One scene unit represents 2.5 m.
export const UNIT_PER_METER = 0.4;
export const ROOM_DEPTH_METERS = 6;
export const ROOM_DEPTH = ROOM_DEPTH_METERS * UNIT_PER_METER;
export const CORRIDOR_WIDTH = 1.38;
export const STORY_HEIGHT = 2.05;
export const ROOM_OUTER_Z = CORRIDOR_WIDTH / 2 + ROOM_DEPTH;
export const STRUCTURAL_GAP = 0.12;
// Schematic ground-floor arrival wing. It attaches to the end of the room corridor.
export const LOBBY_WIDTH = 3.2;
export const LOBBY_REAR_Z = 1.0;
export const LOBBY_FRONT_Z = ROOM_OUTER_Z + 0.06;
export const LOBBY_DEPTH = LOBBY_FRONT_Z - LOBBY_REAR_Z;
export const lobbyCenterX = half => half + LOBBY_WIDTH / 2;
export const lobbyCenterZ = (LOBBY_REAR_Z + LOBBY_FRONT_Z) / 2;

export function buildRoomLayout(rooms) {
  const groups = new Map();
  for (const room of rooms) {
    const floor = Number(room.floor);
    if (room.floor == null || !Number.isFinite(floor)) continue;
    if (!groups.has(floor)) groups.set(floor, { N: [], S: [], other: [] });
    const orientation = String(room.orientation || '').toUpperCase();
    groups.get(floor)[orientation === 'N' || orientation === 'S' ? orientation : 'other'].push(room);
  }
  for (const group of groups.values()) {
    const sort = (a,b) => String(a.id).localeCompare(String(b.id),undefined,{numeric:true});
    group.N.sort(sort); group.S.sort(sort); group.other.sort(sort);
    group.other.forEach(room => (group.N.length <= group.S.length ? group.N : group.S).push(room));
  }
  const widths = [];
  const areaWidth = room => {
    const area = Number(room.area_m2);
    // Unknown sizes use a neutral 22 m² visual footprint, never a reported measurement.
    return (Number.isFinite(area) && area > 0 ? area : 22) / ROOM_DEPTH_METERS * UNIT_PER_METER;
  };
  for (const group of groups.values()) for (const side of ['N','S']) group[side].forEach((room,index)=>{
    widths[index] = Math.max(widths[index] || 0, areaWidth(room) + STRUCTURAL_GAP);
  });
  const total = widths.reduce((sum,width)=>sum+width,0);
  const centers = []; let cursor = -total/2;
  widths.forEach(width=>{centers.push(cursor+width/2);cursor+=width});
  const result = new Map();
  for (const [floor,group] of groups) {
    const entries=[];
    for (const side of ['N','S']) group[side].forEach((room,index)=>entries.push({
      room, side, index, x:centers[index], z:(side==='S'?1:-1)*(CORRIDOR_WIDTH/2+ROOM_DEPTH/2),
      width:areaWidth(room), depth:ROOM_DEPTH, slotWidth:widths[index],
      areaKnown:Number.isFinite(Number(room.area_m2)) && Number(room.area_m2)>0,
    }));
    result.set(floor,entries);
  }
  return result;
}

export const halfWidth = entries => Math.max(1.5, ...entries.map(({x,slotWidth,width})=>Math.abs(x)+(slotWidth||width)/2));

// Asset positions are illustrative groupings. Only a floor/location explicitly supplied by the data is used.
export function buildAssetLayout(assets, floors, half, gap, mode) {
  const roof = [], technical = [], lobby = [], floorAssets = [];
  for (const asset of assets || []) {
    if (!asset.id) continue;
    const floor = asset.floor == null ? null : Number(asset.floor);
    if (floor != null && Number.isFinite(floor) && floors.includes(floor)) floorAssets.push(asset);
    else if (String(asset.type).toLowerCase()==='pv') roof.push(asset);
    else if (String(asset.location||'').toLowerCase()==='lobby') lobby.push(asset);
    else technical.push(asset);
  }
  const bottom=-(floors.length-1)*gap/2, top=(floors.length-1)*gap/2;
  const placements=[];
  technical.forEach((asset,i)=>placements.push({asset,group:'technical',position:[-half-1.85-Math.floor(i/8)*.7,bottom+0.38,-2.3+(i%8)*.65]}));
  roof.forEach((asset,i)=>placements.push({asset,group:'roof',position:[(i-(roof.length-1)/2)*1.2,top+STORY_HEIGHT+0.22,-0.9]}));
  lobby.forEach((asset,i)=>placements.push({asset,group:'lobby',floor:floors[0],position:[lobbyCenterX(half)+0.88-i*0.45,(mode==='floor'?0:bottom)+0.36,lobbyCenterZ-0.32]}));
  floorAssets.forEach((asset,i)=>{
    const floor=Number(asset.floor),index=floors.indexOf(floor);
    const y=mode==='floor'?0:(index-(floors.length-1)/2)*gap;
    placements.push({asset,group:'floor',floor,position:[half+0.48,y+0.35,-0.35+(i%2)*0.45]});
  });
  return placements;
}
