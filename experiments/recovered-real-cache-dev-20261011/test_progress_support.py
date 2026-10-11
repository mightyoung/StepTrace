"""Tests bind to newly recovered real-record fields; mutations are synthetic."""
import json
from pathlib import Path
import unittest
from check_progress_support import record_support


class ProgressSupport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = json.loads((Path(__file__).parent/'results.json').read_text())

    def test_real_pair_disambiguates_current_record_from_retained_progress(self):
        first, later = self.result['selected_real_record_pair']
        self.assertEqual((first['frame'], later['frame']), (5, 6))
        self.assertEqual(first['historical_progress'], later['historical_progress'])
        self.assertTrue(first['new_confirmation_eligible'])
        self.assertFalse(later['new_confirmation_eligible'])
        self.assertEqual(later['cached_decision'], 'unknown')
        self.assertIsNone(later['cached_confirmation'])

    def test_conditional_current_progress_is_not_new_confirmation(self):
        later = self.result['selected_real_record_pair'][1]
        row = {'frame': later['frame'], 'annotation_used': False, 'evidence_source': 'model_geometry',
               'current_step': later['historical_progress'], 'expected_step': later['next_expected_step'],
               'decision': later['cached_decision'], 'confirmed_step': later['cached_confirmation']}
        self.assertEqual(record_support(row), later)

    def test_synthetic_unknown_even_with_confirmation_field_does_not_authorize(self):
        row = {'frame': 0, 'annotation_used': False, 'evidence_source': 'model_geometry',
               'current_step': 'synthetic-step', 'decision': 'unknown', 'confirmed_step': 'synthetic-step'}
        self.assertFalse(record_support(row)['new_confirmation_eligible'])

    def test_annotation_boundary_cannot_be_silently_upgraded(self):
        with self.assertRaisesRegex(ValueError, 'annotation_boundary'):
            record_support({'annotation_used': True})

    def test_sample_never_claims_independent_physical_truth(self):
        self.assertEqual(self.result['independent_human_truth_count'], 0)
        self.assertIsNone(self.result['recognition_accuracy'])
        self.assertIsNone(self.result['physical_false_revocation_rate'])
        self.assertIn('tombstoned', self.result['split'])


if __name__ == '__main__':
    unittest.main()
