import tempfile
import unittest
from pathlib import Path
from max_tutor.storage import Store
from max_tutor.bot import Bot
from max_tutor.content import Catalog

class FakeMax:
    def __init__(self): self.sent=[]; self.fail=False
    def send(self,uid,body):
        if self.fail: raise RuntimeError('network')
        self.sent.append((uid,body)); return {'message':{'body':{'mid':str(len(self.sent))}}}
    def waiting(self,uid): pass
    def edit_keyboard(self,uid,mid,attachments): pass
    def answer(self,callback): pass
class FakeTutor:
    def __init__(self): self.calls=0; self.fail=False
    def reply(self,context):
        self.calls+=1
        if self.fail: raise RuntimeError('network')
        return {'text':'Что обозначает числитель?', 'action':'hint','assessment':'not_assessed','assistance':'hint','visualId':None}

class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'bot.db'); self.mx=FakeMax(); self.ai=FakeTutor()
        self.bot=Bot(self.store,Catalog(Path(__file__).resolve().parents[2]/'content/packs'),self.mx,self.ai)
        self.n=0
    def event(self,uid,text):
        self.n+=1
        return {'update_type':'message_created','timestamp':self.n,'message':{'sender':{'user_id':uid,'is_bot':False},'recipient':{'chat_type':'dialog','chat_id':uid},'body':{'mid':f'm{self.n}','text':text}}}
    def run_event(self,event):
        self.bot.accept(event); self.bot.tick(); self.bot.flush()
    def text(self,uid,text): self.run_event(self.event(uid,text))
    def click(self,uid,label):
        body=next(b for u,b in reversed(self.mx.sent) if u==uid)
        buttons=[b for a in body.get('attachments',[]) for row in a['payload']['buttons'] for b in row]
        b=next(b for b in buttons if label in b['text']); self.n+=1
        event={'update_type':'message_callback','timestamp':self.n,'callback':{'callback_id':f'c{self.n}','user':{'user_id':uid},'payload':b['payload']},'message':{'recipient':{'chat_type':'dialog','chat_id':uid}}}
        self.run_event(event); return event
    def start(self,uid):
        self.text(uid,'/start'); self.click(uid,'Алгебра'); self.click(uid,'рациональные и иррациональные'); self.click(uid,'Рациональные')
    def test_two_students_and_duplicate_stale_events(self):
        self.start(1); self.start(2)
        e=self.event(1,'3'); self.run_event(e); count=len(self.mx.sent); self.run_event(e)
        self.assertEqual(len(self.mx.sent),count)
        self.assertEqual(self.store.user(1)['session']['status'],'task_complete')
        self.assertEqual(self.store.user(2)['session']['status'],'active')
        old=self.click(1,'Следующая'); self.assertEqual(self.store.user(1)['session']['taskId'],'R02')
        old['callback']['callback_id']='new-click-same-old-button'; self.run_event(old)
        self.assertEqual(self.store.user(1)['session']['taskId'],'R02')
    def test_delivery_retry_does_not_call_gpt(self):
        self.start(1); self.mx.fail=True; self.text(1,'не знаю'); self.assertEqual(self.ai.calls,1)
        self.mx.fail=False; self.text(1,'/repeat'); self.assertEqual(self.ai.calls,1)
        self.assertIn('числитель',str(self.mx.sent[-1]))
    def test_restart_and_paid_retry(self):
        self.start(1); self.ai.fail=True; self.text(1,'не знаю')
        self.assertEqual(self.ai.calls,1)
        self.bot=Bot(Store(self.store.path),self.bot.catalog,self.mx,self.ai)
        self.ai.fail=False; self.text(1,'/retry')
        s=self.store.user(1)['session']; self.assertEqual(self.ai.calls,2)
        self.assertEqual(len([m for m in s['messages'] if m['role']=='user']),1)
        self.assertEqual([r['status'] for r in s['requestLog']],['failed','completed'])
    def test_group_messages_ignored(self):
        e=self.event(1,'/start'); e['message']['recipient']['chat_type']='chat'; self.run_event(e)
        self.assertFalse(self.mx.sent)
    def test_callback_cannot_be_used_by_another_user(self):
        self.text(1,'/start'); b=self.mx.sent[-1][1]['attachments'][0]['payload']['buttons'][0][0]
        self.run_event({'update_type':'message_callback','timestamp':1,'callback':{'callback_id':'attack','user':{'user_id':2},'payload':b['payload']}})
        self.assertIsNone(self.store.user(2)['session'])
    def test_budget(self):
        for i in range(2): self.assertTrue(self.store.charge(1,2,3))
        self.assertFalse(self.store.charge(1,2,3)); self.assertTrue(self.store.charge(2,2,3)); self.assertFalse(self.store.charge(3,2,3))
    def test_crash_recovery_marks_pending_without_replay(self):
        self.start(1); u=self.store.user(1); s=u['session']
        s['pending']='abc'; s['requestLog']=[{'id':'abc','status':'pending','event':{'type':'hint'}}]; self.store.save_user(1,u)
        self.store.recover(); self.bot.tick(); self.bot.flush()
        self.assertIsNone(self.store.user(1)['session']['pending']); self.assertEqual(self.ai.calls,0)
        self.assertEqual(self.store.user(1)['session']['error']['code'],'INTERRUPTED')

    def test_taken_uncommitted_message_is_replayed_after_restart(self):
        e=self.event(1,'/start'); self.bot.accept(e); self.assertIsNotNone(self.store.take())
        self.store.recover(); self.bot.tick(); self.bot.flush()
        self.assertIn('Выбери предмет',self.mx.sent[-1][1]['text'])
    def test_gpt_checkpoint_crash_does_not_auto_replay_paid_call(self):
        self.start(1); e=self.event(1,'не знаю'); self.bot.accept(e); row=self.store.take()
        u=self.store.user(1); s=u['session']; s['pending']='p'; s['requestLog']=[{'id':'p','status':'pending','event':{'type':'answer','text':'не знаю','_inboxSeq':row['seq']}}]
        self.store.save_user(1,u); self.store.recover()
        self.assertFalse(self.bot.tick()); self.assertEqual(self.ai.calls,0)
    def test_completed_response_save_is_retried_without_gpt(self):
        import sqlite3
        self.start(1); original=self.store.finish; failed=[False]
        def fail_once(*args,**kwargs):
            if not failed[0]: failed[0]=True; raise sqlite3.OperationalError('disk full')
            return original(*args,**kwargs)
        self.store.finish=fail_once
        self.text(1,'не знаю'); self.assertEqual(self.ai.calls,1)
        self.bot.tick(); self.bot.flush(); self.assertEqual(self.ai.calls,1)
        self.assertEqual(self.store.user(1)['session']['messages'][-1]['role'],'assistant')

    def test_real_retry_checkpoint_recovery_never_auto_replays(self):
        self.start(1); self.ai.fail=True; self.text(1,'не знаю')
        class ProcessCrash(BaseException): pass
        def crash(context): raise ProcessCrash()
        self.ai.reply=crash
        self.bot.accept(self.event(1,'/retry'))
        with self.assertRaises(ProcessCrash): self.bot.tick()
        self.store.recover()
        self.assertFalse(self.bot.tick())
        s=self.store.user(1)['session']
        self.assertEqual(s['requestCount'],2)
        self.assertEqual(s['requestLog'][-1]['code'],'INTERRUPTED')
    def test_malformed_delivery_is_not_success(self):
        self.mx.send=lambda uid,body:{}
        self.text(1,'/start')
        with self.store.db() as db: status=db.execute('SELECT status FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0]
        self.assertEqual(status,'failed')
    def test_visual_help_is_recorded_before_link(self):
        self.bot.public_url='https://tutor.example'; self.bot.bot_name='tutor_bot'
        self.start(1); self.click(1,'Показать наглядно')
        s=self.store.user(1)['session']; self.assertTrue(s['help'][s['taskId']]['visual'])
        buttons=[b for a in self.mx.sent[-1][1]['attachments'] for row in a['payload']['buttons'] for b in row]
        visual=next(b for b in buttons if b['type']=='open_app')
        self.assertEqual(visual['web_app'],'tutor_bot'); self.assertEqual(visual['payload'],'decimal-period')

    def test_malformed_events_are_rejected_before_queue(self):
        events=[None, [], {'update_type':'bot_started','user':[]}, {'update_type':'message_created','message':'bad'}]
        for field,value in [('text',42),('text',[]),('mid','')]:
            event=self.event(1,'hello'); event['message']['body'][field]=value; events.append(event)
        events.append({'update_type':'message_callback','callback':{'callback_id':'x','user':{'user_id':1},'payload':[]}})
        for event in events:
            with self.subTest(event=event): self.assertFalse(self.bot.accept(event))
        self.assertIsNone(self.store.take())

class KeyboardTest(unittest.TestCase):
    event=BotTest.event
    run_event=BotTest.run_event
    text=BotTest.text
    click=BotTest.click
    start=BotTest.start
    def setUp(self):
        BotTest.setUp(self)
        self.edits=[]; self.edit_fail=False
        def edit(uid,mid,attachments):
            self.edits.append((uid,mid,attachments))
            if self.edit_fail: raise RuntimeError('offline')
        self.mx.edit_keyboard=edit
    def test_answer_removes_old_menu_preserves_current_and_other_student(self):
        self.start(1); self.start(2); self.edits.clear()
        old_mid=str(max(i for i,(uid,body) in enumerate(self.mx.sent,1) if uid==1))
        self.text(1,'3')
        self.assertEqual(self.edits,[(1,old_mid,[])])
        self.assertIn('attachments',self.mx.sent[-1][1])
        self.assertEqual(self.ai.calls,0)
        self.bot.flush(); self.assertEqual(len(self.edits),1)
    def test_callback_removes_previous_menu(self):
        self.text(1,'/start'); original=self.mx.sent[0][1]['text']
        self.click(1,'Алгебра')
        self.assertEqual(self.edits,[(1,'1',[])])
        self.assertEqual(self.mx.sent[0][1]['text'],original)
    def test_failed_edit_retries_after_restart_without_gpt(self):
        self.text(1,'/start'); self.edit_fail=True; self.click(1,'Алгебра')
        self.assertEqual(len(self.edits),1)
        self.bot.flush(); self.assertEqual(len(self.edits),1)
        self.edit_fail=False
        with self.store.db() as db: db.execute('UPDATE keyboard_cleanup SET next_attempt=0')
        self.bot=Bot(Store(self.store.path),self.bot.catalog,self.mx,self.ai)
        self.bot.flush(); self.assertEqual(len(self.edits),2)
        self.bot.flush(); self.assertEqual(len(self.edits),2)
        self.assertEqual(self.ai.calls,0)
    def test_delayed_delivery_strips_obsolete_buttons(self):
        self.mx.fail=True; self.text(1,'/start')
        self.mx.fail=False; self.text(1,'/menu'); self.text(1,'/repeat')
        self.assertNotIn('attachments',self.mx.sent[-1][1])
    def test_backfill_preserves_non_keyboard_attachments(self):
        self.text(1,'/start'); self.edits.clear()
        with self.store.db() as db:
            import json
            row=db.execute('SELECT id,body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()
            body=json.loads(row['body']); body['attachments'].insert(0,{'type':'image','payload':{'token':'test-image'}})
            db.execute('UPDATE outbox SET body=? WHERE id=?',(json.dumps(body),row['id']))
        self.text(1,'/menu')
        self.assertEqual(self.edits,[(1,'1',[{'type':'image','payload':{'token':'test-image'}}])])

class WaitingStatusTest(unittest.TestCase):
    setUp=BotTest.setUp
    event=BotTest.event
    run_event=BotTest.run_event
    text=BotTest.text
    click=BotTest.click
    start=BotTest.start
    def test_waiting_sent_before_gpt_not_added_to_lesson(self):
        waiting=[]; self.mx.waiting=lambda uid:waiting.append(uid)
        self.start(1)
        original=self.ai.reply
        def reply(context):
            self.assertEqual(waiting,[1])
            self.assertTrue(self.store.user(1)['session']['pending'])
            return original(context)
        self.ai.reply=reply
        self.text(1,'не знаю')
        self.assertEqual(self.ai.calls,1)
        self.assertNotIn('Пишу…',str(self.store.user(1)['session']['messages']))
    def test_no_waiting_for_local_check_or_quota(self):
        waiting=[]; self.mx.waiting=lambda uid:waiting.append(uid)
        self.start(1); self.text(1,'3'); self.click(1,'Следующая')
        user=self.store.user(1); user['session']['requestCount']=30; self.store.save_user(1,user)
        self.text(1,'не знаю')
        self.assertEqual(waiting,[]); self.assertEqual(self.ai.calls,0)
    def test_status_failure_does_not_cancel_gpt(self):
        attempts=[]
        def fail(uid): attempts.append(uid); raise RuntimeError('network')
        self.mx.waiting=fail
        self.start(1); self.text(1,'не знаю')
        self.assertEqual(attempts,[1]); self.assertEqual(self.ai.calls,1)
        self.assertIsNone(self.store.user(1)['session']['error'])
