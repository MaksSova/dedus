import copy
import unittest
from pathlib import Path
from max_tutor.content import Catalog
from max_tutor.lessons import new_session, respond, advance

class Tutor:
    def __init__(self, reply=None, error=False): self.calls=0; self.value=reply; self.error=error
    def reply(self, context):
        self.calls+=1
        if self.error: raise RuntimeError('secret must not escape')
        return self.value or {'text':'Что нужно найти?', 'action':'ask','assessment':'not_assessed','assistance':'none','visualId':None}

class LessonTest(unittest.TestCase):
    def setUp(self): self.c=Catalog(Path(__file__).resolve().parents[2]/'content/packs')
    def session(self,pack='algebra-real-numbers',topic='rational'): return new_session(self.c.packs[pack],topic,[],True)
    def send(self,s,event,tutor=None):
        return respond(self.c,s,event,tutor,lambda:None,lambda:True)
    def test_exact_answer_without_gpt(self):
        s=self.session(); t=Tutor(); self.send(s,{'type':'answer','text':'3'},t)
        self.assertEqual(s['status'],'task_complete'); self.assertEqual(t.calls,0)
        self.assertEqual(s['results'][0]['level'],'independent')
        advance(self.c,s); self.assertEqual(s['taskId'],'R02')
    def test_help_cannot_complete(self):
        s=self.session(); t=Tutor({'text':'Верно. Число рациональное.','action':'complete','assessment':'correct','assistance':'none','visualId':None})
        self.send(s,{'type':'answer','text':'что значит рациональное?'},t)
        self.assertEqual(s['status'],'active'); self.assertFalse(s['results'])
        self.assertEqual(s['messages'][-1]['assessment'],'not_assessed')
    def test_hint_confirmation_preserves_hint(self):
        s=self.session(topic='irrational'); advance_to='I02'; s['taskId']=advance_to
        t=Tutor({'text':'Подумай о периоде.','action':'hint','assessment':'not_assessed','assistance':'hint','visualId':None})
        self.send(s,{'type':'hint'},t)
        t.value={'text':'Да','action':'complete','assessment':'correct','assistance':'explanation','visualId':None}
        self.send(s,{'type':'answer','text':'Я считаю что это иррациональное число'},t)
        self.assertEqual(s['results'][0]['level'],'hint')
        self.assertIn('фиксированного',s['messages'][-1]['text'])
    def test_failure_retry_no_duplicate_or_hint_inflation(self):
        s=self.session(); t=Tutor(error=True)
        self.send(s,{'type':'hint'},t); self.assertIsNotNone(s['error'])
        self.assertNotIn('secret',str(s['error'])); self.assertEqual(s['requestLog'][0]['status'],'failed')
        t.error=False; self.send(s,{'type':'retry'},t)
        self.assertEqual(len([m for m in s['messages'] if m['role']=='user']),1)
        self.assertEqual(s['help']['R01']['hints'],1); self.assertEqual(t.calls,2)
    def test_stop_and_quota(self):
        s=self.session(); t=Tutor()
        respond(self.c,s,{'type':'answer','text':'не знаю'},t,lambda:None,lambda:False)
        self.assertEqual(t.calls,0); self.assertIn('Лимит',s['notice'])
        self.send(s,{'type':'answer','text':'давай закончим'},t)
        self.assertEqual(s['status'],'ended'); self.assertEqual(t.calls,0)
    def test_practice_honest_about_proofs(self):
        s=self.session(); s['mode']='practice'
        self.send(s,{'type':'answer','text':'Я объясню своими словами'})
        self.assertIn('без ИИ',s['messages'][-1]['text'])
