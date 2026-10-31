"""Credential-free task-bank export and frozen grader falsification controls."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from .integrity import contained,digest,tree
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
                          fixture=str(target.relative_to(output)),prompt=prompt,
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


def audit_graders(bank_path, *, cooperative=False):
    if not cooperative:
        raise ValueError('grader audit requires explicit trusted cooperative mode until sandbox integration is available')
    bank_path=Path(bank_path); validate(bank_path)
    tasks=json.loads(bank_path.read_text())['tasks']; results=[]
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
                run=subprocess.run([sys.executable,str(bundle/'grade.py.txt'),str(workspace),str(bundle/'expected.json')],
                    env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=5)
            envelope=json.loads(run.stdout)
            expected=control['state'] in ('complete','alternative_valid')
            if envelope.get('valid') is not True or envelope.get('outcome') is not expected or run.returncode!=(0 if expected else 1):
                raise ValueError('grader falsification failed: '+task['task_id']+'/'+control['state'])
            results.append(dict(task=task['task_id'],control=control['state'],status='passed'))
        if digest(bundle)!=frozen: raise ValueError('grader bundle mutated during audit')
    return dict(status='controls_passed',checks=results,real_agent_trials='not_run',independence='unverified')
