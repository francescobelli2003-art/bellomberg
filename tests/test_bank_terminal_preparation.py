"""Synthetic transport and stage instructions for explicit capital projection."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator


def contract(version=None):
    # Capture the actual bank contract through the common preparation service.
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_sector_analysis import DAY, providers_for
    from test_input_preparation_bank import _documents
    saved={}
    def capture(dossier,value):saved.update(value)
    prepare_method_inputs(prepare_sector_analysis('SYNTH-BANK',as_of=DAY,providers=providers_for('bank')),
                          documents=_documents(),propose=capture)
    saved['preparation_stage']={'scope':'base','drivers':['terminal_ledger']}
    if version is not None:saved['preparation_stage']['terminal_projection_version']=version
    return saved


def test_new_wire_allows_explicit_projection_but_preserves_legacy_wire():
    from bellomberg.valuation.preparation_ai import response_format
    from test_bank_terminal_retention import retention_records
    record=next(r for r in retention_records() if r['driver']=='terminal_ledger')
    old=response_format(contract());new=response_format(contract(2))
    value=lambda wire:wire['json_schema']['schema']['properties']['drivers']['properties']['terminal_ledger']['anyOf'][0]['properties']['value']
    assert 'statutory_projection' not in value(old)['properties']
    assert list(Draft202012Validator(value(old)).iter_errors(record['value']))
    assert not list(Draft202012Validator(value(new)).iter_errors(record['value']))
    bad=deepcopy(record['value']);bad['statutory_projection']='guess'
    assert list(Draft202012Validator(value(new)).iter_errors(bad))
    legacy=deepcopy(record['value']);legacy.pop('statutory_projection')
    assert not list(Draft202012Validator(value(new)).iter_errors(legacy))


@pytest.mark.parametrize('version',[True,0,3,'2',None])
def test_unknown_explicit_transport_version_rejected(version):
    from bellomberg.valuation.preparation_ai import response_format
    value=contract();value['preparation_stage']['terminal_projection_version']=version
    with pytest.raises(ValueError,match='terminal projection version'):response_format(value)


def test_future_bank_stages_explain_both_projection_policies_without_choosing_numbers():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_sector_analysis import DAY, providers_for
    from test_input_preparation_bank import _documents,_propose
    seen=[]
    def propose(dossier,value):
        stage=value['preparation_stage'];scope=stage['scope']
        plan=_propose(dossier,value)
        if 'terminal_ledger' in stage['drivers']:
            rules=stage['bank_requested_contracts']['terminal_ledger']
            assert stage['terminal_projection_version']==2
            assert 'retained_flows_at_g' in rules['statutory_projection']
            assert 'd*(1+g) >= g*R1' in rules['statutory_projection']
            assert 'undemonstrated tax benefit' in rules['closing_balance_checks']
            assert 'optional' in rules['shape']
            seen.append(scope)
        rows=plan['model'] if scope=='model' else plan['scenarios'][scope]
        return {'drivers':{name:rows[name] for name in stage['drivers']},'rationale':'Synthetic sourced bank'}
    bundle=prepare_sector_analysis('SYNTH-BANK',as_of=DAY,providers=providers_for('bank'))
    result=prepare_method_inputs(bundle,documents=_documents(),propose=StagedProposer(propose,drivers_per_stage=12))
    assert result['status']=='prepared',result['issues']
    assert set(seen)=={'bear','base','bull'}
