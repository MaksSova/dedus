"""Conservative exact checks. Unknown notation is delegated, never guessed."""
import ast
import math
import re
from fractions import Fraction


def unknown(guidance='Разберём запись вместе.', **extra):
    return {'status':'unknown', 'guidance':guidance, **extra}

def result(ok):
    return {'status':'match' if ok else 'mismatch'}

def rational(text):
    if not isinstance(text,str) or len(text)>120 or re.search(r'\d\s+\d',text): return None
    t=re.sub(r'\s','',text).replace(',','.').replace('−','-')
    if t.count('/')==1:
        a,b=map(rational,t.split('/'))
        return a/b if a is not None and b else None
    m=re.fullmatch(r'([+-]?)(\d+)(?:\.(\d*)(?:\((\d+)\))?)?',t)
    if not m or t.endswith('.'): return None
    sign,whole,digits,cycle=m.groups(); digits=digits or ''
    value=Fraction(int(whole+digits),10**len(digits))
    if cycle: value+=Fraction(int(cycle),(10**len(cycle)-1)*10**len(digits))
    return -value if sign=='-' else value

def arithmetic(text):
    if not isinstance(text,str) or len(text)>256: return None
    t=text.translate(str.maketrans({',':'.','×':'*','·':'*','÷':'/',':':'/','−':'-'}))
    t=re.sub(r'\s','',t)
    if not t or re.search(r'[^\d.()+*/-]',t) or '**' in t or '//' in t: return None
    try:
        tree=ast.parse(t,mode='eval')
        def walk(n,depth=0):
            if depth>20: raise ValueError()
            if isinstance(n,ast.Constant) and type(n.value) in (int,float): return Fraction(str(n.value))
            if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
                v=walk(n.operand,depth+1); return -v if isinstance(n.op,ast.USub) else v
            if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
                a,b=walk(n.left,depth+1),walk(n.right,depth+1)
                v=a+b if isinstance(n.op,ast.Add) else a-b if isinstance(n.op,ast.Sub) else a*b if isinstance(n.op,ast.Mult) else a/b
                if abs(v)>10**15: raise ValueError()
                return v
            raise ValueError()
        v=walk(tree.body)
        return float(v) if abs(v)<=10**15 else None
    except (ValueError,TypeError,SyntaxError,RecursionError,ZeroDivisionError,OverflowError): return None

def notation(s):
    s=s.strip().rstrip('.').strip().translate(str.maketrans({'−':'-','×':'*','·':'*','²':'^2','³':'^3','≤':'<=','≥':'>=','≠':'!='}))
    s=re.sub(r'√\s*(\d+(?:[.,]\d+)?)',r'sqrt(\1)',s)
    s=re.sub(r'√\s*\(', 'sqrt(', s)
    s=re.sub(r'\s*([=<>!+\-*/^();\[\]{}∈])\s*',r'\1',s)
    return re.sub(r'\s+',' ',s)

def kind(t):
    if rational(t) is not None: return 'rational'
    if re.fullmatch(r'[+-]?(π|пи|pi)',t): return 'irrational'
    m=re.fullmatch(r'[+-]?(?:sqrt\((.+)\)|√\s*(?:\(([^()]+)\)|([^()]+)))',t)
    if not m: return None
    v=rational(next(g for g in m.groups() if g is not None))
    if v is None or v<0: return None
    return 'rational' if math.isqrt(v.numerator)**2==v.numerator and math.isqrt(v.denominator)**2==v.denominator else 'irrational'

def check(task,text,clarification=None):
    if not isinstance(text,str) or len(text)>4000: return unknown()
    e=task['teacher']['expected']; validator=task['teacher']['validatorId']
    t=text.lower().strip().replace('−','-').rstrip('.;!').strip()
    if validator=='reasoning': return unknown('Здесь нужно обоснование, а не только итог.')
    if validator=='reviewed-answer':
        return result(True) if len(text)<=1000 and notation(text) in [notation(a) for a in e['answers']] else unknown()
    if validator=='number-class':
        k='irrational' if re.fullmatch(r'иррациональн(?:ое|ый|ая)(?: число)?',t) else 'rational' if re.fullmatch(r'(?:рациональн(?:ое|ый|ая)(?: число)?|q)',t) else None
        return result(k==e['kind']) if k else unknown()
    if validator=='number-example':
        k=kind(t); return result(k==e['kind']) if k else unknown()
    if validator=='algebra-value':
        if e.get('form')=='fraction':
            m=re.fullmatch(r'(?:(?:1\s*)?[xх]\s*=\s*)?([+-]?\s*\d+)\s*(?:/|дел[её]нное\s+на|делить\s+на)\s*([+-]?\s*\d+)',t)
            if not m: return unknown('Нужна обыкновенная дробь, например числитель/знаменатель.')
            t=re.sub(r'\s','',m[1]+'/'+m[2])
        a,b=rational(t),rational(e['value'])
        return result(a==b) if a is not None and b is not None else unknown()
    if validator=='number-comparison':
        rel={'>':'>','<':'<','=':'=','больше':'>','меньше':'<','равны':'=','равно':'=','первое больше':'>','второе больше':'<','первое меньше':'<','первое':'>','второе':'<'}
        return result(rel[t]==e['relation']) if t in rel else unknown('Сравни первое число со вторым: >, < или =.')
    if validator=='truth':
        values={'да':True,'верно':True,'истина':True,'нет':False,'неверно':False,'ложь':False}
        return result(values[t]==e['value']) if t in values else unknown()
    if validator=='numeric':
        def mass(s):
            if re.fullmatch(r'(?:в )?(?:г|грамм(?:а|ов|ы|ах)?)',s): return 'г'
            if re.fullmatch(r'(?:в )?(?:кг|килограмм(?:а|ов|ы|ах)?)',s): return 'кг'
        u=mass(t)
        if e['unit']=='kg' and u and clarification and clarification.get('taskId')==task['id']:
            t=f"{clarification['value']} {u}"
        t=re.sub(r'килограмм(?:а|ов|ы|ах)?$', 'кг',t)
        t=re.sub(r'грамм(?:а|ов|ы|ах)?$', 'г',t)
        m=re.fullmatch(r'(.*?)(кдж|дж|кг|г|°\s*[cс]|градус(?:а|ов)?|дж\s*/\s*\(кг[·*\s]*°?[cс]\))?',t)
        if not m: return unknown()
        v=arithmetic(m[1]); u=re.sub(r'\s','',m[2] or '')
        if v is None: return unknown()
        if e['unit']=='kg' and not u: return unknown('Это граммы или килограммы? Можно написать только единицу.',clarification={'kind':'unit','taskId':task['id'],'value':v})
        units={'J':{'дж':1,'кдж':1000},'kg':{'кг':1,'г':.001},'C':{'°c':1,'°с':1,'градус':1,'градуса':1,'градусов':1},'specific':{f'дж/(кг{sep}{deg}{c})':1 for sep in ('','·','*') for deg in ('','°') for c in ('c','с')}}
        if u and u not in units.get(e['unit'],{}): return unknown('Проверь единицу измерения.')
        return result(abs(v*units.get(e['unit'],{}).get(u,1)-e['value'])<=e.get('tolerance',1e-6))
    if validator=='ratio':
        words=dict(zip(['один','два','две','три','четыре','пять','шесть','семь','восемь','девять','десять'],['1','2','2','3','4','5','6','7','8','9','10']))
        t=re.sub('[а-яё]+',lambda m:words.get(m[0],m[0]),t)
        numbered=e['direction'] in ('first','second')
        compact=re.fullmatch(r'([12])(?:\s*,\s*|\s+)(\d+(?:[.,]\d+)?)',t) if numbered else None
        if compact: t=f"у {'первого' if compact[1]=='1' else 'второго'} в {compact[2]} раза"
        m=re.fullmatch(r'(?:(?:у|на|для)\s+)?(первого|первой|первый|первое|второго|второй|второе|воды|воду|вода|кастрюли|кастрюлю|кастрюля)(?:\s+тела)?(?:\s*,\s*|\s+)(?:в\s+)?(\d+(?:[.,]\d+)?)\s+раз(?:а)?(?:\s+(больше|меньше))?',t)
        if not m: return unknown('Укажи, у какого тела больше и во сколько раз.')
        d='first' if m[1].startswith('перв') else 'second' if m[1].startswith('втор') else 'water' if m[1].startswith('вод') else 'pan'
        if numbered != (d in ('first','second')): return unknown()
        if m[3]=='меньше': d={'first':'second','second':'first','water':'pan','pan':'water'}[d]
        v=float(m[2].replace(',','.'))
        return result(d==e['direction'] and abs(v-e['value'])<=e.get('tolerance',.001)) if v>=1 else unknown()
    if validator=='tuple':
        m=re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(кг|г)\s*(горячей|холодной)(?:\s+воды)?(?:\s*[,;]\s*|\s+и\s+|\s+)(\d+(?:[.,]\d+)?)\s*(кг|г)\s*(горячей|холодной)(?:\s+воды)?',t)
        if not m or m[3]==m[6]: return unknown('Подпиши массы горячей и холодной воды.')
        values={m[i+2]:float(m[i].replace(',','.'))*(.001 if m[i+1]=='г' else 1) for i in (1,4)}
        return result(abs(values['горячей']-e['hot'])<1e-6 and abs(values['холодной']-e['cold'])<1e-6)
    return unknown('Эту запись проверит репетитор.')
