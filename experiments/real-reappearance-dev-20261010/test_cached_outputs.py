"""Only cached text/data checks; no media inference or human ground truth."""
from pathlib import Path
import hashlib,json,unittest
from replay_identity_r1 import replay_fsm,replay_evidence
B=Path(__file__).parent
class CachedOutputs(unittest.TestCase):
 def test_original_answer_bytes_match_saved_hashes(self):
  for row in json.loads((B/'results_summary.json').read_text())['conditions']:
   p=B/'model_outputs'/(row['packet_id']+'.json')
   self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),row['raw_answer_sha256'])
   self.assertEqual(len(p.read_bytes()),row['raw_answer_bytes'])
 def test_events_preserve_per_frame_labels(self):
  relation={'external_connection_supported':'connected','separation_supported':'disconnected','contact_or_occupancy_only':'unknown','occluded_or_unknown':'unknown'}
  identity={'supported_within_visible_sequence':'supported','uncertain':'uncertain'}
  for case in json.loads((B/'frozen_events.v1.json').read_text())['fixtures']:
   observations=json.loads((B/'model_outputs'/(case['case_id']+'.json')).read_text())['frame_observations']
   self.assertEqual(len(observations),len(case['events']))
   for a,e in zip(observations,case['events']):
    self.assertEqual(a['frame_id'],e['source']['frame_id']);self.assertEqual(relation[a['relation_state']],e['observation']);self.assertEqual(identity[a['identity_status']],e['identity'])
 def test_same_input_outputs_and_observation_trace(self):
  expected={'q31':['connected'],'q68':['connected','unknown'],'q92':['connected','unknown','connected']}
  for case in json.loads((B/'frozen_events.v1.json').read_text())['fixtures']:
   left=replay_fsm(json.loads(json.dumps(case)));right=replay_evidence(json.loads(json.dumps(case)))
   self.assertEqual(left,right);self.assertEqual([s['current_direct'] for s in left['snapshots']],expected[case['case_id']]);self.assertFalse(any(s['identity_revalidation_required'] for s in left['snapshots']))
if __name__=='__main__':unittest.main()
