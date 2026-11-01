"""Own and remove only the per-run data created by this launcher."""
import json
import os
import re
import shutil
import stat
import time
import uuid
from pathlib import Path

ID = re.compile(r'[0-9a-f]{32}')
TAG = 'IncidentBridge/ephemeral-session/v1'


def plain(path):
    """Reject junctions and symlinks, including dangling links."""
    if os.path.lexists(path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('연결된 경로는 회차 저장에 사용할 수 없습니다: ' + str(path))


class Sessions:
    def __init__(self, root):
        self.work = Path(root) / 'work'
        self.base = self.work / 'sessions'
        for folder in (self.work, self.base):
            plain(folder)
            folder.mkdir(mode=0o700, exist_ok=True)

    def marker(self, path):
        if path.parent != self.base or not ID.fullmatch(path.name):
            raise RuntimeError('회차 경로가 올바르지 않습니다.')
        return self.base / (path.name + '.owner')

    def owned(self, path):
        for item in (self.work, self.base, path, self.marker(path)):
            plain(item)
        marker = self.marker(path)
        if not marker.is_file() or marker.read_text('ascii') != TAG + '/' + path.name:
            raise RuntimeError('소유권을 확인할 수 없는 회차 폴더: ' + str(path))

    def create(self):
        path = self.base / uuid.uuid4().hex
        if os.path.lexists(path):
            raise FileExistsError(path)
        with self.marker(path).open('x', encoding='ascii') as f:
            f.write(TAG + '/' + path.name)
        path.mkdir(mode=0o700)
        return path

    def remove(self, path):
        self.owned(path)
        for attempt in range(11):
            try:
                if path.exists():
                    shutil.rmtree(path)
                self.marker(path).unlink()
                return
            except OSError as exc:
                if attempt == 10:
                    raise RuntimeError('회차 정리 실패. 파일을 사용하는 창을 닫고 다시 실행하세요: ' + str(path)) from exc
                time.sleep(0.3)

    def clear_previous(self):
        # Caller must hold the installation lock and check service ports first.
        for marker in sorted(self.base.glob('*.owner')):
            if ID.fullmatch(marker.stem):
                self.remove(self.base / marker.stem)
        for path in self.base.iterdir():
            if ID.fullmatch(path.name):
                raise RuntimeError('회차 소유권을 확인할 수 없습니다: ' + str(path))

    def active_url(self):
        for marker in self.base.glob('*.owner'):
            if not ID.fullmatch(marker.stem):
                continue
            path = self.base / marker.stem
            self.owned(path)
            launch = path / 'launch.json'
            plain(launch)
            if launch.is_file():
                url = json.loads(launch.read_text('utf-8'))['url']
                if re.fullmatch(r'http://127\.0\.0\.1:8764/#token=[A-Za-z0-9_-]{43}', url):
                    return url
        raise RuntimeError('이미 실행 중입니다. 준비 후 다시 여세요.')


def contain_children():
    """Kill the process tree if the Windows launcher unexpectedly exits."""
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes as w
    # Keep the handle open until process exit. Children inherit job membership,
    # not the handle, so a crashing launcher releases the last job handle.
    class Basic(ctypes.Structure):
        _fields_ = [('proc_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active', w.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                    ('memory', ctypes.c_size_t * 4)]
    lib = ctypes.WinDLL('kernel32', use_last_error=True)
    lib.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
    lib.CreateJobObjectW.restype = w.HANDLE
    lib.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
    lib.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    lib.GetCurrentProcess.restype = w.HANDLE
    lib.CloseHandle.argtypes = [w.HANDLE]
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    job = lib.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    if (not lib.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            or not lib.AssignProcessToJobObject(job, lib.GetCurrentProcess())):
        error = ctypes.get_last_error()
        lib.CloseHandle(job)
        raise RuntimeError('Windows 정책 때문에 시연 프로세스를 격리할 수 없습니다. ' + str(ctypes.WinError(error)))
