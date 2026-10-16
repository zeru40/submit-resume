from types import SimpleNamespace
from platforms.base import ApplicantPlatform
from platforms.specs import SPECS
from core.control import Control

class Page:
    def __init__(self,url):self.url=url;self.closed=False;self.front=False
    def is_closed(self):return self.closed
    def set_default_timeout(self,_):pass
    def close(self):self.closed=True
    def bring_to_front(self):self.front=True
    def goto(self,url,**kwargs):self.url=url
    def wait_for_timeout(self,_):pass

def make(pages):
    obj=object.__new__(ApplicantPlatform)
    obj.code='boss';obj.spec=SPECS['boss']
    obj.context=SimpleNamespace(pages=pages,new_page=lambda:Page('about:blank'))
    obj.page=pages[0]
    obj.control=Control()
    obj.log=lambda *_:None
    return obj

def test_restored_boss_tab_is_selected_ahead_of_blank():
    real=Page('https://www.zhipin.com/web/geek/jobs')
    blank=Page('about:blank')
    obj=make([real,blank])
    assert obj.select_page() is real

def test_login_subdomain_is_reused_instead_of_new_blank():
    login=Page('https://passport.zhipin.com/login')
    blank=Page('about:blank')
    obj=make([blank,login])
    assert obj.select_page() is login

def test_open_brings_platform_forward_and_cleans_its_blank_tab():
    blank=Page('about:blank')
    real=Page('https://www.zhipin.com/web/geek/jobs')
    obj=make([blank,real]);obj.session=lambda:{'state':'waiting_login'}
    assert obj.open()['state']=='waiting_login'
    assert real.front and blank.closed

def test_login_opens_standalone_portal_once_and_preserves_it():
    page=Page('https://www.zhipin.com/web/geek/jobs')
    obj=make([page]);obj.session=lambda:{'state':'waiting_login'}
    navigations=[]
    def navigate(url,**kwargs):
        navigations.append(url);page.url=url
    page.goto=navigate
    obj.open();obj.open()
    assert navigations==['https://www.zhipin.com/web/user/?intent=0']

def test_blank_is_not_reported_as_a_loaded_login_page():
    obj=make([Page('about:blank')])
    assert obj.session()['state']=='page_unavailable'

def test_visible_browser_uses_normal_edge_runtime(tmp_path,monkeypatch):
    received={}
    page=Page('about:blank')
    context=SimpleNamespace(pages=[page],add_init_script=lambda _:None)
    def launch(playwright,executable,profile,destination,data_dir,code,log):
        received.update(profile=profile,destination=destination,code=code)
        return context
    monkeypatch.setattr('core.browser_runtime.open_edge',launch)
    pw=object()
    ApplicantPlatform('boss',pw,tmp_path,Control(),lambda *_:None,lambda *_:None)
    assert received['destination']=='https://www.zhipin.com/web/geek/jobs'
    assert received['profile']==tmp_path/'profiles'/'boss'

def test_native_edge_starts_with_destination_and_without_unsafe_flags(tmp_path,monkeypatch):
    import core.browser_runtime as runtime
    captured={}
    owner=object()
    def popen(command,**kwargs):
        captured['command']=command
        return SimpleNamespace(pid=123,poll=lambda:None)
    monkeypatch.setattr(runtime.subprocess,'Popen',popen)
    monkeypatch.setattr(runtime,'_attach',lambda *args:owner)
    destination='https://www.zhipin.com/web/geek/jobs'
    assert runtime.open_edge(object(),'edge.exe',tmp_path/'profile',destination,tmp_path,'boss',lambda _:None) is owner
    command=captured['command']
    assert command[-1]==destination
    assert 'about:blank' not in command
    assert '--no-sandbox' not in command
    assert not any('AutomationControlled' in arg for arg in command)
    assert '--remote-debugging-address=127.0.0.1' in command

def test_normal_edge_reuses_owned_runtime_without_launching_again(tmp_path,monkeypatch):
    import core.browser_runtime as runtime
    import json
    profile=tmp_path/'profile';profile.mkdir()
    (tmp_path/'browser-runtime-boss.json').write_text(json.dumps({'profile':str(profile.resolve()),'executable':'edge.exe','port':19362}),encoding='utf-8')
    owner=object()
    monkeypatch.setattr(runtime,'_attach',lambda *args:owner)
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *_args,**_kwargs:(_ for _ in ()).throw(AssertionError('不得重复打开Edge')))
    assert runtime.open_edge(object(),'edge.exe',profile,'https://www.zhipin.com/',tmp_path,'boss',lambda _:None) is owner

def test_manual_login_does_not_attach_or_read_page(tmp_path,monkeypatch):
    import core.browser_runtime as runtime
    captured=[]
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda command,**kwargs:captured.append(command) or SimpleNamespace(pid=1234))
    monkeypatch.setattr(runtime,'_attach',lambda *args:(_ for _ in ()).throw(AssertionError('人工登录时不得连接')))
    state=runtime.open_login_window('edge.exe',tmp_path/'profile','https://www.zhipin.com/web/user/?intent=0',tmp_path,'boss',lambda _:None)
    assert state['mode']=='manual_login'
    assert captured[0][-1]=='https://www.zhipin.com/web/user/?intent=0'
    assert '--no-sandbox' not in captured[0]
