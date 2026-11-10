"""Offline command for the read-only static harness audit."""
import argparse
from dataclasses import asdict
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from agent_harness_lab import audit, surfaces
from agent_harness_lab.evaluation.integrity import open_directory_nofollow, relative
from agent_harness_lab.audit import improvement_hint, instruction_totals
from agent_harness_lab.sources.git_view import Folder
from agent_harness_lab.sources.claude_instructions import scope_of
from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.sources import harness_inventory
from agent_harness_lab.workflow import state_root


def _status(status, reasons=()):
    if status == 'nonconformant':
        return 'weak'
    if status == 'conformant':
        return 'conforms'
    if any(reason in ('not_checked', 'not_parsed', 'no_parser', 'parse_failed', 'unsupported',
                      'skill_frontmatter_unsupported', 'command_frontmatter_unsupported', 'no_pinned_capability') for reason in reasons):
        return 'not_checked'
    return 'unknown'


def _project(inventory, report, task_cwd):
    dimensions = {row['dimension']: row for row in report.pop('dimensions')}
    findings = []
    for row in report['findings']:
        dimension = ('skills' if row['kind'] == 'skill' else
                     {'instructions': 'instructions', 'hooks': 'enforcement'}.get(row['dimension']))
        if dimension is None:
            continue
        status = _status(row['status'], (row['reason'],))
        if (row['reason'] in (audit.chain.TRUNCATED, audit.chain.SKIPPED, audit.chain.DECLARED_TRUNCATED, audit.chain.DECLARED_SKIPPED)
                and dict(row['measures']).get('dropped_bytes', 0) > 0):
            status = 'missing'
        findings.append(dict(row, dimension=dimension, status=status, observed=row['reason']))
    report['findings'] = findings
    context = audit.skills.Context(inventory, task_cwd) if inventory.status == 'declared' else None
    skill_nodes = [node for node in inventory.nodes if node.kind == 'skill' and not context.supporting(node)
                   and dict(node.facts).get(surfaces.VENDORED_FACET) is not True] if context else []
    skill_findings = [row for row in findings if row['dimension'] == 'skills']
    refused = bool(context and any('commands' in audit._refused(row, inventory.runtime, audit.registry())
                                   for row in inventory.refusals if row.reason != 'vcs_metadata'))
    skill_states = {row['status'] for row in skill_findings}
    if 'weak' in skill_states:
        skill_status = 'weak'
    elif refused or 'unknown' in skill_states or not skill_nodes:
        skill_status = 'unknown'
    elif 'not_checked' in skill_states:
        skill_status = 'not_checked'
    else:
        skill_status = 'conforms'
    missing_instructions = any(row['dimension'] == 'instructions' and row['status'] == 'missing' for row in findings)
    statuses = {'instructions': 'missing' if missing_instructions else _status(
                    dimensions['instructions']['status'], dimensions['instructions']['reasons']),
                'skills': skill_status,
                'enforcement': _status(dimensions['hooks']['status'], dimensions['hooks']['reasons'])}
    report['coverage'] = [{'dimension': name, 'status': statuses.get(name, 'not_checked'), 'notice': audit.NOTICE}
        for name in ('instructions', 'skills', 'sensors', 'enforcement', 'memory_and_state', 'improvement_loop')]
    return report


def _instruction_hypothesis(report, reason, evidence, remediation, measures=()):
    finding = audit.Finding(audit.digest(['instructions', '.', 'instruction', reason]), 'instructions',
        'nonconformant', reason, '.', audit.digest('.'), 'instruction', evidence,
        'behavioral_evaluation_required', remediation, 'https://code.claude.com/docs/en/features-overview',
        measures=measures)
    report['findings'].append(dict(asdict(finding), status='weak', observed=reason))
    for row in report['coverage']:
        if row['dimension'] == 'instructions' and row['status'] != 'missing': row['status'] = 'weak'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl audit', description=(
        'Check pinned runtime conventions offline. Findings do not measure performance; '
        'the project is not modified. A private report is written to XDG state.'))
    parser.add_argument('project', type=Path, help='Existing project directory')
    parser.add_argument('--runtime', choices=('claude-code', 'codex'), default='codex', help='Runtime profile (default: codex)')
    parser.add_argument('--task-cwd', default='.', help='Existing project-relative task directory (default: project root)')
    parser.add_argument('--json', action='store_true', help='Print the full private JSON report')
    parser.add_argument('--codex-project-doc-max-bytes', type=int,
                        help='Caller-declared Codex byte budget; runtime configuration is not read')
    parser.add_argument('--instruction-soft-lines', type=int, default=200,
                        help='Checkout instruction-line convention (default: 200; not a validated threshold)')
    args = parser.parse_args(argv)
    try:
        if args.codex_project_doc_max_bytes is not None and args.runtime != 'codex':
            raise ValueError('Codex byte budget requires the Codex runtime')
        if args.instruction_soft_lines < 0: raise ValueError('instruction soft-line budget must be nonnegative')
        project = args.project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError('project must be an existing directory')
        if args.task_cwd != '.':
            relative(args.task_cwd)
            if '.git' in Path(args.task_cwd).parts: raise ValueError('unsafe task directory')
            descriptor = open_directory_nofollow(project / args.task_cwd)
            os.close(descriptor)
        destination = (state_root() / 'audits').resolve()
        if destination == project or project in destination.parents:
            raise ValueError('audit state directory must be outside the project')
        profile = dict(schema_version=1, runtime=args.runtime, version=surfaces.PINS[args.runtime], task_cwd=args.task_cwd)
        inventory = harness_inventory.capture(project, profile)
        report = _project(inventory, audit.audit(inventory, task_cwd=args.task_cwd,
            codex_project_doc_max_bytes=args.codex_project_doc_max_bytes).document(), args.task_cwd)
        if args.runtime == 'codex':
            report['codex_budget_declaration'] = dict(
                bytes=(args.codex_project_doc_max_bytes if args.codex_project_doc_max_bytes is not None
                       else audit.chain.CODEX_DEFAULT_BYTES),
                origin='caller_declared' if args.codex_project_doc_max_bytes is not None else 'assumed_default',
                runtime_configuration='unknown', publication_allowed=False)
            try:
                with Folder(project.parent, time.monotonic() + 30) as folder:
                    report['declared_instruction_totals'] = instruction_totals.codex(
                        profile, folder, project.name, inventory, declared_limit=args.codex_project_doc_max_bytes)
            except (ValueError, OSError, TimeoutError):
                report['declared_instruction_totals'] = dict(status='unknown', bytes=None, lines=None, files=None,
                    reasons=['resolution_unavailable'], scope='captured_checkout_project_chain',
                    settings='caller_declared_budget' if args.codex_project_doc_max_bytes is not None else 'assumed_default',
                    observed_loading='unknown', snapshot_relation='separate_resolver_observation',
                    inventory_comparison='selected_instruction_files_only', cross_observation_consistency='unknown',
                    publication_allowed=False)
        if args.runtime == 'claude-code':
            try:
                with Folder(project.parent, time.monotonic() + 30) as folder:
                    expected = {n.path_sha256: n.content_sha256 for n in inventory.nodes
                                if n.origin == 'checkout' and n.kind == 'instruction'
                                and n.content_sha256 is not None and scope_of(n.path) is not None}
                    report['declared_instruction_totals'] = instruction_totals.claude(
                        profile, folder, project.name, expected=expected)
            except (ValueError, OSError, TimeoutError):
                report['declared_instruction_totals'] = dict(status='unknown', bytes=None, lines=None, files=None,
                    reasons=['resolution_unavailable'], scope='captured_checkout_launch_files',
                    settings='assumed_default', observed_loading='unknown', snapshot_relation='separate_resolver_observation',
                    inventory_comparison='recognized_instruction_files_only', cross_observation_consistency='unknown',
                    publication_allowed=False)
        total = report['declared_instruction_totals']
        if args.runtime == 'claude-code' and total['status'] == 'declared' and total.get('empty_import'):
            _instruction_hypothesis(report, 'instruction_empty_import', 'structural',
                                    'evaluate_empty_instruction_import')
        total['soft_line_budget'] = args.instruction_soft_lines
        if total['status'] == 'declared' and total['lines'] > args.instruction_soft_lines:
            _instruction_hypothesis(report, 'instruction_launch_lines_above_soft_budget', 'count',
                'evaluate_instruction_budget_reduction', (('lines', total['lines']),
                                                         ('soft_line_budget', args.instruction_soft_lines)))
        if total['status'] == 'declared' and total['files']:
            shape = total['shape']
            if not shape['constraints']:
                _instruction_hypothesis(report, 'instruction_constraints_not_detected', 'lexical',
                                        'evaluate_explicit_instruction_constraints')
            if not shape['verification_commands']:
                _instruction_hypothesis(report, 'instruction_verification_command_not_detected', 'lexical',
                                        'evaluate_verification_guidance')
            if shape['procedure_sections']:
                _instruction_hypothesis(report, 'instruction_procedure_section_detected', 'structural',
                                        'evaluate_moving_procedure_to_skill_or_path_rule')
        from .protected_paths import capture as protected_paths
        report['protected_path_inventory'] = protected_paths(project)
        from .permission_candidates import report as permission_candidates
        report['permission_candidates'] = permission_candidates(inventory, report['protected_path_inventory'])
        report['improvement_hint'] = improvement_hint.from_findings(report['findings'], args.runtime)
        candidate = next((row for row in report['findings'] if row['remediation']), None)
        if report['status'] != 'completed':
            report['next_action'] = 'Resolve the blocked inventory before using this audit.'
        elif candidate:
            report['next_action'] = ('Evaluate a proposed change for finding ' + candidate['id'] + ' with ahl improve; '
                'the convention check does not establish a performance improvement.')
            if report['improvement_hint'] is not None:
                report['next_action'] += (' Select an improvement runtime compatible with the audited ' +
                    args.runtime + ' and the configured profile before using improvement_hint.focus.')
        else:
            report['next_action'] = 'Capture behavioral evidence with ahl analyze and evaluate a proposed change with ahl improve.'
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder = Path(tempfile.mkdtemp(prefix='audit-', dir=destination))
        report_path = folder / 'report.json'
        atomic_json(report_path, report)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print('Static convention checks; not evidence of harness performance.')
            for row in report['coverage']:
                print(f"{row['dimension']}: {row['status']}")
            events = report['hook_events']
            print('Declared hook events: ' + (', '.join(events['declared']) or 'none observed') +
                  f" (coverage: {events['coverage']}; execution: unknown)")
            candidates = {row['path']: row['status'] for row in report['permission_candidates']['paths']}
            for row in report['protected_path_inventory']['paths']:
                print(f"Protected path candidate: {json.dumps(row['path'], ensure_ascii=True)} ({row['category']}); "
                      f"permission: {candidates[row['path']]}; enforcement: unknown")
            if report['protected_path_inventory']['coverage'] == 'unknown':
                print('Protected path coverage: unknown')
            for row in report['findings']:
                print(f"{row['dimension']}: {row['reason']} ({row['path']}) — {row['remediation'] or 'not checked'}")
            print(report['next_action'])
        print(f'Private report: {report_path}', file=sys.stderr)
        return 0 if report['status'] == 'completed' else 3
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f'ahl audit: {exc}\n')
    except NotImplementedError:
        parser.exit(3, 'ahl audit: required filesystem capabilities unavailable; no unsafe fallback\n')
