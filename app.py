"""Desktop launcher. No software login or external service required."""
import argparse
import ctypes
import json
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import config

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser',action='store_true',help='在默认浏览器打开管理界面')
    parser.add_argument('--no-open',action='store_true',help='仅启动本地服务')
    parser.add_argument('--port',type=int,default=8188)
    args = parser.parse_args()
    config.prepare()
    handle = None
    if sys.platform == 'win32':
        import hashlib
        key = hashlib.sha256(str(config.DATA).encode()).hexdigest()[:16]
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = ctypes.windll.kernel32.CreateMutexW(None,False,'Local\\SubmitResume-' + key)
        if ctypes.windll.kernel32.GetLastError() == 183:
            try:
                endpoint = json.loads((config.DATA/'endpoint.json').read_text())
                webbrowser.open(endpoint['url'])
            except Exception:
                print('应用已启动。')
            return
    import uvicorn
    from server.app import create_app
    application = create_app()
    listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    for port in range(args.port,args.port+30):
        try:
            listener.bind(('127.0.0.1',port))
            break
        except OSError:
            continue
    else:
        raise RuntimeError('未找到可用的本地端口')
    listener.listen(128)
    url = f'http://127.0.0.1:{port}'
    (config.DATA/'endpoint.json').write_text(json.dumps({'url':url}),encoding='utf-8')
    server = uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=port,log_level='warning'))
    thread = threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError('本地服务启动失败')
            time.sleep(.1)
        if not server.started:
            raise RuntimeError('本地服务启动超时')
        print(f'求职投递助手：{url}',flush=True)
        if not args.browser and not args.no_open:
            try:
                import webview
                webview.create_window('求职投递助手',url,width=1380,height=900,min_size=(1024,700))
                webview.start()
                return
            except Exception as exc:
                print(f'桌面窗口不可用，使用浏览器：{exc}')
        if not args.no_open:
            webbrowser.open(url)
        while thread.is_alive():
            time.sleep(.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=12)
        listener.close()
        (config.DATA/'endpoint.json').unlink(missing_ok=True)
        if handle:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))

if __name__ == '__main__':
    main()
