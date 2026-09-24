"""Recognize explicit source spellings without changing quoted text or periods."""
from datetime import date
import re

_MONTHS = 'January February March April May June July August September October November December'.split()
_DATE = re.compile(r'(?<!\w)(?:([0-9]{4}-[0-9]{2}-[0-9]{2})|('
    + '|'.join(_MONTHS) + r')\s+([0-9]{1,2}),\s+([0-9]{4}))(?!\w)', re.I)
_OTHER_DATE = re.compile(r'(?<![\w.,])(?:[0-9]{4}|[0-9]{1,2}[/.][0-9]{1,2}[/.][0-9]{2,4})(?!\w)')
_NUMBER_TOKEN = re.compile(r'(?<![\w.,])[-+]?[0-9][0-9.,]*(?:[eE][-+]?[0-9]+)?(?![\w.,])')
_NUMBER = re.compile(r'[-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?')


def literal_period_matches(span, quote, period, source):
    """One unique contiguous span, one exact date; no implicit locale parsing."""
    if not isinstance(span, str) or span.count(quote) != 1 or source.count(span) != 1:
        return False
    matches = list(_DATE.finditer(span))
    if len(matches) != 1:
        return False
    match = matches[0]
    try:
        observed = (date.fromisoformat(match[1]) if match[1] else date(int(match[4]),
            [m.lower() for m in _MONTHS].index(match[2].lower()) + 1, int(match[3])))
    except ValueError:
        return False
    # A dangling comparative year or unsupported numeric date cannot borrow the
    # recognized date. The isolated measured number is not another date claim.
    remaining = span[:match.start()] + ' ' * len(match[0]) + span[match.end():]
    remaining = remaining.replace(quote, ' ' * len(quote))
    return observed.isoformat() == period and _OTHER_DATE.search(remaining) is None


def literal_number(quote):
    """Only plain decimals or explicit comma groups of three, with decimal dot."""
    if re.search(r'\([^)]*[0-9][^)]*\)|[\u2212\u2013\u2014\u00b1]\s*[0-9]|[-+]\s+[0-9]|'
                 r'[-+](?:\s*[-+])+\s*[0-9]|\b(?:minus|negative|plus|positive)\s+[0-9]', quote, re.I):
        return None  # Unsupported sign spellings must never become positive.
    tokens = _NUMBER_TOKEN.findall(quote)
    if len(tokens) != 1 or _NUMBER.fullmatch(tokens[0]) is None:
        return None
    return float(tokens[0].replace(',', ''))
