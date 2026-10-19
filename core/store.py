"""SQLite is the source of truth. External actions are reserved before clicking."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def dumps(value):
    return json.dumps(value, ensure_ascii=False)

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_assessments(id TEXT PRIMARY KEY,result TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS resumes(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,
                sha256 TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,name TEXT NOT NULL,body TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,platform TEXT NOT NULL,external_id TEXT NOT NULL,
                title TEXT NOT NULL,company TEXT NOT NULL,body TEXT NOT NULL,score INTEGER NOT NULL DEFAULT 0,
                reasons TEXT NOT NULL DEFAULT '[]',eligible INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,
                UNIQUE(platform,external_id));
            CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,scope TEXT NOT NULL,
                resume_id TEXT NOT NULL,plan_id TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,
                UNIQUE(scope,job_id),FOREIGN KEY(job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,application_id TEXT NOT NULL,kind TEXT NOT NULL,
                status TEXT NOT NULL,receipt TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
                UNIQUE(application_id,kind),FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,body TEXT NOT NULL,status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,level TEXT NOT NULL,
                message TEXT NOT NULL,created_at TEXT NOT NULL);
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(resumes)')}
            if 'profile' not in columns:
                db.execute("ALTER TABLE resumes ADD COLUMN profile TEXT NOT NULL DEFAULT '{}'")
            db.execute("UPDATE actions SET status='unknown',error='程序中断，需核验平台结果' WHERE status='pending'")
            db.execute("UPDATE applications SET status='needs_review' WHERE status!='delivered' AND id IN (SELECT application_id FROM actions WHERE status='unknown')")
            db.execute("UPDATE runs SET status='interrupted',error='程序已重启，请检查结果后重新开始' WHERE status IN ('running','queued','paused','waiting_login')")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=15000')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def all(self, sql, args=()):
        with self.db() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.db() as db:
            db.execute(sql, args)

    def setting(self, key, default=None):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        self.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dumps(value)))

    def log(self, message, level='info'):
        self.execute('INSERT INTO events(level,message,created_at) VALUES(?,?,?)', (level, str(message)[:1500], now()))

    def upsert_job(self, job, score, reasons, eligible):
        ident = f"{job['platform']}:{job['external_id']}"
        self.execute('''INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,external_id)
            DO UPDATE SET title=excluded.title,company=excluded.company,body=excluded.body,score=excluded.score,
            reasons=excluded.reasons,eligible=excluded.eligible,updated_at=excluded.updated_at''',
            (ident, job['platform'], job['external_id'], job['title'], job['company'], dumps(job), score,
             dumps(reasons), int(eligible), now()))
        return ident

    def application(self, job_id, scope, resume_id, plan_id):
        with self.db() as db:
            ident = uuid.uuid4().hex
            db.execute('INSERT OR IGNORE INTO applications VALUES(?,?,?,?,?,?,?)',
                       (ident, job_id, scope, resume_id, plan_id, 'ready', now()))
            return dict(db.execute('SELECT * FROM applications WHERE scope=? AND job_id=?', (scope, job_id)).fetchone())

    def reserve(self, application_id, kind):
        """A new attempt token replaces only a proven failure, never unknown/sent."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM actions WHERE application_id=? AND kind=?', (application_id, kind)).fetchone()
            if row and row['status'] != 'failed':
                return None
            token = uuid.uuid4().hex
            db.execute('''INSERT INTO actions VALUES(?,?,?,'pending','','',?) ON CONFLICT(application_id,kind)
                DO UPDATE SET id=excluded.id,status='pending',receipt='',error='',updated_at=excluded.updated_at''',
                (token, application_id, kind, now()))
            return token

    def outcome(self, token, status, receipt='', error=''):
        if status not in {'sent', 'unknown', 'failed'}:
            raise ValueError('无效动作结果')
        with self.db() as db:
            row = db.execute('SELECT * FROM actions WHERE id=?', (token,)).fetchone()
            if not row or row['status'] != 'pending':
                return False
            db.execute('UPDATE actions SET status=?,receipt=?,error=?,updated_at=? WHERE id=?',
                       (status, str(receipt)[:2000], str(error)[:1000], now(), token))
            return True

    def review(self, action_id, sent, note):
        with self.db() as db:
            row = db.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
            if not row or row['status'] != 'unknown':
                raise ValueError('仅能核验结果未知的动作')
            db.execute('UPDATE actions SET status=?,receipt=?,error=?,updated_at=? WHERE id=?',
                       ('sent' if sent else 'failed', '人工核验：' + note if sent else '', note, now(), action_id))
            db.execute("UPDATE applications SET status='ready',updated_at=? WHERE id=? AND status!='delivered'", (now(), row['application_id']))

    def resolve_no_external_contact(self,action_id,job_id,evidence):
        """Only a verified no-op may release an unknown, receipt-less contact.

        Caller must verify the full inbox and the freshly loaded target's
        uncontacted state. This is not a generic timeout retry path.
        """
        if len(evidence.strip())<15:raise ValueError('需要明确的未执行核验依据')
        with self.db() as db:
            row=db.execute('''SELECT x.*,a.job_id FROM actions x JOIN applications a ON a.id=x.application_id
                WHERE x.id=?''',(action_id,)).fetchone()
            if not row or row['job_id']!=job_id or row['kind']!='contact' or row['status']!='unknown' or row['receipt']:
                return False
            db.execute("UPDATE actions SET status='failed',error=?,updated_at=? WHERE id=?",(evidence,now(),action_id))
            db.execute("UPDATE applications SET status='ready',updated_at=? WHERE id=?",(now(),row['application_id']))
            db.execute('INSERT INTO events(level,message,created_at) VALUES(?,?,?)',
                       ('info','核验联系动作未发生 '+action_id+'：'+evidence,now()))
            return True

    def application_status(self, ident, status):
        self.execute('UPDATE applications SET status=?,updated_at=? WHERE id=?', (status, now(), ident))

    def reconcile_contact(self, action_id, job_id, scope, receipt, expected_avatar):
        """Record a positive DOM verification without clicking contact again.

        Caller reads the source HR avatar and verifies the selected conversation.
        This does not confirm any message or attachment delivery.
        """
        identity=receipt.get('identity',{})
        if not expected_avatar or identity.get('avatar')!=expected_avatar.split('?')[0] or not receipt.get('peer'):
            raise ValueError('缺少来源HR与当前会话的匹配依据')
        with self.db() as db:
            row=db.execute('''SELECT x.*,a.job_id,a.scope,j.title,j.company FROM actions x
                JOIN applications a ON a.id=x.application_id JOIN jobs j ON j.id=a.job_id WHERE x.id=?''',(action_id,)).fetchone()
            if (not row or row['kind']!='contact' or row['status']!='unknown' or row['receipt']
                    or row['job_id']!=job_id or row['scope']!=scope or receipt.get('job_id')!=job_id
                    or identity.get('company')!=row['company'] or identity.get('title') not in
                    {row['title'],'兼职·'+row['title'],'全职·'+row['title'],'实习·'+row['title']}):
                return False
            db.execute("UPDATE actions SET status='sent',receipt=?,error='',updated_at=? WHERE id=?",
                       (dumps(receipt),now(),action_id))
            db.execute("UPDATE applications SET status='ready',updated_at=? WHERE id=?",(now(),row['application_id']))
            db.execute('INSERT INTO events(level,message,created_at) VALUES(?,?,?)',
                       ('info','已通过来源HR、公司和岗位核验会话 '+action_id+'；未重复点击沟通',now()))
            return True

    def complete_manually(self, ident, note):
        with self.db() as db:
            row = db.execute('SELECT id FROM applications WHERE id=?',(ident,)).fetchone()
            if not row:
                raise ValueError('投递记录不存在')
            db.execute('''INSERT INTO actions VALUES(?,?,?,'sent',?,'',?)
                ON CONFLICT(application_id,kind) DO UPDATE SET receipt=excluded.receipt,updated_at=excluded.updated_at''',
                (uuid.uuid4().hex,ident,'manual_confirmation',dumps({'verification':'manual','note':note}),now()))
            db.execute("UPDATE applications SET status='delivered',updated_at=? WHERE id=?",(now(),ident))

    def jobs(self):
        rows = self.all('SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 1000')
        for row in rows:
            row['body'] = json.loads(row['body'])
            row['reasons'] = json.loads(row['reasons'])
        return rows
