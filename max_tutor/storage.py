"""Durable state. One worker; every SQLite connection has a short transaction."""
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

def encode(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def fresh_user(): return {'session':None,'history':[],'buttons':{},'subject':None,'view':'home','revision':0}

class Store:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox(seq INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued');
                CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',mid TEXT);
                CREATE TABLE IF NOT EXISTS budget(day TEXT NOT NULL,uid INTEGER NOT NULL,n INTEGER NOT NULL,PRIMARY KEY(day,uid));
                CREATE TABLE IF NOT EXISTS keyboard_cleanup(outbox_id INTEGER PRIMARY KEY,done INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            ''')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10); db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def user(self,uid):
        with self.db() as db: row=db.execute('SELECT data FROM users WHERE id=?',(uid,)).fetchone()
        return json.loads(row[0]) if row else fresh_user()
    def save_user(self,uid,user):
        with self.db() as db: db.execute('INSERT OR REPLACE INTO users VALUES(?,?)',(uid,encode(user)))
    def enqueue(self,key,event):
        with self.db() as db:
            return db.execute('INSERT OR IGNORE INTO inbox(key,payload) VALUES(?,?)',(key,encode(event))).rowcount==1
    def take(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM inbox WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if row: db.execute("UPDATE inbox SET status='working' WHERE seq=?",(row['seq'],))
        return dict(row) if row else None
    def finish(self,seq,uid=None,user=None,messages=()):
        with self.db() as db:
            if user is not None: db.execute('INSERT OR REPLACE INTO users VALUES(?,?)',(uid,encode(user)))
            for body in messages: db.execute('INSERT INTO outbox(uid,body) VALUES(?,?)',(uid,encode(body)))
            db.execute("UPDATE inbox SET status='done' WHERE seq=?",(seq,))
    def charge(self,uid,user_limit=30,global_limit=100):
        day=datetime.now(timezone(timedelta(hours=3))).date().isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            total=db.execute('SELECT coalesce(sum(n),0) FROM budget WHERE day=?',(day,)).fetchone()[0]
            row=db.execute('SELECT n FROM budget WHERE day=? AND uid=?',(day,uid)).fetchone()
            if total>=global_limit or (row and row[0]>=user_limit): return False
            db.execute('INSERT INTO budget VALUES(?,?,1) ON CONFLICT(day,uid) DO UPDATE SET n=n+1',(day,uid))
            return True
    def outgoing(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM outbox WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row: db.execute("UPDATE outbox SET status='sending' WHERE id=?",(row['id'],))
        return dict(row) if row else None
    def delivery(self,oid,status,mid=None):
        with self.db() as db: db.execute('UPDATE outbox SET status=?,mid=? WHERE id=?',(status,mid,oid))
    def keyboard_candidates(self):
        with self.db() as db:
            rows=db.execute("""SELECT o.* FROM outbox o LEFT JOIN keyboard_cleanup k ON k.outbox_id=o.id
                WHERE o.status='sent' AND o.mid IS NOT NULL AND instr(o.body,'inline_keyboard')>0
                AND coalesce(k.done,0)=0 AND coalesce(k.next_attempt,0)<=? ORDER BY o.id""",(time.time(),)).fetchall()
        return [dict(row) for row in rows]
    def keyboard_cleanup_result(self,oid,done=False):
        # Editing attachments is idempotent, unlike sending a new message or calling GPT.
        # Defer before the request so crashes/errors do not cause a rapid retry loop.
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO keyboard_cleanup VALUES(?,?,?)',(oid,int(done),time.time()+60))
    def repeat_delivery(self,uid):
        with self.db() as db:
            # Explicitly replay only failed/uncertain deliveries, never re-run GPT.
            rows=db.execute("SELECT id FROM outbox WHERE uid=? AND status IN ('failed','unknown') ORDER BY id",(uid,)).fetchall()
            for row in rows: db.execute("UPDATE outbox SET status='queued' WHERE id=?",(row['id'],))
            return bool(rows)
    def meta(self,key,value=None):
        with self.db() as db:
            if value is not None: db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,encode(value)))
            row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else None
    def recover(self):
        with self.db() as db:
            db.execute("UPDATE outbox SET status='unknown' WHERE status='sending'")
            db.execute("UPDATE inbox SET status='queued' WHERE status='working'")
            rows=db.execute('SELECT * FROM users').fetchall()
            for row in rows:
                user=json.loads(row['data']); s=user.get('session')
                if not s or not s.get('pending'): continue
                entry=next((r for r in s['requestLog'] if r['id']==s['pending']),None)
                if entry:
                    entry.update(status='failed',code='INTERRUPTED')
                    seq=entry.get('event',{}).get('_inboxSeq')
                    if seq is not None: db.execute("UPDATE inbox SET status='done' WHERE seq=?",(seq,))
                    s['error']={'code':'INTERRUPTED','event':entry['event'],'message':'Связь прервалась. Ответ сохранён. Можно повторить запрос.'}
                s['pending']=None
                db.execute('UPDATE users SET data=? WHERE id=?',(encode(user),row['id']))
                db.execute('INSERT INTO outbox(uid,body) VALUES(?,?)',(row['id'],encode({'text':'Предыдущий запрос был прерван. Напиши /continue, чтобы вернуться к занятию. Автоматически GPT повторно не вызывается.'})))
    def export(self,uid): return self.user(uid)
