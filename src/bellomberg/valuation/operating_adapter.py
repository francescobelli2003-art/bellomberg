"""Documented FCFF adapter. Reuses cash-flow and discount engines, no calibration."""
from .documented_inputs import bind_inputs, finish_documented
from .dcf_quality import SCENARIOS, _finite, _date, _text, _url
from datetime import date, timedelta
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

# Additional documented basis; the existing profile bases remain valid.
REVENUE_ALTERNATIVE_BASES={'manufacturing':('segment_guidance',)}
SEGMENT_KEYS={'segment_id','label','role','base','base_evidence','growth','growth_evidence'}
BASE_EVIDENCE_KEYS={'kind','sources','observation_period','derivation'}
GROWTH_EVIDENCE_KEYS={'kind','sources','guidance_period','derivation'}


def _segment_sources(sources):
    return (isinstance(sources,list) and bool(sources)
            and all(isinstance(source,dict) and set(source)=={'source_id','locator'}
                    and _url(source['source_id']) and _text(source['locator']) for source in sources))


def _segment_span(value):
    if not isinstance(value,dict) or set(value)!={'start','end'}:
        return None
    start,end=_date(value['start']),_date(value['end'])
    return (start,end) if start and end and start<=end else None


def _segment_openings(revenue):
    """Called after validation: economic identity is the segment id, not list order."""
    return {item['segment_id']:{key:item[key] for key in ('label','role','base','base_evidence')}
            for item in revenue['segments']}


def segment_guidance_issues(revenue, *, historical_revenue, revenue_growth, periods,
                            valuation_date, record_kind, aggregate_kind):
    """Validate cited segment paths against opening sales and every group forecast.

    This checks the declared evidence and arithmetic, not the cited document itself.
    No residual, forecast or opening value is inferred here.
    """
    if set(revenue)!={'basis','segments'}:
        return ['segment_guidance: campi esatti basis/segments; nessun other_revenue o residuo anonimo']
    items=revenue['segments']
    if not isinstance(items,list) or any(not isinstance(item,dict) or set(item)!=SEGMENT_KEYS for item in items):
        return ['segment_guidance: ogni voce richiede esattamente '+', '.join(sorted(SEGMENT_KEYS))]
    issues=[]
    ids=[item['segment_id'] for item in items]
    if any(not _text(value) for value in ids) or len(set(ids))!=len(ids) or any(not _text(item['label']) for item in items):
        issues.append('segment_guidance: segment_id o label vuoti, oppure segment_id duplicati')
    roles=[item['role'] for item in items]
    if any(role not in ('segment','consolidation') for role in roles) or roles.count('segment')<2 or roles.count('consolidation')>1:
        issues.append('segment_guidance: almeno due segment e al massimo una consolidation; un solo segmento ripete il totale')
    opening=_date(valuation_date)
    derived=False
    for item in items:
        name='segment_guidance: '+str(item['segment_id'])
        if (not _finite(item['base']) or (item['role']=='segment' and item['base']<=0)
                or (item['role']=='consolidation' and item['base']==0)):
            issues.append(name+': base finita richiesta, positiva per i segmenti, diversa da zero per la consolidation (ometterla se assente)')
        proof=item['base_evidence']
        span=_segment_span(proof.get('observation_period')) if isinstance(proof,dict) else None
        previous=span[0]-timedelta(days=1) if span and span[0]>date.min else None
        whole_year=bool(previous and opening and span[1]==opening
                        and (previous.year,previous.month,previous.day)==(opening.year-1,opening.month,opening.day))
        if (not isinstance(proof,dict) or set(proof)!=BASE_EVIDENCE_KEYS or proof['kind']!='historical'
                or not _segment_sources(proof['sources']) or not _text(proof['derivation']) or not whole_year):
            issues.append(name+': base osservata richiesta (historical, fonti URL e locator, derivazione, anno intero chiuso alla data valore)')
        growth,evidence=item['growth'],item['growth_evidence']
        if not isinstance(growth,list) or len(growth)!=len(periods) or any(not _finite(value) or value<=-1 for value in growth):
            issues.append(name+': crescita finita > -100% richiesta per ogni periodo')
        if not isinstance(evidence,list) or len(evidence)!=len(periods):
            issues.append(name+': evidenza di crescita richiesta per ogni periodo')
            continue
        for index,(proof,period) in enumerate(zip(evidence,periods)):
            where=name+' periodo '+str(index)
            if (not isinstance(proof,dict) or set(proof)!=GROWTH_EVIDENCE_KEYS
                    or proof['kind'] not in ('company_guidance','analyst_estimate')
                    or not _segment_sources(proof['sources']) or not _text(proof['derivation'])):
                issues.append(where+': evidenza company_guidance o analyst_estimate con fonti URL e locator e derivazione richiesta')
                continue
            if proof['guidance_period'] is not None and _segment_span(proof['guidance_period']) is None:
                issues.append(where+': guidance_period start/end ISO oppure null')
            elif proof['kind']=='company_guidance' and (item['role']=='consolidation' or proof['guidance_period']!=period):
                issues.append(where+': company_guidance solo se la guidance copre esattamente il periodo del modello e mai sulla consolidation; una derivazione e analyst_estimate')
            derived=derived or proof['kind']=='analyst_estimate'
    for driver,kind in (('revenue_build',record_kind),('revenue_growth',aggregate_kind)):
        if derived and kind!='analyst_estimate':
            issues.append('segment_guidance: record '+driver+' con celle derivate dichiarato '+str(kind)+'; il kind del record e la provenienza piu debole')
    if issues:
        return issues
    if not isclose(sum(item['base'] for item in items),historical_revenue,rel_tol=1e-9,abs_tol=1e-8):
        issues.append('segment_guidance: somma delle basi non riconciliata ai ricavi iniziali')
    projected=historical_revenue
    levels=[item['base'] for item in items]
    for index,growth in enumerate(revenue_growth):
        projected*=1+growth
        levels=[level*(1+item['growth'][index]) for level,item in zip(levels,items)]
        total=sum(levels)
        if not _finite(projected) or not _finite(total) or any(not _finite(level) for level in levels):
            issues.append('segment_guidance: ricavi non finiti nel periodo '+str(index))
        elif not isclose(projected,total,rel_tol=1e-9,abs_tol=1e-8):
            issues.append('segment_guidance: segmenti non riconciliati ai ricavi nel periodo '+str(index))
    return issues


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
        openings={}
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
            if revenue.get('basis')=='segment_guidance' and 'segment_guidance' in REVENUE_ALTERNATIVE_BASES.get(bundle['decision']['profile_id'],()):
                kinds={row['driver']:row['evidence']['kind'] for row in bound['evidence'] if row['scenario']==scenario}
                reasons=segment_guidance_issues(revenue,historical_revenue=model['historical_revenue'],
                    revenue_growth=sc['revenue_growth'],periods=bound['calendar']['periods'],
                    valuation_date=bound['calendar']['valuation_date'],record_kind=kinds['revenue_build'],aggregate_kind=kinds['revenue_growth'])
                for reason in reasons:
                    problem('revenue_build',scenario+': '+reason)
                if not reasons:
                    openings[scenario]=_segment_openings(revenue)
            else:
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
        if openings and (set(openings)!=set(SCENARIOS) or any(openings[scenario]!=openings['base'] for scenario in SCENARIOS)):
            problem('revenue_build','segment_guidance: stessa base di ricavo e stesse aperture dei segmenti in bear/base/bull; l apertura e una sola osservazione')
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
