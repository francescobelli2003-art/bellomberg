"""Dated NAV snapshots use public evidence and explicit targets, never a private vehicle registry."""
from copy import deepcopy
import json,os,subprocess,sys
from pathlib import Path
import pytest
from bellomberg.valuation import dcf_engine,sector_analysis
from test_sector_operating_drivers import operating_records,DAY

DATE='2025-12-31'


def nav_records(profile='cef'):
    rows=[r for r in operating_records() if r['driver'] in ('perimeter','calendar','quotation','shares')]
    for row in rows:
        row['period']=DATE
        if row['driver']=='calendar':row['value'].update(periods=[],discount_convention='snapshot')
    def add(driver,field,value,unit='contract',basis='nav',scenario='model'):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),unit=unit,accounting_basis=basis,
            scenario=scenario,period=DATE,entity='SYNTH-GROUP',source_id='https://example.org/synthetic/nav-report',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Synthetic sourced snapshot/explicit target'))
    components={'gross_assets':120.,'cash':5.,'debt':20.,'preferred':0.,'other_liabilities':1.,
        'accrued_fees':2.,'distributions_payable':2.,'tax':0.,'equity_adjustments':0.}
    if profile=='dat':
        # Treasury120 plus other assets0, net105 after full warrant exercise5.
        add('holdings','treasury_holdings',{'ASSET-A':{'quantity_millions':10.,'custody':'SYNTH-CUSTODY','ownership_fraction':1.}})
        add('asset_prices','asset_prices',{'ASSET-A':{'price':12.,'currency':'EUR','fx_to_financial':1.,'price_as_of':DATE}})
        components.pop('gross_assets');components['operating_assets']=0.
        add('components','treasury_liabilities',components)
        add('capitalization','dilution_terms',{'basis':'if_converted','basic_shares':9.,
            'conversions':[],'warrants':[{'id':'WARRANT-A','shares':1.,'strike':5.,
                'exercise':'voluntary_in_the_money','exercisable_from':'2025-01-01','expires_on':'2027-12-31'}],
            'debt_basis':'par_before_conversion','cash_basis':'before_exercise'})
    else:
        add('publication','nav_source',{'publisher':'SYNTH-ISSUER','publication_date':DAY,'valuation_date':DATE,
            'basis':'common_equity_net','share_class':'ordinary'})
        add('components','vehicle_liabilities',components)
        add('reported_nav_per_share','nav_per_share',10.,'EUR per share','nav')
        add('reported_nav_precision','nav_per_share',2,'decimals','nav')
        for row in rows:
            if row['driver'] in ('publication','reported_nav_per_share','reported_nav_precision'):row['kind']='historical'
        add('policy','distribution_policy',{'liability_basis':'all_claims_in_components',
            'fees_basis':'accrued_fees_in_components','distributions_basis':'payables_deducted',
            'share_basis':'basic_no_convertibles_or_other_dilution'})
    for sc in ('bear','base','bull'):
        add('nav_target','valuation_target',1.,'ratio','valuation',sc)
        add('target_basis','valuation_target','equity_nav','text','valuation',sc)
    return rows


def nav_bundle(rows=None,profile='cef'):
    return sector_analysis.prepare_sector_analysis('SYNTH-NAV',as_of=DAY,providers={
        'profile':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'data':{'evidence':[
            {'field':f,'value':v,'source_id':'synthetic','as_of':DAY} for f,v in [('instrument','equity'),('business_model',profile)]]}},
        'method_inputs':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'records':deepcopy(nav_records(profile) if rows is None else rows)}},
        user_context={'analysis_context':{'scenario_rationale':{s:'Explicit synthetic NAV target' for s in ('bear','base','bull')}}})


@pytest.mark.parametrize('profile',['cef','investment_holding','dat'])
def test_nav_snapshot_reaches_real_workbook_without_forecast_or_private_registry(tmp_path,profile,monkeypatch):
    monkeypatch.setattr(dcf_engine,'_generate_valuation_legacy',lambda *a,**k:pytest.fail('Legacy/private defaults called'))
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(profile=profile),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==(10.5 if profile=='dat' else 10.)
    assert result['acquisition_snapshot']['case']['records'][1]['value']['periods']==[]
    assert result['path'].endswith('.xlsx')


@pytest.mark.parametrize('profile',['cef','investment_holding','dat'])
def test_nav_explicit_target_sensitivity_is_not_a_geographic_premium(tmp_path,profile):
    rows=nav_records(profile)
    next(r for r in rows if r['driver']=='nav_target' and r['scenario']=='base')['value']=.9
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(rows,profile),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==(9.45 if profile=='dat' else 9.)


@pytest.mark.parametrize('profile',['cef','dat'])
@pytest.mark.parametrize('fault',['target_missing','source','shares','claims','date','extra','historical_target'])
def test_nav_missing_or_incoherent_data_never_becomes_a_default_target(tmp_path,profile,fault):
    rows=nav_records(profile);get=lambda d:next(r for r in rows if r['driver']==d)
    if fault=='target_missing':rows.remove(get('nav_target'))
    if fault=='source':get('components')['valid_until']='2020-01-01'
    if fault=='shares':get('shares')['value']=11.
    if fault=='claims':get('components')['value']['debt']=30.
    if fault=='date':
        if profile=='cef':get('publication')['value']['valuation_date']='2025-12-30'
        else:get('asset_prices')['value']['ASSET-A']['price_as_of']='2025-12-30'
    if fault=='extra':get('components')['value']['inferred_cash']=1.
    if fault=='historical_target':get('nav_target')['kind']='historical'
    # A sourced change in debt changes a digital NAV; no external reported total to contradict it.
    if profile=='dat' and fault=='claims':get('capitalization')['value']['debt_basis']='after_conversion'
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(rows,profile),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None and result['acquisition_tasks']


def test_converted_debt_and_warrant_exercise_are_counted_once(tmp_path):
    rows=nav_records('dat');get=lambda d:next(r for r in rows if r['driver']==d)
    get('capitalization')['value']['conversions']=[{'id':'CONVERTIBLE-A','face_value':10.,'shares':2.,
        'exercise':'voluntary_in_the_money','exercisable_from':'2025-01-01','expires_on':'2027-12-31'}]
    get('shares')['value']=12.
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(rows,'dat'),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    # Net100 + exercise cash5 + debt converted10;12 shares =>9.5833.
    assert result['fair_value_base']==9.58
    assert result['calculation_details']['scenarios']['base']['common_equity_nav']==115.


def test_nav_report_precision_is_not_applied_before_fx_and_quote_scaling(tmp_path):
    rows=nav_records();get=lambda d:next(r for r in rows if r['driver']==d)
    get('components')['value']['gross_assets']=120.06
    get('reported_nav_per_share')['value']=10.01
    get('quotation')['value'].update(quote_currency='GBP',quote_unit='GBX',
        quote_units_per_currency=100.,financial_to_quote_rate=.8,price=800.)
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==800.48


def test_gross_asset_ev_target_is_separate_from_equity_nav_target(tmp_path):
    rows=nav_records('dat')
    for row in rows:
        if row['driver']=='target_basis':row['value']='gross_assets_ev'
        if row['driver']=='nav_target':row['value']=.9
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(rows,'dat'),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    # .9*120+cash10-debt20-other1-fees2-distributions2 =93; /10.
    assert result['fair_value_base']==9.3


@pytest.mark.parametrize('fault',['out_of_money','convertible','expired','future_exercise','debt_basis'])
def test_nav_cannot_create_unavailable_cash_or_debt_relief(tmp_path,fault):
    rows=nav_records('dat');cap=next(r for r in rows if r['driver']=='capitalization')['value']
    if fault=='out_of_money':cap['warrants'][0]['strike']=15.
    if fault=='convertible':cap['conversions']=[{'id':'CONVERTIBLE-A','face_value':20.,'shares':1.,
        'exercise':'voluntary_in_the_money','exercisable_from':'2025-01-01','expires_on':'2027-12-31'}]
    if fault=='expired':cap['warrants'][0]['expires_on']='2025-01-01'
    if fault=='future_exercise':cap['warrants'][0]['exercisable_from']='2026-01-01'
    if fault=='debt_basis':cap['debt_basis']='carrying_value_before_conversion'
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(rows,'dat'),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert any(t['field']=='dilution_terms' for t in result['acquisition_tasks'])


@pytest.mark.parametrize('driver',['publication','reported_nav_per_share','reported_nav_precision'])
def test_official_nav_cannot_be_an_analyst_estimate(tmp_path,driver):
    rows=nav_records();next(r for r in rows if r['driver']==driver)['kind']='analyst_estimate'
    result=dcf_engine.generate_valuation('SYNTH-NAV',prepared_bundle=nav_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert any(driver in t.get('reason','') for t in result['acquisition_tasks'])


@pytest.mark.parametrize('profile',['cef','dat'])
def test_fresh_process_nav_generation_measures_no_personal_registry_reads(tmp_path,profile):
    source=tmp_path/'bundle.json';source.write_text(json.dumps(nav_bundle(profile=profile)),encoding='utf8')
    program='''
import json,sys
from pathlib import Path
from bellomberg.storage import classificazione
calls=[]
def forbidden(*a,**k):
    calls.append(True)
    raise AssertionError('Unexpected private registry read')
classificazione.carica_veicoli=forbidden
from bellomberg.valuation.dcf_engine import generate_valuation
bundle=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
result=generate_valuation('SYNTH-NAV',prepared_bundle=bundle,output_dir=sys.argv[2])
assert result['valuation_usability']['usable'],result.get('error')
print(json.dumps({'registry_reads':len(calls),'fair_value':result['fair_value_base']}))
'''
    proc=subprocess.run([sys.executable,'-c',program,str(source),str(tmp_path/'reports')],
        cwd=Path.cwd(),env={**os.environ,'BELLOMBERG_DATA_DIR':str(tmp_path/'data'),
            'PYTHONPATH':str(Path.cwd()/'src')},capture_output=True,text=True)
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert json.loads(proc.stdout.strip())['registry_reads']==0
