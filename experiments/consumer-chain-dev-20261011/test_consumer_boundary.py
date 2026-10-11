"""Real consumer parser checks; no calls to models, scoring or fitting."""
from copy import deepcopy
import unittest
from consumer_boundary import evidence_envelope, require_use, exercise_real_consumers, audit


class ConsumerBoundaries(unittest.TestCase):
    def test_cached_raw_records_rejected_or_unknown_at_real_entrypoints(self):
        rows = exercise_real_consumers()
        self.assertEqual([r['packet_id'] for r in rows], ['q31', 'q68', 'q92'])
        for row in rows:
            with self.subTest(packet=row['packet_id']):
                self.assertEqual(row['production_stage_contract'], 'rejected_incompatible_schema')
                self.assertEqual(row['production_state_memory'], 'rejected_incompatible_schema')
                self.assertEqual(row['production_run_export_adapter'], 'unknown_no_stage_or_completion_exported')
                self.assertEqual(row['production_G1_training_derivation'], 'rejected_trace_schema_no_training_labels')

    def test_current_unknown_and_conditions_survive_transport(self):
        envelope = require_use(evidence_envelope('q68'), 'cache_record_audit')
        self.assertEqual(envelope['current_observation']['relation_state'], 'occluded_or_unknown')
        self.assertEqual(envelope['conditional_estimate']['basis'], 'conditional_history')
        self.assertEqual(len(envelope['conditional_estimate']['assumptions']), 2)
        self.assertFalse(envelope['record']['current_visible_confirmation_eligible'])
        self.assertIsNone(envelope['stage_mapping'])
        self.assertIsNone(envelope['completion_truth'])
        self.assertIsNone(envelope['training_ground_truth'])

    def test_q68_cannot_supply_current_visible_record_support(self):
        with self.assertRaisesRegex(ValueError, 'current_visible_support_unavailable'):
            require_use(evidence_envelope('q68'), 'current_visible_record_support')

    def test_direct_endpoint_records_still_cannot_generate_stage_or_truth(self):
        for packet in ('q31', 'q68', 'q92'):
            envelope = evidence_envelope(packet)
            for use in ('stage_scorer', 'completion_decision', 'training_ground_truth'):
                with self.subTest(packet=packet, use=use), self.assertRaisesRegex(ValueError, 'no_authorized_stage'):
                    require_use(envelope, use)
            if packet != 'q68':
                self.assertFalse(require_use(envelope, 'current_visible_record_support')['production_wired'])

    def test_transport_does_not_alias_conditions(self):
        envelope = evidence_envelope('q68')
        transported = require_use(envelope, 'cache_record_audit')
        transported['conditional_estimate']['assumptions'].clear()
        self.assertEqual(len(envelope['conditional_estimate']['assumptions']), 2)

    def test_projection_remains_explicitly_lossy(self):
        records, _, _ = audit.load_cache()
        self.assertEqual(audit.lossy_projection(records['q31']), audit.lossy_projection(records['q68']))
        self.assertNotEqual(evidence_envelope('q31')['record']['label'],
                            evidence_envelope('q68')['record']['label'])

    def test_transport_mutations_cannot_override_evidence_mode(self):
        mutations = [
            lambda e: e['conditional_estimate']['assumptions'].clear(),
            lambda e: e['conditional_estimate'].update(basis='direct_current'),
            lambda e: e['current_observation'].update(relation_state='external_connection_supported'),
            lambda e: e['record'].update(current_visible_confirmation_eligible=True),
            lambda e: e.update(stage_mapping='invented-stage'),
        ]
        for index, mutate in enumerate(mutations):
            envelope = evidence_envelope('q68')
            mutate(envelope)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'transport_changed'):
                require_use(envelope, 'cache_record_audit')


if __name__ == '__main__':
    unittest.main()
