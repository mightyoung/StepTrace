#!/usr/bin/env python3
"""Small standard-library replay of frozen perception events, not a visual model.

Two representations share the input gate, conditional-estimate rule and output
formatting. They are not fully independent algorithms. The FSM
updates state incrementally. The evidence version derives each prefix state
from its append-only observations and confirmation facts. Neither establishes
mechanical truth, continuous identity, or any StepTrace performance advantage.
"""
import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

STATES = {'connected', 'disconnected', 'unknown'}


def empty_revalidation():
    return {'required': False, 'anchor_event_id': None, 'gap_event_id': None,
            'gap_at': None, 'gap_scope': None, 'last_success': None}


def event_time(value):
    return Fraction(value['pts']) * Fraction(value['time_base'])


def validate_revalidation(event, case, lock, anchor):
    """Check explicit input claims; this does not authenticate physical identity."""
    def require(condition, reason):
        if not condition:
            raise ValueError('invalid_revalidation:' + reason)

    require(lock['required'] and anchor is not None, 'no_pending_anchored_gap')
    require(event['identity'] == 'supported' and event['target'] == case['target'],
            'event_identity_or_target')
    require(event['scope'] == anchor['scope'] == lock['gap_scope'], 'relation_scope')
    refs, records = event.get('revalidation'), event.get('evidence_records')
    require(isinstance(refs, dict) and isinstance(records, list), 'missing_evidence')
    require(len(records) == 2 and all(isinstance(r, dict) for r in records),
            'two_evidence_records_required')
    ids = [r.get('evidence_id') for r in records]
    require(all(isinstance(i, str) and i for i in ids) and len(set(ids)) == 2,
            'distinct_record_ids')
    identity_id, state_id = refs.get('identity_evidence_id'), refs.get('state_evidence_id')
    require(isinstance(identity_id, str) and isinstance(state_id, str)
            and identity_id != state_id and {identity_id, state_id} == set(ids),
            'unresolved_or_duplicate_reference')
    by_id = {r['evidence_id']: r for r in records}
    identity, state = by_id[identity_id], by_id[state_id]
    namespace = case.get('assembly_scope')
    require(isinstance(namespace, (list, tuple)) and len(namespace) == 3
            and all(isinstance(v, str) and v for v in namespace), 'missing_scope')
    for evidence in (identity, state):
        require(evidence.get('assembly_scope') == list(namespace), 'evidence_scope')
        require(evidence.get('target') == case['target'], 'evidence_target')
        require(evidence.get('scope') == event['scope'], 'evidence_relation_scope')
        require(evidence.get('identity') == 'supported', 'evidence_identity')
        require(evidence.get('gap_event_id') == lock['gap_event_id'], 'gap_reference')
    require(identity.get('kind') == 'cross_gap_identity'
            and identity.get('anchor_event_id') == anchor['event_id'], 'identity_bridge')
    require(state.get('kind') == 'current_state'
            and isinstance(state.get('observation'), str)
            and state.get('observation') in {'connected', 'disconnected'}
            and state.get('observation') == event['observation'], 'current_state')
    try:
        causal = (event_time(anchor['time']) < event_time(lock['gap_at'])
                  < event_time(identity['time']) <= event_time(state['time'])
                  == event_time(event['time']))
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        raise ValueError('invalid_revalidation:evidence_time') from None
    require(causal, 'evidence_causality')
    return {'event_id': event['event_id'], 'identity_evidence_id': identity_id,
            'state_evidence_id': state_id, 'revalidated_at': deepcopy(event['time'])}


def gated_events(case):
    """Common local gate plus persistent identity-gap lock for both methods."""
    previous_time = None
    observations = []
    lock, anchor = empty_revalidation(), None
    for event in case['events']:
        if event.get('event_type') == 'identity_revalidation':
            try:
                valid = (isinstance(event['scope'], str)
                         and event['observation'] in STATES
                         and event['identity'] in {'supported', 'uncertain', 'different'})
                event_time(event['time'])
            except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
                valid = False
            if not valid:
                raise ValueError('invalid_revalidation:event_structure')
        if event['observation'] not in STATES:
            raise ValueError('observation must be connected/disconnected/unknown')
        if event['identity'] not in {'supported', 'uncertain', 'different'}:
            raise ValueError('unsupported identity label')
        time = Fraction(event['time']['pts']) * Fraction(event['time']['time_base'])
        if previous_time is not None and time <= previous_time:
            raise ValueError('events must have strictly increasing observation PTS')
        previous_time = time
        # "supported" is a supplied local correspondence condition, not a
        # guarantee about physical identity throughout an unseen interval.
        gate = event['target'] == case['target'] and event['identity'] == 'supported'
        kind = event.get('event_type', 'observation')
        if kind == 'identity_revalidation':
            success = validate_revalidation(event, case, lock, anchor)
            lock = empty_revalidation()
            lock['last_success'] = success
        elif kind != 'observation':
            raise ValueError('unsupported_event_type:' + str(kind))
        elif 'revalidation' in event or 'evidence_records' in event:
            raise ValueError('invalid_revalidation:recovery_data_on_observation')
        elif event['target'] == case['target'] and event['identity'] != 'supported':
            lock.update(required=True, gap_event_id=event['event_id'],
                        gap_at=deepcopy(event['time']), gap_scope=event['scope'])
        if not lock['required'] and gate:
            anchor = deepcopy(event)
        lock['anchor_event_id'] = anchor['event_id'] if anchor else None
        observations.append({
            'event': deepcopy(event),
            'target_identity_gate': gate,
            'effective_observation': event['observation'] if gate and not lock['required'] else 'unknown',
            'identity_revalidation': deepcopy(lock),
        })
    return observations


def initial_state():
    return {'internal_state': 'unknown', 'current_direct': 'unknown',
            'current_estimate': 'unknown', 'estimate_basis': 'no_input',
            'current_episode_id': None, 'last_confirmed_at': None,
            'last_observation_at': None, 'episode_statuses': {},
            'identity_revalidation_required': False,
            'identity_revalidation': empty_revalidation()}


def estimate(internal, observation):
    if observation['identity_revalidation']['required']:
        return 'unknown', 'identity_revalidation_required'
    if not observation['target_identity_gate']:
        return 'unknown', 'identity_or_target_not_supported'
    direct = observation['effective_observation']
    if direct != 'unknown':
        return direct, 'direct_observation'
    if internal != 'unknown':
        return internal, 'conditional_history'
    return 'unknown', 'no_prior_observation'


def confirmation(event, number):
    return {'episode_id': f'c{number}', 'target': deepcopy(event['target']),
            'confirmed_at': deepcopy(event['time']),
            'source_event_id': event['event_id'],
            'scope': event['scope']}


def result(snapshots, observations, confirmations):
    return {'current': deepcopy(snapshots[-1]) if snapshots else initial_state(),
            'snapshots': snapshots, 'observations': observations,
            'confirmations': confirmations,
            'process_evidence': 'not_assessed_from_state_events'}


def replay_fsm(case):
    """Ordinary reversible FSM with unknown, target gate and full time history."""
    observations = gated_events(case)
    state = initial_state()
    snapshots, confirmations = [], []
    for observed in observations:
        event, direct = observed['event'], observed['effective_observation']
        if direct == 'connected':
            if state['internal_state'] != 'connected':
                fact = confirmation(event, len(confirmations) + 1)
                confirmations.append(fact)
                state['current_episode_id'] = fact['episode_id']
            state['internal_state'] = 'connected'
            state['last_confirmed_at'] = deepcopy(event['time'])
            state['episode_statuses'][state['current_episode_id']] = 'active'
        elif direct == 'disconnected':
            if state['current_episode_id'] is not None:
                state['episode_statuses'][state['current_episode_id']] = 'revoked'
            state['current_episode_id'] = None
            state['internal_state'] = 'disconnected'
        elif state['current_episode_id'] is not None:
            state['episode_statuses'][state['current_episode_id']] = 'suspended'
        # Unknown never silently becomes a directly observed connection.
        state['current_direct'] = direct
        state['current_estimate'], state['estimate_basis'] = estimate(state['internal_state'], observed)
        state['last_observation_at'] = deepcopy(event['time'])
        state['identity_revalidation'] = deepcopy(observed['identity_revalidation'])
        state['identity_revalidation_required'] = observed['identity_revalidation']['required']
        snapshots.append(deepcopy(state))
    return result(snapshots, observations, confirmations)


def replay_evidence(case):
    """Derive applicability from immutable observation prefixes; no extra input."""
    observations = gated_events(case)
    snapshots, final_confirmations = [], []
    for end in range(1, len(observations) + 1):
        prefix = observations[:end]
        decisive = [(i, o) for i, o in enumerate(prefix)
                    if o['effective_observation'] != 'unknown']
        internal = decisive[-1][1]['effective_observation'] if decisive else 'unknown'
        joins = [(i, o) for i, o in decisive if o['effective_observation'] == 'connected']
        separations = [i for i, o in decisive if o['effective_observation'] == 'disconnected']
        # A new confirmation episode follows no earlier decisive connection,
        # or follows a decisive separation. Unknown itself does not end it.
        episode_starts = []
        for index, (position, observed) in enumerate(decisive):
            if observed['effective_observation'] == 'connected' and (
                index == 0 or decisive[index - 1][1]['effective_observation'] == 'disconnected'
            ):
                episode_starts.append((position, observed))
        confirmations = [confirmation(o['event'], n + 1)
                         for n, (_, o) in enumerate(episode_starts)]
        last = prefix[-1]
        statuses = {}
        for fact, (position, _) in zip(confirmations, episode_starts):
            if any(separated > position for separated in separations):
                statuses[fact['episode_id']] = 'revoked'
            else:
                statuses[fact['episode_id']] = ('active' if last['effective_observation'] == 'connected'
                                               else 'suspended')
        current_episode = (confirmations[-1]['episode_id']
                           if confirmations and internal == 'connected' else None)
        inferred, basis = estimate(internal, last)
        state = {'internal_state': internal,
                 'current_direct': last['effective_observation'],
                 'current_estimate': inferred, 'estimate_basis': basis,
                 'current_episode_id': current_episode,
                 'last_confirmed_at': deepcopy(joins[-1][1]['event']['time']) if joins else None,
                 'last_observation_at': deepcopy(last['event']['time']),
                 'episode_statuses': statuses,
                 'identity_revalidation_required': last['identity_revalidation']['required'],
                 'identity_revalidation': deepcopy(last['identity_revalidation'])}
        snapshots.append(state)
        final_confirmations = confirmations
    return result(snapshots, observations, final_confirmations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, default=Path(__file__).with_name('frozen_events.v1.json'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = json.loads(args.events.read_text())
    rows = []
    for case in data['fixtures']:
        fsm, evidence = replay_fsm(case), replay_evidence(case)
        rows.append({'case_id': case['case_id'], 'source_kind': case['kind'],
                     'scope_note': case.get('scope_note', 'Synthetic logic fixture only; no visual truth'),
                     'equivalent': fsm == evidence,
                     'ordinary_reversible_fsm': fsm,
                     'evidence_applicability_record': evidence})
    output = {'scope': 'deterministic shared-perception rule replay only',
              'events_sha256': hashlib.sha256(args.events.read_bytes()).hexdigest(),
              'shared_logic': 'input gate, conditional-estimate rule and formatting; not independent algorithms',
              'human_gt': None, 'visual_accuracy': None, 'steptrace_gain': None,
              'case_count': len(rows),
              'AI_reference_case_count': sum(c['kind'] == 'AI_reference' for c in data['fixtures']),
              'synthetic_case_count': sum(c['kind'] == 'synthetic' for c in data['fixtures']),
              'event_instances_per_representation': sum(len(c['events']) for c in data['fixtures']),
              'equivalent_case_count': sum(c['equivalent'] for c in rows), 'cases': rows}
    text = json.dumps(output, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')
    if not all(c['equivalent'] for c in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
