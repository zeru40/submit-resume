"""One owning thread handles all Playwright objects; UI only submits commands."""
import json
import queue
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime

from core.control import Control, Stopped, NeedHuman
from core.store import dumps, now
from platforms.base import BeforeClickError
from platforms.boss import WaitingReply
from services.matching import match_job

class Runner:
    def __init__(self, store, data_dir, factory=None):
        self.store, self.data_dir = store, data_dir
        self.factory = factory
        from services.ai_matching import AIJobMatcher
        self.ai_matcher = AIJobMatcher(store)
        self.control = Control()
        self.commands = queue.Queue()
        self.lock = threading.RLock()
        self.platforms = {p:{'state':'disconnected','message':'尚未打开平台','account':''} for p in ('boss','zhilian')}
        self.state = {'state':'idle','message':'准备好后开始任务','run_id':'','current':'','scanned':0,'matched':0,'delivered':0,'messaged':0}
        self.pending = False
        self.closing = threading.Event()
        self.adapters = {}
        self.thread = threading.Thread(target=self._loop, name='browser-owner', daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.state)
            if self.control.pause_event.is_set():
                result['state'] = 'paused'
            result['platforms'] = deepcopy(self.platforms)
            result['busy'] = self.pending
            return result

    def publish(self, code, state):
        with self.lock:
            self.platforms[code] = state

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def submit(self, command):
        with self.lock:
            if self.pending:
                raise ValueError('已有任务正在运行，请先暂停或停止当前任务')
            self.control.reset()
            self.pending = True
            self.state.update(state='queued', message='任务已排队')
            self.commands.put(deepcopy(command))

    def pause(self):
        with self.lock:
            if not self.pending:
                raise ValueError('当前没有运行中的任务')
            self.control.pause()
        self.store.log('用户暂停任务；恢复时将重新核验页面')

    def resume(self):
        with self.lock:
            if not self.pending or not self.control.pause_event.is_set():
                raise ValueError('当前任务不在暂停状态')
            self.control.resume()

    def stop(self):
        self.control.stop()

    def close(self):
        self.closing.set()
        self.control.stop()
        self.commands.put(None)
        self.thread.join(timeout=8)

    def _adapter(self, code, playwright):
        adapter = self.adapters.get(code)
        if adapter and adapter.page.is_closed():
            if any(not p.is_closed() for p in adapter.context.pages):
                adapter.page = adapter.select_page()
            else:
                try:
                    adapter.close()
                except Exception:
                    pass
                adapter = None
        if adapter is None:
            if self.factory:
                adapter = self.factory(code, playwright, self.data_dir, self.control, self.publish, self.store.log)
            else:
                from platforms.boss import BossApplicant
                from platforms.zhilian import ZhilianApplicant
                adapter = {'boss':BossApplicant,'zhilian':ZhilianApplicant}[code](playwright, self.data_dir, self.control, self.publish, self.store.log)
            self.adapters[code] = adapter
        if not self.factory:
            # Selector fixes can be applied without interrupting a logged-in browser.
            import importlib
            from platforms import specs
            adapter.spec=importlib.reload(specs).SPECS[code]
        return adapter

    def _loop(self):
        playwright = None
        manager = None
        try:
            while not self.closing.is_set():
                try:
                    command = self.commands.get(timeout=.5)
                except queue.Empty:
                    # Pump browser events on the same thread, even while idle.
                    for code, adapter in list(self.adapters.items()):
                        try:
                            if not adapter.page.is_closed():
                                adapter.page.wait_for_timeout(10)
                        except Exception:
                            self.publish(code, {'state':'closed','message':'浏览器连接已关闭','account':''})
                    continue
                if command is None:
                    break
                run_id = ''
                try:
                    if command['type']=='open' and not self.factory:
                        # A login window must not be instrumented while the user
                        # is authenticating. Connect only on refresh/run/inspect.
                        for existing in self.adapters.values():
                            existing.close()
                        self.adapters.clear()
                        if manager:
                            manager.stop()
                            manager=None
                            playwright=None
                            self.adapters.clear()
                        from core.browser_runtime import open_login_window
                        from config import browser_path
                        from platforms.specs import SPECS
                        code=command['platform']
                        spec=SPECS[code]
                        state=open_login_window(browser_path(),self.data_dir/'profiles'/code,
                                                spec.get('login',spec['home']),self.data_dir,code,self.store.log)
                        self.publish(code,state)
                        self.update(state='idle',message='等待人工登录，浏览器尚未接管')
                        continue
                    if not playwright and not self.factory:
                        # The live runtime uses on-demand CDP, not full Playwright
                        # instrumentation. Playwright remains for isolated tests.
                        playwright = object()
                    if command['type'] in {'open','refresh','inspect'}:
                        adapter = self._adapter(command['platform'], playwright)
                        if command['type']=='inspect':
                            filename = adapter.inspect()
                            self.store.log('本地页面诊断已保存：' + filename)
                        self.publish(command['platform'], adapter.open() if command['type']=='open' else adapter.session())
                        self.update(state='idle',message='平台状态已更新')
                    else:
                        run_id = uuid.uuid4().hex
                        stamp = now()
                        self.store.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (run_id,dumps(command),'running','',stamp,stamp))
                        self.update(state='running',greeting_target=command['plan'].get('max_greetings',command['plan']['max_deliveries']),run_id=run_id,current='',scanned=0,matched=0,delivered=0,messaged=0,search_keyword='',keyword_index=0,keyword_total=0,message='正在执行')
                        self._run(command, playwright)
                        waiting=self.store.one("SELECT COUNT(*) AS n FROM applications WHERE status='waiting_reply'")['n']
                        if command['plan'].get('communication_only'):
                            message=f"本轮已核验发送 {self.snapshot()['messaged']} 条岗位沟通文案；已沟通过的自动跳过"
                        else:
                            message=f'本轮处理完成；{waiting} 个会话等待HR回复后发简历' if waiting else '本轮任务完成'
                        self.update(state='completed',message=message)
                        self.store.execute("UPDATE runs SET status='completed',updated_at=? WHERE id=?", (now(),run_id))
                except Stopped:
                    self._end(run_id, 'stopped', '任务已停止')
                except NeedHuman as exc:
                    self._end(run_id, 'needs_human', str(exc))
                    for adapter in self.adapters.values():
                        adapter.diagnostic(str(exc))
                except Exception as exc:
                    message = str(exc).split('Browser logs:')[0][:800]
                    self._end(run_id, 'error', message)
                    # Drop a broken transport; keep the ordinary Edge window and
                    # all reserved action results. A later task reconnects once.
                    for adapter in self.adapters.values():
                        try:adapter.close()
                        except Exception:pass
                    self.adapters.clear()
                finally:
                    with self.lock:
                        self.pending = False
                        self.control.pause_event.clear()
        finally:
            for adapter in self.adapters.values():
                try:
                    adapter.close()
                except Exception:
                    pass
            if manager:
                manager.stop()

    def _end(self, run_id, status, message):
        self.update(state=status,message=message)
        self.store.log(message, 'warning' if status != 'error' else 'error')
        if run_id:
            self.store.execute('UPDATE runs SET status=?,error=?,updated_at=? WHERE id=?', (status,message,now(),run_id))

    def _run(self, command, playwright):
        command['plan'].setdefault('max_greetings',command['plan']['max_deliveries'])
        if command['plan'].get('communication_only'):
            command['plan']['max_deliveries']=max(command['plan']['max_deliveries'],command['plan']['max_greetings'])
        if command['plan'].get('matching_mode')=='ai':
            from services.ai_matching import AIError,configured
            configured(self.store)
            if not command.get('resume'):raise AIError('大模型匹配需要先选择简历，已停止')
        keywords=command.get('search_keywords',[])
        if not keywords:
            return self._run_single(command,playwright)
        # One finite session: search a small batch, greet matches, then move on.
        # Any login, restriction, navigation or send error exits this whole loop.
        command.setdefault('account_scopes',{})
        attempted=0
        maximum=command['plan']['max_deliveries']
        for index,keyword in enumerate(keywords[:3]):
            self.control.checkpoint()
            if attempted>=maximum or self._greeting_limit(command['plan']):break
            self.update(search_keyword=keyword,keyword_index=index+1,keyword_total=min(len(keywords),3))
            child=dict(command,search_keywords=[],job_ids=[],plan=dict(command['plan'],keyword=keyword,
                max_deliveries=maximum-attempted))
            attempted+=self._run_single(child,playwright,root_command=command)
            command['keywords_completed']=index+1
            if index+1<len(keywords[:3]) and attempted<maximum and not self._greeting_limit(command['plan']):
                self.control.wait(60)

    def _greeting_limit(self, plan):
        return self.snapshot()['messaged'] >= plan.get('max_greetings',plan.get('max_deliveries',5))

    def _assess(self, job, plan, resume_text):
        job.pop('ai_assessment',None)
        if plan.get('matching_mode')!='ai':return match_job(job,plan,resume_text)
        # Keep explicit city/salary/exclusion gates, let the model judge semantic
        # relevance rather than silently substituting a local keyword score.
        gate_plan=dict(plan,skills='',resume_based=False,greeting_style='job_interest',min_score=0)
        _,reasons,allowed=match_job(job,gate_plan,resume_text)
        if not allowed:return 0,reasons,False
        self.control.checkpoint()
        self.update(message='大模型正在评估简历与岗位并生成招呼')
        result=self.ai_matcher.evaluate(job,resume_text)
        self.control.checkpoint()
        job['ai_assessment']=result
        reasons=['大模型评分：'+str(result['score'])]+result['reasons']+['待确认：'+x for x in result['missing']]
        return result['score'],reasons,result['suitable'] and result['score']>=plan.get('min_score',70)

    def _run_single(self, command, playwright, root_command=None):
        plan = command['plan']
        resume = command.get('resume')
        delivery_count = self.snapshot()['delivered']
        processed_count = 0
        for code in command['platforms']:
            self.control.checkpoint()
            adapter = self._adapter(code, playwright)
            interval_seconds = max(plan.get('interval_seconds', 60), 60 if code == 'boss' else 0)
            if adapter.page.url == 'about:blank':
                adapter.open()
            scope = adapter.ensure_session()
            expected_scope = command.get('account_scopes',{}).get(code)
            if expected_scope and expected_scope != scope:
                raise NeedHuman('恢复任务时平台账号发生变化，请切回原求职账号或启动新任务')
            command.setdefault('account_scopes',{})[code] = scope
            existing_companies=set()
            if command['mode']!='search' and code=='boss' and plan.get('skip_existing_contacts'):
                existing_companies=adapter.existing_companies(scope)
            self.store.execute('UPDATE runs SET body=?,updated_at=? WHERE id=?',
                               (dumps(root_command or command),now(),self.snapshot()['run_id']))
            if command.get('job_ids'):
                jobs = []
                for ident in command['job_ids']:
                    row = self.store.one('SELECT * FROM jobs WHERE id=? AND platform=?', (ident, code))
                    if row:
                        jobs.append(json.loads(row['body']))
            else:
                # Materialize discovery before applying: applying navigates away from the list.
                jobs = []
                search_plan = dict(plan)
                if code == 'boss':
                    search_plan['_known_jobs'] = {row['id']:json.loads(row['body']) for row in
                        self.store.all("SELECT id,body FROM jobs WHERE platform='boss'")}
                if code == 'boss' and plan.get('skip_existing_contacts'):
                    protected = self.store.all('''SELECT DISTINCT a.job_id,j.company FROM applications a
                        JOIN jobs j ON j.id=a.job_id WHERE a.scope=? AND j.platform='boss'
                        AND (a.status IN ('message_sent','needs_review','delivered','waiting_reply','previously_contacted',
                            'unavailable','withdrawn','retracted')
                        OR EXISTS (SELECT 1 FROM actions x WHERE x.application_id=a.id
                            AND x.status IN ('sent','pending','unknown')))''', (scope,))
                    search_plan['_skip_job_ids'] = {row['job_id'] for row in protected}
                    search_plan['_skip_companies'] = existing_companies | {row['company'] for row in protected}
                for job in adapter.search(search_plan):
                    score, reasons, eligible = self._assess(job, plan, resume['text'] if resume else '')
                    self.store.upsert_job(job,score,reasons,eligible)
                    jobs.append(job)
                    self.update(scanned=self.snapshot()['scanned']+1,current=job['title'],message='正在读取岗位详情')
                    self.store.log(f"{adapter.spec['label']} · {job['title']} · 匹配 {score} 分")
            if command['mode'] == 'search':
                continue
            for job in jobs:
                self.control.checkpoint(adapter.page.wait_for_timeout)
                if self._greeting_limit(plan):return processed_count
                if code=='boss' and plan.get('skip_existing_contacts'):
                    protected=self.store.one('''SELECT a.id FROM applications a JOIN jobs j ON j.id=a.job_id
                        WHERE a.scope=? AND j.platform='boss' AND (a.job_id=? OR j.company=?)
                        AND (a.status IN ('message_sent','needs_review','delivered','waiting_reply','previously_contacted','unavailable','withdrawn','retracted')
                        OR EXISTS (SELECT 1 FROM actions x WHERE x.application_id=a.id AND x.status IN ('sent','pending','unknown'))) LIMIT 1''',
                        (scope,f"{code}:{job['external_id']}",job['company']))
                    if protected or job['company'] in existing_companies:continue
                score, reasons, eligible = self._assess(job, plan, resume['text'] if resume else '')
                job_id = self.store.upsert_job(job,score,reasons,eligible)
                if not eligible:
                    continue
                self.update(current=f"{job['company']} · {job['title']}", matched=self.snapshot()['matched']+1)
                if processed_count >= plan['max_deliveries']:
                    return processed_count
                if code=='boss' and plan.get('skip_existing_contacts'):
                    prior=self.store.one('''SELECT x.id FROM actions x JOIN applications a ON a.id=x.application_id
                        JOIN jobs j ON j.id=a.job_id WHERE a.scope=? AND j.platform='boss'
                        AND (a.job_id=? OR j.company=?) AND x.kind IN ('contact','greeting')
                        AND x.status IN ('sent','pending','unknown') LIMIT 1''',(scope,job_id,job['company']))
                    if prior or job['company'] in existing_companies:
                        self.store.log('跳过已沟通过的公司/岗位：'+job['company']+' · '+job['title'])
                        continue
                application = self.store.application(job_id,scope,resume['id'] if resume else '',plan['id'])
                if application['resume_id'] != (resume['id'] if resume else ''):
                    self.store.log('该岗位已有其他简历版本的申请记录，请在投递记录中核验', 'warning')
                    continue
                if application['status'] == 'delivered':
                    continue
                if application['status'] == 'needs_review':
                    self.store.log('跳过结果待核验的岗位：' + job['title'], 'warning')
                    continue
                if plan.get('skip_existing_contacts') and application['status'] in {'unavailable','withdrawn','retracted','previously_contacted','message_sent'}:
                    self.store.log('跳过已处理的岗位：' + job['title'])
                    continue
                known_contact=self.store.one("SELECT status FROM actions WHERE application_id=? AND kind='contact'",(application['id'],))
                if code!='boss' or not known_contact or known_contact['status']!='sent':
                    navigator=getattr(adapter,'navigate_job',None)
                    if navigator:
                        try:
                            navigator(job,plan)
                        except BeforeClickError as exc:
                            self.store.application_status(application['id'],'unavailable')
                            self.store.log('跳过暂时无法定位的岗位：'+job['company']+' · '+str(exc),'warning')
                            if plan.get('resume_based') or plan.get('matching_mode')=='ai':raise
                            continue
                    else:
                        adapter.goto(job['url'])
                    adapter.ensure_session(scope)
                    adapter.verify_job(job)
                if code == 'boss':
                    if plan.get('skip_existing_contacts') and adapter.already_contacted():
                        self.store.application_status(application['id'],'previously_contacted')
                        self.store.log('平台标记已沟通，跳过：'+job['company']+' · '+job['title'])
                        existing_companies.add(job['company'])
                        continue
                    composition=None
                    if plan.get('matching_mode')=='ai':
                        result=job['ai_assessment']
                        composition={'message':result['greeting'],'method':result['method'],
                            'job_requirement':result['job_focus'],'resume_evidence':result['evidence'],'ai_score':result['score']}
                    elif plan.get('personalized_greeting',False):
                        from services.greeting import personalized_greeting, job_interest_greeting
                        try:
                            composition=(job_interest_greeting(job) if plan.get('greeting_style')=='job_interest'
                                         else personalized_greeting(job,resume['text']))
                        except ValueError as exc:
                            raise BeforeClickError(str(exc)) from exc
                    if plan.get('communication_only') and not composition and not plan.get('greeting'):
                        raise BeforeClickError('仅沟通模式需要自动生成或填写沟通文案，未发起联系')
                    prepare=getattr(adapter,'prepare_resume',None)
                    if prepare and not plan.get('communication_only') and (not known_contact or known_contact['status']!='sent'):
                        self._action(adapter,application,'upload',lambda:prepare(resume,scope))
                        navigator=getattr(adapter,'navigate_job',None)
                        if navigator:
                            navigator(job,plan)
                        else:
                            adapter.goto(job['url'])
                        adapter.ensure_session(scope)
                        adapter.verify_job(job)
                    try:
                        receipt = self._action(adapter, application, 'contact', lambda: adapter.contact(job,scope))
                    except NeedHuman as exc:
                        if plan.get('resume_based') or plan.get('matching_mode')=='ai':raise
                        if not (plan.get('communication_only') and plan.get('skip_existing_contacts')):
                            raise
                        # Quarantine this reserved contact without retrying it.
                        # Only continue if the same account is still usable.
                        adapter.ensure_session(scope)
                        self.store.log('该岗位沟通结果待核验，本轮不重试：'+job['company']+' · '+str(exc),'warning')
                        existing_companies.add(job['company'])
                        processed_count += 1
                        self.control.wait(interval_seconds,adapter.page.wait_for_timeout)
                        continue
                    # Revisit the exact conversation on restart; never click contact again.
                    adapter.open_chat(receipt,scope)
                    if composition or plan.get('greeting'):
                        message = composition['message'] if composition else plan['greeting'].replace('{岗位}',job['title']).replace('{公司}',job['company'])
                        def send_greeting():
                            result=adapter.greet(message,receipt,scope)
                            if composition:
                                result['composition']=composition
                            return result
                        self._action(adapter,application,'greeting',send_greeting)
                        self.update(messaged=self.snapshot()['messaged']+1)
                    if plan.get('communication_only'):
                        self.store.application_status(application['id'],'message_sent')
                        processed_count += 1
                        existing_companies.add(job['company'])
                        self.update(message='已核验岗位沟通文案发送：'+job['company'])
                        self.store.log('已发送岗位沟通文案：'+job['company']+' · '+job['title'])
                        if self._greeting_limit(plan):return processed_count
                        self.control.wait(interval_seconds,adapter.page.wait_for_timeout)
                        continue
                    try:
                        self._action(adapter,application,'resume',lambda:adapter.send_resume(resume,receipt,scope))
                    except WaitingReply as exc:
                        self.store.application_status(application['id'],'waiting_reply')
                        self.store.log(str(exc))
                        self.update(message=str(exc))
                        # Count this contacted opportunity against the per-run cap.
                        # A later run reuses contact/message receipts and only resumes
                        # the failed-before-click attachment stage.
                        processed_count += 1
                        continue
                else:
                    self._action(adapter,application,'apply',lambda:adapter.apply(job,None,scope))
                self.store.application_status(application['id'],'delivered')
                delivery_count += 1
                processed_count += 1
                self.update(delivered=delivery_count,message='已获得平台结果回执')
                if self._greeting_limit(plan):return processed_count
                self.store.log('已核验投递：' + job['title'])
                self.control.wait(interval_seconds,adapter.page.wait_for_timeout)

        return processed_count

    def _action(self, adapter, application, kind, callback):
        existing = self.store.one('SELECT * FROM actions WHERE application_id=? AND kind=?', (application['id'],kind))
        if existing and existing['status'] == 'sent':
            try:
                receipt = json.loads(existing['receipt'])
            except (ValueError,TypeError):
                raise NeedHuman('人工确认的动作缺少后续所需的会话信息，请在平台完成后续操作')
            return receipt
        if kind in {'contact','apply'}:
            # Across runs and restarts: old short-interval plans cannot start a
            # second BOSS conversation within one minute of the last attempt.
            job = self.store.one('SELECT platform FROM jobs WHERE id=?', (application['job_id'],))
            if job and job['platform'] == 'boss':
                previous = self.store.one('''SELECT x.updated_at FROM actions x
                    JOIN applications a ON a.id=x.application_id JOIN jobs j ON j.id=a.job_id
                    WHERE j.platform='boss' AND x.kind IN ('contact','apply')
                    AND x.status IN ('sent','unknown','pending') ORDER BY x.updated_at DESC LIMIT 1''')
                if previous:
                    delay = max(0, 60 - (time.time() - datetime.fromisoformat(previous['updated_at']).timestamp()))
                    if delay:
                        self.update(message='等待主动沟通间隔（至少60秒）')
                        self.control.wait(delay, adapter.page.wait_for_timeout)
        token = self.store.reserve(application['id'],kind)
        if not token:
            raise NeedHuman('该动作已有执行记录且结果待核验，请先检查平台结果')
        adapter.external_started = False
        try:
            result = callback()
            self.store.outcome(token,'sent',dumps(result))
            return result
        except BaseException as exc:
            status = 'failed' if isinstance(exc,BeforeClickError) and not adapter.external_started else 'unknown'
            self.store.outcome(token,status,error=str(exc))
            self.store.application_status(application['id'],'ready' if status=='failed' else 'needs_review')
            raise
