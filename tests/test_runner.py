import time
from types import SimpleNamespace
import pytest
from core.store import Store
from core.runner import Runner
from core.control import NeedHuman
from platforms.base import BeforeClickError

@pytest.fixture(autouse=True)
def immediate_test_waits(monkeypatch):
    # Exercise pacing decisions without spending real minutes in unit tests.
    from core.control import Control
    monkeypatch.setattr(Control,'wait',lambda self,seconds,pump=None:self.checkpoint(pump))

def wait_done(runner):
    deadline=time.monotonic()+5
    while runner.snapshot()['busy'] and time.monotonic()<deadline:time.sleep(.01)
    assert not runner.snapshot()['busy']

class FakeAdapter:
    spec={'label':'BOSS直聘'}
    def __init__(self,*args):
        self.page=SimpleNamespace(is_closed=lambda:False,url='https://www.zhipin.com',wait_for_timeout=lambda _:None)
        self.contacts=0;self.resumes=0;self.external_started=False
    def ensure_session(self,*args):return 'scope1'
    def goto(self,url):pass
    def verify_job(self,job):pass
    def search(self,plan):
        yield {'platform':'boss','external_id':'job1','title':'Python工程师','company':'公司','url':'https://www.zhipin.com/job_detail/job1.html','description':'岗位职责：\n负责Python后端接口设计、系统架构和项目交付。\n任职要求：熟悉数据库及团队协作。','location':'上海','salary':'20-30K'}
    def contact(self,*args):self.contacts+=1;self.external_started=True;return {'peer':'hr1','chat_url':'https://www.zhipin.com/web/geek/chat?bossId=hr1'}
    def open_chat(self,*args):pass
    def send_resume(self,*args):self.resumes+=1;self.external_started=True;return {'filename':'r.txt'}
    def diagnostic(self,*args):pass
    def close(self):pass

def command():
    return {'type':'run','mode':'auto','platforms':['boss'],'plan':{'id':'p1','max_deliveries':3,'skills':'Python','min_score':60,'interval_seconds':0},
            'resume':{'id':'r1','text':'Python','name':'r.txt'},'job_ids':[]}

def test_end_to_end_runner_does_not_repeat_delivery(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    adapter=FakeAdapter()
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(command());wait_done(runner)
        assert runner.snapshot()['state']=='completed'
        assert store.one('SELECT status FROM applications')['status']=='delivered'
        runner.submit(command());wait_done(runner)
        assert adapter.contacts==adapter.resumes==1
    finally:runner.close()

def test_lost_receipt_quarantines_attempt_and_skips_next_run(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    adapter=FakeAdapter()
    def send(*args):adapter.external_started=True;adapter.resumes+=1;raise NeedHuman('平台已接收但回执丢失')
    adapter.send_resume=send
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(command());wait_done(runner)
        assert runner.snapshot()['state']=='needs_human'
        assert store.one("SELECT status FROM actions WHERE kind='resume'")['status']=='unknown'
        runner.submit(command());wait_done(runner)
        assert adapter.resumes==1
        assert adapter.contacts==1
    finally:runner.close()

def test_preclick_failure_can_retry_without_recontact(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    adapter=FakeAdapter()
    def refuse(*args):raise BeforeClickError('没有发送按钮')
    adapter.send_resume=refuse
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(command());wait_done(runner)
        assert store.one("SELECT status FROM actions WHERE kind='resume'")['status']=='failed'
        adapter.send_resume=lambda *args:{'filename':'r.txt'}
        runner.submit(command());wait_done(runner)
        assert store.one('SELECT status FROM applications')['status']=='delivered'
        assert adapter.contacts==1
    finally:runner.close()


def test_waiting_reply_preserves_sent_actions_and_resumes_only_attachment(tmp_path):
    from platforms.boss import WaitingReply
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter()
    def wait(*args):raise WaitingReply('双方回复后可用')
    adapter.send_resume=wait
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(command());wait_done(runner)
        assert store.one('SELECT status FROM applications')['status']=='waiting_reply'
        assert runner.snapshot()['delivered']==0
        assert store.one("SELECT status FROM actions WHERE kind='resume'")['status']=='failed'
        adapter.send_resume=lambda *args:{'filename':'r.txt'}
        runner.submit(command());wait_done(runner)
        assert store.one('SELECT status FROM applications')['status']=='delivered'
        assert adapter.contacts==1
    finally:runner.close()


def test_short_message_run_skips_platform_history_and_never_resends(tmp_path):
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter();adapter.greetings=[]
    adapter.existing_companies=lambda scope:set()
    adapter.already_contacted=lambda:False
    def greet(message,*args):adapter.greetings.append(message);return {'message':message}
    adapter.greet=greet
    cmd=command();cmd['plan'].update(personalized_greeting=True,greeting_style='job_interest',communication_only=True,skip_existing_contacts=True)
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(cmd);wait_done(runner)
        assert store.one('SELECT status FROM applications')['status']=='message_sent'
        runner.submit(cmd);wait_done(runner)
        assert len(adapter.greetings)==adapter.contacts==1 and adapter.resumes==0
        assert len(adapter.greetings[0])<=120
    finally:runner.close()


def test_manual_platform_conversation_is_skipped_without_local_ledger(tmp_path):
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter()
    adapter.existing_companies=lambda scope:{'公司'}
    cmd=command();cmd['plan'].update(skip_existing_contacts=True,communication_only=True)
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(cmd);wait_done(runner)
        assert adapter.contacts==0 and adapter.resumes==0
        assert not store.one('SELECT id FROM actions')
    finally:runner.close()


def test_search_filters_protected_cards_before_fetching_details(tmp_path):
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter()
    original=adapter.search
    captured=[]
    def search(plan):
        captured.append(plan)
        yield from original(plan)
    adapter.search=search
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(command());wait_done(runner)
        cmd=command();cmd.update(mode='search');cmd['plan']['skip_existing_contacts']=True
        runner.submit(cmd);wait_done(runner)
        assert captured[-1]['_skip_job_ids']=={'boss:job1'}
        assert captured[-1]['_skip_companies']=={'公司'}
        assert captured[-1]['_known_jobs']['boss:job1']['description']
        assert adapter.contacts==adapter.resumes==1
    finally:runner.close()


def test_contact_pacing_survives_restart_and_old_short_interval(tmp_path,monkeypatch):
    from core.control import Control
    waits=[]
    monkeypatch.setattr(Control,'wait',lambda self,seconds,pump=None:waits.append(seconds))
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter()
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(command());wait_done(runner)
    finally:runner.close()

    # A fresh Runner must use the persisted receipt, not an in-memory timer.
    jobs=[dict(j,external_id='job2',company='第二家公司') for j in adapter.search({})]
    other=FakeAdapter();other.search=lambda plan:iter(jobs)
    store=Store(tmp_path/'db.sqlite')
    runner=Runner(store,tmp_path,factory=lambda *args:other)
    try:
        cmd=command();cmd['plan']['interval_seconds']=20
        runner.submit(cmd);wait_done(runner)
        assert other.contacts==1
        assert any(55 < seconds <= 60 for seconds in waits[:-1])
        assert waits[-1]==60
    finally:runner.close()


def test_resume_pipeline_caps_contacts_across_all_directions(tmp_path):
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter();searched=[]
    original=list(adapter.search({}))[0]
    def search(plan):
        searched.append(plan['keyword'])
        for i in range(2):
            yield dict(original,external_id=f'{len(searched)}-{i}',company=f'公司{len(searched)}-{i}',
                title='Python全栈开发',description='负责Python全栈与后端系统开发，包括前端页面、后端接口、数据库设计及业务需求实现。')
    adapter.search=search;adapter.greet=lambda message,*args:{'message':message}
    adapter.existing_companies=lambda scope:set();adapter.already_contacted=lambda:False
    cmd=command();cmd['search_keywords']=['Python后端开发','Java开发','前端开发']
    cmd['plan'].update(max_deliveries=5,resume_based=True,skip_existing_contacts=True,
        communication_only=True,personalized_greeting=True,greeting_style='job_interest')
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(cmd);wait_done(runner)
        assert runner.snapshot()['state']=='completed'
        assert len(searched)==3 and adapter.contacts==5
        assert runner.snapshot()['messaged']==5
        import json
        persisted=json.loads(store.one('SELECT body FROM runs')['body'])
        assert persisted['search_keywords']==cmd['search_keywords']
        assert persisted['account_scopes']=={'boss':'scope1'}
    finally:runner.close()


def test_resume_pipeline_stops_all_directions_on_session_or_page_error(tmp_path):
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter();searched=[]
    def search(plan):
        searched.append(plan['keyword'])
        raise NeedHuman('平台限制或页面异常')
        yield
    adapter.search=search;adapter.existing_companies=lambda scope:set()
    cmd=command();cmd['search_keywords']=['AI Agent','大模型应用开发','Python后端开发']
    cmd['plan'].update(resume_based=True,skip_existing_contacts=True)
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(cmd);wait_done(runner)
        assert runner.snapshot()['state']=='needs_human'
        assert searched==['AI Agent'] and adapter.contacts==0
    finally:runner.close()


def test_resume_pipeline_stops_after_unconfirmed_contact_even_if_account_ready(tmp_path):
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter();original=list(adapter.search({}))[0]
    adapter.search=lambda plan:iter([original,dict(original,external_id='job2',company='另一家公司')])
    adapter.existing_companies=lambda scope:set();adapter.already_contacted=lambda:False
    def uncertain(*args):
        adapter.contacts+=1;adapter.external_started=True
        raise NeedHuman('沟通结果未核验')
    adapter.contact=uncertain
    cmd=command();cmd['search_keywords']=['Python后端开发','Java开发']
    cmd['plan'].update(resume_based=True,skip_existing_contacts=True,
        communication_only=True,personalized_greeting=True,greeting_style='job_interest')
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(cmd);wait_done(runner)
        assert runner.snapshot()['state']=='needs_human' and adapter.contacts==1
        assert store.one("SELECT status FROM actions WHERE kind='contact'")['status']=='unknown'
        assert not store.one("SELECT id FROM applications WHERE job_id='boss:job2'")
    finally:runner.close()


def test_unknown_contact_is_quarantined_while_other_jobs_continue(tmp_path):
    store=Store(tmp_path/'db.sqlite');adapter=FakeAdapter()
    jobs=list(adapter.search({}));other=dict(jobs[0],external_id='job2',company='另一公司');jobs.append(other)
    adapter.search=lambda plan:iter(jobs)
    adapter.existing_companies=lambda scope:set();adapter.already_contacted=lambda:False
    def contact(job,*args):
        adapter.contacts+=1;adapter.external_started=True
        if job['external_id']=='job1':raise NeedHuman('沟通结果无法确认')
        return {'peer':'hr2','chat_url':'https://www.zhipin.com/web/geek/chat?bossId=hr2'}
    adapter.contact=contact;adapter.greet=lambda message,*args:{'message':message}
    cmd=command();cmd['plan'].update(skip_existing_contacts=True,communication_only=True,personalized_greeting=True,greeting_style='job_interest')
    runner=Runner(store,tmp_path,factory=lambda *args:adapter)
    try:
        runner.submit(cmd);wait_done(runner)
        assert runner.snapshot()['state']=='completed'
        assert store.one("SELECT status FROM actions WHERE kind='contact' AND status='unknown'")
        assert store.one("SELECT status FROM applications WHERE job_id='boss:job2'")['status']=='message_sent'
        assert adapter.contacts==2
    finally:runner.close()
