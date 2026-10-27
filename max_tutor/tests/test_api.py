import io
import json
import tempfile
import unittest
from pathlib import Path
from max_tutor.transport import MaxClient, APIError
from max_tutor.tutor import GPTTutor
from max_tutor.web import make_app
from max_tutor.config import Config
from max_tutor.storage import Store
from max_tutor.content import Catalog
from max_tutor.bot import Bot, chunks

class APITest(unittest.TestCase):
    def test_max_contract(self):
        calls=[]
        def request(method,url,headers,body,timeout): calls.append((method,url,headers,body)); return {}
        mx=MaxClient('token',request=request,pace=False)
        mx.send(123,{'text':'hello'}); mx.answer('callback'); mx.updates(42)
        self.assertEqual(calls[0][2]['Authorization'],'token')
        self.assertIn('platform-api2.max.ru/messages?user_id=123',calls[0][1])
        self.assertIn('callback_id=callback',calls[1][1]); self.assertIn('marker=42',calls[2][1])
        self.assertNotIn('token',calls[0][1])
    def test_gpt_schema_and_failure(self):
        calls=[]
        reply={'text':'Что нужно найти?','action':'ask','assessment':'not_assessed','assistance':'none','visualId':None,'misconception':None}
        def request(*args):
            calls.append(args); return {'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(reply)}]}],'usage':{}}
        tutor=GPTTutor('secret','configured-model',request=request)
        value=tutor.reply({'subject':'physics','messages':[],'task':{'visualIds':[]}})
        self.assertEqual(value['action'],'ask'); self.assertFalse(calls[0][3]['store'])
        self.assertTrue(calls[0][3]['text']['format']['strict'])
        reply['action']='hack'; self.assertRaises(APIError,tutor.reply,{'subject':'physics','messages':[],'task':{}})
    def test_chunks_and_config(self):
        self.assertTrue(all(len(x)<=3500 for x in chunks('я'*9000)))
        self.assertRaises(ValueError,Config.from_env,{'MAX_USER_DAILY_LIMIT':'0'})
        self.assertRaises(ValueError,Config.from_env,{'MAX_PUBLIC_URL':'http://example.com'})

class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(__file__).resolve().parents[2]
        self.store=Store(Path(self.tmp.name)/'web.db')
        self.bot=Bot(self.store,Catalog(root/'content/packs'),None)
        self.app=make_app(self.bot,'a-very-long-test-secret',root)
    def call(self,path,method='GET',data=b'',secret=''):
        status=[]
        env={'PATH_INFO':path,'REQUEST_METHOD':method,'CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data),'HTTP_X_MAX_BOT_API_SECRET':secret,'CONTENT_TYPE':'application/json'}
        body=b''.join(self.app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),body
    def test_webhook_auth_and_durable_ack(self):
        e={'update_type':'bot_started','timestamp':1,'user':{'user_id':123},'chat_id':123}
        raw=json.dumps(e).encode()
        self.assertEqual(self.call('/webhook','POST',raw)[0],403)
        self.assertEqual(self.call('/webhook','POST',raw,'a-very-long-test-secret')[0],200)
        self.assertIsNotNone(self.store.take())
    def test_invalid_and_duplicate_updates(self):
        self.assertEqual(self.call('/webhook','POST',b'[]','a-very-long-test-secret')[0],400)
        self.assertEqual(self.call('/webhook','POST',b'x'*70000,'a-very-long-test-secret')[0],413)
    def test_only_public_assets(self):
        self.assertEqual(self.call('/')[0],200)
        self.assertEqual(self.call('/demo-fractions.html')[0],200)
        for path in ['/.env','/../.env','/content/packs/heat-capacity.json','/api/users','/policy.json']:
            self.assertEqual(self.call(path)[0],404,path)

    def test_unhealthy_worker_rejects_webhook(self):
        self.bot.healthy=False
        self.assertEqual(self.call('/health')[0],503)
        self.assertEqual(self.call('/webhook','POST',b'{"update_type":"bot_started"}','a-very-long-test-secret')[0],503)

class KeyboardAPITest(unittest.TestCase):
    def test_edit_only_attachments_and_check_api_success(self):
        calls=[]
        def request(*args): calls.append(args); return {'success':True}
        mx=MaxClient('secret',request=request,pace=False)
        mx.edit_keyboard(123,'message-id',[])
        self.assertEqual(calls[0][0],'PUT')
        self.assertIn('/messages?message_id=message-id',calls[0][1])
        self.assertEqual(calls[0][3],{'attachments':[],'notify':False})
        mx.request=lambda *args:{'success':False}
        self.assertRaises(APIError,mx.edit_keyboard,123,'message-id',[])
