"""Share counts in equity captions must not become monetary balance rows."""
from copy import deepcopy
import json
import pytest

from bellomberg.valuation.balance_sheet_evidence import normalize_balance_sheet
from test_balance_sheet_evidence import balance_raw, primary


def caption_raw(tag='CommonStockSharesOutstanding', unit='shares', amount='42'):
    raw = balance_raw().replace(b'<table>', b'<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>'
        b'<xbrli:unit id="perShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>'
        b'<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit><table>', 1)
    caption = ('<td>CommonStockValue; disclosed <ix:nonfraction name="us-gaap:'+tag+'" contextref="now" '
               'unitref="'+unit+'">'+amount+'</ix:nonfraction></td>').encode()
    return raw.replace(b'<td>CommonStockValue</td>', caption)


@pytest.mark.parametrize('tag,unit,amount', [
    ('CommonStockSharesAuthorized', 'shares', '300'),
    ('CommonStockSharesIssued', 'shares', '45'),
    ('CommonStockSharesOutstanding', 'shares', '42'),
    ('TreasuryStockCommonShares', 'shares', '3'),
    ('CommonStockParOrStatedValuePerShare', 'perShare', '0.10'),
])
def test_caption_is_preserved_but_does_not_change_monetary_equity(tag, unit, amount):
    doc = primary(caption_raw(tag, unit, amount)); before = deepcopy(doc)
    result = normalize_balance_sheet(doc)
    assert result['status'] == 'ready', result
    assert doc == before
    groups = json.loads(result['documents'][0]['text'])['groups']
    equity = next(g for g in groups if g['parent']['reported_tag'] == 'us-gaap:StockholdersEquity')
    stock = next(f for f in equity['components'] if f['reported_tag'] == 'us-gaap:CommonStockValue')
    assert stock['value_exact'] == '5000' and stock['unit'] == 'USD'
    assert amount in stock['label']
    assert equity['parent']['value_exact'] == '60000'


@pytest.mark.parametrize('tag,unit', [
    ('CommonStockValue', 'usd'), ('CommonStockSharesOutstanding', 'usd'),
    ('CommonStockParOrStatedValuePerShare', 'shares'),
    ('UnknownBalance', 'shares'),
])
def test_caption_cannot_hide_monetary_or_unknown_facts(tag, unit):
    result = normalize_balance_sheet(primary(caption_raw(tag, unit)))
    assert result['status'] == 'incomplete' and not result['documents']


def test_two_monetary_values_in_amount_cells_remain_ambiguous():
    raw = caption_raw()
    extra = b'<td><ix:nonfraction name="us-gaap:CommonStockValue" contextref="now" unitref="usd" scale="3">5</ix:nonfraction></td>'
    end = b'scale="3">5</ix:nonfraction></td>'
    raw = raw.replace(end, end+extra, 1)
    result = normalize_balance_sheet(primary(raw))
    assert result['status'] == 'incomplete' and not result['documents']


@pytest.mark.parametrize('inline_negative', [False, True])
def test_printed_contra_equity_is_subtracted_exactly_once(inline_negative):
    row = (b'<tr><td>Treasury stock</td><td>(<ix:nonfraction name="us-gaap:TreasuryStockCommonValue" '
           b'contextref="now" unitref="usd" scale="3"'+(b' sign="-"' if inline_negative else b'')+b'>3</ix:nonfraction>)</td></tr>')
    raw = balance_raw().replace(b'<tr><td>StockholdersEquity</td>', row+b'<tr><td>StockholdersEquity</td>')
    raw = raw.replace(b'scale="3">55<', b'scale="3">58<')
    result = normalize_balance_sheet(primary(raw))
    assert result['status'] == 'ready', result
    groups = json.loads(result['documents'][0]['text'])['groups']
    equity = next(g for g in groups if g['parent']['reported_tag'] == 'us-gaap:StockholdersEquity')
    treasury = next(f for f in equity['components'] if f['reported_tag'] == 'us-gaap:TreasuryStockCommonValue')
    assert treasury['value_exact'] == '-3000'
    assert equity['parent']['value_exact'] == '60000'
    if not inline_negative:
        assert treasury['inline_value_exact'] == '3000'
        assert treasury['presentation_sign'] == 'negative_parentheses'
