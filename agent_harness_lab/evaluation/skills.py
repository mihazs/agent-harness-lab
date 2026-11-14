"""Keep injection, observable activation and independently graded utility separate."""
import hashlib
import json
import os
import stat
from pathlib import Path


def activation(*, injected=False, signal=None, provenance=None, evidence_ref=None):
    explicit=signal in ('read','invocation') and provenance=='native_field' and bool(evidence_ref)
    return dict(available=True if injected or explicit else None,activated=True if explicit else None,
                activation_evidence=evidence_ref if explicit else None,
                missing_reason=None if explicit else 'runtime_activation_unobserved')


def skill_availability(route, name, files):
    """Availability provenance for one staged skill, whatever route delivered it.

    files maps a path inside the bundle to its SHA-256; None marks the entry point as removed. Identical
    staged content gives equal hashes through the `skills` and `harness_paths` routes, so only `route`
    differs. This is placement evidence: it never says the skill was discovered, read or used."""
    entry = files.get('SKILL.md')
    if not entry:
        return dict(name=name, route=route, state='absent', entry_sha256=None, bundle_sha256=None, activated=None)
    bundle = json.dumps(sorted(files.items()), separators=(',', ':')).encode()
    return dict(name=name, route=route, state='present', entry_sha256=entry,
                bundle_sha256=hashlib.sha256(bundle).hexdigest(), activated=None)


SHADOWING_SCHEMA = 'skill-shadowing-v1'
UNREADABLE = 'unreadable'  # a same-name bundle exists but its content could not be read in full
MAX_AMBIENT_FILES = 512
MAX_AMBIENT_BYTES = 8 * 1024 * 1024


def _kind(path):
    """'dir', 'file', 'other', False (absent) or None (could not be determined).

    os.stat is used directly: Path.exists, is_dir and is_file swallow every OSError on newer Pythons and raise it on
    older ones, so a permission error would read as absent on one and as an error on the other."""
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None
    return 'dir' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'other'


def _ambient_bundle(root, name, max_files, max_bytes):
    """A bundle hash, UNREADABLE, None (existence undetermined) or False (no same-name skill) for one name."""
    if name in ('', '.', '..') or Path(name).name != name:
        return None
    folder = root / name
    for path, wanted in ((folder, 'dir'), (folder / 'SKILL.md', 'file')):
        kind = _kind(path)
        if kind is None:
            return None
        if kind != wanted:
            return False
    try:
        files, size = {}, 0
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                size += path.stat().st_size
                if len(files) >= max_files or size > max_bytes:
                    return UNREADABLE
                files[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return UNREADABLE
    return skill_availability('ambient', name, files)['bundle_sha256']


def ambient_skill_copies(roots, names, *, max_files=MAX_AMBIENT_FILES, max_bytes=MAX_AMBIENT_BYTES):
    """Read-only look for same-name bundles in user-level skill folders; it never lists a folder or returns other skills.

    roots maps a layer label to a skills folder. Each layer gives {name: bundle sha256 | UNREADABLE | None} for the
    names whose folder holds a SKILL.md (None: existence could not be determined, for example a permission error),
    or is None when the folder is not a directory or could not be inspected. A missing folder is a scanned layer with
    no copies. A bundle over max_files files or max_bytes bytes is UNREADABLE rather than truncated."""
    found = {}
    for layer, root in roots.items():
        root = Path(root)
        kind = _kind(root)
        if kind is False:
            found[layer] = {}
        elif kind != 'dir':
            found[layer] = None
        else:
            found[layer] = {name: copy for name in names if (copy := _ambient_bundle(root, name, max_files, max_bytes)) is not False}
    return found


def skill_shadowing(availability, ambient):
    """Same-name copies of each staged skill in the scanned user-level layers, from ambient_skill_copies.

    The row says a candidate shadow exists, never which copy the runtime used: effective_layer stays unknown. An
    ambient copy beside an absent (removed) skill means the removal arm still has the skill. `none_found` covers
    only layers_scanned (managed, plugin and enterprise layers are not scanned); an unscanned layer, an unreadable
    folder or an undetermined name is unknown, never none_found."""
    rows = []
    for row in availability:
        name, found, scanned, open_question = row['name'], [], [], False
        for layer, copies in (ambient or {}).items():
            if copies is None or (name in copies and copies[name] is None):
                open_question = True
                continue
            scanned.append(layer)
            if name in copies:
                digest = None if copies[name] == UNREADABLE else copies[name]
                match = None if digest is None or row['bundle_sha256'] is None else digest == row['bundle_sha256']
                found.append(dict(layer=layer, bundle_sha256=digest, bundle_match=match))
        state = ('ambient_same_name' if found else 'none_found' if scanned and not open_question else 'unknown')
        rows.append(dict(name=name, route=row['route'], staged_state=row['state'], state=state, ambient=found,
                         layers_scanned=scanned, effective_layer='unknown'))
    return rows


def shadowing_state(rows):
    """One label per trial: a copy beats unknown, and clear needs every staged skill scanned."""
    states = {row['state'] for row in rows}
    return ('ambient_same_name' if 'ambient_same_name' in states else 'unknown' if not states or 'unknown' in states
            else 'none_found')


ACTIVATION_RECEIPT_PROFILE = 'ahl-skill-activation-receipt-v1'


def activation_receipt(events, *, case_id, skill_id=None):
    """Summarize one case's declared signals, never infer execution from requests.

    Negative evidence requires a complete declared capture, an explicit skill
    target, and at least one captured skill signal. Current native adapters mark
    their capture partial, so they cannot establish non-observation.
    """
    from ..telemetry.claude_native import VERSION as CLAUDE_NATIVE_VERSION
    from ..telemetry.schema import event
    if case_id is not None and (not isinstance(case_id, str) or not case_id):
        raise ValueError('case identity must be nonempty text when supplied')
    if skill_id is not None and (not isinstance(skill_id, str) or not skill_id):
        raise ValueError('skill identity must be nonempty text')
    signals, all_complete, seen_signal = [], True, False
    for raw in events:
        row = event(raw)
        if case_id is None or row['case_id'] != case_id:
            raise ValueError('activation receipt requires one case')
        all_complete &= row.get('capture_complete') is True
        kind, confidence, identity = row.get('skill_signal'), row.get('skill_signal_confidence'), row.get('skill_id')
        source = (row.get('source_format'), row.get('source_version'))
        if (source[0] not in (None, 'normalized') and source != ('claude', CLAUDE_NATIVE_VERSION)):
            continue
        if row['activity'] != 'tool_call' or not identity or (kind, confidence) not in (
                ('declared_invocation', 'declared_name'), ('file_read', 'full_read_request'),
                ('file_read', 'partial_read_request')):
            continue
        seen_signal = True
        if skill_id is None or skill_id == identity:
            signals.append(dict(event_id=row['event_id'], skill_id=identity, signal=kind, confidence=confidence))
    state = ('activated' if signals else 'not_observed' if skill_id is not None and seen_signal and all_complete
             else 'unknown')
    return dict(profile=ACTIVATION_RECEIPT_PROFILE, case_id=case_id, skill_id=skill_id, state=state, signals=signals,
                interpretation='declared_request_not_execution_proof', estimand='skill_availability_intent_to_treat',
                publication_allowed=False)


def attach_activation_receipt(trial, events, *, case_id, skill_id=None):
    """Return a trial with an explicitly attributed, unverified normalized capture.

    The caller owns the trial-to-case linkage; the normalized events cannot prove
    that the runtime emitted them for the supplied trial.
    """
    if not isinstance(trial, dict) or not isinstance(trial.get('task'), str) or not trial['task']:
        raise ValueError('trial with task identity required')
    if not isinstance(trial.get('version'), str) or not trial['version']:
        raise ValueError('trial with version identity required')
    receipt = activation_receipt(events, case_id=case_id, skill_id=skill_id)
    receipt['trial_binding'] = 'caller_declared_unverified'
    return dict(trial, skill_activation_receipt=receipt)


def activation_receipt_main(argv=None):
    """Inspect a caller-declared link between a saved trial and normalized case."""
    import argparse
    import json
    from pathlib import Path
    from .integrity import read_regular
    parser = argparse.ArgumentParser(prog='ahl skills activation-receipt',
        description='Derive a private receipt from a caller-declared trial and normalized capture; the association is unverified.')
    parser.add_argument('--results', required=True, type=Path, help='Private evaluator results.json')
    parser.add_argument('--events', required=True, type=Path, help='Private normalized event JSONL')
    parser.add_argument('--task', required=True, help='Exact trial task')
    parser.add_argument('--version', required=True, choices=('baseline', 'candidate'), help='Exact trial arm')
    parser.add_argument('--repetition', type=int, default=0, help='Exact trial repetition (default: 0)')
    parser.add_argument('--case-id', required=True, help='Caller-selected normalized case identity')
    parser.add_argument('--skill-id', help='Optional pseudonymous skill identity')
    args = parser.parse_args(argv)
    try:
        results = json.loads(read_regular(args.results, 64*1024*1024))
        trials = results['trials']
        if not isinstance(trials, list): raise ValueError('results trials must be a list')
        matches = [t for t in trials if isinstance(t, dict) and t.get('task') == args.task
                   and t.get('version') == args.version and t.get('repetition', 0) == args.repetition]
        if len(matches) != 1: raise ValueError('trial selection must match exactly one result')
        rows = [json.loads(line) for line in read_regular(args.events, 64*1024*1024).splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows): raise ValueError('event records must be objects')
        events = [row for row in rows if row.get('case_id') == args.case_id]
        linked = attach_activation_receipt(matches[0], events, case_id=args.case_id, skill_id=args.skill_id)
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(dict(trial=dict(task=args.task, version=args.version, repetition=args.repetition),
                          skill_activation_receipt=linked['skill_activation_receipt']), sort_keys=True))
    return 0


def utility(records):
    groups={}; tasks={}; roots={}
    for record in records:
        condition=record['condition']
        if condition not in ('natural','preload','negative_routing','transfer'):
            raise ValueError('unknown routing condition')
        group=groups.setdefault(condition,dict(tasks=0,roots=0,records=0,unknown_identity_records=0,verified_outcome_records=0,successes=0,
                                             observable_activations=0,critical_regressions=0))
        group['records']+=1
        task=record.get('task_id'); root=record.get('root_id')
        if isinstance(task,str) and task:
            tasks.setdefault(condition,set()).add(task); group['tasks']=len(tasks[condition])
        else:
            group['unknown_identity_records']+=1
        if isinstance(root,str) and root:
            roots.setdefault(condition,set()).add(root); group['roots']=len(roots[condition])
        group['observable_activations']+=record.get('activated') is True
        if type(record.get('outcome')) is bool and record.get('outcome_provenance')=='executable_grader':
            group['verified_outcome_records']+=1; group['successes']+=record['outcome']
        group['critical_regressions']+=record.get('critical_regression') is True
    return dict(conditions=groups,causal_effect='requires_frozen_paired_experiment',
                disposition='rejected' if any(g['critical_regressions'] for g in groups.values()) else 'uncertified',
                automatic_compilation=False)


def compile_procedure(value, *, enabled=False):
    if not enabled: return dict(status='disabled')
    required=('procedure','prerequisites','activate_when','do_not_activate_when','counterexamples','evidence_refs')
    if (value.get('approved') is not True or value.get('outcome_provenance')!='executable_grader'
            or value.get('verified_success') is not True or value.get('scope')!='project'
            or any(not value.get(k) for k in required)):
        raise ValueError('skill compilation requires approved verified procedures, exclusions and counterexamples')
    # Returns a hypothesis, never installs a skill or grants authority.
    import json
    if len(json.dumps(value))>16384: raise ValueError('procedure exceeds compilation bound')
    return dict(status='hypothesis',procedure={k:value[k] for k in required},
                needs_independent_evaluation=True,global_install=False,automatic_promotion=False)


TRANSFER_SCHEMA = 'skill-paired-transfer-v1'
ARMS = ('absent', 'present')
DECLARABLE = ('natural', 'transfer', 'negative_routing')


def _transfer_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {'target_conditions', 'control_conditions', 'max_control_loss'}:
        raise ValueError('transfer policy declares exactly target_conditions, control_conditions and max_control_loss')
    roles = {}
    for key in ('target_conditions', 'control_conditions'):
        value = policy[key]
        if not isinstance(value, (list, tuple)) or not value or any(c not in DECLARABLE for c in value):
            raise ValueError(f'{key} must list natural, transfer or negative_routing; preload is diagnostic only')
        roles[key] = sorted(set(value))
    if set(roles['target_conditions']) & set(roles['control_conditions']):
        raise ValueError('a condition cannot be both a target and a control')
    loss = policy['max_control_loss']
    if type(loss) not in (int, float) or not 0 <= loss <= 1:
        raise ValueError('max_control_loss must be a number from 0 to 1')
    return dict(roles, max_control_loss=loss)


def _observed(record):
    return record.get('activated') is True and bool(record.get('activation_evidence'))


def paired_transfer(records, *, policy):
    """Compare skill-present with skill-absent arms over matched task roots under a predeclared policy.

    The main contrast covers every eligible root, whatever activation was observed; an
    unobserved activation stays unknown and never filters, confirms or refutes a pair.
    """
    import hashlib
    import json
    from fractions import Fraction
    declared = _transfer_policy(policy)
    role = {c: 'target' for c in declared['target_conditions']}
    role.update({c: 'control' for c in declared['control_conditions']})
    excluded = dict(unknown_identity_records=0, unverified_records=0)
    roots, groups = {}, {}
    activated = dict(records=0, successes=0)
    for record in records:
        condition, arm = record.get('condition'), record.get('arm')
        if condition not in ('natural', 'preload', 'negative_routing', 'transfer'):
            raise ValueError('unknown routing condition')
        if arm not in ARMS:
            raise ValueError('arm must be absent or present')
        group = groups.setdefault(condition, dict(
            role=role.get(condition, 'diagnostic' if condition == 'preload' else 'undeclared'),
            matched_roots=0, unmatched_roots=0, improved_roots=0, unchanged_roots=0, harmed_roots=0,
            mean_delta=None, excluded_records=0, present_observed_activations=0, present_unknown_activations=0,
            absent_observed_activations=0, present_shadowed_records=0, absent_shadowed_records=0, critical_regressions=0))
        # Excluded records never reach the counters that decide the disposition.
        task, root = record.get('task_id'), record.get('root_id')
        if not (isinstance(task, str) and task and isinstance(root, str) and root):
            excluded['unknown_identity_records'] += 1; group['excluded_records'] += 1; continue
        outcome = record.get('outcome')
        if type(outcome) is not bool or record.get('outcome_provenance') != 'executable_grader':
            excluded['unverified_records'] += 1; group['excluded_records'] += 1; continue
        group[f'{arm}_shadowed_records'] += record.get('shadowing') == 'ambient_same_name'
        if arm == 'present':
            group['present_observed_activations' if _observed(record) else 'present_unknown_activations'] += 1
            group['critical_regressions'] += record.get('critical_regression') is True
        else:
            group['absent_observed_activations'] += _observed(record)
        roots.setdefault((condition, task, root), {}).setdefault(arm, []).append(outcome)
        if arm == 'present' and _observed(record):
            activated['records'] += 1; activated['successes'] += outcome
    deltas = {}
    for (condition, _task, _root), arms in roots.items():
        group = groups[condition]
        if len(arms) < 2:
            group['unmatched_roots'] += 1; continue
        delta = Fraction(sum(arms['present']), len(arms['present'])) - Fraction(sum(arms['absent']), len(arms['absent']))
        deltas.setdefault(condition, []).append(delta)
        group['matched_roots'] += 1
        group['improved_roots' if delta > 0 else 'harmed_roots' if delta < 0 else 'unchanged_roots'] += 1
    for condition, values in deltas.items():
        groups[condition]['mean_delta'] = float(sum(values) / len(values))
    tolerance = Fraction(str(declared['max_control_loss']))
    if any(g['critical_regressions'] for g in groups.values()):
        disposition, reason = 'rejected', 'critical_regression'
    elif any(c in deltas and sum(deltas[c]) / len(deltas[c]) < -tolerance for c in declared['control_conditions']):
        disposition, reason = 'rejected', 'negative_transfer'
    elif any(g['absent_observed_activations'] for g in groups.values()):
        disposition, reason = 'inconclusive', 'absent_arm_activation'
    elif any(g['present_shadowed_records'] or g['absent_shadowed_records'] for g in groups.values()):
        # A same-name ambient copy means the arms may not differ by the staged skill alone.
        disposition, reason = 'inconclusive', 'shadowed_skill_copy'
    elif not any(c in deltas for c in declared['target_conditions']):
        disposition, reason = 'inconclusive', 'no_matched_target_roots'
    elif not all(c in deltas for c in declared['control_conditions']):
        disposition, reason = 'inconclusive', 'no_matched_control_roots'
    elif any(groups[c]['unmatched_roots'] or groups[c]['excluded_records'] for c in role if c in groups):
        # Arm-specific attrition (a crashed or ungraded present arm) could hide negative transfer.
        disposition, reason = 'inconclusive', 'unmatched_or_unverified_roots'
    else:
        disposition, reason = 'uncertified', 'requires_frozen_paired_experiment'
    digest = hashlib.sha256(json.dumps(declared, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(schema=TRANSFER_SCHEMA, policy=declared, policy_sha256=digest, conditions=groups, excluded=excluded,
                false_activations=sum(groups[c]['present_observed_activations'] for c in declared['control_conditions'] if c in groups),
                conditional_on_observed_activation=dict(activated, interpretation='descriptive_not_causal'),
                disposition=disposition, reason=reason, causal_effect='requires_frozen_paired_experiment',
                automatic_promotion=False)


COMPLIANCE_SCHEMA = 'skill-procedure-compliance-v1'
REQUIREMENTS = ('tool_call', 'no_tool_call', 'artifact')
MAX_ALTERNATIVES, MAX_REQUIREMENTS = 16, 32


def _requirements(rows, owner, *, required):
    if not isinstance(rows, (list, tuple)) or (required and not rows):
        raise ValueError(f'{owner} needs a {"nonempty " if required else ""}requirement list')
    if len(rows) > MAX_REQUIREMENTS:
        raise ValueError(f'a requirement list holds at most {MAX_REQUIREMENTS} requirements')
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'kind', 'target'}:
            raise ValueError('requirement fields must be exactly kind and target')
        if row['kind'] not in REQUIREMENTS:
            raise ValueError('requirement kind must be tool_call, no_tool_call or artifact')
        if not isinstance(row['target'], str) or not row['target']:
            raise ValueError('requirement target must be nonempty text')
    return [(f"{row['kind']}:{row['target']}", row) for row in rows]


def procedure_compliance(spec, *, events, artifacts):
    """Grade one case against externally checkable requirements, accepting any declared alternative.

    A requirement is `tool_call` (observed), `no_tool_call` (not observed) or `artifact` (a named
    executable check passed). Each resolves to met, unmet or unknown. An absent tool call is unmet only
    when every row of a nonempty capture says it is complete and every tool call is named; otherwise it is unknown, as is an artifact
    check that is missing, ungraded by an executable grader or not boolean. Skill-activation signals and
    phrasing never satisfy a requirement. Safety requirements are reported apart from the procedure.
    """
    from ..telemetry.schema import event
    import hashlib
    import json
    if not isinstance(spec, dict) or set(spec) != {'alternatives', 'safety'}:
        raise ValueError('procedure spec declares exactly alternatives and safety')
    alternatives = spec['alternatives']
    if not isinstance(alternatives, dict) or not alternatives:
        raise ValueError('procedure spec needs at least one alternative')
    if len(alternatives) > MAX_ALTERNATIVES:
        raise ValueError(f'procedure spec holds at most {MAX_ALTERNATIVES} alternatives')
    if any(not isinstance(name, str) or not name for name in alternatives):
        raise ValueError('alternative names must be nonempty text')
    declared = {name: _requirements(rows, f'alternative {name}', required=True) for name, rows in alternatives.items()}
    safety = _requirements(spec['safety'], 'safety', required=False)
    if not isinstance(artifacts, dict):
        raise ValueError('artifact checks must be an object')
    rows = [event(raw) for raw in events]
    if len({row['case_id'] for row in rows}) > 1:
        raise ValueError('procedure compliance requires one case')
    calls = [row for row in rows if row['activity'] == 'tool_call']
    called = {row.get('tool_name') for row in calls}
    # A nameless tool call may be any tool, so it makes absence undecidable like a partial capture.
    complete = bool(rows) and all(row.get('capture_complete') is True for row in rows) and all(row.get('tool_name') for row in calls)

    def resolve(row):
        kind, target = row['kind'], row['target']
        if kind == 'artifact':
            check = artifacts.get(target)
            if (not isinstance(check, dict) or type(check.get('passed')) is not bool
                    or check.get('provenance') != 'executable_grader'):
                return 'unknown'
            return 'met' if check['passed'] else 'unmet'
        if target in called:
            return 'met' if kind == 'tool_call' else 'unmet'
        return ('unmet' if kind == 'tool_call' else 'met') if complete else 'unknown'

    def combine(states):
        return 'noncompliant' if 'unmet' in states else 'unknown' if 'unknown' in states else 'compliant'

    report, verdicts = {}, []
    for name, requirements in declared.items():
        states = {key: resolve(row) for key, row in requirements}
        report[name] = dict(state=combine(states.values()), requirements=states)
        verdicts.append(report[name]['state'])
    guards = {key: resolve(row) for key, row in safety}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(schema=COMPLIANCE_SCHEMA, spec_sha256=digest,
                state='compliant' if 'compliant' in verdicts else 'noncompliant' if set(verdicts) == {'noncompliant'} else 'unknown',
                safety_state=('violated' if 'unmet' in guards.values() else 'unknown' if 'unknown' in guards.values()
                              else 'satisfied' if guards else 'none_declared'),
                alternatives=report, safety=guards, interpretation='observable_requirements_not_causal',
                publication_allowed=False)
