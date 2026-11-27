"""Localized narration vocabulary for the SimSight mod.

Game text (tooltips, labels, feature names) arrives already localized from the
Flash layer. This module localizes only the mod's OWN narration vocabulary
(currency name, checkbox states, glyph replacements).

Locale comes from the connected client's account (set at client connect in
areaserver.c_api_client_connect). Falls back to English until a client exists.
Translations are first-pass; refine per-language as feedback arrives.

The lookup is in lookup; the term tables are the terms_* submodules, one per
area of the mod (menu, words, scanner, keyboard, features, mac).
"""

from __future__ import annotations

from .lookup import _MAC_TRANSLATIONS as _MAC_TRANSLATIONS
from .lookup import _TRANSLATIONS as _TRANSLATIONS
from .lookup import _state as _state
from .lookup import detect_lang, locale_lang, t, text

__all__ = ['detect_lang', 'locale_lang', 't', 'text']
