"""Bounded, private root-file observations; no script execution or stale-command claim."""
import hashlib
import json
import re
import time

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
    if task_cwd!='.' or inventory.status!='declared': return result
    source={'codex':'AGENTS.md','claude-code':'CLAUDE.md'}.get(inventory.runtime)
    nodes=[node for node in inventory.nodes if node.origin=='checkout' and node.kind=='instruction' and node.path==source]
    if (len(nodes)!=1 or nodes[0].declared_state!='present' or nodes[0].parse_status!='parsed'): return result
    try:
        with Folder(project,time.monotonic()+30) as folder:
            instruction=folder.read(source,MAX_BYTES)
            package=folder.read('package.json',MAX_BYTES)
            folder.verify()
        if instruction is None or package is None or hashlib.sha256(instruction).hexdigest()!=nodes[0].content_sha256:
            return result
        text=instruction.decode('utf-8');data=json.loads(package,object_pairs_hook=_unique)
        if not isinstance(data,dict): return result
        scripts=data.get('scripts',{})
        if not isinstance(scripts,dict) or 'test' in scripts and not isinstance(scripts['test'],str): return result
        result.update(status='observed',instruction_mention='present' if _MENTION.search(text) else 'not_observed',
                      test_script='declared' if 'test' in scripts else 'not_declared')
    except (ValueError,OSError,TimeoutError,UnicodeError,RecursionError):
        pass
    return result
