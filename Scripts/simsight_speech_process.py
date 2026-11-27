"""Own fallback speech children with a Windows kill-on-close Job Object.

The kernel ends the child when its owner exits, including crashes. No named
job or inheritable handle is used, so another program cannot keep it alive.
"""

from __future__ import annotations

import ctypes
from typing import Any

# Windows only (mac/build.py leaves it out of the Mac build). WinDLL, WinError
# and get_last_error exist only in Windows' ctypes, unknown to a type check
# made on another system.
_win: Any = ctypes


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            'ReadOperationCount',
            'WriteOperationCount',
            'OtherOperationCount',
            'ReadTransferCount',
            'WriteTransferCount',
            'OtherTransferCount',
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', ctypes.c_uint32),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', ctypes.c_uint32),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', ctypes.c_uint32),
        ('SchedulingClass', ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _BasicLimits),
        ('IoInfo', _IoCounters),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


class SpeechJob:
    """A kill-on-close job; assign() puts a child process in it."""

    def __init__(self) -> None:
        self._handle: int | None = None
        api: Any = _win.WinDLL('kernel32', use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        api.CreateJobObjectW.restype = ctypes.c_void_p
        api.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        api.SetInformationJobObject.restype = ctypes.c_int
        api.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        api.AssignProcessToJobObject.restype = ctypes.c_int
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        api.CloseHandle.restype = ctypes.c_int
        self._api = api
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            raise _win.WinError(_win.get_last_error())
        self._handle = handle
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = _win.get_last_error()
            self.close()
            raise _win.WinError(error)

    def assign(self, proc: Any) -> None:
        """Put the child (a subprocess.Popen) in the job; OSError when refused."""
        process = int(proc._handle)  # noqa: SLF001 - Popen keeps its Windows process handle private
        if not self._api.AssignProcessToJobObject(self._handle, ctypes.c_void_p(process)):
            raise _win.WinError(_win.get_last_error())

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._api.CloseHandle(handle)
