"""Term lookup: the language of the connected client and the localized text
for a term. The tables are the terms_* submodules, merged here in a fixed
order (a later table wins for a term two tables share)."""

from __future__ import annotations

import sys

import simsight_log

from . import terms_features, terms_keyboard, terms_mac, terms_menu, terms_scanner, terms_words

# term -> {language code: text}; 'en' is always there.
_TRANSLATIONS: dict[str, dict[str, str]] = {
    **terms_menu.TERMS,
    **terms_words.TERMS,
    **terms_scanner.TERMS,
    **terms_keyboard.TERMS,
    **terms_features.TERMS,
}

# Read first on the Mac: other keys there.
_MAC_TRANSLATIONS: dict[str, dict[str, str]] = terms_mac.TERMS


class _State:
    def __init__(self) -> None:
        self.locale_lang: str | None = None  # set once a client's locale is known


_state = _State()


def locale_lang() -> str | None:
    """The detected language code, or None while no client locale is known
    (unlike detect_lang, this never looks for one)."""
    return _state.locale_lang


def _client_locale() -> str | None:
    import services

    client = services.client_manager().get_first_client()
    if client is not None and getattr(client.account, 'locale', None):
        return str(client.account.locale)
    return None


def detect_lang() -> str:
    """Language prefix from the connected client's account locale."""
    if _state.locale_lang is not None:
        return _state.locale_lang
    try:
        locale = _client_locale()
    except AttributeError:
        locale = None  # no zone or client manager yet
    except Exception:
        simsight_log.error('lang: client locale')
        locale = None
    if locale is not None:
        lang = locale.lower().replace('-', '_').split('_')[0]
        if any(lang in v for v in _TRANSLATIONS.values()):
            _state.locale_lang = lang
            return lang
    # No client yet: stay undetected so we retry next call.
    return 'en'


def text(en: str, pl: str) -> str:
    """The Polish text when the game speaks Polish, else the English."""
    return pl if detect_lang() == 'pl' else en


def t(term: str) -> str:
    """Localized narration term; falls back to English."""
    entry = (
        _MAC_TRANSLATIONS.get(term) if sys.platform == 'darwin' else None
    ) or _TRANSLATIONS.get(term)
    if not entry:
        return term
    return entry.get(detect_lang(), entry['en'])
