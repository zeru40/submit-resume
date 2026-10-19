import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, quote

from config import browser_path
from core.control import NeedHuman
from platforms.specs import SPECS

class BeforeClickError(NeedHuman):
    """No external action has happened; safe to mark this attempt failed."""

class ApplicantPlatform:
    def __init__(self, code, playwright, data_dir, control, status, log):
        self.code, self.spec = code, SPECS[code]
        self.control, self.publish, self.log = control, status, log
        self.data_dir = Path(data_dir)
        profile = self.data_dir / 'profiles' / code
        self.profile = profile
        from core.browser_runtime import open_edge
        self.context = open_edge(playwright,browser_path(),profile,self.spec['home'],
                                 self.data_dir,code,self.log)
        self.context.add_init_script('''
            window.__careerComposing = false;
            addEventListener('compositionstart', () => window.__careerComposing = true, true);
            addEventListener('compositionend', () => window.__careerComposing = false, true);
        ''')
        self.page = self.select_page()
        self.page.set_default_timeout(6000)
        self.scope = ''
        self.external_started = False
        self.navigation_interval_seconds = 15 if code == 'boss' else 0

    def select_page(self):
        """A restored login tab can sit behind an initial about:blank tab."""
        refresh = getattr(self.context,'refresh',None)
        if callable(refresh):
            refresh()
        pages = [p for p in self.context.pages if not p.is_closed()]
        hosts = set(self.spec['hosts'])
        site_root = 'zhipin.com' if self.code == 'boss' else 'zhaopin.com'
        for candidate in reversed(pages):
            host = (urlsplit(candidate.url).hostname or '').lower()
            if host in hosts or host.endswith('.'+site_root):
                return candidate
        return pages[-1] if pages else self.context.new_page()

    def external_click(self, button):
        self.checkpoint()
        self.external_started = True
        button.click()

    def checkpoint(self):
        self.control.checkpoint(self.page.wait_for_timeout)
        if self.page.is_closed():
            raise NeedHuman('平台页面已关闭，请重新打开平台')

    def first(self, selectors, scope=None):
        root = scope or self.page
        for selector in selectors:
            loc = root.locator(selector)
            for i in range(min(loc.count(), 30)):
                candidate = loc.nth(i)
                if candidate.is_visible():
                    return candidate
        return None

    def unique(self, selectors, scope=None):
        root = scope or self.page
        for selector in selectors:
            loc = root.locator(selector)
            visible = [loc.nth(i) for i in range(min(loc.count(), 30)) if loc.nth(i).is_visible()]
            if len(visible) == 1:
                return visible[0]
            if len(visible) > 1:
                raise NeedHuman('页面出现多个相同操作入口，已暂停，请人工检查')
        return None

    def text(self, selectors, scope=None):
        loc = self.first(selectors, scope)
        return loc.inner_text().strip() if loc else ''

    def assert_url(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in self.spec['hosts']:
            raise NeedHuman('页面离开受支持的平台地址，请检查浏览器')

    def pace_navigation(self):
        self.checkpoint()
        previous = getattr(self, '_last_navigation', None)
        if previous is not None:
            delay = max(0, getattr(self, 'navigation_interval_seconds', 0) - (time.monotonic() - previous))
            self.control.wait(delay, self.page.wait_for_timeout)
        self._last_navigation = time.monotonic()

    def goto(self, url):
        self.assert_url(url)
        self.pace_navigation()
        self.page.goto(url, wait_until='commit', timeout=20000)
        self.page.wait_for_timeout(1000)

    def open(self):
        self.page = self.select_page()
        # Preserve an existing login document; repeatedly reopening must not
        # reset a QR code or create another blank tab during manual login.
        host=(urlsplit(self.page.url).hostname or '').lower()
        root='zhipin.com' if self.code=='boss' else 'zhaopin.com'
        if host!=root and not host.endswith('.'+root):
            self.goto(self.spec['home'])
        self.page = self.select_page()
        observed = self.session()
        login_url = self.spec.get('login')
        if (observed['state']=='waiting_login' and login_url
                and not urlsplit(self.page.url).path.startswith('/web/user')):
            self.goto(login_url)
            self.page = self.select_page()
        if self.page.url in {'about:blank',''}:
            raise NeedHuman('浏览器仍停在空白页，BOSS尚未加载；请检查页面连接')
        self.page.bring_to_front()
        # Only close a blank page in this app's own context, after a real site is open.
        for other in self.context.pages:
            if other != self.page and not other.is_closed() and other.url == 'about:blank':
                other.close()
        result = self.session()
        self.log(f"已打开{self.spec['label']}：{urlsplit(self.page.url).netloc}{urlsplit(self.page.url).path}")
        return result

    def session(self):
        if self.page.is_closed() or self.page.url in {'about:blank',''}:
            pages = [p for p in self.context.pages if not p.is_closed()]
            if pages:
                self.page = self.select_page()
        if self.page.is_closed():
            return {'state':'closed', 'message':'浏览器已关闭', 'account':''}
        url = self.page.url
        if url in {'about:blank',''}:
            return {'state':'page_unavailable','message':'当前是空白页，尚未进入平台，请点击打开平台','account':''}
        host = urlsplit(url).hostname or ''
        if host not in self.spec['hosts']:
            return {'state':'waiting_login', 'message':'请在平台页面完成登录', 'account':''}
        if self.code == 'boss' and urlsplit(url).path == '/web/passport/zp/403.html':
            return {'state':'needs_human', 'reason':'access_restricted',
                    'message':'平台已限制访问，请停止操作并等待平台解除限制；重新登录不能视为已恢复', 'account':''}
        body = self.page.locator('body').inner_text(timeout=5000)
        if any(x in body for x in ('访问受限', '已暂时被限制访问', '请勿频繁提交刷新请求')):
            return {'state':'needs_human', 'reason':'access_restricted',
                    'message':'平台已限制访问，请停止操作并等待平台解除限制；重新登录不能视为已恢复', 'account':''}
        if any(x in body for x in ('访问过于频繁', '请完成安全验证', '账号异常', '滑动验证', '请完成验证',
                                   '今日沟通人数已达上限','今日沟通数量已达上限','沟通人数已达上限')):
            return {'state':'needs_human', 'message':'平台要求验证，请人工处理', 'account':''}
        account = ''
        loc = self.first(self.spec['account'])
        if loc:
            account = (loc.get_attribute('title') or loc.inner_text()).strip()
        if not account or account in {'登录', '登录/注册', '注册'}:
            return {'state':'waiting_login', 'message':'请登录求职账号；若已登录但未识别，请检查页面适配', 'account':''}
        scope = hashlib.sha256(f'{self.code}:{self.profile.resolve()}:{account}'.encode()).hexdigest()[:32]
        return {'state':'ready', 'message':'求职账号已识别', 'account':account, 'scope':scope}

    def inspect(self):
        """Read-only developer diagnostics; never captures credentials or cookies."""
        import json
        self.page = self.select_page()
        target = self.data_dir / 'diagnostics' / f'{self.code}-page.json'
        target.parent.mkdir(parents=True,exist_ok=True)
        if self.page.is_closed():
            raise NeedHuman('平台窗口已关闭')
        info = self.page.evaluate('''() => ({
            url: location.origin + location.pathname,
            title: document.title,
            ready:document.readyState,
            timeOrigin:performance.timeOrigin,
            navigationType:performance.getEntriesByType('navigation')[0]?.type,
            cardSamples:Array.from(document.querySelectorAll('.job-card-box,.job-card-wrapper,.joblist-box__item')).slice(0,2).map(e=>e.outerHTML.slice(0,16000)),
            text: document.body?.innerText?.slice(0,18000) || '',
            elements: Array.from(document.querySelectorAll('a,button,[class*="job-name"],[class*="user-name"],[class*="label-name"],textarea,[contenteditable="true"]'))
                .filter(e => e.getClientRects().length)
                .slice(0,160).map(e => ({tag:e.tagName,cls:e.className,id:e.id,
                    text:e.innerText?.slice(0,160)||'',href:e.tagName==='A'?e.getAttribute('href'):null}))
        })''')
        target.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        return target.name

    def ensure_session(self, expected_scope=None):
        self.checkpoint()
        state = self.session()
        # SPA navigation can render its header after the document is ready.
        # Wait for it in place; never reload a login or reset its QR code.
        for _ in range(36):
            if state['state']!='waiting_login':
                break
            self.page.wait_for_timeout(300)
            self.checkpoint()
            state=self.session()
        self.publish(self.code, state)
        if state['state'] != 'ready':
            raise NeedHuman(state['message'])
        if expected_scope and state['scope'] != expected_scope:
            raise NeedHuman('当前平台账号发生变化，已停止，重新开始任务前请核对账号')
        self.scope = state['scope']
        return self.scope

    def detail_id(self, url):
        parsed = urlsplit(url)
        if self.code == 'boss':
            match = re.search(r'/job_detail/([^/.]+)', parsed.path)
        else:
            match = re.search(r'/([A-Za-z0-9_-]+)\.html', parsed.path)
            if not match:
                match = re.search(r'/jobdetail/([^/?]+)', parsed.path)
        return match[1] if match else ''

    def verify_job(self, job):
        self.checkpoint()
        self.assert_url(self.page.url)
        if self.detail_id(self.page.url) != job['external_id']:
            raise BeforeClickError('当前页面与目标岗位不一致')
        body = self.page.locator('body').inner_text()
        if any(word in body for word in self.spec['blocked']):
            raise BeforeClickError('岗位已经关闭或下线')

    def search(self, plan):
        scope = self.ensure_session()
        if getattr(self, '_search_cache_scope', None) != scope:
            self._search_cache_scope = scope
            self._search_cache = {}
        seen = set()
        emitted = 0
        for page_num in range(1, plan['max_pages'] + 1):
            url = self.spec['search'].format(keyword=quote(plan['keyword']), city_code=quote(plan[f'{self.code}_city_code']), page=page_num)
            self.goto(url)
            self.ensure_session(scope)
            cards = None
            for _ in range(15):
                self.checkpoint()
                for selector in self.spec['cards']:
                    loc = self.page.locator(selector)
                    if loc.count():
                        cards = loc
                        break
                if cards is not None:
                    break
                self.page.wait_for_timeout(400)
            if cards is None:
                raise NeedHuman('没有识别到岗位列表，请检查搜索条件、登录状态或页面选择器')
            batch = []
            for i in range(min(cards.count(), 100)):
                self.checkpoint()
                card = cards.nth(i)
                link = self.first(self.spec['link'], card)
                if not link:
                    continue
                href = link.get_attribute('href') or ''
                from urllib.parse import urljoin
                href = urljoin(self.page.url, href)
                ident = self.detail_id(href)
                if not ident or ident in seen:
                    continue
                self.assert_url(href)
                seen.add(ident)
                job = {'platform':self.code, 'external_id':ident, 'url':href,
                    'search_keyword':plan['keyword'],
                    'title':self.text(self.spec['title'], card) or link.inner_text().strip(),
                    'company':self.text(self.spec['company'], card),
                    'salary':self.text(self.spec['salary'], card), 'location':self.text(self.spec['location'], card)}
                if (f'{self.code}:{ident}' in plan.get('_skip_job_ids', ())
                        or job['company'] in plan.get('_skip_companies', ())):
                    continue
                batch.append(job)
            for job in batch:
                stored = plan.get('_known_jobs', {}).get(f"{self.code}:{job['external_id']}")
                if (stored and len(stored.get('description', '')) >= 30
                        and all(stored.get(k) == job.get(k) for k in ('title','company'))):
                    yield dict(job, description=stored['description'])
                    emitted += 1
                    if emitted >= plan['max_jobs']:
                        return
                    continue
                cached = self._search_cache.get(job['external_id'])
                if (cached and time.monotonic() - cached['read_at'] < 3600
                        and all(cached['job'].get(k) == job.get(k) for k in ('title','company','salary','location'))):
                    yield dict(job, description=cached['job']['description'])
                    emitted += 1
                    if emitted >= plan['max_jobs']:
                        return
                    continue
                job['description'] = self.read_search_description(job,scope)
                if len(job['description']) >= 30:
                    self._search_cache[job['external_id']] = {'read_at':time.monotonic(), 'job':dict(job)}
                yield job
                emitted += 1
                if emitted >= plan['max_jobs']:
                    return
            if not batch:
                return

    def read_search_description(self, job, scope):
        self.goto(job['url'])
        self.ensure_session(scope)
        self.verify_job(job)
        description=''
        for _ in range(15):
            self.checkpoint()
            description=self.text(self.spec['description'])
            if len(description)>=30:break
            self.page.wait_for_timeout(300)
        return description

    def diagnostic(self, message):
        """Screenshots may contain private data: local only, never uploaded."""
        try:
            directory = self.data_dir / 'diagnostics'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f'{self.code}-{int(time.time())}.png'
            self.page.screenshot(path=str(path))
            self.log(f'{message}；现场截图保存在 {path.name}', 'warning')
        except Exception:
            self.log(message, 'warning')

    def close(self):
        self.context.close()
