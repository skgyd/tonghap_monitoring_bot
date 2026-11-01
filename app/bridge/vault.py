"""Windows DPAPI CurrentUser: encrypted token at rest, never placed in config JSON."""
import ctypes,os
from pathlib import Path
class Blob(ctypes.Structure):_fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
def crypt(data,decrypt=False):
 if os.name!='nt':raise RuntimeError('Token storage requires Windows DPAPI')
 buf=ctypes.create_string_buffer(data);src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));dst=Blob()
 lib=ctypes.WinDLL('crypt32',use_last_error=True);fn=lib.CryptUnprotectData if decrypt else lib.CryptProtectData
 fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(Blob)];fn.restype=ctypes.c_int
 kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
 ok=fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst))
 if not ok:raise ctypes.WinError()
 try:return ctypes.string_at(dst.data,dst.size)
 finally:kernel.LocalFree(dst.data)
def save(path,token):
 p=Path(path);tmp=p.with_suffix('.tmp');tmp.write_bytes(crypt(token.encode()));os.replace(tmp,p)
def load(path):return crypt(Path(path).read_bytes(),True).decode()
