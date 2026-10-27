"""Build source ZIP from an allowlist; local data and real env files are excluded."""
from pathlib import Path
import hashlib
import zipfile
from check_release import ROOT,release_files,validate

files=release_files(); failures=validate(files)
if failures:
    for failure in failures: print(failure)
    raise SystemExit(1)
out=ROOT/'dist';out.mkdir(exist_ok=True)
archive=out/'repetitor-max.zip'
manifest=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(ROOT).as_posix()+'\n' for p in files)
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in files: z.write(p,'repetitor-max/'+p.relative_to(ROOT).as_posix())
    z.writestr('repetitor-max/SHA256SUMS.txt',manifest)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(out/'repetitor-max.zip.sha256').write_text(digest+'  repetitor-max.zip\n',encoding='ascii')
print('Built',archive,'files:',len(files)+1)
