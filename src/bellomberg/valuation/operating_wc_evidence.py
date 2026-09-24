"""Reconcile a bounded issuer working-capital APM with printed balance facts.

The disclosure is context for an analyst's perimeter choice, never an approved
forecast, a replacement for the opening component proofs or a TTM sales series.
"""
from datetime import date
from fractions import Fraction
from hashlib import sha256
import calendar
import json
import re

from .foreign_listing_evidence import _proof
from .input_evidence import same_entity_name
from .preparation_exhibits import _sec_parts
from .statement_table_evidence import _identity, _json, _number, normalize_statement_tables

NORMALIZER = 'operating_wc_disclosure_v1'
PREFIX = 'operating-wc-'
_DEFINITION = (
    r'Operating working capital is the difference between the main operating components '
    r'of current assets and current liabilities\.[^.]{0,400}(?:\.[^.]{0,100})? '
    r'Operating working capital days is calculated in the following manner: '
    r'Operating working capital days = \[\(Inventories \+ Trade receivables '
    r'[-\u2013] Trade payables [-\u2013] Customer advances\) / Annualized quarterly sales \] x 365\. '
    r'Operating working capital days is a non-IFRS alternative performance measure\.')
_NUMBER = r'(?:-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?|\((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)? ?\))'
_ROWS = ('Inventories', 'Trade receivables', 'Customer advances', 'Trade payables',
         'Operating working capital', 'Annualized quarterly sales', 'Operating working capital days')
_TABLE = (r'\(all amounts in (?P<scale>thousands|millions) of '
          r'(?P<currency>U\.S\. dollars|euros|pounds sterling)\) At '
          r'(?P<month>'+ '|'.join(calendar.month_name[1:]) + r') (?P<day>\d{1,2}), '
          r'(?P<year>20\d{2}) (?P<prior>20\d{2}) ' + ' '.join(
              re.escape(label)+f' (?P<r{i}a>'+_NUMBER+f') (?P<r{i}b>'+_NUMBER+')'
              for i, label in enumerate(_ROWS)) + r'(?![\d.,])(?!\s+\(?-?\d)')
_COMPONENTS = (('Inventories', 'Current assets', 1), ('TradeReceivables', 'Current assets', 1),
               ('ContractLiabilities', 'Current liabilities', -1), ('TradePayables', 'Current liabilities', -1))
_LIMITATION = ('Company-defined non-IFRS working-capital measure only. The analyst must justify its '
    'DCF perimeter and the treatment of other balances; no forecast perimeter is approved. '
    'Prior-year components are summed, not reconciled to a different balance-sheet date. '
    'Annualized quarterly sales and working-capital days are not TTM revenue or forecasts.')


def normalize_operating_wc(source, primary, statement, *, on, as_of):
    try:
        opening, cutoff = date.fromisoformat(on), date.fromisoformat(as_of)
        pmeta, cik, _ = _identity(primary)
        meta = source['metadata']; scik, accession, _ = _sec_parts(source['url'])
        published, event = date.fromisoformat(source['published_at']), date.fromisoformat(meta['event_date'])
        if (pmeta['report_date'] != on or not opening <= published <= cutoff
                or not opening <= event <= published or date.fromisoformat(primary['published_at']) > cutoff
                or scik.zfill(10) != cik or meta.get('emittente_id') != 'CIK:'+cik
                or not same_entity_name(meta.get('issuer'), pmeta['issuer'])
                or meta.get('accession', '').replace('-', '') != accession
                or meta.get('form') not in ('6-K', '6-K/A')
                or meta.get('evidence_role') != 'foreign_current_report'
                or source['sha256'] != sha256(source['text'].encode()).hexdigest()
                or not re.fullmatch(r'[0-9a-f]{64}', source['id']) or source['document_sha256'] != source['id']):
            raise ValueError('APM source issuer, form, date or hash differs')
        rebuilt = normalize_statement_tables(primary)
        if rebuilt['status'] != 'ready' or any(statement.get(k) != rebuilt['documents'][0].get(k)
                for k in ('id', 'text', 'sha256', 'url', 'published_at', 'document_sha256', 'metadata')):
            raise ValueError('balance observations differ from recompiled primary statement')
        _, definition = _proof(source, _DEFINITION, 'company_definition')
        table, table_proof = _proof(source, _TABLE, 'reported_apm_table')
        month = list(calendar.month_name).index(table['month'])
        current = date(int(table['year']), month, int(table['day']))
        prior = date(int(table['prior']), month, int(table['day']))
        if current != opening or prior.year != current.year-1:
            raise ValueError('APM dates do not match the opening and prior-year periods')
        unit = {'U.S. dollars': 'USD', 'euros': 'EUR', 'pounds sterling': 'GBP'}[table['currency']]+' '+table['scale'][:-1]
        values = [[_number({'text': table[f'r{i}{col}']}, [], exact=True) for col in 'ab'] for i in range(7)]
        if any(n is None for row in values for n in row):
            raise ValueError('APM has an unparsed numeric observation')
        for col in range(2):
            if (any(values[i][col] < 0 for i in (0, 1)) or any(values[i][col] > 0 for i in (2, 3))
                    or sum(Fraction(values[i][col]) for i in range(4)) != Fraction(values[4][col])):
                raise ValueError('APM component signs or exact sum disagree')
        facts = json.loads(statement['text'])['facts']; reconciliation = []
        for i, (concept, section, sign) in enumerate(_COMPONENTS):
            matches = [(n, f) for n, f in enumerate(facts) if f.get('taxonomy') == 'reported-statement'
                and f.get('concept') == concept and f.get('statement') == 'balance' and f.get('section') == section
                and f.get('scope') == 'consolidated' and f.get('end') == on and 'start' not in f
                and f.get('unit') == unit and same_entity_name(f.get('entity'), pmeta['issuer'])]
            if len(matches) != 1 or Fraction(str(matches[0][1]['value']))*sign != Fraction(values[i][0]):
                raise ValueError('APM component has no unique matching current balance: '+concept)
            n, fact = matches[0]
            reconciliation.append({'label': _ROWS[i], 'coefficient': sign, 'document_id': statement['id'],
                'pointer': '/facts/'+str(n), 'statement_value': fact['value'],
                'reported_signed_value_exact': format(values[i][0], 'f')})
        body = {'issuer': pmeta['issuer'], 'reported_measure': {'label': _ROWS[4],
            'value_exact': format(values[4][0], 'f'), 'unit': unit, 'end': on,
            'comparative': {'value_exact': format(values[4][1], 'f'), 'end': prior.isoformat()}},
            'proofs': [definition, table_proof], 'reconciliation': reconciliation,
            'forecast_perimeter_approved': False, 'limitation': _LIMITATION}
        text = _json(body)
        doc = {'id': PREFIX+source['id']+'-'+primary['id'], 'url': source['url'],
            'published_at': source['published_at'], 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'document_sha256': source['id'], 'origin': NORMALIZER, 'metadata': {'normalizer': NORMALIZER,
                'source_document_id': source['id'], 'primary_document_id': primary['id'],
                'statement_document_id': statement['id'], 'report_date': on, 'as_of': as_of,
                'emittente_id': 'CIK:'+cik, 'entity': pmeta['issuer'], 'scope': 'company_defined_apm'}}
        return {'status': 'ready', 'documents': [doc], 'issues': []}
    except (ValueError, TypeError, KeyError, IndexError, ArithmeticError, AttributeError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def collect_operating_wc(documents, *, primary_id, on, as_of):
    """Supplementary disclosed context; absence does not imply zero NWC."""
    catalog = {doc['id']: doc for doc in documents}
    candidates = [doc for doc in documents if (doc.get('metadata') or {}).get('evidence_role') == 'foreign_current_report'
                  and 'operating working capital' in doc.get('text', '').lower()]
    result = {'status': 'not_found', 'documents': [], 'issues': [], 'limitation': _LIMITATION}
    for source in candidates:
        primary = catalog.get(primary_id); statement = catalog.get('statement-tables-'+primary_id)
        if primary is None or statement is None:
            normalized = {'documents': [], 'issues': [{'source': source['id'],
                'reason': 'APM reconciliation requires original printed balance observations'}]}
        else:
            normalized = normalize_operating_wc(source, primary, statement, on=on, as_of=as_of)
        result['documents'].extend(normalized['documents']); result['issues'].extend(normalized['issues'])
    if candidates:
        result['status'] = ('partial' if result['documents'] else 'incomplete') if result['issues'] else 'ready'
    return result
