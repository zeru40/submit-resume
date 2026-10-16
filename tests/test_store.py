from concurrent.futures import ThreadPoolExecutor
import pytest
from core.store import Store

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'app.db')

def app_record(store, scope='account-a'):
    job={'platform':'boss','external_id':'job1','title':'Python','company':'测试公司','url':'https://www.zhipin.com/job_detail/job1.html'}
    key=store.upsert_job(job,90,['匹配'],True)
    return store.application(key,scope,'resume1','plan1')

def test_job_and_application_upsert_preserves_resume(store):
    a=app_record(store)
    b=store.application(a['job_id'],a['scope'],'other-resume','plan2')
    assert a['id']==b['id']
    assert b['resume_id']=='resume1'
    assert len(store.jobs())==1

def test_reservation_is_atomic_across_threads(store):
    a=app_record(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens=list(pool.map(lambda _:store.reserve(a['id'],'contact'),range(16)))
    assert len([x for x in tokens if x])==1

def test_unknown_and_sent_cannot_retry(store):
    a=app_record(store)
    token=store.reserve(a['id'],'contact')
    store.outcome(token,'unknown',error='平台已接收但连接断开')
    assert store.reserve(a['id'],'contact') is None
    assert not store.outcome(token,'failed')
    assert not store.outcome(token,'sent')

def test_failure_gets_new_token_and_stale_callback_cannot_overwrite(store):
    a=app_record(store)
    first=store.reserve(a['id'],'resume')
    store.outcome(first,'failed')
    second=store.reserve(a['id'],'resume')
    assert second and second!=first
    assert not store.outcome(first,'sent')
    assert store.outcome(second,'sent','receipt')

def test_restart_quarantines_unfinished_attempt(store):
    a=app_record(store)
    store.reserve(a['id'],'contact')
    restarted=Store(store.path)
    assert restarted.one('SELECT status FROM actions')['status']=='unknown'
    assert restarted.one('SELECT status FROM applications')['status']=='needs_review'
    assert restarted.reserve(a['id'],'contact') is None

def test_different_accounts_are_independent(store):
    a=app_record(store,'one')
    b=app_record(store,'two')
    assert a['id']!=b['id']
    assert store.reserve(a['id'],'contact')
    assert store.reserve(b['id'],'contact')

def test_manual_review_requires_unknown_state(store):
    a=app_record(store)
    token=store.reserve(a['id'],'resume')
    with pytest.raises(ValueError):store.review(token,False,'还未检查')
    store.outcome(token,'unknown')
    store.review(token,False,'已在平台确认没有发送')
    assert store.reserve(a['id'],'resume')

def test_manual_completion_is_audited_and_survives_restart(store):
    a=app_record(store)
    token=store.reserve(a['id'],'resume')
    store.outcome(token,'unknown')
    store.complete_manually(a['id'],'人工已确认正确版本附件发送成功')
    restarted=Store(store.path)
    assert restarted.one('SELECT status FROM applications')['status']=='delivered'
    assert restarted.one("SELECT receipt FROM actions WHERE kind='manual_confirmation'")

def test_verified_noop_cannot_release_another_job_or_confirmed_contact(store):
    a=app_record(store);token=store.reserve(a['id'],'contact');store.outcome(token,'unknown')
    note='全量聊天列表无目标，重新载入岗位仍明确显示尚未建立联系'
    assert not store.resolve_no_external_contact(token,'another-job',note)
    assert store.resolve_no_external_contact(token,a['job_id'],note)
    second=store.reserve(a['id'],'contact');store.outcome(second,'sent','real-receipt')
    assert not store.resolve_no_external_contact(second,a['job_id'],note)


def test_positive_contact_reconciliation_requires_matching_job_scope_and_identity(store):
    a=app_record(store);token=store.reserve(a['id'],'contact');store.outcome(token,'unknown')
    receipt={'job_id':a['job_id'],'peer':'avatar:test','identity':{'avatar':'https://example.com/hr.png','company':'测试公司','title':'Python'}}
    assert not store.reconcile_contact(token,a['job_id'],'wrong-account',receipt,'https://example.com/hr.png')
    with pytest.raises(ValueError):
        store.reconcile_contact(token,a['job_id'],a['scope'],receipt,'https://example.com/other.png')
    assert store.reconcile_contact(token,a['job_id'],a['scope'],receipt,'https://example.com/hr.png')
    assert not store.reserve(a['id'],'contact')
    assert not store.reconcile_contact(token,a['job_id'],a['scope'],receipt,'https://example.com/hr.png')
