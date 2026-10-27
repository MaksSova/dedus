import copy
import json
from pathlib import Path

SUBJECT_NAMES = {'physics': 'Физика', 'algebra': 'Алгебра', 'geometry': 'Геометрия'}
VISUALS = {
    'heat-proportions': ('Теплоёмкость', 'demo-heat.html'),
    'thermal-balance': ('Тепловой баланс', 'demo-mixing.html'),
    'decimal-period': ('Периодические дроби', 'demo-decimals.html'),
    'function-graphs': ('Графики функций', 'demo-functions.html'),
    'fraction-factors': ('Сокращение дробей', 'demo-fractions.html'),
}

class Catalog:
    def __init__(self, directory):
        self.packs = {}
        for path in sorted(Path(directory).glob('*.json')):
            pack = json.loads(path.read_text(encoding='utf-8-sig'))
            if pack.get('schemaVersion') != 1 or pack['id'] in self.packs:
                raise ValueError('Неподдерживаемый или повторный учебный набор')
            pack['tasks'] = [t for t in pack['tasks'] if t.get('status') == 'reviewed']
            if len({t['id'] for t in pack['tasks']}) != len(pack['tasks']):
                raise ValueError('Повторный ID задачи')
            for task in pack['tasks']:
                if not all(k in task for k in ('teacher','public','topic','source')):
                    raise ValueError('Неполная карточка задачи')
            self.packs[pack['id']] = pack
        if not self.packs:
            raise ValueError('Нет проверенных учебных наборов')
        self.subjects = {p['subject']: SUBJECT_NAMES.get(p['subject'], p.get('subjectTitle', p['subject'])) for p in self.packs.values()}

    def task(self, pack_id, task_id):
        return next(t for t in self.packs[pack_id]['tasks'] if t['id'] == task_id)

    def tasks(self, pack_id, topic):
        return [t for t in self.packs[pack_id]['tasks'] if t['topic'] == topic]

    def public_task(self, pack_id, task_id):
        task = self.task(pack_id, task_id)
        return copy.deepcopy({'id': task['id'], **task['public'], 'source': task['source']})
