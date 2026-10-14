"""Windows-user protected storage for credentials created by our own test."""
import ctypes
from ctypes import wintypes
import json
import os


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("自动测试账号的凭据存储仅支持 Windows DPAPI")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    call = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    call.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    call.restype = wintypes.BOOL
    if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)


def save(path, credentials):
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _crypt(json.dumps(credentials).encode("utf-8"))
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, path)


def load(path):
    return json.loads(_crypt(path.read_bytes(), decrypt=True))
