"""SEC catalog and inline display names may punctuate the same Inc suffix."""
from copy import deepcopy
import pytest

from bellomberg.valuation.balance_detail_evidence import normalize_balance_details
from bellomberg.valuation.input_evidence import same_entity_name
from test_balance_detail_evidence import raw_source, source


def industrial_source(inline_name, catalog_name):
    raw = raw_source().replace(b'Synthetic Industrial Issuer', inline_name.encode())
    doc = source(raw)
    doc['metadata']['issuer'] = catalog_name
    return doc


@pytest.mark.parametrize('inline,catalog', [
    ('Synthetic Industrial Issuer, Inc.', 'SYNTHETIC INDUSTRIAL ISSUER INC'),
    ('Synthetic Industrial Issuer Inc.', 'Synthetic Industrial Issuer, Inc'),
    ('SYNTHETIC INDUSTRIAL ISSUER INC', 'Synthetic Industrial Issuer, Inc.'),
])
def test_sec_balance_accepts_inc_suffix_punctuation_without_rewriting_evidence(inline, catalog):
    doc = industrial_source(inline, catalog); before = deepcopy(doc)
    result = normalize_balance_details(doc)
    assert result['status'] == 'ready', result
    assert doc == before
    assert result['documents'][0]['metadata']['entity'] == catalog
    assert result['documents'][0]['document_sha256'] == doc['id']
    # Other legal entity/perimeter comparisons retain the existing strict rule.
    assert not same_entity_name(inline, catalog)


@pytest.mark.parametrize('other', [
    'Synthetic Industrial Issuer LLC', 'Synthetic Industrial Issuer Holdings Inc',
    'Synthetic Industrial-Issuer Inc', 'Synthetic Industrial Issuer Incorporated',
    'Synthetic Industrial Issuer,Inc.', 'Synthetic Industrial Issuer  Inc',
    'Synthetic Industrial Issuer Inc. ', 'Synthetic Industrial Issuer In\u212a',
    'Synthetic Industrial Issuer', 'Synthetic Industrial Issuer Corp',
])
def test_suffix_spelling_does_not_merge_other_entities_or_normalize_arbitrary_text(other):
    doc = industrial_source('Synthetic Industrial Issuer, Inc.', other)
    result = normalize_balance_details(doc)
    assert result['status'] == 'incomplete' and not result['documents']


def test_accepted_display_punctuation_cannot_hide_a_different_cik():
    raw = raw_source().replace(b'Synthetic Industrial Issuer', b'Synthetic Industrial Issuer, Inc.').replace(b'>123<', b'>456<')
    doc = source(raw); doc['metadata']['issuer'] = 'SYNTHETIC INDUSTRIAL ISSUER INC'
    result = normalize_balance_details(doc)
    assert result['status'] == 'incomplete' and not result['documents']


def test_accepted_display_punctuation_cannot_hide_modified_packet():
    doc = industrial_source('Synthetic Industrial Issuer, Inc.', 'SYNTHETIC INDUSTRIAL ISSUER INC')
    doc['balance_detail_fields']['issuer'] = 'Synthetic Industrial Issuer Inc'
    result = normalize_balance_details(doc)
    assert result['status'] == 'incomplete' and not result['documents']
