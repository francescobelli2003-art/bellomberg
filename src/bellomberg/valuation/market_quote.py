"""Pure snapshot quote comparison; model valuation and sanity keep their own date.

Contract /1 uses the previous weekday, not an exchange holiday calendar or an
intraday freshness promise. A different policy requires a new contract version.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite, isclose
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONTRACT = 'market_quote/1'
FX_CONTRACT = 'market_quote/2'
FX_POLICY = 'exact_quote_observation_day; no_missing_day_carry'
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


def build_repriced_quote(payload, bundle, quotation, fx_evidence=None):
    """Separate /2 comparison: original financial FV at observed-day ECB FX.

    No forecast, valuation date, original FV, workbook or snapshot is changed.
    The worker persists both raw and derived FX; readers can recompile them.
    Unsupported currency pairs and missing exact-day rates stay incomplete.
    """
    payload, quotation = _mapping(payload), _mapping(quotation)
    fvs = {s: payload.get('fair_value_' + s) for s in SCENARIOS}
    block = build_market_quote(bundle, quotation, fvs)
    if block['status'] != 'fx_not_rolled':
        return block
    block.update(contract=FX_CONTRACT, fx=None, comparison_fair_values={},
                 model_valuation_date=_text(payload.get('valuation_date')))
    try:
        from .fx_evidence import normalize_fx, NORMALIZER, LIMITATION
        if _mapping(payload.get('valuation_usability')).get('usable') is not True:
            raise ValueError('modello originale non utilizzabile')
        if _day(block['model_valuation_date']) is None:
            raise ValueError('data del modello assente')
        report = _mapping(fx_evidence)
        if report.get('status') != 'ready':
            raise ValueError('cambio della data osservata non disponibile: ' + str(report.get('issues') or []))
        docs = report.get('documents')
        if not isinstance(docs, list) or len(docs) != 2 or any(not isinstance(d, dict) for d in docs):
            raise ValueError('fonte ECB originale e normalizzata richieste')
        normalized = [d for d in docs if d.get('origin') == NORMALIZER]
        raw = [d for d in docs if d.get('origin') != NORMALIZER]
        if len(normalized) != 1 or len(raw) != 1:
            raise ValueError('fonti FX mancanti o ambigue')
        financial, currency = quotation['financial_currency'], quotation['quote_currency']
        expected = normalize_fx(raw[0], financial_currency=financial, quote_currency=currency,
                                on=block['observed_local_date'], as_of=block['information_cutoff'])
        if expected != normalized[0]:
            raise ValueError('cambio non riproducibile dalla fonte ECB originale')
        rate = json.loads(expected['text'])['facts'][0]['value']
        historical, units, shares = (quotation[k] for k in
            ('financial_to_quote_rate', 'quote_units_per_currency', 'shares_per_quote'))
        if any(not _finite(v) or v <= 0 for v in (historical, units, shares)):
            raise ValueError('conversione o classe azionaria originale non valida')
        if not ((quotation['quote_unit'] == currency and units == 1)
                or (currency == 'GBP' and quotation['quote_unit'] in ('GBX', 'GBp') and units == 100)):
            raise ValueError('unita quotazione e valuta non riconciliate')
        if payload.get('financial_currency') != financial or payload.get('currency') != quotation['quote_unit']:
            raise ValueError('valute del modello e della quotazione divergenti')
        scenarios = _mapping(_mapping(payload.get('calculation_details')).get('scenarios'))
        converted = {}
        for scenario in SCENARIOS:
            value = _mapping(scenarios.get(scenario)).get('fair_value_per_share')
            if not _finite(value) or value <= 0 or not _finite(fvs[scenario]):
                raise ValueError('valore originale in valuta finanziaria mancante: ' + scenario)
            original = value * (historical * units * shares)
            if not _finite(original) or not (isclose(original, fvs[scenario], rel_tol=1e-9, abs_tol=1e-8)
                                            or round(original, 2) == fvs[scenario]):
                raise ValueError('valore finanziario non riconciliato al modello: ' + scenario)
            current = value * (rate * units * shares)
            if not _finite(current) or current <= 0 or _percent(current, block['price']) is None:
                raise ValueError('conversione corrente non rappresentabile: ' + scenario)
            converted[scenario] = current
        block.update(status='ok', fv_basis='valuation_date_current_fx_no_rollforward',
            comparison_fair_values=converted, price_move_since_valuation_pct=_percent(block['price'], block['price_model']),
            fx={'policy': FX_POLICY, 'on': block['observed_local_date'], 'rate': rate,
                'financial_currency': financial, 'quote_currency': currency,
                'source_id': expected['id'], 'source_url': expected['url'],
                'source_sha256': raw[0]['sha256'], 'available_at': expected['available_at'],
                'retrieved_at': raw[0]['retrieval']['retrieved_at'], 'limitation': LIMITATION},
            message='FV alla data del modello, ritradotto al cambio ECB della data del prezzo; '
                    'nessuna capitalizzazione o revisione delle ipotesi. ' + LIMITATION)
        for scenario in SCENARIOS:
            block['upside_' + scenario + '_pct'] = _percent(converted[scenario], block['price'])
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        block['message'] = 'Confronto multivaluta incompleto: ' + str(exc)
    return block


def market_quote_view(block, *, as_of=None, usable=True):
    """Return a copy aged at read time; never recover values hidden by the gate."""
    result = deepcopy(_mapping(block))
    status = result.get('status')
    fx = _mapping(result.get('fx'))
    valid_contract = result.get('contract') == CONTRACT or (result.get('contract') == FX_CONTRACT
        and fx.get('policy') == FX_POLICY and fx.get('on') == result.get('observed_local_date')
        and _finite(fx.get('rate')) and fx['rate'] > 0
        and result.get('fv_basis') == 'valuation_date_current_fx_no_rollforward')
    if (not valid_contract or not isinstance(status, str) or status not in STATUSES
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


def attest_repriced_job(job, payload):
    """Recompile the /2 comparison from persisted acquisitions and exact model."""
    try:
        result, request, checkpoint = job['result'], job['request'], job['checkpoint']
        if (checkpoint['stage'] != 'quote_fx_acquired' or job['ticker'] != payload['ticker']
                or request['generation_id'] != payload['generation_id']
                or result['generation_id'] != payload['generation_id']
                or result['snapshot_id'] != payload['snapshot_id']):
            return False
        rows = [r for r in payload['acquisition_snapshot']['case']['records']
                if r.get('driver') == 'quotation' and r.get('scenario') == 'model']
        if len(rows) != 1:
            return False
        profile = checkpoint['payload']['profile']
        bundle = {'case': {'ticker': job['ticker'], 'as_of': request['as_of'],
            'info': profile['data']['info'], 'sources': {'profile': profile}}}
        expected = build_repriced_quote(payload, bundle, rows[0]['value'], checkpoint['payload']['fx_evidence'])
        supplied = result['market_quote']
        return (expected['contract'] == FX_CONTRACT and expected['status'] == 'ok'
                and isinstance(supplied, dict) and set(supplied) == set(expected)
                and all(supplied[k] == v for k, v in expected.items() if k != 'message'))
    except (KeyError, TypeError, ValueError):
        return False


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
