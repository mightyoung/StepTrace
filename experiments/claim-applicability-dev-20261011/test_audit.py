"""Software regression checks on frozen AI records and explicit synthetic negatives."""
from copy import deepcopy
import unittest
from audit import adapt, load_cache, lossy_projection, negative_records, PROVENANCE, run


class Applicability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.events, cls.verified = load_cache()

    def test_original_bytes_and_blob_ids(self):
        self.assertEqual(len(self.verified), 23)

    def test_endpoint_and_history_eligibility(self):
        for packet, label, eligible in [('q31', 'direct-supported-by-record', True),
                                        ('q68', 'conditional-history', False),
                                        ('q92', 'direct-supported-by-record', True)]:
            with self.subTest(packet=packet):
                result = adapt(self.records[packet], self.events[packet], PROVENANCE)
                self.assertEqual(result['label'], label)
                self.assertEqual(result['current_visible_confirmation_eligible'], eligible)
                self.assertEqual(result['historical_connections'], self.records[packet]['historical_connections'])
                self.assertEqual(result['assumptions'], self.records[packet]['current_estimate']['assumptions'])

    def test_lossy_projection_collision_is_not_current_qualification(self):
        self.assertEqual(lossy_projection(self.records['q31']), lossy_projection(self.records['q68']))
        a = adapt(self.records['q31'], self.events['q31'], PROVENANCE)
        b = adapt(self.records['q68'], self.events['q68'], PROVENANCE)
        self.assertNotEqual(a['current_visible_confirmation_eligible'], b['current_visible_confirmation_eligible'])

    def test_shared_occluded_frame_has_equal_hash_and_unknown_label(self):
        middle = [adapt(self.records[p], self.events[p], PROVENANCE)['frame_labels'][1]
                  for p in ('q68', 'q92')]
        self.assertEqual(middle[0], middle[1])
        self.assertEqual(middle[0]['recorded_relation'], 'occluded_or_unknown')
        self.assertEqual(middle[0]['pts'], 1746)

    def test_synthetic_corruptions_rejected(self):
        for name, bad, provenance in negative_records(self.records):
            with self.subTest(name=name), self.assertRaises(ValueError):
                adapt(bad, self.events[bad['packet_id']], provenance)

    def test_conditional_without_assumptions_rejected(self):
        bad = deepcopy(self.records['q68'])
        bad['current_estimate']['assumptions'] = []
        with self.assertRaisesRegex(ValueError, 'missing_conditions'):
            adapt(bad, self.events['q68'], PROVENANCE)

    def test_uncertain_identity_abstains(self):
        bad = deepcopy(self.records['q31'])
        bad['identity_mapping']['status'] = 'uncertain'
        result = adapt(bad, self.events['q31'], PROVENANCE)
        self.assertEqual(result['label'], 'unknown')
        self.assertFalse(result['current_visible_confirmation_eligible'])

    def test_unbound_active_reference_rejected(self):
        bad = deepcopy(self.records['q31'])
        bad['current_estimate']['active_connection_ids'] = ['invented']
        with self.assertRaisesRegex(ValueError, 'unbound_history'):
            adapt(bad, self.events['q31'], PROVENANCE)

    def test_future_cutoff_rejected(self):
        bad = deepcopy(self.records['q92'])
        bad['timeline']['cutoff_pts'] = 1746
        with self.assertRaisesRegex(ValueError, 'future_or_wrong_timebase'):
            adapt(bad, self.events['q92'], PROVENANCE)

    def test_unknown_rate_and_correct_sampling_unit(self):
        result = run()
        self.assertIsNone(result['physical_false_revocation_rate'])
        self.assertEqual(result['physical_held_negative_count'], 0)
        self.assertEqual((result['source_events'], result['prefixes'], result['unique_frames'],
                          result['repeated_observation_rows']), (1, 3, 3, 6))
        self.assertEqual(result['negative_control']['status'], 'RED_expected_projection_collision')
        self.assertEqual(len(result['synthetic_negatives']), 5)
        self.assertFalse(result['pixel_truth_verified'])


if __name__ == '__main__':
    unittest.main()
