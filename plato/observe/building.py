"""Typed view of building.json (rooms, floors, gateways, limits)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass
class Building:
    rooms: List[str]
    floor_of: Dict[str, int]
    gateway_of: Dict[str, str]
    comfort_c: Tuple[float, float] = (20.0, 23.0)  # occupied-room comfort band
    soc_bounds_kwh: Tuple[float, float] = (10.0, 95.0)
    battery_usable_kwh: float = 100.0
    battery_max_kw: float = 40.0
    pv_kwp: float = 60.0
    resources: Dict[str, str] = field(default_factory=dict)  # resource_id -> role
    raw: dict = field(default_factory=dict)

    @property
    def gateways(self) -> List[str]:
        return sorted(set(self.gateway_of.values()))

    def rooms_of_gateway(self, gw: str) -> List[str]:
        return [r for r in self.rooms if self.gateway_of[r] == gw]

    @classmethod
    def from_json(cls, path: Path) -> "Building":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        bld = raw.get("building", {})
        rooms_raw = bld.get("rooms", [])
        rooms = sorted(r["id"] for r in rooms_raw)
        floor_of = {r["id"]: int(r["floor"]) for r in rooms_raw}
        gateway_of = {r: f"GW_F{floor_of[r]}" for r in rooms}
        for s in bld.get("sensors", []):  # explicit sensor->gateway mapping wins
            if s.get("asset_id") in gateway_of and s.get("gateway"):
                gateway_of[s["asset_id"]] = s["gateway"]

        out = cls(rooms=rooms, floor_of=floor_of, gateway_of=gateway_of, raw=raw)
        comfort = raw.get("constraints", {}).get("occupied_comfort_c")
        if comfort and len(comfort) == 2:
            out.comfort_c = (float(comfort[0]), float(comfort[1]))
        for a in bld.get("assets", []):
            if a.get("id") == "BAT1":
                out.battery_usable_kwh = float(a.get("usable_kwh", out.battery_usable_kwh))
                out.battery_max_kw = float(a.get("max_discharge_kw", out.battery_max_kw))
                sb = a.get("soc_bounds_kwh")
                if sb and len(sb) == 2:
                    out.soc_bounds_kwh = (float(sb[0]), float(sb[1]))
            elif a.get("id") == "PV1":
                out.pv_kwp = float(a.get("peak_kwp", out.pv_kwp))
        out.resources = {r["resource_id"]: r["role"] for r in raw.get("staff", {}).get("resources", [])}
        return out
