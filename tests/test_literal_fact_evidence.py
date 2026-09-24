"""Literal source formats preserve one measured number and its exact dated span."""
import pytest

from bellomberg.valuation.input_preparation import _fact_proof


def proof(number='1,234,567', date='June 30, 2026', *, value=1234567):
    quote = number + '\nshares'
    span = f'As of {date}, there were {quote} issued.'
    return {'value': value / 1e6, 'quoted_value': value, 'quoted_unit': 'shares',
            'evidence_quote': quote, 'period_quote': span}, [{'id': 'synthetic-primary', 'text': span}]


@pytest.mark.parametrize('day', ['June 30, 2026', 'June 30,\n2026', '2026-06-30'])
def test_literal_grouped_shares_and_dated_source_are_not_rewritten(day):
    item, docs = proof(date=day)
    before = repr((item, docs))
    assert _fact_proof('shares', item, docs, 'million shares', '2026-06-30') is None
    assert repr((item, docs)) == before


@pytest.mark.parametrize('date', ['June 30, 2025', 'June 31, 2026', '06/30/2026',
    '30/06/2026', 'June 30, 2026 and 2025', 'June 30, 2026 and 2026-06-30',
    'June 30, 2026 and July 1, 2026', 'June 30, 2026 and 01/07/26'])
def test_wrong_ambiguous_or_unsupported_source_dates_fail(date):
    item, docs = proof(date=date)
    assert _fact_proof('shares', item, docs, 'million shares', '2026-06-30') is not None


@pytest.mark.parametrize('number,value', [('1,234,567',1234568), ('1,23,456',123456),
    ('1.234.567',1234567), ('1.234,56',1234.56), ('1,234.56',1234.56),
    ('1e6',1000000), ('(1,234,567)',1234567), ('1,234,567 and 1,111,111',1234567)])
def test_number_format_scale_and_ambiguity_are_checked(number, value):
    item, docs = proof(number=number, date='2026-06-30', value=value)
    problem = _fact_proof('shares', item, docs, 'million shares', '2026-06-30')
    assert (problem is None) == (number == '1,234.56')


@pytest.mark.parametrize('number', ['\u22121,234', '- 1,234', '+ 1,234', '--1,234',
                                   '+-1,234', '(1,234 shares)', '- +1,234', '\u00b11,234', 'minus 1,234'])
def test_signs_or_parentheses_cannot_be_silently_discarded(number):
    item, docs = proof(number=number, date='2026-06-30', value=1234)
    assert _fact_proof('shares', item, docs, 'million shares', '2026-06-30') is not None
    item.update(value=-.001234, quoted_value=-1234)
    assert _fact_proof('shares', item, docs, 'million shares', '2026-06-30') is not None


def test_explicit_ascii_negative_is_preserved():
    item, docs = proof(number='-1,234', date='2026-06-30', value=-1234)
    assert _fact_proof('shares', item, docs, 'million shares', '2026-06-30') is None


@pytest.mark.parametrize('fault', ['duplicate_span','not_literal','missing_unit','wrong_scale'])
def test_literal_source_identity_and_units_remain_required(fault):
    item, docs = proof()
    if fault == 'duplicate_span': docs[0]['text'] += '\n' + docs[0]['text']
    elif fault == 'not_literal': item['period_quote'] = item['period_quote'].replace('issued','outstanding')
    elif fault == 'missing_unit': item['quoted_unit'] = 'USD'
    else: item['value'] *= 1000
    assert _fact_proof('shares', item, docs, 'million shares', '2026-06-30') is not None


def test_supported_literal_format_survives_full_plan_compilation():
    from hashlib import sha256
    from test_input_preparation import _operating_plan, _documents, _bundle
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    plan = _operating_plan(); documents = _documents()
    driver = plan['model']['shares']
    number = f"{driver['value'] * 1000000:,.0f}"
    quote = number + ' shares'
    span = f'As of December 31, 2025, there were {quote} issued.'
    driver.update(evidence_quote=quote, period_quote=span, quoted_value=int(number.replace(',','')),
                  quoted_unit='shares')
    documents[0]['text'] += '\n' + span
    documents[0]['sha256'] = sha256(documents[0]['text'].encode()).hexdigest()
    result = prepare_method_inputs(_bundle(), documents=documents, propose=lambda *_: plan)
    assert result['status'] == 'prepared', result['issues']
