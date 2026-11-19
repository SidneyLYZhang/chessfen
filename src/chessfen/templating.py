from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from chessfen.i18n import (
    DEFAULT_LOCALE,
    LOCALE_LABELS,
    SUPPORTED_LOCALES,
    load_translations,
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _build_environment(locale: str) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=select_autoescape(("html",)),
        extensions=["jinja2.ext.i18n"],
    )
    environment.install_gettext_translations(  # type: ignore[attr-defined]
        load_translations(locale)
    )
    environment.globals["locales"] = [
        (code, LOCALE_LABELS[code]) for code in SUPPORTED_LOCALES
    ]
    return environment


_ENVIRONMENTS: dict[str, Environment] = {
    code: _build_environment(code) for code in SUPPORTED_LOCALES
}


def render_page(locale: str, template_name: str, context: dict[str, Any]) -> str:
    environment = _ENVIRONMENTS.get(locale, _ENVIRONMENTS[DEFAULT_LOCALE])
    template = environment.get_template(template_name)
    return template.render(**context)
