"""Real browser, synthetic applicant pages. Never sends anything to real sites."""
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from config import browser_path
from core.control import Control, NeedHuman
from platforms.boss import BossApplicant
from platforms.zhilian import ZhilianApplicant

ACCOUNT='<div class="nav-figure"><span class="label-name">测试求职者</span></div><div class="user-name">测试求职者</div>'
CHAT='''<div class="chat-message" id="messages"></div><textarea id="chat-input"></textarea>
<button class="btn-send" onclick="sendMessage()">发送</button>
<button class="btn-resume" onclick="document.querySelector('[role=dialog]').hidden=false">发简历</button>
<div role="dialog" hidden><span onclick="this.dataset.selected='yes'">简历.txt</span>
<button onclick="append('附件简历：简历.txt');this.parentElement.hidden=true">发送</button></div>
<script>
function append(text){const d=document.createElement('div');d.className='message-item sent';d.textContent=text;document.querySelector('#messages').append(d);}
function sendMessage(){append(document.querySelector('#chat-input').value);document.querySelector('#chat-input').value='';}
</script>'''

@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path=browser_path(),headless=True)
        yield b
        b.close()

def adapter(browser, cls, tmp_path, handler):
    context=browser.new_context()
    context.route('**/*',handler)
    obj=object.__new__(cls)
    from platforms.specs import SPECS
    code='boss' if cls is BossApplicant else 'zhilian'
    obj.code,obj.spec=code,SPECS[code]
    obj.context,obj.page=context,context.new_page()
    obj.page.set_default_timeout(1000)
    obj.data_dir=obj.profile=tmp_path
    obj.control=Control()
    obj.publish=lambda *args:None
    obj.log=lambda *args:None
    obj.scope=''
    obj.external_started=False
    return obj

def boss_routes(route):
    url=route.request.url
    if '/job_detail/' in url:
        html=ACCOUNT+'<div class="job-sec-text">负责Python开发、系统接口及数据库设计，具备良好的项目交付和协作能力。</div><a class="op-btn-chat" href="https://www.zhipin.com/web/geek/chat?bossId=hr1">立即沟通</a>'
    elif '/chat' in url:
        html=ACCOUNT+CHAT
    else:
        html=ACCOUNT+'''<div class="job-card-wrapper"><a href="/job_detail/job1.html"><span class="job-name">Python 后端</span></a><span class="company-name">测试公司</span><span class="salary">15-25K</span><span class="job-area">上海</span></div>'''
    route.fulfill(content_type='text/html; charset=utf-8',body=html)


def test_search_reuses_recent_details_across_keywords_but_not_accounts(browser,tmp_path):
    details=[]
    def handler(route):
        if '/job_detail/' in route.request.url:details.append(route.request.url)
        boss_routes(route)
    a=adapter(browser,BossApplicant,tmp_path,handler)
    plan={'keyword':'Python','boss_city_code':'101230200','max_pages':1,'max_jobs':10}
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        first=list(a.search(plan))
        second=list(a.search(dict(plan,keyword='AI')))
        assert len(details)==1
        assert first[0]['description']==second[0]['description']
        assert second[0]['search_keyword']=='AI'
        a._search_cache['job1']['read_at']-=3601
        list(a.search(plan))
        assert len(details)==2
        a._search_cache_scope='different-account'
        list(a.search(plan))
        assert len(details)==3
    finally:a.close()


@pytest.mark.parametrize('guard',[{'_skip_job_ids':{'boss:job1'}},{'_skip_companies':{'测试公司'}}])
def test_protected_cards_do_not_open_detail_pages(browser,tmp_path,guard):
    details=[]
    def handler(route):
        if '/job_detail/' in route.request.url:details.append(route.request.url)
        boss_routes(route)
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        plan=dict(keyword='Python',boss_city_code='101230200',max_pages=1,max_jobs=10,**guard)
        assert list(a.search(plan))==[]
        assert details==[]
    finally:a.close()


def test_anonymous_company_is_skipped_before_detail_when_deduplicating(browser,tmp_path):
    details=[]
    def handler(route):
        if '/job_detail/' in route.request.url:details.append(route.request.url)
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+'''<div class="job-card-wrapper">
            <a href="/job_detail/job1.html"><span class="job-name">AI应用工程师</span></a>
            <span class="company-name">某大型科技公司</span><span class="job-area">厦门</span></div>''')
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        plan={'keyword':'AI','boss_city_code':'101230200','max_pages':1,'max_jobs':10,'skip_existing_contacts':True}
        assert list(a.search(plan))==[] and not details
    finally:a.close()


def test_saved_job_description_avoids_detail_navigation_after_restart(browser,tmp_path):
    details=[]
    def handler(route):
        if '/job_detail/' in route.request.url:details.append(route.request.url)
        boss_routes(route)
    plan={'keyword':'Python','boss_city_code':'101230200','max_pages':1,'max_jobs':10}
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        job=list(a.search(plan))[0]
    finally:a.close()
    b=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        b.goto('https://www.zhipin.com/web/geek/jobs')
        saved={'boss:job1':job}
        cached=list(b.search(dict(plan,keyword='AI',_known_jobs=saved)))
        assert len(details)==1
        assert cached[0]['description']==job['description']
        assert cached[0]['search_keyword']=='AI'
        # Changed identity on the card invalidates a public-description cache.
        saved['boss:job1']=dict(job,company='之前的公司')
        list(b.search(dict(plan,_known_jobs=saved)))
        assert len(details)==2
    finally:b.close()


def test_boss_reads_selected_details_inline_without_opening_legacy_pages(browser,tmp_path):
    detail_requests=[]
    description='负责Python后台服务、业务接口与数据库设计开发，构建Agent工具调用及企业知识库应用。'
    def handler(route):
        if '/job_detail/' in route.request.url:detail_requests.append(route.request.url)
        cards=''
        for n in (1,2):
            cards+=f'''<div class="job-card-wrapper"><a class="job-name" href="/job_detail/job{n}.html"
                onclick="event.preventDefault();document.querySelector('.job-detail-header .job-name').textContent='AI开发{n}';document.querySelector('.op-btn-chat').setAttribute('ka','chat_job{n}');document.querySelector('.job-sec-text').textContent='{description}'">AI开发{n}</a>
                <span class="company-name">公司{n}</span><span class="salary">15-25K</span><span class="job-area">厦门</span></div>'''
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+cards+'''
            <div class="job-detail-container"><div class="job-detail-header"><span class="job-name"></span></div>
            <a class="op-btn-chat" ka="none">立即沟通</a><div class="job-sec-text"></div></div>''')
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        jobs=list(a.search({'keyword':'AI','boss_city_code':'101230200','max_pages':1,'max_jobs':5}))
        assert [job['external_id'] for job in jobs]==['job1','job2']
        assert all(job['description']==description for job in jobs)
        assert detail_requests==[] and '/web/geek/jobs' in a.page.url
        assert not a.external_started
    finally:a.close()


def test_restriction_text_takes_precedence_over_account_header(browser,tmp_path):
    def handler(route):
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+'访问受限：您的账户存在异常行为，已暂时被限制访问！')
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        assert a.session()['reason']=='access_restricted'
    finally:a.close()

def test_boss_search_contact_message_and_resume(browser,tmp_path):
    a=adapter(browser,BossApplicant,tmp_path,boss_routes)
    try:
        a.open()
        scope=a.ensure_session()
        jobs=list(a.search({'max_pages':1,'max_jobs':1,'keyword':'Python','boss_city_code':'100010000'}))
        assert len(jobs)==1 and jobs[0]['title']=='Python 后端'
        a.goto(jobs[0]['url'])
        receipt=a.contact(jobs[0],scope)
        assert receipt['peer']=='hr1'
        a.open_chat(receipt,scope)
        assert a.greet('您好，我对这个岗位感兴趣。',receipt,scope)['peer']=='hr1'
        assert a.send_resume({'name':'简历.txt'},receipt,scope)['filename']=='简历.txt'
        assert len(a.outbound())==2
    finally:a.close()

def test_boss_does_not_send_into_existing_human_draft(browser,tmp_path):
    a=adapter(browser,BossApplicant,tmp_path,boss_routes)
    try:
        a.goto('https://www.zhipin.com/web/geek/chat?bossId=hr1')
        scope=a.ensure_session()
        a.page.locator('#chat-input').fill('人工草稿')
        with pytest.raises(NeedHuman,match='草稿'):
            a.greet('自动消息',{'peer':'hr1'},scope)
        assert not a.outbound()
        assert not a.external_started
    finally:a.close()

def test_boss_wrong_peer_does_not_send(browser,tmp_path):
    a=adapter(browser,BossApplicant,tmp_path,boss_routes)
    try:
        a.goto('https://www.zhipin.com/web/geek/chat?bossId=wrong')
        scope=a.ensure_session()
        with pytest.raises(NeedHuman,match='稳定标识'):
            a.greet('消息',{'peer':'hr1'},scope)
        assert not a.outbound()
    finally:a.close()

def test_boss_input_method_composition_blocks_send(browser,tmp_path):
    a=adapter(browser,BossApplicant,tmp_path,boss_routes)
    try:
        a.goto('https://www.zhipin.com/web/geek/chat?bossId=hr1')
        scope=a.ensure_session()
        a.page.evaluate('window.__careerComposing = true')
        with pytest.raises(NeedHuman,match='输入法'):
            a.greet('消息',{'peer':'hr1'},scope)
        assert not a.outbound()
    finally:a.close()

def test_missing_attachment_does_not_select_other_resume(browser,tmp_path):
    a=adapter(browser,BossApplicant,tmp_path,boss_routes)
    try:
        a.goto('https://www.zhipin.com/web/geek/chat?bossId=hr1')
        scope=a.ensure_session()
        with pytest.raises(NeedHuman,match='未识别到指定附件'):
            a.send_resume({'name':'another.pdf'}, {'peer':'hr1'},scope)
        assert not a.outbound()
    finally:a.close()

def test_attachment_manager_matches_chinese_filename_and_size(browser,tmp_path):
    def handler(route):
        body=ACCOUNT+'<div class="annex-content"><a title="简历5.pdf">简历5.pdf</a><div>271.8KB</div></div>'
        route.fulfill(content_type='text/html; charset=utf-8',body=body)
    a=adapter(browser,BossApplicant,tmp_path,handler)
    path=tmp_path/'r.pdf';path.write_bytes(b'x'*278328)
    try:
        a.goto('https://www.zhipin.com/job_detail/job1.html')
        scope=a.ensure_session()
        result=a.prepare_resume({'name':'简历5.pdf','path':str(path),'sha256':'local-hash'},scope)
        assert result['source']=='platform_existing'
        assert result['size']=='271.8KB'
        assert not a.external_started
    finally:a.close()

def test_zhilian_application_receipt(browser,tmp_path):
    def handler(route):
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+'''<div class="job-description">岗位描述</div><button onclick="this.textContent='已申请'">申请职位</button>''')
    a=adapter(browser,ZhilianApplicant,tmp_path,handler)
    try:
        url='https://jobs.zhaopin.com/CC1234.html'
        a.goto(url)
        scope=a.ensure_session()
        receipt=a.apply({'external_id':'CC1234','url':url},None,scope)
        assert receipt['job']=='CC1234'
        assert '已申请' in receipt['evidence']
        assert a.apply({'external_id':'CC1234','url':url},None,scope)['existing']
    finally:a.close()


def test_plain_chat_url_binds_visible_hr_company_and_position(browser,tmp_path):
    def handler(route):
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+CHAT+'''<div class="friend-content selected"><img src="https://example.com/hr.png"><div class="name-box"><span class="name-text">黄女士</span><span>测试公司</span><span>HR</span></div></div><div class="chat-conversation"><span class="position-name">AI Agent 开发</span></div>''')
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/chat')
        scope=a.ensure_session()
        job={'external_id':'job1','title':'AI Agent 开发','company':'测试公司'}
        receipt=a.chat_receipt(job,'https://example.com/hr.png')
        assert receipt['peer'].startswith('avatar:')
        assert a.greet('您好，希望了解这个岗位。',receipt,scope)
        a.page.locator('.position-name').evaluate("e=>e.textContent='另一岗位'")
        with pytest.raises(NeedHuman,match='岗位已变化'):
            a.greet('不应发送',receipt,scope)
        assert len(a.outbound())==1
    finally:a.close()


def test_resume_waits_for_reply_without_clicking_disabled_control(browser,tmp_path):
    from platforms.boss import WaitingReply
    def handler(route):
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+CHAT.replace('<button class="btn-resume"','<button class="unused"')+'''<div class="toolbar-btn unable" aria-label="求简历：双方回复后可用" onclick="window.badClick=true">发简历</div>''')
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/chat?bossId=hr1')
        scope=a.ensure_session()
        with pytest.raises(WaitingReply,match='双方回复'):
            a.send_resume({'name':'简历.txt'},{'peer':'hr1'},scope)
        assert not a.external_started
        assert not a.page.evaluate('Boolean(window.badClick)')
    finally:a.close()


def test_spa_card_selection_binds_contact_action_to_job(browser,tmp_path):
    def handler(route):
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+'''
        <a class="job-name" href="/job_detail/job1.html" onclick="event.preventDefault();document.querySelector('.op-btn-chat').setAttribute('ka','cpc_job_list_chat_job1');document.querySelector('.job-detail-header .job-name').textContent='AI开发'">AI开发</a>
        <div class="job-detail-container"><div class="job-detail-header"><span class="job-name">另一岗位</span></div><a class="op-btn-chat" ka="wrong">立即沟通</a></div>''')
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        job={'external_id':'job1','title':'AI开发'}
        a.navigate_job(job,{'keyword':'AI','boss_city_code':'101230200','max_pages':1})
        a.verify_job(job)
        assert not a.external_started
        with pytest.raises(NeedHuman,match='不一致'):
            a.verify_job({'external_id':'job2','title':'AI开发'})
    finally:a.close()


@pytest.mark.parametrize('has_prior_message',[False,True])
def test_contact_verifies_message_center_without_reclick(browser,tmp_path,monkeypatch,has_prior_message):
    def handler(route):
        if '/chat' in route.request.url:
            html=ACCOUNT+CHAT+'''<div class="friend-content selected"><img src="https://example.com/hr.png"><div class="name-box"><span class="name-text">黄女士</span><span>测试公司</span></div></div><div class="chat-conversation"><span class="position-name">AI开发</span></div>'''
            if has_prior_message:
                html+='<div class="message-item sent">此前已经沟通</div>'
        else:
            html=ACCOUNT+'''<div class="job-detail-container"><div class="job-detail-header"><span class="job-name">AI开发</span></div><a class="op-btn-chat" ka="chat_job1" href="javascript:;" onclick="this.dataset.isfriend='true'">立即沟通</a></div><div class="job-boss-info"><h2 class="name">黄女士</h2><img src="https://example.com/hr.png"></div>'''
        route.fulfill(content_type='text/html; charset=utf-8',body=html)
    a=adapter(browser,BossApplicant,tmp_path,handler)
    clicks=[]
    original=a.external_click
    def contact_click(button):
        clicks.append(True)
        original(button)
    monkeypatch.setattr(a,'external_click',contact_click)
    monkeypatch.setattr(a.page,'wait_for_timeout',lambda ms:None)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        scope=a.ensure_session()
        job={'external_id':'job1','title':'AI开发','company':'测试公司'}
        if has_prior_message:
            with pytest.raises(NeedHuman,match='已有发出消息'):
                a.contact(job,scope)
        else:
            receipt=a.contact(job,scope)
            assert receipt['identity']['company']=='测试公司'
            assert receipt['identity']['title']=='AI开发'
            assert not a.outbound()
        assert len(clicks)==1
    finally:a.close()


@pytest.mark.parametrize('variant',['inert','notice','old_tab','no_identity','preexisting_dialog','hidden_source'])
def test_contact_failure_preserves_original_page_without_second_click(browser,tmp_path,monkeypatch,variant):
    clicks=[];chat_visits=[]
    action=''
    if variant=='notice':action="document.querySelector('[role=alert]').textContent='今日沟通次数已达上限'"
    source=ACCOUNT+f'''<div class="job-detail-container"><div class="job-detail-header"><span class="job-name">AI开发</span></div>
        <a class="op-btn-chat" ka="chat_job1" href="javascript:;" onclick="{action}">立即沟通</a></div>
        <div role="alert"></div>'''
    if variant!='no_identity':source+='<div class="job-boss-info"><h2 class="name">黄女士<span>在线</span></h2><img src="https://example.com/hr.png"></div>'
    if variant=='hidden_source':source='<div class="job-boss-info" style="display:none"><h2 class="name">旧岗位HR</h2><img src="https://example.com/old.png"></div>'+source
    if variant=='preexisting_dialog':source+='<div role="dialog">需要先处理的页面提示</div>'
    def handler(route):
        if '/chat' in route.request.url:chat_visits.append(route.request.url)
        route.fulfill(content_type='text/html; charset=utf-8',body=source)
    a=adapter(browser,BossApplicant,tmp_path,handler)
    if variant=='old_tab':
        old=a.context.new_page();old.goto('https://www.zhipin.com/web/geek/chat')
        chat_visits.clear()
    original=a.external_click
    def click(button):clicks.append(True);original(button)
    monkeypatch.setattr(a,'external_click',click)
    monkeypatch.setattr(a.page,'wait_for_timeout',lambda ms:None)
    try:
        a.goto('https://www.zhipin.com/web/geek/jobs')
        original_page=a.page
        scope=a.ensure_session()
        a.page.bring_to_front()
        with pytest.raises(NeedHuman):a.contact({'external_id':'job1','title':'AI开发','company':'测试公司'},scope)
        assert len(clicks)==(0 if variant in ('no_identity','preexisting_dialog') else 1)
        assert a.page==original_page and '/jobs' in a.page.url
        assert not chat_visits
        assert not a.outbound()
        diagnostics=list((tmp_path/'diagnostics').glob('boss-contact-*.json'))
        if variant!='no_identity':
            assert diagnostics and 'securityId' not in diagnostics[0].read_text(encoding='utf-8')
            if variant=='hidden_source':assert json.loads(diagnostics[0].read_text(encoding='utf-8'))['expected_hr_name']=='黄女士'
    finally:a.close()


def test_avatar_transport_variant_requires_matching_name_company_and_job(browser,tmp_path):
    def handler(route):
        route.fulfill(content_type='text/html; charset=utf-8',body=ACCOUNT+CHAT+'''
            <div class="friend-content selected"><img src="https://example.com/hr.jpg.webp?resize=80"><div class="name-box">
            <span class="name-text">黄女士</span><span>测试公司</span></div></div>
            <div class="chat-conversation"><span class="position-name">AI开发</span></div>''')
    a=adapter(browser,BossApplicant,tmp_path,handler)
    try:
        a.goto('https://www.zhipin.com/web/geek/chat')
        job={'external_id':'job1','title':'AI开发','company':'测试公司'}
        assert a.chat_receipt(job,'https://example.com/hr.jpg?resize=100','黄女士')['identity']['name']=='黄女士'
        for incorrect in [dict(job,company='另一公司'),dict(job,title='另一岗位')]:
            with pytest.raises(NeedHuman):a.chat_receipt(incorrect,'https://example.com/hr.jpg','黄女士')
        with pytest.raises(NeedHuman):a.chat_receipt(job,'https://example.com/hr.jpg','另一HR')
        assert not a.outbound()
    finally:a.close()
