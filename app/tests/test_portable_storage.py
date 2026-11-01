import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

win=Path(__file__).resolve().parents[2]/'windows'
if win.is_dir():sys.path.insert(0,str(win))
import portable_bootstrap as boot

class PortableStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parent = Path(self.temp.name)
        self.root = parent / '바코챌 시연, PC' / 'IncidentBridge-1.3'
        self.root.mkdir(parents=True)
        self.old = parent / 'old'
        self.old.mkdir()
        (self.old / 'portable.lock').write_bytes(b'0')
        (self.old / 'server.qcow2').write_bytes(b'persisted guest')
        self.ports = patch.object(boot, 'check_ports')
        self.ports.start()
        self.addCleanup(self.ports.stop)

    def test_fresh_launch_and_process_storage_are_local(self):
        data = boot.Sessions(self.root).create()
        with patch.dict(os.environ,{'BRIDGE_SQLITE_PATH':'elsewhere.db'}), patch.object(tempfile,'tempdir',None):
            boot.configure_storage(data)
            self.assertEqual(tempfile.gettempdir(),str(data/'temp'))
            self.assertEqual(os.environ['BRIDGE_SQLITE_PATH'],str(data/'incidents.db'))
        self.assertFalse((data/'server.qcow2').exists())

    def test_firmware_corruption_and_missing_file_are_identified(self):
        data = boot.Sessions(self.root).create()
        share = self.root/'source'
        share.mkdir()
        with self.assertRaisesRegex(RuntimeError,'bios-256k.bin 읽기 실패'):
            boot.checked_firmware(share,data)
        (share/'bios-256k.bin').write_bytes(b'broken')
        with self.assertRaisesRegex(RuntimeError,'bios-256k.bin 무결성'):
            boot.checked_firmware(share,data)

    @unittest.skipIf(os.name=='nt','POSIX probe process for the QEMU argument contract')
    def test_launcher_child_opens_real_bios_disk_and_serial_under_korean_path(self):
        if not (boot.ROOT/'server/qemu/share').is_dir(): self.skipTest('Needs packaged ROMs')
        share = self.root/'server/qemu/share'
        shutil.copytree(boot.ROOT/'server/qemu/share',share)
        data=boot.Sessions(self.root).create()
        (self.root/'server/base.qcow2').write_bytes(b'test disk')
        import hashlib
        boot.atomic_json(self.root/'server/image-manifest.json',{'sha256':hashlib.sha256(b'test disk').hexdigest()})
        probe=self.root/'qemu-test-probe'
        probe.write_text('#!'+sys.executable+'\n'+"""
import hashlib, json, pathlib, sys
args=sys.argv[1:]
assert all(a.isascii() for a in args)
firmware=pathlib.Path(args[args.index('-L')+1]) / args[args.index('-bios')+1]
assert hashlib.sha256(firmware.read_bytes()).hexdigest()=='ae6f6aa973aaccc143f57aa960fb035fd9de4daee4ad0cd713322f8c259e7650'
assert pathlib.Path(json.loads(args[args.index('-blockdev')+1])['filename']).read_bytes()==b'test disk'
serial=pathlib.Path(args[args.index('-serial')+1].split(':',1)[1])
serial.write_text('serial probe')
print('verified relative firmware, disk and serial',file=sys.stderr)
sys.exit(7)
""")
        probe.chmod(0o755)
        launcher=boot.Launcher(data,{'binary':probe, 'share':share})
        with patch.object(boot,'ROOT',self.root):launcher.start()
        self.assertIn('코드 7',launcher.error)
        self.assertIn('verified relative firmware, disk and serial',launcher.error)
        self.assertEqual((data/'logs/server-console.log').read_text(),'serial probe')

    def test_viewer_uses_a_dedicated_local_browser_profile(self):
        data=boot.Sessions(self.root).create()
        fake_exe=self.root/'browser.exe'
        pref = data/'profiles/viewer/Default/Preferences'
        pref.parent.mkdir(parents=True)
        pref.write_text(json.dumps({'download': {'default_directory':'previous-location'},'profile':{'name':'judge'}}))
        # Simulate the Windows branch without changing pathlib's platform.
        from types import SimpleNamespace
        with patch.object(boot,'os',SimpleNamespace(name='nt',replace=os.replace)), patch.object(boot,'installed_browser',return_value=('msedge',fake_exe)), patch.object(boot.subprocess,'Popen') as popen:
            boot.open_demo_window('http://127.0.0.1:8764/',data)
        self.assertIn('--user-data-dir='+str(data/'profiles/viewer'),popen.call_args.args[0])
        prefs=json.loads((data/'profiles/viewer/Default/Preferences').read_text())
        self.assertEqual(prefs['download']['default_directory'],str(data/'downloads'))
        self.assertEqual(prefs['profile']['name'],'judge')
