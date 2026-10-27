const demos={
 'heat-proportions':['physics','Теплоёмкость','demo-heat.html'],
 'thermal-balance':['physics','Тепловой баланс','demo-mixing.html'],
 'decimal-period':['algebra','Периодические дроби','demo-decimals.html'],
 'function-graphs':['algebra','Графики функций','demo-functions.html'],
 'fraction-factors':['algebra','Сокращение дробей','demo-fractions.html']
};
let current='heat-proportions';
function choose(id){
 if(!Object.hasOwn(demos,id))return;
 current=id;const [subject,title,file]=demos[id];
 document.getElementById('demo').src=file;
 document.getElementById('demo').title=title;
 document.getElementById('demo-title').textContent=title;
 for(const s of ['physics','algebra'])document.getElementById(s).classList.toggle('selected',s===subject);
 const choices=document.getElementById('choices');choices.replaceChildren();
 for(const [key,[s,t]] of Object.entries(demos))if(s===subject){const b=document.createElement('button');b.textContent=t;b.classList.toggle('selected',key===id);b.onclick=()=>choose(key);choices.append(b);}
}
for(const s of ['physics','algebra'])document.getElementById(s).onclick=()=>choose(Object.keys(demos).find(k=>demos[k][0]===s));
// Start parameters only select a PUBLIC demo, never authorize access to pupil data.
const raw=new URLSearchParams(location.hash.slice(1)).get('WebAppData') || '';
const start=window.WebApp?.initDataUnsafe?.start_param || new URLSearchParams(raw).get('start_param') || new URLSearchParams(location.search).get('visual');
choose(Object.hasOwn(demos,start)?start:current);
window.WebApp?.ready?.();
