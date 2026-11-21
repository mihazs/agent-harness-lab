"""Validate frozen inputs for a cold loop resume without starting a new request."""
import hashlib
import json
import math
import os
import stat
import time
from pathlib import Path

from agent_harness_lab.evaluation import integrity
from agent_harness_lab.private_journal import record

from .ledger import LoopLedger
from .spec import LoopSpec


OPTIONS = ('agent', 'codex', 'claude', 'timeout', 'trial_limit', 'construction_fraction', 'focus', 'area',
           'scope', 'inherit_env', 'with_user_harness', 'sandbox_graders', 'cgroup_root', 'image_root', 'image_sha256', 'disk_bytes', 'permission_audit',
           'configuration_sha256')


_FIELDS = {'schema_version', 'project', 'selected_agent', 'binary', 'options', 'explicit', 'invocation', 'spec', 'frozen_inputs_sha256',
           'queue', 'carried', 'loop_layers', 'scope_source', 'receipt_context', 'original', 'allowed', 'baseline_sha256', 'baseline_runner_sha256',
           'runner_program_sha256', 'runtime_binary_sha256', 'plugin_sha256', 'brief_sha256', 'manifest_sha256', 'scoring_sha256', 'analysis', 'incremental', 'grading',
           'initial_elapsed', 'initial_stage_spent'}


def _hash_file(path):
    return hashlib.sha256(integrity.read_regular(path, 8*1024*1024)).hexdigest()


def runtime_sha256(path):
    """Hash an executable without requiring it to have only one hard link."""
    descriptor = integrity.open_nofollow(path)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode): raise ValueError('runtime is not a regular file')
        digest = hashlib.sha256()
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''): digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _check_interrupted_receipt(envelope, block, output, history):
    from .receipt import status_of, validate_receipt, verify_projection
    validate_receipt(block)
    saved_count = block['events']
    if (type(saved_count) is not int or not 1 <= saved_count <= len(history)
            or block['ledger_head_sha256'] != history[saved_count-1][1]
            or block['stop_criterion'] != 'interrupted'
            or envelope.get('status') != status_of('interrupted')
            or envelope.get('reason') != 'loop_interrupted'
            or envelope.get('output') != str(output) or envelope.get('changed_files') != []
            or envelope.get('automatic_promotion') is not False or envelope.get('publication_allowed') is not False):
        raise ValueError('saved interrupted receipt differs')
    prefix = [(event['kind'], event['payload']) for event, _ in history[:saved_count]]
    if prefix[-1][0] != 'stopped' or prefix[-1][1].get('value') != 'interrupted':
        raise ValueError('saved receipt does not name an interruption')
    verify_projection(block, prefix)


def read(output, project):
    """Return validated, ledger-bound recovery inputs; never dispatch or modify a frozen input."""
    from agent_harness_lab import config, workflow
    from agent_harness_lab.cli import assets
    output, project = Path(output), Path(project)
    descriptor = integrity.open_directory_nofollow(output)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077: raise ValueError('unsafe recovery directory')
    finally:
        os.close(descriptor)
    context, context_hash = record(output / 'loop-resume-context.json', 8*1024*1024)
    if set(context) != _FIELDS or type(context['schema_version']) is not int or context['schema_version'] != 1 or context['project'] != str(project):
        raise ValueError('invalid loop recovery context')
    spec = LoopSpec.from_document(context['spec'])
    ledger = LoopLedger(workflow.state_root() / 'loops' / output.name, project)
    count, head, events = ledger.verified(spec=context['spec'], include_events=True)
    if (not events or events[0][1].get('resume_context_sha256') != context_hash
            or events[0][1].get('frozen_inputs_sha256') != context['frozen_inputs_sha256']
            or events[0][1].get('mechanisms') != context['queue']):
        raise ValueError('loop recovery context differs from ledger')
    with ledger.locked(read_only=True):
        ids = ledger.case_ids(2)
        if len(ids) != 1: raise ValueError('loop recovery ledger unavailable')
        history = ledger.history(ledger.case(ids[0]))
    if len(history) != count or history[-1][1] != head: raise ValueError('loop recovery ledger changed')
    receipt_path = output / 'workflow.json'
    if os.path.lexists(receipt_path):
        try:
            envelope = json.loads(integrity.read_regular(receipt_path, 8*1024*1024))
            if envelope != {'status': 'collecting'}:
                _check_interrupted_receipt(envelope, envelope['loop'], output, history)
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as error:
            raise ValueError('saved loop receipt is invalid') from error
    elapsed = float(history[-1][0]['recorded_at'])
    if not math.isfinite(elapsed) or elapsed < 0: raise ValueError('invalid recorded budget')
    previous = context['initial_stage_spent']
    initial = context['initial_elapsed']
    if (not isinstance(previous, dict) or set(previous) != {'construction', 'trials'}
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in previous.values())
            or type(initial) not in (int, float) or not math.isfinite(initial) or initial < 0):
        raise ValueError('invalid initial budget')
    spent, wall, elapsed_before = None, 0, initial
    for event, _ in history:
        timestamp = event['payload'].get('wall_recorded_ns')
        event_elapsed = float(event['recorded_at'])
        if (type(timestamp) is not int or timestamp <= 0 or timestamp < wall or not math.isfinite(event_elapsed)
                or event_elapsed + 0.00001 < elapsed_before):
            raise ValueError('invalid recovery clock')
        wall, elapsed_before = timestamp, event_elapsed
        spent = event['payload'].get('stage_spent')
        if (not isinstance(spent, dict) or set(spent) != set(previous)
                or any(type(v) not in (int, float) or not math.isfinite(v) or v < previous[k] for k, v in spent.items())):
            raise ValueError('invalid recorded stage spend')
        previous = spent
    if sum(spent.values()) > elapsed + 0.00001: raise ValueError('recorded stage spend exceeds elapsed time')
    now = time.time_ns()
    if now < wall: raise ValueError('recovery wall clock moved backwards')
    elapsed += (now - wall) / 1_000_000_000
    options = context['options']
    if (not isinstance(options, dict) or set(options) != set(OPTIONS)
            or options['agent'] not in ('auto', 'codex', 'claude') or context['selected_agent'] not in ('codex', 'claude')
            or any(not isinstance(options[name], str) or not options[name] or len(options[name]) > 4096 or '\x00' in options[name]
                   for name in ('codex', 'claude'))
            or not isinstance(context['binary'], str) or not context['binary']
            or context['binary'] != options[context['selected_agent']]
            or options['scope'] not in ('project', 'global', 'project+global')
            or context['scope_source'] not in ('default', 'command_line', 'user_configuration')
            or type(options['timeout']) not in (int, float) or options['timeout'] != spec.limit_seconds
            or type(options['trial_limit']) is not int or options['trial_limit'] < spec.limit_trials
            or type(options['inherit_env']) is not bool or type(options['with_user_harness']) is not bool or type(options['sandbox_graders']) is not bool
            or not isinstance(options['area'], list) or not all(isinstance(area, str) for area in options['area'])
            or options['focus'] is not None and not isinstance(options['focus'], str)
            or not isinstance(context['explicit'], list) or not all(isinstance(item, str) for item in context['explicit'])
            or not isinstance(context['invocation'], dict) or set(context['invocation']) != set(config.INVOCATION_OPTIONS)
            or not isinstance(context['queue'], list) or not all(isinstance(item, str) and 0 < len(item) <= 128 for item in context['queue'])):
        raise ValueError('invalid recovery options')
    expected_grading = {}
    if options['sandbox_graders']:
        expected_grading = dict(sandbox_graders=True, **{name: options[name] for name in ('cgroup_root', 'image_root', 'image_sha256', 'disk_bytes')})
        if options['permission_audit'] is not None: expected_grading['permission_audit'] = options['permission_audit']
    if context['grading'] != expected_grading: raise ValueError('recovery grading settings differ')
    receipt_names = {'_evaluation_repairs', '_authored_digest_ignored', '_construction_seconds', '_evaluation_reuse',
                     '_reuse', '_observed', '_runtime_selection', '_cost_preview'}
    if not isinstance(context['receipt_context'], dict) or not set(context['receipt_context']) <= receipt_names:
        raise ValueError('invalid recovery receipt context')
    path = project / 'ahl.json'
    settings, configuration = config.load(path) if os.path.lexists(path) else (None, None)
    if configuration != options.get('configuration_sha256'):
        from argparse import Namespace
        from . import entry
        from .errors import LoopError
        from agent_harness_lab.evaluation.areas import parse_registry
        layers = dict(context['loop_layers'])
        layers['project'] = (settings or {}).get('loop')
        layers['areas'] = parse_registry(settings['areas']) if settings and 'areas' in settings else ()
        candidate_args = Namespace(timeout=options['timeout'], trial_limit=options['trial_limit'], project_settings=settings,
                                   _configuration_explicit=frozenset(context['explicit']))
        try:
            changed_spec = entry.spec_for(candidate_args, layers).digest != spec.digest
        except (ValueError, KeyError, TypeError):
            changed_spec = True
        if changed_spec: raise LoopError('resume_spec_changed', 'spec', 'project')
        raise ValueError('project configuration changed')
    if context['allowed'] != (settings['harness_paths'] if settings else None):
        raise ValueError('project scope changed')
    incremental = context['incremental']
    if incremental is not None and (not isinstance(incremental, dict) or incremental.get('output') != str(output)
            or incremental.get('scope') != context['allowed'] or incremental.get('baseline') != context['original']
            or type(incremental.get('recorded')) is not bool):
        raise ValueError('incremental recovery state differs')
    files = workflow.harness_files(project, context['allowed'])
    if (len(files) > 1000 or any(p.is_symlink() for file in files for p in (file, *file.parents) if p.is_relative_to(project))
            or sum(file.stat().st_size for file in files) > 10*1024*1024):
        raise ValueError('unsafe project scope')
    states = {str(file.relative_to(project)): list(workflow.file_state(file)) for file in files}
    if states != context['original']: raise ValueError('project harness changed')
    baseline, plugin = output / 'preparation' / 'baseline', output / 'preparation' / '.harness-toolkit'
    if integrity.digest(baseline) != context['baseline_sha256'] or integrity.digest(plugin) != context['plugin_sha256']:
        raise ValueError('frozen harness or toolkit changed')
    if _hash_file(output / 'analysis-brief.json') != context['brief_sha256']:
        raise ValueError('frozen analysis brief changed')
    bank_dir = output / 'preparation' / 'evaluation-0'
    if (_hash_file(bank_dir / 'manifest.json') != context['manifest_sha256']
            or _hash_file(bank_dir / 'loop-scoring-manifest.json') != context['scoring_sha256']):
        raise ValueError('frozen scoring manifests changed')
    from .adapters import ExecutorRunner
    backend = ExecutorRunner(None, context['selected_agent'], context['binary'], inherit_env=options['inherit_env'], with_user_harness=options['with_user_harness'])
    if backend.runtime_executable_sha256() != context['runtime_binary_sha256']:
        raise ValueError('frozen runtime executable changed')
    runner_path = (assets() / 'skills/harness-self-improvement/scripts/run_evaluation.py').resolve()
    if _hash_file(runner_path) != context['runner_program_sha256']:
        raise ValueError('evaluation runner changed')
    return dict(context=context, context_sha256=context_hash, spec=spec, head=head, events=events,
                elapsed=elapsed, stage_spent=spent, settings=settings, backend=backend)


def resume(args, output):
    """Reconstruct the frozen ports and continue only after validating the ledger and inputs."""
    import time
    from agent_harness_lab import cli, workflow
    from agent_harness_lab.budget_stages import Stages
    from . import controller, improve
    from .adapters import FrozenBankEvaluator

    saved = read(output, args.project)
    context, spec, events = saved['context'], saved['spec'], saved['events']
    for name, value in context['options'].items(): setattr(args, name, value)
    args.project_settings = saved['settings']
    args._configuration_explicit = frozenset(context['explicit'])
    args._scope_source = context['scope_source']
    for name, value in context['receipt_context'].items(): setattr(args, name, value)
    args._configuration_invocation = context['invocation']
    args._analysis_brief = json.loads(integrity.read_regular(output / 'analysis-brief.json', 8*1024*1024))
    args._incremental = context['incremental']
    if args._incremental is not None: args._incremental['output'] = Path(args._incremental['output'])
    stages = Stages(args.timeout, time.monotonic() - saved['elapsed'], construction=args.construction_fraction)
    stages.spent = dict(saved['stage_spent'])
    args._stages, args._budget_started = stages, stages.started
    runner = cli.evaluator()
    prepare = output / 'preparation'
    baseline, plugin, manifest = prepare / 'baseline', prepare / '.harness-toolkit', prepare / 'evaluation-0' / 'manifest.json'
    if runner.digest(baseline) != context['baseline_runner_sha256']: raise ValueError('frozen baseline changed')
    original = {name: tuple(value) for name, value in context['original'].items()}
    grading = dict(context['grading'])
    for name in ('cgroup_root', 'image_root'):
        if grading.get(name) is not None: grading[name] = Path(grading[name])
    backend = saved['backend']
    backend.evaluate = runner.evaluate
    backend.options = dict(inherit_env=args.inherit_env, with_user_harness=args.with_user_harness, config=args.project / 'ahl.json',
                           deadline=lambda: time.monotonic() + max(0.0, stages.remaining()), **grading)
    baseline_digest = context['baseline_sha256']
    folders = {baseline_digest: baseline}
    registry = [item['id'] for item in (args.project_settings or {}).get('areas', [])]
    evaluator = FrozenBankEvaluator(manifest, backend, folders.__getitem__, output, spec=spec,
        areas=dict(declared=registry, selected=list(args.area or ())) if registry else None)
    if evaluator.frozen_inputs != context['frozen_inputs_sha256']: raise ValueError('frozen evaluation inputs changed')
    if improve.mechanisms(args) != context['queue']: raise ValueError('frozen mechanism queue changed')
    state = dict(args=args, stages=stages, runner=runner, output=output, prepare=prepare, started=stages.started,
                 progress=getattr(args, '_progress', lambda *a, **kw: None), baseline=baseline,
                 baseline_hash=context['baseline_runner_sha256'], baseline_digest=baseline_digest, original=original,
                 allowed=context['allowed'], agent=context['selected_agent'], binary=context['binary'], plugin=plugin,
                 manifest_path=manifest, snapshots=folders, states={baseline_digest: original}, frozen={},
                 queue=context['queue'], spec=spec, model_calls=[])
    proposer = improve.PreparationProposer(state, lambda: evaluator)
    current_base, last_attempt, abandoned = None, -1, None
    for kind, payload in events:
        if kind == 'round_started':
            current_base = payload.get('base')
            if current_base not in folders: raise ValueError('round base is unavailable')
        elif kind == 'candidate_frozen':
            frozen = payload.get('recovery_candidate')
            if (not isinstance(frozen, dict) or set(frozen) != {'attempt', 'deferrals', 'model_calls'}
                    or type(frozen['attempt']) is not int or not last_attempt < frozen['attempt'] <= 10000
                    or not isinstance(frozen['deferrals'], list) or not isinstance(frozen['model_calls'], list)):
                raise ValueError('candidate recovery record is invalid')
            last_attempt = frozen['attempt']
            candidate = output / f'candidate-{last_attempt}'
            snapshot = payload.get('snapshot')
            if integrity.digest(candidate) != snapshot: raise ValueError('frozen candidate changed')
            state['snapshots'][snapshot] = candidate
            state['states'][snapshot] = workflow.candidate_states(candidate, original)
            state['frozen'][snapshot] = dict(attempt=last_attempt)
            evaluator.bases[snapshot] = current_base
            proposer.deferrals = list(frozen['deferrals'])
            state['model_calls'] = list(frozen['model_calls'])
        elif kind == 'candidate_evaluated' and payload.get('abandoned') is True:
            abandoned = payload.get('round')
        elif kind == 'candidate_started':
            abandoned = None
        elif kind in ('baseline_evaluated', 'candidate_evaluated'):
            observed = payload.get('recovery_evaluation')
            keys = {'index', 'snapshot', 'against', 'artifact_sha256'}
            if (not isinstance(observed, dict) or set(observed) != keys or type(observed['index']) is not int
                    or observed['index'] != len(evaluator.runs) or observed['snapshot'] != payload.get('snapshot')
                    or observed['snapshot'] not in folders or (kind == 'baseline_evaluated' and observed['against'] is not None)
                    or (kind == 'candidate_evaluated' and observed['against'] != current_base)):
                raise ValueError('evaluation recovery record is invalid')
            artifact, artifact_hash = record(output / f'loop-evaluation-{observed["index"]}.json', 8*1024*1024)
            if (artifact_hash != observed['artifact_sha256'] or set(artifact) != {'index', 'snapshot', 'against', 'guard', 'pairs', 'investigation', 'area_gate'}
                    or any(artifact[name] != observed[name] for name in ('index', 'snapshot', 'against'))):
                raise ValueError('evaluation recovery artifact changed')
            restored = dict(artifact, evaluation=controller._restored(payload.get('evaluation')))
            evaluator.runs.append(restored)
            abandoned = None
            if kind == 'baseline_evaluated': evaluator.baseline = baseline_digest
    proposer.attempt = last_attempt + 1
    proposer.resume_context_sha256 = saved['context_sha256']
    charged = improve.Charged(evaluator, stages)
    charged.abandoned_to_preserve = abandoned
    ledger = LoopLedger(workflow.state_root() / 'loops' / output.name, args.project)
    carried = context['carried']
    result = controller.resume(spec, charged, proposer, ledger, stages.clock, spent=saved['elapsed'],
        dispositions=carried and carried['dispositions'], expected_head=saved['head'])
    result_context = dict(output=output, stages=stages, agent=context['selected_agent'], data={'execution': context['analysis']})
    found = improve._result(args, result_context, state, evaluator, proposer, ledger, spec, result, carried)
    workflow.construction_receipt(found, args)
    from agent_harness_lab import incremental
    incremental.attach(found, args._incremental, original)
    from agent_harness_lab.scope import combine
    found = combine(args, [('project', found)], base=found)
    workflow.write_json(output / 'workflow.json', found)
    return workflow._sealed(found, args)
