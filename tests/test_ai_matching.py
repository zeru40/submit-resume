import json
import httpx
import pytest
from core.store import Store
from services.ai_matching import AIError,AIJobMatcher,redact,public_settings,validate_config

RESUME='参与Python后台服务开发，搭建RAG知识库问答系统，负责接口集成和验证。'
JOB={'title':'Python开发工程师','company':'测试公司','location':'厦门',
     'description':'负责Python后台服务和业务接口开发，构建RAG知识库问答与工具调用系统，完成系统集成和质量验证。'}

def result(**changes):
    focus='构建RAG知识库问答与工具调用系统'
    greeting=f'您好，关注到贵司的Python开发工程师岗位，职责提到{focus}。我对这个方向很感兴趣，希望了解具体业务场景、目前项目进展、技术方案和团队的分工安排，方便进一步沟通吗？'
    value=dict(score=85,suitable=True,reasons=['Python和RAG项目内容关联'],missing=['实际部署规模待确认'],
        evidence=[{'resume_quote':'搭建RAG知识库问答系统','job_quote':focus}],job_focus=focus,greeting=greeting)
    value.update(changes)
    return value

def config(store,**changes):
    value={'enabled':True,'api_url':'https://model.example/v1','api_key':'secret-test-key','model':'test-model',
           'protocol':'chat_completions','json_mode':True,'min_score':70}
    value.update(changes);store.set_setting('ai',validate_config(value))

def transport_for(value,calls):
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value,ensure_ascii=False)}}]})
    return httpx.MockTransport(respond)

def test_single_request_cache_redaction_and_configuration_changes(tmp_path):
    store=Store(tmp_path/'db.sqlite');config(store);calls=[]
    client=AIJobMatcher(store,transport_for(result(),calls))
    resume='姓名：测试人\n电话：13812345678\n邮箱：person@example.com\n'+RESUME
    first=client.evaluate(JOB,resume);second=client.evaluate(JOB,resume)
    assert len(calls)==1 and first==second
    sent=calls[0]['messages'][1]['content']
    assert '13812345678' not in sent and 'person@example.com' not in sent
    assert 'api_key' not in public_settings(store) and public_settings(store)['has_api_key']
    config(store,model='other-model');client.evaluate(JOB,resume)
    assert len(calls)==2

@pytest.mark.parametrize('status',[401,429,500])
def test_http_failure_stops_without_retry_or_error_body_leak(tmp_path,status):
    store=Store(tmp_path/'db.sqlite');config(store);calls=[]
    def fail(request):
        calls.append(True);return httpx.Response(status,text='secret-test-key upstream detail')
    with pytest.raises(AIError) as caught:AIJobMatcher(store,httpx.MockTransport(fail)).evaluate(JOB,RESUME)
    assert len(calls)==1 and 'secret-test-key' not in str(caught.value)

@pytest.mark.parametrize('change',[{'score':101},{'score':'85'},{'evidence':[{'resume_quote':'不存在的项目经历','job_quote':'构建RAG知识库问答与工具调用系统'}]},
    {'greeting':'我精通Python，拥有十年经验。'},{'greeting':'太短'},{'job_focus':'不存在的岗位内容'}])
def test_invalid_or_ungrounded_model_output_fails_closed(tmp_path,change):
    store=Store(tmp_path/'db.sqlite');config(store)
    with pytest.raises(AIError):AIJobMatcher(store,transport_for(result(**change),[])).evaluate(JOB,RESUME)
    assert not store.all('SELECT * FROM ai_assessments')

def test_responses_protocol_and_incomplete_rejection(tmp_path):
    store=Store(tmp_path/'db.sqlite');config(store,protocol='responses');calls=[]
    def reply(request):
        assert str(request.url).endswith('/responses');calls.append(json.loads(request.content))
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(result(),ensure_ascii=False)}]}]})
    assert AIJobMatcher(store,httpx.MockTransport(reply)).evaluate(JOB,RESUME)['score']==85
    assert calls[0]['text']['format']['type']=='json_object'
    assert '13812345678' not in redact('手机号13812345678')
    assert '13812345678' not in redact('联系１３８１２３４５６７８')
    assert redact('⼯具调⽤')=='工具调用'

def test_key_in_url_and_insecure_remote_endpoint_rejected():
    for url in ['http://model.example/v1','https://user:password@model.example/v1','https://model.example/v1?key=secret']:
        with pytest.raises(ValueError):validate_config({'api_url':url})
