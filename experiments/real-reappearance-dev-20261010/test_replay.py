import unittest,copy
from replay_identity_r1 import replay_fsm,replay_evidence
class Checks(unittest.TestCase):
 def case(self,identity):
  target={'host_instance_id':'synthetic_table','part_instance_id':'synthetic_leg','interface_id':'synthetic_slot'}
  return {'target':target,'assembly_scope':['synthetic_session','synthetic_table','synthetic_spec'],'events':[{'event_id':str(i),'target':copy.deepcopy(target),'time':{'pts':i+1,'time_base':'1/25'},'observation':o,'identity':d,'scope':'external_relation_only'} for i,(o,d) in enumerate(zip(['connected','unknown','connected'],identity))]}
 def test_occlusion_does_not_revoke(self):
  c=self.case(['supported']*3);a=replay_fsm(c);b=replay_evidence(c);self.assertEqual(a,b);self.assertEqual(a['snapshots'][1]['current_direct'],'unknown');self.assertEqual(a['snapshots'][1]['current_estimate'],'connected');self.assertNotIn('revoked',a['snapshots'][1]['episode_statuses'].values());self.assertEqual(a['current']['current_direct'],'connected')
 def test_local_support_does_not_unlock(self):
  c=self.case(['supported','uncertain','supported']);a=replay_fsm(c);b=replay_evidence(c);self.assertEqual(a,b);self.assertTrue(a['current']['identity_revalidation_required']);self.assertEqual(a['current']['current_estimate'],'unknown')
if __name__=='__main__':unittest.main()
