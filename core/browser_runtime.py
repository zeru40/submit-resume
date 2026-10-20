"""Launch the normal installed Edge, then attach locally, as in the HR app.

The browser receives the real destination at process creation. No sandbox or
automation-hiding switches are added. Detaching never closes a login window.
"""
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit


def background_render_args():
    """Keep the project's Windows renderer running when its window is covered.

    These are rendering/scheduling controls; the browser sandbox, website
    security, and automation identity stay unchanged.
    """
    if os.name!='nt':return []
    return ['--disable-backgrounding-occluded-windows','--disable-renderer-backgrounding',
            '--disable-features=CalculateNativeWinOcclusion']


def _attach(playwright, endpoint, profile, process=None):
    from core.cdp_page import CdpContext
    return CdpContext(endpoint,profile)


def open_login_window(executable, profile, destination, data_dir, code, log):
    """Human login gets a normal Edge window, with no Playwright attachment."""
    if not executable:
        raise RuntimeError('请先安装 Microsoft Edge')
    profile=Path(profile).resolve()
    profile.mkdir(parents=True,exist_ok=True)
    marker=Path(data_dir)/f'browser-runtime-{code}.json'
    saved=None
    if marker.exists():
        try:
            candidate=json.loads(marker.read_text(encoding='utf-8'))
            if candidate['profile']==str(profile) and candidate['executable']==str(executable):
                with urlopen(f"http://127.0.0.1:{int(candidate['port'])}/json/version",timeout=1) as response:
                    json.load(response)
                saved=candidate
        except Exception:
            pass
    if saved:
        port=int(saved['port'])
        try:
            with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=1) as response:
                targets=json.load(response)
            expected=urlsplit(destination)
            if any(t.get('type')=='page' and
                   urlsplit(t.get('url','')).hostname==expected.hostname and
                   urlsplit(t.get('url','')).path==expected.path for t in targets):
                log('人工登录标签已存在，保留当前二维码与输入内容，不重复打开')
                return {'state':'waiting_login','message':'请在已打开的BOSS注册登录标签完成登录，再点击检查状态',
                        'account':'','mode':'manual_login'}
        except Exception:
            pass
    else:
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0))
            port=reservation.getsockname()[1]
    command=[str(executable),f'--remote-debugging-port={port}','--remote-debugging-address=127.0.0.1',
             f'--user-data-dir={profile}','--no-first-run','--no-default-browser-check',
             '--start-maximized',*background_render_args(),destination]
    process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    marker.write_text(json.dumps({'profile':str(profile),'executable':str(executable),
                                 'port':port,'pid':saved['pid'] if saved else process.pid}),encoding='utf-8')
    log('已用普通 Edge 打开人工登录窗口；登录期间不连接 Playwright，不执行页面读取或导航')
    return {'state':'waiting_login','message':'请先在普通 Edge 完成登录，再点击检查状态以连接浏览器',
            'account':'','mode':'manual_login'}


def open_edge(playwright, executable, profile, destination, data_dir, code, log):
    profile = Path(profile).resolve()
    profile.mkdir(parents=True,exist_ok=True)
    marker = Path(data_dir)/f'browser-runtime-{code}.json'
    if marker.is_file():
        try:
            saved = json.loads(marker.read_text(encoding='utf-8'))
            if saved['profile']==str(profile) and saved['executable']==str(executable):
                endpoint=f"http://127.0.0.1:{int(saved['port'])}"
                owner=_attach(playwright,endpoint,profile)
                log('已重新连接本项目的普通 Edge，保留现有登录窗口')
                return owner
        except Exception:
            log('上次浏览器连接已结束，将打开本项目独立窗口')
    if not executable:
        raise RuntimeError('普通浏览器模式需要 Microsoft Edge，请安装 Edge 后重试')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0))
        port=reservation.getsockname()[1]
    command=[str(executable),f'--remote-debugging-port={port}',
             '--remote-debugging-address=127.0.0.1',f'--user-data-dir={profile}',
             '--no-first-run','--no-default-browser-check','--start-maximized',*background_render_args(),destination]
    process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    error=None
    for _ in range(30):
        try:
            owner=_attach(playwright,f'http://127.0.0.1:{port}',profile,process)
            marker.write_text(json.dumps({'profile':str(profile),'executable':str(executable),
                                         'port':port,'pid':process.pid}),encoding='utf-8')
            log('已连接普通 Edge：启动时直接打开 BOSS/招聘平台，不创建初始空白页')
            return owner
        except Exception as exc:
            error=exc
            if process.poll() is not None:
                break
            time.sleep(.25)
    raise RuntimeError('普通 Edge 未能建立本地连接。独立配置目录可能仍被旧窗口占用；'
                       '请关闭本项目之前打开的窗口后重试。'+str(error).splitlines()[0][:120])
