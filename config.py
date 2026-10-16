"""Local, single-user paths. Never shares recruiter browser profiles."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SUBMIT_RESUME_DATA', ROOT / 'data')).resolve()
WEB = ROOT / 'web'

def prepare():
    for name in ('resumes', 'profiles', 'diagnostics'):
        (DATA / name).mkdir(parents=True, exist_ok=True)

def browser_path():
    for location in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                     r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'):
        if Path(location).is_file():
            return location
    return None
