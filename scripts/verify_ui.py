"""Local UI acceptance, isolated data and no real platform access."""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright
from config import browser_path, ROOT
from server.app import create_app

def main():
    output=ROOT/'artifacts'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='resume-ui-') as directory:
        app=create_app(Path(directory))
        listener=socket.socket()
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
        thread.start()
        errors=[]
        try:
            for _ in range(100):
                if server.started:break
                time.sleep(.05)
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=browser_path(),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}')
                page.get_by_role('heading',name='工作台',exact=True).wait_for()
                page.locator('.topbar-right').filter(has_text='本地服务已连接').wait_for()
                page.screenshot(path=str(output/'dashboard-empty.png'),full_page=True)
                page.get_by_role('button',name='我的简历',exact=True).click()
                page.locator('input[type=file]').set_input_files({'name':'验收简历.txt','mimeType':'text/plain','buffer':'Python FastAPI SQL 后端项目经验'.encode()})
                page.get_by_role('heading',name='自动读取的简历正文',exact=True).wait_for()
                page.get_by_role('heading',name='简历自动求职',exact=True).wait_for()
                page.get_by_text('Python后端开发',exact=True).wait_for()
                assert page.get_by_role('button',name='开始按简历自动求职',exact=True).is_enabled()
                page.locator('.modal .icon-button').click()
                page.locator('.resume-card h3').filter(has_text='验收简历.txt').wait_for()
                page.get_by_role('button',name='求职方案',exact=True).click()
                page.get_by_role('button',name='新建求职方案').first.click()
                page.get_by_label('方案名称',exact=True).fill('上海 Python 后端')
                page.get_by_label('搜索岗位关键词',exact=True).fill('Python 后端')
                page.get_by_label('期望城市',exact=True).fill('上海')
                page.get_by_label('BOSS 搜索城市代码',exact=False).fill('101020100')
                page.get_by_label('技能关键词',exact=True).fill('Python, FastAPI, SQL')
                page.get_by_role('button',name='保存方案',exact=True).click()
                page.get_by_role('heading',name='上海 Python 后端').wait_for()
                page.get_by_role('button',name='工作台',exact=True).click()
                page.get_by_text('目标岗位',exact=True).wait_for()
                page.screenshot(path=str(output/'dashboard-ready.png'),full_page=True)
                for label in ['我的简历','求职方案','岗位与投递','设置']:
                    page.get_by_role('button',name=label,exact=True).click()
                    page.get_by_role('heading',name=label,exact=True).wait_for()
                    assert page.locator('body').inner_text().strip()
                page.get_by_role('button',name='工作台',exact=True).click()
                page.set_viewport_size({'width':1050,'height':850})
                page.screenshot(path=str(output/'dashboard-compact.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '页面横向溢出'
                assert not errors,errors
                browser.close()
            print('UI acceptance passed: five pages, resume import, plan save, responsive layout; no platform messages sent.')
        finally:
            server.should_exit=True
            thread.join(timeout=8)
            listener.close()

if __name__=='__main__':main()
