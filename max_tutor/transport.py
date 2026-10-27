"""Explicit HTTP clients. Credentials are headers; redirects and retries are disabled."""
import json
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

class APIError(Exception):
    def __init__(self,code): self.code=str(code); super().__init__('Ошибка внешнего сервиса: '+self.code)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def http_request(method,url,headers,body=None,timeout=40,*,cafile=None):
    context=ssl.create_default_context()
    if cafile: context.load_verify_locations(cafile=cafile)
    opener=build_opener(HTTPSHandler(context=context),NoRedirect())
    data=None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8')
    req=Request(url,data=data,headers={**headers,'Content-Type':'application/json'},method=method)
    try:
        with opener.open(req,timeout=timeout) as res:
            raw=res.read(2_000_001)
            if len(raw)>2_000_000: raise APIError('FORMAT')
            value=json.loads(raw)
            if not isinstance(value,dict): raise APIError('FORMAT')
            return value
    except HTTPError as exc: raise APIError(exc.code) from None
    except (TimeoutError,socket.timeout): raise APIError('TIMEOUT') from None
    except URLError as exc: raise APIError('TIMEOUT' if isinstance(exc.reason,socket.timeout) else 'NETWORK') from None
    except (ValueError,UnicodeError): raise APIError('FORMAT') from None

class MaxClient:
    def __init__(self,token,*,request=None,cafile=None,pace=True):
        if not token: raise ValueError('Укажите MAX_BOT_TOKEN в .env.max')
        self.token=token; self.pace=pace; self.last={}; self.lock=threading.Lock()
        self.request=request or (lambda method,url,headers,body,timeout:http_request(method,url,headers,body,timeout,cafile=cafile))
    def call(self,method,path,query=None,body=None,timeout=40):
        url='https://platform-api2.max.ru'+path+('?' + urlencode({k:v for k,v in (query or {}).items() if v is not None}) if query else '')
        return self.request(method,url,{'Authorization':self.token},body,timeout)
    def paced_call(self,uid,method,path,query,body,timeout=40):
        with self.lock:
            if self.pace:
                delay=max(0,self.last.get(uid,0)+.55-time.monotonic())
                if delay: time.sleep(delay)
            self.last[uid]=time.monotonic()
            return self.call(method,path,query,body,timeout=timeout)
    def send(self,uid,body):
        return self.paced_call(uid,'POST','/messages',{'user_id':uid},body)
    def waiting(self,uid):
        # Ephemeral notice: never queue/retry it after the final answer.
        return self.paced_call(uid,'POST','/messages',{'user_id':uid},{'text':'Пишу…','notify':False},timeout=5)
    def edit_keyboard(self,uid,mid,attachments):
        result=self.paced_call(uid,'PUT','/messages',{'message_id':mid},{'attachments':attachments,'notify':False})
        if result.get('success') is not True: raise APIError('EDIT_REJECTED')
    def answer(self,callback): return self.call('POST','/answers',{'callback_id':callback},{'notification':'Принято'},timeout=5)
    def updates(self,marker): return self.call('GET','/updates',{'marker':marker,'timeout':30,'types':'bot_started,message_created,message_callback'},timeout=40)
    def info(self): return self.call('GET','/me')
    def subscriptions(self): return self.call('GET','/subscriptions')
    def subscribe(self,url,secret): return self.call('POST','/subscriptions',body={'url':url,'secret':secret,'update_types':['bot_started','message_created','message_callback']})
