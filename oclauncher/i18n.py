"""Minimal translation layer. English is the source language.

Language is picked from OCL_LANG, then LANG. Translations live in
oclauncher/locale/<lang>.json as {"English text": "translated text"}.
Missing strings fall back to English.
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_table = {}


def set_language(lang=None):
    global _table
    lang = (lang or os.environ.get("OCL_LANG") or os.environ.get("LANG") or "en")[:2].lower()
    path = os.path.join(_HERE, "locale", f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as f:
            _table = json.load(f)
    except (OSError, ValueError):
        _table = {}
    return lang


def _(text):
    return _table.get(text, text)


set_language()
