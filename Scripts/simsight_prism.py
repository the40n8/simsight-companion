"""Small Python 3.7 binding for the shipped Prism 0.18.2 C ABI.

Prism owns screen-reader detection and communication. Windows fallback speech
stays in the speech module so existing fallback voice settings still apply.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from typing import Any, Callable

import simsight_log

# Prism backends left out: UIA can report success without a reader actually
# speaking; SAPI and OneCore are plain voices, the speech module's fallback.
_SKIPPED_BACKENDS = ('sapi', 'onecore', 'uia')
_FEATURE_BRAILLE = 16  # prism_backend_get_features bit
_PROBE_SECONDS = 2.0  # between attempts to find a running screen reader


class _Result:
    """A value handed from the reader thread (the game's stripped standard
    library does not ship concurrent.futures)."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None

    def set_result(self, value: Any) -> None:
        self.value = value
        self.done.set()

    def set_exception(self, error: BaseException) -> None:
        self.error = error
        self.done.set()

    def result(self) -> Any:
        self.done.wait()
        if self.error is not None:
            raise self.error
        return self.value


def _bind(dll: Any) -> None:
    p = ctypes.c_void_p
    u = ctypes.c_uint64
    signatures = {
        'prism_init': ([p], p),
        'prism_registry_count': ([p], ctypes.c_size_t),
        'prism_registry_id_at': ([p, ctypes.c_size_t], u),
        'prism_registry_name': ([p, u], ctypes.c_char_p),
        'prism_registry_create': ([p, u], p),
        'prism_backend_initialize': ([p], ctypes.c_int),
        'prism_backend_get_features': ([p], u),
        'prism_backend_free': ([p], None),
        'prism_backend_speak': ([p, ctypes.c_char_p, ctypes.c_bool], ctypes.c_int),
        'prism_backend_braille': ([p, ctypes.c_char_p], ctypes.c_int),
        'prism_backend_stop': ([p], ctypes.c_int),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result


class _NativeReader:
    """Prism itself; used only from the Reader's thread."""

    def __init__(self, path: str, log: Callable[[str], None]) -> None:
        self.log = log
        self.lock = threading.RLock()
        self.dll: Any = ctypes.CDLL(path)
        self.backend: Any = None
        self.name: str | None = None
        self.next_probe = 0.0
        _bind(self.dll)
        self.context = self.dll.prism_init(None)
        if not self.context:
            raise RuntimeError('Prism context initialization failed')
        self.ids: list[tuple[int, str]] = []
        for i in range(self.dll.prism_registry_count(self.context)):
            ident = self.dll.prism_registry_id_at(self.context, i)
            name = (self.dll.prism_registry_name(self.context, ident) or b'').decode(
                'utf-8', 'replace'
            )
            if name.lower().replace(' ', '') not in _SKIPPED_BACKENDS:
                self.ids.append((ident, name))
        self.select()

    def select(self, force: bool = False) -> bool:
        """True when a screen reader backend is in use (probing for one at
        most every two seconds unless forced)."""
        with self.lock:
            if force:
                self._drop()
            if self.backend:
                return True
            if not force and time.monotonic() < self.next_probe:
                return False
            self.next_probe = time.monotonic() + _PROBE_SECONDS
            for ident, name in self.ids:
                backend = self.dll.prism_registry_create(self.context, ident)
                if not backend:
                    continue
                if self.dll.prism_backend_initialize(backend) == 0:
                    self.backend, self.name = backend, name
                    self.log('Prism screen reader: ' + name)
                    return True
                self.dll.prism_backend_free(backend)
            return False

    def _drop(self) -> None:
        if self.backend:
            self.dll.prism_backend_free(self.backend)
        self.backend, self.name = None, None

    def has_braille(self) -> bool:
        with self.lock:
            return bool(
                self.backend
                and self.dll.prism_backend_get_features(self.backend) & _FEATURE_BRAILLE
            )

    def output(self, text: str, brief: str, mode: str, interrupt: bool) -> bool:
        """Speak and/or braille per the braille mode. False when no reader
        took it (the caller then uses the fallback voice)."""
        with self.lock:
            if not self.select():
                return False
            if mode == 'only':
                result = self.dll.prism_backend_braille(self.backend, brief.encode('utf-8'))
                if result:
                    self.log(f'Prism braille failed: {result}')
                return True  # Never substitute audio for braille-only output.
            result = self.dll.prism_backend_speak(self.backend, text.encode('utf-8'), interrupt)
            if result:
                self.log(f'Prism speech failed: {self.name} ({result})')
                self._drop()
                return False
            if mode != 'off' and self.has_braille():
                self.dll.prism_backend_braille(
                    self.backend, (text if mode == 'full' else brief).encode('utf-8')
                )
            return True

    def stop(self) -> bool:
        with self.lock:
            return bool(self.backend and self.dll.prism_backend_stop(self.backend) == 0)


class Reader:
    """Own every native call on one thread, including COM initialization.

    Speech may arrive from the simulation thread or a deferred speech timer.
    COM readers must never be created on one of these and called on another.
    """

    def __init__(self, path: str, log: Callable[[str], None]) -> None:
        self.jobs: queue.Queue[tuple[str, tuple[Any, ...], dict[str, Any], _Result]] = queue.Queue()
        self.ready = _Result()
        self.thread = threading.Thread(target=self._run, args=(path, log), daemon=True)
        self.thread.start()
        self.ready.result()

    def _run(self, path: str, log: Callable[[str], None]) -> None:
        try:
            native = _NativeReader(path, log)
        except Exception as ex:  # the caller gets it from ready.result()
            simsight_log.error('prism: reader start')
            self.ready.set_exception(ex)
            return
        self.ready.set_result(True)
        while True:
            method, args, kwargs, result = self.jobs.get()
            try:
                value = (
                    native.name if method == 'name' else getattr(native, method)(*args, **kwargs)
                )
                result.set_result(value)
            except Exception as ex:  # the caller gets it from result.result()
                simsight_log.error(f'prism: {method}')
                result.set_exception(ex)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        result = _Result()
        self.jobs.put((method, args, kwargs, result))
        return result.result()

    @property
    def name(self) -> str | None:
        """The screen reader in use, or None."""
        return self._call('name')

    def select(self, force: bool = False) -> bool:
        return bool(self._call('select', force=force))

    def has_braille(self) -> bool:
        return bool(self._call('has_braille'))

    def output(self, text: str, brief: str, mode: str, interrupt: bool) -> bool:
        return bool(self._call('output', text, brief, mode, interrupt))

    def stop(self) -> bool:
        return bool(self._call('stop'))
