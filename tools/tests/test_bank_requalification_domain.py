"""Closed-input syntax is not a source reader, trusted policy or capability."""
from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import bank_requalification_domain as domain


def declaration():
    return dict(schema_version=2,profile=domain.PROFILE,**{name:'a'*64 for name in domain.HASH_FIELDS},
        project_inputs=dict(profile='ahl-controlled-agents-project-inputs-v1',root='/synthetic/project',paths=['AGENTS.md','ahl.json']),
        sources=[dict(kind='events_file',selection='/synthetic/nonexistent/events.jsonl',
            snapshot_sha256=hashlib.sha256(b'').hexdigest(),format='normalized',version=None,session=None)])


class ClosedInputDomainTests(unittest.TestCase):
    def test_explicit_empty_event_content_is_a_valid_pinned_representation(self):
        value=declaration();parsed=domain.parse(value)
        self.assertEqual(parsed,value)
        self.assertEqual(parsed['sources'][0]['snapshot_sha256'],hashlib.sha256(b'').hexdigest())
        self.assertIsNot(parsed,value);self.assertIsNot(parsed['sources'],value['sources'])
        parsed['sources'][0]['selection']='/synthetic/changed'
        self.assertEqual(value['sources'][0]['selection'],'/synthetic/nonexistent/events.jsonl')

    def test_project_read_set_is_required_fixed_and_copied(self):
        value=declaration();value.pop('project_inputs')
        with self.assertRaises(ValueError):domain.parse(value)
        valid=dict(profile='ahl-controlled-agents-project-inputs-v1',root='/synthetic/project',paths=['AGENTS.md','ahl.json'])
        missing_root=dict(valid);missing_root.pop('root')
        invalid=(None,{},missing_root,dict(valid,profile='whole-checkout'),dict(valid,paths=['ahl.json','AGENTS.md']),
            dict(valid,paths=['AGENTS.md']),dict(valid,paths=['AGENTS.md','ahl.json','src']),
            dict(valid,paths=('AGENTS.md','ahl.json')),dict(valid,paths=['*']),dict(valid,recursive=True))
        for inputs in invalid:
            value=declaration();value['project_inputs']=inputs
            with self.subTest(project_inputs=inputs),self.assertRaises(ValueError):domain.parse(value)
        value=declaration();parsed=domain.parse(value)
        self.assertEqual(parsed['project_inputs'],valid)
        self.assertIsNot(parsed['project_inputs'],value['project_inputs'])
        parsed['project_inputs']['paths'].append('unreviewed')
        self.assertEqual(value['project_inputs'],valid)
        for root in ('relative/project','//synthetic/project','/synthetic/../project','/synthetic/./project',
                '/synthetic/project/','/synthetic/project*','/synthetic/hidden\u200b'):
            value=declaration();value['project_inputs']['root']=root
            with self.subTest(root=root),self.assertRaises(ValueError):domain.parse(value)

    def test_supported_event_adapters_preserve_explicit_version_selection(self):
        versions={'normalized':(None,), 'ai-memory':(None,),
            'codex':(None,domain.CAPABILITIES['codex']['version'],domain.CODEX_VERSION),
            'claude':(None,domain.CAPABILITIES['claude']['version'],domain.CLAUDE_VERSION)}
        for source_format,supported in versions.items():
            for version in supported:
                with self.subTest(format=source_format,version=version):
                    value=declaration();value['sources'][0].update(format=source_format,version=version)
                    self.assertEqual(domain.parse(value),value)

    def test_native_root_uses_existing_pinned_codex_contract(self):
        for session in (None,'synthetic-thread-17'):
            value=declaration();value['sources'][0].update(kind='native_source_root',format='codex',version=domain.CODEX_VERSION,session=session)
            self.assertEqual(domain.parse(value),value)

    def test_source_selection_never_reads_or_resolves_the_filesystem(self):
        with patch.object(Path,'stat',side_effect=AssertionError('no filesystem reads')), \
                patch.object(Path,'resolve',side_effect=AssertionError('no path resolution')), \
                patch.object(Path,'is_symlink',side_effect=AssertionError('no symlink inspection')), \
                patch.object(Path,'home',side_effect=AssertionError('no source discovery')):
            self.assertEqual(domain.parse(declaration()),declaration())

    def test_empty_or_duplicate_sources_cannot_enable_autodiscovery(self):
        for sources in ([],(),None,True):
            value=declaration();value['sources']=sources
            with self.subTest(sources=sources),self.assertRaises(ValueError):domain.parse(value)
        value=declaration();value['sources']*=2
        with self.assertRaisesRegex(ValueError,'duplicate'):domain.parse(value)
        value=declaration();value['sources']=[dict(value['sources'][0],selection='/synthetic/'+str(i)) for i in range(33)]
        with self.assertRaises(ValueError):domain.parse(value)

    def test_noncanonical_paths_wildcards_and_controls_are_refused(self):
        paths=('relative/events','~/events','//host/events','/synthetic//events','/synthetic/./events',
            '/synthetic/../events','/synthetic/events/','/synthetic/events*','/synthetic/?',
            '/synthetic/[ab]','/synthetic/{a,b}','/synthetic/back\\slash','/synthetic/new\nline',
            '/synthetic/null\0byte','/synthetic/del\x7f','/synthetic/hidden\u200b','/synthetic/\ud800')
        for path in paths:
            value=declaration();value['sources'][0]['selection']=path
            with self.subTest(path=repr(path)),self.assertRaises(ValueError):domain.parse(value)
        value=declaration();value['sources'][0]['selection']='/'+'x'*4096
        with self.assertRaises(ValueError):domain.parse(value)

    def test_hashes_require_exact_lowercase_hex_strings(self):
        for field in (*domain.HASH_FIELDS,'snapshot_sha256'):
            for invalid in (None,True,1,'A'*64,'a'*63,'g'*64):
                value=declaration();target=value['sources'][0] if field=='snapshot_sha256' else value
                target[field]=invalid
                with self.subTest(field=field,invalid=invalid),self.assertRaises(ValueError):domain.parse(value)

    def test_unknown_fields_and_noncanonical_schema_numbers_are_refused(self):
        for schema in (True,False,2.,'2',None):
            value=declaration();value['schema_version']=schema
            with self.subTest(schema=schema),self.assertRaises(ValueError):domain.parse(value)
        for extra in ('capability','qualification','policy','bank_sha256'):
            value=declaration();value[extra]='caller-authored'
            with self.subTest(extra=extra),self.assertRaises(ValueError):domain.parse(value)
        value=declaration();value['sources'][0]['authority']=True
        with self.assertRaises(ValueError):domain.parse(value)
        value=declaration();value.pop('engine_sha256')
        with self.assertRaises(ValueError):domain.parse(value)

    def test_ambiguous_source_profile_and_session_combinations_are_refused(self):
        combinations=(dict(kind='autodiscovery'),dict(format='unknown'),dict(version='future'),
            dict(format='normalized',version=domain.CODEX_VERSION),dict(format='ai-memory',version='0.2'),
            dict(session='thread'),dict(kind='native_source_root',format='codex',version=None),
            dict(kind='native_source_root',format='codex',version=domain.CAPABILITIES['codex']['version']),
            dict(kind='native_source_root',format='claude',version=domain.CLAUDE_VERSION))
        for change in combinations:
            value=declaration();value['sources'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):domain.parse(value)
        for session in ('','..','../thread','thread*','thread\nother',True,'x'*257):
            value=declaration();value['sources'][0].update(kind='native_source_root',format='codex',version=domain.CODEX_VERSION,session=session)
            with self.subTest(session=session),self.assertRaises(ValueError):domain.parse(value)

    def test_input_objects_are_never_mutated_when_validation_fails(self):
        value=declaration();value['sources'].append(dict(value['sources'][0],selection='/different',snapshot_sha256='invalid'))
        before=deepcopy(value)
        with self.assertRaises(ValueError):domain.parse(value)
        self.assertEqual(value,before)
