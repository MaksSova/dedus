import json
import re
import uuid
from datetime import datetime, timezone
from .checks import check


def now(): return datetime.now(timezone.utc).isoformat()
def new_session(pack,topic,previous,ai):
    tasks=[t for t in pack['tasks'] if t['topic']==topic]
    if not tasks: raise ValueError('В этой теме пока нет задач.')
    return {'id':uuid.uuid4().hex,'packId':pack['id'],'version':pack['version'],'topic':topic,'taskId':tasks[0]['id'],
            'mode':'ai' if ai else 'practice','status':'active','revision':0,'messages':[],'results':[],'help':{},
            'previous':list(previous),'requestCount':0,'requestLog':[],'pending':None,'error':None,'createdAt':now()}

def task_for(c,s):
    if c.packs[s['packId']]['version']!=s['version']: raise ValueError('Набор задач обновлён. Начни новое занятие.')
    return c.task(s['packId'],s['taskId'])

def advance(c,s,harder=False):
    task=task_for(c,s)
    if s['status']!='task_complete': raise ValueError('Сначала заверши задачу или закончи занятие.')
    done={r['taskId'] for r in s['results']}
    choices=[t for t in c.tasks(s['packId'],s['topic']) if t['id'] not in done and (not harder or t['difficulty']>task['difficulty'])]
    if not choices: raise ValueError('В этой теме больше нет подходящих задач. Можно выбрать другую тему.')
    s.update(taskId=choices[0]['id'],status='active',error=None,clarification=None,revision=s['revision']+1)

def intent(text,previous=''):
    t=re.sub(r'[?!.,;\\\s]+$','',str(text).lower().replace('ё','е')).strip()
    if re.fullmatch(r'(?:(?:давай|давайте)\s+)?(?:стоп|хватит|закончим|закончить(?: занятие)?|на сегодня хватит|я хочу закончить(?: занятие)?|не хочу(?: дальше| продолжать| решать| заниматься)?)',t): return 'stop'
    if t in ('нет','не','не надо') and re.search(r'(продолжим|хочешь продолжить|будем продолжать|хочешь еще одну задачу)[^?]*\?',previous.lower()): return 'stop'
    if not t or re.match(r'^(?:объясни|поясни|помоги|подскажи|нужна подсказка|непонятно)(?:\s|$)',t) or re.fullmatch(r'(?:(?:да|ну|я|вообще|пока|совсем|уже)\s+)*(?:не знаю|не понимаю|не понял(?:а)?|не могу(?: решить)?|не получается)(?:[, ]+(?:помоги|подскажи|объясни|что делать|как решать))?',t): return 'help'
    if re.match(r'^(?:в плане|в смысле|что (?:(?:это )?(?:значит|означает)|такое)|какие обе части|какие части|что за (?:части|число)|почему (?:получилось|получается)|откуда (?:взялось|взялись|получилось))(?:\s|$)',t): return 'clarification'
    return 'answer_or_other'

def reply(text,action='ask',assessment='not_assessed',assistance='none',**extra):
    return dict(text=text,action=action,assessment=assessment,assistance=assistance,visualId=None,**extra)

def practice(task,s,event,checked):
    if event['type']=='hint': return reply(task['teacher']['hints'][min(s['help'][task['id']]['hints']-1,2)],'hint',assistance='hint')
    if event['type']=='explain': return reply(task['teacher']['hints'][2],'explain',assistance='explanation')
    if checked['status']=='mismatch': return reply('Ответ пока не совпал с проверкой. Проверь расчёт или попроси подсказку.',assessment='incorrect')
    return reply('В режиме без ИИ я проверяю числа и известные формы ответа. Свободное рассуждение сейчас проверить не могу. '+checked.get('guidance',''))

def context(c,s,event,checked):
    pack=c.packs[s['packId']]; task=task_for(c,s)
    msgs=[m for m in s['messages'] if m['taskId']==s['taskId']]
    feedback=[m for m in msgs if m['role']=='assistant']
    struggling=len(feedback)>=2 and all(m.get('assessment')!='correct' and (m.get('assistance') in ('hint','explanation') or m.get('assessment')=='incorrect') for m in feedback[-2:])
    return {'subject':pack['subject'],'grade':pack['grade'],'task':task,'event':event,'check':checked,
            'help':s['help'].get(s['taskId'],{}),'results':s['results'],'messages':msgs[-24:],
            'learnerTurn':{'kind':intent(msgs[-1]['text'] if msgs else '')},'support':{'struggling':struggling},
            'repeated':s['taskId'] in s['previous'], 'knowledge':[k for k in pack.get('knowledge',[]) if s['topic'] in k['topics']]}

def respond(c,s,event,tutor,checkpoint,charge,on_wait=None):
    """Mutates one user's session. checkpoint persists BEFORE any paid request."""
    task=task_for(c,s); s.pop('notice',None)
    if event['type']=='finish' or (event['type']=='answer' and intent(event.get('text',''),next((m['text'] for m in reversed(s['messages']) if m['role']=='assistant'),''))=='stop'):
        s.update(status='ended',revision=s['revision']+1); return
    if s['status']!='active': raise ValueError('Задача уже завершена. Выбери следующую или другую тему.')
    retry=event['type']=='retry'
    if retry:
        if not s.get('error') or not s['error'].get('event'): raise ValueError('Нет запроса для повторения.')
        event={**s['error']['event'],'_inboxSeq':event.get('_inboxSeq')}
    elif event['type'] not in ('answer','hint','explain'): raise ValueError('Неизвестное действие.')
    if event['type']=='answer' and (not event.get('text','').strip() or len(event['text'])>4000): raise ValueError('Напиши ответ длиной до 4000 символов.')
    checked=check(task,event.get('text',''),s.get('clarification')) if event['type']=='answer' else {'status':'unknown'}
    exact=checked['status']=='match'
    ai=s['mode']=='ai' and tutor is not None and not exact and not checked.get('clarification')
    if ai and (s['requestCount']>=30 or not charge()):
        s['notice']='Лимит запросов GPT достигнут. Точный числовой ответ ещё можно проверить; продолжить диалог — после обновления лимита.'; return
    if not retry:
        h=s['help'].setdefault(task['id'],{'hints':0,'explained':False,'visual':False})
        if event['type']=='hint': h['hints']=min(3,h['hints']+1)
        if event['type']=='explain': h['explained']=True
        s['messages'].append({'role':'user','text':event.get('text') or ('Нужна подсказка' if event['type']=='hint' else 'Объясни проще'),'taskId':task['id'],'at':now()})
    s.update(error=None,revision=s['revision']+1)
    s['clarification']=checked.get('clarification')
    source='verification'
    if exact: r=reply('Верно. '+task['teacher']['solution'],'complete','correct')
    elif checked.get('clarification'): r=reply(checked['guidance'])
    elif not ai:
        r=practice(task,s,event,checked); source='practice'
    else:
        attempt={'id':uuid.uuid4().hex,'taskId':task['id'],'event':dict(event),'status':'pending','startedAt':now()}
        s['requestLog'].append(attempt); s['requestCount']+=1; s['pending']=attempt['id']
        checkpoint()
        if on_wait is not None:
            try: on_wait()
            except Exception: pass  # A missing status must not prevent the actual answer.
        try:
            r=tutor.reply(context(c,s,event,checked)); source='gpt'
        except Exception as exc:
            code=getattr(exc,'code','SERVICE')
            if code not in ('TIMEOUT','NETWORK','FORMAT','401','403','429','503'): code='SERVICE'
            attempt.update(status='failed',code=code,finishedAt=now()); s['pending']=None
            s['error']={'code':code,'event':dict(event),'message':'Не удалось получить ответ GPT. Твой ответ сохранён. Нажми «Повторить запрос» или попробуй другой ответ.'}
            return
        attempt.update(status='completed',finishedAt=now()); s['pending']=None
    turn=intent(event.get('text','')) if event['type']=='answer' else 'help'
    if r['action']=='stop': r=reply('Можно закончить занятие кнопкой «Закончить» или продолжить разбор.'); source='verification'
    if not exact and turn in ('help','clarification') and (r['action']=='complete' or r['assessment'] in ('correct','incorrect')):
        r={**r,'text':re.sub(r'^(?:Да|Верно|Правильно)[.!,:\s]+','',r['text'],flags=re.I) or 'Какой шаг нужно объяснить?', 'action':'explain','assessment':'not_assessed','assistance':'explanation'}
    if checked['status']=='mismatch' and (r['assessment']=='correct' or r['action']=='complete'):
        r=reply('Ответ пока не совпал с проверкой. Расскажи, как ты рассуждал.'); source='verification'
    if not exact and r['action']=='complete' and r['assessment']=='correct' and r['assistance']!='correction' and task['teacher']['validatorId'] in ('number-comparison','number-class','truth','algebra-value'):
        r=reply('Верно. '+task['teacher']['solution'],'complete','correct'); source='verification'
    support=context(c,s,event,checked)['support']
    if r['assessment']!='correct' and r['action']!='complete' and r['assistance']!='correction' and (event['type']=='explain' or (support['struggling'] and turn=='help')):
        prior=[m for m in s['messages'] if m['taskId']==task['id'] and 'recoveryStep' in m]
        index=prior[-1]['recoveryStep']+1 if prior else 0
        steps=task['teacher'].get('recoverySteps',[])
        if index<len(steps): r=reply(steps[index]['text'],'explain',assistance='explanation',recoveryStep=index); source='verification'
    s['messages'].append({'role':'assistant','taskId':task['id'],'source':source,'at':now(),**r})
    h=s['help'].setdefault(task['id'],{'hints':0,'explained':False,'visual':False})
    if r['assistance']=='hint': h['hints']=max(1,h['hints'])
    if r['assistance']=='explanation': h['explained']=True
    if r['assistance']=='correction': h['correction']=True
    if r.get('visualId') not in task.get('visualIds',[]): s['messages'][-1]['visualId']=None
    if r['action']=='complete' and r['assessment']=='correct' and checked['status']!='mismatch':
        level='review' if h.get('correction') else 'explained' if h['explained'] else 'hint' if h['hints'] or h['visual'] else 'independent'
        s['status']='task_complete'; s['results'].append({'taskId':task['id'],'level':level,'repeated':task['id'] in s['previous'],'at':now()})
