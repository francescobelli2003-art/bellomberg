"""Explicit analyst NWC perimeter over a recompiled, complete reported balance."""
from decimal import Decimal, localcontext
from hashlib import sha256
from math import isclose, isfinite
import json
import re

from .balance_sheet_evidence import NORMALIZER, PREFIX, normalize_balance_sheet
from .input_evidence import same_entity_name

OPERATION = 'balance_sheet_nwc'
TREATMENTS = ('operating_nwc', 'cash_or_investment', 'financing', 'fixed_or_intangible_asset',
              'income_tax', 'other_operating', 'nonoperating', 'unresolved')
_NON_NWC = frozenset('us-gaap:'+tag for tag in (
    'CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents',
    'RestrictedCashAndCashEquivalentsCurrent', 'RestrictedCashAndCashEquivalentsNoncurrent',
    'AvailableForSaleSecuritiesDebtSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecuritiesNoncurrent',
    'DebtSecuritiesAvailableForSaleRestricted', 'ShortTermBorrowings', 'LongTermDebtCurrent',
    'LongTermDebtNoncurrent', 'DebtCurrent', 'LongTermDebt', 'PropertyPlantAndEquipmentNet',
    'Goodwill', 'IntangibleAssetsNetExcludingGoodwill', 'DeferredIncomeTaxAssetsNet',
    'DeferredIncomeTaxLiabilitiesNet', 'AccruedIncomeTaxesCurrent'))
DISCLOSURE = ('ANALYST CLASSIFICATION: reported amounts and the selected sum are verified; '
    'component treatments are analyst judgments, not a PM approval. NWC exclusions do not '
    'settle forecasts or EV-equity adjustments. Unquantified disclosures remain unquantified, '
    'not zero valuation liabilities. NWC coverage: ')


def _ledgers(documents):
    return [d for d in documents if str(d.get('id', '')).startswith(PREFIX)
            or isinstance(d.get('metadata'), dict) and d['metadata'].get('normalizer') == NORMALIZER]


def balance_nwc_policy(documents):
    ledgers = _ledgers(documents)
    if not ledgers:
        return None
    return {'operation': OPERATION,
        'sources': [{'id': d['id'], 'sha256': d['sha256'], 'entity': d['metadata']['entity'],
                     'report_date': d['metadata']['report_date']} for d in ledgers],
        'calculation_keys': ['operation', 'balance_document_id', 'classifications', 'nonmonetary_review'],
        'classification_keys': ['component_tag', 'treatment', 'judgment', 'rationale', 'evidence_ids', 'evidence_quote'],
        'treatments': list(TREATMENTS), 'judgment': 'analyst_estimate',
        'nonmonetary_review_keys': ['disclosure_index', 'treatment', 'judgment', 'rationale', 'evidence_ids', 'evidence_quote'],
        'nonmonetary_treatments': ['outside_nwc', 'unresolved'],
        'requirements': 'Classify every exact components[].reported_tag once, including explicit zero balances. '
            'Cite one original issuer narrative excerpt per classification; source nature and economic choice are distinct. '
            'Never supply new amounts, coefficients, approval fields or an arbitrary split of a mixed balance. '
            'Use unresolved when its economic treatment is not supported: the compiler keeps the plan incomplete. '
            'Only operating_nwc contributes to the sum: reported asset amounts positive, liabilities negative, '
            'scaled into the driver currency millions. All other treatments are explicit NWC exclusions requiring reasons. '
            'Review every nonmonetary_disclosures entry by its zero-based disclosure_index; outside_nwc is an '
            'analyst scope choice, never proof of a zero liability or completed valuation review. '
            'Driver evidence_ids must contain the selected normalized balance, its original filing and exactly the '
            'sources used by classifications/reviews. No pointer, literal amount or legacy narrow sum may replace this coverage.'}


def balance_nwc_selection_problem(item, catalog, entity, period):
    ledgers = _ledgers(catalog.values())
    if not ledgers:
        return None
    matching = [d for d in ledgers if (d.get('metadata') or {}).get('report_date') == period
                and same_entity_name((d.get('metadata') or {}).get('entity'), entity)]
    if len(matching) != 1:
        return 'NWC: unique reported balance for the opening issuer/date required'
    calculation = item.get('calculation')
    if (not isinstance(calculation, dict) or calculation.get('operation') != OPERATION
            or calculation.get('balance_document_id') != matching[0]['id']
            or matching[0]['id'] not in item.get('evidence_ids', [])):
        return 'NWC: complete reported-balance classifications required; a narrow sum or literal APM is insufficient'
    return None


def _judgment(row, catalog, entity, used):
    if row['judgment'] != 'analyst_estimate':
        raise ValueError('component classification must be an analyst judgment, not a historical fact or approval')
    if not isinstance(row['rationale'], str) or not row['rationale'].strip():
        raise ValueError('component classification rationale missing')
    ids, quote = row['evidence_ids'], row['evidence_quote']
    if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in catalog:
        raise ValueError('one original classification source required')
    doc = catalog[ids[0]]; meta = doc.get('metadata') or {}
    if (meta.get('normalizer') or not same_entity_name(meta.get('issuer', meta.get('entity')), entity)
            or not isinstance(quote, str) or not quote.strip() or quote not in doc['text']):
        raise ValueError('classification quote must occur in an original narrative of the same issuer')
    used.add(ids[0])


def balance_nwc_proof(item, evidence, unit, period, entity, *, scale):
    """Check every decision and sum source amounts; never infer an economic choice."""
    try:
        if any(k in item for k in ('evidence_pointer', 'evidence_quote', 'quoted_value', 'quoted_unit', 'period_quote', 'facts')):
            raise ValueError('balance coverage cannot be mixed with another proof form')
        calc = item['calculation']
        if not isinstance(calc, dict) or set(calc) != {'operation', 'balance_document_id', 'classifications', 'nonmonetary_review'} or calc['operation'] != OPERATION:
            raise ValueError('complete balance NWC calculation required; unknown fields are not consumed')
        catalog = {d['id']: d for d in evidence}
        ids = item.get('evidence_ids')
        if (len(catalog) != len(evidence) or not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
                or len(ids) != len(set(ids)) or set(ids) != set(catalog)):
            raise ValueError('driver sources must match cited evidence exactly')
        selection = balance_nwc_selection_problem(item, catalog, entity, period)
        if selection:
            raise ValueError(selection)
        ledger = catalog[calc['balance_document_id']]
        if ledger not in _ledgers(evidence):
            raise ValueError('reported balance normalization required')
        original_id = ledger['metadata']['source_document_id']
        result = normalize_balance_sheet(catalog[original_id])
        if result['status'] != 'ready' or any(ledger.get(k) != result['documents'][0].get(k)
                for k in ('id', 'text', 'sha256', 'metadata', 'url', 'document_sha256', 'published_at')):
            raise ValueError('balance NWC source differs from recompiled filing')
        body = json.loads(ledger['text']); components = body['components']
        choices = calc['classifications']
        fields = {'component_tag', 'treatment', 'judgment', 'rationale', 'evidence_ids', 'evidence_quote'}
        if not isinstance(choices, list) or len(choices) != len(components) or any(
                not isinstance(row, dict) or set(row) != fields or not isinstance(row['component_tag'], str) for row in choices):
            raise ValueError('exactly one explicit classification required for every reported balance component')
        by_tag = {row['component_tag']: row for row in choices}
        if len(by_tag) != len(choices) or set(by_tag) != {f['reported_tag'] for f in components}:
            raise ValueError('duplicate, missing or invented balance component classification')
        used = {ledger['id'], original_id}
        with localcontext() as ctx:
            ctx.prec = 256
            total = Decimal(0)
            for fact in components:
                row = by_tag[fact['reported_tag']]; treatment = row['treatment']
                if treatment not in TREATMENTS or treatment == 'unresolved':
                    raise ValueError('unresolved or unsupported economic treatment: '+fact['reported_tag'])
                _judgment(row, catalog, entity, used)
                side = fact['accounting_side']
                if ((treatment in ('cash_or_investment', 'fixed_or_intangible_asset') and side != 'asset')
                        or (treatment == 'financing' and side != 'liability')):
                    raise ValueError('economic treatment conflicts with reported accounting side')
                if treatment == 'operating_nwc':
                    concept = ('us-gaap:'+fact['reported_tag'].split(':', 1)[1]
                               if re.fullmatch(r'https?://fasb.org/us-gaap/20\d{2}', fact['namespace'])
                               else fact['reported_tag'])
                    if concept in _NON_NWC:
                        raise ValueError('cash, financing or fixed/tax asset cannot be smuggled into operating NWC')
                    factor = scale(fact['unit'], unit)
                    if factor is None:
                        raise ValueError('balance NWC requires the same currency and declared unit scaling')
                    total += Decimal(fact['value_exact'])*Decimal(str(factor))*(1 if side == 'asset' else -1)
            # Even a selection with no included components must have the correct currency.
            if not components or any(f['end'] != period or scale(f['unit'], unit) is None for f in components):
                raise ValueError('balance NWC currency or opening date differs')
            reviews = calc['nonmonetary_review']; disclosures = body['nonmonetary_disclosures']
            fields = {'disclosure_index', 'treatment', 'judgment', 'rationale', 'evidence_ids', 'evidence_quote'}
            if not isinstance(reviews, list) or len(reviews) != len(disclosures) or any(
                    not isinstance(row, dict) or set(row) != fields or type(row['disclosure_index']) is not int for row in reviews):
                raise ValueError('every nonmonetary disclosure needs an explicit review, never an invented zero')
            if {r['disclosure_index'] for r in reviews} != set(range(len(disclosures))):
                raise ValueError('duplicate or missing nonmonetary disclosure review')
            for row in reviews:
                if row['treatment'] != 'outside_nwc':
                    raise ValueError('unresolved nonmonetary disclosure scope')
                _judgment(row, catalog, entity, used)
            value = item.get('value')
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
                    or not isclose(value, float(total), rel_tol=1e-10, abs_tol=1e-8)):
                raise ValueError('NWC amount does not equal the signed sum of explicitly selected balance components')
        if used != set(ids):
            raise ValueError('unused NWC classification evidence')
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError) as exc:
        return str(exc)
    return None


def balance_nwc_disclosure(item):
    calculation = item['calculation']
    summary = {'balance_document_id': calculation['balance_document_id'],
        'classification_sha256': balance_nwc_provenance(item)['sha256'],
        'classifications': {row['component_tag']: row['treatment'] for row in calculation['classifications']},
        'nonmonetary_review': {str(row['disclosure_index']): row['treatment'] for row in calculation['nonmonetary_review']}}
    return DISCLOSURE + json.dumps(summary, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n'


def balance_nwc_provenance(item):
    from copy import deepcopy
    calculation = item['calculation']
    encoded = json.dumps(calculation, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return {'origin': 'analyst_classification_not_approved', 'sha256': sha256(encoded.encode()).hexdigest(),
            'calculation': deepcopy(calculation)}
