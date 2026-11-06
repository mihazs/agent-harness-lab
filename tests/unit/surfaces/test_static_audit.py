"""Model-free static harness audit over synthetic checkouts; no real configuration, process, network or model."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import audit, surfaces
from agent_harness_lab.sources import harness_inventory, plugins_inventory
from tests.support import harness_surface_fixture as fixture

SETTINGS = {'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'true'}]}]},
            'enabledPlugins': {'synthetic@market': True}}
WORKFLOW = 'export const meta = { name: "build", description: "Build it" };\nexport default async function run() { return 1; }\n'
WELL_FORMED = {
    'CLAUDE.md': '# Synthetic project\n\nRun the synthetic tests.\n',
    '.claude/rules/style.md': '---\npaths:\n  - "src/**"\n---\nUse synthetic style.\n',
    '.claude/settings.json': json.dumps(SETTINGS),
    '.claude/workflows/build.js': WORKFLOW,
    '.claude/agents/reviewer.md': '---\nname: reviewer\ndescription: Reviews synthetic code\n---\nReview.\n',
    '.claude/commands/ship.md': '---\ndescription: Ship synthetic work\n---\nShip.\n',
    '.mcp.json': json.dumps({'mcpServers': {'synthetic': {'command': 'synthetic-server'}}}),
}
NONCONFORMANT = {
    'CLAUDE.md': '   \n',
    '.claude/settings.json': json.dumps({'hooks': {'NotAnEvent': [{'hooks': [{'type': 'command', 'command': 'true'}]}]}}),
    '.claude/workflows/build.js': 'export const meta = { name: "b", description: "d" };\nexport default () => Date.now();\n',
    '.claude/agents/reviewer.md': '---\nname: reviewer\n---\nNo description.\n',
    '.claude/commands/ship.md': '---\nname: ship\ndescription: Ship\n---\nShip.\n',
}
AUDIT = []


def _audit(event, args):
    if AUDIT and event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)): AUDIT[-1].append(os.fsdecode(args[0]))


sys.addaudithook(_audit)


def denied(*args, **kwargs): raise AssertionError('the audit must not spawn processes or use the network')


class StaticAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ahl-audit-'); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def build(self, files, name='checkout', **options):
        root = self.base / name; root.mkdir()
        for path, text in files.items():
            target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)
        (root / '.git').mkdir(); (root / '.git/HEAD').write_text('ref: refs/heads/main\n')
        return root

    def statuses(self, report):
        return {row.dimension: row.status for row in report.dimensions}

    def test_an_empty_project_reports_every_dimension_unknown_never_conformant(self):
        report = audit.run(self.build({}), 'claude-code')
        self.assertEqual(report.status, 'completed')
        self.assertEqual(tuple(row.dimension for row in report.dimensions), audit.DIMENSIONS)
        for row in report.dimensions:
            self.assertEqual((row.status, row.surfaces, row.reasons), ('unknown', 0, ('no_surface_declared',)))
        self.assertEqual(report.findings, ())

    def test_a_well_formed_project_conforms_in_every_dimension(self):
        report = audit.run(self.build(WELL_FORMED), 'claude-code')
        self.assertEqual(self.statuses(report), dict.fromkeys(audit.DIMENSIONS, 'conformant'))
        self.assertEqual(report.findings, ())
        # The settings file counts once per surface it holds: hook, plugin enablement and configuration.
        counts = {row.dimension: row.surfaces for row in report.dimensions}
        self.assertEqual((counts['hooks'], counts['plugins'], counts['config']), (1, 1, 2))
        for row in report.dimensions:
            self.assertEqual(row.notice, audit.NOTICE)
            self.assertTrue(row.source.startswith('https://'))

    def test_the_document_is_stable_documented_json_with_no_score(self):
        checkout = self.build(WELL_FORMED)
        first, second = audit.run(checkout, 'claude-code').document(), audit.run(checkout, 'claude-code').document()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual({key: first[key] for key in ('profile', 'status', 'aggregate_score', 'model_calls', 'executed_files',
            'files_written', 'network_used', 'publication_allowed', 'performance_evidence', 'notice', 'schema_version')},
            dict(profile='ahl-static-audit-v1', status='completed', aggregate_score=None, model_calls=0, executed_files=0,
                 files_written=0, network_used=False, publication_allowed=False, performance_evidence=False,
                 notice='convention_check_not_performance_evidence', schema_version=1))
        self.assertEqual(set(first['dimensions'][0]), {'dimension', 'status', 'surfaces', 'conformant', 'nonconformant',
                                                       'unknown', 'reasons', 'source', 'notice'})

    def test_documented_violations_are_nonconformant_with_cheapest_remediation_first(self):
        report = audit.run(self.build(dict(WELL_FORMED, **NONCONFORMANT)), 'claude-code')
        expected = dict.fromkeys(audit.DIMENSIONS, 'nonconformant')
        expected.update(rules='conformant', plugins='unknown', config='conformant')  # settings with no plugins: absence
        self.assertEqual(self.statuses(report), expected)
        reasons = [(row.dimension, row.reason) for row in report.findings if row.status == 'nonconformant']
        self.assertEqual(sorted(reasons), sorted([('instructions', 'instruction_empty'), ('hooks', 'hook_unknown_event'),
            ('workflows', 'workflow_lint_issues'), ('agents', 'agent_skipped_by_runtime'),
            ('commands', 'command_name_key_ignored')]))
        costs = [row.cost_class for row in report.findings]
        rank = [audit.COSTS.index(cost) for cost in costs if cost is not None]
        self.assertEqual(rank, sorted(rank))
        self.assertEqual(report.findings[0].cost_class, 'deterministic_edit')
        for row in report.findings:
            if row.status == 'nonconformant': self.assertEqual(audit.REMEDIATIONS[row.reason], (row.cost_class, row.remediation))

    def test_private_excluded_surfaces_never_become_a_pass(self):
        files = dict(WELL_FORMED, **{'CLAUDE.local.md': 'synthetic personal notes\n', '.claude/settings.local.json': '{}'})
        report = audit.run(self.build(files), 'claude-code')
        statuses = self.statuses(report)
        for dimension in ('instructions', 'hooks', 'plugins', 'config'):
            self.assertEqual(statuses[dimension], 'unknown', dimension)
            row = next(row for row in report.dimensions if row.dimension == dimension)
            self.assertEqual(row.reasons, ('private_excluded',))
        self.assertEqual({row.path for row in report.findings}, {'CLAUDE.local.md', '.claude/settings.local.json'})
        self.assertTrue(all(row.status == 'unknown' and row.cost_class is None for row in report.findings))

    def test_unparsed_and_unchecked_surfaces_are_unknown(self):
        files = dict(WELL_FORMED, **{'.claude/settings.json': '{not json', '.claude/skills/demo/SKILL.md': '---\nname: demo\n---\n'})
        report = audit.run(self.build(files), 'claude-code')
        reasons = {row.dimension: row.reasons for row in report.dimensions}
        self.assertEqual((reasons['hooks'], reasons['plugins'], reasons['commands']),
                         (('parse_failed',), ('parse_failed',), ('not_parsed',)))
        self.assertEqual(reasons['config'], ('parse_failed',))
        for dimension in ('hooks', 'plugins', 'commands', 'config'): self.assertEqual(self.statuses(report)[dimension], 'unknown')

    def test_a_refused_entry_is_unknown_in_the_dimension_it_would_belong_to(self):
        checkout = self.build(WELL_FORMED)
        (checkout / 'shared.md').write_text('---\nname: shared\ndescription: Shared\n---\n')
        (checkout / '.claude/agents/linked.md').symlink_to('../../shared.md')
        (checkout / 'docs').mkdir(); (checkout / 'docs/linked.md').symlink_to('../shared.md')
        report = audit.run(checkout, 'claude-code')
        row = next(row for row in report.dimensions if row.dimension == 'agents')
        self.assertEqual((row.status, row.reasons, row.unknown, row.conformant), ('unknown', ('refused_symlink',), 1, 1))
        self.assertEqual(report.unattributed_refusals, 1)  # docs/linked.md belongs to no dimension
        # An agent file can also declare hooks, so the unread link leaves hooks unknown too; nothing else changes.
        hooks = next(row for row in report.dimensions if row.dimension == 'hooks')
        self.assertEqual((hooks.status, hooks.reasons), ('unknown', ('refused_symlink',)))
        others = {key: value for key, value in self.statuses(report).items() if key not in ('agents', 'hooks')}
        self.assertEqual(set(others.values()), {'conformant'})

    def test_a_refused_harness_folder_makes_every_dimension_unknown(self):
        checkout = self.build({'CLAUDE.md': '# Synthetic\n'})
        (self.base / 'elsewhere').mkdir(); (checkout / '.claude').symlink_to(self.base / 'elsewhere', target_is_directory=True)
        report = audit.run(checkout, 'claude-code')
        self.assertEqual(set(self.statuses(report).values()), {'unknown'})
        self.assertIn('refused_symlink', next(row for row in report.dimensions if row.dimension == 'instructions').reasons)

    def test_a_refused_entry_in_another_runtimes_folder_is_unattributed(self):
        checkout = self.build({'CLAUDE.md': '# Synthetic\n', 'AGENTS.md': '# Synthetic\n',
                               '.claude/agents/r.md': '---\nname: r\ndescription: Reviews\n---\nReview.\n'})
        (checkout / '.codex/agents').mkdir(parents=True); (checkout / '.codex/agents/x.toml').symlink_to('../../AGENTS.md')
        report = audit.run(checkout, 'claude-code')
        statuses = self.statuses(report)
        self.assertEqual((statuses['instructions'], statuses['agents']), ('unknown', 'conformant'))  # AGENTS.md: setting
        self.assertEqual(report.unattributed_refusals, 1)
        self.assertNotIn('.codex/agents/x.toml', {row.path for row in report.findings})
        # A dual-runtime .claude/CLAUDE.md link is not a Codex surface either.
        checkout = self.build({'AGENTS.md': '# Synthetic\n'}, name='codex')
        (checkout / '.claude').mkdir(); (checkout / '.claude/CLAUDE.md').symlink_to('../AGENTS.md')
        report = audit.run(checkout, 'codex')
        self.assertEqual(self.statuses(report)['instructions'], 'conformant')
        self.assertEqual((report.unattributed_refusals, report.findings), (1, ()))

    def test_settings_that_declare_no_hooks_or_plugins_leave_them_unknown(self):
        files = {'CLAUDE.md': '# Synthetic\n', '.claude/settings.json': json.dumps({'permissions': {'allow': []}})}
        report = audit.run(self.build(files), 'claude-code')
        reasons = {row.dimension: (row.status, row.reasons) for row in report.dimensions}
        self.assertEqual(reasons['hooks'], ('unknown', ('no_hooks_declared',)))
        self.assertEqual(reasons['plugins'], ('unknown', ('no_plugins_declared',)))
        self.assertEqual(reasons['config'], ('conformant', ()))

    def test_an_agent_outside_a_project_agents_folder_is_not_judged_by_its_disposition(self):
        files = {'.claude-plugin/plugin.json': json.dumps({'name': 'p'}), 'agents/a.md': '---\ndescription: d\n---\n'}
        report = audit.run(self.build(files), 'claude-code')
        row = next(row for row in report.dimensions if row.dimension == 'agents')
        self.assertEqual((row.status, row.reasons), ('unknown', ('agent_layer_not_modelled',)))
        self.assertFalse([row for row in report.findings if row.status == 'nonconformant'])

    def test_a_claude_code_agents_md_depends_on_an_unknown_setting(self):
        report = audit.run(self.build({'AGENTS.md': '# Synthetic\n'}), 'claude-code')
        row = next(row for row in report.dimensions if row.dimension == 'instructions')
        self.assertEqual((row.status, row.reasons), ('unknown', ('instruction_files_setting_unknown',)))

    def test_plugin_manifest_checks_apply_to_a_plugin_checkout_inventory(self):
        files = {'CLAUDE.md': '# Synthetic\n', '.claude/settings.json': json.dumps({'enabledPlugins': {'p@m': True}})}
        project, plugin = self.build(files), self.base / 'plugin'
        for name, text in {'.claude-plugin/plugin.json': json.dumps({'version': '1'}),
                           '.claude-plugin/marketplace.json': json.dumps({'name': 'm'})}.items():
            (plugin / name).parent.mkdir(parents=True, exist_ok=True); (plugin / name).write_text(text)
        (plugin / '.git/refs/heads').mkdir(parents=True); (plugin / '.git/HEAD').write_text('ref: refs/heads/main\n')
        (plugin / '.git/refs/heads/main').write_text('c0ffee' + 'a' * 34 + '\n')
        profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
        checkout, = plugins_inventory.capture(project, profile, plugin_checkouts={'p@m': str(plugin)}).checkouts
        report = audit.audit(checkout.inventory)
        self.assertEqual(self.statuses(report)['plugins'], 'nonconformant')
        self.assertEqual(sorted(row.reason for row in report.findings if row.dimension == 'plugins'),
                         ['plugin_manifest_name_missing', 'plugin_marketplace_entries_missing'])
        # audit.run() over a checkout never inventories manifests, so the same files there are no plugin surface.
        self.assertEqual(next(row for row in audit.run(plugin, 'claude-code').dimensions if row.dimension == 'plugins').surfaces, 0)

    def test_a_bound_overflow_is_blocked_never_a_truncated_pass(self):
        checkout = self.build(WELL_FORMED)
        with patch.object(harness_inventory, 'MAX_FILES', 2):
            report = audit.run(checkout, 'claude-code')
        self.assertEqual((report.status, report.blocked_reason, report.findings), ('blocked', 'file_bound_exceeded', ()))
        for row in report.dimensions:
            self.assertEqual((row.status, row.surfaces, row.reasons), ('unknown', 0, ('inventory_file_bound_exceeded',)))
        with self.assertRaises(ValueError):
            audit.Report(**{**report.__dict__, 'dimensions': tuple(
                audit.Dimension(row.dimension, 'conformant', 1, 1, 0, 0, (), row.source) for row in report.dimensions)})

    def test_an_unpinned_runtime_version_is_unknown_everywhere(self):
        profile = dict(schema_version=1, runtime='claude-code', version='0.0.1', task_cwd='.')
        report = audit.audit(harness_inventory.capture(self.build(WELL_FORMED), profile))
        self.assertEqual((report.status, report.blocked_reason), ('unsupported', 'unsupported'))
        self.assertEqual(set(self.statuses(report).values()), {'unknown'})

    def test_codex_audits_its_own_surfaces(self):
        files = {'AGENTS.md': '# Synthetic\n', 'CLAUDE.md': '# Not read by Codex\n',
                 '.codex/agents/reviewer.toml': 'name = "reviewer"\ndescription = "Reviews"\ndeveloper_instructions = "Review."\n'}
        report = audit.run(self.build(files), 'codex')
        statuses = self.statuses(report)
        self.assertEqual(statuses['agents'], 'conformant')
        self.assertEqual(statuses['instructions'], 'unknown')  # Codex reads no CLAUDE.md: unsupported, never a pass
        self.assertEqual(next(row for row in report.dimensions if row.dimension == 'instructions').reasons, ('unsupported',))

    def test_the_audit_reads_only_the_checkout_and_never_executes_writes_or_connects(self):
        checkout = fixture.build(self.base / 'fixture')
        for path, text in WELL_FORMED.items():
            target = checkout / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)
        (checkout / '.claude/hooks/check.sh').chmod(0o755)
        before = fixture.tree_state(checkout)
        AUDIT.append([])
        try:
            with patch.object(subprocess, 'Popen', denied), patch.object(socket, 'socket', denied), \
                    patch.object(os, 'system', denied), patch.object(os, 'execv', denied):
                report = audit.run(checkout, 'claude-code')
        finally: opened = AUDIT.pop()
        self.assertEqual(report.status, 'completed')
        self.assertEqual(fixture.tree_state(checkout), before)
        absolute = [path for path in opened if os.path.isabs(path)]
        # Absolute opens are the checkout, the ancestors a no-follow walk opens to reach it, or the interpreter.
        self.assertTrue(all(os.path.commonpath((str(checkout), path)) in (str(checkout), path) or path.startswith(sys.prefix)
                            or path.startswith(sys.base_prefix) for path in absolute), absolute)
        self.assertNotIn(fixture.SECRET, json.dumps(report.document()))
        # The hook script is listed but never read as a check, so hooks stay unknown rather than pass.
        self.assertIn('not_parsed', next(row for row in report.dimensions if row.dimension == 'hooks').reasons)

    def test_the_registry_is_sorted_and_covers_every_inventory_kind(self):
        table = audit.registry()
        self.assertEqual({row[0] for row in harness_inventory.PARSERS} - set(table), set())
        self.assertEqual({row[0] for row in table.values()}, set(audit.DIMENSIONS))
        self.assertTrue(all(row[1] in surfaces.SURFACES for row in table.values()))
        with patch.object(audit, 'CHECKS', tuple(reversed(audit.CHECKS))), self.assertRaises(ValueError): audit.registry()
        self.assertTrue(all(cost in audit.COSTS for cost, _ in audit.REMEDIATIONS.values()))

    def test_a_finding_cannot_claim_a_remediation_without_a_violation(self):
        with self.assertRaises(ValueError):
            audit.Finding('0' * 64, 'hooks', 'unknown', 'private_excluded', 'a', '0' * 64, 'hook', 'structural',
                          'deterministic_edit', 'remove_undocumented_hook_fields')


if __name__ == '__main__':
    unittest.main()
