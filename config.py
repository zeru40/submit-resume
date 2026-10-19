"""Local, single-user paths. Never shares recruiter browser profiles."""
import os
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG = ROOT / 'workspace.local.json'

def saved_directory():
    if not LOCAL_CONFIG.exists():return str(ROOT / 'data')
    return json.loads(LOCAL_CONFIG.read_text(encoding='utf-8'))['data_dir']

DATA = Path(os.environ.get('SUBMIT_RESUME_DATA') or saved_directory()).resolve()
WEB = ROOT / 'web'

def save_directory(value):
    if os.environ.get('SUBMIT_RESUME_DATA'):
        raise ValueError('数据目录由 SUBMIT_RESUME_DATA 环境变量指定，请先修改环境变量')
    target = Path(value).expanduser()
    if not target.is_absolute():raise ValueError('请填写绝对路径')
    target = target.resolve()
    if target == ROOT or target in ROOT.parents or (ROOT in target.parents and
            target != ROOT / 'data' and ROOT / 'data' not in target.parents):
        raise ValueError('请选择仓库外的数据文件夹，或默认 data 目录，避免隐私被提交')
    target.mkdir(parents=True,exist_ok=True)
    if any(target.iterdir()) and not (target / 'app.sqlite3').is_file() and target != DATA:
        raise ValueError('请选择空文件夹，或包含 app.sqlite3 的现有工作空间')
    with tempfile.TemporaryFile(dir=target):pass
    temporary=LOCAL_CONFIG.with_suffix('.tmp')
    temporary.write_text(json.dumps({'data_dir':str(target)},ensure_ascii=False),encoding='utf-8')
    temporary.replace(LOCAL_CONFIG)
    return str(target)

def prepare():
    for name in ('resumes', 'profiles', 'diagnostics'):
        (DATA / name).mkdir(parents=True, exist_ok=True)

def browser_path():
    for location in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                     r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'):
        if Path(location).is_file():
            return location
    return None
