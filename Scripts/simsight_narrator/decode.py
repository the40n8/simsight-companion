"""Percent-decoding of Flash payloads and the glyph clean-up of game text."""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import unquote_to_bytes

import simsight_lang as lang


def _utf8(chunk: bytes) -> str | None:
    try:
        return chunk.decode('utf-8')
    except UnicodeDecodeError:
        return None  # not a complete UTF-8 sequence


def _decode_char(raw: bytes, i: int) -> tuple[str, int]:
    """The character at raw[i] (not ASCII) and its width: UTF-8 where it
    validates, else one Latin-1 byte."""
    for width in (2, 3, 4):
        char = _utf8(raw[i : i + width])
        if char is not None:
            return char, width
    return raw[i : i + 1].decode('latin-1'), 1


def decode_percent(token: str) -> str:
    """Decode Flash payloads. Scaleform's encodeURIComponent is broken for
    U+0080..U+00FF: those arrive as bare Latin-1 bytes while higher
    codepoints arrive as proper UTF-8, so a strict UTF-8 unquote turns every
    accented letter (and the simoleon sign) into U+FFFD. Decode byte-wise:
    UTF-8 where it validates, Latin-1 for stray single bytes."""
    try:
        raw = unquote_to_bytes(token)
    except (TypeError, ValueError):
        return token
    out = []
    i = 0
    while i < len(raw):
        if raw[i] < 0x80:
            out.append(chr(raw[i]))
            i += 1
            continue
        char, width = _decode_char(raw, i)
        out.append(char)
        i += width
    return ''.join(out)


def _direction(match: re.Match[str]) -> str:
    args = [
        arg.strip() or lang.t('transfer other household')
        for arg in match.group(2).split('\x02')[1:]
    ]
    template = lang.t('transfer ' + match.group(1))
    try:
        return template.format(*args)
    except (IndexError, KeyError, ValueError):
        return ' '.join(args)


def _amount(term: str) -> Callable[[re.Match[str]], str]:
    """Replacement for a currency regex: "<amount> <localized unit>"."""
    return lambda m: m.group(1) + ' ' + lang.t(term)


def _clean_glyphs(text: str) -> str:
    """Replace/strip game icon glyphs that TTS reads badly (localized).
    EA repurposes ordinary symbol chars via the game font: paragraph sign =
    simoleons, U+2260 = galactic credits, lozenge = satisfaction, and so on
    (found by censusing the STBLs; see HANDOFF)."""
    # EA name/description strings carry text markup (<i>, <b>) that only
    # renders inside TextFields; Flash-side COMPOSED payloads (catalog
    # cells read mData.Name raw) pass it through, so spoken text must
    # drop the tags (heard live: "The Indoor <i>and</i> Outdoor Plastic
    # Chair", household inventory 2026-08-20).
    text = re.sub(r'</?[a-zA-Z][^>]*>', ' ', text)
    # Stack-count marker from the catalog cell composer (Flash appends
    # "\x02qty\x02<n>"; keeping it a control-char marker instead of an
    # English word keeps the 17-language table in charge of the wording).
    text = re.sub('\x02qty\x02(\\d+)', lambda m: lang.t('quantity') + ' ' + m.group(1), text)
    # Transfer direction marker from the household and retail transfer
    # panels: "\x02dir\x02<kind>" then "\x02"-separated names and amounts,
    # worded here so the language table owns it. Runs before the currency
    # rule so a household's paragraph-sign amount still becomes simoleons.
    text = re.sub('\x02dir\x02(\\w+)((?:\x02[^\x01\x02]*)*)', _direction, text)
    # Business summaries and panels sign amounts as "+  §500" and
    # "-  §500" and colour them; a lone dash is often skipped by
    # screen readers, so say plus and minus (2026-10-04).
    text = re.sub(
        '(?<![\\w§])([+-])\\s*(?=§)',
        lambda m: lang.t('plus' if m.group(1) == '+' else 'minus') + ' ',
        text,
    )
    # Currency amounts: the capture must END on a digit. The old
    # [\d][\d.,]* also swallowed a trailing sentence period, so
    # "§645. Romantic" became "645. simoleons Romantic" with the unit
    # glued to the next clause (household inventory bug, 2026-08-20).
    # Interior separators ("13,709") still match.
    text = re.sub(r'[§�]\s*(\d(?:[\d.,]*\d)?)', _amount('simoleons'), text)
    text = re.sub('≠\\s*(\\d(?:[\\d.,]*\\d)?)', _amount('galactic credits'), text)
    # Eco Lifestyle recycling currencies (glyphs captured in-game 2026-08-01:
    # wallet order is credits, bits, pieces)
    text = re.sub('⬡\\s*(\\d(?:[\\d.,]*\\d)?)', _amount('bits'), text)
    text = re.sub('⬢\\s*(\\d(?:[\\d.,]*\\d)?)', _amount('pieces'), text)
    text = text.replace('�', ' ')
    text = re.sub(
        r'◊\s*(\d+)', lambda m: '. ' + m.group(1) + ' ' + lang.t('satisfaction points') + '. ', text
    )
    text = text.replace('◴', ' ' + lang.t('likes') + ': ')
    text = text.replace('◵', ' ' + lang.t('dislikes') + ': ')
    # private-use-area icon glyphs
    text = re.sub('[-]', ' ', text)
    text = re.sub(r'\s*[\r\n]+\s*', '. ', text)
    return re.sub(r'  +', ' ', text).strip()


def _norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', text.lower())
