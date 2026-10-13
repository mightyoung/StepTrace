"""Pinned AI-record audit. No pixel inference, physical truth or production bridge."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

BASELINE = '955d2bae7f047c5e699caa90ac094b74b9b738e3'
GIT_ROOT = Path(__file__).resolve().parents[2]
ROOT = GIT_ROOT
CACHE = ROOT / 'experiments/real-reappearance-dev-20261010'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _git_blob_bytes(blob_sha):
    result = subprocess.run(['git', 'cat-file', 'blob', blob_sha], cwd=GIT_ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0, 'blob_unavailable:' + blob_sha)
    data = result.stdout
    actual_blob_sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    require(actual_blob_sha == blob_sha, 'blob_object_integrity:' + blob_sha)
    return data


def _read_verified_manifest_bytes(entry, root=ROOT):
    try:
        data = (root / entry['path']).read_bytes()
    except OSError:
        data = b''
    expected_blob_sha = hashlib.sha1(
        b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if (len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256'] or
            expected_blob_sha != entry['git_blob_sha']):
        data = _git_blob_bytes(entry['git_blob_sha'])
    require(len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256'],
            'cache_integrity:' + entry['path'])
    actual_blob_sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    require(actual_blob_sha == entry['git_blob_sha'], 'blob_integrity:' + entry['path'])
    return data


def lossy_projection(record):
    """Deliberately insufficient negative control; never a supervision target."""
    estimate = record['current_estimate']
    return {'relation_state': estimate['relation_state'],
            'active': bool(estimate['active_connection_ids'])}


def adapt(record, reference_events, provenance):
    """Classify claim applicability against frozen AI labels, not physical GT.

    reference_events must come from the separately hash-checked pinned cache.
    A matching caller-supplied provenance string alone authenticates nothing.
    """
    require(provenance == {'origin': 'model_first_answer', 'human_gt': False,
                           'independent_ground_truth': False}, 'provenance_upgrade')
    require(record.get('contract_version') == 'steptrace-connection-probe/0.2',
            'unsupported_contract')
    observations = record['frame_observations']
    require(len(observations) == len(reference_events) and bool(observations),
            'observation_count')
    relation = {'connected': 'external_connection_supported',
                'disconnected': 'separation_supported', 'unknown': 'occluded_or_unknown'}
    rows = []
    for observed, event in zip(observations, reference_events):
        source = event['source']
        require(observed['frame_id'] == source['frame_id'] and
                observed['relation_state'] == source['reported_relation_state'] and
                observed['identity_status'] == source['reported_identity_status'],
                'frozen_frame_label_changed')
        require(record['target_ref'] == event['target'], 'target_mismatch')
        require(event['time']['pts'] <= record['timeline']['cutoff_pts'] and
                event['time']['time_base'] == record['timeline']['time_base'],
                'future_or_wrong_timebase')
        rows.append({'frame_id': observed['frame_id'], 'pts': event['time']['pts'],
                     'frame_sha256': source['frame_sha256'],
                     'recorded_relation': observed['relation_state'],
                     'origin': 'AI_model_label', 'human_gt': False})
    last = observations[-1]
    require(record['timeline']['cutoff_pts'] == record['timeline']['last_observed_pts']
            == reference_events[-1]['time']['pts'], 'cutoff_mismatch')
    require(record['last_observation']['frame_id'] == last['frame_id'] and
            record['last_observation']['relation_state'] == last['relation_state'],
            'latest_observation_mismatch')
    require(all(i.get('changes_excluded') is False for i in record['unobserved_intervals']),
            'unobserved_change_exclusion_not_supported')
    estimate = record['current_estimate']
    active = estimate['active_connection_ids']
    history = record['historical_connections']
    require(set(active) <= {h['connection_id'] for h in history}, 'unbound_history')
    require(not set(active) & (set(estimate['revoked_connection_ids']) |
                              set(estimate['suspended_connection_ids'])), 'status_conflict')
    supported = last['relation_state'] == relation['connected']
    identity_supported = record['identity_mapping']['status'] == 'supported_within_visible_sequence'
    if estimate['basis'] == 'direct_current':
        require(estimate['relation_state'] == last['relation_state'] and
                not estimate['assumptions'], 'direct_claim_not_supported')
        label = 'direct-supported-by-record' if supported and identity_supported and active else 'unknown'
    elif estimate['basis'] == 'conditional_history':
        require(last['relation_state'] == 'occluded_or_unknown' and
                bool(estimate['assumptions']) and active and history and
                estimate['relation_state'] == relation['connected'], 'conditional_history_missing_conditions')
        label = 'conditional-history' if identity_supported else 'unknown'
    else:
        label = 'unknown'
    return {'packet_id': record['packet_id'], 'label': label,
            'current_visible_confirmation_eligible': label == 'direct-supported-by-record',
            'direct_relation_by_record': last['relation_state'],
            'basis': estimate['basis'], 'assumptions': deepcopy(estimate['assumptions']),
            'historical_connections': deepcopy(history), 'active_connection_ids': deepcopy(active),
            'unobserved_intervals': deepcopy(record['unobserved_intervals']),
            'frame_labels': rows, 'origin': 'AI_record_derived', 'human_gt': False,
            'mechanical_firmness': 'unknown', 'safety': 'not_evaluated'}


def load_cache():
    manifest = json.loads((CACHE / 'public_manifest.json').read_text())
    verified = []
    verified_bytes = {}
    for entry in manifest['files']:
        verified_bytes[entry['path']] = _read_verified_manifest_bytes(entry, root=ROOT)
        verified.append(entry)
    records = {p: json.loads(verified_bytes[
                   'experiments/real-reappearance-dev-20261010/model_outputs/' + p + '.json'])
               for p in ('q31', 'q68', 'q92')}
    events = {f['case_id']: f['events'] for f in
              json.loads(verified_bytes[
                  'experiments/real-reappearance-dev-20261010/frozen_events.v1.json'])['fixtures']}
    return records, events, verified


PROVENANCE = {'origin': 'model_first_answer', 'human_gt': False, 'independent_ground_truth': False}


def negative_records(records):
    bad = deepcopy(records['q68'])
    bad['current_estimate']['assumptions'] = []
    bad['current_estimate']['basis'] = 'direct_current'
    yield 'conditions_removed_and_current_claim', bad, deepcopy(PROVENANCE)
    bad = deepcopy(records['q92'])
    bad['frame_observations'][1]['relation_state'] = 'external_connection_supported'
    yield 'future_backfill_into_f1746', bad, deepcopy(PROVENANCE)
    bad = deepcopy(records['q68'])
    bad['unobserved_intervals'][0]['changes_excluded'] = True
    yield 'unseen_changes_excluded', bad, deepcopy(PROVENANCE)
    for field in ('human_gt', 'independent_ground_truth'):
        provenance = deepcopy(PROVENANCE)
        provenance[field] = True
        yield 'model_copy_claimed_' + field, deepcopy(records['q31']), provenance


def run():
    records, events, verified = load_cache()
    labels = [adapt(record, events[p], PROVENANCE) for p, record in records.items()]
    frame_index = {}
    for label in labels:
        for frame in label['frame_labels']:
            key = (frame['frame_id'], frame['frame_sha256'])
            if key in frame_index:
                require(frame_index[key] == frame['recorded_relation'], 'cross_packet_label_conflict')
            frame_index[key] = frame['recorded_relation']
    require(len(frame_index) == 3, 'unique_frame_count')
    negatives = []
    for name, bad, provenance in negative_records(records):
        try:
            adapt(bad, events[bad['packet_id']], provenance)
        except ValueError as exc:
            negatives.append({'case': name, 'kind': 'synthetic_code_negative',
                              'guard_status': 'PASS_rejected', 'reason': str(exc)})
        else:
            raise AssertionError('negative accepted: ' + name)
    return {'schema_version': 'claim-applicability-audit/1', 'source_commit': BASELINE,
            'cache_manifest_sha256': hashlib.sha256((CACHE / 'public_manifest.json').read_bytes()).hexdigest(),
            'verified_manifest_entries': len(verified), 'labels': labels,
            'negative_control': {'status': 'RED_expected_projection_collision',
                                 'q31': lossy_projection(records['q31']),
                                 'q68': lossy_projection(records['q68']),
                                 'collision': lossy_projection(records['q31']) == lossy_projection(records['q68']),
                                 'interpretation': 'lossy projection cannot supervise current visible confirmation; no production bug established'},
            'synthetic_negatives': negatives, 'source_events': 1, 'prefixes': 3,
            'unique_frames': len(frame_index), 'repeated_observation_rows': 6,
            'physical_held_negative_count': 0, 'physical_false_revocation_rate': None,
            'mechanism_gain': None, 'new_model_calls': 0, 'new_visual_samples': 0,
            'cross_round_memory_experiment': False, 'pixel_truth_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(), ensure_ascii=False, indent=2) + '\n')
