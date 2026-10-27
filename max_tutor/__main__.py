"""CLI: python -m max_tutor --help. All demo commands are offline by default."""
import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler
from .bot import Bot
from .config import Config,ROOT
from .content import Catalog
from .storage import Store
from .transport import MaxClient,APIError
from .tutor import GPTTutor
from .web import make_app

class ThreadedServer(ThreadingMixIn,WSGIServer): daemon_threads=True
class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args): pass

@contextmanager
def single_worker(path):
    lock=Path(str(path)+'.lock'); lock.parent.mkdir(parents=True,exist_ok=True)
    f=lock.open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            if f.seek(0,2)==0: f.write(b'0'); f.flush()
            f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        f.close(); raise ValueError('Эта база уже используется другим процессом бота.') from None
    try: yield
    finally: f.close()

class ConsoleMax:
    def __init__(self,quiet=False): self.sent=[]; self.quiet=quiet
    def send(self,uid,body):
        self.sent.append((uid,body))
        if not self.quiet:
            print('\nРЕПЕТИТОР:',body['text'])
            for i,b in enumerate(self.buttons(),1): print(f"  {i}. {b['text']}")
        return {'message':{'body':{'mid':str(len(self.sent))}}}
    def buttons(self):
        return [b for a in (self.sent[-1][1].get('attachments',[]) if self.sent else []) for row in a['payload']['buttons'] for b in row]
    def waiting(self,uid):
        if not self.quiet: print('РЕПЕТИТОР: Пишу…',flush=True)
    def edit_keyboard(self,uid,mid,attachments): pass
    def answer(self,callback): pass

class Simulation:
    def __init__(self,bot,mx): self.bot=bot; self.mx=mx; self.n=0
    def text(self,text):
        self.n+=1
        e={'update_type':'message_created','timestamp':self.n,'message':{'sender':{'user_id':1},'recipient':{'chat_type':'dialog','chat_id':1},'body':{'mid':'sim-'+str(self.n),'text':text}}}
        self.run(e)
    def click(self,button):
        self.n+=1
        if button['type']!='callback': print('Открыть:',button.get('url',button.get('payload'))); return
        self.run({'update_type':'message_callback','timestamp':self.n,'callback':{'callback_id':'sim-'+str(self.n),'user':{'user_id':1},'payload':button['payload']}})
    def label(self,label): self.click(next(b for b in self.mx.buttons() if label in b['text']))
    def run(self,e): self.bot.accept(e); self.bot.tick(); self.bot.flush()

def worker(bot,stop):
    while not stop.is_set():
        try:
            worked=bot.tick()
            if bot.healthy: bot.flush()
        except Exception as exc:
            bot.healthy=False
            print('Обработка остановлена:',type(exc).__name__, 'Проверьте диск и перезапустите бота.',file=sys.stderr)
            stop.set(); return
        if not worked: stop.wait(.2)

def main(argv=None):
    parser=argparse.ArgumentParser(description='Репетитор MAX: физика и алгебра')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('check-token',help='Проверить MAX-токен через GET /me')
    sub.add_parser('poll',help='Локальный тест бота через Long Polling')
    sub.add_parser('serve',help='Webhook и мини-приложение, за HTTPS-прокси')
    sub.add_parser('preview',help='Только локальный просмотр мини-приложения, без ключей')
    sub.add_parser('subscribe',help='Зарегистрировать HTTPS webhook в MAX')
    p=sub.add_parser('console',help='Диалог в терминале без MAX'); p.add_argument('--ai',action='store_true',help='Явно включить платные запросы GPT')
    p=sub.add_parser('demo',help='Автоматическое занятие без API'); p.add_argument('--output',default=str(ROOT/'docs/max-bot-demo.json'))
    p=sub.add_parser('export',help='Локальный отчёт одного ученика'); p.add_argument('user_id',type=int); p.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    catalog=Catalog(ROOT/'content/packs')
    if args.command=='demo':
        with tempfile.TemporaryDirectory() as d:
            mx=ConsoleMax(quiet=True); store=Store(Path(d)/'demo.db'); bot=Bot(store,catalog,mx); sim=Simulation(bot,mx)
            sim.text('/start'); sim.label('Алгебра'); sim.label('рациональные и иррациональные'); sim.label('Рациональные'); sim.text('3'); sim.label('Следующая'); sim.label('Подсказка'); sim.text('рациональное'); sim.text('/finish')
            out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps({'gpt_calls':0,'lesson':store.user(1)['session'],'messages':[b for _,b in mx.sent]},ensure_ascii=False,indent=2),encoding='utf-8')
            print('Демонстрационное занятие пройдено. Отчёт:',out)
        return
    cfg=Config.from_env() if args.command!='preview' else Config()
    if args.command=='export':
        data=Store(cfg.db).export(args.user_id)
        Path(args.output).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); print('Отчёт сохранён.'); return
    if args.command in ('check-token','subscribe'):
        mx=MaxClient(cfg.token,cafile=cfg.ca_file or None)
        if args.command=='check-token':
            data=mx.info(); print(json.dumps({k:data.get(k) for k in ('user_id','name','username','is_bot')},ensure_ascii=False)); return
        if not cfg.public_url or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}',cfg.webhook_secret): raise ValueError('Нужны MAX_PUBLIC_URL и MAX_WEBHOOK_SECRET (16–256 букв, цифр, _ или -).')
        response=mx.subscribe(cfg.public_url+'/webhook',cfg.webhook_secret)
        if response.get('success') is not True: raise APIError('SUBSCRIPTION_REJECTED')
        print('Webhook зарегистрирован.'); return
    if args.command=='preview':
        with tempfile.TemporaryDirectory() as d:
            bot=Bot(Store(Path(d)/'preview.db'),catalog,ConsoleMax(True))
            with make_server('127.0.0.1',cfg.port,make_app(bot,'',ROOT),ThreadedServer,QuietHandler) as server:
                print(f'Наглядные примеры: http://127.0.0.1:{cfg.port}/',flush=True)
                server.serve_forever()
        return
    ai=GPTTutor(cfg.key,cfg.model,reasoning=cfg.reasoning) if cfg.key and cfg.model and (args.command!='console' or args.ai) else None
    if args.command=='console':
        with tempfile.TemporaryDirectory() as d:
            mx=ConsoleMax(); bot=Bot(Store(Path(d)/'console.db'),catalog,mx,ai); sim=Simulation(bot,mx)
            print('Тестовый диалог. :1 — нажать первую кнопку; /exit — выход. История временная.'); sim.text('/start')
            while True:
                value=input('\nТЫ: ')
                if value=='/exit': return
                if re.fullmatch(r':\d+',value):
                    buttons=mx.buttons(); index=int(value[1:])-1
                    if 0<=index<len(buttons): sim.click(buttons[index])
                    else: print('Такой кнопки нет.')
                else: sim.text(value)
    mx=MaxClient(cfg.token,cafile=cfg.ca_file or None)
    if args.command=='serve' and not re.fullmatch(r'[A-Za-z0-9_-]{16,256}',cfg.webhook_secret): raise ValueError('Задайте MAX_WEBHOOK_SECRET длиной 16–256 символов.')
    if args.command=='poll' and mx.subscriptions().get('subscriptions'): raise ValueError('У бота уже есть Webhook. Используйте serve; удалять подписку автоматически не будем.')
    with single_worker(cfg.db):
        store=Store(cfg.db); store.recover()
        bot=Bot(store,catalog,mx,ai,allowed=cfg.allowed,public_url=cfg.public_url,bot_name=cfg.bot_name,user_limit=cfg.user_limit,global_limit=cfg.global_limit)
        stop=threading.Event(); thread=threading.Thread(target=worker,args=(bot,stop),daemon=True); thread.start()
        try:
            if args.command=='serve':
                with make_server('127.0.0.1',cfg.port,make_app(bot,cfg.webhook_secret,ROOT),ThreadedServer,QuietHandler) as server:
                    print(f'Webhook и мини-приложение: 127.0.0.1:{cfg.port}. Для MAX нужен HTTPS-прокси.',flush=True)
                    server.serve_forever()
            else:
                print('Бот запущен для теста. Остановить: Ctrl+C.',flush=True)
                while not stop.is_set():
                    try: data=mx.updates(store.meta('poll_marker'))
                    except APIError as exc:
                        if exc.code in ('401','403'): raise
                        print('MAX временно недоступен:',exc.code); stop.wait(3); continue
                    for event in data.get('updates',[]): bot.accept(event)
                    if data.get('marker') is not None: store.meta('poll_marker',data['marker'])
                    stop.wait(.35)
        finally:
            stop.set(); thread.join(timeout=45)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    try: main()
    except KeyboardInterrupt: print('\nОстановлено. Сохранённое занятие можно продолжить.')
    except (ValueError,APIError) as exc: print(str(exc),file=sys.stderr); sys.exit(1)
