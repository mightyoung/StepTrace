"""Use existing real detector output; never infer pixels or physical completion."""
import argparse
import hashlib
import json
from pathlib import Path
from collections import Counter


def record_support(row):
    if row.get('annotation_used') is not False:
        raise ValueError('annotation_boundary_not_preserved')
    if row.get('evidence_source') != 'model_geometry':
        raise ValueError('unsupported_source_for_this_cache')
    progress = row.get('current_step')
    confirmation = row.get('confirmed_step')
    if row.get('decision') == 'normal' and confirmation and confirmation == progress:
        label = 'current_confirmation_by_cached_detector'
    elif progress and confirmation is None and row.get('decision') == 'unknown':
        label = 'historical_progress_without_new_confirmation'
    else:
        label = 'no_current_confirmation'
    return {'frame': row['frame'], 'historical_progress': progress,
            'next_expected_step': row.get('expected_step'), 'cached_decision': row['decision'],
            'cached_confirmation': confirmation, 'support_label': label,
            'new_confirmation_eligible': label == 'current_confirmation_by_cached_detector',
            'scope': 'cached_detector_record_only', 'human_gt': False,
            'physical_completion': 'unknown'}


def run(events_path, video_path):
    raw = events_path.read_bytes()
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if [r['frame'] for r in rows] != list(range(len(rows))):
        raise ValueError('non_contiguous_trace')
    labels = [record_support(r) for r in rows]
    # Deterministic selection: first confirmation followed immediately by an
    # unknown row retaining that step. Selection is retrospective development.
    pairs = [(a, b) for a, b in zip(labels, labels[1:])
             if a['new_confirmation_eligible'] and
             b['support_label'] == 'historical_progress_without_new_confirmation' and
             a['historical_progress'] == b['historical_progress']]
    if not pairs:
        raise ValueError('requested_counterexample_not_present')
    first = pairs[0]
    import cv2
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise ValueError('video_decode_unavailable')
        width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        decoded = []
        for frame in (0, first[0]['frame'], first[1]['frame'], count//2):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame)
            ok, image = capture.read()
            if not ok:
                raise ValueError('selected_frame_decode_failed')
            decoded.append({'frame': frame, 'decoded': True,
                            'shape': list(image.shape), 'decoded_bgr_sha256': hashlib.sha256(image.tobytes()).hexdigest()})
    finally:
        capture.release()
    if count != len(rows):
        raise ValueError('trace_video_frame_count_mismatch')
    video_hash = hashlib.sha256(video_path.read_bytes()).hexdigest()
    if video_hash != '8d79d430ba1f30305ba4c62e489bc50e46b6385b38c689698a256076321b5d8b':
        raise ValueError('archived_AGENTS_video_hash_mismatch')
    return {'schema_version': 'recovered-real-progress-record-audit/1',
            'scope': 'real_video_cached_detector_records; no new perception or independent GT',
            'source_event_trace_sha256': hashlib.sha256(raw).hexdigest(),
            'source_video_sha256': video_hash, 'source_video_bytes': video_path.stat().st_size,
            'video': {'width': width, 'height': height, 'fps': fps, 'frames': count, 'decode_checks': decoded},
            'split': 'development_exposed_MECCANO_0009_tombstoned_for_formal_test',
            'selection': 'first consecutive confirmation/unknown-retained-progress pair in saved trace; retrospective',
            'selected_real_record_pair': list(first),
            'selected_source_record_canonical_sha256': [hashlib.sha256(
                json.dumps(rows[label['frame']], sort_keys=True, ensure_ascii=False,
                           separators=(',', ':')).encode('utf-8')).hexdigest() for label in first],
            'lossy_control': {'projection': 'current_step_only', 'same_projected_value': first[0]['historical_progress'],
                             'collision': first[0]['historical_progress'] == first[1]['historical_progress'],
                             'status': 'RED_expected_collision_if_misused_as_new_confirmation'},
            'decision_counts': dict(Counter(r['decision'] for r in rows)),
            'support_label_counts': dict(Counter(r['support_label'] for r in labels)),
            'new_confirmation_rows': sum(r['new_confirmation_eligible'] for r in labels),
            'production_bug_established': False, 'independent_human_truth_count': 0,
            'physical_false_revocation_rate': None, 'recognition_accuracy': None,
            'mechanism_gain': None, 'new_model_calls': 0, 'new_training_runs': 0,
            'future_frames_used_for_pair_selection_only': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(args.events, args.video), ensure_ascii=False, indent=2)+'\n')
