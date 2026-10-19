import csv
import hashlib
import io
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

import config
from core.store import Store, now, dumps
from core.runner import Runner
from services.resumes import import_resume, MAX_BYTES
from services.ai_matching import AIError, AISettings, AIJobMatcher, configured, public_settings, validate_config

class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = ''
    name: str = Field(min_length=1,max_length=100)
    keyword: str = Field(min_length=1,max_length=100)
    city: str = Field(default='',max_length=40)
    boss_city_code: str = Field(default='100010000',pattern=r'^\d{3,12}$')
    zhilian_city_code: str = Field(default='489',pattern=r'^\d{2,12}$')
    salary_min: float = Field(default=0,ge=0,le=1000)
    skills: str = Field(default='',max_length=2000)
    exclude_keywords: str = Field(default='',max_length=2000)
    min_score: int = Field(default=60,ge=0,le=100)
    max_pages: int = Field(default=1,ge=1,le=10)
    max_jobs: int = Field(default=10,ge=1,le=100)
    max_deliveries: int = Field(default=5,ge=1,le=50)
    interval_seconds: int = Field(default=60,ge=10,le=3600)
    resume_id: str = ''
    greeting: str = Field(default='',max_length=500)
    personalized_greeting: bool = True
    greeting_style: str = Field(default='resume_evidence',pattern=r'^(resume_evidence|job_interest)$')
    communication_only: bool = False
    skip_existing_contacts: bool = True
    resume_based: bool = False
    matching_mode: str=Field(default='rules',pattern=r'^(rules|ai)$')
    search_keywords: list[Annotated[str,Field(min_length=1,max_length=100)]] = Field(default_factory=list,max_length=3)
    zhilian_online_resume: bool = False

class ResumeRunBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    city: str = Field(default='厦门',min_length=1,max_length=40)
    boss_city_code: str = Field(default='101230200',pattern=r'^\d{3,12}$')
    max_contacts: int = Field(default=5,ge=1,le=5)
    matching_mode: str=Field(default='ai',pattern=r'^(rules|ai)$')

class RunBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: str
    mode: str = Field(pattern=r'^(search|auto)$')
    platforms: list[str] = Field(min_length=1,max_length=2)
    job_ids: list[str] = Field(default_factory=list,max_length=100)

class ReviewBody(BaseModel):
    sent: bool
    note: str = Field(min_length=5,max_length=1000)

class CompleteBody(BaseModel):
    note: str = Field(min_length=5,max_length=1000)

def create_app(data_dir=None, runner_factory=Runner):
    directory = Path(data_dir or config.DATA)
    for name in ('resumes','profiles','diagnostics'):
        (directory / name).mkdir(parents=True,exist_ok=True)
    store = Store(directory / 'app.sqlite3')
    token = secrets.token_urlsafe(32)
    runner = runner_factory(store,directory)

    @asynccontextmanager
    async def lifespan(app):
        yield
        runner.close()

    app = FastAPI(title='求职投递助手',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.store, app.state.runner, app.state.token = store, runner, token
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])

    @app.middleware('http')
    async def local_boundary(request:Request,call_next):
        if request.method not in {'GET','HEAD','OPTIONS'}:
            if not secrets.compare_digest(request.headers.get('x-app-token',''),token):
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':'本地请求校验失败，请刷新应用'},status_code=403)
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.url.netloc}'
            if origin and origin != expected:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':'不接受跨站请求'},status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':str(exc)},status_code=400)

    app.add_exception_handler(AIError,invalid)

    @app.get('/',response_class=HTMLResponse)
    def index():
        return (config.WEB/'index.html').read_text(encoding='utf-8').replace('__APP_TOKEN__',token)

    @app.get('/api/state')
    def state():
        return runner.snapshot()

    @app.get('/api/overview')
    def overview():
        return {
            'jobs':store.one('SELECT COUNT(*) n FROM jobs')['n'],
            'eligible':store.one('SELECT COUNT(*) n FROM jobs WHERE eligible=1')['n'],
            'delivered':store.one("SELECT COUNT(*) n FROM applications WHERE status='delivered'")['n'],
            'review':store.one("SELECT COUNT(*) n FROM actions x JOIN applications a ON a.id=x.application_id WHERE x.status='unknown' AND a.status!='delivered'")['n'],
            'events':store.all('SELECT * FROM events ORDER BY id DESC LIMIT 80'),
            'runs':store.all('SELECT id,status,error,created_at,updated_at FROM runs ORDER BY created_at DESC LIMIT 20'),
        }

    @app.get('/api/plans')
    def plans():
        return [json.loads(row['body']) for row in store.all('SELECT body FROM plans ORDER BY updated_at DESC')]

    @app.post('/api/plans')
    def save_plan(body:Plan):
        value = body.model_dump()
        ident = value['id'] or uuid.uuid4().hex
        if body.resume_id and not store.one('SELECT id FROM resumes WHERE id=?',(body.resume_id,)):
            raise ValueError('所选简历不存在')
        value['id'] = ident
        store.execute('INSERT INTO plans VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,body=excluded.body,updated_at=excluded.updated_at',
                      (ident,body.name,dumps(value),now()))
        return value

    @app.delete('/api/plans/{ident}')
    def delete_plan(ident:str):
        if runner.snapshot()['busy']:
            raise ValueError('请在任务结束后删除方案')
        store.execute('DELETE FROM plans WHERE id=?',(ident,))
        return {'ok':True}

    @app.get('/api/resumes')
    def resumes():
        rows = store.all('SELECT id,name,sha256,text,profile,created_at FROM resumes ORDER BY created_at DESC')
        from services.profile import extract_profile
        for row in rows:
            profile = json.loads(row['profile'])
            row['profile'] = profile or extract_profile(row['text'])
        return rows

    @app.post('/api/resumes')
    async def upload(file:UploadFile=File(...)):
        content = await file.read(MAX_BYTES+1)
        row = import_resume(store,directory,file.filename or '',content)
        row.pop('path',None)
        from services.profile import extract_profile
        row['profile'] = json.loads(row['profile']) or extract_profile(row['text'])
        from services.resume_search import resume_strategy
        row['strategy'] = resume_strategy(row['text'])
        store.log('导入简历：' + row['name'])
        return row

    @app.get('/api/resumes/{ident}/strategy')
    def resume_search_strategy(ident:str):
        from services.resume_search import resume_strategy
        row=store.one('SELECT text FROM resumes WHERE id=?',(ident,))
        if not row:raise HTTPException(404,'简历不存在')
        return resume_strategy(row['text'])

    @app.post('/api/resumes/{ident}/auto-run')
    def run_from_resume(ident:str,body:ResumeRunBody):
        from services.resume_search import resume_strategy
        if runner.snapshot()['busy']:raise ValueError('已有任务运行中，请先停止')
        resume=store.one('SELECT * FROM resumes WHERE id=?',(ident,))
        if not resume:raise HTTPException(404,'简历不存在')
        strategy=resume_strategy(resume['text'])
        if not strategy['ready']:raise ValueError('未识别到明确求职方向，请补充求职意向或使用手动方案')
        ai=configured(store) if body.matching_mode=='ai' else None
        plan=Plan(name='简历自动求职 · '+resume['name'][:50],keyword=strategy['keywords'][0],
            city=body.city,boss_city_code=body.boss_city_code,resume_id=ident,
            skills=','.join(strategy['skills']),min_score=ai['min_score'] if ai else 40,max_pages=1,max_jobs=5,
            max_deliveries=body.max_contacts,interval_seconds=60,communication_only=True,
            personalized_greeting=True,greeting_style='job_interest',skip_existing_contacts=True,resume_based=True,
            search_keywords=strategy['keywords'],matching_mode=body.matching_mode)
        value=save_plan(plan)
        runner.submit({'type':'run','mode':'auto','plan':value,'resume':resume,
            'platforms':['boss'],'job_ids':[],'search_keywords':strategy['keywords']})
        store.log('根据简历启动小批求职：'+'、'.join(strategy['keywords']))
        return {'ok':True,'plan_id':value['id'],'strategy':strategy}

    @app.get('/api/resumes/{ident}/file')
    def resume_file(ident:str):
        row = store.one('SELECT * FROM resumes WHERE id=?',(ident,))
        if not row:
            raise HTTPException(404,'简历不存在')
        return FileResponse(row['path'],filename=row['name'])

    @app.delete('/api/resumes/{ident}')
    def delete_resume(ident:str):
        if runner.snapshot()['busy']:
            raise ValueError('请在任务结束后删除简历')
        for row in store.all('SELECT body FROM plans'):
            if json.loads(row['body']).get('resume_id') == ident:
                raise ValueError('该简历正被求职方案使用，请先修改方案')
        if store.one('SELECT id FROM applications WHERE resume_id=?',(ident,)):
            raise ValueError('该版本已有投递记录，需要保留以供追溯')
        row = store.one('SELECT path FROM resumes WHERE id=?',(ident,))
        if row:
            Path(row['path']).unlink(missing_ok=True)
            store.execute('DELETE FROM resumes WHERE id=?',(ident,))
        return {'ok':True}

    @app.get('/api/jobs')
    def jobs():
        return store.jobs()

    @app.get('/api/applications')
    def applications():
        rows = store.all('''SELECT a.*,j.title,j.company,j.platform,j.body job FROM applications a
            JOIN jobs j ON j.id=a.job_id ORDER BY a.updated_at DESC LIMIT 1000''')
        for row in rows:
            row['url'] = json.loads(row.pop('job'))['url']
            row['actions'] = store.all('SELECT * FROM actions WHERE application_id=? ORDER BY updated_at',(row['id'],))
        return rows

    @app.get('/api/jobs/{ident}/message-preview')
    def message_preview(ident:str,plan_id:str):
        from services.greeting import personalized_greeting, job_interest_greeting
        job=store.one('SELECT body FROM jobs WHERE id=?',(ident,))
        plan=store.one('SELECT body FROM plans WHERE id=?',(plan_id,))
        if not job or not plan:
            raise HTTPException(404,'岗位或方案不存在')
        value=Plan.model_validate(json.loads(plan['body']))
        resume=store.one('SELECT text FROM resumes WHERE id=?',(value.resume_id,))
        if not resume:
            raise ValueError('请在方案中选择简历')
        if value.matching_mode=='ai':
            result=AIJobMatcher(store).evaluate(json.loads(job['body']),resume['text'])
            return dict(result,message=result['greeting'],resume_evidence=result['evidence'])
        if value.personalized_greeting:
            if value.greeting_style=='job_interest':
                return job_interest_greeting(json.loads(job['body']))
            return personalized_greeting(json.loads(job['body']),resume['text'])
        body=json.loads(job['body'])
        return {'message':value.greeting.replace('{岗位}',body['title']).replace('{公司}',body['company']),
                'method':'自定义文案','resume_evidence':[]}

    @app.post('/api/actions/{ident}/review')
    def review(ident:str,body:ReviewBody):
        if runner.snapshot()['busy']:
            raise ValueError('请先停止任务后再核验结果')
        store.review(ident,body.sent,body.note)
        store.log('人工核验动作：' + ident)
        return {'ok':True}

    @app.post('/api/applications/{ident}/complete')
    def complete_application(ident:str,body:CompleteBody):
        if runner.snapshot()['busy']:
            raise ValueError('请先停止任务后再确认投递')
        store.complete_manually(ident,body.note.strip())
        store.log('人工确认完整投递：' + body.note.strip())
        return {'ok':True}

    @app.post('/api/platforms/{code}/{operation}')
    def platform(code:str,operation:str):
        if code not in {'boss','zhilian'} or operation not in {'open','refresh','inspect'}:
            raise HTTPException(404,'平台或操作不存在')
        runner.submit({'type':operation,'platform':code})
        return {'ok':True}

    @app.post('/api/runs')
    def run(body:RunBody):
        if any(code not in {'boss','zhilian'} for code in body.platforms) or len(set(body.platforms)) != len(body.platforms):
            raise ValueError('请选择受支持且不重复的平台')
        row = store.one('SELECT body FROM plans WHERE id=?',(body.plan_id,))
        if not row:
            raise ValueError('请先保存求职方案')
        plan = Plan.model_validate(json.loads(row['body'])).model_dump()
        if plan.get('matching_mode')=='ai':configured(store)
        resume = store.one('SELECT * FROM resumes WHERE id=?',(plan['resume_id'],)) if plan['resume_id'] else None
        if plan.get('matching_mode')=='ai' and not resume:raise ValueError('大模型匹配前请先选择简历')
        if body.mode == 'auto':
            if not resume or not Path(resume['path']).is_file():
                raise ValueError('自动投递前请导入并选择简历')
            if 'zhilian' in body.platforms and not plan['zhilian_online_resume']:
                raise ValueError('请先确认智联使用平台当前在线简历，并在方案中启用')
        for ident in body.job_ids:
            job = store.one('SELECT platform FROM jobs WHERE id=?',(ident,))
            if not job or job['platform'] not in body.platforms:
                raise ValueError('选中岗位不存在或不属于当前平台')
        runner.submit({'type':'run','mode':body.mode,'plan':plan,'resume':resume,
                       'platforms':body.platforms,'job_ids':body.job_ids,
                       'search_keywords':plan.get('search_keywords',[]) if plan.get('resume_based') and not body.job_ids else []})
        store.log('启动任务：' + plan['name'] + (' · 仅搜索' if body.mode=='search' else ' · 自动投递'))
        return {'ok':True}

    @app.post('/api/runs/{ident}/retry')
    def retry_run(ident:str):
        row = store.one('SELECT * FROM runs WHERE id=?',(ident,))
        if not row or row['status'] not in {'interrupted','needs_human','error','stopped'}:
            raise ValueError('仅能恢复中断、停止或需要人工处理的任务')
        command = json.loads(row['body'])
        resume = command.get('resume')
        if resume:
            path = Path(resume['path'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != resume['sha256']:
                raise ValueError('原任务简历文件已变化，请重新创建任务')
        runner.submit(command)
        store.log('按原方案恢复任务；已成功和待核验的动作不会重复执行')
        return {'ok':True}

    @app.post('/api/control/{operation}')
    def control(operation:str):
        if operation not in {'pause','resume','stop'}:
            raise HTTPException(404)
        getattr(runner,operation)()
        return {'ok':True}

    @app.get('/api/export')
    def export():
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['平台','公司','岗位','状态','更新时间'])
        for row in applications():
            values = [row[k] for k in ('platform','company','title','status','updated_at')]
            writer.writerow(["'"+v if str(v).startswith(('=','+','-','@','\t','\r')) else v for v in values])
        return StreamingResponse(iter([output.getvalue().encode('utf-8-sig')]),media_type='text/csv',
                                 headers={'Content-Disposition':'attachment; filename="applications.csv"'})

    @app.get('/api/settings')
    def settings():
        return {'data_dir':str(directory),'matching':'大模型 / 本地规则','platforms':['boss','zhilian'],
                'ai':public_settings(store),'version':'0.2.0','resume_types':['PDF','DOCX','TXT']}

    @app.put('/api/settings/ai')
    def save_ai(body:AISettings):
        if runner.snapshot()['busy']:raise ValueError('任务运行中不能修改模型配置')
        value=body.model_dump()
        if not value['api_key']:value['api_key']=store.setting('ai',{}).get('api_key','')
        value=validate_config(value)
        store.set_setting('ai',value)
        return public_settings(store)

    @app.post('/api/settings/ai/test')
    def test_ai():
        if runner.snapshot()['busy']:raise ValueError('请在任务停止后测试模型')
        result=AIJobMatcher(store).evaluate(
            {'title':'Python开发工程师','company':'合成测试公司','location':'厦门',
             'description':'负责Python后台服务和业务接口开发，构建RAG知识库问答与工具调用系统，完成系统集成和质量验证。'},
            '求职意向：Python开发。参与Python后台服务开发，搭建RAG知识库问答系统，负责接口集成和验证。')
        return {'ok':True,'score':result['score'],'model':result['model']}

    app.mount('/static',StaticFiles(directory=config.WEB),name='static')
    return app
