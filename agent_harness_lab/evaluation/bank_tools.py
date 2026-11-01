"""Credential-free task-bank export and frozen grader falsification controls."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .integrity import artifacts,contained,digest,tree
from .task_bank import validate



def materialize_sources(workspace):
    source=workspace/'source_files.json'
    if not source.exists(): return
    files=json.loads(source.read_text())
    if not isinstance(files,dict) or len(files)>100: raise ValueError('invalid source fixture')
    for name,content in files.items():
        if not isinstance(content,str) or len(content.encode())>1024*1024 or not name.startswith('app/') or not name.endswith('.py'):
            raise ValueError('invalid bounded synthetic source')
        target=contained(workspace,name)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content); target.chmod(0o644)


def export_manifest(bank_path,output,max_trials=24):
    bank_path,output=Path(bank_path),Path(output)
    if output.absolute().is_relative_to(bank_path.parent.resolve()) or bank_path.parent.resolve().is_relative_to(output.absolute()):
        raise ValueError('bank export and source must not overlap')
    if any(p.is_symlink() for p in (output,*output.absolute().parents)):
        raise ValueError('bank export crosses symlink')
    reviewed=validate(bank_path)
    value=json.loads(bank_path.read_text())
    if 2*len(value['tasks'])>max_trials: raise ValueError('bank export exceeds caller trial ceiling')
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    tasks=[]; controls=[]; bundles={}
    for task in value['tasks']:
        ident=task['task_id']
        if '/' in ident or '\\' in ident or ident in ('.','..'): raise ValueError('unsafe exported task ID')
        fixture=contained(bank_path.parent,task['fixture']); bundle=contained(bank_path.parent,task['grader_bundle'])
        target=output/'fixtures'/ident; grader=output/'graders'/ident
        shutil.copytree(fixture,target); shutil.copytree(bundle,grader)
        if digest(target)!=task['fixture_digest'] or digest(grader)!=task['grader_digest']:
            raise ValueError('source changed during bank export')
        materialize_sources(target)
        prompt=json.loads((target/'task.json').read_text())['prompt']
        tasks.append(dict(id=ident,root=task['root_id'],family=task['family'],split={'calibration':'regression','certification':'heldout','transfer':'heldout'}.get(task['partition'],'development'),
                          fixture=str(target.relative_to(output)),prompt=prompt,artifacts=list(task['artifacts']),
                          grader=[sys.executable,str(grader.resolve()/'grade.py.txt'),'{workspace}',str(grader.resolve()/'expected.json')]))
        audit=json.loads((grader/'controls.json').read_text())
        for control in audit:
            materialize_sources(contained(grader,control['fixture']))
            if control['state'] in ('broken','partial','complete'):
                controls.append(dict(task=ident,state=control['state'],fixture=str((grader/control['fixture']).relative_to(output))))
        bundles[ident]=dict(path=str(grader.relative_to(output)),digest=digest(grader))
    if validate(bank_path)['bank_sha256']!=reviewed['bank_sha256']:
        raise ValueError('bank changed during export')
    manifest=dict(schema_version=1,profile='descriptive_smoke',tasks=tasks,grader_self_tests=controls,
                  grader_bundles=bundles,max_trials=max_trials,budget_seconds=300,timeout_seconds=10,
                  bank_digest=reviewed['bank_sha256'],independence_verified=reviewed['certification_eligible'],automatic_promotion=False)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def regrade_artifacts(workspace, allowlist, bundle, expected_digest, backend, *, limits=None):
    """Worker must be stopped before extraction; never trust its claimed result."""
    import tempfile
    from dataclasses import replace
    from .sandbox import Limits
    started = time.monotonic()
    limits = limits or Limits(seconds=5)
    limits.validate()
    if digest(bundle) != expected_digest:
        raise ValueError('grader bundle integrity changed before regrading')
    extracted = artifacts(workspace, allowlist)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        oracle, fixture, scratch = root/'oracle', root/'fixture', root/'scratch'
        shutil.copytree(bundle, oracle)
        if digest(oracle) != expected_digest:
            raise ValueError('grader bundle changed during copying')
        fixture.mkdir(); scratch.mkdir()
        for name, data in extracted.items():
            target = contained(fixture, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        remaining = limits.seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError('regrading budget exhausted before launch')
        run = backend.run(['/usr/bin/python3', '-I', '/inputs/oracle/grade.py.txt',
                           '/inputs/fixture', '/inputs/oracle/expected.json'],
                          scratch, {'fixture': fixture, 'oracle': oracle}, 'grader',
                          replace(limits, seconds=remaining))
    if digest(bundle) != expected_digest or run.get('inputs_unchanged') is not True:
        raise ValueError('grader inputs changed during regrading')
    outcome = None
    if run.get('status') in ('completed', 'failed'):
        try:
            envelope = json.loads(run['stdout'])
            if (type(envelope) is dict and type(envelope.get('schema_version')) is int
                    and envelope['schema_version'] == 1 and envelope.get('valid') is True
                    and type(envelope.get('outcome')) is bool
                    and all(isinstance(envelope.get(k), str) and 0 < len(envelope[k]) <= 128
                            for k in ('grader_id', 'grader_version'))
                    and type(run.get('exit_code')) is int
                    and run['exit_code'] == (0 if envelope['outcome'] else 1)
                    and run['status'] == ('completed' if envelope['outcome'] else 'failed')):
                outcome = envelope['outcome']
        except (ValueError, TypeError, KeyError):
            pass
    return dict(status='grader_error' if outcome is None else ('passed' if outcome else 'failed'),
                outcome=outcome, execution=run, automatic_promotion=False)


def audit_graders(bank_path, *, cooperative=False, sandbox=False, budget_seconds=300):
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError('invalid grader audit budget')
    started = time.monotonic()
    deadline = started + budget_seconds
    if cooperative and sandbox:
        raise ValueError('choose one grader execution mode')
    if not cooperative and not sandbox:
        raise ValueError('grader audit requires explicit cooperative or sandbox execution mode')
    bank_path=Path(bank_path); validate(bank_path)
    result = dict(status='incomplete', checks=[], real_agent_trials='not_run', independence='unverified',
                  execution_mode='sandbox' if sandbox else 'cooperative', automatic_promotion=False,
                  budget_seconds=budget_seconds)
    if time.monotonic() >= deadline:
        return dict(result, status='budget_exhausted', elapsed_seconds=time.monotonic()-started)
    if sandbox:
        from .sandbox import SandboxBackend
        backend = SandboxBackend()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return dict(result, status='budget_exhausted', elapsed_seconds=time.monotonic()-started)
        capability = backend.capability(seconds=min(3, remaining))
        if time.monotonic() >= deadline:
            return dict(result, status='budget_exhausted', capability=capability, elapsed_seconds=time.monotonic()-started)
        if capability['status'] != 'available':
            return dict(result, status='blocked', capability=capability, elapsed_seconds=time.monotonic()-started)
    tasks=json.loads(bank_path.read_text())['tasks']; results=result['checks']
    for task in tasks:
        bundle=contained(bank_path.parent,task['grader_bundle']); frozen=digest(bundle)
        controls=json.loads((bundle/'controls.json').read_text())
        states={c['state'] for c in controls}
        if not {'broken','partial','complete','alternative_valid'}<=states:
            raise ValueError('grader audit needs falsifiers and a valid alternative')
        for control in controls:
            fixture=contained(bundle,control['fixture']); tree(fixture)
            import tempfile
            with tempfile.TemporaryDirectory() as folder:
                workspace=Path(folder)/'work'; shutil.copytree(fixture,workspace); materialize_sources(workspace)
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return dict(result, status='budget_exhausted', elapsed_seconds=time.monotonic()-started)
                try:
                    if sandbox:
                        from .sandbox import Limits
                        grade = regrade_artifacts(workspace, task['artifacts'], bundle, frozen, backend,
                                                  limits=Limits(seconds=min(5, remaining)))
                    else:
                        run=subprocess.run([sys.executable,str(bundle/'grade.py.txt'),str(workspace),str(bundle/'expected.json')],
                            env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=min(5, remaining))
                except (TimeoutError, subprocess.TimeoutExpired):
                    return dict(result, status='budget_exhausted' if time.monotonic() >= deadline else 'grader_error',
                                task=task['task_id'], control=control['state'], elapsed_seconds=time.monotonic()-started)
            if time.monotonic() >= deadline:
                return dict(result, status='budget_exhausted', elapsed_seconds=time.monotonic()-started)
            envelope = dict(valid=grade['outcome'] is not None,outcome=grade['outcome']) if sandbox else json.loads(run.stdout)
            expected=control['state'] in ('complete','alternative_valid')
            code = grade['execution']['exit_code'] if sandbox else run.returncode
            if envelope.get('valid') is not True or envelope.get('outcome') is not expected or code!=(0 if expected else 1):
                raise ValueError('grader falsification failed: '+task['task_id']+'/'+control['state'])
            checked = dict(task=task['task_id'],control=control['state'],status='passed')
            if sandbox:
                checked['regrade'] = grade
            results.append(checked)
        if digest(bundle)!=frozen: raise ValueError('grader bundle mutated during audit')
    return dict(result, status='controls_passed' if time.monotonic() < deadline else 'budget_exhausted',
                elapsed_seconds=time.monotonic()-started)
