"""Bounded first-run resolution and independently checked structural repairs.

Behavioral promotion remains owned by the controlled evaluation policy.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import time

from . import config


def resolve(args, explicit):
    """Resolve execution intent without treating project data as authorization."""
    path = args.project / 'ahl.json'
    loaded = config.load(path) if os.path.lexists(path) else None
    settings = loaded[0] if loaded else None
    if args.timeout is None:
        args.timeout = settings['limits']['seconds'] if settings else 2700
    if args.agent == 'auto' and settings and '--agent' not in explicit:
        args.agent = settings['runtime']['name']
    if settings and settings['runtime']['name'] in ('codex', 'claude'):
        name = settings['runtime']['name']
        if '--' + name not in explicit:
            setattr(args, name, settings['runtime']['executable'])
    args.trial_limit = settings['limits']['trials'] if settings else 24
    args.project_settings = settings
    args.configuration_sha256 = loaded[1] if loaded else None
    return settings


def preflight(args):
    settings = getattr(args, 'project_settings', None)
    if settings:
        from .evaluation.task_bank import validate
        reviewed = validate(config.bank_path(args.project, settings))
        if reviewed['bank_sha256'] != settings['bank']['sha256']:
            return 'configured_bank_digest_mismatch'
        if settings['profile'] != 'cooperative':
            return 'configured_profile_does_not_allow_cooperative_improvement'
        if pinned_bank(args) is not None:
            if args.agent == 'auto': args.agent = settings['runtime']['name']
            if args.agent != settings['runtime']['name'] or getattr(args, args.agent, None) != settings['runtime']['executable']:
                return 'configured_runtime_differs_from_requested_runtime'
            if any(getattr(args, name, None) is not None for name in ('experiment_request', 'evaluation_store')):
                return 'configured_bank_cannot_combine_with_managed_experiment'
            if reviewed.get('admission') is not None:
                return 'configured_managed_bank_requires_controlled_workflow'
            partitions = reviewed['partitions']
            if not ((partitions['development'] or partitions['construction']) and partitions['calibration']
                    and (partitions['certification'] or partitions['transfer'])):
                return 'configured_bank_requires_development_regression_and_heldout'
            if args.trial_limit < 1 + 2 * reviewed['tasks']:
                return 'configured_bank_shared_trial_budget_exhausted'
    if args.agent == 'auto':
        found = [name for name in ('codex', 'claude') if shutil.which(getattr(args, name))]
        if len(found) != 1:
            return 'select_one_runtime_with_agent' if found else 'agent_runtime_unavailable'
        args.agent = found[0]
    if args.agent != 'responses' and not shutil.which(getattr(args, args.agent)):
        return 'agent_runtime_unavailable'
    if pinned_bank(args) is None and getattr(args, 'trial_limit', 24) < 8:
        return 'trial_budget_requires_two_preparation_and_six_paired_trials'
    return None


def pinned_bank(args):
    settings = getattr(args, 'project_settings', None)
    return config.bank_path(args.project, settings) if settings and settings['bank']['path'] != 'starter' else None


def unchanged(args):
    path = args.project / 'ahl.json'
    expected = getattr(args, 'configuration_sha256', None)
    if not (config.load(path)[1] == expected if expected else not os.path.lexists(path)):
        return False
    bank = pinned_bank(args)
    if bank is not None:
        from .evaluation.task_bank import validate
        return validate(bank)['bank_sha256'] == args.project_settings['bank']['sha256']
    return True


def check_configuration(args):
    if hasattr(args, 'project_settings') and not unchanged(args):
        raise ValueError('project configuration changed during improvement')


def allowed_paths(args):
    settings = getattr(args, 'project_settings', None)
    return settings['harness_paths'] if settings else None


def check_scope(args, proposed):
    allowed = allowed_paths(args)
    if allowed is not None and not set(proposed) <= set(allowed):
        raise ValueError('candidate exceeds configured harness scope')


def structural_candidate(raw, directory):
    """Only remove a UTF-8 BOM preceding an otherwise valid minimal skill header.

    This intentionally handles a strict subset, not arbitrary YAML. Extended,
    executable, ambiguous, duplicate and multiline metadata are unsupported.
    """
    if not raw.startswith(b'\xef\xbb\xbf---\n'):
        return None
    text = raw[3:].decode('utf-8')
    match = re.match(r'---\nname: ([a-z0-9]+(?:-[a-z0-9]+)*)\ndescription: ([^\n]+)\n---\n', text)
    if not match or match[1] != directory or len(directory) > 64:
        return None
    description = match[2]
    if description.lower() in ('true', 'false', 'yes', 'no', 'on', 'off', 'null'):
        return None
    if len(description) > 1024 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 .,;!?()/-]*', description):
        return None
    return raw[3:]


def contracts(args):
    """One-call deterministic contract path; invoked only by explicit CLI option."""
    from .workflow import (output_dir, snapshot, candidate_states, file_state,
                           state_root, identity, skill_roots, write_json)
    from .promotion.journal import Journal, file_record
    if getattr(args, 'experiment_request', None) is not None:
        raise ValueError('contract repair and repeated experiment requests are separate modes')
    started = time.monotonic()
    output = output_dir(args)
    baseline, candidate = output/'baseline', output/'candidate'
    original = snapshot(args.project, baseline, allowed_paths=allowed_paths(args))
    candidate_states(baseline, original)
    shutil.copytree(baseline, candidate)
    operations = []
    settings = getattr(args, 'project_settings', None)
    headers = {str((root/'SKILL.md').relative_to(baseline)) for root in skill_roots(baseline)}
    for name in original:
        path = candidate/name
        if name not in headers:
            continue
        if settings and name not in settings['harness_paths']:
            continue
        raw = path.read_bytes()
        repaired = structural_candidate(raw, path.parent.name)
        if repaired is None:
            continue
        path.write_bytes(repaired)
        operations.append(dict(path=name, surface='skill', operation='update',
            profile='one-call-improvement-v1', publication_allowed=False,
            contract='minimal-agent-skill-header-without-bom-v1',
            before_sha256=hashlib.sha256(raw).hexdigest(), after_sha256=hashlib.sha256(repaired).hexdigest()))
    proposed = candidate_states(candidate, original)
    plan = dict(schema_version=1, kind='deterministic_contract', operations=operations,
                profile='one-call-improvement-v1', publication_allowed=False,
                configuration_sha256=getattr(args, 'configuration_sha256', None),
                behavioral_certification=False, runtime_activation='unknown',
                deferred=['behavioral optimization', 'skill lifecycle and routing', 'instructions', 'hooks'])
    write_json(output/'change-plan.json', plan)
    result = dict(status='no_change', output=str(output), changed_files=[], plan=str(output/'change-plan.json'),
                  profile=plan['profile'], publication_allowed=False,
                  behavioral_certification=False, deferred=plan['deferred'])
    if operations:
        if settings and settings['recommendation_only']:
            result.update(status='inconclusive', reason='configuration_requires_recommendation_only')
        elif time.monotonic()-started >= args.timeout:
            result.update(status='budget_exhausted')
        else:
            if not unchanged(args) or candidate_states(candidate, original) != proposed:
                raise ValueError('configuration or candidate changed before application')
            if any(file_state(args.project/name) != state for name, state in original.items()):
                raise ValueError('target changed before application')
            # Recompute the exact allowed transformation from the frozen baseline;
            # a model claim, serialized plan or passing smoke test grants no authority.
            for operation in operations:
                name = operation['path']
                if structural_candidate((baseline/name).read_bytes(), (baseline/name).parent.name) != (candidate/name).read_bytes():
                    raise ValueError('contract repair did not match the frozen independent check')
            journal = Journal(args.project, state_root()/'promotions'/identity(args.project))
            record = journal.prepare({o['path']: file_record(baseline/o['path']) for o in operations},
                {o['path']: file_record(candidate/o['path']) for o in operations},
                dict(source='deterministic_contract_v1', behavioral_certification=False))
            def permitted():
                check_configuration(args)
                if time.monotonic()-started >= args.timeout:
                    raise TimeoutError('Contract repair budget exhausted before target mutation')
            permitted()
            receipt = journal._execute(record['deployment'],pre_write=permitted)
            result.update(status='applied', changed_files=sorted(o['path'] for o in operations),
                          deployment=receipt['deployment'], journal=str(journal.folder),
                          rollback=f'ahl rollback {receipt["deployment"]} --project {shlex.quote(str(args.project))}')
    write_json(output/'workflow.json', result)
    return result


def rollback_main(argv):
    import argparse
    from .workflow import state_root, identity
    from .promotion.journal import Journal
    parser = argparse.ArgumentParser(prog='ahl rollback', description='Restore an owned deployment if no intervening edits conflict',
        epilog='Use the deployment ID from a successful repair receipt: ahl rollback DEPLOYMENT --project /project. Reads the private project Journal, verifies current owned bytes/modes and restores the saved state; foreign edits are preserved as conflicts. Prints a JSON receipt. Exit 0 on rollback; invalid ownership/input or conflicts exit 2. No model calls.')
    parser.add_argument('deployment',help='Exact 32-hex deployment ID returned by the owned application Journal; not a run ID or arbitrary file path')
    parser.add_argument('--project', type=Path, default=Path.cwd(),help='Existing project directory belonging to that deployment (default: current directory); selects its private Journal')
    args = parser.parse_args(argv)
    try:
        project = args.project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError('project must be a directory')
        journal = Journal(project, state_root()/'promotions'/identity(project))
        result = journal.rollback(args.deployment)
        receipt={key: result[key] for key in ('deployment','kind','status')}
        receipt.update(profile='one-call-improvement-v1',publication_allowed=False)
        print(json.dumps(receipt, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'rollback: {exc}\n')
