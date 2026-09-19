"""Append observed quote evidence without moving the workbook's existing value cells."""
from .i18n_excel import label


def quote_comparison_text(payload, *, as_of=None):
    """Presentation only: price evidence is retained; missing upside stays unavailable."""
    import math
    from .i18n import label as text
    from bellomberg.valuation.market_quote import market_quote_view
    usable = (payload.get('valuation_usability') or {}).get('usable') is True
    quote = market_quote_view(payload.get('market_quote'), as_of=as_of, usable=usable)
    def number(value, suffix=''):
        try:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        except OverflowError:
            valid = False
        if valid:
            return f'{value:g}' + suffix
        return text('quote.unavailable')
    missing = text('quote.unavailable')
    return text('quote.comparison', model_price=number(payload.get('price')),
                model_date=payload.get('valuation_date') or missing,
                model_upside=number(payload.get('upside_pct') if usable else None, '%'),
                observed_price=number(quote.get('price')), currency=quote.get('currency') or missing,
                observed_at=quote.get('observed_at') or missing,
                source=quote.get('source_id') or missing,
                current_upside=number(quote.get('upside_base_pct'), '%'),
                status=quote.get('status_at_read') or 'data_missing')


def append_market_quote_rows(worksheet, block, *, usable):
    from bellomberg.valuation.market_quote import market_quote_view
    # Workbooks are acquisition snapshots. Later readers evaluate freshness separately.
    quote = market_quote_view(block, as_of=(block or {}).get('acquired_as_of'), usable=usable)
    fields = (
        (label('Quote: model price'), 'price_model'), (label('Quote: model date'), 'price_model_as_of'),
        (label('Quote: observed price'), 'price'), (label('Quote: currency'), 'currency'),
        (label('Quote: observed at UTC'), 'observed_at'), (label('Quote: source'), 'source_id'),
        (label('Quote: exchange'), 'exchange'), (label('Quote: delay minutes'), 'delayed_minutes'),
        (label('Quote: acquisition status'), 'status'), (label('Quote: bear upside percent'), 'upside_bear_pct'),
        (label('Quote: base upside percent'), 'upside_base_pct'), (label('Quote: bull upside percent'), 'upside_bull_pct'),
    )
    worksheet.append([label('Quote: comparison note'), label('Quote: historical FV note')])
    for caption, key in fields:
        value = quote.get(key)
        worksheet.append([caption, value if value is not None else label('Quote: unavailable')])
