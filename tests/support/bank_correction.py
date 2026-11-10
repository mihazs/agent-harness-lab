"""Shared synthetic reviewed bank correction."""
from contextlib import contextmanager
import hashlib
import json

from agent_harness_lab.evaluation.oracle_history import HistoryStore
from tests.integration.evaluation import test_grader_correction as corrections
from tests.support.unittest_cleanups import run_cleanups


class BankCorrection:
    """A reviewed synthetic correction beside its parent bank."""

    def __init__(self, fixture):
        self.fixture = fixture
        self.root = self.fixture.root; self.store = self.fixture.store
        self.fixture.audited(); self.store.review(self.fixture.case_id,self.fixture.review_file())
        self.case_id,self.parent = self.fixture.case_id,self.fixture.bank
        self.history = HistoryStore(self.root/'selected-history')

    def request(self, **changes):
        current = self.store.show(self.case_id)
        expected = None
        if self.history.root.exists():
            old_task = self.store.opened(self.case_id)[4]
            try: expected = self.history.oracle(old_task.grader_digest)['event_sha256']
            except (ValueError,OSError): pass
        value = dict(schema_version=1,case_id=self.case_id,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],parent_bank_sha256=hashlib.sha256(self.parent.read_bytes()).hexdigest(),
            parent_revision_sha256=None,history_store=str(self.history.root),expected_oracle_event_sha256=expected,
            operator=dict(identity='synthetic-curator',source='operator_attestation',provenance='synthetic operator',
                basis='Apply independently reviewed frozen oracle correction'),reason='Synthetic explicit development correction')
        if json.loads(self.parent.read_text()).get('controller_profile'):
            value['parent_revision_sha256'] = self.parent.parent.name
        value.update(changes)
        path = self.root/'application.json'; path.write_text(json.dumps(value)); return path

    def apply(self, **changes): return self.store.apply(self.case_id,self.parent,self.history,self.request(**changes))

    def bank(self,result): return self.store.root/'revisions'/result['revision_sha256']/'bank.json'


@contextmanager
def bank_correction():
    fixture = corrections.GraderCorrectionTests()
    try:
        fixture.setUp()
        yield BankCorrection(fixture)
    finally:
        run_cleanups(fixture)
