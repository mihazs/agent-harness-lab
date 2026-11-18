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
        version='candidate', controller_grade=dict(trajectory=dict(assertions=[dict(
            assertion_id='synthetic', assertion_sha256='a' * 64, status='violated', required=True,
            arguments='SYNTHETIC_SECRET')])) )])
    document.write_text(page(scenario))
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            tab = browser.new_page()
            requests = []
            tab.on('request', lambda request: requests.append(request.url))
            tab.goto(document.as_uri())
            group = tab.get_by_role('group', name='Filter task pairs')
            rows = group.locator('tbody tr')
            visible = group.locator('tbody tr:visible')
            total = rows.count()
            decision = tab.locator('body').inner_text().split('Task table', 1)[0]
            assert total > 2 and visible.count() == total
            group.locator('#pair-filter-all').focus()
            assert group.locator('#pair-filter-all').evaluate('(element) => getComputedStyle(element).outlineStyle') != 'none'
            tab.keyboard.press('ArrowRight')
            assert group.locator('#pair-filter-improved').is_checked()
            assert visible.count() > 0
            assert all(visible.nth(i).get_attribute('data-verdict') == 'improved' for i in range(visible.count()))
            for verdict in ('improved', 'regressed', 'unknown'):
                group.locator('#pair-filter-' + verdict).check()
                assert visible.count() > 0
                assert all(visible.nth(i).get_attribute('data-verdict') == verdict for i in range(visible.count()))
                assert tab.locator('body').inner_text().split('Task table', 1)[0] == decision
            group.locator('#pair-filter-missing').check()
            assert any(visible.nth(i).get_attribute('data-verdict') == 'improved' for i in range(visible.count()))
            assert any(visible.nth(i).get_attribute('data-verdict') == 'unknown' for i in range(visible.count()))
            assert all(visible.nth(i).get_attribute('data-verdict') == 'unknown'
                       or visible.nth(i).get_attribute('data-coverage') == 'gap' for i in range(visible.count()))
            group.locator('#pair-filter-assertions').check()
            assert visible.count() == 1 and 'candidate: violated' in visible.first.inner_text()
            assert 'Required assertions (unverified)' in group.inner_text()
            assert 'SYNTHETIC_SECRET' not in tab.content()
            assert tab.locator('body').inner_text().split('Task table', 1)[0] == decision
            group.locator('#pair-filter-all').check()
            assert visible.count() == total
            assert all(url.startswith('file:') for url in requests)
        finally:
            browser.close()
