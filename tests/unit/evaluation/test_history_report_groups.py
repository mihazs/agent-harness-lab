"""Offline status browsing does not modify the decision or require scripting."""
from copy import deepcopy
from html.parser import HTMLParser

from agent_harness_lab.evaluation.history_report import render


class Groups(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.summary=False;self.labels=[]
    def handle_starttag(self,tag,attrs):
        if tag=='details':self.depth+=1
        if tag=='summary' and self.depth==1:self.summary=True
    def handle_endtag(self,tag):
        if tag=='summary':self.summary=False
        if tag=='details':self.depth-=1
    def handle_data(self,data):
        if self.summary:self.labels.append(data)


def test_status_groups_are_accessible_and_do_not_change_input_or_decision():
    def side(ident):return dict(outcome='passed',trial_id=ident,timeline=dict(coverage='unknown',events=[]))
    statuses=('regressed','improved','unchanged','unknown','not comparable')
    pairs=[dict(task=f'synthetic-{index}',root='root',family='family',repetition=0,\
                baseline=side('baseline'),candidate=side('candidate'),status=status)
           for index,status in enumerate(statuses)]
    entry=dict(receipt_sha256='b'*64,receipt=dict(run_id='a'*64,plan_sha256='c'*64),\
               analysis=dict(status='rejected',reason='critical_regression'),task_pairs=pairs)
    data=dict(report_kind='controller_history',publication_allowed=False,comparisons=[entry]);original=deepcopy(data)
    markdown,document=render(data);parser=Groups();parser.feed(document)
    assert data==original
    assert all(status+' (1)' in parser.labels for status in statuses)
    assert 'rejected / critical_regression' in document
    assert 'analysis population and decision remain unchanged' in document
    assert '<script' not in document and "default-src 'none'" in document
    assert all('synthetic-'+str(i) in markdown for i in range(5))
