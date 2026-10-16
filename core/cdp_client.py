"""Minimal local DevTools transport; read commands do not enable Runtime events."""
import itertools
import json
from urllib.parse import urlsplit
import websocket

class CdpClient:
    def __init__(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!='ws' or parsed.hostname not in {'127.0.0.1','localhost'}:
            raise ValueError('仅允许连接本机浏览器')
        self.socket=websocket.create_connection(url,timeout=20,suppress_origin=True)
        self.ids=itertools.count(1)

    def call(self,method,params=None):
        ident=next(self.ids)
        self.socket.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
        for _ in range(500):
            response=json.loads(self.socket.recv())
            if response.get('id')!=ident:
                continue
            if 'error' in response:
                raise RuntimeError(str(response['error']))
            return response.get('result',{})
        raise RuntimeError('浏览器响应超出预期')

    def evaluate(self,expression):
        result=self.call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True,'silent':True})
        if result.get('exceptionDetails'):
            detail=result['exceptionDetails']
            raise RuntimeError(detail.get('exception',{}).get('description') or detail.get('text','页面执行失败'))
        return result.get('result',{}).get('value')

    def close(self):
        self.socket.close()
