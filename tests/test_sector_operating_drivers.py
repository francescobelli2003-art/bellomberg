"""Documented operating inputs reach the real calculator, without sector priors."""
from copy import deepcopy
from pathlib import Path
import json

import pytest
from openpyxl import load_workbook

from bellomberg.valuation import dcf_engine, sector_analysis


DAY = '2026-09-10'
SPAN = '2026-01-01/2026-12-31|2027-01-01/2027-12-31'


def operating_records():
    """Synthetic flat business with explicit cash-flow and terminal assumptions."""
    rows = []
    def add(driver, field, value, unit, basis, scenario='model', period=SPAN):
        rows.append(dict(driver=driver, field=field, value=deepcopy(value), unit=unit,
            accounting_basis=basis, scenario=scenario, period=period, entity='SYNTH-GROUP',
            source_id='https://example.org/synthetic/dated-case', as_of=DAY,
            valid_until='2027-01-01', kind='analyst_estimate',
            rationale='Synthetic documented assumption, independent of ticker and holdings'))
    add('perimeter', 'valuation_perimeter', {'entity':'SYNTH-GROUP','currency':'EUR','share_class':'ordinary'}, 'contract', 'scope')
    add('calendar', 'valuation_perimeter', {'valuation_date':'2025-12-31',
        'periods':[{'start':'2026-01-01','end':'2026-12-31'},{'start':'2027-01-01','end':'2027-12-31'}],
        'discount_convention':'annual_end'}, 'contract', 'calendar')
    add('quotation', 'quotation_units', {'financial_currency':'EUR','quote_currency':'EUR','quote_unit':'EUR',
        'quote_units_per_currency':1.,'financial_to_quote_rate':1.,'shares_per_quote':1.,
        'share_class':'ordinary','price':10.,'price_as_of':'2025-12-31'}, 'contract','quotation',period='2025-12-31')
    # Information cutoff includes the opening balances/price; no look-ahead.
    for row in rows: row['as_of'] = DAY
    add('historical_revenue', 'historical_financials', 100., 'EUR million','operating',period='2025-12-31')
    add('opening_nwc', 'historical_financials', 0., 'EUR million','operating',period='2025-12-31')
    add('shares', 'diluted_shares', 10., 'million shares','valuation',period='2025-12-31')
    add('capdev_amortization_years', 'reinvestment', 4, 'years','operating')
    for scenario in ('bear','base','bull'):
        add('revenue_build','revenue_drivers', {'basis':'units_price','volume':[10.,10.],
            'unit_price':[10.,10.],'utilization':[1.,1.],'other_revenue':[0.,0.]},'contract','operating',scenario)
        for driver, value in {'revenue_growth':0.,'gross_margin':.4,'rnd_pct':.1,'sga_pct':.1,
                              'capdev_pct':0.,'da_tan_pct':.05,'tax_rate':0.,'capex_pct':.1,'nwc_pct':0.}.items():
            field = 'revenue_drivers' if driver == 'revenue_growth' else 'reinvestment' if driver in ('capdev_pct','da_tan_pct','capex_pct','nwc_pct') else 'margin_drivers'
            add(driver,field,[value,value],'ratio','operating',scenario)
        add('opening_intangible_amortization','reinvestment',[0.,0.],'EUR million','operating',scenario)
        add('terminal_bridge','terminal_assumptions',{'normalized_ebit':15.,'capitalized_research_adjustment':0.,
            'cycle_adjustment':0.,'expiring_product_loss':0.,'replacement_product_income':0.,
            'other_adjustment':0.},'contract','operating',scenario)
        for driver, field, value, unit, basis in [
            ('wacc','discount_rate_inputs',.1,'ratio','valuation'),
            ('terminal_growth','terminal_assumptions',0.,'ratio','valuation'),
            ('terminal_ronic','terminal_assumptions',.1,'ratio','valuation'),
            ('net_debt','enterprise_equity_bridge',0.,'EUR million','valuation'),
            ('equity_adjustments','enterprise_equity_bridge',0.,'EUR million','valuation')]:
            add(driver,field,value,unit,basis,scenario)
        add('accounting_policies','margin_drivers', {
            'tax':'no_loss_tax_credit','sbc':'included_in_operating_costs',
            'leases':'operating_rent_in_costs','research':'expensed_except_explicit_capdev',
            'cycle':'explicit_forecast','patents':'explicit_forecast'}, 'contract','operating',scenario)
    return rows


def bundle_for(records=None, symbol='SYNTH-EXT', profile='software'):
    records = operating_records() if records is None else records
    records=deepcopy(records)
    basis={'software':'subscribers_arpu','services':'units_price','payment_network':'transactions_fee',
           'semiconductor_foundry':'capacity_utilization_price','merchant_generation':'capacity_utilization_price',
           'telecom':'subscribers_arpu','media':'audience_yield','payment_processor':'transactions_fee','fee_asset_manager':'aum_fee','broker_fee':'transactions_fee','hospital':'patients_yield','contracted_generation':'capacity_utilization_price','pharma_mature':'product_volume_price'}.get(profile,'units_price')
    for row in records:
        if row['driver']=='revenue_build': row['value']['basis']=basis
    return sector_analysis.prepare_sector_analysis(symbol, as_of=DAY, providers={
        'profile':lambda *a,**k: {'status':'ok','source_id':'synthetic','as_of':DAY,
            'data':{'info':{},'evidence':[{'field':f,'value':v,'source_id':'synthetic','as_of':DAY}
                for f,v in [('instrument','equity'),('business_model',profile)]]}},
        'method_inputs':lambda *a,**k: {'status':'ok','source_id':'synthetic','as_of':DAY,'records':deepcopy(records)}},
        user_context={'analysis_context':{'scenario_rationale':{s:'Synthetic flat case, no unmodelled tax credits or financing' for s in ('bear','base','bull')}}})


def test_documented_operating_calculation_workbook_and_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(dcf_engine, '_generate_valuation_legacy',lambda *a,**k: pytest.fail('Legacy priors entered documented calculation'))
    result = dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'], result['valuation_usability']
    # EBIT 15, tax 0, +DA 5, -capex 10 => FCFF 10. Terminal EBIT15/.1=150.
    assert result['fair_value_base'] == pytest.approx(14.13)
    sidecar=json.loads(Path(result['path']).with_suffix('.payload.json').read_text(encoding='utf8'))
    assert sidecar['fair_value_base'] == result['fair_value_base']
    wb=load_workbook(result['path'],data_only=True)
    assert wb['Valuation']['B2'].value == pytest.approx(14.13)
    wb.close()
    assert result['acquisition_snapshot']['case']['route']=='operating'
    assert 'gross_margin' not in result['acquisition_snapshot']['case']['routing']['profilo']


@pytest.mark.parametrize('profile',['software','manufacturing','consumer_staples','consumer_discretionary','telecom','media','pharma_mature','services','payment_network','semiconductor_foundry','merchant_generation','hardware','semiconductors','semiconductor_fabless','semiconductor_equipment','payment_processor','fee_asset_manager','broker_fee','hospital','medtech','energy_services','contracted_generation'])
def test_same_economic_inputs_on_supported_profiles_have_same_result(tmp_path,profile):
    result=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(profile=profile),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result['valuation_usability']
    assert result['fair_value_base']==pytest.approx(14.13)


@pytest.mark.parametrize('fault',['missing','stale','extra','unit','duplicate','terminal','source','period','future_history'])
def test_documented_operating_gaps_block_fv(tmp_path,fault):
    records=operating_records()
    row=next(r for r in records if r['driver']=='wacc' and r['scenario']=='base')
    if fault=='missing': records.remove(row)
    if fault=='stale': row['valid_until']='2026-01-01'
    if fault=='extra': records.append({**row,'driver':'unused_beta'})
    if fault=='unit': row['unit']='percent'
    if fault=='duplicate': records.append(deepcopy(row))
    if fault=='terminal': row['value']=0.
    if fault=='source': row['source_id']=''
    if fault=='period': row['period']='wrong'
    if fault=='future_history': next(r for r in records if r['driver']=='revenue_growth')['kind']='historical'
    result=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(records),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None
    assert result['acquisition_tasks']


def test_capex_changes_real_cash_value_and_original_generation_survives(tmp_path):
    first=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(),output_dir=str(tmp_path))
    records=operating_records()
    next(r for r in records if r['driver']=='capex_pct' and r['scenario']=='base')['value']=[.2,.2]
    second=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(records),output_dir=str(tmp_path))
    assert second['valuation_usability']['usable'],second['valuation_usability']
    assert second['fair_value_base']==pytest.approx(12.40)
    assert first['fair_value_base']>second['fair_value_base']
    assert first['path']!=second['path'] and Path(first['path']).is_file()


@pytest.mark.parametrize('fault',['unreconciled_revenue','nonannual','bridge_rounding','capdev_amortization'])
def test_economic_driver_reconciliation_and_precision(tmp_path,fault):
    rows=operating_records()
    get=lambda d: next(r for r in rows if r['driver']==d and r['scenario']=='base')
    if fault=='unreconciled_revenue': get('revenue_build')['value']['volume'][0]=11.
    if fault=='nonannual':
        calendar=next(r for r in rows if r['driver']=='calendar')['value']
        calendar['discount_convention']='ACT/365F'
        calendar['periods'][0]['end']='2026-06-30'
    if fault=='bridge_rounding': get('equity_adjustments')['value']=.049
    if fault=='capdev_amortization':
        get('capdev_pct')['value']=[.04,.04]
        next(r for r in rows if r['driver']=='capdev_amortization_years')['value']=2
    result=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(rows),output_dir=str(tmp_path))
    if fault in ('unreconciled_revenue','nonannual'):
        assert not result['valuation_usability']['usable']
    elif fault=='bridge_rounding':
        assert result['fair_value_base']==pytest.approx(14.14)
    else:
        # Capitalized cash is reinvested; finite amortization changes terminal EBIT.
        assert result['calculation_details']['scenarios']['base']['rows']['ebit']==[17.,15.]


def test_terminal_capdev_must_be_normalized_and_expiry_changes_value(tmp_path):
    rows=operating_records()
    get=lambda d: next(r for r in rows if r['driver']==d and r['scenario']=='base')
    get('capdev_pct')['value']=[.04,.04]
    # Short forecast ends at EBIT17; steady-state amortization lowers it to15.
    get('terminal_bridge')['value']['capitalized_research_adjustment']=-2.
    good=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(rows),output_dir=str(tmp_path))
    assert good['valuation_usability']['usable'],good['valuation_usability']
    assert good['fair_value_base']==pytest.approx(14.13)
    get('terminal_bridge')['value']['expiring_product_loss']=3.
    get('terminal_bridge')['value']['normalized_ebit']=12.
    expired=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(rows),output_dir=str(tmp_path))
    assert expired['fair_value_base']==pytest.approx(11.65)
    get('terminal_bridge')['value']['capitalized_research_adjustment']=0.
    bad=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(rows),output_dir=str(tmp_path))
    assert not bad['valuation_usability']['usable']


@pytest.mark.parametrize('fault',['bad_scenario','overflow','historical_build','historical_terminal'])
def test_adversarial_records_fail_closed_without_crash(tmp_path,fault):
    rows=operating_records()
    get=lambda d: next(r for r in rows if r['driver']==d and r['scenario']=='base')
    if fault=='bad_scenario': get('wacc')['scenario']=[]
    if fault=='overflow': get('wacc')['value']=1e308
    if fault=='historical_build': get('revenue_build')['kind']='historical'
    if fault=='historical_terminal': get('terminal_bridge')['kind']='historical'
    result=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None
    assert result['acquisition_tasks']


def test_share_class_conversion_precedes_rounding(tmp_path):
    rows=operating_records()
    next(r for r in rows if r['driver']=='shares')['value']=100000.
    next(r for r in rows if r['driver']=='quotation')['value']['shares_per_quote']=10000.
    result=dcf_engine.generate_valuation('SYNTH-EXT',prepared_bundle=bundle_for(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result['valuation_usability']
    assert result['fair_value_base']==pytest.approx(14.13)
