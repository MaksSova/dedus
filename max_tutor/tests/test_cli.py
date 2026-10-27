import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
class CLITest(unittest.TestCase):
    def test_demo_without_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'report.json'
            run=subprocess.run([sys.executable,'-m','max_tutor','demo','--output',str(out)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(run.returncode,0,run.stderr)
            data=json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(data['gpt_calls'],0)
            self.assertEqual(data['lesson']['results'][0]['level'],'independent')
            self.assertEqual(data['lesson']['status'],'ended')
    def test_help(self):
        run=subprocess.run([sys.executable,'-m','max_tutor','--help'],cwd=ROOT,capture_output=True)
        self.assertEqual(run.returncode,0)

    def test_subscribe_false_does_not_claim_success(self):
        from unittest.mock import patch
        from max_tutor.__main__ import main
        from max_tutor.config import Config
        from max_tutor.transport import APIError
        cfg=Config(token='test',public_url='https://example.test',webhook_secret='1234567890123456')
        with patch('max_tutor.__main__.Config.from_env',return_value=cfg),patch('max_tutor.__main__.MaxClient') as client:
            client.return_value.subscribe.return_value={'success':False,'message':'private upstream details'}
            self.assertRaises(APIError,main,['subscribe'])
