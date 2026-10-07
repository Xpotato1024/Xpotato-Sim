"""アプリ起動で生成したworkerだけの停止を管理する。任意commandのCLIは持たない。"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
from time import monotonic, sleep
from typing import IO
from uuid import uuid4
from threading import current_thread, main_thread


class _WindowsJob:
    """workerを開始gateの手前でjobへ登録し、親消失時にも子孫を終了する。"""

    def __init__(self) -> None:
        import ctypes as c
        from ctypes import wintypes as w

        class Basic(c.Structure):
            _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                        ("min_working_set", c.c_size_t), ("max_working_set", c.c_size_t),
                        ("active_limit", w.DWORD), ("affinity", c.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class Io(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(c.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_memory", c.c_size_t),
                        ("job_memory", c.c_size_t), ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t)]

        self._api = c.WinDLL("kernel32", use_last_error=True)
        self._api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self._api.CreateJobObjectW.restype = w.HANDLE
        self._api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self._api.SetInformationJobObject.restype = w.BOOL
        self._api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self._api.AssignProcessToJobObject.restype = w.BOOL
        self._api.CloseHandle.argtypes = [w.HANDLE]
        self._api.CloseHandle.restype = w.BOOL
        self.name = "SelfrionetteApp-" + uuid4().hex
        self._handle = self._api.CreateJobObjectW(None, self.name)
        if not self._handle:
            raise c.WinError(c.get_last_error())
        info = Extended()
        info.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._api.SetInformationJobObject(self._handle, 9, c.byref(info), c.sizeof(info)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen) -> None:
        import ctypes as c
        # Popenが所有する既存handleを使用し、PID検索で別processを取得しない。
        if not self._api.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise c.WinError(c.get_last_error())

    def close(self) -> None:
        if self._handle:
            import ctypes as c
            if not self._api.CloseHandle(self._handle):
                raise c.WinError(c.get_last_error())
            self._handle = None


class OwnedApplicationWorkers:
    """開始済みworkerを有限cleanupする。既存processを名前で検索・終了しない。"""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []
        self._logs: list[IO[bytes]] = []
        self._job = _WindowsJob() if os.name == "nt" else None
        self._closed = False

    def __enter__(self) -> OwnedApplicationWorkers:
        return self

    def __exit__(self, exc_type, failure, traceback) -> None:
        try:
            self.close()
        except Exception as cleanup:
            if failure is None:
                raise
            failure.add_note(f"application cleanup failed: {cleanup!r}")

    def start(self, argv: list[str], *, cwd: Path, log_path: Path,
              env: dict[str, str]) -> subprocess.Popen:
        if self._closed:
            raise RuntimeError("application worker owner is closed")
        log = log_path.open("wb")
        self._logs.append(log)
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        worker_env = {**env, "XPOTATO_SIM_JOB": self._job.name} if self._job is not None else env
        process = subprocess.Popen(argv, cwd=cwd, env=worker_env, stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, shell=False, **options)
        self.processes.append(process)
        try:
            if self._job is not None:
                self._job.assign(process)
            # workerはこのgate以前にbackend/Node/networkを開始しない。
            assert process.stdin is not None
            process.stdin.write(b"start\n")
            process.stdin.flush()
            process.stdin.close()
        except BaseException:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            process.kill()
            process.wait(timeout=5)
            raise
        return process

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        previous_handlers = {}
        if current_thread() is main_thread():
            for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", signal.SIGINT)):
                if sig not in previous_handlers:
                    previous_handlers[sig] = signal.signal(sig, signal.SIG_IGN)
        try:
            for process in reversed(self.processes):
                try:
                    if os.name == "nt":
                        if process.poll() is None:
                            process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    # consoleなしのWindowsでもjob closeで後始末する。
                    pass
            deadline = monotonic() + 2.0
            while any(p.poll() is None for p in self.processes) and monotonic() < deadline:
                sleep(0.02)
            if self._job is not None:
                self._job.close()
            else:
                for process in self.processes:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            for process in self.processes:
                try:
                    process.wait(timeout=5)
                except Exception as exc:
                    errors.append(exc)
        finally:
            for log in self._logs:
                log.close()
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
        if errors:
            raise RuntimeError("application worker cleanup did not complete") from errors[0]


def join_application_job() -> None:
    """Windows venv redirectorから生じた実worker自身も、開始前に同じjobへ入れる。"""
    if os.name != "nt":
        return
    import ctypes as c
    from ctypes import wintypes as w
    name = os.environ.pop("XPOTATO_SIM_JOB", None)
    if not name or not name.startswith("SelfrionetteApp-"):
        raise RuntimeError("application worker requires its parent job")
    api = c.WinDLL("kernel32", use_last_error=True)
    api.OpenJobObjectW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    api.OpenJobObjectW.restype = w.HANDLE
    api.GetCurrentProcess.restype = w.HANDLE
    api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]
    api.IsProcessInJob.restype = w.BOOL
    api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    api.AssignProcessToJobObject.restype = w.BOOL
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    handle = api.OpenJobObjectW(0x0001 | 0x0004, False, name)  # ASSIGN_PROCESS | QUERY
    if not handle:
        raise c.WinError(c.get_last_error())
    try:
        process = api.GetCurrentProcess()
        member = w.BOOL()
        if not api.IsProcessInJob(process, handle, c.byref(member)):
            raise c.WinError(c.get_last_error())
        if not member.value and not api.AssignProcessToJobObject(handle, process):
            raise c.WinError(c.get_last_error())
    finally:
        # 子にjob handleを残さず、親のhandle closeを終了条件として維持する。
        api.CloseHandle(handle)
