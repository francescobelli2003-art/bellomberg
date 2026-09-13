"""Explicit rendering of authored bilingual messages retained in data caches.

Only ``message(it, en, **values)`` marks text as authored presentation. Plain
strings, dictionary keys, codes and source/user text never undergo lookup or
translation. ``render_payload`` copies a payload and renders those marked
messages, without fetching data, recalculating metrics or changing cache age.

The string subtype keeps existing JSON consumers compatible. Deep copying does
not switch language: only the explicit presentation boundary does. Parameters
are retained separately so nested labels can be rendered in either language.
"""
from copy import deepcopy

from bellomberg.core.language import capture_language, text


class _PresentationText(str):
    def __new__(cls, italian, english, values, *, language):
        if not isinstance(italian, str) or not isinstance(english, str):
            raise TypeError("Both authored message variants must be strings")
        rendered_values = {key: _render(value, language) for key, value in values.items()}
        # Both variants must be usable, even when only one is being displayed.
        rendered_it = italian.format_map(rendered_values) if values else italian
        rendered_en = english.format_map(rendered_values) if values else english
        result = super().__new__(cls, text(rendered_it, rendered_en, language=language))
        result._italian, result._english = italian, english
        result._values = deepcopy(values)
        return result

    def __deepcopy__(self, memo):
        result = str.__new__(type(self), str(self))
        memo[id(self)] = result
        result._italian, result._english = self._italian, self._english
        result._values = deepcopy(self._values, memo)
        return result


def message(italian: str, english: str, **values) -> str:
    """Mark an authored message; parameters and both templates stay in memory."""
    return _PresentationText(italian, english, values, language=capture_language())


def join_messages(separator: str, values) -> str:
    """Join presentation messages without discarding their authored variants."""
    parameters = {f"item{index}": value for index, value in enumerate(values)}
    if not parameters:
        return ""
    escaped_separator = separator.replace("{", "{{").replace("}", "}}")
    template = escaped_separator.join("{" + key + "}" for key in parameters)
    return message(template, template, **parameters)


def error_text(error: Exception) -> str:
    """Retain an authored exception message; external exceptions stay verbatim."""
    if len(error.args) == 1 and isinstance(error.args[0], _PresentationText):
        return error.args[0]
    return str(error)


def _render(value, language):
    if isinstance(value, _PresentationText):
        return _PresentationText(value._italian, value._english, value._values, language=language)
    if isinstance(value, dict):
        return {deepcopy(key): _render(item, language) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, language) for item in value]
    if isinstance(value, tuple):
        return tuple(_render(item, language) for item in value)
    return deepcopy(value)


def render_payload(payload, *, language=None):
    """Return a separate presentation view, retaining pairs for composed caches."""
    return _render(payload, capture_language(language))
