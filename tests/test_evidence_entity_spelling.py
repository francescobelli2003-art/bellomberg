"""Source display capitalization must not change legal reporting scope."""
from copy import deepcopy
from datetime import date

import pytest

from bellomberg.valuation.input_preparation import _bank_schema, _catalog, _fact_proof
from test_bank_consolidation_evidence import _case, _proof, ISSUER
from test_parent_inline_evidence import normal, observation, source


def test_verified_parent_source_accepts_only_ascii_capitalization_difference():
    original = source(); doc = normal(original)['documents'][0]
    catalog, issues, _ = _catalog([original, doc], date(2026, 9, 10))
    assert not issues
    cash = {**observation(doc, 2), 'value': 10}
    evidence = list(catalog.values())
    assert _fact_proof('capital.parent_opening_cash', cash, evidence, 'USD million',
        '2025-12-31', expected_entity='Synth-Bank-Parent') is None
    for other in ('Synth Bank Parent', 'Synth-Bank-Parent Ltd', 'Synth-Bank-Parent ',
                  'Synth-Bank-Parént', 'SYNTH-BAN\u212a-PARENT'):
        assert _fact_proof('capital.parent_opening_cash', cash, evidence, 'USD million',
            '2025-12-31', expected_entity=other)
    assert _fact_proof('opening_common_equity', cash, evidence, 'USD million',
        '2025-12-31', expected_entity='Synth-Bank-Parent')


def test_consolidation_preserves_ids_and_evidence_with_different_display_case():
    docs, item, context = _case()
    original = deepcopy(docs)
    model = context['model']
    model['legal_structure']['value']['parent_entity'] = model['legal_structure']['value']['parent_entity'].lower()
    previous = model['legal_structure']['value']['subsidiaries'][0]['id']
    changed = previous.lower()
    model['legal_structure']['value']['subsidiaries'][0]['id'] = changed
    item['calculation']['terms']['subsidiaries'][changed] = item['calculation']['terms']['subsidiaries'].pop(previous)
    assert _proof(item, docs, context) is None
    assert _fact_proof('opening_consolidation_adjustments', item, docs, 'USD million',
        '2025-12-31', expected_entity=ISSUER.lower(), bank_context=context) is None
    assert docs == original


@pytest.mark.parametrize('role', ['duplicate_sub', 'parent_as_sub'])
def test_capitalization_cannot_create_another_legal_entity_or_reporting_scope(role):
    docs, item, context = _case()
    legal = context['model']['legal_structure']['value']
    first = legal['subsidiaries'][0]['id']
    alias = first.lower() if role == 'duplicate_sub' else legal['parent_entity'].lower()
    legal['subsidiaries'].append({'id':alias,'regime':'synthetic'})
    item['calculation']['terms']['subsidiaries'][alias] = deepcopy(item['calculation']['terms']['subsidiaries'][first])
    item['value'] -= 90.
    assert _bank_schema(context, {'entity':'Synthetic consolidated group'})[2]
    assert _proof(item, docs, context)
