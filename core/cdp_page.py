"""Small DOM/input facade over on-demand CDP, without Runtime.enable listeners."""
import base64
import json
import time
from pathlib import Path
from urllib.request import urlopen

from core.cdp_client import CdpClient

SELECT = r'''
let nodes=[document];
const visible=e=>!!e && !!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
for(const step of STEPS){
  if('nth' in step){nodes=nodes[step.nth]?[nodes[step.nth]]:[];continue;}
  if('text' in step){
    const all=nodes.flatMap(n=>Array.from(n.querySelectorAll('*')));
    const matches=e=>step.exact?(e.innerText||'').trim()===step.text:(e.innerText||'').includes(step.text);
    nodes=all.filter(e=>visible(e)&&matches(e)&&!Array.from(e.children).some(c=>visible(c)&&matches(c)));
    continue;
  }
  let selector=step.css;
  const wantVisible=selector.includes(':visible');selector=selector.replaceAll(':visible','');
  const match=selector.match(/:has-text\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\)/);
  let text=null;
  if(match){text=match[1].slice(1,-1);selector=selector.replace(match[0],'');}
  nodes=nodes.flatMap(n=>Array.from(n.querySelectorAll(selector||'*')))
    .filter(e=>(!wantVisible||visible(e))&&(text===null||(e.innerText||'').includes(text)));
}
'''

class CdpLocator:
    def __init__(self,page,steps):self.page,self.steps=page,steps
    def _expression(self,body):
        return '(()=>{'+SELECT.replace('STEPS',json.dumps(self.steps,ensure_ascii=False))+body+'})()'
    def _read(self,body):return self.page.client.evaluate(self._expression(body))
    def locator(self,selector):return CdpLocator(self.page,self.steps+[{'css':selector}])
    def nth(self,index):return CdpLocator(self.page,self.steps+[{'nth':index}])
    @property
    def first(self):return self.nth(0)
    def count(self):return self._read('return nodes.length;')
    def is_visible(self):return self._read('return visible(nodes[0]);')
    def inner_text(self,**kwargs):return self._read("return nodes[0]?.innerText||'';")
    def all_inner_texts(self):return self._read("return nodes.map(e=>e.innerText||'');")
    def get_attribute(self,name):return self._read(f'return nodes[0]?.getAttribute({json.dumps(name)})??null;')
    def input_value(self):return self._read("return nodes[0]?.value||'';")
    def evaluate(self,function):return self._read('if(!nodes[0])throw Error("元素已不存在");return ('+function+')(nodes[0]);')
    def get_by_text(self,text,exact=False):return CdpLocator(self.page,self.steps+[{'text':text,'exact':exact}])

    def click(self):
        self.page.bring_to_front()
        self._read("const e=nodes[0];if(!e||!visible(e)||e.disabled)throw Error('元素不可点击');e.scrollIntoView({block:'center',inline:'center',behavior:'instant'});return true;")
        self.page.wait_for_timeout(80)
        for attempt in range(10):
            try:
                point=self._read('''const e=nodes[0];if(!e||!visible(e)||e.disabled)throw Error('元素不可点击');
                    const r=e.getBoundingClientRect();
                    const x=r.left+r.width/2,y=r.top+r.height/2,hit=document.elementFromPoint(x,y);
                    if(!hit||!(e===hit||e.contains(hit)))throw Error('操作入口被其他元素遮挡');return {x,y};''')
                break
            except RuntimeError as exc:
                if '被其他元素遮挡' not in str(exc) or attempt==9:
                    raise
                self.page.wait_for_timeout(100)
        self.page.client.call('Input.dispatchMouseEvent',{'type':'mouseMoved',**point,'button':'none','buttons':0})
        # Hover can change a sticky header or reveal a floating toolbar. Verify
        # the actual target again after that layout settles, before pressing.
        self.page.wait_for_timeout(100)
        point=self._read('''const e=nodes[0];if(!e||!visible(e)||e.disabled)throw Error('元素不可点击');
            const r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
            const hit=document.elementFromPoint(x,y);
            if(!hit||!(e===hit||e.contains(hit)))throw Error('操作入口被其他元素遮挡');return {x,y};''')
        self.page.client.call('Input.dispatchMouseEvent',{'type':'mouseMoved',**point,'button':'none','buttons':0})
        for kind,buttons in (('mousePressed',1),('mouseReleased',0)):
            self.page.client.call('Input.dispatchMouseEvent',{'type':kind,**point,'button':'left','buttons':buttons,'clickCount':1})

    def fill(self,value):
        self.page.bring_to_front()
        self._read('''const e=nodes[0];if(!e||!visible(e))throw Error('输入框不可用');e.focus();
            if(e.setSelectionRange){e.setSelectionRange(0,e.value.length);}
            else if(e.isContentEditable){const r=document.createRange();r.selectNodeContents(e);const s=getSelection();s.removeAllRanges();s.addRange(r);}
            else throw Error('不是可编辑输入框');return true;''')
        self.page.client.call('Input.insertText',{'text':str(value)})

    def set_input_files(self,payload):
        if not isinstance(payload,dict):raise ValueError('仅支持指定的本地简历文件')
        import hashlib
        filename=Path(payload['name']).name
        content=payload['buffer']
        directory=self.page.context.profile.parent.parent/'upload-staging'/hashlib.sha256(content).hexdigest()
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/filename;path.write_bytes(content)
        remote=self.page.client.call('Runtime.evaluate',{'expression':self._expression('return nodes[0];'),'returnByValue':False})
        object_id=remote.get('result',{}).get('objectId')
        if not object_id:raise RuntimeError('没有找到文件上传控件')
        self.page.client.call('DOM.setFileInputFiles',{'files':[str(path.resolve())],'objectId':object_id})


class CdpPage:
    def __init__(self,context,target):
        self.context,self.ident=context,target['id']
        self.last_url=target['url']
        self.client=CdpClient(target['webSocketDebuggerUrl'])
    @property
    def url(self):
        row=next((t for t in self.context.targets() if t['id']==self.ident),None)
        if row:self.last_url=row['url']
        return self.last_url
    def is_closed(self):return not any(t['id']==self.ident for t in self.context.targets())
    def set_default_timeout(self,_):pass
    def wait_for_timeout(self,ms):time.sleep(ms/1000)
    def locator(self,selector):return CdpLocator(self,[{'css':selector}])
    def get_by_text(self,text,exact=False):return CdpLocator(self,[{'text':text,'exact':exact}])
    def evaluate(self,expression):
        expression=expression.strip()
        if expression.startswith(('() =>','()=>','function')):expression='('+expression+')()'
        return self.client.evaluate(expression)
    def goto(self,url,wait_until='commit',timeout=20000):
        self.bring_to_front()
        try:previous=self.evaluate('performance.timeOrigin')
        except Exception:previous=None
        result=self.client.call('Page.navigate',{'url':url})
        if result.get('errorText'):raise RuntimeError(result['errorText'])
        deadline=time.monotonic()+timeout/1000
        while time.monotonic()<deadline:
            try:
                state=self.evaluate("({ready:document.readyState!=='loading'&&Boolean(document.body),origin:performance.timeOrigin})")
                if state['ready'] and (state['origin']!=previous or 'loaderId' not in result):return
            except Exception:pass
            time.sleep(.15)
        raise RuntimeError('页面加载超时，请检查网络或在浏览器查看提示')
    def bring_to_front(self):
        window=self.context.browser_client.call('Browser.getWindowForTarget',{'targetId':self.ident})
        if window.get('bounds',{}).get('windowState')=='minimized':
            self.context.browser_client.call('Browser.setWindowBounds',
                {'windowId':window['windowId'],'bounds':{'windowState':'normal'}})
        self.client.call('Page.bringToFront')
        for _ in range(20):
            if self.evaluate('document.visibilityState')=='visible':return
            self.wait_for_timeout(100)
        raise RuntimeError('平台浏览器仍不可见，未继续页面输入；请恢复项目的 Edge 窗口')
    def close(self):
        self.context.browser_client.call('Target.closeTarget',{'targetId':self.ident})
        self.client.close()
    def screenshot(self,path,**kwargs):
        result=self.client.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})
        Path(path).write_bytes(base64.b64decode(result['data']))


class CdpContext:
    def __init__(self,endpoint,profile):
        self.endpoint,self.profile=endpoint,Path(profile)
        self.cache={}
        with urlopen(endpoint+'/json/version',timeout=2) as response:version=json.load(response)
        self.browser_client=CdpClient(version['webSocketDebuggerUrl'])
    def targets(self):
        with urlopen(self.endpoint+'/json/list',timeout=2) as response:
            return [t for t in json.load(response) if t.get('type')=='page']
    def refresh(self):return self.targets()
    @property
    def pages(self):
        result=[]
        for target in self.targets():
            if target['id'] not in self.cache:self.cache[target['id']]=CdpPage(self,target)
            result.append(self.cache[target['id']])
        return result
    def new_page(self):
        result=self.browser_client.call('Target.createTarget',{'url':'about:blank'})
        return next(p for p in self.pages if p.ident==result['targetId'])
    def add_init_script(self,script):
        # Deliberately do not install scripts/listeners on an existing login page.
        # Draft/recipient checks are performed immediately before each action.
        pass
    def close(self):
        for page in self.cache.values():
            try:page.client.close()
            except Exception:pass
        self.browser_client.close()
