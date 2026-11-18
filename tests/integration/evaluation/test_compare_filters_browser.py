"""Exercise the script-free filter in a real browser when explicitly provisioned."""
import os

import pytest

from tests.unit.evaluation.test_compare_viewer import fixture, page

pytestmark = pytest.mark.skipif(os.environ.get('AHL_COMPARE_BROWSER') != '1', reason='browser validation is opt-in')


def test_filter_changes_only_the_visible_table_rows(tmp_path):
    from playwright.sync_api import sync_playwright
    document = tmp_path / 'report.html'
    scenario = fixture()
    scenario['trials'][0]['capture'] = 'partial'
    candidate = scenario['trials'][1]
    scenario['trajectory_results'] = dict(trajectory_profile='controller-trajectory-v1', experiment_id='exp-1', trials=[dict(
        trial_id=candidate['trial_id'], task=candidate['task'], root=candidate['root'], family=candidate['family'],
        version='candidate', controller_grade=dict(valid=True, trajectory=dict(valid=True, closed=True, status='violated', assertions=[dict(
            assertion_id='synthetic', assertion_sha256='a' * 64, status='violated', valid=True, required=True,
            arguments='SYNTHETIC_SECRET')])) )])
    document.write_text(page(scenario))
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            tab = browser.new_page()
            requests = []
            tab.on('request', lambda request: requests.append(request.url))
            tab.goto(document.as_uri())
            group = tab.locator('div.pair-filters')
            rows = group.locator('tbody tr')
            visible = group.locator('.pair-group:visible tbody tr')
            total = rows.count()
            decision = tab.locator('body').inner_text().split('Task table', 1)[0]
            assert total > 2 and visible.count() == total
            improved = group.get_by_role('link', name='Improved', exact=True)
            improved.focus()
            assert improved.evaluate('(element) => getComputedStyle(element).outlineStyle') != 'none'
            tab.keyboard.press('Enter')
            assert visible.count() > 0
            classes = lambda row: row.evaluate('(element) => element.closest(".pair-group").className')
            assert all('verdict-improved' in classes(visible.nth(i)) for i in range(visible.count()))
            for verdict, label in (('improved', 'Improved'), ('regressed', 'Regressed'), ('unknown', 'Unknown outcome')):
                group.get_by_role('link', name=label, exact=True).click()
                assert visible.count() > 0
                assert all('verdict-' + verdict in classes(visible.nth(i)) for i in range(visible.count()))
                assert tab.locator('body').inner_text().split('Task table', 1)[0] == decision
            group.get_by_role('link', name='Missing evidence', exact=True).click()
            assert any('verdict-improved' in classes(visible.nth(i)) for i in range(visible.count()))
            assert any('verdict-unknown' in classes(visible.nth(i)) for i in range(visible.count()))
            assert any('verdict-regressed' in classes(visible.nth(i))
                       and 'coverage-complete' in classes(visible.nth(i)) for i in range(visible.count()))
            assert all(any(name in classes(visible.nth(i)) for name in ('verdict-unknown', 'coverage-gap', 'assertion-gap-yes'))
                       for i in range(visible.count()))
            group.get_by_role('link', name='Reported required assertion violations (unverified)', exact=True).click()
            assert visible.count() == 1 and 'candidate: violated' in visible.first.inner_text()
            assert 'Required assertions (unverified)' in group.inner_text()
            assert 'SYNTHETIC_SECRET' not in tab.content()
            assert tab.locator('body').inner_text().split('Task table', 1)[0] == decision
            group.get_by_role('link', name='All pairs', exact=True).click()
            assert visible.count() == total
            assert all(url.startswith('file:') for url in requests)
        finally:
            browser.close()
