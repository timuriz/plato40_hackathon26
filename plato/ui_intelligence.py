"""Read-only dashboard projections. No detector, approval, or executor is simulated here."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .observe.quality import Q_NAMES


def value(obj):
    if isinstance(obj, dict):
        return {str(k): value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [value(v) for v in obj]
    if obj is None or pd.isna(obj):
        return None
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat().replace('+00:00', 'Z')
    return obj.item() if isinstance(obj, np.generic) else obj


def records(frame):
    return [value(row) for row in frame.to_dict('records')]


def read_log(path):
    if not path or not Path(path).is_file():
        return []
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{path}:{number}: {exc.msg}') from exc
        if not isinstance(row, dict):
            raise ValueError(f'{path}:{number}: expected an object')
        try:
            stamp = pd.to_datetime(row.get('timestamp_utc'), utc=True)
        except (TypeError, ValueError):
            continue
        if stamp is None or pd.isna(stamp):
            continue  # Undated recommendations and states cannot be used in causal replay.
        rows.append({**row, 'timestamp_utc': value(stamp)})
    return sorted(rows, key=lambda row: pd.Timestamp(row['timestamp_utc']))


def causal_log(path, timestamp):
    visible = []
    for original in read_log(path):
        if pd.Timestamp(original['timestamp_utc']) > timestamp:
            continue
        row = dict(original)
        # Also honor explicit state-change timestamps in enriched decision records.
        for prefix in ('approval', 'consent', 'execution', 'verification'):
            changed = row.get(f'{prefix}_timestamp_utc') or row.get(f'{prefix}_at_utc')
            if changed:
                parsed = pd.to_datetime(changed, utc=True, errors='coerce')
                if pd.isna(parsed) or parsed > timestamp:
                    row = {k: v for k, v in row.items() if not k.startswith(prefix+'_')}
        visible.append(row)
    return visible


def snapshot(rows, id_fields):
    """Latest known update per explicit identity, preserving first detection time."""
    result = {}
    for index, row in enumerate(rows):
        key = next((row.get(k) for k in id_fields if row.get(k)), f'row-{index}')
        first = result.get(key, {}).get('first_seen_utc', row['timestamp_utc'])
        result[key] = {**row, 'first_seen_utc': first}
    return list(result.values())


def decision_lifecycle(decision, actions):
    related = [a for a in actions if a.get('decision_event_id') == decision.get('event_id')]
    approval = decision.get('approval_status') or ('pending' if decision.get('requires_human_approval') else 'not_required' if decision.get('requires_human_approval') is False else 'unknown')
    consent = decision.get('consent_status') or ('required_not_recorded' if decision.get('requires_guest_consent') else 'not_required' if decision.get('requires_guest_consent') is False else 'unknown')
    execution = decision.get('execution_status') or 'not_recorded'
    verification = decision.get('verification_status') or 'not_recorded'
    proof = decision.get('verification_evidence') or []
    for event in related:
        approval = event.get('approval_status', approval)
        consent = event.get('consent_status', consent)
        execution = event.get('execution_status', execution)
        verification = event.get('verification_status', verification)
        proof = event.get('verification_evidence', proof)
    verified = verification in ('verified', 'passed') and bool(proof)
    if verification in ('verified', 'passed') and not proof:
        verification = 'reported_without_evidence'
    if verified:
        stage = 'verified_outcome'
    elif approval in ('rejected', 'deferred') or decision.get('action') == 'defer_action':
        stage = 'rejected' if approval == 'rejected' else 'deferred'
    elif execution in ('simulated', 'simulated_completed'):
        stage = 'simulated_execution'
    elif execution not in ('not_recorded', 'pending', 'proposed'):
        stage = 'execution_reported'  # A log claim, not a changed historical measurement.
    elif approval == 'approved':
        stage = 'approved_action'
    elif decision.get('requires_human_approval'):
        stage = 'awaiting_approval'
    else:
        stage = 'proposed_action'
    return {'stage': stage, 'approval': approval, 'consent': consent, 'execution': execution,
            'verification': verification, 'verification_evidence': proof, 'events': related}


def next_incident_time(obs, path, timestamp, direction):
    firsts = {}
    for index, row in enumerate(read_log(path)):
        if row.get('issue_type') == 'no_incident':
            continue
        identity = row.get('episode_id') or row.get('incident_id') or row.get('event_id') or f'row-{index}'
        firsts.setdefault(identity, pd.Timestamp(row['timestamp_utc']))
    # Move to the first step where a detection is actually available (never round it back).
    candidates = set()
    for stamp in firsts.values():
        i = obs.grid.searchsorted(stamp, side='left')
        if i < len(obs.grid):
            candidates.add(obs.grid[i])
    targets = sorted(t for t in candidates if (t > timestamp if direction == 'next' else t < timestamp))
    target = (targets[0] if direction == 'next' else targets[-1]) if targets else None
    return {'timestamp_utc': value(target)}  # No future incident content is disclosed.


def operational_context(obs, view, timestamp):
    def current(name):
        frame = view.series(name)
        return value(frame.iloc[-1].to_dict()) if len(frame) else {}
    events = view.events()
    recent = events[events.timestamp_utc >= timestamp-pd.Timedelta('48h')]
    requests = view.guest_requests()
    requests = requests[requests.timestamp_utc >= timestamp-pd.Timedelta('48h')]
    tariff = view.tariff()
    tariff = tariff[(tariff.timestamp_utc <= timestamp+pd.Timedelta('24h')) & (tariff.timestamp_utc >= timestamp.floor('h'))]
    orders = view.work_orders()
    return {
        'as_of_utc': value(timestamp),
        'events': records(recent), 'guest_requests': records(requests),
        'work_orders': records(orders),
        'weather': current('weather'), 'weather_quality': {k: Q_NAMES.get(v,'missing') for k,v in current('weather_q').items()},
        'forecast': records(view.forecast().head(24)), 'tariff': records(tariff),
        'equipment': current('equipment'), 'equipment_quality': {k: Q_NAMES.get(v,'missing') for k,v in current('equipment_q').items()},
        'equipment_status': current('equipment_status'), 'water_balance': current('water.balance'),
        'water_note': 'Residual includes common-area use and losses. Missing room meters invalidate a complete comparison; it is not proof of a leak.',
        'safety_constraints': obs.building.raw.get('constraints', {}),
    }


def learning_insights(obs, timestamp):
    """Reproducible descriptive recommendations from history available by the cursor."""
    quality = obs.equipment.wide.quality.loc[:timestamp]
    if quality.empty:
        return []
    missing = (quality == 1).sum()
    if missing.empty or missing.max() < 4:
        return []
    signal = str(missing.idxmax())
    stamps = quality.index[quality[signal] == 1]
    count, total = int(missing.max()), len(quality)
    evidence = {'source': 'equipment.csv', 'signal': signal, 'missing_steps': count, 'total_steps': total,
                'missing_share_pct': round(100*count/total, 2), 'first_missing_utc': value(stamps[0]),
                'latest_missing_utc': value(stamps[-1]), 'observed_through_utc': value(timestamp),
                'method': 'Count quality=missing in the causal 15-minute equipment series; estimated values are not counted as measured.'}
    limitation = 'Missing readings do not establish an equipment fault. This is only the available replay period, not an extrapolation to a full year. Validate sensor and gateway causes before investment.'
    return [
        {'insight_id': 'CLARA-sensor-continuity', 'target': 'CLARA', 'topic': 'Operational monitoring',
         'timestamp_utc': value(timestamp), 'finding': f'{signal}: {count} of {total} expected observations are missing ({100*count/total:.1f}%).',
         'evidence': [evidence], 'explanation': 'Decisions depending on this signal cannot rely on uninterrupted measurement coverage.',
         'recommendation': 'Add a sensor-continuity check before condition-based maintenance decisions. Surface gaps and require corroborating signals before dispatch.',
         'limitations': limitation, 'expected_benefit': 'More explicit uncertainty and fewer unsupported maintenance decisions; benefit has not been measured.', 'source': 'deterministic_history_analysis'},
        {'insight_id': 'PLATO-sensor-resilience', 'target': 'PLATO', 'topic': 'Future building design',
         'timestamp_utc': value(timestamp), 'finding': f'The {signal} measurement path has {count} missing samples in the observed period.',
         'evidence': [evidence], 'explanation': 'A future design should account for observation gaps in equipment monitoring.',
         'recommendation': 'Review this measurement path for local buffering, sensor-health diagnostics and independent corroborating measurement. Confirm the failure mechanism before choosing hardware.',
         'limitations': limitation, 'expected_benefit': 'Better diagnosability and continuity; no savings or reliability improvement is claimed.', 'source': 'deterministic_history_analysis'},
    ]
