import test from 'node:test';
import assert from 'node:assert/strict';
import { incidentVisible, visibleIncidents } from '../src/incidentVisibility.js';

const surge = (day, at) => ({
  issue_type: 'occupancy_surge', status: 'detected', timestamp_utc: at,
  affected_assets: ['BUILDING', 'R101'], features: { target_date_local: day },
});

test('a surge label exists only on its own day', () => {
  const incidents = [
    surge('2026-01-06', '2026-01-05T06:00:00Z'),
    surge('2026-01-23', '2026-01-21T06:00:00Z'),
    surge('2026-01-24', '2026-01-22T06:00:00Z'),
  ];
  const shown = visibleIncidents(incidents, '2026-01-24T12:00:00Z', []);
  assert.deepEqual(shown.map(item => item.features.target_date_local), ['2026-01-24']);
});

test('a turnaround label goes away once the room is clean', () => {
  const incident = { issue_type: 'room_turnaround', status: 'detected', timestamp_utc: '2026-01-08T08:00:00Z', affected_assets: ['R101'] };
  const dirty = [{ id: 'R101', readiness: { state: 'dirty' } }];
  const clean = [{ id: 'R101', readiness: { state: 'clean' } }];
  assert.equal(incidentVisible(incident, '2026-01-08T18:00:00Z', dirty), true);
  assert.equal(incidentVisible(incident, '2026-01-08T10:00:00Z', clean), false);
});

test('a cold-room label goes away once the room is back in band', () => {
  const incident = { issue_type: 'heating_comfort', status: 'detected', timestamp_utc: '2026-01-10T06:00:00Z', affected_assets: ['R208'] };
  assert.equal(incidentVisible(incident, '2026-01-10T07:00:00Z', [{ id: 'R208', comfort: { within_band: false } }]), true);
  assert.equal(incidentVisible(incident, '2026-01-10T07:00:00Z', [{ id: 'R208', comfort: { within_band: true } }]), false);
});

test('a leak label drops after the detector goes quiet', () => {
  const incident = { issue_type: 'water_leak', status: 'detected', timestamp_utc: '2026-01-13T22:00:00Z', affected_assets: ['R104', 'WM_MAIN'] };
  assert.equal(incidentVisible(incident, '2026-01-14T00:00:00Z', []), true);
  assert.equal(incidentVisible(incident, '2026-01-14T02:00:00Z', []), false);
});

test('a guest reply label ends at the deadline', () => {
  const incident = {
    issue_type: 'guest_request', status: 'detected', timestamp_utc: '2026-01-05T09:00:00Z', affected_assets: ['R309'],
    features: { response_due_utc: '2026-01-05T13:00:00Z' },
  };
  assert.equal(incidentVisible(incident, '2026-01-05T12:00:00Z', []), true);
  assert.equal(incidentVisible(incident, '2026-01-05T13:01:00Z', []), false);
});
