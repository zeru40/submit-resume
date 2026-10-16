from urllib.parse import parse_qs, urlsplit
from pathlib import Path
import json
import hashlib
from urllib.parse import quote
from core.control import NeedHuman
from platforms.base import ApplicantPlatform, BeforeClickError

class WaitingReply(BeforeClickError):
    """The platform requires a reply before enabling attachment delivery."""

class BossApplicant(ApplicantPlatform):
    """Explicitly distinguishes opening a conversation from delivering an attachment."""
    def __init__(self, *args, **kwargs):
        super().__init__('boss', *args, **kwargs)

    def navigate_job(self, job, plan):
        """Select the real SPA list card before using its right-hand action."""
        # Results can reorder after each chat. Company search also narrows the
        # visible list, while the exact public job ID still binds the target.
        keywords=list(dict.fromkeys(value for value in
            [job['title'],job.get('company'),job.get('search_keyword'),plan['keyword']] if value))
        for keyword in keywords:
            url=self.spec['search'].format(keyword=quote(keyword),city_code=plan['boss_city_code'],page=1)
            self.goto(url)
            link=self.page.locator('a.job-name[href="/job_detail/'+job['external_id']+'.html"]')
            for _ in range(20):
                self.checkpoint()
                if link.count() and self.page.locator('.job-detail-container .op-btn-chat').count():break
                self.page.wait_for_timeout(250)
            if link.count()!=1:
                continue
            # Clicking the card's job area selects the inline detail. Do not use
            # the company link or a standalone legacy detail page for contact.
            self.page.wait_for_timeout(300)
            link.click()
            for _ in range(25):
                self.checkpoint()
                action=self.page.locator('.job-detail-container .op-btn-chat')
                if action.count()==1 and job['external_id'] in (action.get_attribute('ka') or ''):
                    self.verify_job(job)
                    if not action.evaluate("e=>e.matches('.is-disabled,.disabled,[aria-disabled=\"true\"]')"):
                        return
                self.page.wait_for_timeout(250)
        raise BeforeClickError('未在当前搜索列表定位到该岗位，未发起沟通')

    def verify_job(self, job):
        if urlsplit(self.page.url).path=='/web/geek/jobs':
            self.checkpoint();self.assert_url(self.page.url)
            action=self.page.locator('.job-detail-container .op-btn-chat')
            title=self.text(['.job-detail-header .job-name'])
            if (action.count()!=1 or job['external_id'] not in (action.get_attribute('ka') or '')
                    or title!=job['title']):
                raise BeforeClickError('列表中选中的岗位与目标不一致')
            body=self.page.locator('.job-detail-container').inner_text()
            if any(word in body for word in self.spec['blocked']):
                raise BeforeClickError('岗位已经关闭或下线')
            return
        return super().verify_job(job)

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

    def chat_receipt(self, job, expected_avatar):
        snapshot=self.chat_snapshot()
        if (not expected_avatar or snapshot['avatar']!=expected_avatar.split('?')[0]
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
        avatar=self.first(['.job-boss-info .detail-figure img','.job-boss-info img'])
        expected_avatar=avatar.get_attribute('src') if avatar else ''
        query = parse_qs(urlsplit(href).query)
        expected_peer = (query.get('bossId') or query.get('encryptBossId') or query.get('id') or [''])[0]
        if not expected_peer:
            reference = self.first(['[data-boss-id]','.job-boss-info a[href*="bossId="]','.boss-info a[href*="bossId="]'])
            if reference:
                expected_peer = reference.get_attribute('data-boss-id') or (parse_qs(urlsplit(reference.get_attribute('href') or '').query).get('bossId') or [''])[0]
        generation = self.control.generation
        if generation != self.control.generation:
            raise BeforeClickError('操作状态变化')
        self.external_click(button)
        # Clicking contact itself may send a platform greeting. Never re-click it.
        for _ in range(25):
            self.checkpoint()
            # A document may be replaced while the click navigates. Read again,
            # without repeating the external contact click.
            try:
                continu = self.unique(self.spec['continue_chat'])
            except RuntimeError:
                self.page.wait_for_timeout(300)
                continue
            if continu:
                continu.click()
            pages = [p for p in self.context.pages if not p.is_closed()]
            candidate = next((p for p in reversed(pages) if '/chat' in p.url and p != self.page), None)
            if candidate:
                self.page = candidate
            if '/chat' in self.page.url and self.first(self.spec['input']):
                if expected_avatar:
                    try:
                        return self.chat_receipt(job,expected_avatar)
                    except BeforeClickError:
                        # The editor can render before the selected row/header.
                        self.page.wait_for_timeout(400)
                        continue
                actual = self.peer()
                if not actual or (expected_peer and actual != expected_peer):
                    raise NeedHuman('已进入聊天，但无法核验目标HR，需人工检查')
                if not expected_peer:
                    chat_title = self.text(['.chat-info .job-name','.chat-job-info .job-name','.chat-position .job-name'])
                    chat_company = self.text(['.chat-info .company-name','.chat-job-info .company-name'])
                    if chat_title != job['title'] or not chat_company or chat_company != job['company']:
                        raise NeedHuman('已进入聊天，缺少来源HR标识且无法匹配岗位与公司，请人工核验')
                # A stable link and a loaded chat prove navigation, not resume delivery.
                return {'peer': actual, 'chat_url': self.page.url,
                        'chat_job_ref':(parse_qs(urlsplit(self.page.url).query).get('jobId') or [''])[0]}
            self.page.wait_for_timeout(400)
        # Some browser sessions keep the job page after the single contact click.
        # Verify the resulting conversation via the ordinary message-center UI;
        # do not click contact again or reuse a previously held action.
        if expected_avatar:
            self.ensure_session(scope)
            self.goto('https://www.zhipin.com/web/geek/chat')
            self.ensure_session(scope)
            source_avatar=expected_avatar.split('?')[0]
            selected=False
            for _ in range(25):
                self.checkpoint()
                if not selected:
                    indices=self.page.evaluate('''()=>[...document.querySelectorAll('.friend-content')]
                        .map((e,i)=>({i,avatar:(e.querySelector('img')?.getAttribute('src')||'').split('?')[0],
                            company:e.querySelector('.name-box > span:nth-child(2)')?.innerText.trim()}))
                        .filter(e=>e.avatar==='''+json.dumps(source_avatar)+'''&&e.company==='''+json.dumps(job['company'])+''').map(e=>e.i)''')
                    if len(indices)==1:
                        row=self.page.locator('.friend-content').nth(indices[0])
                        matches=row.evaluate('e=>(e.querySelector("img")?.getAttribute("src")||"").split("?")[0]==='+json.dumps(source_avatar)+'&&e.querySelector(".name-box > span:nth-child(2)")?.innerText.trim()==='+json.dumps(job['company']))
                        if matches:
                            row.click()
                            selected=True
                if selected:
                    try:
                        receipt=self.chat_receipt(job,expected_avatar)
                    except BeforeClickError:
                        self.page.wait_for_timeout(400)
                        continue
                    if self.outbound():
                        raise NeedHuman('对应会话已有发出消息，保留待核验，不补发文案')
                    return receipt
                self.page.wait_for_timeout(400)
        raise NeedHuman('沟通按钮已点击，未获得可核验会话，请检查是否已经发起沟通')

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
