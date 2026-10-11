"""Isolated record-use boundary; not wired into StepTrace runtime or training."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / 'experiments/claim-applicability-dev-20261011/audit.py'
spec = importlib.util.spec_from_file_location('pinned_claim_audit', AUDIT_PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sys.path.insert(0, str(ROOT / 'src'))


def evidence_envelope(packet_id):
    records, events, _ = audit.load_cache()
    record = records[packet_id]
    label = audit.adapt(record, events[packet_id], audit.PROVENANCE)
    return {'schema_version': 'isolated-record-envelope/1',
            'record': deepcopy(label),
            'current_observation': deepcopy(record['last_observation']),
            'conditional_estimate': deepcopy(record['current_estimate']),
            'stage_mapping': None, 'completion_truth': None, 'training_ground_truth': None,
            'scope': 'external_relation_only; AI_record_consistency',
            'production_wired': False}


def require_use(envelope, intended_use):
    """Audit is possible; current visibility needs direct support; stage/GT absent."""
    audit.require(envelope.get('schema_version') == 'isolated-record-envelope/1', 'wrong_envelope')
    # This adapter transports only the pinned three-record cache. Rebind the
    # entire envelope, rather than trusting a caller-modifiable eligibility bit.
    audit.require(envelope == evidence_envelope(envelope['record']['packet_id']),
                  'transport_changed_pinned_evidence')
    if intended_use == 'cache_record_audit':
        return deepcopy(envelope)
    if intended_use == 'current_visible_record_support':
        audit.require(envelope['record']['current_visible_confirmation_eligible'] is True,
                      'current_visible_support_unavailable')
        return deepcopy(envelope)
    if intended_use in {'stage_scorer', 'completion_decision', 'training_ground_truth'}:
        raise ValueError('no_authorized_stage_mapping_or_independent_truth')
    raise ValueError('unsupported_use')


def exercise_real_consumers():
    """Exercise production code with AI cache + explicitly synthetic routing inputs.

    Synthetic stage/opportunity names exist only to reach parser branches. They
    are never assigned to the real recording, used as truth, or scored.
    """
    import tempfile
    from display_project.vlm_sop.state_risk_contract import validate_inference, StateRiskContractError
    from display_project.vlm_sop.state_memory import StateMemory
    from display_project.metrics.state_risk import predictions_from_run_dir
    from display_project.experiments.g1_derivation import derive_inputs
    records, _, _ = audit.load_cache()
    rows = []
    for packet, record in records.items():
        row = {'packet_id': packet, 'input_origin': 'cached_AI_first_answer',
               'routing_fixture': 'synthetic_software_only_no_visual_truth'}
        try:
            validate_inference(record, knowledge={}, window={})
        except StateRiskContractError:
            row['production_stage_contract'] = 'rejected_incompatible_schema'
        else:
            raise AssertionError('raw probe unexpectedly accepted as stage inference')
        memory = StateMemory('synthetic-task')
        try:
            memory.append_estimate(record, model_sha256='b' * 64)
        except ValueError:
            row['production_state_memory'] = 'rejected_incompatible_schema'
        else:
            raise AssertionError('raw probe unexpectedly accepted into stage history')
        audit.require(memory.current_estimate is None and not memory.estimate_history,
                      'rejected_input_wrote_stage_history')
        with tempfile.TemporaryDirectory(prefix='steptrace-consumer-') as directory:
            folder = Path(directory)
            truth = {'schema_version': '1.0', 'evaluation_id': 'synthetic-routing-only',
                     'no_event_category_policy': 'exclude_empty_categories_from_macro_f1',
                     'matching_tolerance_s': 1.0, 'enabled_risk_ids': ['synthetic-risk'],
                     'jobs': [{'job_id': 'synthetic-job', 'task_id': 'synthetic-task',
                               'video_id': 'synthetic-video', 'duration_s': 10.0}],
                     'opportunities': [{'opportunity_id': 'synthetic-opportunity',
                                        'job_id': 'synthetic-job', 'timestamp_s': 1.0,
                                        'stage_id': 'synthetic-stage', 'completion_state': 'unknown'}],
                     'risk_events': [], 'transitions': []}
            (folder / 'manifest.json').write_text(json.dumps({'terminal': {
                'method_id': 'synthetic-routing', 'source_sha256': 'a' * 64, 'status': 'completed'}}))
            (folder / 'estimates.jsonl').write_text(json.dumps({
                'decision_time': 1.0, 'available_time': 1.0, 'status': 'ok', 'inference': record}) + '\n')
            predictions, _ = predictions_from_run_dir(truth, folder,
                job_id='synthetic-job', method_id='synthetic-routing')
            opportunity = predictions['opportunities'][0]
            audit.require(opportunity['status'] == 'unknown' and
                          'estimated_stage' not in opportunity and 'completion_state' not in opportunity,
                          'raw_probe_upgraded_by_scorer_adapter')
            row['production_run_export_adapter'] = 'unknown_no_stage_or_completion_exported'
            # Minimal synthetic manifests/CSV satisfy upstream structure only;
            # original cached JSON remains the trace row, unmodified.
            (folder / 'training-manifest.json').write_text(json.dumps({'records': [
                {'video_id': '0007', 'project_split': 'CALIBRATION', 'group_hash': 'a' * 64},
                {'video_id': '0001', 'project_split': 'DEVELOPMENT', 'group_hash': 'b' * 64}]}))
            (folder / 'actions.csv').write_text('video_id,action_id,action_name,start_frame,end_frame\n'
                                               '0007,1,synthetic-pick,2,4\n0001,1,synthetic-pick,2,4\n')
            (folder / 'mapping.json').write_text(json.dumps({'schema_version': 1,
                'task_id': 'synthetic-task', 'states': ['synthetic-state'],
                'action_to_state': {'synthetic-pick': 'synthetic-state'}, 'proxy_labels': {}}))
            (folder / 'trace.jsonl').write_text(json.dumps(record) + '\n')
            try:
                derive_inputs(folder / 'training-manifest.json', folder / 'actions.csv',
                              folder / 'trace.jsonl', action_state_map=folder / 'mapping.json',
                              code_hash='synthetic-code', model_hash='synthetic-model', config_hash='synthetic-config')
            except ValueError as exc:
                audit.require('trace line 1 schema mismatch' in str(exc), 'unexpected_training_failure')
                row['production_G1_training_derivation'] = 'rejected_trace_schema_no_training_labels'
            else:
                raise AssertionError('raw probe unexpectedly generated training inputs')
        rows.append(row)
    return rows


def run():
    records, _, _ = audit.load_cache()
    envelopes = [evidence_envelope(p) for p in records]
    decisions = []
    for envelope in envelopes:
        for use in ('cache_record_audit', 'current_visible_record_support', 'stage_scorer',
                    'completion_decision', 'training_ground_truth'):
            try:
                require_use(envelope, use)
                outcome = 'allowed_AI_record_scope_only'
            except ValueError as exc:
                outcome = 'rejected:' + str(exc)
            decisions.append({'packet_id': envelope['record']['packet_id'], 'use': use, 'outcome': outcome})
    return {'source_commit': audit.BASELINE,
            'implementation_parent': '0a6aa25529ec169156b9ad979d03a13754e0a874',
            'actual_consumers': exercise_real_consumers(), 'isolated_envelopes': envelopes,
            'use_decisions': decisions,
            'lossy_negative_control': {'q31': audit.lossy_projection(records['q31']),
                                     'q68': audit.lossy_projection(records['q68']),
                                     'status': 'RED_expected_collision'},
            'physical_truth': 'unknown', 'physical_false_revocation_rate': None,
            'new_model_calls': 0, 'new_training_runs': 0, 'new_visual_samples': 0,
            'algorithm_gain': None, 'stage_accuracy_measured': False}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(), ensure_ascii=False, indent=2) + '\n')
