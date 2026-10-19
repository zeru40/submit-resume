import socket
import subprocess
import time
from urllib.parse import quote
import pytest
from config import browser_path
from core.cdp_page import CdpContext

@pytest.fixture(scope='module')
def context(tmp_path_factory):
    executable=browser_path()
    if not executable:pytest.skip('需要本机Edge')
    profile=tmp_path_factory.mktemp('cdp-isolated')
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    process=subprocess.Popen([executable,'--headless=new',f'--remote-debugging-port={port}',f'--user-data-dir={profile}',
                              '--no-first-run','--no-default-browser-check','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    ctx=None
    try:
        for _ in range(50):
            try:ctx=CdpContext(f'http://127.0.0.1:{port}',profile);break
            except Exception:time.sleep(.1)
        assert ctx,'本地隔离浏览器未启动'
        yield ctx
    finally:
        if ctx:
            try:ctx.browser_client.call('Browser.close')
            except Exception:pass
            ctx.close()
        if process.poll() is None:process.terminate()

def test_dom_locator_fill_click_and_visibility(context):
    page=context.pages[0]
    html='''<meta charset="utf-8"><div class="card"><b>岗位</b><span hidden>隐藏</span></div>
    <textarea id="message"></textarea><button onclick="document.querySelector('#result').innerText=document.querySelector('textarea').value">发送</button><p id="result"></p>'''
    page.goto('data:text/html;charset=utf-8,'+quote(html))
    assert page.locator('.card').locator('b').inner_text()=='岗位'
    assert not page.locator('[hidden]').is_visible()
    page.locator('#message').fill('测试消息，不发送到任何网站')
    assert page.locator('#message').input_value()=='测试消息，不发送到任何网站'
    page.locator('button:has-text("发送")').click()
    assert page.locator('#result').inner_text()=='测试消息，不发送到任何网站'
    assert page.get_by_text('岗位',exact=True).count()==1

def test_repeated_reads_do_not_reload_document(context):
    page=context.pages[0]
    first=page.evaluate('performance.timeOrigin')
    for _ in range(5):
        assert page.locator('.card').count()==1
        assert page.evaluate('performance.timeOrigin')==first


def test_minimized_window_is_restored_before_page_input():
    from types import SimpleNamespace
    from core.cdp_page import CdpPage
    calls=[];restored=[False]
    def browser_call(method,params):
        calls.append(method)
        if method=='Browser.getWindowForTarget':return {'windowId':12,'bounds':{'windowState':'minimized'}}
        assert params=={'windowId':12,'bounds':{'windowState':'normal'}}
        restored[0]=True
    page=object.__new__(CdpPage);page.ident='task'
    page.context=SimpleNamespace(browser_client=SimpleNamespace(call=browser_call))
    page.client=SimpleNamespace(call=lambda method:calls.append(method))
    page.evaluate=lambda _: 'visible' if restored[0] else 'hidden'
    page.bring_to_front()
    assert calls==['Browser.getWindowForTarget','Browser.setWindowBounds','Page.bringToFront']


def test_hidden_page_refuses_input_instead_of_silent_click():
    from types import SimpleNamespace
    from core.cdp_page import CdpPage
    calls=[];page=object.__new__(CdpPage);page.ident='task'
    page.context=SimpleNamespace(browser_client=SimpleNamespace(call=lambda *args:{'windowId':12,'bounds':{'windowState':'normal'}}))
    page.client=SimpleNamespace(call=lambda method:calls.append(method))
    page.evaluate=lambda _:'hidden';page.wait_for_timeout=lambda _:None
    with pytest.raises(RuntimeError,match='不可见'):page.bring_to_front()
    assert calls==['Page.bringToFront']
