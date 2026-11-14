"""Causal, paginated activity projection; source records remain unchanged."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from .ui_intelligence import causal_log, decision_lifecycle, value

CATEGORIES = {'events', 'requests', 'anomalies', 'incidents', 'decisions', 'actions', 'approvals', 'verification'}


def _stamp(raw):
    if not raw:
        return None
    try:
        parsed = pd.Timestamp(raw)
        if pd.isna(parsed):
            return None
        return parsed.tz_localize('UTC') if parsed.tzinfo is None else parsed.tz_convert('UTC')
    except (ValueError, TypeError, OverflowError):
        return None


def _stable_id(category, row, suffix=''):
    # Stable across pagination and replay times, including rows with no source identifier.
    canonical = json.dumps(value(row), sort_keys=True, ensure_ascii=False, default=str, separators=(',', ':'))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f'{category}:{digest}{suffix}'


def _assets(row):
    assets = row.get('affected_assets') or row.get('asset_ids') or []
    if not isinstance(assets, list):
        assets = [assets]
    for key in ('room_id', 'asset_id', 'target_asset'):
        if row.get(key):
            assets.append(row[key])
    return list(dict.fromkeys(str(a) for a in assets if a is not None and str(a)))


def build_activity(obs, timestamp, decisions_path=None, incidents_path=None, actions_path=None,
                   category='all', asset='', severity='', issue_type='', status='', from_date='', to_date='',
                   cursor=None, limit=50):
    """Use only records available as of the snapped replay instant, including delayed records."""
    t = _stamp(timestamp)
    if t is None or len(obs.grid) == 0 or t < obs.grid[0] or t > obs.grid[-1]:
        raise ValueError('Replay timestamp outside dataset range')
    t = obs.grid[obs.grid.searchsorted(t, side='right') - 1]
    if category != 'all' and category not in CATEGORIES:
        raise ValueError('Unknown activity type')
    limit = min(max(int(limit), 1), 100)
    view = obs.asof(t)
    rows = []

    def append(kind, row, title, source, description='', stage=None, suffix=''):
        row = value(row)
        stamp = _stamp(row.get('timestamp_utc'))
        available = _stamp(row.get('available_at') or row.get('available_at_utc') or row.get('published_utc'))
        if stamp is None or stamp > t or (available is not None and available > t):
            return
        # Invalid explicit availability is unknown and must never be exposed as known.
        if any(row.get(k) for k in ('available_at', 'available_at_utc', 'published_utc')) and available is None:
            return
        rows.append({'id': _stable_id(kind, row, suffix), 'timestamp_utc': value(stamp),
                     'available_at_utc': value(available or stamp), 'type': kind, 'source': source,
                     'title': title, 'description': description or '', 'asset_ids': _assets(row),
                     'severity': row.get('priority') or row.get('severity'), 'issue_type': row.get('issue_type'),
                     'status': stage or row.get('status') or ({'events':'historical_record', 'requests':'received', 'anomalies':'reported'}.get(kind)), 'incident_id': row.get('episode_id') or row.get('incident_id') or (row.get('event_id') if kind == 'incidents' else None),
                     'decision_id': row.get('decision_event_id') or (row.get('event_id') if kind == 'decisions' else None),
                     'metadata': row})

    for row in view.events().to_dict('records'):
        row = value(row)
        event_type = row.get('type') or 'event'
        anomaly = event_type == 'bms_alarm'
        title = ('BMS alarm reported' if anomaly else {'checkin':'Guest checked in', 'checkout':'Guest checked out', 'checkin_early':'Guest checked in early', 'checkout_early':'Guest checked out early', 'stay_in_progress':'Stay in progress', 'cleaning_started':'Cleaning started', 'cleaning_completed':'Cleaning completed'}.get(event_type, event_type.replace('_', ' ').capitalize()))
        append('anomalies' if anomaly else 'events', row, title, 'events.jsonl',
               'A BMS alarm is a clue, not a confirmed fault.' if anomaly else row.get('detail') or '')
    for row in view.guest_requests().to_dict('records'):
        row = value(row)
        append('requests', row, (row.get('request_type') or 'Guest request').replace('_', ' ').capitalize(),
               'guest_requests.csv', row.get('text') or '')
    # The observation pipeline has time-stamped quality findings; undated ingestion summaries are excluded.
    for row in obs.issues.to_dict('records'):
        row = value(row)
        if not row.get('start_utc'):
            continue
        safe = {k: row.get(k) for k in ('source', 'issue', 'asset_id', 'column', 'start_utc', 'action')}
        safe['timestamp_utc'] = safe['start_utc']
        append('anomalies', safe, (safe.get('issue') or 'Quality finding').replace('_', ' ').capitalize(),
               'observe / ' + str(safe.get('source') or 'data quality'),
               'Quality finding, not proof of an operational fault.')

    actions = causal_log(actions_path, t)
    visible_incidents = causal_log(incidents_path, t)
    incident_by_episode = {row.get('episode_id'): row for row in visible_incidents if row.get('episode_id')}
    for row in visible_incidents:
        if row.get('issue_type') != 'no_incident':
            append('incidents', row, row.get('title') or str(row.get('issue_type') or 'Detected incident').replace('_', ' ').capitalize(),
                   row.get('detector') or 'incident output', row.get('reason') or '')
    visible_decisions = causal_log(decisions_path, t)
    decisions_by_id = {row.get('event_id'): row for row in visible_decisions if row.get('event_id')}
    for row in visible_decisions:
        state = decision_lifecycle(row, actions)
        # Separate current lifecycle status from the original decision record.
        enriched = {**row, 'lifecycle': state}
        origin = row.get('source') or incident_by_episode.get(row.get('episode_id'), {}).get('source')
        source = f'decision pipeline · {origin}' if origin else 'decision output'
        append('decisions', enriched, str(row.get('action') or 'Decision').replace('_', ' ').capitalize(),
               source, row.get('reason') or '', stage=state['stage'])
    for row in actions:
        def append_audit(kind, title, source, stage, suffix):
            before = len(rows)
            append(kind, row, title, source, row.get('reason') or '', stage=stage, suffix=suffix)
            decision = decisions_by_id.get(row.get('decision_event_id'))
            if decision and len(rows) > before:
                # Relationship is explicit by decision_event_id, never inferred by asset overlap.
                item = rows[-1]
                item['asset_ids'] = list(dict.fromkeys(item['asset_ids'] + _assets(decision)))
                item['incident_id'] = item['incident_id'] or decision.get('episode_id') or decision.get('incident_id')
                item['issue_type'] = item['issue_type'] or decision.get('issue_type')
                item['severity'] = item['severity'] or decision.get('priority')
        if row.get('execution_status') or row.get('action') or row.get('action_type'):
            append_audit('actions', str(row.get('action') or row.get('action_type') or 'Action record').replace('_', ' ').capitalize(),
                         'action audit', row.get('execution_status') or 'recorded', ':execution')
        if row.get('approval_status') or row.get('consent_status'):
            append_audit('approvals', 'Approval or consent update', 'approval audit',
                         row.get('approval_status') or row.get('consent_status'), ':approval')
        if row.get('verification_status') or row.get('verification_evidence'):
            append_audit('verification', 'Verification update', 'verification audit',
                         row.get('verification_status') or 'evidence_recorded', ':verification')

    def within(row):
        local_date = pd.Timestamp(row['timestamp_utc']).tz_convert('Europe/Vienna').strftime('%Y-%m-%d')
        return ((category == 'all' or row['type'] == category)
                and (not asset or any(asset.casefold() in x.casefold() for x in row['asset_ids']))
                and (not severity or str(row.get('severity') or '').casefold() == severity.casefold())
                and (not issue_type or issue_type.casefold() in str(row.get('issue_type') or '').casefold())
                and (not status or status.casefold() in str(row.get('status') or '').casefold())
                and (not from_date or local_date >= from_date) and (not to_date or local_date <= to_date))
    # Identity for decision events must not change when later actions update their lifecycle.
    for row in rows:
        if row['type'] == 'decisions':
            raw = {k: v for k, v in row['metadata'].items() if k != 'lifecycle'}
            row['id'] = _stable_id('decisions', raw)
    unique = {row['id']: row for row in rows if within(row)}
    ordered = sorted(unique.values(), key=lambda r: (_stamp(r['timestamp_utc']).value, r['id']), reverse=True)
    total = len(ordered)
    highlights_by_type = {}
    for item in ordered:
        highlights_by_type.setdefault(item['type'], item)
    highlights = sorted(highlights_by_type.values(), key=lambda r: (_stamp(r['timestamp_utc']).value, r['id']), reverse=True)[:5]
    if cursor:
        try:
            cursor_time, cursor_id = cursor.split('|', 1)
            if _stamp(cursor_time) is None or not cursor_id:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Invalid activity cursor')
        cursor_key = (_stamp(cursor_time).value, cursor_id)
        ordered = [r for r in ordered if (_stamp(r['timestamp_utc']).value, r['id']) < cursor_key]
    page = ordered[:limit]
    next_cursor = f"{page[-1]['timestamp_utc']}|{page[-1]['id']}" if len(ordered) > limit else None
    return {'as_of_utc': value(t), 'items': page, 'highlights': highlights, 'total': total, 'next_cursor': next_cursor,
            'availability': {'incidents': bool(incidents_path and Path(incidents_path).is_file()),
                             'decisions': bool(decisions_path and Path(decisions_path).is_file()),
                             'actions': bool(actions_path and Path(actions_path).is_file())}}
