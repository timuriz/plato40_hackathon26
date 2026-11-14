"""Dashboard data integrity and replay regression tests. No live LLM or example incidents."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from plato.observe import observe
from plato.ui_payload import build_dashboard_payload, build_resource_tracking
from plato.ui_intelligence import causal_log, decision_lifecycle, learning_insights, next_incident_time, snapshot

ROOT = Path(__file__).resolve().parents[1]


class ReplayLogTests(unittest.TestCase):
    def write_log(self, directory, rows):
        path = Path(directory) / 'test-only.jsonl'
        path.write_text('\n'.join(json.dumps(r) for r in rows))
        return path

    def test_future_updates_and_nested_states_do_not_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_log(directory, [
                {'event_id':'a', 'timestamp_utc':'2026-01-05T01:00Z', 'approval_status':'approved',
                 'approval_at_utc':'2026-01-05T03:00Z', 'verification_status':'verified',
                 'verification_at_utc':'2026-01-05T04:00Z', 'verification_evidence':['future outcome']},
                {'event_id':'a', 'timestamp_utc':'2026-01-05T03:00Z', 'approval_status':'approved'},
                {'event_id':'undated'},
            ])
            visible = causal_log(path, pd.Timestamp('2026-01-05T02:00Z'))
            self.assertEqual(len(visible), 1)
            self.assertNotIn('approval_status', visible[0])
            self.assertNotIn('verification_evidence', visible[0])
            late = snapshot(causal_log(path, pd.Timestamp('2026-01-05T03:00Z')), ('event_id',))
            self.assertEqual(len(late), 1)
            self.assertEqual(late[0]['approval_status'], 'approved')
            self.assertEqual(late[0]['first_seen_utc'], '2026-01-05T01:00:00Z')

    def test_proposal_is_not_execution_and_verification_requires_evidence(self):
        decision = {'event_id':'test', 'requires_human_approval':True,
                    'requires_guest_consent':True, 'expected_outcome':'Simulated: room cleaned'}
        state = decision_lifecycle(decision, [])
        self.assertEqual(state['stage'], 'awaiting_approval')
        self.assertEqual(state['execution'], 'not_recorded')
        self.assertEqual(state['consent'], 'required_not_recorded')
        claim = [{'decision_event_id':'test','verification_status':'verified'}]
        self.assertNotEqual(decision_lifecycle(decision, claim)['stage'], 'verified_outcome')
        claim[0]['verification_evidence'] = ['test fixture evidence']
        self.assertEqual(decision_lifecycle(decision, claim)['stage'], 'verified_outcome')
        simulation = [{'decision_event_id':'test','approval_status':'approved','execution_status':'simulated_completed'}]
        self.assertEqual(decision_lifecycle(decision, simulation)['stage'], 'simulated_execution')


class DashboardDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs = observe(ROOT / 'data')

    def test_default_dashboard_contains_no_illustrative_incidents_or_decisions(self):
        for timestamp in [self.obs.grid[0], self.obs.grid[130], self.obs.grid[-1]]:
            payload = build_dashboard_payload(self.obs, timestamp)
            self.assertEqual(payload['incidents'], [])
            self.assertEqual(payload['decisions'], [])
            self.assertEqual(payload['actions'], [])
            self.assertFalse(payload['capabilities']['approval_write'])
            self.assertFalse(payload['capabilities']['execution_write'])
            json.dumps(payload, allow_nan=False)
            for event in payload['operational_context']['events']:
                self.assertLessEqual(pd.Timestamp(event['timestamp_utc']), timestamp)
            for forecast in payload['operational_context']['forecast']:
                self.assertLessEqual(pd.Timestamp(forecast['issued_utc']), timestamp)
            for price in payload['operational_context']['tariff']:
                self.assertLessEqual(pd.Timestamp(price['published_utc']), timestamp)

    def test_staff_location_is_not_invented_from_a_shift_or_dispatch(self):
        early = pd.Timestamp('2026-01-05T08:00Z')
        tracking = build_resource_tracking(self.obs, self.obs.asof(early), early)
        self.assertTrue(any(r['shift_status']=='scheduled' for r in tracking['resources']))
        self.assertTrue(all(r['last_known_location'] is None for r in tracking['resources']))
        at = pd.Timestamp('2026-01-06T08:30Z')
        resources = build_resource_tracking(self.obs, self.obs.asof(at), at)['resources']
        cleaner = next(r for r in resources if r['resource_id']=='CLEANER_1')
        self.assertEqual(cleaner['last_known_location']['room_id'], 'R201')
        self.assertEqual(cleaner['last_known_location']['source'], 'events')
        self.assertIsNone(cleaner['last_known_location']['coordinates'])
        completed = pd.Timestamp('2026-01-06T09:15Z')
        later = build_resource_tracking(self.obs, self.obs.asof(completed), completed)['resources']
        cleaner = next(r for r in later if r['resource_id']=='CLEANER_1')
        self.assertEqual(cleaner['last_task_report']['state'], 'completed')

    def test_incident_navigation_rounds_up_and_exposes_only_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'test-only.jsonl'
            path.write_text(json.dumps({'episode_id':'test-episode','timestamp_utc':'2026-01-05T01:07Z','issue_type':'water_leak'}))
            result = next_incident_time(self.obs,path,self.obs.grid[0],'next')
            self.assertEqual(result, {'timestamp_utc':'2026-01-05T01:15:00Z'})
            self.assertEqual(next_incident_time(self.obs,path,pd.Timestamp(result['timestamp_utc']),'next'), {'timestamp_utc':None})
            self.assertEqual(next_incident_time(self.obs,path,self.obs.grid[-1],'previous'), result)

    def test_learning_uses_only_observed_missing_samples(self):
        self.assertEqual(learning_insights(self.obs,self.obs.grid[0]), [])
        for timestamp in [self.obs.grid[800],self.obs.grid[-1]]:
            for insight in learning_insights(self.obs,timestamp):
                evidence = insight['evidence'][0]
                expected = int((self.obs.equipment.wide.quality.loc[:timestamp,evidence['signal']] == 1).sum())
                self.assertEqual(evidence['missing_steps'], expected)
                self.assertLessEqual(pd.Timestamp(evidence['latest_missing_utc']), timestamp)
                self.assertIn(insight['target'], ['CLARA','PLATO'])
        self.assertEqual(len(learning_insights(self.obs,self.obs.grid[-1])), 2)


if __name__ == '__main__':
    unittest.main()
