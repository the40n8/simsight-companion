"""Screen-reader output through Prism, with configurable Windows speech fallback.

Every spoken line goes through speak(): it decides what the read-current key
repeats (the capture), thins bursts of hover announcements to the newest one,
drops repeats, and sends the rest to Prism (a screen reader, with braille) or
to the fallback voice (Windows SAPI child; on macOS the simsight-say companion
or the companion bridge).
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import threading
import time
from typing import Any

# Imported here, on the loading thread: speech timers run on background
# threads, and a first-time import there can crash the Mac game.
from urllib.parse import quote

import simsight_log


def _optional_module(name: str) -> Any:
    """A standard module the game's trimmed Python may lack, else None."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


atexit = _optional_module('atexit')  # kernel job ownership also covers its absence
subprocess = _optional_module('subprocess')

# macOS port: no ctypes, Prism or SAPI there. The fallback voice is the
# simsight-say companion (AVSpeechSynthesizer), which speaks the same
# stdin line protocol as the Windows SAPI child.
IS_MAC = sys.platform == 'darwin'
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: the fallback voice has no console

MODS_FOLDER = simsight_log.MODS_FOLDER
LOG_PATH = simsight_log.PATH


def mods_root() -> str:
    """The game's Mods folder itself. The speech pack is a SEPARATE download
    (CurseForge forbids DLL files), so users may extract it anywhere under
    Mods, not just into our folder. Walk up from our own load path to the
    directory literally named Mods; unlike the user folder name, "Mods" is
    not localized."""
    path = MODS_FOLDER
    for _ in range(8):
        if os.path.basename(path).lower() == 'mods':
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return MODS_FOLDER


def _first_file(candidates: list[str]) -> str | None:
    return next((p for p in candidates if os.path.isfile(p)), None)


def _prism_path() -> str:
    pack = 'SimSight_SpeechPack'
    candidates = [
        os.path.join(MODS_FOLDER, pack, 'prism.dll'),
        os.path.join(MODS_FOLDER, 'prism.dll'),
        os.path.join(mods_root(), pack, 'prism.dll'),
    ]
    return _first_file(candidates) or candidates[0]


PRISM_DLL_PATH = _prism_path()

# NVDA-style speech modes: hover speaks everything as you point, manual
# captures silently for the read-current key, beeps replaces speech with a
# short tone, off is silent. All four persist via the settings file.
VALID_MODES = ('hover', 'manual', 'beeps', 'off')
_MODE_ANNOUNCEMENTS = {
    'hover': 'Speech on.',
    'manual': 'Speech manual.',
    'beeps': 'Speech beeps.',
    'off': 'Speech off.',
}
# How braille output is handled when a braille display is present:
#   concise  speech gets the full text, braille gets the leading label
#   full     both get the full text
#   only     braille only, speech suppressed (deafblind users who do not
#            want audio at all)
#   off      speech only, no braille
_BRAILLE_MODES = ('concise', 'full', 'only', 'off')


class Modes:
    """The speech and braille modes, which other modules (the narrator, the mod
    menu) read and set as speech.modes.speech and speech.modes.braille."""

    def __init__(self) -> None:
        self.speech = 'hover'  # one of VALID_MODES
        self.braille = 'concise'  # one of _BRAILLE_MODES


modes = Modes()

# Logging stays on if the settings module itself fails to import.
simsight_log.enabled = True
try:
    import simsight_settings as _settings

    simsight_log.enabled = bool(_settings.get('logging'))
    _stored_braille = str(_settings.get('braille_mode', modes.braille))
    if _stored_braille in _BRAILLE_MODES:
        modes.braille = _stored_braille
    _stored_mode = str(_settings.get('speech_mode', 'hover'))
    if _stored_mode in VALID_MODES:
        modes.speech = _stored_mode
except Exception:
    simsight_log.error('speech: reading the settings')

_MIN_GAP = 0.15  # burst thinning: at most one automatic utterance per this
_FLUSH_ORDER = ('widget', 'tooltip', 'event')
_QUEUE_AFTER_WIDGET_WINDOW = 2.5
_REPEAT_WINDOW = 1.0  # the same text again within this is dropped
_TOOLTIP_DEDUPE_WINDOW = 4.0  # a tooltip drops leading segments heard this recently
_BEEP_GAP = 0.12
_BRAILLE_MAX = 120
_CAPTION_MAX_CHARS = 480
# No backend exposes an is-speaking query, so track roughly how long the
# audio should last. Queued utterances (a tooltip parked behind a row label)
# start only once the previous one ends, so measuring from send time would
# call playback finished while it is still talking.
_SPEAK_RATE_CPS = 15.0
# Unreserved ASCII, left as is by Flash's encodeURIComponent.
_CAPTION_SAFE = b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~'


class _State:
    """What the speech pipeline remembers between utterances."""

    def __init__(self) -> None:
        self.backend_name = 'uninitialized'  # prism, sapi or log-only once init() ran
        self.sapi_proc: Any = None  # the fallback voice child (or the bridge's pipe)
        self.sapi_job: Any = None  # its Windows kill-on-close job
        # The last utterance actually sent to a backend.
        self.last_text: str | None = None
        self.last_time = 0.0
        self.last_source: str | None = None
        self.busy_until = 0.0  # when that audio should have finished
        self.last_accept_time = 0.0  # burst thinning
        self.pending_timer: threading.Timer | None = None
        # What the pointer is on: the text the read-current key reads.
        self.captured_text: str | None = None
        self.captured_time = 0.0
        # Text the user has already HEARD: either spoken automatically (hover
        # mode) or read out by the read-current key. Drives that key's
        # read/stop toggle.
        self.last_repeated: str | None = None
        self.deferred_timer: threading.Timer | None = None
        self.last_beep = 0.0
        self.prism: Any = None  # the Prism Reader once loaded (Windows)


_state = _State()
_init_lock = threading.RLock()
_sapi_lock = threading.RLock()
_speech_lock = threading.Lock()
_pending: dict[str, str] = {}  # source -> newest text parked by burst thinning


def log_line(message: str) -> None:
    """A routine log note (other modules' entry point to simsight_log.line)."""
    simsight_log.line(message)


def _try_init_prism() -> bool:
    if IS_MAC or not os.path.isfile(PRISM_DLL_PATH):
        return False
    try:
        from simsight_prism import Reader

        _state.prism = Reader(PRISM_DLL_PATH, simsight_log.line)
        simsight_log.line(f'Prism loaded from {PRISM_DLL_PATH}')
        return True
    except Exception as ex:
        simsight_log.line(f'Prism initialization failed: {ex!r}')
        return False


def screen_reader_name() -> str | None:
    """The screen reader Prism speaks through, or None (not loaded)."""
    return getattr(_state.prism, 'name', None)


def has_braille() -> bool:
    return bool(_state.prism is not None and _state.prism.has_braille())


def _braille_text(text: str) -> str:
    """Condense for a braille display, which typically shows ~40 cells.

    The leading segment is the label ("Fishing. 4" / "Dismiss"), which is what
    a braille reader wants; the trailing prose is what speech is good at.
    """
    if modes.braille == 'full':
        return text
    head = text.split('. ', 1)[0].strip()
    if not head:
        head = text
    return head[:_BRAILLE_MAX]


def _voice_options(default_voice: str) -> tuple[int, int, str]:
    """(rate, volume, voice) of the fallback voice from the settings."""
    try:
        import simsight_settings as settings

        rate = max(-10, min(10, int(settings.get('sapi_rate'))))
        volume = max(0, min(100, int(settings.get('sapi_volume'))))
        voice = str(settings.get('sapi_voice') or '')
    except Exception:
        simsight_log.error('speech: voice settings')
        return 3, 70, default_voice
    return rate, volume, voice


def _sapi_script() -> str:
    """SAPI child script, honoring the sapi_* options. Only affects the
    fallback voice; screen-reader users control voice/rate/volume in their
    own screen reader settings."""
    rate, volume, voice = _voice_options('zira')
    script = (
        'Add-Type -AssemblyName System.Speech; '
        '$sp = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
        f'$sp.Rate = {rate}; $sp.Volume = {volume}; '
    )
    if voice:
        # Case-insensitive partial match, so "david"/"zira"/"hazel" work
        # without the full "Microsoft ... Desktop" name.
        script += (
            "$want = '{}'.ToLower(); "
            'foreach($iv in $sp.GetInstalledVoices()) {{ '
            'if($iv.VoiceInfo.Name.ToLower().Contains($want)) {{ '
            'try {{ $sp.SelectVoice($iv.VoiceInfo.Name) }} catch {{}}; break }} }}; '
        ).format(voice.replace("'", "''"))
    script += (
        'while ($true) { '
        '$l = [Console]::In.ReadLine(); '
        'if ($null -eq $l) { break }; '
        'if ($l.Length -lt 1) { continue }; '
        "if ($l[0] -ne 'Q') { $sp.SpeakAsyncCancelAll() }; "
        'if ($l.Length -lt 2) { continue }; '
        '$t = [System.Uri]::UnescapeDataString($l.Substring(1)); '
        '[void]$sp.SpeakAsync($t) }'
    )
    return script


def _mac_say_path() -> str | None:
    return _first_file(
        [
            os.path.join(MODS_FOLDER, 'simsight-say'),
            os.path.join(MODS_FOLDER, 'SimSight_Mac', 'simsight-say'),
            os.path.join(mods_root(), 'SimSight_Mac', 'simsight-say'),
        ]
    )


def _mac_lang() -> str:
    """The voice language for the client's locale, once it is known."""
    try:
        import simsight_lang as lang

        code = lang.locale_lang()
    except Exception:
        simsight_log.error('speech: client language')
        code = None
    return {'fr': 'fr-FR', 'de': 'de-DE', 'es': 'es-ES', 'en': 'en-US'}.get(code or '', '')


def _try_init_mac_say() -> bool:
    rate, volume, voice = _voice_options('')
    if voice.lower() == 'zira':  # the Windows default voice
        voice = ''
    lang = _mac_lang()
    # Through the companion bridge when it runs (phone app); the game already
    # imported it at load, so this never imports from a speech timer thread.
    bridge = sys.modules.get('simsight_bridge')
    if bridge is not None and bridge.start():
        bridge.set_voice(rate, volume, voice, lang)
        _state.sapi_proc = bridge.SpeechPipe()
        simsight_log.line('speech through the companion bridge')
        return True
    path = _mac_say_path()
    if subprocess is None or path is None:
        simsight_log.line('macOS speech companion missing (simsight-say)')
        return False
    args = [path, '--rate', str(rate), '--volume', str(volume)]
    if voice:
        args += ['--voice', voice]
    if lang:
        args += ['--lang', lang]
    try:
        _state.sapi_proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        simsight_log.line('macOS speech companion started: {}'.format(' '.join(args[1:])))
        return True
    except Exception as ex:
        simsight_log.line(f'macOS speech companion failed: {ex!r}')
        return False


def _dispose_sapi_locked() -> None:
    proc, job = _state.sapi_proc, _state.sapi_job
    _state.sapi_proc, _state.sapi_job = None, None
    # Closing the kernel job also handles a stuck PowerShell startup.
    if job is not None:
        job.close()
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=1)
    except Exception as ex:
        simsight_log.line(f'SAPI child cleanup failed: {ex!r}')
    finally:
        if proc.stdin is not None:
            # Already closed, or the bridge's pipe (no close): nothing to free.
            with contextlib.suppress(OSError, ValueError, AttributeError):
                proc.stdin.close()


def _start_sapi_locked() -> bool:
    job = proc = None
    try:
        from simsight_speech_process import SpeechJob

        job = SpeechJob()
        proc = subprocess.Popen(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', _sapi_script()],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
            close_fds=True,
        )
        job.assign(proc)
        _state.sapi_proc, _state.sapi_job = proc, job
        return True
    except Exception as ex:
        # Never retain an unowned helper if native ownership is refused.
        _state.sapi_proc, _state.sapi_job = proc, job
        _dispose_sapi_locked()
        simsight_log.line(f'SAPI backend init failed: {ex!r}')
        return False


def _try_init_sapi() -> bool:
    if IS_MAC:
        return _try_init_mac_say()
    if subprocess is None:
        return False
    with _sapi_lock:
        if _state.sapi_proc is not None:
            if _state.sapi_proc.poll() is None:
                return True
            _dispose_sapi_locked()
        return _start_sapi_locked()


def _write_sapi(data: bytes, action: str) -> None:
    with _sapi_lock:
        proc = _state.sapi_proc
    if proc is not None:
        try:
            # Never hold the lifecycle lock during pipe I/O: restart/exit must
            # still be able to kill a helper that is stuck before reading stdin.
            proc.stdin.write(data)
            proc.stdin.flush()
        except Exception as ex:
            simsight_log.line(f'SAPI {action} failed: {ex!r}')


def restart_sapi() -> None:
    """Release and reap the owned child; the next utterance uses fresh options."""
    with _sapi_lock:
        _dispose_sapi_locked()


if atexit is not None:
    atexit.register(restart_sapi)


def _mac_voices() -> list[str]:
    path = _mac_say_path()
    if path is None:
        return []
    out = subprocess.run(
        [path, '--list-voices'],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    return [
        line.split('\t')[1]
        for line in out.stdout.decode('utf-8', 'replace').splitlines()
        if line.count('\t') >= 2
    ]


def _windows_voices() -> list[str]:
    out = subprocess.run(
        [
            'powershell',
            '-NoProfile',
            '-NonInteractive',
            '-Command',
            (
                'Add-Type -AssemblyName System.Speech; '
                '(New-Object System.Speech.Synthesis.SpeechSynthesizer)'
                '.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }'
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=_NO_WINDOW,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def list_voices() -> list[str]:
    """Installed fallback voice names (one-shot query, ~a second)."""
    if subprocess is None:
        return []
    try:
        return _mac_voices() if IS_MAC else _windows_voices()
    except Exception as ex:
        simsight_log.line(f'voice list failed: {ex!r}')
        return []


def speech_pack_installed() -> bool:
    return os.path.isfile(PRISM_DLL_PATH)


def init() -> str:
    """Pick the backend once: prism, else sapi (the fallback voice), else log-only."""
    with _init_lock:
        return _init_locked()


def _init_locked() -> str:
    if _state.backend_name != 'uninitialized':
        return _state.backend_name
    if _try_init_prism():
        _state.backend_name = 'prism'
    elif _try_init_sapi():
        _state.backend_name = 'sapi'
    else:
        _state.backend_name = 'log-only'
    simsight_log.line(f'speech backend: {_state.backend_name}')
    if not speech_pack_installed():
        simsight_log.line(f'Prism speech pack missing; expected {PRISM_DLL_PATH}')
    return _state.backend_name


def backend_name() -> str:
    return _state.backend_name


def _segs(text: str) -> list[str]:
    return [seg.strip() for seg in text.split('. ') if seg.strip()]


def _adds_nothing(new: str | None, old: str | None) -> bool:
    """True when every segment of `new` already appears in `old`: new is a
    label-only repeat of a richer line about the same control (e.g. "Hunger"
    right after "Hunger. 80%": the motive bar posts its composed line and a
    generic hover announce of the same row can follow with just the title).
    Speaking or parking the bare label would only lose the richer text."""
    if not new or not old or new == old:
        return False
    old_segs = set(_segs(old))
    new_segs = _segs(new)
    return bool(new_segs) and all(seg in old_segs for seg in new_segs)


def _play_beep() -> None:
    try:
        if IS_MAC:
            subprocess.Popen(
                ['/usr/bin/afplay', '-v', '0.4', '/System/Library/Sounds/Tink.aiff'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        import ctypes

        ctypes.windll.kernel32.Beep(1150, 45)  # type: ignore[attr-defined]  # Windows only
    except Exception:
        simsight_log.error('speech: beep')


def _beep() -> None:
    """Short activity tone for beeps mode. Beep() blocks for its duration,
    so play it off-thread, throttled against hover bursts."""
    now = time.monotonic()
    if now - _state.last_beep < _BEEP_GAP:
        return
    _state.last_beep = now
    threading.Thread(target=_play_beep, daemon=True).start()


def set_mode(mode: str) -> bool:
    """Switch the speech mode, save it and say the new mode. False for an
    unknown mode."""
    if mode not in VALID_MODES:
        return False
    modes.speech = mode
    try:
        import simsight_settings as settings

        settings.set('speech_mode', mode)
    except Exception:
        simsight_log.error('speech: saving the speech mode')
    simsight_log.line(f'speech mode -> {mode}')
    _speak_now(_MODE_ANNOUNCEMENTS[mode], source='widget')
    return True


def cycle_mode() -> None:
    set_mode(VALID_MODES[(VALID_MODES.index(modes.speech) + 1) % len(VALID_MODES)])


def _cancel_deferred() -> None:
    timer = _state.deferred_timer
    _state.deferred_timer = None
    if timer is not None:
        timer.cancel()


def speak_progressive(
    head: str,
    body: str | None,
    source: str = 'tooltip',
    delay: float = 1.2,
    head_spoken: bool = False,
) -> None:
    """Say the short head now and hold the long body back, so a pointer that
    is only passing through (the time/weather panel sits under the speed
    buttons) never dumps a wall of text. Anything else being said cancels the
    body; the read-current key still reads head and body together.

    head_spoken=True means the head was already announced as its own
    utterance (the time panel's widget line), so this call only sets the
    combined capture and schedules the body."""
    if not head_spoken:
        speak(head, source=source)
    full = head if not body else head + '. ' + body
    _state.captured_text = full
    _state.captured_time = time.monotonic()
    if not body or modes.speech != 'hover':
        return  # other modes never auto-speak, so nothing to defer
    # The head is playing right now and the body is on its way, so the user
    # is hearing this capture: the read key must STOP. speak() armed the key
    # with the head alone, which broke the stop-equality and made the key
    # re-read the whole text, heard as "restarts the narration" (numpad-8
    # bug, fixed 2026-08-02).
    _state.last_repeated = full
    _cancel_deferred()
    timer = threading.Timer(delay, _flush_deferred, args=(body, source))
    timer.daemon = True
    _state.deferred_timer = timer
    timer.start()


def _flush_deferred(body: str, source: str) -> None:
    _state.deferred_timer = None
    if modes.speech != 'hover':
        return
    _state.last_repeated = _state.captured_text  # heard in full: 8 now stops
    _speak_now(body, source)


def _note_utterance(text: str, interrupt: bool) -> None:
    now = time.monotonic()
    start = now if interrupt else max(now, _state.busy_until)
    _state.busy_until = start + max(1.0, min(25.0, 0.6 + len(text) / _SPEAK_RATE_CPS))


def _probably_still_speaking() -> bool:
    if _state.last_text is None:
        return False
    return time.monotonic() < _state.busy_until


def repeat_last() -> None:
    """The read-current key: stops speech that is still playing, otherwise
    reads what is under the pointer. The already-heard check alone made a
    press in SILENCE do a soundless "stop", so the key felt dead until a
    second press (tester report, fixed 2026-08-03)."""
    _cancel_deferred()
    if modes.speech == 'off':
        return
    captured = _state.captured_text
    if not captured:
        return
    if _state.last_repeated == captured and _probably_still_speaking():
        stop()
        _state.last_repeated = None
        return
    _state.last_repeated = captured
    init()
    _speak_now(captured, source='widget')


def _capture(text: str, now: float) -> None:
    """Command confirmations are not "what the pointer is on", so only
    automatic speech becomes the capture the read-current key repeats. A bare
    label re-announcing a fresh richer capture of the same control keeps the
    richer line for the read key."""
    captured = _state.captured_text
    keep_richer = (
        captured is not None
        and now - _state.captured_time < _QUEUE_AFTER_WIDGET_WINDOW
        and _adds_nothing(text, captured)
    )
    if not keep_richer:
        if text != captured:
            _state.last_repeated = None  # new capture re-arms the read key
        _state.captured_text = text
    _state.captured_time = now


def _park_if_burst(text: str, source: str, now: float) -> bool:
    """Burst thinning. True when the text was parked for a moment (the
    newest per channel is flushed together), False when it may be spoken now."""
    with _speech_lock:
        gap = now - _state.last_accept_time
        if gap >= _MIN_GAP:
            _state.last_accept_time = now
            if source == 'widget':
                _pending.pop('tooltip', None)
            return False
        # Park newest per channel: a description can never evict its own
        # (or the next) label; a new label evicts a stale description.
        if source == 'widget':
            _pending.pop('tooltip', None)
            parked = _pending.get('widget')
            if parked is not None and _adds_nothing(text, parked):
                # A bare label must not evict the richer parked line for
                # the same control ("Fishing" evicting "Fishing. 4"
                # under newest-wins ate the level read, 2026-08-02).
                text = parked
        _pending[source] = text
        _state.last_repeated = text
        timer = _state.pending_timer
        if timer is None or not timer.is_alive():
            timer = threading.Timer(_MIN_GAP - gap, _flush_pending)
            timer.daemon = True
            _state.pending_timer = timer
            timer.start()
        return True


def speak(
    text: str, interrupt: bool = True, source: str = 'tooltip', explicit: bool = False
) -> None:
    """Speak text through the active backend, newest-wins under flood.

    Bursts of announcements (hover transitions, busy async UI) are thinned to
    one per _MIN_GAP seconds, but the LATEST utterance in a burst is the one
    that survives: it is parked and flushed a moment later, replacing anything
    older still waiting. The last thing hovered is always what gets spoken.

    Interrupt policy: widget labels always interrupt (newest hover wins); a
    tooltip that follows a recent widget label QUEUES behind it instead of
    cutting it off, so "Fishing. 4" finishes before the skill description.
    The `interrupt` argument is accepted from callers but the policy decides.
    """
    if not text:
        return
    init()
    _cancel_deferred()  # a new utterance drops any held-back tooltip body
    now = time.monotonic()
    if explicit:
        # The user asked for this utterance directly: skip burst-thinning,
        # and drop anything parked so a stale hover can't talk over it.
        with _speech_lock:
            _pending.clear()
            _state.last_accept_time = time.monotonic()
        _speak_now(text, source)
        return
    _capture(text, now)
    # Speech modes: automatic narration is suppressed in manual (captured
    # for the read-current key), replaced by a tone in beeps, silenced in
    # off. Explicit speech (command feedback, read-current) always talks.
    # Deafblind exception: in braille-only mode the braille stream keeps
    # flowing regardless of speech mode.
    if modes.speech != 'hover' and not (modes.braille == 'only' and has_braille()):
        simsight_log.line(f'CAPTURE[{modes.speech}/{source[0]}]: {text}')
        if modes.speech == 'beeps':
            _beep()
        return
    if _park_if_burst(text, source, now):
        return
    # About to be spoken automatically, so the user is hearing it: the
    # read-current key becomes a stop key until something new is captured.
    _state.last_repeated = text
    _speak_now(text, source)


def _flush_pending() -> None:
    with _speech_lock:
        items = [(src, _pending.pop(src)) for src in _FLUSH_ORDER if src in _pending]
        _pending.clear()
        _state.last_accept_time = time.monotonic()
    for src, text in items:
        _speak_now(text, src)


def stop() -> None:
    """Cut off current and queued speech immediately."""
    init()
    _cancel_deferred()
    with _speech_lock:
        _pending.clear()
    _state.last_text = None
    _state.last_time = time.monotonic()
    _state.last_source = 'widget'
    _state.busy_until = 0.0
    if _state.prism is not None:
        try:
            _state.prism.stop()
        except Exception as ex:
            simsight_log.line(f'Prism stop failed: {ex!r}')
    # Always cancel any fallback that may still be speaking after a switch.
    _write_sapi(b'I\n', 'stop')


def request_hush(grace: float = 0.8) -> None:
    """A textless control was touched (Flash fell back to a derived name or
    an outright stop). Silence stale speech, but never cut down a widget
    line that just started: entering a row often crosses its icon-only
    children, and their empty announce used to kill the row's own label
    mid-word (part of the skills/needs "title, then level" regression,
    2026-08-02)."""
    now = time.monotonic()
    with _speech_lock:
        if _pending.get('widget') is not None:
            return
    if (
        _state.last_source == 'widget'
        and _state.last_text is not None
        and (now - _state.last_time) < grace
    ):
        return
    stop()


def retry_screen_reader() -> bool:
    """Look for a screen reader again; True when one is now in use."""
    with _init_lock:
        return _retry_screen_reader_locked()


def _retry_screen_reader_locked() -> bool:
    if _state.prism is None and not _try_init_prism():
        return False
    _state.backend_name = 'prism'
    found = bool(_state.prism.select(force=True))
    if found:
        restart_sapi()
    return found


def _caption_encode(text: str) -> str:
    """Same UTF-8 percent encoding as Flash encodeURIComponent. Escape all
    bytes outside the unreserved ASCII set, including pipes and percent."""
    return ''.join(
        chr(byte) if byte in _CAPTION_SAFE else f'%{byte:02X}'
        for byte in text.encode('utf-8', errors='replace')
    )


def _send_caption(text: str) -> None:
    """Simulation thread only; recheck the toggle for queued utterances."""
    import simsight_settings as settings

    if text and not settings.get('captions', False):
        return
    import distributor.ops
    import distributor.system

    op = distributor.ops.SendUIMessage('A11y|cap|' + _caption_encode(text))
    distributor.system.Distributor.instance().add_op_with_no_owner(op)


def _post_caption(text: str) -> None:
    try:
        import simsight_settings as settings

        if text and not settings.get('captions', False):
            return
        import simsight_pump as pump

        if len(text) > _CAPTION_MAX_CHARS:
            text = text[: _CAPTION_MAX_CHARS - 1] + '…'
        pump.post(_send_caption, text)
    except Exception as ex:
        simsight_log.line(f'caption: post failed: {ex!r}')


def clear_caption() -> None:
    _post_caption('')


def _strip_heard(text: str, last: str) -> str | None:
    """Cross-utterance dedupe: a tooltip often opens with the exact text just
    spoken from the hovered control (e.g. a trait name). The text without the
    leading segments that were just heard; None when nothing is left."""
    prev_segs = {seg.strip() for seg in last.split('. ')}
    segs = [seg.strip() for seg in text.split('. ')]
    while segs and segs[0] in prev_segs:
        segs.pop(0)
    return '. '.join(segs) if segs else None


def _deduplicated(text: str, source: str, now: float) -> str | None:
    """What is left to say of text after the repeat checks, or None."""
    last = _state.last_text
    since = now - _state.last_time
    if text == last and since < _REPEAT_WINDOW:
        return None
    if (
        source == 'widget'
        and _state.last_source == 'widget'
        and last
        and since < _QUEUE_AFTER_WIDGET_WINDOW
        and _adds_nothing(text, last)
    ):
        # A bare label re-announcing the control whose richer line is still
        # playing ("Hunger" cutting off "Hunger. 80%"): information-free,
        # and widget-over-widget interrupts, so speaking it truncated the
        # level read (2026-08-02). Keep the read key armed on what is
        # actually being heard.
        _state.last_repeated = last
        return None
    if source == 'tooltip' and last and since < _TOOLTIP_DEDUPE_WINDOW:
        return _strip_heard(text, last)
    return text


def _interrupts(source: str, now: float) -> bool:
    if source == 'event':
        # Ambient narration (e.g. "what is my sim doing") never cuts off
        # whatever the player is actively reading.
        return False
    # A tooltip right after a widget label queues behind it.
    return not (
        source == 'tooltip'
        and _state.last_source == 'widget'
        and (now - _state.last_time) < _QUEUE_AFTER_WIDGET_WINDOW
    )


def _mirror(text: str, interrupt: bool) -> None:
    """Windows: the phone's history shows what the screen reader said (on
    macOS the speech itself goes through the bridge)."""
    if IS_MAC:
        return
    bridge = sys.modules.get('simsight_bridge')
    if bridge is not None:
        try:
            bridge.mirror(text, interrupt)
        except Exception as ex:
            simsight_log.line(f'companion mirror failed: {ex!r}')


def _output(text: str, interrupt: bool) -> None:
    """To the screen reader when it takes it, else to the fallback voice."""
    if _state.backend_name == 'prism' and _state.prism is not None:
        try:
            if _state.prism.output(text, _braille_text(text), modes.braille, interrupt):
                restart_sapi()
                return
        except Exception as ex:
            simsight_log.line(f'Prism output failed: {ex!r}')
    if modes.braille == 'only':
        return
    with _sapi_lock:
        if _state.backend_name in ('prism', 'sapi') and _state.sapi_proc is None:
            # No available screen reader; use the configured Windows voice.
            _try_init_sapi()
    line = ('I' if interrupt else 'Q') + quote(text) + '\n'
    _write_sapi(line.encode('ascii'), 'speak')


def _speak_now(text: str, source: str = 'tooltip') -> None:
    now = time.monotonic()
    spoken = _deduplicated(text, source, now)
    if spoken is None:
        return
    interrupt = _interrupts(source, now)
    _state.last_text = spoken
    _state.last_time = now
    _state.last_source = source
    _note_utterance(spoken, interrupt)
    # After capture, thinning and dedupe: repeat/deferred speech also comes
    # through here, whereas text merely captured in manual/off never does.
    _post_caption(spoken)
    simsight_log.line('SPEAK[{}{}]: {}'.format(source[0], 'i' if interrupt else 'q', spoken))
    _mirror(spoken, interrupt)
    _output(spoken, interrupt)
