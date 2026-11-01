"""Build one portable submission ZIP from both published Windows delivery ZIPs."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import NamedTemporaryFile
import hashlib
import json
import os
import sys
import zipfile

ROOT = 'monitoringbot/'
PARTS = ('monitoringbot-part1.zip', 'monitoringbot-part2.zip')
FINAL = 'monitoringbot.zip'

def build(inputs, folder):
    output = folder / FINAL
    if output.exists():
        raise FileExistsError('The output already exists. Move it to another folder before rebuilding.')
    with ExitStack() as stack:
        parts, input_prefix = PARTS, ROOT
        if not any((inputs / name).exists() for name in parts):
            # The original immutable runtimes can also seed an initial build.
            parts = ('IncidentBridge-1.4.5-Windows-Part1.zip','IncidentBridge-1.4.5-Windows-Part2.zip')
            input_prefix = 'IncidentBridge-1.4.5/'
        original = [stack.enter_context(zipfile.ZipFile(inputs / name)) for name in parts]
        updates = stack.enter_context(zipfile.ZipFile(Path(__file__).with_name('package-files.zip')))
        manifest_name = ROOT + 'MANIFEST.json'
        manifest = updates.read(manifest_name)
        hashes = json.loads(manifest)['files']
        entries = {}
        for archive, prefix in [(part, input_prefix) for part in original] + [(updates, ROOT)]:
            for entry in archive.infolist():
                name=entry.filename
                if entry.is_dir() or not name.startswith(prefix):
                    continue
                relative=name[len(prefix):]
                if relative in hashes:
                    entries[ROOT + relative] = (archive, entry)
        if set(entries) != {ROOT + name for name in hashes}:
            raise ValueError('Input files do not match the manifest. Check both input ZIPs.')
        tmp = NamedTemporaryFile(prefix='monitoringbot-', suffix='.partial', dir=folder, delete=False)
        tmp.close()
        try:
            with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as result:
                for name, (archive, entry) in entries.items():
                    if entry.file_size > 100000000:
                        print('Packing ' + name + ' ...', flush=True)
                    meta = zipfile.ZipInfo(name, entry.date_time)
                    meta.compress_type = zipfile.ZIP_DEFLATED
                    meta.external_attr = entry.external_attr
                    meta.create_system = entry.create_system
                    digest = hashlib.sha256()
                    with archive.open(entry) as src, result.open(meta, 'w') as dst:
                        for block in iter(lambda: src.read(1024*1024), b''):
                            dst.write(block)
                            digest.update(block)
                    if digest.hexdigest() != hashes[name[len(ROOT):]]:
                        raise ValueError('Checksum mismatch: ' + name)
                result.writestr(manifest_name, manifest)
            with zipfile.ZipFile(tmp.name) as result:
                if len(result.namelist()) != len(hashes) + 1 or result.testzip() is not None:
                    raise ValueError('Output ZIP verification failed.')
            os.rename(tmp.name, output)
        finally:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
    print('Ready: ' + str(output), flush=True)

if __name__ == '__main__':
    try:
        build(Path(sys.argv[1]).resolve(), Path(__file__).resolve().parent)
    except Exception as exc:
        sys.exit('ERROR: ' + str(exc))
