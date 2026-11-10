"""Private reports on actual synthetic receipts; no native identity/model claim."""
from copy import deepcopy
import io
import json
from pathlib import Path
from contextlib import ExitStack,redirect_stderr,redirect_stdout
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark,history_report
from tests.support.preservation_receipts import PreservationReceipt


class TestHistoryReport:
    @pytest.fixture(autouse=True)
    def _helpers(self):
        with ExitStack() as cleanups:
            self.cleanups = cleanups
            yield

    def fixture(self):
        helper=PreservationReceipt(self.cleanups)
        fixture=helper.fixture(True); execution,value=helper.receipt(fixture)
        refs=[dict(run_id=execution['run_id'],receipt_sha256=value['receipt_sha256'])]
        return fixture,value,refs

    def test_current_qualification_transitions_and_unknowns_are_reported_privately(self):
        fixture,value,refs=self.fixture()
        report=benchmark.history_summary(fixture.store.root,refs)
        assert report['comparisons'][0]['analysis'] == value['analysis']
        assert report['comparisons'][0]['receipt'] == value['receipt']
        assert not report['publication_allowed']; assert not report['adoption_authority']
        assert report['pooled_treatment_effect'] is None
        markdown,document=benchmark.render(report)
        for field in ('qualification_dependencies','assumptions','pass_fail','fail_pass','coverage','success_rate'):
            assert field in markdown; assert field in document
        assert '<th scope="col">' in document
        malformed=deepcopy(refs); malformed[0]['safe']=True
        for changed in (refs+refs,malformed,[]):
            with pytest.raises(ValueError): benchmark.history_summary(fixture.store.root,changed)
        fixture.revoke()
        with pytest.raises(ValueError): benchmark.history_summary(fixture.store.root,refs)

    def test_report_keeps_all_receipts_and_analysis_beyond_former_byte_ceiling(self):
        refs=[dict(run_id=f'{index:064x}',receipt_sha256=f'{index+100:064x}') for index in range(9)]
        analysis=dict(status='inconclusive',detail='synthetic analysis '*250000)
        def verified(run_id,receipt_sha256):
            return dict(receipt=dict(run_id=run_id),analysis=analysis)
        with patch('agent_harness_lab.evaluation.oracle_history.HistoryStore') as store:
            store.return_value.root=Path('/synthetic-report-store')
            store.return_value.verify_decision.side_effect=verified
            result=history_report.summary('/synthetic-report-store',refs)
            assert store.return_value.verify_decision.call_count == 2*len(refs)
        assert len(result['comparisons']) == 9
        assert len(json.dumps(result)) > 32*1024*1024
        assert not result['publication_allowed']

    def test_public_mode_refuses_before_private_authority_or_input_is_opened(self):
        with patch('agent_harness_lab.evaluation.oracle_history.HistoryStore') as store:
            with pytest.raises(ValueError, match='private'):
                benchmark.history_summary(Path('/public-synthetic-unopened'),[],public=True)
            store.assert_not_called()
        errors=io.StringIO()
        with redirect_stderr(errors),pytest.raises(SystemExit) as exit:
            main(['benchmark-report','--history-store','/public-synthetic-unopened',
                  '--input','/public-synthetic-unopened-input','--output','/public-synthetic-unused','--public'])
        assert exit.value.code == 2
        assert 'controller history reports cannot be exported publicly' in errors.getvalue()

    def test_cli_rechecks_after_render_and_never_creates_revoked_output(self):
        fixture,_,refs=self.fixture(); source=fixture.root/'report-references.json'
        source.write_text(json.dumps(refs)); output=fixture.root/'private-report'
        args=['benchmark-report','--history-store',str(fixture.store.root),'--input',str(source),'--output',str(output)]
        with redirect_stdout(io.StringIO()): assert main(args) == 0
        for name in ('results.json','report.md','report.html'):
            assert (output/name).stat().st_mode&0o777 == 0o600
        original=benchmark.render
        def revoke(value):
            rendered=original(value); fixture.revoke(); return rendered
        rejected=fixture.root/'revoked-report'
        with patch.object(benchmark,'render',side_effect=revoke),redirect_stderr(io.StringIO()),pytest.raises(SystemExit):
            main(args[:-1]+[str(rejected)])
        assert not rejected.exists()

    def test_report_renderer_escapes_untrusted_cohort_labels_and_provenance(self):
        fixture,_,refs=self.fixture(); report=benchmark.history_summary(fixture.store.root,refs)
        entry=report['comparisons'][0]; cohorts=entry['analysis']['preservation']['cohorts']
        value=next(iter(cohorts.values())); cohorts.clear(); cohorts['``|<script>bad</script>']=value
        entry['receipt']['qualification_dependencies']['synthetic-note']='<script>private</script>'
        markdown,document=benchmark.render(report)
        assert '\\|' in markdown
        assert '<script>' not in document
        assert '&lt;script&gt;' in document
        forged=dict(report,publication_allowed=True,
            provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic',derived_from_private=False))
        assert not benchmark.public_export_allowed([forged])
