"""Closed-input syntax is not a source reader, trusted policy or capability."""
from copy import deepcopy
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import bank_requalification_domain as domain


def declaration():
    return dict(schema_version=2,profile=domain.PROFILE,**{name:'a'*64 for name in domain.HASH_FIELDS},
        project_inputs=dict(profile='ahl-controlled-agents-project-inputs-v1',root='/synthetic/project',paths=['AGENTS.md','ahl.json']),
        sources=[dict(kind='events_file',selection='/synthetic/nonexistent/events.jsonl',
            snapshot_sha256=hashlib.sha256(b'').hexdigest(),format='normalized',version=None,session=None)])


VALID_INPUTS=dict(profile='ahl-controlled-agents-project-inputs-v1',root='/synthetic/project',paths=['AGENTS.md','ahl.json'])


def test_explicit_empty_event_content_is_a_valid_pinned_representation():
    value=declaration();parsed=domain.parse(value)
    assert parsed == value
    assert parsed['sources'][0]['snapshot_sha256'] == hashlib.sha256(b'').hexdigest()
    assert parsed is not value;assert parsed['sources'] is not value['sources']
    parsed['sources'][0]['selection']='/synthetic/changed'
    assert value['sources'][0]['selection'] == '/synthetic/nonexistent/events.jsonl'


def test_project_read_set_is_required_fixed_and_copied():
    value=declaration();value.pop('project_inputs')
    with pytest.raises(ValueError):domain.parse(value)
    value=declaration();parsed=domain.parse(value)
    assert parsed['project_inputs'] == VALID_INPUTS
    assert parsed['project_inputs'] is not value['project_inputs']
    parsed['project_inputs']['paths'].append('unreviewed')
    assert value['project_inputs'] == VALID_INPUTS


def _without_root():
    missing_root=dict(VALID_INPUTS);missing_root.pop('root')
    return missing_root


@pytest.mark.parametrize('inputs',[
    pytest.param(None,id='none'),pytest.param({},id='empty'),pytest.param(_without_root(),id='missing-root'),
    pytest.param(dict(VALID_INPUTS,profile='whole-checkout'),id='whole-checkout-profile'),
    pytest.param(dict(VALID_INPUTS,paths=['ahl.json','AGENTS.md']),id='reordered-paths'),
    pytest.param(dict(VALID_INPUTS,paths=['AGENTS.md']),id='missing-path'),
    pytest.param(dict(VALID_INPUTS,paths=['AGENTS.md','ahl.json','src']),id='extra-path'),
    pytest.param(dict(VALID_INPUTS,paths=('AGENTS.md','ahl.json')),id='tuple-paths'),
    pytest.param(dict(VALID_INPUTS,paths=['*']),id='wildcard-path'),
    pytest.param(dict(VALID_INPUTS,recursive=True),id='recursive-flag')])
def test_project_read_set_refuses_other_inputs(inputs):
    value=declaration();value['project_inputs']=inputs
    with pytest.raises(ValueError):domain.parse(value)


@pytest.mark.parametrize('root',[
    pytest.param('relative/project',id='relative'),pytest.param('//synthetic/project',id='double-slash'),
    pytest.param('/synthetic/../project',id='parent-segment'),pytest.param('/synthetic/./project',id='dot-segment'),
    pytest.param('/synthetic/project/',id='trailing-slash'),pytest.param('/synthetic/project*',id='wildcard'),
    pytest.param('/synthetic/hidden​',id='zero-width-space')])
def test_project_read_set_refuses_noncanonical_roots(root):
    value=declaration();value['project_inputs']['root']=root
    with pytest.raises(ValueError):domain.parse(value)


SUPPORTED_VERSIONS={'normalized':(None,), 'ai-memory':(None,),
    'codex':(None,domain.CAPABILITIES['codex']['version'],domain.CODEX_VERSION),
    'claude':(None,domain.CAPABILITIES['claude']['version'],domain.CLAUDE_VERSION)}


@pytest.mark.parametrize(('source_format','version'),[
    pytest.param(source_format,version,id=f'{source_format}-{index}-{version}')
    for source_format,supported in SUPPORTED_VERSIONS.items() for index,version in enumerate(supported)])
def test_supported_event_adapters_preserve_explicit_version_selection(source_format,version):
    value=declaration();value['sources'][0].update(format=source_format,version=version)
    assert domain.parse(value) == value


def test_native_root_uses_existing_pinned_codex_contract():
    for session in (None,'synthetic-thread-17'):
        value=declaration();value['sources'][0].update(kind='native_source_root',format='codex',version=domain.CODEX_VERSION,session=session)
        assert domain.parse(value) == value


def test_source_selection_never_reads_or_resolves_the_filesystem():
    with patch.object(Path,'stat',side_effect=AssertionError('no filesystem reads')), \
            patch.object(Path,'resolve',side_effect=AssertionError('no path resolution')), \
            patch.object(Path,'is_symlink',side_effect=AssertionError('no symlink inspection')), \
            patch.object(Path,'home',side_effect=AssertionError('no source discovery')):
        assert domain.parse(declaration()) == declaration()


@pytest.mark.parametrize('sources',[
    pytest.param([],id='empty-list'),pytest.param((),id='empty-tuple'),pytest.param(None,id='none'),
    pytest.param(True,id='bool')])
def test_empty_or_duplicate_sources_cannot_enable_autodiscovery(sources):
    value=declaration();value['sources']=sources
    with pytest.raises(ValueError):domain.parse(value)


def test_duplicate_or_too_many_sources_are_refused():
    value=declaration();value['sources']*=2
    with pytest.raises(ValueError,match='duplicate'):domain.parse(value)
    value=declaration();value['sources']=[dict(value['sources'][0],selection='/synthetic/'+str(i)) for i in range(33)]
    with pytest.raises(ValueError):domain.parse(value)


@pytest.mark.parametrize('path',[
    pytest.param(path,id=repr(path)) for path in (
        'relative/events','~/events','//host/events','/synthetic//events','/synthetic/./events',
        '/synthetic/../events','/synthetic/events/','/synthetic/events*','/synthetic/?',
        '/synthetic/[ab]','/synthetic/{a,b}','/synthetic/back\\slash','/synthetic/new\nline',
        '/synthetic/null\0byte','/synthetic/del\x7f','/synthetic/hidden​','/synthetic/\ud800')])
def test_noncanonical_paths_wildcards_and_controls_are_refused(path):
    value=declaration();value['sources'][0]['selection']=path
    with pytest.raises(ValueError):domain.parse(value)


def test_overlong_source_selection_is_refused():
    value=declaration();value['sources'][0]['selection']='/'+'x'*4096
    with pytest.raises(ValueError):domain.parse(value)


@pytest.mark.parametrize(('field','invalid'),[
    pytest.param(field,invalid,id=f'{field}-{label}')
    for field in (*domain.HASH_FIELDS,'snapshot_sha256')
    for label,invalid in (('none',None),('bool',True),('int',1),('uppercase','A'*64),('short','a'*63),('non-hex','g'*64))])
def test_hashes_require_exact_lowercase_hex_strings(field,invalid):
    value=declaration();target=value['sources'][0] if field=='snapshot_sha256' else value
    target[field]=invalid
    with pytest.raises(ValueError):domain.parse(value)


@pytest.mark.parametrize('schema',[
    pytest.param(True,id='true'),pytest.param(False,id='false'),pytest.param(2.,id='float'),
    pytest.param('2',id='string'),pytest.param(None,id='none')])
def test_noncanonical_schema_numbers_are_refused(schema):
    value=declaration();value['schema_version']=schema
    with pytest.raises(ValueError):domain.parse(value)


@pytest.mark.parametrize('extra',['capability','qualification','policy','bank_sha256'])
def test_unknown_fields_are_refused(extra):
    value=declaration();value[extra]='caller-authored'
    with pytest.raises(ValueError):domain.parse(value)


def test_unknown_source_fields_and_missing_hashes_are_refused():
    value=declaration();value['sources'][0]['authority']=True
    with pytest.raises(ValueError):domain.parse(value)
    value=declaration();value.pop('engine_sha256')
    with pytest.raises(ValueError):domain.parse(value)


@pytest.mark.parametrize('change',[
    pytest.param(dict(kind='autodiscovery'),id='autodiscovery-kind'),
    pytest.param(dict(format='unknown'),id='unknown-format'),
    pytest.param(dict(version='future'),id='future-version'),
    pytest.param(dict(format='normalized',version=domain.CODEX_VERSION),id='normalized-with-codex-version'),
    pytest.param(dict(format='ai-memory',version='0.2'),id='ai-memory-with-version'),
    pytest.param(dict(session='thread'),id='events-file-session'),
    pytest.param(dict(kind='native_source_root',format='codex',version=None),id='native-codex-unversioned'),
    pytest.param(dict(kind='native_source_root',format='codex',version=domain.CAPABILITIES['codex']['version']),
        id='native-codex-capability-version'),
    pytest.param(dict(kind='native_source_root',format='claude',version=domain.CLAUDE_VERSION),id='native-claude')])
def test_ambiguous_source_profile_and_session_combinations_are_refused(change):
    value=declaration();value['sources'][0].update(change)
    with pytest.raises(ValueError):domain.parse(value)


@pytest.mark.parametrize('session',[
    pytest.param('',id='empty'),pytest.param('..',id='parent'),pytest.param('../thread',id='parent-prefix'),
    pytest.param('thread*',id='wildcard'),pytest.param('thread\nother',id='newline'),pytest.param(True,id='bool'),
    pytest.param('x'*257,id='overlong')])
def test_ambiguous_native_sessions_are_refused(session):
    value=declaration();value['sources'][0].update(kind='native_source_root',format='codex',version=domain.CODEX_VERSION,session=session)
    with pytest.raises(ValueError):domain.parse(value)


def test_input_objects_are_never_mutated_when_validation_fails():
    value=declaration();value['sources'].append(dict(value['sources'][0],selection='/different',snapshot_sha256='invalid'))
    before=deepcopy(value)
    with pytest.raises(ValueError):domain.parse(value)
    assert value == before
