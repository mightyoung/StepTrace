"""Read-only check of the published failed-node arrays and source excerpt."""
from pathlib import Path
import hashlib
import json
import runpy

root = Path(__file__).resolve().parent
old = root.parent / 'task6-failure-comparison-20261021'
comparison = json.loads((root / 'root-comparison.json').read_text())
summary = (root / 'root.summary.txt').read_bytes()
current, removed = runpy.run_path(str(old / 'recompute.py'))['extract'](summary.decode())
baseline = json.loads((old / 'comparison.json').read_text())['baseline']['failed_node_ids']
checks = {
    'source_excerpt_hash': hashlib.sha256(summary).hexdigest() == comparison['summary_sha256'],
    'current_array': current == comparison['current_failed_node_ids'],
    'baseline_array': baseline == comparison['baseline_failed_node_ids'],
    'suffix_record': removed == comparison['removed_exception_suffixes'],
    'new_difference': sorted(set(current) - set(baseline)) == comparison['new_failed_node_ids'],
    'resolved_difference': sorted(set(baseline) - set(current)) == comparison['resolved_failed_node_ids'],
    'equality': (set(current) == set(baseline)) == comparison['equal_failed_node_sets'],
    'junit_node_array': sorted(x['node_id'] for x in comparison['junit_failed_cases']) == current,
}
for row in comparison['junit_failed_cases']:
    # All failed JUnit cases in this frozen root run are module-level functions.
    checks['junit_identity:' + row['node_id']] = (
        row['classname'].replace('.', '/') + '.py::' + row['name'] == row['node_id']
    )
if not all(checks.values()):
    raise ValueError('evidence mismatch: ' + ', '.join(k for k, passed in checks.items() if not passed))
print(json.dumps({'all_checks_passed': True, 'baseline_count': len(baseline),
                  'current_count': len(current), 'new': comparison['new_failed_node_ids'],
                  'resolved': comparison['resolved_failed_node_ids']}))
