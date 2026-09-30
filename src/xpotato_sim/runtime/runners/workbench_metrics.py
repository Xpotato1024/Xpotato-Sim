"""現在の所有processだけを測る。別processの探索・操作はしない。"""
import ctypes
import os
from pathlib import Path


def process_memory():
    """RSSとprivate bytes。未対応platformの未測定値を0で補完しない。"""
    if os.name == "nt":
        from ctypes import wintypes as w
        class Counters(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_paged", "paged",
                    "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = w.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Counters), w.DWORD]
        info = Counters()
        info.cb = ctypes.sizeof(info)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb):
            return {"rss_bytes": None, "private_bytes": None}
        return {"rss_bytes": info.rss, "private_bytes": info.private}
    statm = Path("/proc/self/statm")
    if statm.is_file():
        return {"rss_bytes": int(statm.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE"), "private_bytes": None}
    return {"rss_bytes": None, "private_bytes": None}
