"""Locale selection for the memory demo seed datasets.

The seeders run at server bootstrap, before any browser preference exists,
so the dataset language comes from ``Settings.seed_locale``
(``AGENT_SEED_LOCALE``). Resolution mirrors the frontend's
``frontend/src/i18n/index.ts`` exactly, so the seeded data matches the
language the console will display:

- ``zh-TW`` / ``zh-HK`` / ``zh-MO`` / ``zh-Hant*`` → Traditional Chinese
- ``zh*`` → Simplified Chinese
- ``en*`` → English
- ``hi*`` → Hindi
- anything else → Simplified Chinese (the frontend fallback)

Only *display* strings are localized — identity keys (external ids, uuid5
inputs, ticket numbers) stay constant so the seeders remain idempotent
regardless of locale.

Call ``set_locale(settings)`` once before seeding (``run_standalone`` and
``seed_memory_acceptance`` already do), then wrap display strings with
``T(zh=..., en=..., zh_tw=..., hi=...)``. ``zh_tw`` falls back to ``zh``
and ``hi`` falls back to ``en`` when omitted, but seed data should provide
all four bundles.
"""

import re

from agent_platform.settings import Settings

_locale = "zh-CN"


def resolve_locale(value: str | None) -> str:
    v = (value or "").lower()
    if re.match(r"^zh-(tw|hk|mo|hant)(-|$)", v):
        return "zh-TW"
    if v.startswith("zh"):
        return "zh-CN"
    if v.startswith("en"):
        return "en"
    if v.startswith("hi"):
        return "hi"
    return "zh-CN"


def set_locale(settings: Settings) -> str:
    global _locale
    _locale = resolve_locale(settings.seed_locale)
    return _locale


def current_locale() -> str:
    return _locale


def T(zh: str, en: str, zh_tw: str | None = None, hi: str | None = None) -> str:
    """Pick the display string for the active seed locale.

    ``zh`` is the Simplified Chinese bundle; ``zh_tw``/``hi`` fall back to
    ``zh``/``en`` respectively when not provided.
    """
    if _locale == "zh-TW":
        return zh_tw if zh_tw is not None else zh
    if _locale == "en":
        return en
    if _locale == "hi":
        return hi if hi is not None else en
    return zh
