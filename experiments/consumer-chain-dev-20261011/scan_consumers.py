"""Reproduce bounded static inventory; absence of tokens is not a runtime proof."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKENS = ('connection_probe', 'steptrace-connection-probe', 'active_connection_ids',
          'historical_connections', 'conditional_history', 'current_estimate')
READS = {
    'src/display_project/vlm_sop/state_risk_engine.py': [(212, 252)],
    'src/display_project/vlm_sop/state_memory.py': [(56, 62), (82, 125)],
    'src/display_project/vlm_sop/state_risk_contract.py': [(88, 126)],
    'scripts/run_vlm_state_risk.py': [(185, 221)],
    'src/display_project/metrics/state_risk.py': [(396, 430), (448, 466)],
    'src/display_project/experiments/g1_derivation.py': [(166, 190), (228, 236), (275, 326)],
    'src/display_project/data/yolo_training_views.py': [(140, 168)],
    'src/display_project/sop/state_machine.py': [(82, 109), (152, 181)],
    'src/display_project/experiments/adapters.py': [(250, 295)],
    'src/display_project/metrics/_paired_freeze_core.py': [(278, 310)],
}


def scan():
    scanned, hits = [], {token: [] for token in TOKENS}
    for scope in ('src', 'scripts', 'configs', 'tests'):
        for path in sorted((ROOT / scope).rglob('*')):
            if not path.is_file() or path.suffix not in {'.py', '.json', '.yaml', '.yml'}:
                continue
            raw = path.read_bytes()
            scanned.append({'path': str(path.relative_to(ROOT)),
                            'sha256': hashlib.sha256(raw).hexdigest()})
            for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
                for token in TOKENS:
                    if token in line:
                        hits[token].append({'path': str(path.relative_to(ROOT)), 'line': number,
                                            'text': line.strip()})
    reads = []
    for relative, ranges in READS.items():
        path = ROOT / relative
        raw = path.read_bytes()
        lines = raw.decode('utf-8').splitlines()
        reads.append({'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(),
                      'snippets': [{'first_line': a, 'last_line': b,
                                    'text': '\n'.join(lines[a-1:b])} for a, b in ranges]})
    return {'source_commit': '0a6aa25529ec169156b9ad979d03a13754e0a874',
            'scope': 'src/scripts/configs/tests textual py/json/yaml files',
            'scanned_file_count': len(scanned), 'scanned_files': scanned, 'token_hits': hits,
            'manually_read_consumers': reads,
            'finding': 'no connection-probe conversion found in published source; current_estimate name in StateMemory is a separate stage-inference container',
            'unverified': ['private or untracked glue', 'external pipelines', 'runtime visual truth',
                           'end-to-end video execution', 'training execution']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(scan(), ensure_ascii=False, indent=2) + '\n')
