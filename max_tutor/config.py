import os
import re
from dataclasses import dataclass,field
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]

def env_file(path):
    if not Path(path).exists(): return {}
    result={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        key,sep,value=line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z_][A-Z0-9_]*',key.strip()): continue
        value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"): value=value[1:-1]
        result[key.strip()]=value
    return result

@dataclass(repr=False)
class Config:
    token:str=''
    key:str=''
    model:str=''
    reasoning:str=''
    webhook_secret:str=''
    public_url:str=''
    bot_name:str=''
    ca_file:str=''
    db:Path=ROOT/'.local/max-tutor/state.db'
    port:int=3212
    user_limit:int=30
    global_limit:int=100
    allowed:tuple=field(default_factory=tuple)
    @classmethod
    def from_env(cls,env=None):
        e={**env_file(ROOT/'.env'),**env_file(ROOT/'.env.max'),**os.environ} if env is None else env
        def number(key,default,maximum):
            try: v=int(e.get(key,default))
            except (ValueError,TypeError): raise ValueError('Некорректная настройка '+key) from None
            if not 1<=v<=maximum: raise ValueError('Некорректная настройка '+key)
            return v
        url=e.get('MAX_PUBLIC_URL','').rstrip('/')
        if url:
            parsed=urlsplit(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError('MAX_PUBLIC_URL должен быть адресом HTTPS без параметров.')
        name=e.get('MAX_BOT_NAME','').lstrip('@')
        if name and not re.fullmatch('[a-zA-Z0-9_]+',name): raise ValueError('Некорректный MAX_BOT_NAME')
        allowed=tuple(int(x.strip()) for x in e.get('MAX_ALLOWED_USERS','').split(',') if x.strip())
        if any(x<=0 for x in allowed): raise ValueError('Некорректный MAX_ALLOWED_USERS')
        return cls(token=e.get('MAX_BOT_TOKEN',''),key=e.get('OPENAI_API_KEY',''),model=e.get('OPENAI_MODEL',''),
                   reasoning=e.get('OPENAI_REASONING_EFFORT','low' if e.get('OPENAI_MODEL')=='gpt-6-luna' else ''),
                   webhook_secret=e.get('MAX_WEBHOOK_SECRET',''),public_url=url,bot_name=name,ca_file=e.get('MAX_CA_FILE',''),
                   db=Path(e.get('MAX_DATABASE',str(ROOT/'.local/max-tutor/state.db'))),port=number('MAX_PORT',3212,65535),
                   user_limit=number('MAX_USER_DAILY_LIMIT',30,100000),global_limit=number('MAX_GLOBAL_DAILY_LIMIT',100,100000),allowed=allowed)
