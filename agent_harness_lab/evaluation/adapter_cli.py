"""`ahl adapters`: stage one reviewed presentation adapter version and roll it back, under a controller-owned manifest.

The manifest (ahl.adapter-authorization.v1) is the only source of authority: the tools and adapter contracts a project
allows. It must live outside the project, where an optimizer edits, and be writable by nobody else. Candidates arrive
as untrusted files and are reviewed against it without model judgment or execution. Without the exact candidate spec
digest, stage reviews and writes nothing; with it, the two declared artifacts are deployed through a private journal
kept apart from the project's promotion journal. Nothing here installs a hook, calls a runtime or rolls out
automatically, and a review is never evidence that the adapter helps.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys

PROFILE = 'ahl-adapters-v1'
SCHEMA = 'ahl.adapter-authorization.v1'
MANIFEST_FIELDS = ('schema', 'project', 'version', 'tools', 'adapters')
MAX_MANIFEST, MAX_SPEC, MAX_ADAPTER = 256 * 1024, 64 * 1024, 1024 * 1024
BOUNDS = dict(max_json_depth=8, max_json_containers=1000, max_json_values=10000)
_DEPLOYMENT = re.compile(r'[0-9a-f]{32}')


def _load_json(raw, name):
    from ..sources.hardened import load_json
    return load_json(raw, BOUNDS, name)


def load_manifest(path, project):
    """The project's Authorization and its authorized spec per adapter; any doubt about the file is a refusal."""
    from ..workflow import identity
    from .adapters import AdapterSpec, Authorization
    from .integrity import read_regular
    path, project = Path(path), Path(project)
    if path.resolve(strict=True).is_relative_to(project.resolve(strict=True)):
        raise ValueError('authorization manifest must live outside the project')
    raw = read_regular(path, MAX_MANIFEST)  # refuses a symlink, a special file and an oversized one
    if os.lstat(path).st_mode & 0o022:
        raise ValueError('authorization manifest must not be writable by group or others')
    data = _load_json(raw, 'authorization manifest')
    if not isinstance(data, dict) or set(data) != set(MANIFEST_FIELDS) or data['schema'] != SCHEMA:
        raise ValueError('authorization manifest must hold exactly the declared fields of ' + SCHEMA)
    if data['project'] != identity(project):
        raise ValueError('authorization manifest names a different project')
    if not isinstance(data['version'], str) or not all(isinstance(data[name], list) for name in ('tools', 'adapters')) \
            or not data['adapters']:
        raise ValueError('authorization manifest needs a text version, a tool list and at least one adapter')
    specs = [AdapterSpec.from_mapping(row) for row in data['adapters']]
    ids = tuple(spec.adapter_id for spec in specs)
    if len(set(ids)) != len(ids):
        raise ValueError('authorization manifest names an adapter twice')
    return Authorization(data['project'], tuple(data['tools']), ids, data['version']), {s.adapter_id: s for s in specs}


def _packet(action, project, authorization, **fields):
    return dict(schema_version=1, profile=PROFILE, action=action, project=str(project),
                authorization_sha256=authorization.digest, executes_adapter_code=False, publication_allowed=False,
                adoption_authority=False, automatic_promotion=False, certification='not_established',
                activation='unknown', behavioral_effect='not_established', **fields)


def _journal(project, *, existing):
    """The private adapter journal, apart from the promotion journal; a preview never creates it."""
    from ..workflow import identity, state_root
    from .adapter_deployment import AdapterJournal
    folder = state_root() / 'adapters' / identity(project)
    if existing and not folder.is_dir():
        raise ValueError('no adapter deployment was staged for this project')
    return AdapterJournal(project, folder)


def _undo_unfinished(journal):
    """Return every unfinished deployment to its previous state as one unit; a foreign edit is a conflict that writes nothing."""
    for path in sorted(journal.folder.glob('*.json')):
        if _DEPLOYMENT.fullmatch(path.stem):
            try:
                journal.recover(path.stem)
            except ValueError:  # finished, already rejected or superseded: nothing of this command is left to undo
                pass


def _guarded(journal, operation):
    """Run one journal write after undoing a crashed run, and undo it again if this one fails midway."""
    _undo_unfinished(journal)
    try:
        return operation()
    except BaseException:
        _undo_unfinished(journal)
        raise


def stage_command(args, project):
    from .adapter_deployment import check, stage
    from .adapters import AdapterSpec, review_candidate_spec
    from .integrity import read_regular
    authorization, authorized = load_manifest(args.manifest, project)
    candidate = _load_json(read_regular(args.candidate, MAX_SPEC), 'candidate spec')
    adapter_bytes = read_regular(args.adapter_bytes, MAX_ADAPTER)
    if not isinstance(candidate, dict):
        raise ValueError('candidate spec must be a JSON object')
    base = authorized.get(candidate.get('adapter_id'))
    if base is None:
        return _packet('stage', project, authorization, status='blocked', reasons=['adapter_not_authorized'])
    review = review_candidate_spec(base, candidate, authorization)
    if review['status'] != 'within_authorized_capabilities':
        return _packet('stage', project, authorization, status='blocked', reasons=review['reasons'],
                       candidate_spec_sha256=review['candidate_digest'], runtime=review['runtime'])
    spec = AdapterSpec.from_mapping(candidate)  # the review above already parsed it
    shown = dict(adapter_id=spec.adapter_id, candidate_spec_sha256=spec.digest, runtime=review['runtime'])
    if args.authorize_spec is None:
        files = sorted(check(project, base, authorization, candidate, adapter_bytes)[1])
        command = shlex.join(['ahl', 'adapters', 'stage', str(project), '--manifest', str(args.manifest), '--candidate',
                              str(args.candidate), '--adapter-bytes', str(args.adapter_bytes), '--authorize-spec', spec.digest])
        return _packet('stage', project, authorization, status='awaiting_review', reasons=[], authorize_with=command,
                       files=files, **shown)
    if args.authorize_spec != spec.digest:
        raise ValueError('authorization names a different candidate spec than the one reviewed')
    journal = _journal(project, existing=False)
    # stage() checks the deployed bytes itself, after _guarded has undone a crashed earlier deployment.
    record = _guarded(journal, lambda: journal.execute(stage(journal, base, authorization, candidate, adapter_bytes)['deployment']))
    return _packet('stage', project, authorization, status='deployed', reasons=[], deployment=record['deployment'],
                   files=sorted(record['after']),
                   rollback_with=shlex.join(['ahl', 'adapters', 'rollback', str(project), '--manifest', str(args.manifest),
                                             '--deployment', record['deployment'], '--execute']), **shown)


def rollback_command(args, project):
    from .adapter_deployment import preview_rollback, rollback
    authorization, _ = load_manifest(args.manifest, project)
    if not _DEPLOYMENT.fullmatch(args.deployment):
        raise ValueError('deployment must be a 32-hex ID from a staged adapter deployment')
    journal = _journal(project, existing=True)
    command = shlex.join(['ahl', 'adapters', 'rollback', str(project), '--manifest', str(args.manifest),
                          '--deployment', args.deployment, '--execute'])
    try:
        if args.execute:
            try:
                record = _guarded(journal, lambda: rollback(journal, args.deployment, authorization))
                return _packet('rollback', project, authorization, status='rolled_back', deployment=args.deployment,
                               rollback_deployment=record['deployment'])
            except ValueError:  # a conflict leaves every file as it is; the preview names why
                pass
        preview = preview_rollback(journal, args.deployment, authorization)
    except FileNotFoundError:
        raise ValueError('no such adapter deployment for this project') from None
    eligible = preview['status'] == 'rollback_eligible' and not args.execute  # a refused --execute is a conflict
    return _packet('rollback', project, authorization, status='rollback_eligible' if eligible else 'conflict',
                   deployment=args.deployment, preview=preview, **(dict(execute_with=command) if eligible else {}))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl adapters',
        description='Stage one reviewed presentation adapter version in a project, or roll it back, under a controller-owned authorization manifest.',
        epilog='The manifest (ahl.adapter-authorization.v1: schema, project, version, tools, adapters) holds the tools and the authorized adapter contracts. '
               'It must be outside the project, bound to this project path and not writable by group or others, so a candidate or the optimizer cannot change it. '
               'Both actions are read-only unless confirmed: stage needs the exact candidate spec digest it printed, rollback needs --execute. '
               'A deployment writes only .ahl/adapters/ID/adapter.py and spec.json, without an execute bit, through a private journal; a later edit or a newer deployment is a conflict that keeps your file. '
               'Adapter code is never executed, no hook or runtime is touched and nothing is rolled out automatically; a review proves only that a candidate stays inside the declared authority. '
               'Exit: 0 awaiting review, deployed, rollback eligible or rolled back; 1 execution error or interruption; 2 invalid input; 3 candidate blocked or rollback conflict. '
               'Example: ahl adapters stage . --manifest ~/adapters.json --candidate spec.json --adapter-bytes adapter.py')
    sub = parser.add_subparsers(dest='action', required=True, help='Select stage or rollback; ACTION --help describes its inputs')
    stage = sub.add_parser('stage', help='Review a candidate adapter version against the manifest and deploy it only with its spec digest',
        description='Review a candidate adapter spec and its exact bytes against the manifest without executing them; with the digest it prints, deploy both files through the private journal.')
    stage.add_argument('--candidate', type=Path, required=True, help='JSON file holding the candidate adapter spec (ahl.adapter-spec.v1 fields)')
    stage.add_argument('--adapter-bytes', type=Path, required=True, help='Python file whose SHA-256 the candidate spec declares')
    stage.add_argument('--authorize-spec', metavar='SHA256', help='Exact candidate_spec_sha256 of the reviewed candidate; without it nothing is written')
    back = sub.add_parser('rollback', help='Preview or restore the previous adapter version of one deployment',
        description='Preview, or with --execute restore, the exact previous state of one staged deployment unless a later edit, a newer deployment or the manifest forbids it.')
    back.add_argument('--deployment', required=True, help='32-hex deployment ID printed by ahl adapters stage')
    back.add_argument('--execute', action='store_true', help='Restore the previous version; without it the command only previews')
    for command in (stage, back):
        command.add_argument('project', type=Path, help='Existing project directory that holds .ahl/adapters')
        command.add_argument('--manifest', type=Path, required=True, help='Controller-owned authorization manifest outside the project')
    args = parser.parse_args(argv)
    try:
        project = args.project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError('project must be a directory')
        packet = (stage_command if args.action == 'stage' else rollback_command)(args, project)
    except (ValueError, TypeError, KeyError, FileNotFoundError, NotADirectoryError) as error:
        print(f'ahl adapters: {error}', file=sys.stderr)
        return 2
    except OSError as error:
        receipt = getattr(error, 'receipt', None)  # a guarded transaction reports what it left behind
        print(f'ahl adapters: {error}', file=sys.stderr)
        if receipt is not None:
            print(json.dumps(receipt, indent=2))
        return 1
    except KeyboardInterrupt:
        print('ahl adapters: interrupted; a deployment or rollback that had started was returned to its previous state', file=sys.stderr)
        return 1
    print(json.dumps(packet, indent=2, allow_nan=False))
    return 3 if packet['status'] in ('blocked', 'conflict') else 0
