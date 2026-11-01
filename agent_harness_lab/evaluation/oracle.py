"""Versioned identity of oracle contents, argv and artifact selection, not runtime proof."""
import hashlib
import errno
import json
from pathlib import Path
import re
from .integrity import contained, digest, read_regular, tree

MAX_TASKS = 1000
MAX_BYTES = 64*1024*1024
MAX_FILES = 4096
MAX_ARGV_BYTES = 32768


def _text(value, maximum):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('oracle identity requires bounded nonempty strings')
    try: raw = value.encode('utf-8')
    except UnicodeError: raise ValueError('oracle identity requires valid UTF-8') from None
    if len(raw)>maximum: raise ValueError('oracle identity string bound exceeded')
    return len(raw)


def _argument(root, folder, value, executable):
    prefix, path = '', value
    if value.startswith('-'):
        if '=' not in value: return {'literal':value}
        prefix, path = value.split('=',1); prefix += '='
        if not path: return {'literal':value}
    if '{workspace}' in path or '{trace}' in path: return {'literal':value}
    candidate = Path(path)
    if '..' in candidate.parts: raise ValueError('oracle argv cannot cross parent directories')
    if not candidate.is_absolute(): candidate = root/candidate
    if candidate.is_relative_to(folder):
        relative = str(candidate.relative_to(folder))
        target = folder if relative=='.' else contained(folder,relative)
        if not target.is_dir(): read_regular(target,10*1024*1024)
        return {'oracle':relative,'prefix':prefix}
    try: exists = candidate.exists() or candidate.is_symlink()
    except OSError as error:
        if error.errno==errno.ENAMETOOLONG: return {'literal':value}
        raise ValueError('cannot inspect oracle argv reference') from None
    if not executable and exists:
        raise ValueError('grader argv reference is outside its declared oracle bundle')
    return {'literal':value}


def identity(root, tasks, bundles):
    """Hash task IDs, complete current bundles and relocation-stable grader argv."""
    if not isinstance(root,(str,Path)): raise ValueError('oracle root must be an explicit path')
    root = Path(root).absolute()
    if '..' in root.parts: raise ValueError('oracle root must be canonical')
    _text(str(root),4096)
    if not isinstance(tasks,(list,tuple)) or not 0<len(tasks)<=MAX_TASKS:
        raise ValueError('oracle identity requires bounded nonempty tasks')
    if not isinstance(bundles,dict) or len(bundles)!=len(tasks):
        raise ValueError('every task requires its complete declared oracle bundle')
    indexed = {}
    for task in tasks:
        if not isinstance(task,dict): raise ValueError('oracle task must be an object')
        name = task.get('id'); _text(name,128)
        if name in ('.','..') or '/' in name or '\\' in name or any(ord(c)<32 for c in name) or name in indexed:
            raise ValueError('invalid or duplicate oracle task ID')
        argv = task.get('grader')
        if not isinstance(argv,list) or not 0<len(argv)<=128:
            raise ValueError('oracle grader requires bounded explicit argv')
        if sum(_text(arg,4096) for arg in argv)>MAX_ARGV_BYTES:
            raise ValueError('oracle argv byte bound exceeded')
        allowed = None
        if 'artifacts' in task:
            from .workspace import names
            allowed = names(task['artifacts'])
        indexed[name] = (argv,allowed)
    if set(indexed)!=set(bundles): raise ValueError('oracle bundle/task identities differ')
    remaining_bytes, remaining_files = MAX_BYTES, MAX_FILES
    verified = {}; result = {}
    for name in sorted(indexed):
        declaration = bundles[name]
        if not isinstance(declaration,dict) or set(declaration)!={'path','digest'}:
            raise ValueError('oracle declaration requires exact path and digest fields')
        expected = declaration['digest']
        if not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None:
            raise ValueError('oracle digest must be exact lowercase SHA-256')
        folder = contained(root,declaration['path'])
        if folder not in verified:
            files = tree(folder,max_files=min(1000,remaining_files),max_bytes=min(10*1024*1024,remaining_bytes))
            remaining_files -= len(files)
            remaining_bytes -= sum(contained(folder,p).stat().st_size for p in files)
            if remaining_bytes<0 or digest(folder)!=expected:
                raise ValueError('declared oracle integrity changed')
            verified[folder] = expected
        elif verified[folder]!=expected:
            raise ValueError('conflicting oracle declarations')
        argv, allowed = indexed[name]
        result[name] = dict(bundle_sha256=expected,artifacts=allowed,argv=[_argument(root,folder,arg,index==0)
            for index,arg in enumerate(argv)])
    if any(digest(folder)!=expected for folder,expected in verified.items()):
        raise ValueError('oracle changed during identity capture')
    raw = json.dumps(dict(schema_version=1,algorithm='declared-oracle-argv-v1',tasks=result),
                     sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
