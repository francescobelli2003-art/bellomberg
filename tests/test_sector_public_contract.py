"""All documented families remain economic cases, independent of private context."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine,dcf_quality,sector_analysis
from test_documented_consumers import CASES
from test_managed_care_integration import make_bundle

FAMILIES=[case[0] for case in CASES]+[make_bundle]


def rebuild(original,*,ticker=None,context=None,sources=None):
    providers={key:(lambda *a,v=value,**k:deepcopy(v)) for key,value in
        (original['case']['sources'] if sources is None else sources).items()}
    return sector_analysis.prepare_sector_analysis(ticker or original['case']['ticker'],
        as_of=original['case']['as_of'],providers=providers,
        user_context={'analysis_context':original['analysis_context'],**(context or {})})


@pytest.mark.parametrize('factory',FAMILIES)
def test_every_family_renames_without_private_portfolio_or_mandate_prior(tmp_path,monkeypatch,factory):
    from bellomberg.storage import classificazione
    def forbidden(*a,**k):raise AssertionError('Private registry or legacy valuation used')
    monkeypatch.setattr(classificazione,'carica_veicoli',forbidden)
    monkeypatch.setattr(dcf_engine,'_generate_valuation_legacy',forbidden)
    original=factory()
    neutral=rebuild(original,context={'portfolio':{'positions':[]},'mandate':{'side':'LONG'}})
    opposite=rebuild(original,context={'portfolio':{'positions':[{'ticker':'UNRELATED','weight':1}]},'mandate':{'side':'SHORT'}})
    assert neutral==opposite
    first=dcf_engine.generate_valuation(neutral['case']['ticker'],prepared_bundle=neutral,output_dir=str(tmp_path))
    renamed=rebuild(original,ticker='EXT-RENAMED')
    second=dcf_engine.generate_valuation('EXT-RENAMED',prepared_bundle=renamed,output_dir=str(tmp_path))
    assert first['valuation_usability']['usable'],first.get('error')
    assert second['valuation_usability']['usable'],second.get('error')
    for key in ('fair_value_base','fair_value_bear','fair_value_bull'):
        assert second.get(key)==first.get(key)
    assert first['method']==second['method']
    assert first['generation_id']!=second['generation_id'] and first['path']!=second['path']
    # Source data remain attached; valuation outputs cannot survive these boundaries.
    for fault in ('stale','legacy','method_changed','unused','nested_block'):
        value=deepcopy(first);expected=None;cutoff=original['case']['as_of']
        if fault=='stale':cutoff='2030-01-01'
        if fault=='legacy':value.pop('snapshot_id');value.pop('input_consumption')
        if fault=='method_changed':expected={**value['valuation_decision'],'method_id':'different_economics'}
        if fault=='unused':value['input_consumption']['unconsumed_fields']=['unread_source']
        if fault=='nested_block':value['sensitivity']={'fair_value':123.,'status':'BLOCK'}
        blocked=dcf_quality.normalize_valuation_payload(value,expected_decision=expected,as_of=cutoff)
        assert not blocked['valuation_usability']['usable'],fault
        assert all(blocked.get(key) is None for key in ('fair_value_base','fair_value_bear','fair_value_bull','fair_value_weighted'))
        if fault=='nested_block':assert blocked['sensitivity']['fair_value'] is None
    changed_sources=deepcopy(original['case']['sources'])
    data=changed_sources['profile']['data']
    data['evidence']=[{'field':f,'value':v,'source_id':'synthetic-new-business','as_of':cutoff}
        for f,v in [('instrument','equity'),('business_model','software' if original['decision']['method_id']=='bank_residual_income' else 'bank')]]
    changed=rebuild(original,sources=changed_sources)
    assert changed['decision']['method_id']!=original['decision']['method_id']
    invalid=dcf_engine.generate_valuation(changed['case']['ticker'],prepared_bundle=changed,output_dir=str(tmp_path))
    assert not invalid['valuation_usability']['usable'] and invalid.get('fair_value_base') is None


def test_blocked_sotp_hides_derived_child_equity_values(tmp_path):
    from test_sotp_documented import generate,DAY
    value=generate(tmp_path);assert value['valuation_usability']['usable']
    value['child_valuations']['BANK']['sanity']['severity']='BLOCK'
    blocked=dcf_quality.normalize_valuation_payload(value,as_of=DAY)
    for part in blocked['calculation_details']['scenarios']['base']['segments']:
        assert part['raw_equity_value'] is None and part['owned_equity_value'] is None


@pytest.mark.parametrize('factory',FAMILIES)
def test_registry_readiness_describes_documented_adapter_without_certifying_case(factory):
    note=factory()['decision']['support_note']
    assert 'documented records' in note and 'gate' in note


@pytest.mark.parametrize('family',['nav','bank','property','development'])
def test_late_scenario_gap_hides_calculated_values_but_retains_source_and_cash_book(tmp_path,family):
    from test_sector_nav_drivers import nav_bundle,nav_records
    from test_sector_bank_capital import bank_bundle,bank_records
    from test_real_estate_valuation import property_bundle,property_records
    from test_development_valuation import development_bundle,development_records
    factory,record_factory={'nav':(nav_bundle,nav_records),'bank':(bank_bundle,bank_records),
        'property':(property_bundle,property_records),'development':(development_bundle,development_records)}[family]
    records=record_factory()
    def bull(driver):return next(r for r in records if r['scenario']=='bull' and r['driver']==driver)
    if family=='nav':bull('nav_target')['value']=-1.
    if family=='bank':bull('capital.terminal_equity')['value']=999.
    if family=='property':bull('ffo_bridge')['value']['affo']+=1.
    if family=='development':bull('probabilities')['value']['STAGE-A']=2.
    bundle=factory(records)
    result=dcf_engine.generate_valuation(bundle['case']['ticker'],prepared_bundle=bundle,output_dir=str(tmp_path))
    result=dcf_quality.normalize_valuation_payload(result,as_of=bundle['case']['as_of'])
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    base=result['calculation_details']['scenarios']['base']
    if family=='nav':
        assert base['common_equity_nav'] is None and base['nav_per_share'] is None
        observed=next(r for r in result['acquisition_snapshot']['case']['records'] if r['driver']=='reported_nav_per_share')
        assert observed['value']==10.
    if family=='bank':
        assert all(base[k] is None for k in ('residual_income_value','cash_equity_value','terminal_common_equity_value'))
        assert base['closing_common_equity']==118.
    if family=='property':
        assert base['nav']['common_equity_nav'] is None and base['nav']['nav_per_share'] is None
        assert base['nav']['components']['gross_assets'] is None
        assert base['nav']['components']['equity_adjustments'] is None
        assert all(a['asset_value'] is None for a in base['assets'].values())
    if family=='development':
        assert all(o['value_contribution'] is None for o in base['outcomes'])
        assert base['outcomes'][0]['probability']==.5


@pytest.mark.parametrize('key',sorted(dcf_quality._CALCULATED_VALUATION_KEYS))
def test_derived_calculation_alias_is_gated_and_nonfinite_is_not_usable(key):
    from test_sector_usability import payload_for,DAY
    value=payload_for();source=deepcopy(value['acquisition_snapshot'])
    value['nav_per_share']=123.
    value['calculation_details']={'scenarios':{'base':{key:float('inf'),'closing_common_equity':118.}}}
    blocked=dcf_quality.normalize_valuation_payload(value,as_of=DAY)
    assert not blocked['valuation_usability']['usable']
    assert blocked['calculation_details']['scenarios']['base'][key] is None
    assert blocked['calculation_details']['scenarios']['base']['closing_common_equity']==118.
    assert blocked['acquisition_snapshot']==source and blocked['nav_per_share']==123.
