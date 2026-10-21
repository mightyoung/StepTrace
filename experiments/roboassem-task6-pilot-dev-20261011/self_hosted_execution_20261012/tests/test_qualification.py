"""Metadata and synthetic JSON only; never load media, weights or a backend."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'tools'))


class QualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.find_spec('task6_qualification')
        assert spec is not None, 'Missing offline Task6 qualification checker'
        import task6_qualification
        cls.tool = task6_qualification

    def packet(self):
        manifest = self.tool.manifest()
        receiver = 'synthetic-provider/host-01'
        packet = {
            'schema_version': 'steptrace.task6.qualification-packet.v1',
            'experiment_id': manifest['experiment_id'],
            'manifest_sha256': self.tool.MANIFEST_SHA256,
            'input_contract_sha256': manifest['input_contract']['sha256'],
            'receiver': {
                'kind': 'self_managed', 'provider_id': receiver,
                'host_id': 'synthetic-host', 'controller': 'synthetic-controller',
                'terms_evidence_reference': 'synthetic-evidence',
                'retention_policy': 'synthetic-retention',
                'training_use_policy': 'synthetic-no-training',
                'deletion_policy': 'synthetic-deletion',
            },
            'rights': {
                'media_scope_evidence_reference': 'synthetic-video-scope',
                'derivatives_scope_evidence_reference': 'synthetic-derivative-scope',
                'attestation': {
                    'schema_version': 'steptrace.data-rights-attestation.v1',
                    'experiment_id': manifest['experiment_id'],
                    'input_contract_sha256': manifest['input_contract']['sha256'],
                    'authorization_status': 'authorized',
                    'rights_basis': 'synthetic only, no legal authorization',
                    'license_id': 'synthetic-only', 'evidence_reference': 'synthetic',
                    'evidence_file': 'synthetic.txt', 'evidence_bytes': 1,
                    'evidence_sha256': 'a'*64, 'inference_authorized': True,
                    'transfer_authorized': True, 'provider_id': receiver,
                    'expires_utc': '2999-01-01T00:00:00Z',
                    'public_redistribution_authorized': False,
                    'synthetic_template_only': False,
                },
            },
            'backend': {
                'commit': manifest['backend']['commit'],
                'model_revision': manifest['model']['revision'],
                'model_files': copy.deepcopy(manifest['model']['files']),
                'build_options': copy.deepcopy(manifest['backend']['build']),
                'server_arguments': copy.deepcopy(manifest['backend']['server_arguments']),
            },
            'stages': {name: {
                'state': 'completed', 'exit_code': 0,
                'evidence_reference': 'synthetic-evidence-' + name,
                'evidence_sha256': 'b'*64, 'binary_sha256': 'c'*64,
                'provider_id': receiver, 'host_id': 'synthetic-host',
            } for name in self.tool.STAGES},
        }

        packet['stages']['synthetic_schema_generation'].update({
            'input_kind': 'synthetic_text_only', 'generation_calls': 2, 'retries': 0,
            'private_media_used': False, 'schema_arms': ['F', 'HF'],
            'full_responses_schema_validated': True, 'server_stopped': True,
        })
        return packet

    def test_probe_cannot_claim_qualification_from_no_calls_or_private_media(self):
        for field, value in (('input_kind', 'private_video'), ('generation_calls', 0),
                             ('generation_calls', 3), ('generation_calls', True),
                             ('retries', 1), ('retries', False), ('private_media_used', True),
                             ('schema_arms', ['F']), ('full_responses_schema_validated', False),
                             ('server_stopped', False)):
            with self.subTest(field=field, value=value):
                packet = self.packet()
                packet['stages']['synthetic_schema_generation'][field] = value
                self.assertFalse(self.tool.audit(packet)['metadata_complete'])

    def test_complete_synthetic_metadata_never_grants_execution(self):
        report = self.tool.audit(self.packet())
        self.assertEqual(report['issues'], [])
        self.assertTrue(report['metadata_complete'])
        self.assertFalse(report['execution_ready'])
        self.assertEqual(report['backend_qualification'], 'not_independently_verified')
        self.assertEqual(report['network_calls'], 0)
        self.assertEqual(report['generation_calls'], 0)

    def test_missing_packet_reports_specific_fields(self):
        report = self.tool.audit({})
        self.assertFalse(report['metadata_complete'])
        for field in ('receiver.host_id', 'rights.attestation', 'backend.commit', 'stages.build'):
            self.assertTrue(any(field in x for x in report['issues']), field)

    def test_receiver_and_rights_binding_and_scope_are_required(self):
        for section, field, value in (
            ('receiver', 'provider_id', 'UNSET'),
            ('receiver', 'host_id', 'unknown'),
            ('receiver', 'kind', 'external_api'),
            ('receiver', 'training_use_policy', ''),
            ('rights', 'media_scope_evidence_reference', ''),
            ('rights', 'derivatives_scope_evidence_reference', ''),
        ):
            with self.subTest(field=field):
                packet = self.packet()
                packet[section][field] = value
                self.assertFalse(self.tool.audit(packet)['metadata_complete'])
        packet = self.packet()
        packet['rights']['attestation']['provider_id'] = 'another-receiver'
        self.assertTrue(any('rights.attestation.provider_id' in x for x in self.tool.audit(packet)['issues']))

    def test_backend_drift_is_rejected(self):
        for field in ('commit', 'model_revision', 'model_files', 'build_options', 'server_arguments'):
            with self.subTest(field=field):
                packet = self.packet()
                packet['backend'][field] = None
                self.assertTrue(any('backend.'+field in x for x in self.tool.audit(packet)['issues']))

    def test_missing_failed_or_unbound_stage_never_counts_as_completed(self):
        for field, value in (('state', 'not_run'), ('exit_code', 1),
                             ('exit_code', False), ('evidence_reference', ''),
                             ('evidence_sha256', 'bad'), ('host_id', 'other-host'),
                             ('binary_sha256', 'd'*64), ('provider_id', 'other-provider')):
            with self.subTest(field=field):
                packet = self.packet()
                packet['stages']['synthetic_schema_generation'][field] = value
                self.assertFalse(self.tool.audit(packet)['metadata_complete'])

    def content(self, arm):
        return {
            'schema_version': '1', 'condition': arm,
            'authority': 'development_state_estimation_only',
            'state_write_allowed': False, 'identity_continuity': 'not_observed',
            'current_relations': [{
                'part_id': 'white_u_part', 'seat_id': seat, 'appearance': 'unknown',
                'evidence_input_ids': ['k04'], 'reason': 'synthetic only',
            } for seat in ('blue_seat_left', 'blue_seat_right')],
            'historical_events': [], 'history_current_applicability': 'synthetic unknown',
            'assumptions': [], 'SOP_completion': 'unknown', 'mechanical_lock': 'unknown',
            'force_state': 'unknown', 'limitations': ['synthetic; not model output'],
        }

    def test_synthetic_f_hf_schema_valid_does_not_verify_backend(self):
        for arm in ('F', 'HF'):
            report = self.tool.check_content(arm, self.content(arm))
            self.assertTrue(report['schema_valid'], report['issues'])
            self.assertFalse(report['execution_ready'])
            self.assertEqual(report['backend_qualification'], 'not_independently_verified')

    def test_schema_counterexamples_preserve_unknown_and_authority(self):
        for arm in ('F', 'HF'):
            for field, bad in (('condition', 'H'), ('state_write_allowed', True),
                               ('SOP_completion', 'completed'), ('mechanical_lock', 'locked'),
                               ('force_state', 'safe'), ('unexpected', True)):
                with self.subTest(arm=arm, field=field):
                    payload = self.content(arm)
                    payload[field] = bad
                    self.assertFalse(self.tool.check_content(arm, payload)['schema_valid'])

    def test_current_evidence_cannot_be_past_and_f_cannot_contain_history(self):
        for arm in ('F', 'HF'):
            payload = self.content(arm)
            payload['current_relations'][0]['evidence_input_ids'] = ['k01']
            self.assertFalse(self.tool.check_content(arm, payload)['schema_valid'])
        payload = self.content('F')
        payload['historical_events'] = [{'part_id': 'white_u_part', 'seat_id': 'unknown',
            'event': 'unknown', 'evidence_input_ids': ['k01'], 'uncertainty': 'synthetic'}]
        self.assertFalse(self.tool.check_content('F', payload)['schema_valid'])

    def test_duplicate_seats_null_and_wrong_arm_are_rejected(self):
        payload = self.content('HF')
        payload['current_relations'][1]['seat_id'] = 'blue_seat_left'
        self.assertFalse(self.tool.check_content('HF', payload)['schema_valid'])
        self.assertFalse(self.tool.check_content('F', None)['schema_valid'])
        self.assertFalse(self.tool.check_content('H', {})['schema_valid'])

    def test_non_objects_are_reported_without_crashing(self):
        for value in (None, [], True, 'synthetic'):
            self.assertFalse(self.tool.audit(value)['metadata_complete'])
        packet = self.packet()
        packet['receiver'] = []
        packet['rights'] = None
        packet['stages'] = []
        self.assertFalse(self.tool.audit(packet)['metadata_complete'])

    def test_json_loader_rejects_duplicates_and_nonfinite_constants(self):
        for raw in ('{"host_id":"a","host_id":"b"}', '{"exit_code":NaN}'):
            with self.assertRaises(ValueError):
                self.tool.strict_json(raw)

    def test_unknown_template_stays_blocked(self):
        template = json.loads((PACKAGE / 'qualification-packet.template.json').read_text())
        report = self.tool.audit(template)
        self.assertFalse(report['metadata_complete'])
        self.assertFalse(report['execution_ready'])
