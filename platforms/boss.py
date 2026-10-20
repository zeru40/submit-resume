from urllib.parse import parse_qs, urlsplit
from pathlib import Path
import json
import hashlib
import time
import re
import unicodedata
from urllib.parse import quote
from core.control import NeedHuman
from platforms.base import ApplicantPlatform, BeforeClickError, JobUnavailable

class WaitingReply(BeforeClickError):
    """The platform requires a reply before enabling attachment delivery."""

def avatar_key(value):
    parsed=urlsplit(value or '')
    path=parsed.path
    if path.lower().endswith(('.jpg.webp','.jpeg.webp','.png.webp')):path=path[:-5]
    return parsed._replace(path=path,query='',fragment='').geturl()

class BossApplicant(ApplicantPlatform):
    """Explicitly distinguishes opening a conversation from delivering an attachment."""
    def __init__(self, *args, **kwargs):
        super().__init__('boss', *args, **kwargs)

    def select_search_card(self, job):
        href='/job_detail/'+job['external_id']+'.html'
        link=self.page.locator('a.job-name[href="'+href+'"]')
        if link.count()!=1:raise BeforeClickError('未找到唯一岗位标题，未选择岗位')
        # Current BOSS binds inline selection to the card, while its title link
        # can consume clicks without changing the selected detail.
        for wrapper in ('.job-card-box',):
            card=self.page.locator(wrapper+':has(a.job-name[href="'+href+'"])')
            if card.count()==1:
                card.click()
                return
            if card.count()>1:raise BeforeClickError('岗位卡片不唯一，未选择岗位')
        link.click() # Legacy layout without card containers.

    def advance_search_results(self, scope, known_ids=None):
        """Load one more visible result batch without resetting an infinite list."""
        selector='a.job-name[href*="/job_detail/"]'
        links=self.page.locator(selector)
        count=links.count()
        if not count:return False
        before=set(self.page.evaluate("()=>[...document.querySelectorAll('a.job-name[href*=\"/job_detail/\"]')].map(e=>e.getAttribute('href'))"))
        if known_ids is not None and any(self.detail_id(href) not in known_ids for href in before):
            return True
        self.pace_navigation()
        self.page.bring_to_front()
        links.nth(count-1).evaluate("e=>{(e.closest('.job-card-box')||e).scrollIntoView({block:'end',behavior:'instant'});window.scrollBy({top:Math.max(400,innerHeight*.75),behavior:'instant'});return true;}")
        deadline=time.monotonic()+20
        session_check=time.monotonic()+2
        while time.monotonic()<deadline:
            self.checkpoint()
            if time.monotonic()>=session_check:
                self.ensure_session(scope)
                session_check=time.monotonic()+2
            current=set(self.page.evaluate("()=>[...document.querySelectorAll('a.job-name[href*=\"/job_detail/\"]')].map(e=>e.getAttribute('href'))"))
            if current-before:return True
            self.page.wait_for_timeout(400)
        return False

    def navigate_job(self, job, plan):
        """Select the real SPA list card before using its right-hand action."""
        # Results can reorder after each chat. Company search also narrows the
        # visible list, while the exact public job ID still binds the target.
        urls=[]
        source=job.get('source_url','')
        if source:
            self.assert_url(source)
            parsed=urlsplit(source)
            if parsed.path=='/web/geek/jobs' and parse_qs(parsed.query).get('city')==[plan['boss_city_code']]:
                urls.append(source)
        # A cached role can move between result pages. Try its recorded page,
        # then bounded exact-title/company searches; absence is not a send error.
        for keyword in dict.fromkeys([job['title'],job.get('company')]):
            if keyword:
                urls.append(self.spec['search'].format(keyword=quote(keyword),city_code=plan['boss_city_code'],page=1))
        urls=list(dict.fromkeys(urls))
        current=self.page.url
        if urlsplit(current).path=='/web/geek/jobs' and self.page.locator('a.job-name[href="/job_detail/'+job['external_id']+'.html"]').count()==1:
            urls.insert(0,current)
        for url in dict.fromkeys(urls):
            if self.page.url!=url:self.goto(url)
            self.ensure_session(self.scope or None)
            link=self.page.locator('a.job-name[href="/job_detail/'+job['external_id']+'.html"]')
            for _ in range(20):
                self.checkpoint()
                if link.count() and self.page.locator('.job-detail-container .op-btn-chat').count():break
                self.page.wait_for_timeout(250)
            if url==source and link.count()!=1:
                for _ in range(min(max(int(job.get('source_batch',1))-1,0),9)):
                    if not self.advance_search_results(self.scope):break
                    if link.count()==1:break
            if link.count()!=1:
                continue
            # Clicking the card's job area selects the inline detail. Do not use
            # the company link or a standalone legacy detail page for contact.
            self.page.wait_for_timeout(300)
            self.pace_navigation()
            self.select_search_card(job)
            for _ in range(25):
                self.checkpoint()
                action=self.page.locator('.job-detail-container .op-btn-chat')
                if action.count()==1 and job['external_id'] in (action.get_attribute('ka') or ''):
                    self.verify_job(job)
                    if not action.evaluate("e=>e.matches('.is-disabled,.disabled,[aria-disabled=\"true\"]')"):
                        return
                self.page.wait_for_timeout(250)
            # A present card with a mismatched detail is uncertain, not absent.
            raise BeforeClickError('岗位卡片存在，但详情身份未完成核验，已停止')
        raise JobUnavailable('限定搜索范围内未定位到该岗位，未发起沟通，已标记跳过')

    def verify_job(self, job):
        if urlsplit(self.page.url).path=='/web/geek/jobs':
            self.checkpoint();self.assert_url(self.page.url)
            action=self.page.locator('.job-detail-container .op-btn-chat')
            title=self.text(['.job-detail-header .job-name'])
            if (action.count()!=1 or not (action.get_attribute('ka') or '').endswith('_'+job['external_id'])
                    or title!=job['title']):
                raise BeforeClickError('列表中选中的岗位与目标不一致')
            body=self.page.locator('.job-detail-container').inner_text()
            if any(word in body for word in self.spec['blocked']):
                raise JobUnavailable('岗位已经关闭或下线')
            return
        return super().verify_job(job)

    def reconcile_discovery_title(self, job):
        """Resolve a list alias only during discovery, never during sending.

        BOSS may omit qualifiers such as an overseas assignment from its card.
        Bind the complete title to the exact ID and both company labels before
        recording it for review. A later changed title still fails verify_job.
        """
        snapshot=self.page.evaluate('''()=>{
            const id=JOB_ID, selectors=COMPANY_SELECTORS;
            const links=[...document.querySelectorAll('a.job-name')]
                .filter(e=>e.getAttribute('href')==='/job_detail/'+id+'.html');
            const card=links.length===1?links[0].closest('.job-card-box,.job-card-wrapper,.job-card'):null;
            const company=card?(selectors.map(s=>card.querySelector(s)?.innerText.trim()).find(Boolean)||''):'';
            const titles=[...document.querySelectorAll('.job-detail-header .job-name')];
            const actions=[...document.querySelectorAll('.job-detail-container .op-btn-chat')];
            const bosses=[...document.querySelectorAll('.job-detail-container .job-boss-info .boss-info-attr')];
            return {links:links.length,listing:links[0]?.innerText.trim()||'',company,
                titles:titles.length,title:titles[0]?.innerText.trim()||'',actions:actions.length,
                reference:actions[0]?.getAttribute('ka')||'',bosses:bosses.length,
                detail_company:bosses.length===1?bosses[0].innerText.split(' · ')[0].trim():''};
        }'''.replace('JOB_ID',json.dumps(job['external_id']))
           .replace('COMPANY_SELECTORS',json.dumps(self.spec['company'])))
        if snapshot['titles']==1 and snapshot['title']==job['title']:return
        compact=lambda text:re.sub(r'\s+','',unicodedata.normalize('NFKC',text))
        listing,detail=compact(job['title']),compact(snapshot['title'])
        compatible=bool(listing) and (listing==detail or (
            detail.startswith(listing) and re.fullmatch(r'(?:\([^()]+\))+',detail[len(listing):]) is not None))
        if (snapshot['links']!=1 or snapshot['titles']!=1 or snapshot['actions']!=1
                or not compatible
                or not snapshot['reference'].endswith('_'+job['external_id'])
                or snapshot['listing']!=job['title'] or not snapshot['title']
                or not job.get('company') or snapshot['company']!=job['company']
                or snapshot['bosses']!=1 or snapshot['detail_company']!=job['company']):
            raise BeforeClickError('列表与详情标题不同，且岗位ID或公司未完成唯一核验，已停止搜索')
        job['listing_title']=job['title']
        job['title']=snapshot['title']

    def read_search_description(self, job, scope):
        """Current BOSS list exposes details inline; avoid a full page per job."""
        if urlsplit(self.page.url).path!='/web/geek/jobs' or not self.page.locator('.job-detail-container').count():
            return super().read_search_description(job,scope)
        link=self.page.locator('a.job-name[href="/job_detail/'+job['external_id']+'.html"]')
        if link.count()!=1:raise BeforeClickError('未找到唯一岗位卡片，已停止搜索')
        self.pace_navigation()
        self.select_search_card(job)
        for _ in range(25):
            self.checkpoint()
            action=self.page.locator('.job-detail-container .op-btn-chat')
            if action.count()==1 and (action.get_attribute('ka') or '').endswith('_'+job['external_id']):
                self.ensure_session(scope)
                self.reconcile_discovery_title(job)
                self.verify_job(job)
                description=self.text(self.spec['description'],self.page.locator('.job-detail-container'))
                if len(description)>=30:return description
            self.page.wait_for_timeout(300)
        raise BeforeClickError('岗位内嵌详情未完成核验，已停止搜索')

    def peer(self):
        snapshot = self.chat_snapshot()
        if snapshot.get('avatar'):
            return 'avatar:' + hashlib.sha256(snapshot['avatar'].encode()).hexdigest()
        query = parse_qs(urlsplit(self.page.url).query)
        return (query.get('bossId') or query.get('encryptBossId') or query.get('id') or [''])[0]

    def chat_snapshot(self):
        return self.page.evaluate('''() => {
            const rows=[...document.querySelectorAll('.friend-content.selected')];
            const row=rows.length===1?rows[0]:null;
            const box=row?.querySelector('.name-box');
            const parts=box?[...box.querySelectorAll(':scope > span')].map(e=>e.innerText.trim()):[];
            const titles=[...document.querySelectorAll('.chat-conversation .position-name')];
            return {avatar:(row?.querySelector('img')?.src||'').split('?')[0],
                    name:row?.querySelector('.name-text')?.innerText.trim()||'',
                    company:parts[1]||'',title:titles.length===1?titles[0].innerText.trim():''};
        }''')

    def chat_receipt(self, job, expected_avatar, expected_name=''):
        snapshot=self.chat_snapshot()
        if (not expected_avatar or avatar_key(snapshot['avatar'])!=avatar_key(expected_avatar)
                or (expected_name and snapshot['name']!=expected_name)
                or snapshot['company']!=job['company'] or snapshot['title'] not in
                {job['title'],'兼职·'+job['title'],'全职·'+job['title'],'实习·'+job['title']}):
            raise BeforeClickError('聊天对象、公司或岗位与来源不一致，未发送内容')
        return {'peer':self.peer(),'chat_url':'https://www.zhipin.com/web/geek/chat',
                'identity':snapshot,'job_id':f"boss:{job['external_id']}"}

    def _verify_identity(self, receipt):
        expected=receipt.get('identity')
        if expected and self.chat_snapshot()!=expected:
            raise BeforeClickError('当前选中的HR、公司或岗位已变化，未发送内容')

    def outbound(self):
        for selector in self.spec['messages_out']:
            rows = self.page.locator(selector)
            if rows.count():
                return rows.all_inner_texts()
        return []

    def existing_companies(self, scope):
        """Read the visible conversation list, including manually created chats."""
        if '/web/geek/chat' not in self.page.url:
            self.goto('https://www.zhipin.com/web/geek/chat')
        self.ensure_session(scope)
        for _ in range(25):
            self.checkpoint()
            if self.page.locator('.friend-content').count():
                break
            self.page.wait_for_timeout(300)
        return set(self.page.evaluate('''()=>[...document.querySelectorAll('.friend-content .name-box')]
            .map(e=>[...e.querySelectorAll(':scope > span')][1]?.innerText.trim()).filter(Boolean)'''))

    def already_contacted(self):
        button=self.unique(self.spec['contact'])
        if not button:
            raise BeforeClickError('未找到唯一沟通入口，不能核验是否已沟通')
        flag=(button.get_attribute('data-isfriend') or '').lower()
        return flag in {'true','1'} or '继续沟通' in button.inner_text() or '已沟通' in button.inner_text()

    def prepare_resume(self,resume,scope):
        """Upload the supplied bytes before contacting an HR, when no attachment exists."""
        self.ensure_session(scope)
        filename=Path(resume['name']).name
        # The detail-page upload shortcut can be inert on the current site.
        # Use the actual attachment manager linked by the signed-in header.
        self.goto('https://www.zhipin.com/web/geek/resume')
        self.ensure_session(scope)
        existing=self.page.locator('a[title='+json.dumps(filename,ensure_ascii=False)+']')
        for _ in range(20):
            self.checkpoint()
            if existing.count():break
            self.page.wait_for_timeout(200)
        if existing.count()==1:
            info=existing.evaluate("e=>(e.closest('.annex-content')||e.parentElement).innerText")
            expected=f"{Path(resume['path']).stat().st_size/1024:.1f}KB"
            if expected in info.replace(' ',''):
                return {'filename':filename,'local_sha256':resume.get('sha256',''),
                        'source':'platform_existing','verified_by':'exact_name_and_displayed_size','size':expected}
            raise BeforeClickError('平台已有同名附件但文件大小不一致，请核验版本，未替换原附件')
        for _ in range(15):
            self.checkpoint()
            all_inputs=self.page.locator('input[type="file"][accept*=".pdf"]')
            available=[all_inputs.nth(i) for i in range(all_inputs.count())
                       if all_inputs.nth(i).evaluate('e=>Boolean(e.parentElement.getClientRects().length)')]
            if len(available)==1:
                break
            self.page.wait_for_timeout(200)
        if len(available)!=1:
            raise BeforeClickError('已打开附件上传入口，但未识别到唯一的文件控件，请先在平台上传指定PDF')
        import mimetypes
        self.ensure_session(scope)
        self.external_started=True
        available[0].set_input_files({'name':filename,'mimeType':mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                                'buffer':Path(resume['path']).read_bytes()})
        for _ in range(40):
            self.checkpoint()
            if filename in self.page.locator('body').inner_text():
                return {'filename':filename,'sha256':resume.get('sha256',''),'source':'local_upload'}
            self.page.wait_for_timeout(300)
        raise NeedHuman('附件已提交上传，未核验到文件名，请检查平台附件状态')

    def _guard_chat(self, expected_peer, scope, receipt=None):
        self.ensure_session(scope)
        if not expected_peer or self.peer() != expected_peer:
            raise BeforeClickError('无法确认聊天对象的稳定标识，请人工处理当前会话')
        if receipt:
            self._verify_identity(receipt)
        if receipt and receipt.get('chat_job_ref'):
            current = (parse_qs(urlsplit(self.page.url).query).get('jobId') or [''])[0]
            if current != receipt['chat_job_ref']:
                raise BeforeClickError('聊天关联的岗位发生变化')
        editor = self.first(self.spec['input'])
        if not editor:
            raise BeforeClickError('没有识别到聊天输入框')
        if self.page.evaluate('Boolean(window.__careerComposing)'):
            raise BeforeClickError('输入法正在编辑，请先完成人工输入')
        if (editor.input_value() if editor.evaluate('(el) => el.tagName === "TEXTAREA" || el.tagName === "INPUT"') else editor.inner_text()).strip():
            raise BeforeClickError('聊天框已有草稿，请先人工处理')

    def contact(self, job, scope):
        self.verify_job(job)
        self.ensure_session(scope)
        button = self.unique(self.spec['contact'])
        if not button:
            raise BeforeClickError('未找到沟通入口')
        if button.evaluate("e=>e.matches('.is-disabled,.disabled,[aria-disabled=\"true\"]')"):
            raise BeforeClickError('沟通入口尚不可用，未发起沟通')
        href = button.get_attribute('redirect-url') or button.get_attribute('href') or ''
        hr=self.unique(['.job-detail-container .job-boss-info','.job-boss-info'])
        avatar=self.first(['.detail-figure img','img'],hr) if hr else None
        expected_avatar=avatar.get_attribute('src') if avatar else ''
        name=self.first(['.name'],hr) if hr else None
        expected_name=name.evaluate("e=>[...e.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim()") if name else ''
        query = parse_qs(urlsplit(href).query)
        if query.get('jobId') and query['jobId']!=[job['external_id']]:
            raise BeforeClickError('沟通入口关联的岗位与目标不一致，未点击沟通')
        expected_peer = (query.get('bossId') or query.get('encryptBossId') or query.get('id') or [''])[0]
        if not expected_peer:
            reference = self.first(['[data-boss-id]','.job-boss-info a[href*="bossId="]','.boss-info a[href*="bossId="]'])
            if reference:
                expected_peer = reference.get_attribute('data-boss-id') or (parse_qs(urlsplit(reference.get_attribute('href') or '').query).get('bossId') or [''])[0]
        if not expected_peer and not (expected_avatar and expected_name):
            raise BeforeClickError('沟通前缺少来源HR身份信息，未点击沟通')
        previous_pages=list(self.context.pages)
        evidence={'job_id':job['external_id'],'company':job['company'],'title':job['title'],
                  'expected_hr_name':expected_name,'expected_avatar':avatar_key(expected_avatar),'observations':[]}
        evidence_path=self.data_dir/'diagnostics'/f"boss-contact-{hashlib.sha256(job['external_id'].encode()).hexdigest()[:12]}-{time.time_ns()}.json"
        evidence_path.parent.mkdir(parents=True,exist_ok=True)
        def observe(before=False):
            info=self.page.evaluate('''()=>({path:location.pathname,
                blocked:['访问受限','已暂时被限制访问','今日沟通人数已达上限','今日沟通数量已达上限',
                    '今日沟通次数已达上限','沟通人数已达上限','操作过于频繁','请完成安全验证','账号异常']
                    .filter(text=>document.body.innerText.includes(text)),
                notices:[...document.querySelectorAll('[role="alert"],[role="dialog"],.dialog-wrap,.dialog-container,.toast,.toast-content,.message-tip,.message-error,.error-tip')]
                    .filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden')
                    .map(e=>e.innerText.trim().slice(0,600)).filter(Boolean),
                friendship:[...document.querySelectorAll('.job-detail-container .op-btn-chat,.btn-startchat')]
                    .filter(e=>e.getClientRects().length).map(e=>({text:e.innerText.trim(),flag:e.getAttribute('data-isfriend')}))})''')
            if not evidence['observations'] or evidence['observations'][-1]!=info:
                evidence['observations'].append(info)
                evidence_path.write_text(json.dumps(evidence,ensure_ascii=False),encoding='utf-8')
            if info['notices'] or info['blocked']:
                if before:raise BeforeClickError('沟通前页面存在提示或弹窗，未点击沟通，请先处理原页面')
                if info['blocked']:raise NeedHuman('平台提示：'+'、'.join(info['blocked'])+'；已保留现场，未发送文案')
                raise NeedHuman('点击沟通后平台出现提示，已保留原页面和本地诊断，未发送文案，请人工核验')
            return any(x['flag'] in ('true','1') or '继续沟通' in x['text'] or '已沟通' in x['text'] for x in info['friendship'])
        # Persist only local identity evidence, never redirect URLs/security tokens.
        evidence_path.write_text(json.dumps(evidence,ensure_ascii=False),encoding='utf-8')
        observe(before=True)
        generation = self.control.generation
        if generation != self.control.generation:
            raise BeforeClickError('操作状态变化')
        self.external_click(button)
        confirmed_contact=False
        continued=False
        # Clicking contact itself may send a platform greeting. Never re-click it.
        for _ in range(25):
            self.checkpoint()
            # A document may be replaced while the click navigates. Read again,
            # without repeating the external contact click.
            try:
                confirmed_contact=observe() or confirmed_contact
                continu = self.unique(self.spec['continue_chat'])
            except RuntimeError:
                self.page.wait_for_timeout(300)
                continue
            if continu and not continued:
                continu.click()
                continued=True
            pages = [p for p in self.context.pages if not p.is_closed()]
            candidate = next((p for p in reversed(pages) if '/web/geek/chat' in p.url
                              and p != self.page and p not in previous_pages), None)
            if candidate:
                self.page = candidate
            if '/chat' in self.page.url and self.first(self.spec['input']):
                self.ensure_session(scope)
                if expected_avatar:
                    try:
                        receipt=self.chat_receipt(job,expected_avatar,expected_name)
                    except BeforeClickError:
                        # The editor can render before the selected row/header.
                        self.page.wait_for_timeout(400)
                        continue
                    if self.outbound():raise NeedHuman('对应会话已有发出消息，保留待核验，不补发文案')
                    return receipt
                actual = self.peer()
                if not actual or (expected_peer and actual != expected_peer):
                    raise NeedHuman('已进入聊天，但无法核验目标HR，需人工检查')
                if not expected_peer:
                    chat_title = self.text(['.chat-info .job-name','.chat-job-info .job-name','.chat-position .job-name'])
                    chat_company = self.text(['.chat-info .company-name','.chat-job-info .company-name'])
                    if chat_title != job['title'] or not chat_company or chat_company != job['company']:
                        raise NeedHuman('已进入聊天，缺少来源HR标识且无法匹配岗位与公司，请人工核验')
                # A stable link and a loaded chat prove navigation, not resume delivery.
                if self.outbound():raise NeedHuman('对应会话已有发出消息，保留待核验，不补发文案')
                return {'peer': actual, 'chat_url': self.page.url,
                        'chat_job_ref':(parse_qs(urlsplit(self.page.url).query).get('jobId') or [''])[0]}
            self.page.wait_for_timeout(400)
        # Some browser sessions keep the job page after the single contact click.
        # Verify the resulting conversation via the ordinary message-center UI;
        # do not click contact again or reuse a previously held action.
        if expected_avatar and confirmed_contact:
            self.ensure_session(scope)
            self.goto('https://www.zhipin.com/web/geek/chat')
            self.ensure_session(scope)
            source_avatar=avatar_key(expected_avatar)
            selected=False
            for _ in range(25):
                self.checkpoint()
                if not selected:
                    rows=self.page.evaluate('''()=>[...document.querySelectorAll('.friend-content')]
                        .map((e,i)=>({i,avatar:e.querySelector('img')?.src||'',
                            name:e.querySelector('.name-text')?.innerText.trim()||'',
                            company:e.querySelector('.name-box > span:nth-child(2)')?.innerText.trim()}))''')
                    indices=[r['i'] for r in rows if avatar_key(r['avatar'])==source_avatar
                             and r['company']==job['company'] and (not expected_name or r['name']==expected_name)]
                    if len(indices)==1:
                        row=self.page.locator('.friend-content').nth(indices[0])
                        current=row.evaluate('''e=>({avatar:e.querySelector('img')?.src||'',name:e.querySelector('.name-text')?.innerText.trim()||'',company:e.querySelector('.name-box > span:nth-child(2)')?.innerText.trim()})''')
                        matches=(avatar_key(current['avatar'])==source_avatar and current['company']==job['company']
                                 and (not expected_name or current['name']==expected_name))
                        if matches:
                            row.click()
                            selected=True
                if selected:
                    try:
                        receipt=self.chat_receipt(job,expected_avatar,expected_name)
                    except BeforeClickError:
                        self.page.wait_for_timeout(400)
                        continue
                    if self.outbound():
                        raise NeedHuman('对应会话已有发出消息，保留待核验，不补发文案')
                    return receipt
                self.page.wait_for_timeout(400)
        raise NeedHuman('沟通点击后未取得完整回执，已保留现场并标记待核验；不会跳转掩盖提示或重复点击')

    def open_chat(self, receipt, scope):
        if '/web/geek/chat' not in self.page.url:
            self.goto(receipt['chat_url'])
        for _ in range(20):
            self.checkpoint()
            if self.first(self.spec['input']):
                break
            self.page.wait_for_timeout(300)
        identity=receipt.get('identity')
        if identity and self.chat_snapshot()!=identity:
            matches=[]
            for _ in range(20):
                self.checkpoint()
                rows=self.page.locator('.friend-content')
                matches=[]
                for i in range(rows.count()):
                    row=rows.nth(i)
                    img=row.locator('img').first
                    # Rows also contain menu icons. The first image is the HR
                    # avatar; bind name/company too because avatars can be shared.
                    if (img.count()==1 and (img.get_attribute('src') or '').split('?')[0]==identity['avatar']
                            and identity['name'] in row.inner_text() and identity['company'] in row.inner_text()):
                        matches.append(row)
                if matches:
                    break
                self.page.wait_for_timeout(300)
            if len(matches)!=1:
                raise BeforeClickError('未在聊天列表找到唯一的原HR，请人工打开原会话')
            matches[0].click()
            self.page.wait_for_timeout(400)
        self._guard_chat(receipt['peer'], scope, receipt)

    def greet(self, message, receipt, scope):
        self._guard_chat(receipt['peer'], scope, receipt)
        before = self.outbound()
        editor = self.unique(self.spec['input'])
        send = self.unique(self.spec['send'])
        if not send:
            raise BeforeClickError('未识别到发送按钮')
        generation = self.control.generation
        editor.fill(message)
        self.checkpoint()
        if generation != self.control.generation or self.peer() != receipt['peer']:
            raise BeforeClickError('填写期间发生暂停或会话变化，请检查草稿')
        self._verify_identity(receipt)
        actual_text=editor.input_value() if editor.evaluate('e=>e.tagName==="TEXTAREA"||e.tagName==="INPUT"') else editor.inner_text()
        if actual_text.strip()!=message.strip():
            raise BeforeClickError('输入内容与预期不一致，请检查草稿')
        self.ensure_session(scope)
        self.external_click(send)
        for _ in range(20):
            self.checkpoint()
            after = self.outbound()
            if len(after) > len(before) and message.strip() in after[-1] and self.peer() == receipt['peer']:
                return {'message':message, 'peer':receipt['peer']}
            self.page.wait_for_timeout(300)
        raise NeedHuman('消息发送结果未确认，不会重复发送，请人工核验')

    def send_resume(self, resume, receipt, scope):
        self._guard_chat(receipt['peer'], scope, receipt)
        filename = Path(resume['name']).name
        before = self.outbound()
        button = self.unique(self.spec['resume_button'])
        if not button:
            raise BeforeClickError('没有识别到发简历入口，请人工发送附件')
        if button.evaluate('e=>e.disabled || e.classList.contains("unable") || e.getAttribute("aria-disabled")==="true"'):
            label=button.get_attribute('aria-label') or ''
            if '双方回复后可用' in label:
                raise WaitingReply('沟通文案已处理；平台要求双方回复后才能发简历，等待HR回复后继续')
            raise BeforeClickError('发简历入口暂不可用：'+label)
        # This click may itself submit a default resume; unknown is safer than a retry.
        self.external_click(button)
        self.page.wait_for_timeout(500)
        dialog = self.unique(self.spec['resume_dialog'])
        if dialog:
            # Choose ONLY an exact visible filename. Never silently send a default attachment.
            chosen = dialog.get_by_text(filename, exact=True)
            if chosen.count() == 0:
                upload = dialog.locator('input[type="file"]')
                if upload.count() == 1:
                    import mimetypes
                    upload.set_input_files({'name':filename,'mimeType':mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                                           'buffer':Path(resume['path']).read_bytes()})
                    for _ in range(20):
                        self.checkpoint()
                        if chosen.count() == 1 and chosen.is_visible():
                            break
                        self.page.wait_for_timeout(400)
            if chosen.count() != 1 or not chosen.is_visible():
                raise NeedHuman(f'未识别到指定附件“{filename}”；请在BOSS管理附件简历后核验当前操作，未自动选择其他简历')
            chosen.click()
            self.checkpoint()
            self.ensure_session(scope)
            if self.peer() != receipt['peer']:
                raise NeedHuman('选择简历后会话变化，请人工核验')
            confirm = self.unique(self.spec['resume_confirm'], dialog)
            if not confirm:
                raise NeedHuman('未识别到附件发送确认按钮')
            confirm.click()
        for _ in range(25):
            self.checkpoint()
            after = self.outbound()
            if len(after) > len(before) and filename in '\n'.join(after[len(before):]) and self.peer() == receipt['peer']:
                return {'filename':filename, 'peer':receipt['peer'], 'evidence':after[-1]}
            self.page.wait_for_timeout(300)
        raise NeedHuman('附件发送结果未确认，请在平台核验附件名称，系统不会重复发送')
