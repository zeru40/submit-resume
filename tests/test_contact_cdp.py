"""Exercise the production CDP transport against a localhost-only fake platform."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from test_cdp_page import context
from core.control import Control, NeedHuman
from platforms.boss import BossApplicant
from platforms.specs import SPECS


@pytest.mark.parametrize('mode',['success','inert'])
def test_real_cdp_contact_and_greeting_are_single_shot(context,tmp_path,mode):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            requests.append(self.path.split('?')[0])
            root=f'http://127.0.0.1:{self.server.server_port}'
            account='<div class="nav-figure"><span class="label-name">模拟求职者</span></div>'
            if self.path.startswith('/web/geek/chat'):
                body=account+f'''<div class="friend-content selected"><img src="{root}/avatar.png"><div class="name-box">
                <span class="name-text">测试HR</span><span>模拟企业</span></div></div>
                <div class="chat-conversation"><span class="position-name">AI开发</span></div>
                <textarea id="chat-input"></textarea><button class="btn-send" onclick="if(event.isTrusted){{const d=document.createElement('div');d.className='message-item sent';d.textContent=document.querySelector('textarea').value;document.body.append(d);document.querySelector('textarea').value='';}}">发送</button>'''
            else:
                navigate="location.href='/web/geek/chat?bossId=hr1'" if mode=='success' else ''
                body=account+f'''<script>localStorage.setItem('contact_clicks','0')</script>
                <div class="job-detail-container"><div class="job-detail-header"><span class="job-name">AI开发</span></div>
                <a class="op-btn-chat" ka="chat_job1" href="javascript:;" onclick="if(event.isTrusted){{localStorage.setItem('contact_clicks',String(Number(localStorage.getItem('contact_clicks'))+1));{navigate}}}">立即沟通</a></div>
                <div class="job-boss-info"><h2 class="name">测试HR<span>在线</span></h2><img src="{root}/avatar.png"></div>'''
            if self.path=='/avatar.png':
                self.send_response(204);self.end_headers();return
            data=('<meta charset="utf-8">'+body).encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    a=object.__new__(BossApplicant)
    a.code='boss';a.spec=dict(SPECS['boss'],hosts=['127.0.0.1'])
    a.context=context;a.page=context.new_page();a.data_dir=a.profile=tmp_path
    a.control=Control();a.publish=a.log=lambda *args:None;a.external_started=False;a.scope=''
    a.assert_url=lambda _:None # Only this isolated localhost fixture accepts HTTP.
    original_wait=a.page.wait_for_timeout
    try:
        a.page.goto(f'http://127.0.0.1:{server.server_port}/web/geek/jobs')
        scope=a.ensure_session();job={'external_id':'job1','title':'AI开发','company':'模拟企业'}
        if mode=='success':
            receipt=a.contact(job,scope)
            assert receipt['identity']['name']=='测试HR'
            a.open_chat(receipt,scope)
            a.greet('仅发送到本地模拟页面的招呼。',receipt,scope)
            assert a.outbound()==['仅发送到本地模拟页面的招呼。']
            assert requests.count('/web/geek/chat')==1
        else:
            a.page.wait_for_timeout=lambda _:None
            with pytest.raises(NeedHuman,match='未取得完整回执'):a.contact(job,scope)
            assert '/jobs' in a.page.url and '/web/geek/chat' not in requests
            assert not a.outbound()
        assert a.page.evaluate("localStorage.getItem('contact_clicks')")=='1'
    finally:
        a.page.wait_for_timeout=original_wait
        a.page.close();server.shutdown();server.server_close();worker.join(timeout=2)
