"""Incremental analyze: reuse one verified deterministic stage output for unchanged input, and say exactly why.

One stage only: the process-mining result of `ahl analyze --events FILE`. Its inputs are exact digests (the capture
bytes and the project's harness files), so a changed event or instruction file yields a new node key and a miss.
No model is called, nothing is scanned or retrieved (the capture is named explicitly), and the store only holds
deterministic output: live trials and proposals are never reusable."""
import contextlib
import hashlib
import json
import os
from pathlib import Path

from . import commit as stage_commit, reuse
from .store import MemoryStore, MemoryStoreError

STAGE = 'diagnostic_process_mining'
# Bump when normalization, ProcessMiner or analyze's mining inputs change the output; a golden test pins the output.
STAGE_VERSION = 'process-graph-1'
PROFILE = 'ahl-analysis-process-graph-v1'
GRAPH = {STAGE: ()}  # the one stage this slice reuses; its upstream inputs enter as exact digests
SUMMARY_SCHEMA = 'ahl-incremental-analysis-v1'
ROLE_VARIABLE = 'AHL_SANDBOX_ROLE'  # isolated sandbox roles (worker, proposer, evaluator author, grader)
HARNESS_DOMAIN = b'ahl-harness-identity-v1\x00'


def encode(value, sort=False):
    """The versioned output form: compact UTF-8 JSON; stage output keeps the stage's own key order."""
    return json.dumps(value, sort_keys=sort, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def harness_identity(project):
    """Digest of the exact harness file set with content and mode; refuses what it cannot hash (a symlink)."""
    from agent_harness_lab.workflow import MAX_BYTES, MAX_FILES, file_state, harness_files
    states, total = {}, 0
    for path in harness_files(project):
        total += path.stat().st_size
        if len(states) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError('harness identity exceeds its file/byte limit')
        states[str(path.relative_to(project))] = file_state(path)
    return hashlib.sha256(HARNESS_DOMAIN + encode(states, sort=True)).hexdigest()


def require(args):
    """Flag contract, checked before any output or store exists."""
    if not args.incremental:
        if args.memory_store is not None or args.explain_reuse:
            raise ValueError('--memory-store and --explain-reuse require --incremental')
        return
    if args.memory_store is None:
        raise ValueError('--incremental requires an explicit --memory-store; there is no default location')
    if args.events is None:
        raise ValueError('--incremental requires an explicit --events capture; --memory-store does not enable '
                         'discovery, source scanning or retrieval')
    from agent_harness_lab.telemetry.claude_native import VERSION as CLAUDE_NATIVE
    from agent_harness_lab.telemetry.codex_rollout import VERSION as CODEX_NATIVE
    if (args.format, args.source_version) in (('codex', CODEX_NATIVE), ('claude', CLAUDE_NATIVE)):
        raise ValueError('--incremental does not support pinned native profiles: their reads depend on the project directory')
    if os.environ.get(ROLE_VARIABLE):
        raise ValueError('the memory store is not available to isolated roles')


class Incremental:
    """One analyze run: the reuse decision before the stage runs, verification and commit after it."""

    def __init__(self, args):
        require(args)
        self.project, self.explain = Path(args.project), bool(args.explain_reuse)
        named = (getattr(args, name, None) for name in ('output', 'events', 'source_cache', 'history_store', 'incident_corpus'))
        self.store = MemoryStore.open(args.memory_store, excluded=[self.project, *(p for p in named if p is not None)])
        self.project_id = hashlib.sha256(str(self.project).encode()).hexdigest()
        events = Path(args.events)
        if events.is_symlink() or not events.is_file():
            raise ValueError('events must be an existing regular file, not a symlink')
        self.events_sha256, self.harness_sha256 = file_sha256(events), harness_identity(self.project)
        request = {'stage_version': STAGE_VERSION, 'external_inputs': [self.events_sha256, self.harness_sha256],
                   'scope': {'project': self.project_id}, 'config': {'format': args.format, 'source_version': args.source_version},
                   'policy': {'profile': PROFILE}}
        self.parts = reuse.components(STAGE, STAGE_VERSION, request['external_inputs'], request['scope'],
                                      request['config'], request['policy'])
        self.key = reuse.node_key(self.parts)
        self.done, self.compared = False, None
        prior, live, self.cached = self._prior()
        # Freshness is verified: the capture was hashed just now and finish() proves the analysis read those bytes.
        self.receipt = reuse.plan({STAGE: request}, prior, live, source_freshness='verified', graph=GRAPH)
        self.reason = next(row['reason'] for bucket in ('reused', 'executed', 'excluded', 'unknown') for row in self.receipt[bucket])
        if not self.receipt['reused']:
            self.cached = None

    def bind_output(self, output):
        """The run directory is created after the store opens; refuse a store that overlaps it."""
        from .store import _isolated
        _isolated(self.store.root, [output])

    # ---- before: what may be reused ----------------------------------------------------------------------
    def _prior(self):
        """The exact complete record for this key, else the newest one (it explains a miss). Bytes are re-verified."""
        for node_key in (self.key, None):
            rows = self.store.stage_rows(self.project_id, STAGE, node_key=node_key, status='complete', limit=1)
            if rows:
                break
        else:
            return {}, {}, None
        row, exact = rows[0], rows[0]['node_key'] == self.key
        record = self._record(row)
        if record is None and not exact:
            return {}, {}, None  # an unreadable older record explains nothing
        if record is not None and not exact:
            self.compared = {'prior_sequence': row['sequence'], 'inputs': record['components'].get('inputs')}
        value = self._output(record) if exact and record is not None else None
        record = record or {'node_key': self.key, 'components': self.parts, 'output': row['output'], 'status': 'complete'}
        return ({STAGE: {k: record[k] for k in ('node_key', 'components', 'output', 'status')}},
                {STAGE: {'bytes': value is not None, 'role': True}}, value)

    def _record(self, row):
        """The verified record of an index row (the layout and its checks belong to `commit`), or None when unsound."""
        try:
            return stage_commit.verified(self.store, row, STAGE)
        except (MemoryStoreError, OSError):
            return None

    def _output(self, record):
        """The stored stage output, only when its bytes verify and re-encode to themselves (versioned canonical form)."""
        try:
            raw = self.store.read(record['output'])
            value = json.loads(raw)
        except (MemoryStoreError, OSError, ValueError):
            return None
        return value if isinstance(value, dict) and value.get('profile') == PROFILE and encode(value) == raw else None

    # ---- after: verify, commit, explain ------------------------------------------------------------------
    def finish(self, data, elapsed):
        """Fail closed unless the analysis read the digested bytes and any reused output agrees with them."""
        source = (data.get('sources') or [{}])[0]
        if source.get('sha256') != self.events_sha256:
            raise ValueError('the capture changed while it was analyzed; rerun so the reuse record matches the bytes read')
        analysis = data['analysis']
        if self.cached is not None:
            self._agree(analysis)
            commit = 'not_needed'
        elif self.reason == 'invalid_integrity':
            commit = 'withheld: the stored record failed verification; run ahl memory verify, then reindex'
        else:
            commit = self._commit(analysis['process_mining'])
        self.done = True
        return self._summary(commit, elapsed)

    def _agree(self, analysis):
        """A reused result must match what this run just counted from the same bytes."""
        mined = self.cached
        try:
            seen = (mined['events'], mined['cases'], mined['reported_roots'],
                    {name: row['events'] for name, row in mined['activities'].items()})
        except (KeyError, TypeError, AttributeError):
            seen = None
        if seen != (analysis['events'], analysis['cases'], analysis['reported_roots'], dict(analysis['activities'])):
            raise ValueError('reused process mining disagrees with the analyzed events; the stored output was not trusted')

    def _commit(self, mining):
        try:
            raw = encode(mining)
            if json.loads(raw) != mining or encode(json.loads(raw)) != raw:
                raise ValueError('no stable canonical form')
        except (TypeError, ValueError):
            return 'refused: the computed output has no stable canonical form'
        try:
            if self.store.stage_rows(self.project_id, STAGE, node_key=self.key, status='complete', limit=1):
                return 'already_committed'  # a concurrent run committed this exact key first
            stage_commit.commit_stage(self.store, self.project_id, self.parts, raw, graph=GRAPH)
            return 'committed'
        except (MemoryStoreError, OSError) as error:  # busy, read-only or full: the analysis result is still valid
            return f'failed: {error}'

    def interrupted(self):
        """The stage did not complete: keep a diagnostic record that is never reusable. A reuse hit executed nothing."""
        if self.cached is None and not self.done:
            with contextlib.suppress(MemoryStoreError, OSError):
                stage_commit.record_event(self.store, self.project_id, self.parts, 'interrupted', 'analysis_not_completed', graph=GRAPH)

    def _summary(self, commit, elapsed):
        counts = self.receipt['counts']
        total = counts['reused_deterministic'] + counts['executed_deterministic']
        summary = {
            'schema': SUMMARY_SCHEMA, 'stage': STAGE, 'outcome': 'reused' if self.cached is not None else 'executed',
            'reason': self.reason, 'counts': counts,
            # Computed from stage counts, never trials; unknown when no deterministic stage was decided.
            'reused_fraction': counts['reused_deterministic'] / total if total else None, 'commit': commit,
            'counters': {  # kept apart: a local stage reuse is not a provider cache hit and not historical reuse
                'local_deterministic_cache': {'avoided_stages': counts['reused_deterministic'],
                                              'executed_stages': counts['executed_deterministic'],
                                              'elapsed_seconds': round(elapsed, 3), 'avoided_elapsed_seconds': 'unknown'},
                'provider_cache': {'status': 'not_applicable'},
                'historical_reuse': {'reused_live_trials': 0},
                'tokens': {'model_calls': 0, 'billing_savings': 'unknown'}},
            'publication_allowed': False}
        if self.explain:
            summary.update(receipt=self.receipt, node_key=self.key,
                           inputs={'events_sha256': self.events_sha256, 'harness_sha256': self.harness_sha256})
            if self.compared and isinstance(self.compared['inputs'], list):  # which input differs from the newest prior run
                same = {name: 'same' if digest in self.compared['inputs'] else 'changed'
                        for name, digest in (('capture', self.events_sha256), ('harness', self.harness_sha256))}
                summary['compared_to'] = {'prior_sequence': self.compared['prior_sequence'], **same}
        return summary
