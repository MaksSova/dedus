import unittest
from pathlib import Path
from max_tutor.content import Catalog
from max_tutor.checks import check, rational, arithmetic
ROOT = Path(__file__).resolve().parents[2]
class ChecksTest(unittest.TestCase):
    def setUp(self): self.c = Catalog(ROOT / 'content/packs')
    def test_catalog(self):
        self.assertEqual(len(self.c.packs), 10)
        self.assertEqual(set(self.c.subjects), {'physics','algebra'})
        self.assertEqual(sum(len(p['tasks']) for p in self.c.packs.values()),185)
        self.assertNotIn('teacher',self.c.public_task('heat-capacity','H01'))
    def test_fractions(self):
        t=self.c.task('algebra-real-numbers','R04')
        for a in ['7/9','1х = 7 деленное на 9','14/18']: self.assertEqual(check(t,a)['status'],'match',a)
        for a in ['0,(7)','7/9 потому что знаменатель не важен']: self.assertEqual(check(t,a)['status'],'unknown')
        self.assertEqual(rational('1,2/0,5'),rational('2,4'))
        self.assertIsNone(rational('1 2'))
    def test_properties(self):
        t=self.c.task('algebra-real-numbers','I01')
        for a,s in [('sqrt(2)','match'),('sqrt(4)','mismatch'),('0,(123)','mismatch')]: self.assertEqual(check(t,a)['status'],s)
    def test_safe_arithmetic(self):
        self.assertEqual(arithmetic('(100+100+100+20):4'),80)
        for a in ['__import__("os")','2**1000000','1/0','('*1000,'1e100000']: self.assertIsNone(arithmetic(a))
    def test_mass(self):
        t=next(t for t in self.c.packs['heat-capacity']['tasks'] if t['teacher']['expected'].get('unit')=='kg')
        c=check(t,str(t['teacher']['expected']['value']*1000))
        self.assertEqual(c['status'],'unknown')
        self.assertEqual(check(t,'граммов',c['clarification'])['status'],'match')
        self.assertEqual(check(t,'граммов')['status'],'unknown')
    def test_reviewed(self):
        for p in self.c.packs.values():
            for t in p['tasks']:
                if t['teacher']['validatorId']=='reviewed-answer':
                    for a in t['teacher']['expected']['answers']: self.assertEqual(check(t,a)['status'],'match',(t['id'],a))
                if t['teacher']['validatorId']=='reasoning': self.assertEqual(check(t,t['teacher']['solution'])['status'],'unknown')

    def test_existing_chapter_fixtures(self):
        import json
        for path in (ROOT/'tests/fixtures').glob('chapter-*-cases.json'):
            for case in json.loads(path.read_text(encoding='utf-8-sig')):
                self.assertEqual(check(self.c.task(case['packId'],case['taskId']),case['input'])['status'],case['status'],case)
