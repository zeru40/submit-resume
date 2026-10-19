from types import SimpleNamespace
import pytest
import platforms.base as base
from platforms.boss import BossApplicant
from platforms.specs import SPECS
from core.control import NeedHuman


@pytest.mark.parametrize('available_after',[8,None])
def test_slow_results_wait_in_place_with_fixed_deadline(monkeypatch,available_after):
    clock=[0.0];visits=[];session_checks=[]
    monkeypatch.setattr(base,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    a=object.__new__(BossApplicant);a.code='boss';a.spec=SPECS['boss']
    class Cards:
        def count(self):return int(available_after is not None and clock[0]>=available_after)
        def nth(self,index):return self
    a.page=SimpleNamespace(url='https://www.zhipin.com/web/geek/jobs',locator=lambda _:Cards(),
        wait_for_timeout=lambda ms:clock.__setitem__(0,clock[0]+ms/1000))
    def session(*args):session_checks.append(clock[0]);return 'scope'
    a.ensure_session=session;a.goto=lambda url:visits.append(url);a.checkpoint=lambda:None
    a.first=lambda *args:SimpleNamespace(get_attribute=lambda _:'/job_detail/job1.html')
    a.text=lambda *args:'已处理的测试岗位'
    plan={'keyword':'AI','boss_city_code':'101230200','max_pages':1,'max_jobs':5,'_skip_job_ids':{'boss:job1'}}
    if available_after is None:
        with pytest.raises(NeedHuman,match='20秒'):list(a.search(plan))
        assert 20<=clock[0]<21
    else:
        assert list(a.search(plan))==[] and 8<=clock[0]<9
    assert len(visits)==1 and len(session_checks)>3
