"""`ahl apply`: review the saved change plan of one run, and write the project only for a plan that still matches it,
that the application policy permits and whose exact hash the caller names (#77). Neither the saved report nor an
authorization becomes certification."""
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys

PROFILE = 'ahl-plan-review-v1'
MAX_JSON = 16 * 1024 * 1024
BEHAVIORAL = 'behavioral_candidate'


def _read(path):
    from .evaluation.integrity import read_regular
    return json.loads(read_regular(path, MAX_JSON))


def _states(folder, original):
    from .workflow import candidate_states
    try:
        return candidate_states(folder, original) if folder.is_dir() else None
    except (ValueError, OSError):
        return None


def _bind(plan, run, project, before, after):
    """What the plan froze against what exists now: one check per bound input, and the staged candidate folder."""
    from . import config
    from .harness_change_plan import digest
    from .promotion import policy
    from .workflow import file_state, harness_files
    refs, ini = dict(plan.references), project / 'ahl.json'
    try:
        settings, sha = config.load(ini) if os.path.lexists(ini) else (None, None)
    except (ValueError, OSError):
        settings, sha = None, 'unreadable'
    try:  # the run's own discovery rule, so a harness file added since the plan counts as a change
        inventory = {str(p.relative_to(project)): file_state(p)
                     for p in harness_files(project, settings['harness_paths'] if settings else None)}
    except (ValueError, OSError):
        inventory = None
    candidate = next((c for c in [run / 'candidate', *sorted(run.glob('candidate-*'))] if _states(c, before) == after), None)
    return dict(project=refs['project'] == digest(str(project)), configuration=sha == refs['configuration'],
                policy=refs['policy'] == hashlib.sha256(Path(policy.__file__).read_bytes()).hexdigest(),
                project_inventory=inventory == before, baseline=_states(run / 'baseline', before) == before,
                candidate=candidate is not None), candidate


def _reproduced(plan, before, after, baseline, candidate):
    """Recompute every operation as the one bounded repair (BOM removal before a minimal skill header); trust no label."""
    from .one_call import structural_candidate
    try:
        return all(op.action == 'update' and PurePosixPath(op.path).name == 'SKILL.md' and before[op.path][1] == after[op.path][1]
                   and structural_candidate((baseline / op.path).read_bytes(), (baseline / op.path).parent.name)
                   == (candidate / op.path).read_bytes() for op in plan.operations)
    except (ValueError, OSError):
        return False


def _blockers(plan, kind, project, before, after, baseline, candidate):
    from .read_only import FrozenProjectError, require_writable
    try:
        require_writable(project)
    except FrozenProjectError:
        yield 'project_frozen_read_only'
    if plan.unresolved_operations:
        yield 'unresolved_operation'
    if os.path.lexists(project / 'ahl.json'):  # a project configuration keeps every plan recommendation-only
        yield 'configuration_requires_recommendation_only'
    if kind == BEHAVIORAL:  # saved reports are editable, so none can recreate certification (#8/#9)
        yield 'behavioral_application_requires_live_certification'
    elif not _reproduced(plan, before, after, baseline, candidate):
        yield 'repair_not_reproduced_by_independent_check'


def _lines(old, new):
    try:
        a, b = (p.read_text('utf-8').splitlines() if p.is_file() else [] for p in (old, new))
    except (UnicodeDecodeError, OSError):
        return None
    rows = [row for row in difflib.unified_diff(a, b, n=0, lineterm='') if row[:3] not in ('+++', '---')]
    return dict(added=sum(row[0] == '+' for row in rows), removed=sum(row[0] == '-' for row in rows))


def _operations(plan, baseline, candidate):
    before, after = ({r.path: dict(sha256=r.sha256, mode=f'{r.mode:04o}') for r in rows} for rows in (plan.before, plan.after))
    return [dict(path=op.path, action=op.action, surface=op.surface, facets=list(op.facets), before=before.get(op.path),
                 after=after.get(op.path), lines=candidate and _lines(baseline / op.path, candidate / op.path))
            for op in plan.operations]


def _saved(run):
    """Facts the run reported about itself: declared and unverified, shown to the reviewer and never read as authority."""
    try:
        saved = _read(run / 'workflow.json')
        gates = saved.get('implementation_prerequisites', {}).get('mandatory_certification', {})
        return dict(source='saved_report_declared_unverified',
                    **{name: saved[name] for name in ('status', 'reason', 'recommendation', 'evidence_strength', 'budget_stages', 'inference') if name in saved},
                    certification_prerequisites={name: row.get('status') for name, row in gates.items()})
    except (ValueError, OSError, AttributeError):
        return None


def review(run, project, authorize=None):
    """Return the review packet. Only an authorized, fully bound and policy-permitted plan writes the project."""
    from .harness_change_plan import HarnessChangePlan
    run, project = Path(run).resolve(strict=True), Path(project).resolve(strict=True)
    if not run.is_dir() or not project.is_dir():
        raise ValueError('run and project must be directories')
    document = _read(run / 'change-plan.json')
    if not isinstance(document, dict):
        raise ValueError('saved change plan must be a JSON object')
    plan = HarnessChangePlan.parse(document.get('harness_change_plan'))
    if document.get('plan_sha256') != plan.sha256:
        raise ValueError('saved plan identity does not match its content')
    if authorize is not None and authorize != plan.sha256:
        raise ValueError('authorization names a different plan than the one reviewed')
    before, after = ({r.path: (r.sha256, r.mode) for r in rows} for rows in (plan.before, plan.after))
    packet = dict(schema_version=1, profile=PROFILE, plan_sha256=plan.sha256, plan_kind=document.get('kind'), run=str(run),
                  project=str(project), evaluation=_saved(run), binding=None, blockers=[], operations=[],
                  publication_allowed=False, adoption_authority=False, certification='not_established',
                  activation='unknown', behavioral_effect='not_established')
    if not plan.operations:
        return dict(packet, status='no_change')
    checks, candidate = _bind(plan, run, project, before, after)
    bound = all(checks.values())
    packet.update(binding={name: 'matched' if ok else 'changed' for name, ok in checks.items()},
                  binding_not_rechecked=['runtime', 'analysis', 'evidence', 'authorization'],
                  operations=_operations(plan, run / 'baseline', candidate if bound else None))
    if not bound:
        return dict(packet, status='stale')
    blockers = list(_blockers(plan, document.get('kind'), project, before, after, run / 'baseline', candidate))
    if blockers:
        return dict(packet, status='blocked', blockers=blockers)
    if authorize is None:
        return dict(packet, status='awaiting_review', authorize_with=f'ahl apply {run} --project {project} --authorize-plan {plan.sha256}')
    from .workflow import adopt_with_receipt
    receipt = adopt_with_receipt(project, run / 'baseline', candidate, before, after,
                                 provenance=dict(source='reviewed_plan_apply', plan_sha256=plan.sha256))
    return dict(packet, status=receipt['status'], receipt=receipt)


def _interrupted(project, plan):
    """Guidance after an interruption. A review writes nothing; an authorization may have started a journaled write."""
    if plan is None:
        return 'ahl apply: interrupted; a review writes nothing, repeat the command'
    from .workflow import identity, state_root
    project = Path(project).resolve()
    try:  # the scan only guides the reader: an unreadable record must not hide the interruption
        records = (json.loads(path.read_text()) for path in (state_root() / 'promotions' / identity(project)).glob('*.json') if path.name != 'active.json')
        started = sorted(r['deployment'] for r in records if r.get('status') == 'applying' and (r.get('metadata') or {}).get('plan_sha256') == plan)
    except (OSError, ValueError, KeyError, AttributeError):
        started = []
    advice = ('ahl apply: interrupted during an authorized application. Repeat the same command: it applies once when the project is unchanged '
              'and reports stale when the reviewed bytes are already installed.')
    if started:
        advice += f' Finish an unfinished deployment with: ahl promotion {project} --deployment ID --action recover (unfinished: {", ".join(started)})'
    return advice


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl apply',
        description='Review the saved change plan of one improvement run, and write the project only when the plan still matches it, the application policy permits it and the exact plan hash is authorized.',
        epilog='Without --authorize-plan this is read-only: it prints a review packet (operations with hashes, modes and line counts; binding checks; blockers; the saved report\'s declared evaluation and prerequisite statuses) and changes nothing. '
               'With the exact plan SHA-256 it writes only a deterministic skill-header repair that is recomputed from the staged bytes, through the private rollback journal; a project ahl.json keeps every plan recommendation-only. '
               'Behavioral candidates stay blocked: a saved report cannot recreate certification and an authorization is not certification. A changed project, configuration, baseline, candidate or policy makes the plan stale. No model call, network or commit. '
               'Exit: 0 awaiting review, no change or applied and verified; 1 execution error or an interruption; 2 invalid input or an authorization naming another plan; 3 stale, blocked or applied but unverified. '
               'Example: ahl apply RUN_DIRECTORY --project . --authorize-plan PLAN_SHA256')
    parser.add_argument('run', type=Path, help='Run output directory printed by ahl improve (holds change-plan.json, baseline and candidate)')
    parser.add_argument('--project', type=Path, default=Path.cwd(), help='Existing project directory the plan was made for (default: current directory)')
    parser.add_argument('--authorize-plan', metavar='SHA256', help='Exact plan_sha256 of the reviewed plan; without it nothing is written')
    args = parser.parse_args(argv)
    try:
        packet = review(args.run, args.project, args.authorize_plan)
    except (ValueError, TypeError, KeyError, FileNotFoundError, NotADirectoryError) as error:
        print(f'ahl apply: {error}', file=sys.stderr)
        return 2
    except OSError as error:
        receipt = getattr(error, 'receipt', None)  # a guarded transaction reports what it left behind
        print(f'ahl apply: {error}', file=sys.stderr)
        if receipt is not None:
            print(json.dumps(receipt, indent=2))
        return 1
    except KeyboardInterrupt:
        print(_interrupted(args.project, args.authorize_plan), file=sys.stderr)
        return 1
    print(json.dumps(packet, indent=2, allow_nan=False))
    return 0 if packet['status'] in ('awaiting_review', 'no_change', 'applied_verified') else 3
