"""Private filename-only feedback-sensor candidates; no command or execution claims."""
import os
from pathlib import PurePosixPath

from agent_harness_lab.sources import harness_inventory

PROFILE='ahl-feedback-sensor-candidates-v1'
CONFIGS={
    'pytest.ini':('tests','local'), 'tox.ini':('tests','local'), 'pyproject.toml':('python_config','local'),
    'ruff.toml':('lint_format','local'), '.ruff.toml':('lint_format','local'),
    '.eslintrc':('lint_format','local'), '.eslintrc.json':('lint_format','local'),
    'eslint.config.js':('lint_format','local'), 'eslint.config.mjs':('lint_format','local'),
    'biome.json':('lint_format','local'), 'biome.jsonc':('lint_format','local'),
    '.prettierrc':('lint_format','local'), '.prettierrc.json':('lint_format','local'),
    'mypy.ini':('type_check','local'), '.mypy.ini':('type_check','local'),
    'pyrightconfig.json':('type_check','local'), 'tsconfig.json':('type_check','local'),
    'Taskfile.yml':('task_runner','local'), 'Taskfile.yaml':('task_runner','local'),
    'Makefile':('task_runner','local'), 'makefile':('task_runner','local'), 'package.json':('task_runner','local'),
    '.pre-commit-config.yaml':('git_hook','pre_commit'), 'lefthook.yml':('git_hook','pre_commit'),
    'lefthook.yaml':('git_hook','pre_commit'),
}


def category(path):
    parts=PurePosixPath(path).parts;name=parts[-1]
    if name in CONFIGS:return CONFIGS[name]
    if ((name.startswith('test_') and name.endswith('.py')) or name.endswith(('_test.py','.test.js','.test.ts',
            '.test.tsx','.spec.js','.spec.ts','.spec.tsx'))):
        return 'tests','local'
    return None


class _MetadataWalk(harness_inventory._Walk):
    def __init__(self):
        super().__init__({});self.candidates=[]

    def file(self, descriptor, name, path, info, root):
        found=category(path)
        if found is None:return
        if info.st_nlink!=1:
            self.refuse(root,path,'hardlink');return
        self.candidates.append(dict(path=path,category=found[0],stage=found[1],evidence='filename_only',
                                    content='not_read',command='unknown',execution='unknown'))


def capture(checkout):
    """List bounded checkout filename candidates; refused or blocked walks stay unknown."""
    walk=_MetadataWalk()
    try:
        walk.root(os.path.abspath(checkout),'checkout','checkout')
    except (harness_inventory._Blocked,OSError,ValueError):
        return dict(profile=PROFILE,status='blocked',candidates=[],coverage='unknown',consistency='not_checked',
                    runtime_execution='unknown',publication_allowed=False)
    for path in sorted(walk.directories):
        if PurePosixPath(path).name in ('test','tests'):
            walk.candidates.append(dict(path=path,category='tests',stage='local',evidence='directory_name_only',
                                        content='not_read',command='unknown',execution='unknown'))
    refusals=[row for row in walk.refusals if row.reason!='vcs_metadata']
    return dict(profile=PROFILE,status='completed',candidates=sorted(walk.candidates,key=lambda row:(row['path'],row['category'])),
                coverage='unknown' if refusals else 'walked_checkout_metadata_only',consistency='not_checked',
                runtime_execution='unknown',publication_allowed=False)
