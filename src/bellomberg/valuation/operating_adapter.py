"""Documented FCFF adapter. Reuses cash-flow and discount engines, no calibration."""
from .documented_inputs import bind_inputs, finish_documented
from .dcf_quality import SCENARIOS, _finite, _date
from math import isclose


SCHEMA={
    'perimeter':('valuation_perimeter','contract','scope','future','contract','model'),
    'calendar':('valuation_perimeter','contract','calendar','future','contract','model'),
    'quotation':('quotation_units','contract','quotation','opening','contract','model'),
    'historical_revenue':('historical_financials','money','operating','opening','number','model'),
    'opening_nwc':('historical_financials','money','operating','opening','number','model'),
    'shares':('diluted_shares','million shares','valuation','opening','number','model'),
    'capdev_amortization_years':('reinvestment','years','operating','future','number','model'),
    'opening_intangible_amortization':('reinvestment','money','operating','future','path','scenario'),
    'wacc':('discount_rate_inputs','ratio','valuation','future','number','scenario'),
    'terminal_growth':('terminal_assumptions','ratio','valuation','future','number','scenario'),
    'terminal_ronic':('terminal_assumptions','ratio','valuation','future','number','scenario'),
    'terminal_bridge':('terminal_assumptions','contract','operating','future','contract','scenario'),
    'net_debt':('enterprise_equity_bridge','money','valuation','future','number','scenario'),
    'equity_adjustments':('enterprise_equity_bridge','money','valuation','future','number','scenario'),
    'accounting_policies':('margin_drivers','contract','operating','future','contract','scenario'),
    'revenue_build':('revenue_drivers','contract','operating','future','contract','scenario'),
}
for _driver in ('revenue_growth','gross_margin','rnd_pct','sga_pct','capdev_pct','da_tan_pct','tax_rate','capex_pct','nwc_pct'):
    _field='revenue_drivers' if _driver=='revenue_growth' else 'reinvestment' if _driver in ('capdev_pct','da_tan_pct','capex_pct','nwc_pct') else 'margin_drivers'
    SCHEMA[_driver]=(_field,'ratio','operating','future','path','scenario')

REVENUE_BASES={
    'software':'subscribers_arpu','telecom':'subscribers_arpu','media':'audience_yield',
    'payment_network':'transactions_fee','payment_processor':'transactions_fee',
    'fee_asset_manager':'aum_fee','broker_fee':'transactions_fee',
    'hardware':'units_price','semiconductors':'units_price','semiconductor_fabless':'units_price',
    'semiconductor_foundry':'capacity_utilization_price','semiconductor_equipment':'units_price',
    'manufacturing':'units_price','services':'units_price','consumer_discretionary':'units_price',
    'consumer_staples':'units_price','pharma_mature':'product_volume_price','medtech':'units_price',
    'hospital':'patients_yield','energy_services':'units_price',
    'merchant_generation':'capacity_utilization_price','contracted_generation':'capacity_utilization_price',
}


def generate_operating(bundle, *, output_dir, metadata):
    from .dcf_buyside_v3 import _scenario_numbers, _dcf_value
    bound=bind_inputs(bundle,SCHEMA); problem=bound['problem']; model=bound['values']['model']; results={}
    if not bound['issues']:
        previous=_date(bound['calendar']['valuation_date'])
        for period in bound['calendar']['periods']:
            end=_date(period['end'])
            if (end.month,end.day)!=(previous.month,previous.day) or end.year!=previous.year+1:
                problem('calendar','FCFF richiede esercizi annuali interi: ammortamenti e terminale non annualizzano stub implicitamente')
            previous=end
        if model['shares']<=0 or model['historical_revenue']<=0: problem('shares','Azioni e ricavi iniziali devono essere positivi')
        if type(model['capdev_amortization_years']) is not int or model['capdev_amortization_years']<=0:
            problem('capdev_amortization_years','Vita utile intera positiva esplicita richiesta')
        for scenario in SCENARIOS:
            sc=bound['values'][scenario]
            policies={'tax':'no_loss_tax_credit','sbc':'included_in_operating_costs',
                      'leases':'operating_rent_in_costs','research':'expensed_except_explicit_capdev',
                      'cycle':'explicit_forecast','patents':'explicit_forecast'}
            if sc['accounting_policies']!=policies:
                problem('accounting_policies',scenario+': basi non supportate; riconciliare SBC/costi, lease/rent, R&D, ciclo e scadenze nei driver espliciti')
            if sc['wacc']<=max(0,sc['terminal_growth']) or sc['terminal_ronic']<=max(0,sc['terminal_growth']):
                problem('terminal_ronic',scenario+': WACC e RONIC devono superare g; nessun clamp o terminale azzerato')
            if sc['terminal_growth']<=-1: problem('terminal_growth',scenario+': crescita <= -100% non ammissibile')
            terminal=sc['terminal_bridge']
            terminal_keys={'normalized_ebit','capitalized_research_adjustment','cycle_adjustment',
                           'expiring_product_loss','replacement_product_income','other_adjustment'}
            if set(terminal)!=terminal_keys or any(not _finite(v) for v in terminal.values()):
                problem('terminal_bridge',scenario+': ponte numerico completo dell EBIT normalizzato richiesto')
            elif terminal['expiring_product_loss']<0 or terminal['replacement_product_income']<0:
                problem('terminal_bridge',scenario+': perdite per scadenze e nuovi utili devono essere non negativi')
            if any(v<=-1 for v in sc['revenue_growth']): problem('revenue_growth',scenario+': ricavi negativi/non definiti')
            if any(not 0<=v<=1 for v in sc['tax_rate']): problem('tax_rate',scenario+': aliquota fuori [0,1]')
            if any(v<0 for key in ('rnd_pct','sga_pct','capdev_pct','da_tan_pct','capex_pct','opening_intangible_amortization') for v in sc[key]):
                problem('reinvestment',scenario+': costi/reinvestimenti negativi non supportati')
            if any(cap>rnd for cap,rnd in zip(sc['capdev_pct'],sc['rnd_pct'])):
                problem('capdev_pct',scenario+': sviluppo capitalizzato supera R&D da cui viene scorporato')
            revenue=sc['revenue_build']
            keys={'basis','volume','unit_price','utilization','other_revenue'}
            if set(revenue)!=keys or revenue.get('basis')!=REVENUE_BASES.get(bundle['decision']['profile_id']):
                problem('revenue_build',scenario+': base economica o campi non compatibili col profilo')
            elif any(not isinstance(revenue[k],list) or len(revenue[k])!=len(bound['times']) or any(not _finite(v) or v<0 for v in revenue[k]) for k in keys-{'basis'}):
                problem('revenue_build',scenario+': percorsi non negativi completi richiesti; unita del prodotto in milioni valuta bilancio')
            elif any(v>1 for v in revenue['utilization']):
                problem('revenue_build',scenario+': utilizzo fuori [0,1]')
            else:
                projected=model['historical_revenue']
                for i,growth in enumerate(sc['revenue_growth']):
                    projected*=1+growth
                    built=revenue['volume'][i]*revenue['unit_price'][i]*revenue['utilization'][i]+revenue['other_revenue'][i]
                    if not isclose(projected,built,rel_tol=1e-9,abs_tol=1e-8):
                        problem('revenue_build',scenario+': driver operativi non riconciliati ai ricavi nel periodo '+str(i))
    if not bound['issues']:
        for scenario in SCENARIOS:
            sc=bound['values'][scenario]
            spec={'documented_inputs':True,'capdev_amortization_years':model['capdev_amortization_years'],
                  'discount_periods':bound['times'],'net_debt':sc['net_debt'],
                  'shares_m':model['shares'],'diluted_shares_m':model['shares'], 'mid_year':False,
                  'equity_adjustments':[{'label':'Documented aggregate bridge','value_m':sc['equity_adjustments']}]}
            try:
                numbers=_scenario_numbers(spec,sc,model['historical_revenue'],nwc0=model['opening_nwc'])
            except ArithmeticError as exc:
                problem('reinvestment',scenario+': calcolo non definito: '+str(exc))
                continue
            terminal=sc['terminal_bridge']
            research_adjustment=numbers['research_amortization'][-1]-numbers['revenue'][-1]*sc['capdev_pct'][-1]
            bridged=(numbers['ebit'][-1]+terminal['capitalized_research_adjustment']+terminal['cycle_adjustment']
                     -terminal['expiring_product_loss']+terminal['replacement_product_income']+terminal['other_adjustment'])
            if not isclose(terminal['capitalized_research_adjustment'],research_adjustment,rel_tol=1e-9,abs_tol=1e-8):
                problem('terminal_bridge',scenario+': ricerca terminale deve essere interamente spesata; rettifica non riconciliata all ammortamento transitorio')
            if not isclose(bridged,terminal['normalized_ebit'],rel_tol=1e-9,abs_tol=1e-8):
                problem('terminal_bridge',scenario+': EBIT normalizzato non riconciliato a ultimo periodo/ciclo/scadenze/ricerca')
            try:
                fv=_dcf_value(spec,numbers['ufcf'],sc['wacc'],sc['terminal_growth'],
                              ebit_terminal=terminal['normalized_ebit'],ronic=sc['terminal_ronic'],tax_term=sc['tax_rate'][-1])
            except ArithmeticError as exc:
                problem('wacc',scenario+': sconto non definito: '+str(exc))
                continue
            results[scenario]={'fair_value_per_share':fv,'rows':numbers,'wacc':sc['wacc'],
                'terminal_growth':sc['terminal_growth'],'terminal_ronic':sc['terminal_ronic'],
                'terminal_bridge':terminal,'value_basis':'equity'}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='operating')
