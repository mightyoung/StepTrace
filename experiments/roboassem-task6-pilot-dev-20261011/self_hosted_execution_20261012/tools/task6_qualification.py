#!/usr/bin/env python3
"""Offline metadata review and frozen-schema validation, never execution authority.

Reads only explicitly named JSON records plus pinned public package JSON.
Does not dereference evidence references, open media/weights, invoke a backend,
or access network. Complete metadata is still an unverified operator claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from task6_bundle import validate_rights_attestation

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SHA256 = 'cc4be2b00dd7a1c259e678cb223170c1b79a1d0e13a7ef0c5987d9c57689c45f'
STAGES = ('build', 'runtime', 'gpu_no_generation', 'synthetic_schema_generation')
PLACEHOLDERS = {'', 'unset', 'unknown', 'self-hosted', 'self hosted', '自托管'}


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError('non-finite JSON constant: ' + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def pinned_json(path, digest):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('pinned public JSON digest mismatch: ' + path.name)
    return strict_json(raw)


def manifest():
    return pinned_json(ROOT / 'experiment-manifest.json', MANIFEST_SHA256)


def base_report():
    return {'execution_ready': False, 'backend_qualification': 'not_independently_verified',
            'rights_verification': 'not_independently_verified',
            'evidence_files_verified': False, 'network_calls': 0, 'generation_calls': 0}


def named(value):
    return isinstance(value, str) and value.strip().casefold() not in PLACEHOLDERS


def digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def audit(packet):
    """Check a qualification packet's metadata; never attest evidence authenticity."""
    spec = manifest()
    issues = []
    packet = packet if isinstance(packet, dict) else {}
    for key, value in {
        'schema_version': 'steptrace.task6.qualification-packet.v1',
        'experiment_id': spec['experiment_id'],
        'manifest_sha256': MANIFEST_SHA256,
        'input_contract_sha256': spec['input_contract']['sha256'],
    }.items():
        if packet.get(key) != value:
            issues.append(key + ' differs from pinned contract')

    def obj(parent, key):
        value = parent.get(key)
        return value if isinstance(value, dict) else {}

    receiver = obj(packet, 'receiver')
    if receiver.get('kind') not in ('self_managed', 'cloud_host'):
        issues.append('receiver.kind must be self_managed or cloud_host for this loopback backend')
    for key in ('provider_id', 'host_id', 'controller', 'terms_evidence_reference',
                'retention_policy', 'training_use_policy', 'deletion_policy'):
        if not named(receiver.get(key)):
            issues.append('receiver.' + key + ' requires a concrete private record')
    rights = obj(packet, 'rights')
    for key in ('media_scope_evidence_reference', 'derivatives_scope_evidence_reference'):
        if not named(rights.get(key)):
            issues.append('rights.' + key + ' is required; model licensing is insufficient')
    for issue in validate_rights_attestation(
        rights.get('attestation'), experiment_id=spec['experiment_id'],
        input_contract_sha256=spec['input_contract']['sha256'],
        provider_id=receiver.get('provider_id'),
    ):
        issues.append('rights.attestation.' + issue)

    backend = obj(packet, 'backend')
    for key, expected in {
        'commit': spec['backend']['commit'], 'model_revision': spec['model']['revision'],
        'model_files': spec['model']['files'], 'build_options': spec['backend']['build'],
        'server_arguments': spec['backend']['server_arguments'],
    }.items():
        # Canonical JSON comparison also distinguishes true from numeric 1.
        if json.dumps(backend.get(key), sort_keys=True) != json.dumps(expected, sort_keys=True):
            issues.append('backend.' + key + ' differs from pinned manifest')
    stages = obj(packet, 'stages')
    binary = obj(stages, 'build').get('binary_sha256')
    for name in STAGES:
        stage = obj(stages, name)
        prefix = 'stages.' + name + '.'
        if stage.get('state') != 'completed':
            issues.append(prefix + 'state is not completed; dispatch/not_run is not execution')
        if type(stage.get('exit_code')) is not int or stage['exit_code'] != 0:
            issues.append(prefix + 'exit_code must be integer zero')
        if not named(stage.get('evidence_reference')):
            issues.append(prefix + 'evidence_reference is missing')
        if not digest(stage.get('evidence_sha256')):
            issues.append(prefix + 'evidence_sha256 is missing or malformed')
        if not digest(stage.get('binary_sha256')) or stage.get('binary_sha256') != binary:
            issues.append(prefix + 'binary_sha256 must bind the same build across all stages')
        for key in ('provider_id', 'host_id'):
            if not named(stage.get(key)) or stage.get(key) != receiver.get(key):
                issues.append(prefix + key + ' must bind the declared receiver')
    probe = obj(stages, 'synthetic_schema_generation')
    for key, expected in {
        'input_kind': 'synthetic_text_only', 'generation_calls': 2, 'retries': 0,
        'private_media_used': False, 'schema_arms': ['F', 'HF'],
        'full_responses_schema_validated': True, 'server_stopped': True,
    }.items():
        if type(probe.get(key)) is not type(expected) or probe.get(key) != expected:
            issues.append('stages.synthetic_schema_generation.' + key + ' does not match the bounded probe protocol')
    return {**base_report(), 'metadata_complete': not issues,
            'status': 'metadata_complete_pending_independent_review' if not issues else 'metadata_incomplete',
            'issues': issues}


def check_content(arm, content):
    """Use the exact runner contract's schema, not the alternative offline v2 profile."""
    spec = manifest()
    contract = pinned_json(ROOT / 'execution-input-contract.json', spec['input_contract']['sha256'])
    issues = []
    if arm not in ('F', 'HF'):
        issues.append('arm must be F or HF')
    else:
        schema = contract['content_schemas'][arm]
        Draft202012Validator.check_schema(schema)
        for error in Draft202012Validator(schema).iter_errors(content):
            # Do not echo private payloads or free text into the audit report.
            path = '/'.join(str(x) for x in error.absolute_path)
            issues.append(f'content/{path}: schema keyword {error.validator} failed')
    return {**base_report(), 'schema_valid': not issues, 'issues': issues,
            'classification': 'offline JSON schema check; not backend generation or visual truth'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('audit').add_argument('--packet', type=Path, required=True)
    check = sub.add_parser('schema-check')
    check.add_argument('--arm', choices=('F', 'HF'), required=True)
    check.add_argument('--content', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'audit':
            report = audit(strict_json(args.packet.read_text(encoding='utf-8')))
            passed = report['metadata_complete']
        else:
            report = check_content(args.arm, strict_json(args.content.read_text(encoding='utf-8')))
            passed = report['schema_valid']
    except (OSError, ValueError) as exc:
        # No payload/path echo; JSON syntax errors may contain private content.
        report = {**base_report(), 'status': 'invalid_input', 'issues': [type(exc).__name__]}
        passed = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
