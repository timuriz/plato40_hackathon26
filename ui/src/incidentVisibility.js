/** Which detected incidents are still a problem at the replay cursor.

A detection record stays in the log forever. The 3D labels should not. A label stays while the
current building state still shows the fault, or, when we have no live state, until the detector's
own quiet period has passed since the last sighting.
*/

const ACTIVE = new Set(['open', 'active', 'acknowledged', 'in_progress', 'investigating', 'new', 'detected', 'escalated']);
const CLOSED = new Set(['resolved', 'closed', 'cleared', 'verified', 'rejected']);
const HOUR = 3600 * 1000;

// How long after the last sighting a label may linger when live state cannot confirm it.
const GRACE_MS = {
  water_leak: 3 * HOUR,
  heating_comfort: 3 * HOUR,
  room_turnaround: 6 * HOUR,
  ventilation_maintenance: 12 * HOUR,
  data_quality: 6 * HOUR,
  logistics: 14 * HOUR,
  guest_request: 6 * HOUR,
};

export const ROOM_PIN_TYPES = new Set(['room_turnaround', 'heating_comfort', 'water_leak', 'guest_request']);

export function viennaDate(iso) {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return '';
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Vienna', year: 'numeric', month: '2-digit', day: '2-digit' }).format(t);
}

function listedRooms(incident, rooms) {
  const ids = new Set(incident.affected_assets || []);
  return (rooms || []).filter(room => ids.has(room.id));
}

export function incidentVisible(incident, now, rooms = []) {
  if (!incident || !now) return false;
  const status = String(incident.status || '').toLowerCase();
  if (CLOSED.has(status)) return false;
  if (status && !ACTIVE.has(status)) return false;
  const type = incident.issue_type;
  if (type === 'energy_optimization') return false;
  const seen = Date.parse(incident.timestamp_utc);
  const t = Date.parse(now);
  if (!Number.isFinite(seen) || !Number.isFinite(t) || t < seen) return false;

  if (type === 'room_turnaround') {
    const roomsNow = listedRooms(incident, rooms);
    if (roomsNow.some(room => room.readiness?.state === 'clean')) return false;
    if (roomsNow.some(room => ['dirty', 'in_progress', 'inspection_required'].includes(room.readiness?.state))) return true;
  }
  if (type === 'heating_comfort') {
    const roomsNow = listedRooms(incident, rooms);
    if (roomsNow.length) {
      if (roomsNow.some(room => room.comfort?.within_band === false)) return true;
      if (roomsNow.every(room => room.comfort?.within_band === true)) return false;
    }
  }
  if (type === 'occupancy_surge') {
    const day = incident.features?.target_date_local;
    if (!day) return t - seen <= 24 * HOUR;
    return viennaDate(now) === day;
  }
  if (type === 'guest_request') {
    const due = Date.parse(incident.features?.response_due_utc || '');
    return t <= (Number.isFinite(due) ? due : seen + GRACE_MS.guest_request);
  }
  return t - seen <= (GRACE_MS[type] ?? 6 * HOUR);
}

export function visibleIncidents(incidents, now, rooms = []) {
  return (incidents || []).filter(incident => incidentVisible(incident, now, rooms));
}
