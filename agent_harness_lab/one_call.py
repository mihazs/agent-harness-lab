"""Bounded first-run resolution and independently checked structural repairs.

Behavioral promotion remains owned by the controlled evaluation policy.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import time

from . import config, surfaces, user_config
from .exit_guidance import REMEDIATION as REMEDIATION


def invocation_context(args,output,deadline,*,remaining_seconds=None,remaining_trials=None):
    """Descriptive parent context; these fields never issue execution authority."""
    settings=getattr(args,'project_settings',None) or {}
    application=settings.get('application',{})
    return dict(schema_version=1,profile='ahl-skill-invocation-context-v1',scope=getattr(args,'scope','project'),
        origin=getattr(args,'_invocation_origin','cli'),parent_run_id=Path(output).name,
        project=str(args.project),runtime=args.agent,configuration_sha256=getattr(args,'configuration_sha256',None),
        policy=application.get('policy'),review_store=application.get('review_store'),authorization_id=application.get('authorization_id'),
        bank_case_id=application.get('bank_case_id'),deadline=deadline,trial_limit=getattr(args,'trial_limit',config.DEFAULT_TRIALS),
        remaining_seconds=max(0,deadline-time.monotonic()) if remaining_seconds is None else max(0,remaining_seconds),
        remaining_trials=getattr(args,'trial_limit',config.DEFAULT_TRIALS) if remaining_trials is None else remaining_trials,
        execution_authority=False,publication_allowed=False,runtime_selection=getattr(args,'_runtime_selection',None),cost_preview=getattr(args,'_cost_preview',None))


def resolve(args, explicit):
    """Resolve execution intent without treating project data as authorization."""
    requested=getattr(args,'_configuration_invocation',None)
    if requested is None:requested={name:getattr(args,name,None) for name in config.INVOCATION_OPTIONS}
    args._configuration_invocation=dict(requested);args._configuration_explicit=frozenset(explicit)
    resolved=config.resolve(args.project,requested,explicit)
    settings=resolved['settings']
    for name,value in resolved['options'].items():setattr(args,name,value)
    args.trial_limit = resolved['trial_limit']
    args.project_settings = settings
    args.configuration_sha256 = resolved['configuration_sha256']
    args._configured_responses=bool(settings and settings['runtime']['name']=='responses' and args.agent=='responses')
    return settings


def preflight(args):
    args._preflight_requested_runtime = args.agent
    args._preflight_runtimes = None
    if getattr(args, 'history', False) and args.history_source == 'ai-memory':
        return 'history_source_ai_memory_unsupported'
    if args.agent == 'responses' and getattr(args, 'inherit_env', False):
        return 'responses_inherit_env_unsupported'
    settings = getattr(args, 'project_settings', None)
    if settings:
        from .evaluation.manifest_contract import ContractError
        from .evaluation.task_bank import validate
        try:
            reviewed = validate(config.bank_path(args.project, settings), registry=settings.get('areas', []))
        except ContractError:
            raise  # a typed area declaration defect keeps its reason, field and origin
        except (ValueError, OSError, TypeError, KeyError):
            return 'configured_bank_invalid_or_unavailable'
        if reviewed['bank_sha256'] != settings['bank']['sha256']:
            return 'configured_bank_digest_mismatch'
        if settings['profile'] != 'cooperative':
            return 'configured_profile_does_not_allow_cooperative_improvement'
        if pinned_bank(args) is not None:
            if settings['runtime']['name'] == 'responses':
                # Pinned project banks are evaluated through the cooperative path only.
                return 'configured_bank_requires_cooperative_runtime'
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
        args._preflight_runtimes = found
        host = invoking_host()
        remembered = user_config.load()['runtime']
        if remembered in found: args._runtime_selection = dict(policy='user_remembered', runtime=remembered)
        elif len(found) == 1: args._runtime_selection = dict(policy='single_installed', runtime=found[0])
        elif len(found) > 1 and host in found: args._runtime_selection = dict(policy='invoking_host', runtime=host)
        else:
            args._runtime_selection = dict(policy='ask_once', runtime=None, host=host) if found else None
            return 'select_one_runtime_with_agent' if found else 'agent_runtime_unavailable'
        args.agent = args._runtime_selection['runtime']
    if args.agent != 'responses':
        available = bool(shutil.which(getattr(args, args.agent)))
        args._preflight_runtimes = [args.agent] if available else []
        if not available:
            return 'agent_runtime_unavailable'
    if pinned_bank(args) is None and getattr(args, 'trial_limit', config.DEFAULT_TRIALS) < 8:
        return 'trial_budget_requires_two_preparation_and_six_paired_trials'
    if getattr(args,'_configured_responses',False):
        args._response_references=config.response_references(args.project,vars(args))
    if getattr(args, 'remember_runtime', False) and user_config.load()['runtime'] != args.agent:
        user_config.remember(args.agent)  # only the explicit flag writes, and only after the run passed preflight
    return None


HOST_MARKERS = (('claude', ('CLAUDECODE',)), ('codex', ('CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED')))


def invoking_host():
    """The supported runtime that launched this process, from variable presence only; None when absent or both."""
    hosts = [name for name, markers in HOST_MARKERS if any(os.environ.get(marker) for marker in markers)]
    return hosts[0] if len(hosts) == 1 else None


AUTHORIZATION = ('An explicit invocation authorizes the bounded default workflow within the stated limits. '
    'execution_authority describes this plan only: false because nothing was dispatched, not because a run is disallowed.')


# One actionable step per preflight reason; no step grants authorization or changes a setting itself.
DEFAULT_REMEDIATION = 'Select a supported runtime/profile and budget; see ahl improve --help'


def preflight_plan(args, reason):
    settings = getattr(args, 'project_settings', None)
    plan = dict(requested_runtime=args._preflight_requested_runtime, observed_runtimes=args._preflight_runtimes,
        configured_scope=settings['harness_paths'] if settings else None,
        limits=dict(seconds=args.timeout, trials=args.trial_limit),
        runtime_selection=getattr(args, '_runtime_selection', None),
        choice=dict(option='--agent', values=args._preflight_runtimes, remember_option='--remember-runtime') if reason == 'select_one_runtime_with_agent' else None,
        next_step=REMEDIATION.get(reason, DEFAULT_REMEDIATION), authorization=AUTHORIZATION,
        candidate_prepared=False, evidence_prepared=False, execution_authority=False)
    if getattr(args, 'history', False): plan['history'] = dict(since=args.since or 'first', source=args.history_source or 'native')
    return plan


def cost_preview(args):
    """Ceilings and authorization shown once preflight passes, before any model dispatch. Cost is never estimated."""
    settings = getattr(args, 'project_settings', None)
    return dict(runtime=args.agent, harness_scope=settings['harness_paths'] if settings else 'project default',
        max_seconds=args.timeout, max_trials=args.trial_limit, authorization=AUTHORIZATION,
        model_cost='unknown: model usage is billed by the runtime provider and AHL does not measure tokens or money; '
                   'the ceilings above bound time and trials, not spend')


def announce_cost(args):
    """Record the preview for the run context and show it via the CLI hook when one is attached."""
    args._cost_preview = preview = cost_preview(args)
    show = getattr(args, '_preview', None)
    if show: show(preview)


def pinned_bank(args):
    settings = getattr(args, 'project_settings', None)
    return config.bank_path(args.project, settings) if settings and settings['bank']['path'] != 'starter' else None


def unchanged(args, *, check_bank=True):
    path = args.project / 'ahl.json'
    expected = getattr(args, 'configuration_sha256', None)
    if not (config.load(path)[1] == expected if expected else not os.path.lexists(path)):
        return False
    bank = pinned_bank(args) if check_bank else None
    if bank is not None:
        from .evaluation.task_bank import validate
        return validate(bank)['bank_sha256'] == args.project_settings['bank']['sha256']
    return True


def check_configuration(args, *, check_bank=True):
    if hasattr(args, 'project_settings') and not unchanged(args, check_bank=check_bank):
        raise ValueError('project configuration changed during improvement')
    references=getattr(args,'_response_references',None)
    if references is not None and config.response_references(args.project,vars(args))!=references:
        raise ValueError('controlled Responses references changed during improvement')


def allowed_paths(args):
    settings = getattr(args, 'project_settings', None)
    return settings['harness_paths'] if settings else None


def check_scope(args, proposed, original=None):
    """Configured harness scope, then the instruction scope from surfaces: with the frozen baseline inventory, a
    changed instruction or rule file must be governing (see sources.instruction_validation.outside_scope)."""
    allowed = allowed_paths(args)
    if allowed is not None and not set(proposed) <= set(allowed):
        raise ValueError('candidate exceeds configured harness scope')
    if original is not None:
        from .sources.instruction_validation import outside_scope
        if outside_scope(original, proposed, getattr(args, '_instruction_scope', None)):
            raise ValueError('candidate edits an instruction or rule file outside its governing scope')


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


# Work outside the surface matrix; the surface part of the deferred list comes from surfaces.deferred().
DEFERRED_WORK=('behavioral optimization','skill lifecycle and routing')


def deferred_surfaces(hook_contracts):
    """The contract path resolves only skill headers; 'hooks' is resolved too only when the run has hook units and
    every hook_contract_v1 verdict passed. A workflow_contract_v1 verdict is not a hook unit."""
    from .hooks_contract import PROFILE
    units=[row for row in hook_contracts if row.profile==PROFILE]
    passed=bool(units) and all(row.status=='passed' for row in units)
    return [*DEFERRED_WORK,*surfaces.deferred(('commands_skills','hooks') if passed else ('commands_skills',))]


def layer_lock(args, layer):
    """improve() already holds the project's lock; a user-level layer takes its own unless it is that same directory."""
    from .workflow import project_lock
    return contextlib.nullcontext() if layer.label == 'project' or layer.root == args.project else project_lock(layer.root)


def contracts(args):
    """One-call deterministic contract path; invoked only by explicit CLI option. --scope selects the layers: the
    project by default, or the user-level skill directory (alone or first) when explicitly requested."""
    from . import scope
    from .workflow import output_dir, write_json
    if getattr(args, 'experiment_request', None) is not None:
        raise ValueError('contract repair and repeated experiment requests are separate modes')
    started = getattr(args, '_construction_started', None)
    if started is None: started = time.monotonic()
    args._budget_started = started
    selection = scope.layers(args)
    output = output_dir(args, deadline=started + args.timeout)
    if len(selection) == 1:
        layer = selection[0]
        with layer_lock(args, layer):
            args._scope_current, args._scope_results, args._scope_roots = layer.label, [], {layer.label: layer.root}
            return _layer_contracts(args, layer, output, output, started, lambda result: scope.combine(args, [(layer.label, result)], base=result, roots=args._scope_roots))
    results, args._scope_results, args._scope_roots = [], [], {layer.label: layer.root for layer in selection}
    for layer in selection:
        args._scope_current = layer.label
        if results and results[-1][1]['status'] not in ('applied', 'no_change'):
            results.append((layer.label, scope.not_attempted(results[-1][1]['status'])))
            continue
        folder = output / layer.label
        folder.mkdir()
        with layer_lock(args, layer):
            try:
                results.append((layer.label, _layer_contracts(args, layer, output, folder, started)))
            except (ValueError, TypeError, KeyError) as exc:
                if not any(done['changed_files'] for _, done in results):
                    raise
                # An earlier layer is already committed: report it beside this failure instead of hiding it behind an error.
                results.append((layer.label, dict(status='invalid_input', changed_files=[], error=str(exc), reason=getattr(exc, 'reason', None))))
        args._scope_results = list(results)
    base = dict(output=str(output), profile='one-call-improvement-v1', publication_allowed=False, behavioral_certification=False,
                deferred=results[0][1]['deferred'], precedence=scope.precedence(scope.runtime_of(args), selection))
    result = scope.combine(args, results, base=base, roots=args._scope_roots)
    try:
        write_json(output / 'workflow.json', result)
    except (OSError, KeyboardInterrupt):
        result['workflow_completion'] = 'unrecorded'  # every layer already recorded its own receipt and journal
    return result


def _layer_contracts(args, layer, output, folder, started, finish=None):
    """One layer of the deterministic contract path, rooted at the layer's own directory."""
    from .workflow import (snapshot, candidate_states, file_state,
                           state_root, identity, skill_roots, write_json)
    from .promotion.journal import Journal, file_record
    root = layer.root
    baseline, candidate = folder/'baseline', folder/'candidate'
    original = snapshot(root, baseline, allowed_paths=layer.allowed)
    candidate_states(baseline, original)
    shutil.copytree(baseline, candidate)
    operations = []
    # A project ahl.json constrains the project harness only; it never widens or narrows the user-level layer.
    settings = getattr(args, 'project_settings', None) if layer.label == 'project' else None
    headers = {str((skill/'SKILL.md').relative_to(baseline)) for skill in skill_roots(baseline)}
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
    from .harness_change_plan import produce,attach
    from .hooks_contract import contracts_for
    changed=[name for name in original.keys()|proposed.keys() if original.get(name)!=proposed.get(name)]
    hook_contracts=contracts_for(root,{name:file_record(baseline/name) if name in original else None for name in changed},
        {name:file_record(candidate/name) if name in proposed else None for name in changed})
    deferred=deferred_surfaces(hook_contracts)
    common_plan=produce(args,original,proposed,analysis=dict(kind='deterministic_contract_inventory'),evidence=operations,
        diagnosis=dict(contract='minimal-agent-skill-header-without-bom-v1'),
        falsifiers=['outside_strict_header_contract','candidate_not_exact_bom_removal'],
        controls=['independent_frozen_structural_candidate_check'],deferred=deferred,contracts=hook_contracts,project=root,user_level=layer.label!='project')
    plan = dict(schema_version=1, kind='deterministic_contract', operations=operations,
                profile='one-call-improvement-v1', publication_allowed=False,
                configuration_sha256=getattr(args, 'configuration_sha256', None) if layer.label == 'project' else None,
                behavioral_certification=False, runtime_activation='unknown', deferred=deferred)
    attach(plan,common_plan)
    write_json(folder/'change-plan.json', plan)
    result = dict(status='no_change', output=str(output), changed_files=[], plan=str(folder/'change-plan.json'),
                  profile=plan['profile'], publication_allowed=False,
                  behavioral_certification=False, deferred=plan['deferred'],plan_sha256=common_plan.sha256)
    if operations:
        from .evidence_label import pre_edit_check
        result['pre_edit_check'] = pre_edit_check(root, [o['path'] for o in operations])
        if common_plan.unresolved_operations:
            result.update(status='blocked',reason='unresolved_operation_requires_hook_contract')
        elif hook_contracts:
            # A passed hook contract resolves the plan, but adoption still needs explicit authorization.
            result.update(status='inconclusive',reason='hook_adoption_requires_explicit_authorization')
        elif settings and settings['recommendation_only']:
            result.update(status='inconclusive', reason='configuration_requires_recommendation_only')
        elif time.monotonic()-started >= args.timeout:
            result.update(status='budget_exhausted')
        else:
            if not unchanged(args) or candidate_states(candidate, original) != proposed:
                raise ValueError('configuration or candidate changed before application')
            if any(file_state(root/name) != state for name, state in original.items()):
                raise ValueError('target changed before application')
            # Recompute the exact allowed transformation from the frozen baseline;
            # a model claim, serialized plan or passing smoke test grants no authority.
            for operation in operations:
                name = operation['path']
                if structural_candidate((baseline/name).read_bytes(), (baseline/name).parent.name) != (candidate/name).read_bytes():
                    raise ValueError('contract repair did not match the frozen independent check')
            from .workflow import harness_files
            journal=None
            def target_states():
                states={str(path.relative_to(root)):file_state(path)
                        for path in harness_files(root,layer.allowed)}
                pending=getattr(journal,'_pending_snapshot',None)
                if pending is not None:
                    name=str(pending[0].relative_to(root))
                    if file_state(pending[0])!=(pending[1],pending[2]):raise ValueError('journal staging snapshot changed')
                    states.pop(name,None)
                return states
            common_plan.validate_inventories(target_states(),proposed)
            journal = Journal(root, state_root()/'promotions'/identity(root))
            record = journal.prepare({o['path']: file_record(baseline/o['path']) for o in operations},
                {o['path']: file_record(candidate/o['path']) for o in operations},
                dict(source='deterministic_contract_v1', behavioral_certification=False,plan_sha256=common_plan.sha256))
            def permitted():
                check_configuration(args)
                common_plan.validate_inventories(candidate_states(baseline,original),candidate_states(candidate,original))
                observed=target_states()
                if observed.keys()!=original.keys() or any(state!=original[name] and
                        (name not in {o['path'] for o in operations} or state!=proposed[name])
                        for name,state in observed.items()):
                    raise ValueError('target inventory changed during planned application')
                if time.monotonic()-started >= args.timeout:
                    raise TimeoutError('Contract repair budget exhausted before target mutation')
            permitted()
            receipt = journal._execute(record['deployment'],pre_write=permitted)
            result.update(status='applied', changed_files=sorted(o['path'] for o in operations),
                          deployment=receipt['deployment'], journal=str(journal.folder),
                          rollback=f'ahl rollback {receipt["deployment"]} --project {shlex.quote(str(root))}')
    if operations and result['status'] != 'applied':
        result['recommended_files'] = sorted(o['path'] for o in operations)
    def record_result():
        write_json(folder/'workflow.json', finish(result) if finish else result)
    try:
        record_result()
    except BaseException as exc:
        if result['status'] != 'applied':
            raise
        # The Journal already returned its committed receipt. Observe it and the
        # exact expected inventory; never replay or restore after commit.
        observed = False
        try:
            observed = (journal.active == receipt['deployment'] and journal.load(receipt['deployment']) == receipt
                        and all(file_state(root/name) == state for name, state in proposed.items()))
        except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
            pass
        result.update(status='applied' if observed else 'requires_intervention', commit_observation='committed',
                      target_readback='matched' if observed else 'changed_or_unavailable',
                      post_commit_error_type=type(exc).__name__)
        if not observed:
            result['remaining_change_ownership'] = 'unverified'
        try:
            record_result()
        except (OSError, KeyboardInterrupt):
            result['workflow_completion'] = 'unrecorded'
    return finish(result) if finish else result


def rollback_main(argv):
    import argparse
    from .workflow import state_root, identity
    from .promotion.journal import Journal
    parser = argparse.ArgumentParser(prog='ahl rollback', description='Restore an owned deployment if no intervening edits conflict',
        epilog='Use the deployment ID from a successful repair receipt: ahl rollback DEPLOYMENT --project /project. Reads the private project Journal, verifies current owned bytes/modes and restores the saved state; foreign edits are preserved as conflicts. Prints a JSON receipt. Exit 0 on rollback; invalid ownership/input or conflicts exit 2. No model calls. An argument named install-NAME restores an installation backup instead (launcher, skill links, instruction files); see ahl update --help.')
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
