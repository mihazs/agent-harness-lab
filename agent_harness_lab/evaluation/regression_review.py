"""Private controller-observed case lifecycle; operator attestations are not auth."""
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.private_journal import MAX_EVENTS, MAX_METADATA, PrivateJournal, encoded, record, sync_directory, write_bytes
from .bank_tools import materialize_sources
from .integrity import artifacts_digest, contained, copy_frozen, digest, read_regular, tree
from .regression_controls import containment, read_draft
from .regression_replay import outcome_rows, read_spec, replay
from .regressions import fields, load, references, sha256, text
from .task_bank import identifier

def transition(state, kind, target, previous_kind=None):
    if state is None: return kind == 'reported' and target == 'reported'
    if kind == 'attempt_started': return state == target == 'reported' and previous_kind != 'attempt_started'
    if kind == 'attempt_finished': return state == target == 'reported' and previous_kind == 'attempt_started'
    if kind == 'attempt_abandoned': return state == target == 'reported' and previous_kind == 'attempt_started'
    if kind == 'reproduced': return state == 'reported' and target == 'reproduced' and previous_kind == 'attempt_started'
    if kind == 'drafted': return state == 'reproduced' and target == 'drafted'
    if kind == 'reviewed': return state == 'drafted' and target == 'reviewed'
    if kind == 'accepted': return state == 'reviewed' and target == 'accepted'
    return kind == target and target in ('quarantined', 'rejected', 'retired') and state not in ('rejected', 'retired', target)


def review_value(review, case_id):
    fields(review, 'schema_version case_id expected_event_sha256 evidence_sha256 decision reviewer evidence_refs reason')
    if type(review['schema_version']) is not int or review['schema_version'] != 1 or review['case_id'] != case_id:
        raise ValueError('review case/schema mismatch')
    sha256(review['expected_event_sha256']); text(review['reason']); references(review['evidence_refs'])
    reviewer = review['reviewer']; fields(reviewer, 'identity source provenance basis_kind basis')
    if review['decision'] not in ('reviewed', 'quarantined', 'rejected', 'retired', 'reported'):
        raise ValueError('unsupported operator review decision')
    identifier(reviewer['identity']); text(reviewer['identity']); text(reviewer['provenance']); text(reviewer['basis'])
    text(reviewer['basis_kind'])
    if reviewer['source'] != 'operator_attestation': raise ValueError('review requires explicit operator provenance')
    return review


class ReviewStore(PrivateJournal):
    def __init__(self, root):
        super().__init__(root, 'regression-review-store', transition)

    def opened(self, case_id):
        case = self.case(case_id); events = self.history(case)
        inputs, identity = record(case/'inputs.json')
        fields(inputs, 'schema_version author origins hashes privacy')
        if (type(inputs['schema_version']) is not int or inputs['schema_version'] != 1
                or set(inputs['origins']) != {'proposal.json', 'draft.json', 'spec.json'}
                or set(inputs['hashes']) != set(inputs['origins'])
                or hashlib.sha256(encoded(inputs['hashes'])).hexdigest() != case_id):
            raise ValueError('invalid case input manifest')
        identifier(inputs['author']); text(inputs['author'])
        if inputs['privacy'] not in ('private', 'public_or_synthetic_source'):
            raise ValueError('invalid case privacy class')
        if events[0][0]['payload'].get('inputs_sha256') != identity:
            raise ValueError('case input manifest changed')
        for name, expected in inputs['hashes'].items():
            if hashlib.sha256(read_regular(contained(case, name), 256*1024)).hexdigest() != expected:
                raise ValueError('stored case declaration changed')
        return case, inputs, events

    def summary(self, case_id, inputs, events):
        last = events[-1]
        evidence = next((e['payload']['evidence_sha256'] for e, _ in reversed(events) if e['kind'] in ('reproduced', 'drafted')), None)
        attempt = next((e for e, _ in reversed(events) if e['kind'] in ('attempt_started', 'attempt_finished', 'attempt_abandoned', 'reproduced')), None)
        status = None if attempt is None else {'attempt_started':'uncommitted', 'attempt_abandoned':'abandoned', 'reproduced':'reproduced'}.get(attempt['kind'], attempt['payload'].get('status'))
        if status not in (None, 'uncommitted', 'abandoned', 'reproduced', 'blocked', 'budget_exhausted', 'incomplete', 'replay_completed',
                          'controls_failed', 'invalid_input', 'environment_or_grader_defect', 'containment_error', 'grader_error', 'grader_timeout'):
            status = 'unverified'
        disposition = None if attempt is None else ('reproduced' if attempt['kind'] == 'reproduced' else attempt['payload'].get('disposition'))
        if disposition not in (None, 'reproduced', 'not_reproduced', 'baseline_already_passes', 'unstable_control',
                               'minimization_boundary_changed', 'earlier_valid_not_reproduced', 'insufficient_evidence'):
            disposition = 'unverified'
        result = dict(schema_version=1, case_id=case_id, state=last[0]['state'], event_sha256=last[1],
            evidence_sha256=evidence, privacy=inputs['privacy'],
            last_attempt_status=status,
            last_attempt_disposition=disposition,
            attempt_disposition='uncommitted' if last[0]['kind'] == 'attempt_started' else 'recorded',
            review_independence='operator_attested' if last[0]['state'] == 'reviewed' else 'unverified',
            historical_verification='unverified', independence='unverified', bank_admission=False,
            automatic_promotion=False, publication_allowed=False)
        accepted = next((e for e,_ in reversed(events) if e['kind'] == 'accepted'),None)
        if accepted is not None:
            from .bank_revisions import revision_path, validate_admission
            revision = accepted['payload']['revision_sha256']
            checked = validate_admission(revision_path(self,revision)/'bank.json')
            result.update(bank_revision_sha256=revision,bank_admission=checked['current_eligible'])
        return result

    def show(self, case_id):
        with self.locked():
            _, inputs, events = self.opened(case_id)
            return self.summary(case_id, inputs, events)

    def propose(self, proposal_path, draft_path, spec_path, author):
        identifier(author); text(author)
        origins = {name:Path(path).absolute() for name, path in
                   (('proposal.json', proposal_path), ('draft.json', draft_path), ('spec.json', spec_path))}
        if any('..' in path.parts for path in origins.values()): raise ValueError('case declaration paths must be canonical')
        raw = {name:read_regular(path, 256*1024) for name, path in origins.items()}
        with tempfile.TemporaryDirectory() as folder:
            check = Path(folder)
            for name, value in raw.items(): write_bytes(check/name, value)
            proposal, p_hash = load(check/'proposal.json')
            descriptor, task, d_hash = read_draft(check/'draft.json'); spec, s_hash = read_spec(check/'spec.json')
        if spec['proposal_sha256'] != p_hash or spec['draft_sha256'] != d_hash or descriptor['proposal_sha256'] != p_hash:
            raise ValueError('case declaration identities mismatch')
        if not all(getattr(task, key) == proposal.value[key] for key in ('root_id', 'family')) or task.source_kind != proposal.value['source']['kind']:
            raise ValueError('case task provenance mismatch')
        draft_root, spec_root = origins['draft.json'].parent, origins['spec.json'].parent
        protected = [contained(draft_root, value) for value in (task.fixture, task.grader_bundle)]
        protected.extend(contained(draft_root, c['fixture']) for c in descriptor['controls'])
        protected.append(contained(spec_root, spec['task_start']))
        snapshots = [spec['before'], spec['after']]
        if spec['earlier_valid'] is not None: snapshots.append(spec['earlier_valid']['paths'])
        protected.extend(contained(spec_root, value) for snapshot in snapshots for value in snapshot.values())
        if any(self.root.is_relative_to(p) or p.is_relative_to(self.root) for p in protected):
            raise ValueError('controller store and case source trees must not overlap')
        hashes = dict(zip(origins, (p_hash, d_hash, s_hash)))
        case_id = hashlib.sha256(encoded(hashes)).hexdigest()
        inputs = dict(schema_version=1, author=author, origins={n:str(p) for n, p in origins.items()}, hashes=hashes,
                      privacy='private' if task.source_kind == 'private' else 'public_or_synthetic_source')
        with self.locked(create=True), tempfile.TemporaryDirectory(dir=self.root, prefix='.case-stage-') as folder:
            staged = Path(folder)/'case'; staged.mkdir(mode=0o700); (staged/'events').mkdir(mode=0o700)
            for name, value in raw.items(): write_bytes(staged/name, value)
            write_bytes(staged/'inputs.json', encoded(inputs)); sync_directory(staged)
            events = []; self.append(staged, events, 'reported', 'reported', dict(inputs_sha256=hashlib.sha256(encoded(inputs)).hexdigest()))
            cases = self.root/'cases'; cases.mkdir(mode=0o700, exist_ok=True)
            destination = self.case(case_id)
            if destination.exists(): raise ValueError('case already reported; existing history retained')
            staged.rename(destination); sync_directory(cases)
            sync_directory(self.root)
            return self.summary(case_id, inputs, events)

    def packet(self, case, inputs, run):
        proposal, _ = load(case/'proposal.json'); descriptor, task, _ = read_draft(case/'draft.json')
        spec, _ = read_spec(case/'spec.json')
        result, result_hash = record(run/'replay.json')
        if (result.get('status') != 'replay_completed' or result.get('reproduction_disposition') != 'reproduced'
                or any(result.get(key) is not True for key in ('aa_observed_agreement', 'minimization_boundary_preserved', 'replay_task_failure_observed'))
                or any(result.get(key) is not False for key in ('task_failure_verified', 'bank_admission', 'automatic_promotion', 'publication_allowed'))
                or result.get('execution_kind') != 'offline_replay'):
            raise ValueError('case needs intact controller-observed reproduction, not merely a completed replay')
        if (result.get('proposal_sha256') != inputs['hashes']['proposal.json'] or result.get('draft_sha256') != inputs['hashes']['draft.json']
                or result.get('replay_spec_sha256') != inputs['hashes']['spec.json']):
            raise ValueError('reproduction does not match case declaration bytes')
        expected_views = {'before', 'after', *(['earlier'] if spec['earlier_valid'] is not None else [])}
        if (set(result['execution_view_sha256']) != expected_views
                or set(result['materialized_fixture_sha256']) != {'recorded', 'minimized'}):
            raise ValueError('unexpected controller snapshot view')
        files = {'replay/replay.json':result_hash}; trees = {}; outputs = {}
        for name, identity in result['execution_view_sha256'].items():
            path = run/'inputs'/name
            if digest(path) != identity: raise ValueError('frozen execution view changed')
            expected = proposal.value[name] if name != 'earlier' else spec['earlier_valid']['identities']
            if {p.name for p in path.iterdir()} != {'harness', 'config', 'memory'} or any(
                    digest(path/component) != expected[component+'_sha256'] for component in ('harness', 'config', 'memory')):
                raise ValueError('snapshot execution assembly changed')
            trees['replay/inputs/'+name] = identity
        for name, identity in dict(result['materialized_fixture_sha256'], oracle=task.grader_digest).items():
            path = run/'inputs'/name
            if digest(path) != identity: raise ValueError('frozen fixture/oracle changed')
            trees['replay/inputs/'+name] = identity
        controls, control_hash = record(run/'controls/review.json', 1024*1024)
        if control_hash != result['control_receipt_sha256'] or controls['status'] != 'grader_controls_passed':
            raise ValueError('controller control receipt changed')
        files['replay/controls/review.json'] = control_hash
        if len(controls['checks']) != 5: raise ValueError('missing grader controls')
        for index, (control, checked) in enumerate(zip(descriptor['controls'], controls['checks'])):
            expected = control['state'] in ('complete', 'alternative_valid')
            if (checked['control'] != control['state'] or checked['observed'] is not expected or checked['input_sha256'] != control['digest']
                    or not containment({'attestation':checked.get('attestation')}, proposal.value['environment']['image_sha256'])):
                raise ValueError('invalid or unattested control evidence')
            for prefix, identity in (('controls-source', control['digest']), ('controls-materialized', checked['materialized_sha256'])):
                path = run.parent/prefix/str(index)
                if digest(path) != identity: raise ValueError('frozen control evidence changed')
                trees[prefix+'/'+str(index)] = identity
        phases = {'aa', 'ab', *(['earlier'] if spec['earlier_valid'] is not None else [])}
        if set(result['phases']) != phases or result.get('task_trials') != 4*len(phases):
            raise ValueError('missing or unexpected replay phase')
        for phase in sorted(phases):
            phase_result, phase_hash = record(run/phase/'results.json', 8*1024*1024)
            rows, error = outcome_rows(phase_result, task, proposal.value['environment']['image_sha256'])
            if error or phase_hash != result['phases'][phase]['results_sha256']:
                raise ValueError('graded phase evidence changed')
            files['replay/'+phase+'/results.json'] = phase_hash
            for trial in phase_result['trials']:
                key = trial['task']+'/'+trial['version']; declared = result['phases'][phase]['outcomes'][key]
                actual = artifacts_digest(run/phase/trial['version']/trial['task']/'workspace', task.artifacts)
                if declared['outcome'] is not rows[(trial['task'], trial['version'])] or declared['artifacts_sha256'] != actual:
                    raise ValueError('graded artifact evidence changed')
                outputs[phase+'/'+key] = actual
        return dict(schema_version=1, files=files, trees=trees, artifacts=outputs,
                    historical_verification='unverified', independence='unverified', bank_admission=False)

    def checked_evidence(self, case, inputs, events):
        try:
            event = next(e for e, _ in reversed(events) if e['kind'] == 'reproduced')
            attempt = contained(case, event['payload']['attempt'])
            stored, identity = record(attempt/'evidence.json')
            if identity != event['payload']['evidence_sha256'] or stored != self.packet(case, inputs, attempt/'replay'):
                raise ValueError('sealed case evidence changed')
            return identity
        except (OSError, ValueError, KeyError, StopIteration):
            self.append(case, events, 'quarantined', 'quarantined', {'reason':'sealed_evidence_changed'})
            raise ValueError('case evidence changed; quarantined') from None

    def sync_evidence(self, case, attempt, packet, task, deadline):
        files = {contained(attempt, name) for name in packet['files']}
        folders = {case, attempt.parent, attempt}
        for name in packet['trees']:
            root = contained(attempt, name); folders.add(root)
            files.update(contained(root, relative) for relative in tree(root))
        for key in packet['artifacts']:
            phase, name, version = key.split('/')
            workspace = attempt/'replay'/phase/version/name/'workspace'
            files.update(contained(workspace, name) for name in task.artifacts)
        for path in files:
            if time.monotonic() >= deadline: raise TimeoutError('case evidence sync budget exhausted')
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
            folders.update(p for p in path.parents if p.is_relative_to(attempt))
        for path in sorted(folders, key=lambda p:len(p.parts), reverse=True):
            if time.monotonic() >= deadline: raise TimeoutError('case directory sync budget exhausted')
            sync_directory(path)

    def reproduce(self, case_id, backend, *, budget_seconds=60):
        started = time.monotonic()
        if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 300:
            raise ValueError('case reproduction budget must be positive and at most 300 seconds')
        deadline = started+budget_seconds
        if backend.cgroup_root is None or backend.image is None or backend.disk_bytes is None:
            raise ValueError('case reproduction requires the full offline profile')
        with self.locked():
            case, inputs, events = self.opened(case_id)
            if events[-1][0]['state'] != 'reported' or events[-1][0]['kind'] == 'attempt_started':
                raise ValueError('case is not ready; uncommitted attempts require operator intervention')
            # Leave room for completion, draft/review, acceptance and two operator decisions.
            if len(events)+7 > MAX_EVENTS: raise ValueError('case journal lacks lifecycle completion capacity')
            try:
                sources_match = all(hashlib.sha256(read_regular(p, 256*1024)).hexdigest() == inputs['hashes'][n]
                                    for n, p in inputs['origins'].items())
            except (OSError, ValueError): sources_match = False
            if not sources_match:
                self.append(case, events, 'quarantined', 'quarantined', {'reason':'declaration_changed'})
                raise ValueError('case source declarations changed; case quarantined')
            relative = 'attempts/'+secrets.token_hex(16); attempt = contained(case, relative)
            (case/'attempts').mkdir(mode=0o700, exist_ok=True)
            attempt.mkdir(mode=0o700)
            self.append(case, events, 'attempt_started', 'reported', dict(attempt=relative, budget_seconds=budget_seconds))
            try:
                descriptor, task, _ = read_draft(case/'draft.json')
                raw_controls = attempt/'controls-source'; raw_controls.mkdir(mode=0o700)
                materialized = attempt/'controls-materialized'; materialized.mkdir(mode=0o700)
                origin = Path(inputs['origins']['draft.json']).parent
                for index, control in enumerate(descriptor['controls']):
                    if time.monotonic() >= deadline: raise TimeoutError('case control freeze budget exhausted')
                    copy_frozen(contained(origin, control['fixture']), raw_controls/str(index), control['digest'])
                    copy_frozen(raw_controls/str(index), materialized/str(index), control['digest'])
                    materialize_sources(materialized/str(index))
                if time.monotonic() >= deadline: raise TimeoutError('case preflight budget exhausted')
                replay(inputs['origins']['proposal.json'], inputs['origins']['draft.json'], inputs['origins']['spec.json'],
                       attempt/'replay', backend, budget_seconds=budget_seconds, parent_deadline=deadline)
                result, _ = record(attempt/'replay/replay.json')
                if result['status'] != 'replay_completed' or result.get('reproduction_disposition') != 'reproduced':
                    self.append(case, events, 'attempt_finished', 'reported', dict(attempt=relative, status=result['status'],
                                disposition=result.get('reproduction_disposition')))
                else:
                    packet = self.packet(case, inputs, attempt/'replay')
                    self.sync_evidence(case, attempt, packet, task, deadline)
                    atomic_json(attempt/'evidence.json', packet)
                    identity = record(attempt/'evidence.json')[1]
                    if time.monotonic() >= deadline: raise TimeoutError('case evidence budget exhausted')
                    self.append(case, events, 'reproduced', 'reproduced', dict(attempt=relative, evidence_sha256=identity))
            except TimeoutError:
                self.append(case, events, 'attempt_finished', 'reported', dict(attempt=relative, status='budget_exhausted'))
            except (OSError, ValueError, subprocess.SubprocessError):
                self.append(case, events, 'attempt_finished', 'reported', dict(attempt=relative, status='incomplete'))
            return self.summary(case_id, inputs, events)

    def draft(self, case_id):
        with self.locked():
            case, inputs, events = self.opened(case_id)
            if events[-1][0]['state'] != 'reproduced': raise ValueError('drafting requires reproduced evidence')
            identity = self.checked_evidence(case, inputs, events)
            self.append(case, events, 'drafted', 'drafted', dict(evidence_sha256=identity))
            return self.summary(case_id, inputs, events)

    def accept(self, case_id, parent_path, record_path):
        from .bank_revisions import accept
        return accept(self, case_id, parent_path, record_path)

    def review(self, case_id, review_path):
        review, _ = record(review_path, 64*1024)
        review_value(review, case_id); reviewer = review['reviewer']
        with self.locked():
            case, inputs, events = self.opened(case_id); current = self.summary(case_id, inputs, events)
            if review['expected_event_sha256'] != current['event_sha256'] or review['evidence_sha256'] != current['evidence_sha256']:
                raise ValueError('stale review event/evidence identity')
            decision = review['decision']
            if decision == 'reviewed':
                if current['state'] != 'drafted' or reviewer['identity'].casefold() == inputs['author'].casefold():
                    raise ValueError('positive review requires a drafted case and a separate reviewer')
                if reviewer['basis_kind'] != 'independent_artifact_review' or not review['evidence_refs']:
                    raise ValueError('fresh sessions alone do not establish independent artifact review')
                self.checked_evidence(case, inputs, events)
            kind = 'attempt_abandoned' if decision == 'reported' else decision
            self.append(case, events, kind, decision, dict(review=review))
            return self.summary(case_id, inputs, events)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl regression-review', description=__doc__)
    parser.add_argument('--store', type=Path, required=True, help='Explicit private owner-only controller store')
    actions = parser.add_subparsers(dest='action', required=True)
    propose = actions.add_parser('propose', help='Record exact authorized P/D/R declarations without executing them')
    propose.add_argument('--proposal', type=Path, required=True)
    propose.add_argument('--draft', type=Path, required=True)
    propose.add_argument('--replay', type=Path, required=True)
    propose.add_argument('--author', required=True)
    for name in ('show', 'reproduce', 'draft', 'review', 'accept'):
        command = actions.add_parser(name)
        command.add_argument('--case', required=True)
        if name in ('review','accept'): command.add_argument('--record', type=Path, required=True)
        if name == 'accept': command.add_argument('--parent-bank', type=Path, required=True)
        if name == 'reproduce':
            command.add_argument('--cgroup-root', type=Path, required=True)
            command.add_argument('--image-root', type=Path, required=True)
            command.add_argument('--image-sha256', required=True)
            command.add_argument('--disk-bytes', type=int, required=True)
            command.add_argument('--budget-seconds', type=float, default=60)
    args = parser.parse_args(argv)
    try:
        store = ReviewStore(args.store)
        if args.action == 'propose': result = store.propose(args.proposal, args.draft, args.replay, args.author)
        elif args.action == 'reproduce':
            from .sandbox import SandboxBackend
            backend = SandboxBackend(cgroup_root=args.cgroup_root, image_root=args.image_root,
                                     image_sha256=args.image_sha256, disk_bytes=args.disk_bytes)
            result = store.reproduce(args.case, backend, budget_seconds=args.budget_seconds)
        elif args.action == 'draft': result = store.draft(args.case)
        elif args.action == 'review': result = store.review(args.case, args.record)
        elif args.action == 'accept': result = store.accept(args.case, args.parent_bank, args.record)
        else: result = store.show(args.case)
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.action == 'reproduce' and result['state'] != 'reproduced':
            return 3 if result['last_attempt_status'] == 'blocked' else 1
        return 0
    except BlockingIOError:
        print(json.dumps(dict(status='busy', bank_admission=False, publication_allowed=False)))
        return 3
    except (ValueError, OSError, TypeError, KeyError, StopIteration):
        parser.exit(2, 'regression case error: invalid or unavailable local input; private case history retained\n')
