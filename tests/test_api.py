import re
import pytest
from fastapi.testclient import TestClient
from server.app import create_app

class FakeRunner:
    def __init__(self,*args):self.commands=[]
    def snapshot(self):return {'busy':False,'state':'idle','platforms':{}}
    def submit(self,command):self.commands.append(command)
    def close(self):pass

@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path,runner_factory=FakeRunner)
    with TestClient(app) as c:
        c.headers['X-App-Token']=app.state.token
        yield c

def plan(client,**extra):
    response=client.post('/api/plans',json={'name':'测试方案','keyword':'Python',**extra})
    assert response.status_code==200,response.text
    return response.json()

def test_opens_directly_without_login(client):
    response=client.get('/')
    assert response.status_code==200
    assert '__APP_TOKEN__' not in response.text
    assert '求职投递助手' in response.text
    assert client.get('/api/overview').json()['jobs']==0

def test_requires_local_token_and_rejects_cross_origin(client):
    assert client.post('/api/plans',json={},headers={'X-App-Token':'wrong'}).status_code==403
    assert client.post('/api/plans',json={},headers={'Origin':'https://external.example'}).status_code==403
    assert client.get('/',headers={'Host':'evil.example'}).status_code==400

def test_plan_validation_and_search_mode(client):
    p=plan(client)
    assert client.post('/api/runs',json={'plan_id':p['id'],'mode':'search','platforms':['boss']}).status_code==200
    assert client.post('/api/runs',json={'plan_id':p['id'],'mode':'auto','platforms':['boss']}).status_code==400
    assert client.post('/api/plans',json={'name':'p','keyword':'x','interval_seconds':0}).status_code==422

def test_resume_import_and_version_protection(client):
    response=client.post('/api/resumes',files={'file':('../简历.txt','Python 项目经历'.encode(),'text/plain')})
    assert response.status_code==200,response.text
    resume=response.json()
    assert resume['name']=='简历.txt' and 'path' not in resume
    assert 'Python' in resume['profile']['skills']
    duplicate=client.post('/api/resumes',files={'file':('other.txt','Python 项目经历'.encode(),'text/plain')})
    assert duplicate.json()['id']==resume['id']
    p=plan(client,resume_id=resume['id'])
    assert client.delete('/api/resumes/'+resume['id']).status_code==400
    assert client.post('/api/runs',json={'plan_id':p['id'],'mode':'auto','platforms':['boss']}).status_code==200
    assert client.post('/api/runs',json={'plan_id':p['id'],'mode':'auto','platforms':['zhilian']}).status_code==400

def test_rejects_empty_or_unsupported_resume(client):
    assert client.post('/api/resumes',files={'file':('resume.exe',b'code')}).status_code==400
    assert client.post('/api/resumes',files={'file':('resume.txt',b'')}).status_code==400

def test_export_works_and_unknown_platform_is_rejected(client):
    p=plan(client)
    assert client.get('/api/export').content.startswith(b'\xef\xbb\xbf')
    assert client.post('/api/runs',json={'plan_id':p['id'],'mode':'auto','platforms':['unknown']}).status_code==400
    assert client.post('/api/platforms/boss/invalid').status_code==404

def test_retry_uses_original_snapshot(client):
    from core.store import dumps,now
    store=client.app.state.store
    command={'type':'run','mode':'search','platforms':['boss'],'plan':{'name':'原始方案'},'resume':None}
    store.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',('interrupted-run',dumps(command),'interrupted','restart',now(),now()))
    assert client.post('/api/runs/interrupted-run/retry').status_code==200
    assert client.app.state.runner.commands[-1]['plan']['name']=='原始方案'
