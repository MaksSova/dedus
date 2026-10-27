"""Validate only explicitly publishable files; never inspect local credentials."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'README.md','.env.example','.gitignore','.gitattributes','requirements.txt','Start-MaxTutor.ps1'}
PATTERNS=('max_tutor/*.py','max_tutor/policy.json','max_tutor/tests/*.py','max_tutor/static/*.html','max_tutor/static/*.css','max_tutor/static/*.js','content/packs/*.json','tests/fixtures/chapter-*-cases.json','web/demo-*.html','web/shared.css','docs/*.md','docs/assets/*.png','docs/assets/*-prompt.txt','docs/screenshots/*.md','docs/screenshots/*.png','docs/screenshots/*.jpg','docs/screenshots/*.jpeg','certs/russian-trusted-root.pem','certs/SOURCE.txt','scripts/*.py')

def release_files():
    files={ROOT/name for name in ROOT_FILES if (ROOT/name).is_file()}
    for pattern in PATTERNS: files.update(p for p in ROOT.glob(pattern) if p.is_file())
    return sorted(files)

def validate(files):
    failures=[]
    for name in ROOT_FILES:
        if not (ROOT/name).is_file(): failures.append('Missing required file: '+name)
    for p in files:
        rel=p.relative_to(ROOT).as_posix()
        if p.is_symlink(): failures.append('Symlink not allowed: '+rel); continue
        if p.suffix.lower() in ('.png','.jpg','.jpeg'): continue
        text=p.read_text(encoding='utf-8-sig')
        # Report paths only, never matching credential text.
        patterns=[r'\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}',r'\bgh[pousr]_[A-Za-z0-9]{20,}',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'[A-Z]:[\\/]Users[\\/]']
        if any(re.search(pattern,text) for pattern in patterns): failures.append('Sensitive-looking data: '+rel)
        if p.name=='.env.example':
            for line in text.splitlines():
                key,sep,value=line.partition('=')
                if sep and key.strip() in ('MAX_BOT_TOKEN','OPENAI_API_KEY','MAX_WEBHOOK_SECRET') and value.strip(): failures.append('Nonempty secret placeholder: '+rel)
    return failures

if __name__=='__main__':
    files=release_files(); failures=validate(files)
    for failure in failures: print(failure)
    if failures: raise SystemExit(1)
    print('Publishable files:',len(files))
    print('Allowlist and text credential checks passed. Review screenshot privacy manually.')
