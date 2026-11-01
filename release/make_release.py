"""Make a Windows builder from this source checkout and immutable asset checksums."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'monitoringbot/'


def files(folder):
    for p in folder.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            yield p.relative_to(folder).as_posix(), p.read_bytes()


def make(dest):
    dest.mkdir(parents=True, exist_ok=True)
    runtime = dict(files(ROOT/'app'))
    runtime.update(dict(files(ROOT/'windows')))
    runtime.update({'qa/'+name:payload for name,payload in files(ROOT/'qa')})
    for n in ['REQUIREMENTS-TRACE.md','monitoringbot-requirements.md']:
        runtime[n]=(ROOT/n).read_bytes()
    runtime['VERIFICATION.txt']=(ROOT/'VERIFICATION.txt').read_bytes()
    hashes=json.loads((ROOT/'release/bundled-files.json').read_text())
    hashes.update({name:hashlib.sha256(payload).hexdigest() for name,payload in runtime.items()})
    manifest = {'version':'1.4.6','files':hashes}
    runtime['MANIFEST.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    with zipfile.ZipFile(dest/'package-files.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name,payload in runtime.items():z.writestr(PREFIX+name,payload)
    for n in ['MAKE-ZIP.cmd','build_zip.py']:
        shutil.copyfile(ROOT/'release'/n,dest/n)
    shutil.copyfile(ROOT/'release/BUILDER-KO.txt',dest/'README_KO.txt')
    kit = dest/'monitoringbot-builder.zip'
    with zipfile.ZipFile(kit,'w',zipfile.ZIP_DEFLATED) as z:
        for n in ['MAKE-ZIP.cmd','build_zip.py','package-files.zip','README_KO.txt']:
            z.write(dest/n,n)
    return kit

if __name__=='__main__':
    print(make(Path(sys.argv[1]).resolve()))
