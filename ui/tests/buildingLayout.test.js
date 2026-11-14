import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRoomLayout, buildAssetLayout, ROOM_DEPTH, CORRIDOR_WIDTH, UNIT_PER_METER } from '../src/buildingLayout.js';

const fixture = Array.from({length:4},(_,f)=>['S','N'].flatMap((orientation,side)=>[22,28,32,28,22].map((area_m2,i)=>({
  id:`R${f+1}${String(i+1+side*5).padStart(2,'0')}`,floor:f+1,orientation,area_m2,
})))).flat();

test('layout preserves IDs, cardinal sides and proportional areas without overlaps',()=>{
  const layout=buildRoomLayout(fixture);
  assert.equal(layout.size,4);
  for(const entries of layout.values()){
    assert.equal(entries.length,10);
    for(const side of ['N','S']){
      const row=entries.filter(e=>e.side===side).sort((a,b)=>a.x-b.x);
      assert.equal(row.length,5);
      row.forEach((entry,i)=>{
        assert.ok(side==='N'?entry.z<0:entry.z>0);
        assert.ok(Math.abs(Math.abs(entry.z)-entry.depth/2-CORRIDOR_WIDTH/2)<1e-10);
        assert.ok(Math.abs(entry.width*entry.depth/(UNIT_PER_METER**2)-entry.room.area_m2)<1e-10);
        assert.ok(Math.abs(entry.width*ROOM_DEPTH/(row[0].width*ROOM_DEPTH)-entry.room.area_m2/row[0].room.area_m2)<1e-10);
        if(i) assert.ok(row[i-1].x+row[i-1].width/2 < entry.x-entry.width/2);
      });
    }
  }
  assert.equal(new Set([...layout.values()].flat().map(e=>e.room.id)).size,40);
});

test('layout adapts to different floor counts and missing metadata',()=>{
  const layout=buildRoomLayout([{id:'A',floor:8,orientation:'S',area_m2:32},{id:'B',floor:8,area_m2:null},{id:'C',floor:9,orientation:'N',area_m2:22}]);
  assert.deepEqual([...layout.keys()],[8,9]);
  assert.equal(layout.get(8).length,2);
  assert.equal(layout.get(8).find(e=>e.room.id==='B').areaKnown,false);
  assert.ok(layout.get(8).every(e=>Number.isFinite(e.x)&&e.width>0));
});

test('asset markers use supplied floor and schematic groups',()=>{
  const items=buildAssetLayout([{id:'X',type:'heat_pump'},{id:'Y',type:'waste_bin',floor:2},{id:'Z',type:'pv'}],[1,2],5,2.05,'building');
  assert.deepEqual(items.map(i=>i.group),['technical','roof','floor']);
  assert.equal(items.find(i=>i.asset.id==='Y').floor,2);
});
