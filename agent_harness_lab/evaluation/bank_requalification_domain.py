"""Untrusted closed-input declarations; parsing never issues execution authority."""
import json
from pathlib import PurePosixPath
import re
import unicodedata

from agent_harness_lab.telemetry.adapters import CAPABILITIES
from agent_harness_lab.telemetry.claude_native import VERSION as CLAUDE_VERSION
from agent_harness_lab.telemetry.codex_rollout import VERSION as CODEX_VERSION

PROFILE='conditional-bank-closed-input-domain-v2'
PROJECT_INPUT_PROFILE='ahl-controlled-agents-project-inputs-v1'
PROJECT_INPUT_PATHS=('AGENTS.md','ahl.json')
MAX_BYTES=256*1024
HASH_FIELDS=('project_sha256','baseline_sha256','engine_sha256','methodology_sha256','history_identity_sha256')
SOURCE_FIELDS={'kind','selection','snapshot_sha256','format','version','session'}
EVENT_VERSIONS={
    'normalized':(None,), 'ai-memory':(None,),
    'codex':(None,CAPABILITIES['codex']['version'],CODEX_VERSION),
    'claude':(None,CAPABILITIES['claude']['version'],CLAUDE_VERSION),
}


def _text(value,maximum):
    if type(value) is not str or not value or any(unicodedata.category(c) in ('Cc','Cf','Cs') for c in value):
        raise ValueError('bounded unambiguous domain text required')
    if len(value.encode('utf-8'))>maximum:raise ValueError('domain text exceeds capacity')
    return value


def _hash(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('exact lowercase domain SHA-256 required')
    return value


def _selection(value):
    value=_text(value,4096);path=PurePosixPath(value)
    if (not path.is_absolute() or value.startswith('//') or str(path)!=value or '..' in path.parts
            or any(c in value for c in '\\*?[]{}')):
        raise ValueError('exact normalized absolute source selection required')
    return value


def _source(value):
    if type(value) is not dict or set(value)!=SOURCE_FIELDS:raise ValueError('exact domain source fields required')
    kind=value['kind'];source_format=value['format'];version=value['version'];session=value['session']
    if kind not in ('events_file','native_source_root') or type(source_format) is not str or source_format not in EVENT_VERSIONS:
        raise ValueError('supported explicit domain source required')
    if version is not None:_text(version,128)
    if session is not None:
        _text(session,256)
        if session in ('.','..') or any(c in session for c in '/\\*?[]{}'):
            raise ValueError('exact native session identity required')
    if kind=='native_source_root':
        if source_format!='codex' or version!=CODEX_VERSION:
            raise ValueError('native source root requires the pinned Codex profile')
    elif session is not None or version not in EVENT_VERSIONS[source_format]:
        raise ValueError('events source format, version or session is ambiguous')
    return dict(kind=kind,selection=_selection(value['selection']),snapshot_sha256=_hash(value['snapshot_sha256']),
        format=source_format,version=version,session=session)


def _project_inputs(value):
    if (type(value) is not dict or set(value)!={'profile','root','paths'}
            or type(value['profile']) is not str or value['profile']!=PROJECT_INPUT_PROFILE
            or type(value['paths']) is not list or value['paths']!=list(PROJECT_INPUT_PATHS)
            or any(type(path) is not str for path in value['paths'])):
        raise ValueError('exact supported project input read set required')
    return dict(profile=PROJECT_INPUT_PROFILE,root=_selection(value['root']),paths=list(PROJECT_INPUT_PATHS))


def parse(value):
    """Validate lexical scope and adapter selection without reading selected paths."""
    if type(value) is not dict or set(value)!={'schema_version','profile','sources','project_inputs',*HASH_FIELDS}:
        raise ValueError('exact closed-input domain fields required')
    if type(value['schema_version']) is not int or value['schema_version']!=2 or value['profile']!=PROFILE:
        raise ValueError('unsupported closed-input domain schema')
    sources=value['sources']
    if type(sources) is not list or not 1<=len(sources)<=32:
        raise ValueError('one to thirty-two explicit sources required; discovery is unavailable')
    result=dict(schema_version=2,profile=PROFILE,**{name:_hash(value[name]) for name in HASH_FIELDS})
    result['project_inputs']=_project_inputs(value['project_inputs'])
    result['sources']=[_source(source) for source in sources]
    if len({source['selection'] for source in result['sources']})!=len(sources):
        raise ValueError('duplicate domain source selection')
    if len(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8'))>MAX_BYTES:
        raise ValueError('closed-input domain exceeds metadata capacity')
    return result
