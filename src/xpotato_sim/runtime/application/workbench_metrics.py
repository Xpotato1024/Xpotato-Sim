"""Workbenchのprocess memoryを明示診断し、未測定を区別する。"""
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


class StageTimings:
    """ownerが測る区間別の経過時間。CPU時間ではなく、各区間の末尾標本を保持する。"""
    NAMES = ("receive_wait", "input_dispatch", "advance", "projection", "status")

    def __init__(self, capacity=600):
        from collections import deque
        if type(capacity) is not int or capacity < 1:
            raise ValueError("positive timing capacity required")
        self.samples = {name: deque(maxlen=capacity) for name in self.NAMES}
        self.counts = {name: 0 for name in self.NAMES}

    def clear(self):
        for name, values in self.samples.items():
            values.clear()
            self.counts[name] = 0

    def observe(self, name, elapsed_ns):
        self.samples[name].append(elapsed_ns)
        self.counts[name] += 1

    def snapshot(self):
        result = {}
        for name, values in self.samples.items():
            ordered = sorted(values)
            count = len(ordered)
            def percentile(percent):
                return ordered[(percent * count + 99) // 100 - 1] if count else None
            result[name] = {"count": count, "observed": self.counts[name],
                "last_ns": values[-1] if count else None,
                "mean_ns": sum(ordered) / count if count else None,
                "p50_ns": percentile(50), "p95_ns": percentile(95),
                "p99_ns": percentile(99), "max_ns": ordered[-1] if count else None}
        return result
