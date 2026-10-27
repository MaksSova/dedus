"""MAX event routing, independent of HTTP and of the model provider."""
import hashlib
import json
import secrets
import sqlite3
import threading
from .content import VISUALS
from .lessons import new_session,respond,advance,task_for
from .storage import encode


def envelope(event):
    # Validate before durable enqueue so malformed input cannot poison the worker.
    if not isinstance(event,dict): return None
    def obj(value): return value if isinstance(value,dict) else {}
    kind=event.get('update_type')
    if kind=='bot_started':
        uid=obj(event.get('user')).get('user_id')
        return uid,'start:'+hashlib.sha256(encode(event).encode()).hexdigest(),'/start',None
    if kind=='message_created':
        m=obj(event.get('message')); sender=obj(m.get('sender'))
        if sender.get('is_bot') or obj(m.get('recipient')).get('chat_type')!='dialog': return None
        body=obj(m.get('body')); mid=body.get('mid'); text=body.get('text')
        if not isinstance(mid,str) or not mid: return None
        if text is not None and not isinstance(text,str): return None
        return sender.get('user_id'),'m:'+mid,text or '',None
    if kind=='message_callback':
        cb=obj(event.get('callback')); m=event.get('message')
        if m is not None and obj(obj(m).get('recipient')).get('chat_type')!='dialog': return None
        cid=cb.get('callback_id')
        if not isinstance(cid,str) or not cid or not isinstance(cb.get('payload'),str): return None
        return obj(cb.get('user')).get('user_id'),'c:'+cid,None,cb
    return None


def chunks(text,limit=3500):
    # Plain text, no split HTML/Markdown markup or hidden embedded instructions.
    result=[]
    while len(text)>limit:
        cut=text.rfind('\n',0,limit)
        if cut<limit//2: cut=limit
        result.append(text[:cut]); text=text[cut:].lstrip('\n')
    if text: result.append(text)
    return result or [' ']

def stale_keyboard(body,buttons):
    keyboards=[a for a in body.get('attachments',[]) if a.get('type')=='inline_keyboard']
    if not keyboards: return False
    tokens={b.get('payload') for a in keyboards for row in a.get('payload',{}).get('buttons',[]) for b in row if b.get('type')=='callback'}
    return not tokens.intersection(buttons)

def without_keyboard(body):
    result={**body}
    attachments=[a for a in body.get('attachments',[]) if a.get('type')!='inline_keyboard']
    if attachments: result['attachments']=attachments
    else: result.pop('attachments',None)
    return result

class Bot:
    def __init__(self,store,catalog,max_client,tutor=None,*,allowed=(),public_url='',bot_name='',user_limit=30,global_limit=100):
        self.store,self.catalog,self.max,self.tutor=store,catalog,max_client,tutor
        self.allowed=set(allowed); self.public_url=public_url.rstrip('/'); self.bot_name=bot_name
        self.user_limit,self.global_limit=user_limit,global_limit
        self.worker_lock=threading.Lock(); self.healthy=True; self.pending_commit=None
    def accept(self,event):
        parsed=envelope(event)
        if not parsed: return False
        uid,key,_,_=parsed
        if type(uid) is not int or uid<=0: return False
        if self.allowed and uid not in self.allowed: return False
        return self.store.enqueue(str(uid)+':'+key,event)
    def button(self,user,label,action):
        token=secrets.token_urlsafe(18); user['buttons'][token]=action
        return {'type':'callback','text':label[:128],'payload':token}
    def packet(self,user,text,options=(),extra=()):
        buttons=[[self.button(user,label,action)] for label,action in options]
        buttons+= [[b] for b in extra]
        parts=[{'text':part} for part in chunks(text)]
        if buttons: parts[-1]['attachments']=[{'type':'inline_keyboard','payload':{'buttons':buttons[:30]}}]
        return parts
    def menu(self,u):
        opts=[(title,{'type':'subject','subject':sid}) for sid,title in self.catalog.subjects.items()]
        if u['session'] and u['session']['status']!='ended': opts.insert(0,('Продолжить занятие',{'type':'continue'}))
        opts.append(('Мой прогресс',{'type':'progress'}))
        return self.packet(u,'Привет! Я помогу разобраться в задаче шаг за шагом. Выбери предмет.\nОтветы и подсказки сохраняются для разбора занятия преподавателем.\n'+('Диалог с ИИ включён.' if self.tutor else 'Сейчас тренажёр без ИИ: числа и подсказки, без проверки свободных рассуждений.'),opts)
    def packs_menu(self,u,subject):
        if subject not in self.catalog.subjects: raise ValueError('Предмет недоступен.')
        u['subject']=subject
        opts=[(p['title'],{'type':'pack','pack':p['id'],'page':0}) for p in self.catalog.packs.values() if p['subject']==subject]
        return self.packet(u,'Выбери раздел: '+self.catalog.subjects[subject],opts+[('Все предметы',{'type':'menu'})])
    def topics_menu(self,u,pid,page=0):
        p=self.catalog.packs[pid]; topics=[t for t in p['topics'] if self.catalog.tasks(pid,t['id'])]
        page=max(0,min(page,(len(topics)-1)//8)); opts=[(t['title'],{'type':'start','pack':pid,'topic':t['id']}) for t in topics[page*8:page*8+8]]
        if page: opts.append(('Предыдущие темы',{'type':'pack','pack':pid,'page':page-1}))
        if (page+1)*8<len(topics): opts.append(('Ещё темы',{'type':'pack','pack':pid,'page':page+1}))
        opts.append(('Назад к разделам',{'type':'subject','subject':p['subject']}))
        return self.packet(u,p['title']+'\nВыбери тему:',opts)
    def lesson_menu(self,u,prefix='',show_task=False):
        s=u['session']; task=task_for(self.catalog,s); opts=[]; text=prefix
        if show_task:
            pub=task['public']; text+='\n'+pub['title']+'\n'+pub['statement']+'\n'+pub.get('reference','')
            for figure in pub.get('figures',[]): text+='\n'+(figure if isinstance(figure,str) else figure.get('description',encode(figure)))
            source=task['source']
            if source.get('kind')=='original': text+='\nАвторское тренировочное задание.'
            else: text+='\nИсточник: '+', '.join(f'{k}: {v}' for k,v in source.items() if k not in ('kind','sha256'))
            if task['id'] in s['previous']: text+='\nТы уже встречал эту задачу — это повторение.'
        if s.get('notice'): text+='\n'+s['notice']
        if s.get('error'): text+='\n'+s['error']['message']; opts.append(('Повторить запрос',{'type':'retry'}))
        if s['status']=='active':
            opts.extend([('Подсказка',{'type':'hint'}),('Объясни проще',{'type':'explain'})])
            if self.public_url and task.get('visualIds'): opts.append(('Показать наглядно',{'type':'visual'}))
            opts.append(('Закончить',{'type':'finish'}))
        elif s['status']=='task_complete':
            done={r['taskId'] for r in s['results']}; remaining=[t for t in self.catalog.tasks(s['packId'],s['topic']) if t['id'] not in done]
            if remaining: opts.append(('Следующая задача',{'type':'next'}))
            if any(t['difficulty']>task['difficulty'] for t in remaining): opts.append(('Посложнее',{'type':'harder'}))
            opts.append(('Закончить',{'type':'finish'}))
            if len(s['results'])>=3: text+='\nУже решено три задачи или больше. Можно сделать перерыв.'
        else: text+='\n'+self.summary(s)
        opts.append(('Предметы и темы',{'type':'menu'}))
        return self.packet(u,text.strip() or 'Напиши свой ответ или ход рассуждения.',opts)
    def summary(self,s):
        names={'independent':'самостоятельно','hint':'с подсказкой','explained':'после объяснения','review':'нужна перепроверка'}
        counts={k:sum(r['level']==k for r in s['results']) for k in names}
        return 'Итог: '+', '.join(f'{names[k]} — {v}' for k,v in counts.items())+'. Незавершённая задача не считается ошибкой.'
    def dispatch(self,uid,u,action):
        typ=action['type']; s=u['session']
        if typ=='menu': return self.menu(u)
        if typ=='subject': return self.packs_menu(u,action['subject'])
        if typ=='pack': return self.topics_menu(u,action['pack'],action.get('page',0))
        if typ=='progress':
            sessions=u['history']+([s] if s else [])
            text='Занятий: '+str(len(sessions))+'\n'+'\n'.join(self.catalog.packs[x['packId']]['title']+': '+self.summary(x) for x in sessions[-8:])
            return self.packet(u,text or 'Занятий пока нет.',[('Предметы',{'type':'menu'})])
        if typ=='start':
            if s: u['history'].append(s)
            pack=self.catalog.packs[action['pack']]
            previous=[t for old in u['history'] if old['packId']==pack['id'] for t in [old['taskId'],*[r['taskId'] for r in old['results']]]]
            u['session']=new_session(pack,action['topic'],previous,bool(self.tutor))
            return self.lesson_menu(u,show_task=True)
        if not s: return self.menu(u)
        if typ=='continue': return self.lesson_menu(u,show_task=True)
        if typ in ('next','harder'):
            advance(self.catalog,s,typ=='harder'); return self.lesson_menu(u,show_task=True)
        if typ=='visual':
            task=task_for(self.catalog,s)
            if not self.public_url: return self.lesson_menu(u,'Наглядные примеры пока не опубликованы. Можно попросить объяснить проще.')
            h=s['help'].setdefault(task['id'],{'hints':0,'explained':False,'visual':False}); h['visual']=True
            extra=[]
            for vid in task.get('visualIds',[]):
                if vid not in VISUALS: continue
                if self.bot_name: extra.append({'type':'open_app','text':VISUALS[vid][0],'web_app':self.bot_name,'payload':vid})
                else: extra.append({'type':'link','text':VISUALS[vid][0],'url':self.public_url+'/?visual='+vid})
            return self.packet(u,'Посмотри отдельный пример, затем вернись к нашей задаче. Наглядная помощь отмечена в занятии.',[('Вернуться к задаче',{'type':'continue'})],extra)
        before=len(s['messages'])
        respond(self.catalog,s,action,self.tutor,lambda:self.store.save_user(uid,u),lambda:self.store.charge(uid,self.user_limit,self.global_limit),on_wait=lambda:self.max.waiting(uid))
        added=[m['text'] for m in s['messages'][before:] if m['role']=='assistant']
        return self.lesson_menu(u,'\n\n'.join(added))
    def commit(self,*args):
        self.pending_commit=args
        try: self.store.finish(*args)
        except (sqlite3.Error,OSError):
            self.healthy=False; return False
        self.pending_commit=None; self.healthy=True; return True
    def tick(self):
        # One worker serializes updates; the webhook can still enqueue concurrently.
        with self.worker_lock:
            if self.pending_commit is not None: return self.commit(*self.pending_commit)
            row=self.store.take()
            if not row: return False
            event=json.loads(row['payload']); uid,_,text,cb=envelope(event); u=self.store.user(uid)
            try:
                if cb:
                    action=u['buttons'].get(cb.get('payload'))
                    try: self.max.answer(cb['callback_id'])
                    except Exception: pass  # Does not suppress the durable lesson reply.
                    if not action:
                        self.commit(row['seq'],uid,u,[{'text':'Эта кнопка уже нажата.'}]); return True
                else:
                    commands={'/start':'menu','/menu':'menu','/continue':'continue','/finish':'finish','/progress':'progress','/retry':'retry','/hint':'hint','/explain':'explain'}
                    if text.strip()=='/repeat':
                        repeated=self.store.repeat_delivery(uid)
                        self.commit(row['seq'],uid,u,[] if repeated else [{'text':'Нет недоставленных сообщений. /continue — вернуться к задаче.'}]); return True
                    action={'type':commands[text.strip()]} if text.strip() in commands else {'type':'answer','text':text}
                    if not text.strip():
                        self.commit(row['seq'],uid,u,[{'text':'Пока я читаю текст. Перепиши условие или ответ сообщением; распознавание фото ещё не подключено.'}]); return True
                u['buttons']={}; u['revision']+=1
                action={**action,'_inboxSeq':row['seq']}
                messages=self.dispatch(uid,u,action)
            except (ValueError,KeyError,StopIteration) as exc:
                # Only our own plain validation messages; never print provider details.
                message=str(exc) if isinstance(exc,ValueError) else 'Тема или задача недоступна. Выбери её заново.'
                messages=self.packet(u,message,[('Предметы',{'type':'menu'}),('Продолжить',{'type':'continue'})])
            self.commit(row['seq'],uid,u,messages)
            return True
    def flush(self):
        while row:=self.store.outgoing():
            try:
                body=json.loads(row['body'])
                if stale_keyboard(body,self.store.user(row['uid'])['buttons']): body=without_keyboard(body)
                response=self.max.send(row['uid'],body)
                mid=response.get('message',{}).get('body',{}).get('mid')
                if not isinstance(mid,str) or not mid: raise ValueError('Missing delivery receipt')
            except Exception:
                self.store.delivery(row['id'],'failed'); continue
            self.store.delivery(row['id'],'sent',mid)
        self.clean_keyboards()
    def clean_keyboards(self):
        users={}; attempted=0
        for row in self.store.keyboard_candidates():
            uid=row['uid']
            if uid not in users: users[uid]=self.store.user(uid)['buttons']
            body=json.loads(row['body'])
            if not stale_keyboard(body,users[uid]): continue
            self.store.keyboard_cleanup_result(row['id'])
            try: self.max.edit_keyboard(uid,row['mid'],without_keyboard(body).get('attachments',[]))
            except Exception: pass  # Retry a harmless edit later; never repeat the lesson/GPT.
            else: self.store.keyboard_cleanup_result(row['id'],done=True)
            attempted+=1
            if attempted>=10: break
