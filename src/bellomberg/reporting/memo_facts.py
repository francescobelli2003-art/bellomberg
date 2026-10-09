"""Offline weekly fact tracing: mechanical flags, never approval or semantic truth.

Only explicit schemas/labels are interpreted. Unavailable or ambiguous scope is
reported, not replaced by a pool of numbers from other issuers or periods.
"""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from hashlib import sha256
import ast
import json
import re

from bellomberg.core.memo_numbers import (
    _quantity_spans_v4, _local_number_values, _number_attested,
    _UNIT_SCALE, _field_scale)

VERSION = 'weekly-facts/1'
_ROLES = {'system', 'user', 'assistant', 'tool'}
_METRICS = {'revenue': ('revenue', 'ricavi'), 'eps': ('eps',),
            'net_income': ('net income', 'utile netto', 'net_income'),
            'free_cash_flow': ('free cash flow', 'free_cash_flow', 'fcf'),
            'operating_cash_flow': ('operating cash flow', 'operating_cash_flow', 'ocf'),
            'debt': ('debt', 'debito')}
_DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
_PERIOD = re.compile(r'\b(?:(?:FY|CY)\s?\d{4}|Q[1-4][ -]+(?:FY|CY)?\s?\d{4}|\d{4}-\d{2}-\d{2})\b', re.I)
_TAG = re.compile(r'\[src:\s*([^\]\n]+)\]', re.I)
_CURRENCY = re.compile(r'\b(EUR|USD|GBP|GBX)\b|[\u20ac$]')
_EVENTS = re.compile(r'\b(earnings|risultati|cpi|fomc)\b', re.I)
_DEFAULT = {'context_presence': 'UNAVAILABLE', 'tool_attribution': 'UNAVAILABLE',
            'desk_attribution': 'UNAVAILABLE', 'semantic_scope': 'NOT_ASSESSED',
            'calculation': 'NOT_APPLICABLE', 'event_date': 'NOT_APPLICABLE'}


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                            allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _text_hash(value):
    return sha256(value.encode('utf-8')).hexdigest()


def _day(value):
    if not isinstance(value, str):
        raise ValueError('date unavailable')
    return date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value.replace('Z', '+00:00')).date()


def _issue(issues, code, ref=None):
    item = {'code': code}
    if ref is not None:
        item['reference_hash'] = _text_hash(str(ref))
    issues.append(item)


def _metric(value):
    normalized = str(value).lower().replace('-', '_')
    for key, aliases in _METRICS.items():
        if normalized == key or any(normalized == a.replace(' ', '_') for a in aliases):
            return key
        for alias in aliases:
            prefix = alias.replace(' ', '_') + '_'
            # Only dimensional suffixes: adjusted/per-share metrics are distinct.
            if normalized.startswith(prefix) and re.fullmatch(
                    r'(?:(?:abs|growth|delta|change)_)?(?:(?:eur|usd|gbp|gbx)_)?'
                    r'(?:pct|bn|b|mld|billions?|m|mn|mm|mln|millions?|k|thousands?)'
                    r'|(?:abs|growth|delta|change|eur|usd|gbp|gbx)', normalized[len(prefix):]):
                return key
    return None


def _claim_metric(text, ticker, token):
    """Closed clause grammar, not substring NLP: every word must be accounted for."""
    if not ticker:
        return None
    names = {alias.lower(): key for key, aliases in _METRICS.items() for alias in aliases}
    metric = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    issuer = r'(?:' + re.escape(ticker) + r'|\[ticker:\s*' + re.escape(ticker) + r'\])'
    measure = r'(?:growth|crescita|variazione|delta|yoy)'
    duration = r'(?:annual|annuale|yearly|quarterly|trimestrale|ttm|ytd|semiannual|semestrale)'
    paired = r'\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}\s+vs\s+\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}'
    pattern = (r'\s*' + issuer + r'\s+(?P<metric>' + metric + r')'
               + r'(?:\s+' + measure + r')?(?:\s+' + duration + r')?\s+'
               + re.escape(token) + r'(?:\s+(?:EUR|USD|GBP|GBX|\u20ac|\$))?\s+'
               + r'(?:' + paired + '|' + _PERIOD.pattern + r')\s*'
               + _TAG.pattern + r'\s*')
    found = re.fullmatch(pattern, text, re.I)
    return names[found.group('metric').lower()] if found else None


def _period(value):
    if value is None:
        return None
    normalized = re.sub(r'\s+', '', str(value).upper())
    # Producer spells Q3 2026 as Q3-2026; never infer FY=CY or expand a year.
    return re.sub(r'^(Q[1-4])-(\d{4})$', r'\1\2', normalized)


def _claim_period(text):
    paired = re.search(r'(\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2})\s+vs\s+(\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2})', text, re.I)
    if paired:
        return _period(paired.group()) if len(_DATE.findall(text)) == 4 else None
    found = list(dict.fromkeys(_period(m.group()) for m in _PERIOD.finditer(text)))
    return found[0] if len(found) == 1 else None


def _unit(text, token=''):
    if token.endswith('%'):
        return 'pct'
    if token.lower().endswith(('bps', 'pp')):
        return token.lower().split()[-1]
    found = { {'\u20ac': 'EUR', '$': 'USD'}.get(m.group(), m.group()) for m in _CURRENCY.finditer(text)}
    return next(iter(found)) if len(found) == 1 else None


def _numeric(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def _token_values(token, language):
    unit = re.sub(r'^[-+\u2212]?[\d.,]+\s*', '', token).lower()
    exponent = _UNIT_SCALE.get(unit, 0)
    return [(value, exponent) for value, _ in _local_number_values(token, language)]


def _context(snapshot, issues, language):
    context = snapshot.get('context')
    if not isinstance(context, dict):
        _issue(issues, 'CONTEXT_UNAVAILABLE')
        return [], 'UNAVAILABLE'
    parts = []
    invalid = False
    rows = context.get('parts')
    if not isinstance(rows, list):
        rows = []
        invalid = True
    ids = Counter(str(p.get('id')) for p in rows if isinstance(p, dict))
    for p in rows:
        if (not isinstance(p, dict) or not isinstance(p.get('text'), str)
                or not isinstance(p.get('id'), str) or not p['id'] or ids[p['id']] != 1
                or p.get('role') not in _ROLES or p.get('sha256') != _text_hash(p['text'])):
            _issue(issues, 'CONTEXT_PART_INVALID')
            invalid = True
            continue
        numbers = []
        for token, _, _ in _quantity_spans_v4(p['text']):
            numbers.extend(_token_values(token, language))
        parts.append({'id': p['id'], 'role': p['role'], 'kind': p.get('kind'),
                      'desk': p.get('desk'), 'numbers': numbers, 'text': p['text']})
    att = context.get('attestation') or {}
    complete = (context.get('status') == 'COMPLETE' and not invalid
                and context.get('missing_parts') == [] and isinstance(att, dict)
                and att.get('status') == 'VERIFIED'
                and att.get('basis') in ('wire_journal', 'verified_single_request_checkpoint')
                and isinstance(att.get('request_ids'), list) and bool(att['request_ids'])
                and all(isinstance(v, str) and v for v in att['request_ids']))
    if not complete or not parts:
        _issue(issues, 'CONTEXT_PARTIAL')
    return parts, 'COMPLETE' if complete and parts else 'PARTIAL' if parts else 'UNAVAILABLE'


def _moment(value):
    if not isinstance(value, str):
        raise ValueError('date unavailable')
    if len(value) == 10:
        return date.fromisoformat(value)
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timezone unavailable')
    return result


def _after_cutoff(value, cutoff):
    moment = _moment(value)
    if isinstance(moment, datetime) and isinstance(cutoff, datetime):
        return moment > cutoff
    # A date-only availability statement has day precision, never invented hours.
    day = moment.date() if isinstance(moment, datetime) else moment
    cutoff_day = cutoff.date() if isinstance(cutoff, datetime) else cutoff
    return day > cutoff_day


def _duration(value):
    values = {'annual': 'annual', 'annuale': 'annual', 'yearly': 'annual',
              'quarterly': 'quarterly', 'trimestrale': 'quarterly',
              'ttm': 'ttm', 'ytd': 'ytd', 'semiannual': 'semiannual', 'semestrale': 'semiannual'}
    return values.get(str(value).lower())


def _claim_duration(text):
    found = {_duration(m.group()) for m in re.finditer(
        r'\b(?:annual|annuale|yearly|quarterly|trimestrale|ttm|ytd|semiannual|semestrale)\b', text, re.I)}
    return next(iter(found)) if len(found) == 1 else 'AMBIGUOUS' if found else None


def _country(text):
    aliases = {'US': ('US', 'USA', 'United States', 'Stati Uniti'),
               'JP': ('JP', 'Japan', 'Giappone'), 'GB': ('GB', 'UK', 'United Kingdom'),
               'IT': ('IT', 'Italy', 'Italia'), 'DE': ('DE', 'Germany', 'Germania'),
               'FR': ('FR', 'France', 'Francia'), 'CN': ('CN', 'China', 'Cina')}
    found = {code for code, names in aliases.items() if any(
        re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I) for name in names)}
    return next(iter(found)) if len(found) == 1 else None


def _unavailable_node(node, cutoff):
    if (node.get('error') or node.get('ok') is False or node.get('success') is False
            or node.get('stale') is True or node.get('is_stale') is True
            or node.get('source_verified') is False or node.get('fonte_primaria_verificata') is False
            or str(node.get('status') or node.get('stato') or '').upper() in
               {'STALE', 'ERROR', 'FAILED', 'UNAVAILABLE', 'SUPERSEDED'}):
        return 'SCOPE_UNAVAILABLE'
    # These are availability dates, NOT fiscal periods or future event dates.
    for key in ('published_at', 'source_date', 'observed_at', 'as_of', 'available_at'):
        if node.get(key) is not None:
            try:
                if _after_cutoff(node[key], cutoff):
                    return 'SOURCE_AFTER_CUTOFF'
            except (ValueError, TypeError):
                return 'SOURCE_DATE_UNAVAILABLE'
    return None


def _scopes(payload, *, tool, ticker, cutoff, issues, ref, admitted_nodes=None):
    scopes, events = [], []
    excluded = 0

    def add(value, metric, period, unit, exponent, path, measure='level', duration=None):
        number = _numeric(value)
        if number is not None:
            scopes.append({'value': number, 'metric': metric, 'period': _period(period),
                           'unit': unit, 'exponent': exponent, 'path': path,
                           'measure': measure, 'duration': duration})

    def walk(node, path, depth=0):
        nonlocal excluded
        if depth > 16:
            excluded += 1
            _issue(issues, 'SCOPE_DEPTH_UNSUPPORTED', ref)
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, path + '/' + str(index), depth + 1)
            return
        if not isinstance(node, dict):
            return
        unavailable = _unavailable_node(node, cutoff)
        if unavailable:
            excluded += 1
            _issue(issues, unavailable, ref + path)
            return
        if node.get('ticker') and node['ticker'] != ticker:
            excluded += 1
            _issue(issues, 'SCOPE_TICKER_DIFFERS', ref + path)
            return
        if admitted_nodes is not None:
            admitted_nodes.add(path)
        period = node.get('period') or node.get('period_end') or node.get('fiscal_date')
        currency = node.get('currency') or node.get('valuta')
        if tool == 'get_guidance' and isinstance(node.get('active'), list):
            for index, item in enumerate(node['active']):
                itempath = path + '/active/' + str(index)
                if not isinstance(item, dict) or item.get('status') != 'active':
                    excluded += 1
                    _issue(issues, 'GUIDANCE_NOT_ACTIVE', ref + itempath)
                else:
                    walk(item, itempath, depth + 1)
            if node.get('history'):
                excluded += len(node['history']) if isinstance(node['history'], list) else 1
                _issue(issues, 'GUIDANCE_HISTORY_EXCLUDED', ref + path)
            return
        if tool == 'get_guidance' and node.get('metric'):
            rawunit = node.get('unit')
            # MemoryDB.GUIDANCE_UNITS: pct is a FRACTION, unlike Filing delta_pct.
            unit, exponent = {'musd': ('USD', 6), 'pct': ('pct', 2),
                              'eps': (None, 0)}.get(rawunit, (rawunit, 0))
            for key in ('value_low', 'value_mid', 'value_high'):
                add(node.get(key), _metric(node['metric']), period, unit, exponent, path + '/' + key,
                    'change' if node['metric'] == 'revenue_growth' else 'level')
            return
        if node.get('voce') and 'delta_pct' in node:
            periods = node.get('periodi') or {}
            before, after = periods.get('prima') or {}, periods.get('dopo') or {}
            period = (after.get('inizio', '') + '/' + after.get('fine', '') + ' vs '
                      + before.get('inizio', '') + '/' + before.get('fine', '')) if periods else None
            add(node['delta_pct'], _metric(node['voce']), period, 'pct', 0, path + '/delta_pct', 'change')
            return
        event_type = str(node.get('event_type') or '').lower()
        if event_type in ('earnings', 'cpi', 'fomc') and node.get('event_date'):
            try:
                events.append({'event_type': event_type, 'date': _day(node['event_date']).isoformat(),
                               'estimated': node.get('date_estimated') is not False,
                               'path': path, 'ticker': node.get('ticker') or ticker,
                               'country': _country(str(node.get('country') or ''))})
            except (ValueError, TypeError):
                excluded += 1
                _issue(issues, 'EVENT_DATE_UNAVAILABLE', ref + path)
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                walk(value, path + '/' + key.replace('~', '~0').replace('/', '~1'), depth + 1)
            elif _metric(key):
                match = re.search(r'(?:^|_)(eur|usd|gbp|gbx)(?:_|$)', key, re.I)
                unit = match.group(1).upper() if match else currency
                if key.endswith('_pct'):
                    unit = 'pct'
                measure = 'change' if re.search(r'_(?:growth|delta|change)(?:_|$)', key) else 'level'
                add(value, _metric(key), period, unit, _field_scale(key), path + '/' + key, measure, _duration(node.get('period_type')))
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                # Numeric presence within an untyped tool field is NOT a metric proof.
                add(value, None, period, None, 0, path + '/' + key)
    walk(payload, '')
    return scopes, events, excluded


def _receipts(snapshot, cutoff, issues, *, followup=False):
    rows = snapshot.get('receipts')
    if not isinstance(rows, list):
        _issue(issues, 'RECEIPTS_UNAVAILABLE')
        return [], 0, 0, 0
    ids = Counter(str(r.get('id')) for r in rows if isinstance(r, dict))
    valid, rejected, excluded = [], 0, 0
    for entry in rows:
        try:
            if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id'] or ids[entry['id']] != 1:
                raise ValueError('RECEIPT_ID_INVALID')
            row = entry.get('receipt')
            if not isinstance(row, dict) or entry.get('sha256') != _digest(row):
                raise ValueError('RECEIPT_HASH_INVALID')
            if row.get('success') is not True or row.get('truncated') is not False:
                raise ValueError('RECEIPT_UNAVAILABLE')
            if not isinstance(row.get('tool'), str) or not isinstance(row.get('input'), dict):
                raise ValueError('RECEIPT_IDENTITY_UNAVAILABLE')
            ticker = row['input'].get('ticker')
            payload = json.loads(row.get('output') or '')
            if not isinstance(payload, dict):
                raise ValueError('RECEIPT_JSON_UNSUPPORTED')
            admitted_nodes = set() if followup else None
            scopes, events, gaps = _scopes(payload, tool=row['tool'], ticker=ticker, cutoff=cutoff,
                                           issues=issues, ref=entry['id'], admitted_nodes=admitted_nodes)
            excluded += gaps
            valid.append({'id': entry['id'], 'tool': row['tool'], 'ticker': ticker,
                          'scopes': scopes, 'events': events, 'sha256': entry['sha256']})
            if followup:
                valid[-1]['admitted_scope_paths'] = admitted_nodes
        except (ValueError, TypeError, OverflowError) as exc:
            rejected += 1
            # Local controlled codes only; never raw decoder/provider messages.
            code = str(exc) if type(exc) is ValueError and str(exc).startswith('RECEIPT_') else 'RECEIPT_INVALID'
            _issue(issues, code)
    return valid, len(rows), rejected, excluded


def _ticker(text, receipts):
    explicit = re.findall(r'\[ticker:\s*([A-Z0-9.^=_/-]+)\]', text)
    known = {r['ticker'] for r in receipts if isinstance(r.get('ticker'), str)
             and re.search(r'(?<![\w.])' + re.escape(r['ticker']) + r'(?![\w.])', text)}
    symbols = set(explicit) | known
    return next(iter(symbols)) if len(symbols) == 1 else None


def _finding(kind, start, end, source, dimensions=None):
    return {'id': '', 'kind': kind, 'span': [start, end],
            'line': source.count('\n', 0, start) + 1,
            'token_hash': _text_hash(source[start:end]),
            'dimensions': {**_DEFAULT, **(dimensions or {})}, 'references': []}


def _presence(finding, token, parts, completeness, language, clause):
    matched = [p for p in parts if _number_attested(token, language, p['numbers'])]
    finding['dimensions']['context_presence'] = 'MATCH' if matched else (
        'NOT_FOUND' if completeness == 'COMPLETE' else 'UNAVAILABLE')
    finding['references'].extend({'part_id': p['id'], 'role': p['role'], 'kind': p['kind']}
                                 for p in matched)
    desks = {p['desk'] for p in matched if p['role'] in ('user', 'tool')
             and p['kind'] == 'desk_report' and isinstance(p['desk'], str) and p['desk']}
    declared = set(re.findall(r'\[desk:\s*([a-z_]+)\]|\b([a-z_]+)\s+desk\s*:', clause, re.I))
    declared = {name.lower() for pair in declared for name in pair if name}
    if declared:
        desks = {desk for desk in desks if desk.lower() in declared} if len(declared) == 1 else set()
    finding['dimensions']['desk_attribution'] = ('REPORT_MATCH' if len(desks) == 1
                                                else 'AMBIGUOUS' if desks else 'UNAVAILABLE')


def _semantic_number_attested(token, language, scope):
    """Typed quantities: honor declared scale and pct units, never infer ratios."""
    unit = re.sub(r'^[-+\u2212]?[\d.,]+\s*', '', token.strip()).lower()
    if unit == '%' and (',' if language == 'en' else '.') in token:
        return False
    exponent = _UNIT_SCALE.get(unit, 0)
    try:
        candidate = scope['value'].scaleb(scope['exponent'] - exponent)
        return any(candidate.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) == value
                   for value, places in _local_number_values(token, language))
    except ArithmeticError:
        return False


def _attribute(finding, token, clause, receipts, language):
    tags = list(dict.fromkeys(t.strip() for t in _TAG.findall(clause)))
    if not tags:
        return
    ticker = _ticker(clause, receipts)
    if len(tags) != 1:
        finding['dimensions']['tool_attribution'] = 'AMBIGUOUS'
        return
    candidates = [r for r in receipts if r['tool'] == tags[0] and ticker and r['ticker'] == ticker]
    if not candidates:
        finding['dimensions']['tool_attribution'] = 'NO_RECEIPT'
        return
    metric, period, unit = _claim_metric(clause, ticker, token), _claim_period(clause), _unit(clause, token)
    selected = [(r, s) for r in candidates for s in r['scopes']]
    matching = [(r, s) for r, s in selected if _number_attested(token, language, [(s['value'], s['exponent'])])]
    receipt_ids = {r['id'] for r, _ in matching}
    finding['dimensions']['tool_attribution'] = ('MATCH' if len(receipt_ids) == 1 else
                                                'AMBIGUOUS' if receipt_ids else 'NO_RECEIPT')
    finding['references'].extend({'receipt_id': r['id'], 'sha256': r['sha256'], 'path': s['path']}
                                 for r, s in matching)
    if not metric or not period or not unit:
        return
    typed = [(r, s) for r, s in selected if s['metric'] and s['period'] and s['unit']]
    if not typed:
        return
    measure = 'change' if re.search(r'\b(?:growth|crescita|variazione|delta|yoy|vs)\b', clause, re.I) else 'level'
    duration = _claim_duration(clause)
    if duration == 'AMBIGUOUS' or (duration and any(s['duration'] is None for _, s in typed)):
        return
    scoped = [(r, s) for r, s in typed if s['metric'] == metric and s['period'] == period
              and s['unit'] == unit and s['measure'] == measure
              and (not duration or s['duration'] == duration)]
    if len(scoped) > 1:
        finding['dimensions']['semantic_scope'] = 'AMBIGUOUS'
    elif len(scoped) == 1:
        s = scoped[0][1]
        finding['dimensions']['semantic_scope'] = ('CONSISTENT_EXPLICIT' if
            _semantic_number_attested(token, language, s) else 'MISMATCH_EXPLICIT')
    else:
        finding['dimensions']['semantic_scope'] = 'MISMATCH_EXPLICIT'


def _arithmetic(expression, language):
    """Literal arithmetic only. AST is walked, never compiled or evaluated."""
    if len(expression) > 400 or expression.count('=') != 1:
        return 'INCOMPLETE'
    left, right = expression.split('=', 1)
    right = right.strip()
    if not re.fullmatch(r'[-+\u2212]?[\d.,]+\s*%?', right):
        return 'INCOMPLETE'
    try:
        tree = ast.parse(left.strip(), mode='eval')
        if sum(1 for _ in ast.walk(tree)) > 80:
            return 'INCOMPLETE'
        def walk(node):
            if isinstance(node, ast.Expression):
                return walk(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                segment = ast.get_source_segment(left.strip(), node)
                if not re.fullmatch(r'\d+(?:\.\d+)?', segment or ''):
                    raise ValueError('literal unsupported')
                return Decimal(segment)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = walk(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                a, b = walk(node.left), walk(node.right)
                if isinstance(node.op, ast.Add): return a + b
                if isinstance(node.op, ast.Sub): return a - b
                if isinstance(node.op, ast.Mult): return a * b
                return a / b
            raise ValueError('operator unsupported')
        with localcontext() as ctx:
            ctx.prec = 40
            result = walk(tree)
            if right.endswith('%'):
                result *= 100
            values = _local_number_values(right, language)
            if len(values) != 1 or not result.is_finite():
                return 'INCOMPLETE'
            expected, places = next(iter(values))
            return 'CONSISTENT_ARITHMETIC' if result.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) == expected else 'MISMATCH_ARITHMETIC'
    except (ValueError, SyntaxError, ArithmeticError, RecursionError):
        return 'INCOMPLETE'


def _followup_period_kind(value):
    """Comparable labels only: an end date, FY, CY and provider bucket differ."""
    if not isinstance(value, str):
        return None
    value = _period(value)
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            _day(value)
            return 'date'
        except ValueError:
            return None
    found = re.fullmatch(r'(FY|CY|Q[1-4](?:FY|CY)?)\d{4}', value)
    return re.sub(r'^Q[1-4]', 'Q', found.group(1)) if found else None


def _followup_attribute(finding, token, clause, receipts, projection, language):
    """New contract only: missing fiscal/unit/duration evidence is not falsehood.

    Reuses the closed claim grammar and numeric comparison of /1; only an exact
    receipt path admitted by its source/cutoff checks can supply scoped metadata.
    """
    finding['dimensions']['semantic_scope'] = 'NOT_ASSESSED'
    tags = list(dict.fromkeys(t.strip() for t in _TAG.findall(clause)))
    ticker = _ticker(clause, receipts)
    metric = _claim_metric(clause, ticker, token)
    period, unit, duration = _claim_period(clause), _unit(clause, token), _claim_duration(clause)
    kind = _followup_period_kind(period)
    if (len(tags) != 1 or not ticker or not metric or not kind or not unit or duration == 'AMBIGUOUS'
            or re.search(r'\b(?:growth|crescita|variazione|delta|yoy|vs)\b', clause, re.I)):
        return
    admitted = {(r['sha256'], s['path']) for r in receipts for s in r['scopes']}
    admitted_metadata = {(r['sha256'], path) for r in receipts for path in r.get('admitted_scope_paths', ())}
    candidates, incomplete, conflicting = [], False, False
    for fact in projection.get('facts', []):
        ref = fact['source_receipt']
        if (ref['tool'] != tags[0] or fact['ticker'] != ticker or _metric(fact['metric']) != metric
                or (ref['sha256'], ref['path']) not in admitted or fact.get('value_status') != 'AVAILABLE'):
            continue
        # The numeric field can survive while its sibling metadata is rejected.
        # Require the same validator's admission for every supplying metadata node.
        metadata_paths = ref.get('metadata_paths')
        if (not metadata_paths or any((ref['sha256'], path) not in admitted_metadata for path in metadata_paths)):
            incomplete = True
            continue
        labels = {_period(fact.get(key)) for key in ('provider_bucket', 'fiscal_year_label', 'period_end')
                  if _followup_period_kind(fact.get(key)) == kind}
        raw_unit = fact.get('unit')
        currency = fact.get('currency')
        actual_unit = currency if currency in ('EUR', 'USD', 'GBP', 'GBX') else raw_unit
        scale = _UNIT_SCALE.get(str(raw_unit).lower(), 0)
        if (not labels or actual_unit not in ('EUR', 'USD', 'GBP', 'GBX', 'pct')
                or (raw_unit is not None and raw_unit not in (actual_unit, str(actual_unit) + '/shares')
                    and str(raw_unit).lower() not in _UNIT_SCALE)
                or (duration and _duration(fact.get('duration')) is None)):
            incomplete = True
            continue
        if len(labels) != 1:
            conflicting = True
            continue
        candidates.append({'value': _numeric(fact['value']), 'exponent': scale,
                           'period': next(iter(labels)), 'unit': actual_unit,
                           'duration': _duration(fact.get('duration'))})
    if conflicting:
        finding['dimensions']['semantic_scope'] = 'AMBIGUOUS'
        return
    matched = [s for s in candidates if s['period'] == period and s['unit'] == unit
               and (not duration or s['duration'] == duration)]
    if len(matched) > 1:
        finding['dimensions']['semantic_scope'] = 'AMBIGUOUS'
    elif len(matched) == 1:
        finding['dimensions']['semantic_scope'] = ('CONSISTENT_EXPLICIT' if
            _semantic_number_attested(token, language, matched[0]) else 'MISMATCH_EXPLICIT')
    elif candidates and not incomplete:
        finding['dimensions']['semantic_scope'] = 'MISMATCH_EXPLICIT'


def _event(finding, clause, receipts):
    identity = list(dict.fromkeys('earnings' if m.group().lower() == 'risultati' else m.group().lower()
                                 for m in _EVENTS.finditer(clause)))
    days = list(dict.fromkeys(m.group() for m in _DATE.finditer(clause)))
    tags = list(dict.fromkeys(t.strip() for t in _TAG.findall(clause)))
    dims = finding['dimensions']
    dims['event_date'] = 'UNAVAILABLE'
    if re.search(r'\b\d{1,2}:\d{2}\b|\b(?:CET|CEST|EST|EDT|UTC|GMT)\b', clause):
        return  # v1 compares explicit dates only; no silent timezone approval.
    if len(identity) != 1 or len(days) != 1 or len(tags) != 1:
        return
    try: _day(days[0])
    except ValueError: return
    ticker = _ticker(clause, receipts)
    country = _country(clause)
    if identity[0] != 'earnings' and not country:
        return
    candidates = [(r, e) for r in receipts for e in r['events']
                  if r['tool'] == tags[0] and e['event_type'] == identity[0]
                  and (identity[0] != 'earnings' or ticker and e['ticker'] == ticker)
                  and (identity[0] == 'earnings' or e['country'] == country)]
    if len(candidates) > 1:
        dims['event_date'] = 'AMBIGUOUS'
    elif candidates:
        r, event = candidates[0]
        dims['event_date'] = ('ESTIMATED' if event['estimated'] else
                             'MATCH_EXPLICIT' if event['date'] == days[0] else 'MISMATCH_EXPLICIT')
        finding['references'].append({'receipt_id': r['id'], 'sha256': r['sha256'], 'path': event['path']})


def audit_memo_facts(source_memo, evidence_snapshot, *, language='it', version=VERSION):
    """Audit explicit fragments of the canonical memo against immutable run evidence."""
    report = {'version': VERSION, 'run_id': None, 'cutoff': None, 'language': language if language in ('it', 'en') else 'it',
              'source_memo_sha256': None, 'snapshot_sha256': None, 'status': 'CHECK_UNAVAILABLE',
              'context_completeness': 'UNAVAILABLE', 'findings': [], 'issues': [], 'counters': {},
              'limitations': ['NUMERIC_MATCH_NOT_TRUTH', 'EXPLICIT_SCHEMAS_ONLY', 'RECOGNIZED_FRAGMENTS_NOT_ALL_CLAIMS',
                              'ARITHMETIC_NOT_OPERAND_ATTESTATION', 'EVENT_IDENTITY_REQUIRED']}
    issues = report['issues']
    try:
        if language not in ('it', 'en') or version != VERSION or not isinstance(source_memo, str) or not isinstance(evidence_snapshot, dict):
            raise ValueError('INPUT_CONTRACT_UNAVAILABLE')
        snapshot = evidence_snapshot
        if snapshot.get('version') != VERSION or not isinstance(snapshot.get('run_id'), str) or not snapshot['run_id']:
            raise ValueError('RUN_IDENTITY_UNAVAILABLE')
        cutoff = _moment(snapshot.get('cutoff'))
        source_hash = _text_hash(source_memo)
        if snapshot.get('source_memo_sha256', source_hash) != source_hash:
            raise ValueError('SOURCE_IDENTITY_DIFFERS')
        report.update(run_id=snapshot['run_id'], cutoff=snapshot['cutoff'], source_memo_sha256=source_hash,
                      snapshot_sha256=_digest(snapshot))
        parts, completeness = _context(snapshot, issues, report['language'])
        report['context_completeness'] = completeness
        receipts, total, rejected, excluded = _receipts(snapshot, cutoff, issues,
                                                       followup='evidence_scope_followup' in snapshot)
        if 'evidence_scope_followup' in snapshot:
            from bellomberg.core.evidence_followup_policy import POLICY, project_receipts
            followup = snapshot['evidence_scope_followup']
            if not isinstance(followup, dict) or followup.get('policy') != POLICY:
                raise ValueError('EVIDENCE_FOLLOWUP_CONTRACT_UNAVAILABLE')
            # Recompute only from hash-verified receipts; a sidecar cannot certify itself.
            valid_ids = {r['id'] for r in receipts}
            verified_rows = [r['receipt'] for r in snapshot['receipts'] if r.get('id') in valid_ids]
            report['evidence_scope_followup'] = project_receipts(verified_rows, run_id=snapshot['run_id'])
        for _ in snapshot.get('issues') or []:
            _issue(issues, 'COLLECTOR_REPORTED_GAP')
        findings = report['findings']
        for match in re.finditer(r'(?:[^\n;.!?]|[.!?](?!\s|$))+', source_memo):
            clause, offset = match.group(), match.start()
            calc = re.search(r'\b(?:calc|calcolo|formula)\s*:\s*(.*)', clause, re.I)
            if calc:
                expression = _TAG.sub('', calc.group(1)).strip()
                findings.append(_finding('calculation', offset + calc.start(), match.end(), source_memo,
                                         {'calculation': _arithmetic(expression, report['language'])}))
                continue
            if _EVENTS.search(clause):
                event = _finding('event', offset, match.end(), source_memo)
                _event(event, clause, receipts)
                findings.append(event)
            # Blank tags: digits in tool names/tickers are identifiers, not quantities.
            scan = re.sub(r'\[(?:src|ticker):[^\]]*\]', lambda m: ' ' * len(m.group()), clause, flags=re.I)
            for token, start, end in _quantity_spans_v4(scan):
                finding = _finding('number', offset + start, offset + end, source_memo)
                _presence(finding, token, parts, completeness, report['language'], clause)
                _attribute(finding, token, clause, receipts, report['language'])
                if 'evidence_scope_followup' in report:
                    _followup_attribute(finding, token, clause, receipts,
                                        report['evidence_scope_followup'], report['language'])
                findings.append(finding)
        for index, finding in enumerate(findings):
            finding['id'] = 'f' + str(index)
        counts = Counter({'findings': len(findings), 'context_parts': len(parts), 'receipts_total': total,
                          'receipts_excluded': rejected, 'scopes_excluded': excluded, 'checks_failed': 0, 'semantic_scope.NOT_ASSESSED': 0})
        for finding in findings:
            counts['kind.' + finding['kind']] += 1
            for dimension, state in finding['dimensions'].items():
                counts[dimension + '.' + state] += 1
        report['counters'] = dict(counts)
        report['status'] = 'COMPLETE' if completeness == 'COMPLETE' and not issues else 'PARTIAL'
    except Exception as exc:
        # No exception message, source text, or provider payload can enter the rendered diagnostic.
        _issue(issues, 'AUDIT_CHECK_UNAVAILABLE')
        report['error_type'] = type(exc).__name__
        report['counters'] = {'findings': len(report['findings']), 'checks_failed': 1}
    return report


def render_memo_facts(report):
    """Brief measured summary: no private raw text, identifiers, URLs or values."""
    en = report.get('language') == 'en'
    counters = report.get('counters') or {}
    def count(key):
        value = counters.get(key)
        return str(value) if type(value) is int and value >= 0 else ('n/a' if en else 'n.d.')
    status = report.get('status')
    status = status if status in ('COMPLETE', 'PARTIAL', 'CHECK_UNAVAILABLE') else 'CHECK_UNAVAILABLE'
    context = report.get('context_completeness')
    context = context if context in ('COMPLETE', 'PARTIAL', 'UNAVAILABLE') else 'UNAVAILABLE'
    lines = ['## MEMO FACTS - ' + status,
             ('Context: ' if en else 'Contesto: ') + context + '.',
             ('Mechanical traceability only; does not certify factual truth or economic merit.' if en else
              'Solo tracciabilita meccanica; non certifica verita fattuale o merito economico.'),
             ('Recognized fragments: ' if en else 'Frammenti riconosciuti: ') + count('findings') + '. ' +
             ('Context parts: ' if en else 'Parti di contesto: ') + count('context_parts') + '. ' +
             ('Excluded receipts: ' if en else 'Ricevute escluse: ') + count('receipts_excluded') + '.',
             ('Metric/period not assessed: ' if en else 'Metrica/periodo non valutati: ') + count('semantic_scope.NOT_ASSESSED') + '.',
             ('Unrecognized prose/ambiguous scope is not verified. Missing context prevents absence claims.' if en else
              'Prosa non riconosciuta e scope ambigui non sono verificati. Contesto mancante impedisce giudizi di assenza.')]
    flagged = []
    for item in report.get('findings') or []:
        dimensions = item.get('dimensions') or {}
        states = [d + '=' + s for d, s in dimensions.items() if d in _DEFAULT and s in
                  {'NOT_FOUND', 'NO_RECEIPT', 'AMBIGUOUS', 'MISMATCH_EXPLICIT', 'MISMATCH_ARITHMETIC', 'INCOMPLETE', 'ESTIMATED'}]
        line = item.get('line')
        if states and type(line) is int and line >= 1:
            flagged.append(('- Line ' if en else '- Riga ') + str(line) + ': ' + '; '.join(states))
    lines.extend(flagged[:8])
    if 'evidence_scope_followup' in report:
        evidence = report['evidence_scope_followup']
        gaps = []
        for fact in evidence.get('facts', []):
            missing = [key for key in ('fiscal_year_status', 'duration_status', 'unit_status',
                                      'currency_status', 'definition_status') if fact.get(key) == 'UNVERIFIED']
            if fact.get('identity_basis') == 'UNVERIFIED':
                missing.append('identity_basis')
            if missing:
                gaps.append(str(fact.get('ticker') or 'n.d.') + ' ' + fact['metric'] + ': '
                            + ', '.join(missing) + ' = UNVERIFIED')
        lines.append(('Evidence metadata gaps (not proven falsehood): ' if en else
                      'Lacune metadati delle ricevute (non falsita dimostrate): ') + str(len(gaps)) + '.')
        lines.extend('- ' + gap for gap in gaps[:8])
        if evidence.get('issues'):
            lines.append(('Projection diagnostics: ' if en else 'Diagnostica proiezione: ')
                         + ', '.join(sorted({item['code'] for item in evidence['issues']})) + '.')
        lines.append(('Other prose and issuer names: NOT_ASSESSED; desk claims are not primary sources.' if en else
                      'Altra prosa e nomi emittenti: NOT_ASSESSED; claim dei desk non sono fonti primarie.'))
    lines.append(('Diagnostics displayed: ' if en else 'Diagnosi mostrate: ') + str(min(8, len(flagged))) + '/' + str(len(flagged)) + '.')
    return '\n'.join(lines)
