from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.support import Translations

SUPPORTED_LOCALES: tuple[str, ...] = ("zh", "en", "de")
DEFAULT_LOCALE = "zh"
LOCALE_LABELS: dict[str, str] = {"zh": "中文", "en": "English", "de": "Deutsch"}

_LOCALE_DIR = Path(__file__).parent / "locale"


@lru_cache(maxsize=None)
def load_translations(locale: str) -> Translations:
    """Load a locale catalog from its .po file.

    The catalog is compiled to MO in memory so the .po files stay the single
    source of truth with no build step.
    """
    po_path = _LOCALE_DIR / locale / "LC_MESSAGES" / "messages.po"
    with po_path.open("rb") as source:
        catalog = read_po(source, locale=locale)
    buffer = BytesIO()
    write_mo(buffer, catalog)
    buffer.seek(0)
    return Translations(buffer)


def negotiate_locale(
    cookie_locale: str | None, accept_language: str | None
) -> str:
    """Pick the response locale: explicit cookie, then Accept-Language, then default."""
    if cookie_locale in SUPPORTED_LOCALES:
        return cookie_locale
    for candidate in _accepted_languages(accept_language):
        if candidate in SUPPORTED_LOCALES:
            return candidate
    return DEFAULT_LOCALE


def _accepted_languages(header: str | None) -> list[str]:
    if not header:
        return []
    weighted: list[tuple[float, int, str]] = []
    for position, part in enumerate(header.split(",")):
        segment = part.strip()
        if not segment:
            continue
        tag, _, params = segment.partition(";")
        quality = 1.0
        for param in params.split(";"):
            param = param.strip()
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        language = tag.strip().split("-")[0].lower()
        if language:
            weighted.append((quality, position, language))
    weighted.sort(key=lambda item: (-item[0], item[1]))
    return [language for quality, _, language in weighted if quality > 0]
