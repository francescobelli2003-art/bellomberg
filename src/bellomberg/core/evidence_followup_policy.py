"""Follow-up evidence contract, independent of historical weekly prompt /1.

Pure projections retain receipt identity and missing dimensions. They attest
transport and explicit metadata, never economic truth or a desk's interpretation.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math

KEY = 'evidence_followup_policy'
POLICY = 'weekly-evidence-followup/1'
AS_OF_KEY = 'evidence_followup_as_of'

INSTRUCTIONS = (
    "\nEVIDENCE FOLLOW-UP weekly-evidence-followup/1: per ogni cifra conserva fonte/ricevuta, "
    "ticker, periodo iniziale/finale, durata, FY, unita', valuta, definizione e data acquisizione. "
    "Una fine periodo NON attesta H1, trimestre o TTM; il bucket 0y NON attesta un anno solare "
    "o fiscale. Valuta quote NON e' valuta EPS. debt_to_equity grezzo senza unita' NON sono "
    "volte: lascia valore grezzo e unita' n.d. Non trasferire qualifiche da un altro campo. "
    "Usa il ticker quando il nome dell'emittente non e' attestato nella ricevuta pertinente. "
    "Il nome citato da un altro desk e' un claim del desk, NON una fonte primaria. "
    "Per mNAV cita metodo e perimetro: price/NAV-per-share, market-cap/asset-value e "
    "enterprise-value/asset-value non sono multipli identici; debito, preferred, diluizione "
    "e DTL devono restare distinti. Una chiamata/acquisizione non dimostra lettura della "
    "fonte, verifica economica o completezza. Riutilizza ricevute della stessa run con "
    "timestamp, dichiarando nessuna nuova acquisizione nel round quando applicabile. "
    "Metadati mancanti = UNVERIFIED/n.d., NON falsita'; prosa o identita' non riconosciute "
    "dal controllo deterministico = NOT_ASSESSED. Segnala solo contraddizioni esplicite.\n"
)


def enabled(contract):
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    if KEY not in contract:
        return False
    if contract[KEY] != POLICY:
        raise WeeklyRunBlocked('Policy evidence follow-up non compatibile')
    return True


def board_enabled(board):
    store = getattr(board, 'weekly_store', None)
    return (getattr(board, 'run_scope', None) == 'weekly' and store is not None
            and enabled(store.context.get('contract', {})))


def frozen_as_of(contract):
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    value = contract.get(AS_OF_KEY)
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise WeeklyRunBlocked('Evidence follow-up: cutoff UTC congelato assente o invalido') from None
    return value


def instructions(board):
    return INSTRUCTIONS if board_enabled(board) else ''


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _pointer(payload, path):
    node = payload
    for bit in path.split('/')[1:]:
        bit = bit.replace('~1', '/').replace('~0', '~')
        node = node[int(bit)] if isinstance(node, list) else node[bit]
    return node


def _status(value):
    return 'DECLARED' if value is not None and value != '' else 'UNVERIFIED'


def project_fact(receipt, metric, *, path='', run_id=None, round_number=None):
    """Project one field at a JSON-pointer object path; never search other desks.

    ``source_receipt.path`` identifies the value, not merely the enclosing tool.
    Only metadata on this object or its metric-specific dictionary is admitted.
    ``run_id``/``round_number`` are fallback *transport* identifiers from the caller.
    """
    payload = json.loads(receipt['output'])
    node = _pointer(payload, path)
    # Explicit statement containers: compact Italian output and native SEC cells.
    scope_path = (path.rsplit('/', 1)[0] if path.endswith(('/valori', '/latest_period/values')) else path)
    scope = _pointer(payload, scope_path)
    ticker = (receipt.get('input') or {}).get('ticker')
    if any(item.get('ticker') not in (None, ticker) for item in (payload, scope)):
        raise ValueError('SCOPE_TICKER_DIFFERS')
    meta = {}
    metadata_paths = [scope_path]
    for key in ('cashflow_metadata', 'metric_metadata'):
        candidate = scope.get(key, {}).get(metric) if isinstance(scope.get(key), dict) else None
        if isinstance(candidate, dict):
            meta.update(candidate)
            metadata_paths.append(scope_path + '/' + key + '/' + metric.replace('~', '~0').replace('/', '~1'))
    def field(*keys):
        for source in (meta, scope):
            for key in keys:
                if key in source:
                    value = source[key]
                    if key == 'unita' and isinstance(value, dict):
                        value = value.get(metric)
                    return deepcopy(value)
        return None
    identity = payload.get('issuer_identity') if isinstance(payload, dict) else None
    identity = identity if isinstance(identity, dict) else {}
    attested_identity = (identity.get('status') in ('PROVIDER_SYMBOL_MATCH', 'PRIMARY_SOURCE_VERIFIED')
                         and ticker is not None and identity.get('ticker') == ticker)
    value = node.get(metric)
    valid_value = type(value) in (int, float) and math.isfinite(value)
    out = {'metric': metric, 'value': value if valid_value else None,
           'value_status': 'AVAILABLE' if valid_value else 'UNAVAILABLE',
           'ticker': ticker,
           'issuer_name': identity.get('name') if attested_identity else None,
           'issuer_identity': deepcopy(identity) or None,
           'identity_basis': identity.get('basis') if attested_identity else 'UNVERIFIED',
           'source_receipt': {'sha256': _digest(receipt), 'tool': receipt.get('tool'),
                              'path': path + '/' + metric.replace('~', '~0').replace('/', '~1'),
                              'metadata_paths': metadata_paths},
           'source': receipt.get('source') or field('source'),
           'source_status': field('status', 'stato'), 'source_error': field('error'),
           'run_id': receipt.get('run_id', run_id), 'round': receipt.get('round', round_number),
           'observed_at': receipt.get('observed_at') or receipt.get('timestamp') or field('observed_at', 'acquired_at'),
           'period_start': field('period_start'), 'period_end': field('period_end', 'fiscal_date'),
           'provider_bucket': field('period'), 'fiscal_year_label': field('fiscal_year_label'),
           'duration': field('period_type', 'duration'), 'unit': field('unit', 'unita'),
           'duration_days': field('duration_days'),
           'currency': field('currency', 'valuta'), 'definition': field('definition'),
           'method': field('method'), 'perimeter': field('perimeter')}
    for key, status in (('fiscal_year_label', 'fiscal_year_status'), ('duration', 'duration_status'),
                        ('unit', 'unit_status'), ('currency', 'currency_status'),
                        ('definition', 'definition_status')):
        out[status] = _status(out[key])
    if out['duration'] is None and type(out['duration_days']) in (int, float):
        out['duration_status'] = 'DECLARED'
    out['semantic_scope'] = 'NOT_ASSESSED'
    return out


_FIELDS = {'revenue', 'eps', 'net_income', 'debt_to_equity', 'free_cashflow', 'operating_cashflow',
           'free_cash_flow', 'operating_cash_flow', 'total_debt', 'debt', 'mnav', 'mnav_ev',
           'mnav_equity_basic', 'mnav_dtl_addback', 'EPS riportato'}


def project_receipts(receipts, *, run_id=None):
    """Read actual run receipts only; unknown shapes remain explicitly unassessed."""
    out = {'policy': POLICY, 'facts': [], 'issues': [], 'semantic_scope': 'NOT_ASSESSED'}
    if not isinstance(receipts, list):
        out['issues'].append({'code': 'RECEIPTS_UNAVAILABLE'})
        return out
    for index, receipt in enumerate(receipts):
        try:
            if receipt.get('success') is not True or receipt.get('truncated') is not False:
                raise ValueError('RECEIPT_UNAVAILABLE')
            if run_id is not None and receipt.get('run_id') not in (None, run_id):
                out['issues'].append({'code': 'RECEIPT_RUN_DIFFERS', 'receipt_index': index})
                continue
            payload = json.loads(receipt['output'])
            if not isinstance(payload, dict):
                raise ValueError('RECEIPT_JSON_UNSUPPORTED')
            def walk(node, path='', depth=0):
                if depth > 16:
                    out['issues'].append({'code': 'SCOPE_DEPTH_UNSUPPORTED', 'receipt_index': index})
                    return
                if isinstance(node, list):
                    for i, child in enumerate(node):
                        walk(child, path + '/' + str(i), depth + 1)
                elif isinstance(node, dict):
                    if node.get('ticker') not in (None, (receipt.get('input') or {}).get('ticker')):
                        out['issues'].append({'code': 'SCOPE_TICKER_DIFFERS', 'receipt_index': index, 'path': path})
                        return
                    for key, value in node.items():
                        if key in _FIELDS or key == 'avg' and path.split('/')[1:2] in (['eps_estimates'], ['revenue_estimates']):
                            fact = project_fact(receipt, key, path=path, run_id=run_id)
                            if key == 'avg':
                                fact['metric'] = {'eps_estimates': 'eps', 'revenue_estimates': 'revenue'}[path.split('/')[1]]
                            fact['source_receipt']['index'] = index
                            out['facts'].append(fact)
                        elif key not in ('other_desk_report', 'provider_metadata', 'metric_metadata',
                                         'cashflow_metadata', 'issuer_identity') and isinstance(value, (dict, list)):
                            walk(value, path + '/' + key.replace('~', '~0').replace('/', '~1'), depth + 1)
            walk(payload)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            out['issues'].append({'code': 'RECEIPT_UNAVAILABLE', 'receipt_index': index})
    if not out['facts']:
        out['issues'].append({'code': 'NO_RECOGNIZED_FACTS'})
    return out


def followup_block(board):
    """Native prompt transport of already saved data; no fetch or recalculation."""
    if not board_enabled(board):
        return ''
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    from bellomberg.core.evidence_prompt_policy import quant_semantics
    store = board.weekly_store
    as_of = frozen_as_of(store.context['contract'])
    priming = store.get('priming')
    if not isinstance(priming, dict):
        raise WeeklyRunBlocked('Evidence follow-up: priming persistito assente')
    data = deepcopy(priming)
    synthesis = store.get('synthesis_context')
    if isinstance(synthesis, dict):
        data['risk_data'] = synthesis.get('risk_data')
    # Desk acquisitions already in the durable receipt stream; never tool-health or a new fetch.
    quant_sources = {}
    for receipt in getattr(board, 'tool_receipts', None) or []:
        if not isinstance(receipt, dict):
            continue
        field_name = {'get_sector_exposure': 'sector_exposure', 'get_var_backtest': 'var_backtest'}.get(receipt.get('tool'))
        if (field_name is None or receipt.get('truncated') is not False
                or receipt.get('run_id') not in (None, store.run_id)):
            continue
        try:
            value = json.loads(receipt['output'])
            if isinstance(value, dict) and (receipt.get('success') is True or value.get('error') or value.get('error_code')):
                # An error payload is evidence of this calculation's KO, too.
                data[field_name] = value
                quant_sources[field_name] = {'tool': receipt['tool'], 'sha256': _digest(receipt),
                                              'observed_at': receipt.get('observed_at') or receipt.get('timestamp')}
        except (ValueError, TypeError, KeyError):
            continue  # Unusable receipt remains visible in project_receipts issues below.
    payload = project_receipts(getattr(board, 'tool_receipts', None), run_id=store.run_id)
    payload.update(as_of=as_of, quant=quant_semantics(data), freshness=deepcopy(priming.get('freshness_report')),
                   quant_sources=quant_sources,
                   quant_acquisition={'var_contribution': deepcopy(priming.get('var_contribution_acquisition'))},
                   desk_reports='CLAIMS_ONLY_NOT_PRIMARY_SOURCES')
    return ('\n\n=== EVIDENZA DELLA RUN: METADATI E LACUNE ===\n'
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False))


def no_acquisition_notice(board, round_number):
    rows = getattr(board, 'tool_receipts', None)
    run_id = getattr(getattr(board, 'weekly_store', None), 'run_id', None)
    available = [r for r in rows or [] if isinstance(r, dict) and r.get('success') is True
                 and r.get('truncated') is False and r.get('run_id') in (None, run_id)]
    times = sorted({str(r.get('observed_at') or r.get('timestamp') or 'n.d.') for r in available})
    detail = ('Ricevute della run disponibili per eventuale riuso; acquisizioni: ' + ', '.join(times)
              if available else 'Nessuna ricevuta della run disponibile per il riuso')
    return ('[ROUND ' + str(round_number) + ': nessuna nuova acquisizione in questo round. '
            + detail + '. Disponibilita\' non attesta lettura o verifica economica.]')
