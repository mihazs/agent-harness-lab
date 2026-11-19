"""Bounded, private root-file observations; no script execution or stale-command claim."""
import json
import re

from agent_harness_lab.audit import observation_inputs
from agent_harness_lab.sources.git_view import Folder

PROFILE='ahl-npm-test-observation-v1'
MAX_BYTES=256*1024
_MENTION=re.compile(r'(?<![\w-])npm[ \t]+(?:run[ \t]+)?test(?![\w/:-]|\.[\w])')


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def capture(project, inventory, task_cwd):
    """Compare root instruction text and root package script declaration only."""
    result=dict(profile=PROFILE,status='unknown',instruction_mention='unknown',test_script='unknown',
                scope='checkout_root_files_only',observed_loading='unknown',runtime_execution='unknown',
                snapshot_relation='separate_observation',publication_allowed=False)
    try:
        found=observation_inputs.read(project,inventory,task_cwd,'package.json',Folder,MAX_BYTES)
        if found is None: return result
        instruction,package=found
        text=instruction.decode('utf-8');data=json.loads(package,object_pairs_hook=_unique)
        if not isinstance(data,dict): return result
        scripts=data.get('scripts',{})
        if not isinstance(scripts,dict) or 'test' in scripts and not isinstance(scripts['test'],str): return result
        result.update(status='observed',instruction_mention='present' if _MENTION.search(text) else 'not_observed',
                      test_script='declared' if 'test' in scripts else 'not_declared')
    except (ValueError,OSError,TimeoutError,UnicodeError,RecursionError):
        pass
    return result
