"""Pure snapshot quote comparison; model valuation and sanity keep their own date.

Contract /1 uses the previous weekday, not an exchange holiday calendar or an
intraday freshness promise. A different policy requires a new contract version.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONTRACT = 'market_quote/1'
SCENARIOS = ('bear', 'base', 'bull')
STATUSES = frozenset({'ok', 'stale', 'data_missing', 'currency_mismatch', 'fx_not_rolled',
                      'identity_mismatch', 'source_unavailable'})
FRESHNESS_POLICY = 'previous_weekday; holidays_not_modelled'


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
    except OverflowError:
        return False


def _number(value):
    return value if _finite(value) else None


def _day(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            return parsed if parsed.isoformat() == value else None
        except ValueError:
            pass
    return None


def _freshness(observation, reference):
    observed, reference = _day(observation), _day(reference)
    if observed is None or reference is None:
        return 'data_missing'
    if (observed - reference).days > 1:
        return 'source_unavailable'
    previous = reference
    try:
        previous -= timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
    except OverflowError:
        return 'data_missing'
    return 'stale' if observed < previous else 'ok'


def _observed_time(info):
    stamp, zone = info.get('regularMarketTime'), _text(info.get('exchangeTimezoneName'))
    if not _finite(stamp) or zone is None:
        return None, None
    try:
        observed = datetime.fromtimestamp(stamp, tz=timezone.utc)
        local = observed.astimezone(ZoneInfo(zone))
        return observed.isoformat().replace('+00:00', 'Z'), local.date().isoformat()
    except (ValueError, OverflowError, OSError, ZoneInfoNotFoundError):
        return None, None


def _percent(numerator, denominator):
    if not _finite(numerator) or not _finite(denominator) or denominator <= 0:
        return None
    value = (numerator / denominator - 1) * 100
    return round(value, 1) if _finite(value) else None


def build_market_quote(bundle, quotation, fair_values):
    """Build raw /1 evidence using only the acquired profile; no provider/DB I/O.

    fair_values maps bear/base/bull to the payload's final values in quote units.
    It does not recalculate, roll forward or convert the fair values.
    """
    case = _mapping(_mapping(bundle).get('case'))
    source = _mapping(_mapping(case.get('sources')).get('profile'))
    info, quotation, fair_values = _mapping(case.get('info')), _mapping(quotation), _mapping(fair_values)
    price = _number(info.get('regularMarketPrice'))
    price = price if price is not None and price > 0 else None
    observed, local_day = _observed_time(info)
    identity_matches = _text(case.get('ticker')) is not None and info.get('symbol') == case.get('ticker')
    block = {
        'contract': CONTRACT, 'status': 'data_missing', 'message': '',
        'source_id': _text(source.get('source_id')), 'source_status': _text(source.get('status')),
        'acquired_as_of': _text(source.get('as_of')), 'information_cutoff': _text(case.get('as_of')),
        'symbol': _text(case.get('ticker')), 'info_symbol': _text(info.get('symbol')),
        'exchange': _text(info.get('fullExchangeName')), 'exchange_timezone': _text(info.get('exchangeTimezoneName')),
        'quote_source_name': _text(info.get('quoteSourceName')),
        'delayed_minutes': _number(info.get('exchangeDataDelayedBy')),
        'currency': _text(info.get('currency')), 'price': price if identity_matches else None,
        'observed_at': observed, 'observed_local_date': local_day,
        'price_model': _number(quotation.get('price')), 'price_model_as_of': _text(quotation.get('price_as_of')),
        'price_move_since_valuation_pct': None, 'fv_basis': 'valuation_date_no_rollforward',
        'sanity_basis': 'price_model', 'freshness_policy': FRESHNESS_POLICY,
        **{'upside_' + scenario + '_pct': None for scenario in SCENARIOS},
    }

    def finish(status, message):
        block.update(status=status, message=message)
        return block

    if source.get('status') != 'ok':
        return finish('source_unavailable', 'Fonte profile non disponibile: ' + str(block['source_status']))
    if price is None:
        return finish('data_missing', 'regularMarketPrice finito e positivo assente; nessun prezzo sostitutivo')
    if observed is None:
        return finish('data_missing', 'regularMarketTime o exchangeTimezoneName assente/non valido')
    if block['symbol'] is None or block['info_symbol'] != block['symbol']:
        block['price'] = None
        return finish('identity_mismatch', 'Simbolo del profilo diverso dal titolo acquisito')
    acquired, cutoff = _day(block['acquired_as_of']), _day(block['information_cutoff'])
    if block['source_id'] is None or acquired is None or cutoff is None:
        return finish('data_missing', 'Fonte, data acquisizione o cutoff informativo mancanti')
    if acquired > cutoff:
        return finish('source_unavailable', 'Fonte acquisita dopo il cutoff informativo')
    freshness = _freshness(local_day, acquired)
    if freshness != 'ok':
        return finish(freshness, 'Quotazione vecchia o data non valida; soglia al feriale precedente, festivi non modellati')
    unit, financial, quote_currency = (_text(quotation.get(key)) for key in
                                       ('quote_unit', 'financial_currency', 'quote_currency'))
    if block['currency'] is None or unit is None or financial is None or quote_currency is None:
        return finish('data_missing', 'Valuta osservata o contratto di quotazione mancanti')
    equivalent = lambda value: 'GBX' if value in ('GBX', 'GBp') else value
    if equivalent(block['currency']) != equivalent(unit):
        return finish('currency_mismatch', 'Unita della quotazione osservata diversa dal fair value')
    if financial != quote_currency:
        return finish('fx_not_rolled', 'Fair value convertito al cambio storico: nessun cambio corrente acquisito')
    block['price_move_since_valuation_pct'] = _percent(price, block['price_model'])
    for scenario in SCENARIOS:
        block['upside_' + scenario + '_pct'] = _percent(fair_values.get(scenario), price)
    unavailable = any(_finite(fair_values.get(s)) and block['upside_' + s + '_pct'] is None for s in SCENARIOS)
    message = ('Quotazione osservata; FV alla data valore, senza capitalizzazione. '
               'Freschezza al feriale precedente, festivi non modellati.')
    if unavailable:
        message += ' Upside n.d.: rapporto aritmetico non rappresentabile.'
    return finish('ok', message)


def market_quote_view(block, *, as_of=None, usable=True):
    """Return a copy aged at read time; never recover values hidden by the gate."""
    result = deepcopy(_mapping(block))
    status = result.get('status')
    if (result.get('contract') != CONTRACT or not isinstance(status, str) or status not in STATUSES
            or result.get('freshness_policy') != FRESHNESS_POLICY):
        at_read = 'data_missing'
    elif status == 'ok':
        at_read = _freshness(result.get('observed_local_date'), date.today() if as_of is None else as_of)
    else:
        at_read = status
    result['status_at_read'] = at_read
    for scenario in SCENARIOS:
        key = 'upside_' + scenario + '_pct'
        if not usable or at_read != 'ok' or not _finite(result.get(key)):
            result[key] = None
    return result


def attest_market_quote(payload, bundle, quotation):
    """Attest raw /1 evidence, acquisition status and arithmetic, never today's age.

    Legacy payloads without a block remain readable. Presentation text is free;
    every other field is a reproducible part of the versioned contract.
    """
    payload = _mapping(payload)
    supplied = payload.get('market_quote')
    if supplied is None:
        return []
    expected = build_market_quote(bundle, quotation, {s: payload.get('fair_value_' + s) for s in SCENARIOS})
    if not isinstance(supplied, dict) or set(supplied) != set(expected):
        return ['market_quote: contratto non riproducibile dallo snapshot di acquisizione']
    if any(supplied[key] != value for key, value in expected.items() if key != 'message'):
        return ['market_quote: osservazioni, stato o upside non riproducibili dallo snapshot di acquisizione']
    return []
