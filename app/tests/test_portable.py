import sys
from pathlib import Path
win=Path(__file__).resolve().parents[2]/'windows'
if win.is_dir():sys.path.insert(0,str(win))
import json,socket,tempfile,unittest,threading,urllib.request,urllib.error,hashlib
from pathlib import Path
from unittest.mock import patch
from portable_bootstrap import claim_lock,check_ports,qemu_command,Launcher,atomic_json,Control
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from app import Application
from demo_launcher import configuration

class PortableTests(unittest.TestCase):
    def test_lock_released_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock';first=claim_lock(path)
            with self.assertRaises(OSError):claim_lock(path)
            first.close();claim_lock(path).close()
    def test_port_conflict_fails_without_touching_service(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1',0));port=server.getsockname()[1]
            with patch('portable_bootstrap.PORTS',(port,)):
                with self.assertRaisesRegex(RuntimeError,str(port)):check_ports()
            server.listen()
    def test_vm_uses_loopback_tcg_and_json_filename(self):
        disk=Path('/tmp/한글, path/server.qcow2')
        args=qemu_command('qemu',Path('/share'),disk,Path('/log'),12345)
        self.assertIn('tcg,thread=multi',args)
        self.assertIn('hostfwd=tcp:127.0.0.1:8065-:8065',args[args.index('-netdev')+1])
        self.assertEqual(json.loads(args[args.index('-blockdev')+1])['filename'],str(disk))
        self.assertNotIn('-enable-kvm',args)
    def test_existing_disk_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher=Launcher(tmp);disk=Path(tmp)/'server.qcow2';disk.write_bytes(b'existing user data')
            with self.assertRaisesRegex(RuntimeError,'기존 쓰기 디스크'): launcher.prepare_disk()
            self.assertEqual(disk.read_bytes(),b'existing user data')
    def test_corrupt_base_not_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'bundle';(root/'server').mkdir(parents=True)
            (root/'server/base.qcow2').write_bytes(b'corrupt')
            atomic_json(root/'server/image-manifest.json',{'sha256':'0'*64})
            launcher=Launcher(Path(tmp)/'data')
            with patch('portable_bootstrap.ROOT',root), patch('portable_bootstrap.shutil.disk_usage',return_value=SimpleNamespace(free=10*1024**3)):
                with self.assertRaisesRegex(RuntimeError,'검증 실패'):launcher.prepare_disk()
            self.assertFalse((launcher.data/'server.qcow2').exists());self.assertFalse((launcher.data/'server.copying').exists())
    def test_demo_events_survive_worker_restart_in_same_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=configuration(False);c.update(portable=True,persist_demo=True)
            atomic_json(root/'config.json',c)
            app=Application(root);event=app.add_demo('polestar',direct=False)
            again=Application(root)
            self.assertEqual(again.mock['polestar'][0]['event_id'],event['event_id'])
            self.assertEqual(again.mock['polestar'][0]['severity'],'critical')
            self.assertNotIn('severity',again.add_demo('jobpass',direct=False))
            self.assertEqual(again.add_demo('polestar',direct=False)['severity'],'major')
            self.assertEqual(again.add_demo('polestar',direct=False)['severity'],'critical')
            self.assertTrue(again.state()['portable'])
    def test_split_image_is_assembled_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'bundle';(root/'server').mkdir(parents=True)
            payload=b'complete server image'
            (root/'server/base.part1').write_bytes(payload[:9]);(root/'server/base.part2').write_bytes(payload[9:])
            atomic_json(root/'server/image-manifest.json',{'parts':['base.part1','base.part2'],'sha256':hashlib.sha256(payload).hexdigest()})
            launcher=Launcher(Path(tmp)/'data')
            with patch('portable_bootstrap.ROOT',root),patch('portable_bootstrap.shutil.disk_usage',return_value=SimpleNamespace(free=10*1024**3)):
                self.assertEqual(launcher.prepare_disk().read_bytes(),payload)
    def test_missing_part_fails_before_writing_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'bundle';(root/'server').mkdir(parents=True)
            (root/'server/base.part1').write_bytes(b'first')
            atomic_json(root/'server/image-manifest.json',{'parts':['base.part1','base.part2'],'sha256':'0'*64})
            launcher=Launcher(Path(tmp)/'data')
            with patch('portable_bootstrap.ROOT',root):
                with self.assertRaisesRegex(RuntimeError,r'monitoringbot-part1\.zip과 monitoringbot-part2\.zip.*monitoringbot\.zip'):
                    launcher.prepare_disk()
            self.assertFalse((launcher.data/'server.qcow2').exists())
    def test_control_rejects_unauthenticated_stop_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher=Launcher(tmp);server=ThreadingHTTPServer(('127.0.0.1',0),Control);server.launcher=launcher
            worker=threading.Thread(target=server.serve_forever);worker.start()
            try:
                for path,method in [('/api/status','GET'),('/api/stop','POST')]:
                    req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,method=method)
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
                    self.assertEqual(error.exception.code,403)
                self.assertFalse(launcher.stop.is_set())
            finally:server.shutdown();server.server_close();worker.join()
    def test_control_rejects_cross_origin_even_with_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher=Launcher(tmp);server=ThreadingHTTPServer(('127.0.0.1',0),Control);server.launcher=launcher
            worker=threading.Thread(target=server.serve_forever);worker.start()
            try:
                req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/stop',method='POST',headers={'Host':'127.0.0.1:8764','X-Launcher-Token':launcher.token,'Origin':'https://example.com'})
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
                self.assertEqual(error.exception.code,403);self.assertFalse(launcher.stop.is_set())
            finally:server.shutdown();server.server_close();worker.join()

if __name__=='__main__':unittest.main()
