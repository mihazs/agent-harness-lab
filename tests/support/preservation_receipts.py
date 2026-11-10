"""Shared synthetic preservation receipt helpers."""
from agent_harness_lab.private_journal import encoded
from tests.integration.evaluation import test_oracle_history as legacy
from tests.integration.evaluation import test_preservation_history as cohorts
from tests.support.unittest_cleanups import run_cleanups


class PreservationReceipt:
    """Build sealed synthetic receipt fixtures."""

    def __init__(self, cleanups):
        self.cleanups = cleanups
        self.addCleanup = cleanups.callback

    def fixture(self,cohort=False):
        if cohort:
            return cohorts.PreservationHistory(self.cleanups).f
        fixture=legacy.OracleHistoryTests()
        self.cleanups.callback(run_cleanups,fixture); fixture.setUp()
        return fixture

    def receipt(self,fixture):
        summary,calls=fixture.run_history(); assert summary['state']=='sealed',summary
        return summary,fixture.store.decision(summary['run_id'])

    def target(self,fixture,summary,value):
        return fixture.store.case(summary['run_id'])/'decisions'/(value['receipt_sha256']+'.json')

    def quarantine(self,root,store,bundle):
        value=dict(schema_version=1,bundle_sha256=bundle,expected_event_sha256=store.oracle(bundle)['event_sha256'],
            operator=dict(identity='synthetic-operator',source='operator_attestation',provenance='synthetic fixture',basis='Synthetic defect'),
            evidence_refs=[dict(ref='synthetic:defect',sha256='a'*64)],reason='Synthetic negative control')
        path=root/'receipt-quarantine.json'; path.write_bytes(encoded(value)); store.quarantine(path)
