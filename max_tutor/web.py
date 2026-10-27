"""WSGI ingress: validates and persists an event, never waits for GPT."""
import hmac
import json
import mimetypes
from pathlib import Path
from .content import VISUALS


def make_app(bot,secret,root):
    root=Path(root)
    assets={'/':root/'max_tutor/static/index.html','/mini.js':root/'max_tutor/static/mini.js','/mini.css':root/'max_tutor/static/mini.css'}
    assets.update({'/'+file:root/'web'/file for _,file in VISUALS.values()})
    assets['/shared.css']=root/'web/shared.css'
    def app(env,start):
        def send(code,body,kind='text/plain; charset=utf-8'):
            if isinstance(body,str): body=body.encode('utf-8')
            reasons={200:'OK',400:'Bad Request',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',413:'Payload Too Large',503:'Service Unavailable'}
            start(f'{code} {reasons[code]}',[('Content-Type',kind),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer')])
            return [body]
        path=env.get('PATH_INFO','/'); method=env.get('REQUEST_METHOD','GET')
        if path=='/webhook':
            if method!='POST': return send(405,'POST required')
            if not getattr(bot,'healthy',True): return send(503,'Worker unavailable')
            supplied=env.get('HTTP_X_MAX_BOT_API_SECRET','')
            if not secret or not hmac.compare_digest(secret.encode(),supplied.encode()): return send(403,'Forbidden')
            try: size=int(env.get('CONTENT_LENGTH','0'))
            except ValueError: return send(400,'Invalid length')
            if size<1 or size>65536: return send(413,'Invalid body size')
            if env.get('CONTENT_TYPE','').split(';')[0]!='application/json': return send(400,'JSON required')
            try:
                event=json.loads(env['wsgi.input'].read(size))
                if not isinstance(event,dict) or not isinstance(event.get('update_type'),str): return send(400,'Invalid event')
                bot.accept(event)
            except (ValueError,TypeError,AttributeError): return send(400,'Invalid event')
            except Exception: return send(503,'Could not persist event')
            return send(200,'{"ok":true}','application/json')
        if method!='GET': return send(405,'GET required')
        if path=='/health':
            healthy=getattr(bot,'healthy',True)
            return send(200 if healthy else 503,json.dumps({'ok':healthy}),'application/json')
        if path not in assets or not assets[path].is_file(): return send(404,'Not found')
        kind=mimetypes.guess_type(str(assets[path]))[0] or 'application/octet-stream'
        return send(200,assets[path].read_bytes(),kind+'; charset=utf-8')
    return app
