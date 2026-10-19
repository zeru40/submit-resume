from types import SimpleNamespace
import pytest
from platforms.boss import BossApplicant
from platforms.base import JobUnavailable, BeforeClickError
from platforms.specs import SPECS


def build(source,found=True,valid=True):
    adapter=object.__new__(BossApplicant)
    adapter.spec=SPECS['boss'];adapter.scope='same-account'
    visited=[];clicks=[]
    job={'external_id':'job1','title':'AI应用工程师','company':'示例企业','source_url':source}
    page=SimpleNamespace(url='https://www.zhipin.com/web/geek/chat',wait_for_timeout=lambda _:None)
    class Locator:
        def __init__(self,selector):self.selector=selector
        def count(self):return int(found and page.url==source)
        def click(self):clicks.append(page.url)
        def get_attribute(self,name):return 'cpc_job_list_chat_job1' if valid else 'other-job'
        def evaluate(self,*args):return False
    page.locator=Locator
    adapter.page=page
    def goto(url):visited.append(url);page.url=url
    adapter.goto=goto;adapter.assert_url=lambda url:None
    adapter.ensure_session=lambda expected:None
    adapter.checkpoint=adapter.pace_navigation=lambda:None
    adapter.verify_job=lambda job:None
    return adapter,job,visited,clicks


def test_restores_recorded_second_page_before_searching_again():
    source='https://www.zhipin.com/web/geek/jobs?query=AI&city=101230200&page=2'
    adapter,job,visited,clicks=build(source)
    adapter.navigate_job(job,{'boss_city_code':'101230200'})
    assert visited==clicks==[source]


def test_absence_is_bounded_and_never_clicks():
    adapter,job,visited,clicks=build('',found=False)
    with pytest.raises(JobUnavailable):adapter.navigate_job(job,{'boss_city_code':'101230200'})
    assert len(visited)==2 and not clicks


def test_present_card_with_wrong_detail_still_stops():
    source='https://www.zhipin.com/web/geek/jobs?query=AI&city=101230200&page=2'
    adapter,job,visited,clicks=build(source,valid=False)
    with pytest.raises(BeforeClickError) as exc:
        adapter.navigate_job(job,{'boss_city_code':'101230200'})
    assert not isinstance(exc.value,JobUnavailable)
    assert visited==clicks==[source]
