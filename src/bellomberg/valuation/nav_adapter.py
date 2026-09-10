"""Source-bound NAV snapshots; no rates, forecast horizon or private registry defaults."""
from copy import deepcopy
import re
from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
from .documented_inputs import bind_inputs,finish_documented
from .operating_adapter import SCHEMA as OPERATING_SCHEMA
from .capital_inputs import equal
from .dcf_quality import SCENARIOS,_finite,_text,_date

COMMON={k:(*OPERATING_SCHEMA[k][:3],'opening',*OPERATING_SCHEMA[k][4:]) for k in ('perimeter','calendar','quotation','shares')}
COMMON.update({
    'nav_target':('valuation_target','ratio','valuation','opening','number','scenario'),
    'target_basis':('valuation_target','text','valuation','opening','text','scenario'),
})
FUND={**COMMON,
    'publication':('nav_source','contract','nav','opening','contract','model'),
    'components':('vehicle_liabilities','contract','nav','opening','contract','model'),
    'reported_nav_per_share':('nav_per_share','price','nav','opening','number','model'),
    'reported_nav_precision':('nav_per_share','decimals','nav','opening','number','model'),
    'policy':('distribution_policy','contract','nav','opening','contract','model'),
}
DIGITAL={**COMMON,
    'holdings':('treasury_holdings','contract','nav','opening','contract','model'),
    'asset_prices':('asset_prices','contract','nav','opening','contract','model'),
    'components':('treasury_liabilities','contract','nav','opening','contract','model'),
    'capitalization':('dilution_terms','contract','nav','opening','contract','model'),
}
COMPONENTS={'gross_assets','cash','debt','preferred','other_liabilities','accrued_fees','distributions_payable','tax','equity_adjustments'}


def digital_components(model,bound):
    problem=bound['problem']; holdings=model['holdings'];prices=model['asset_prices'];value=0.
    if not holdings or set(holdings)!=set(prices):
        problem('holdings','Holdings e prezzi devono coprire gli stessi asset');return None
    for asset,h in holdings.items():
        p=prices[asset]
        if not _text(asset) or not isinstance(h,dict) or set(h)!={'quantity_millions','custody','ownership_fraction'} or not _text(h['custody']) or not _finite(h['quantity_millions']) or h['quantity_millions']<0 or not _finite(h['ownership_fraction']) or not 0<h['ownership_fraction']<=1:
            problem('holdings',asset+': unita/ownership/custodia complete richieste');continue
        if not isinstance(p,dict) or set(p)!={'price','currency','fx_to_financial','price_as_of'} or not all(_finite(p[k]) and p[k]>0 for k in ('price','fx_to_financial')) or not isinstance(p['currency'],str) or not re.fullmatch('[A-Z]{3}',p['currency']):
            problem('asset_prices',asset+': prezzo/valuta/cambio completo richiesto');continue
        if p['price_as_of']!=bound['calendar']['valuation_date'] or (p['currency']==bound['perimeter']['currency'] and p['fx_to_financial']!=1):
            problem('asset_prices',asset+': data/cambio non riconciliati alla fotografia')
        value+=h['quantity_millions']*h['ownership_fraction']*p['price']*p['fx_to_financial']
    cap=model['capitalization'];keys={'basis','basic_shares','conversions','warrants','debt_basis','cash_basis'}
    if set(cap)!=keys or cap.get('basis') not in ('basic','if_converted') or cap.get('debt_basis')!='par_before_conversion' or cap.get('cash_basis')!='before_exercise' or not _finite(cap.get('basic_shares')) or cap['basic_shares']<=0 or not isinstance(cap['conversions'],list) or not isinstance(cap['warrants'],list):
        problem('capitalization','Base azioni, conversioni e claims prima della conversione completi richiesti');return None
    if cap['basis']=='basic' and (cap['conversions'] or cap['warrants']):
        problem('capitalization','Basic con claims diluitivi non riconciliato; fornire convenzione if_converted esplicita')
    shares=cap['basic_shares'];debt_removed=0.;exercise_cash=0.;ids=set()
    q=bound['quotation'];share_price=q['price']/(q['financial_to_quote_rate']*q['quote_units_per_currency']*q['shares_per_quote'])
    for name,numeric in (('conversions',{'face_value','shares'}),('warrants',{'strike','shares'})):
        fields=numeric|{'id','exercise','exercisable_from','expires_on'}
        for claim in cap[name]:
            if not isinstance(claim,dict) or set(claim)!=fields or not _text(claim['id']) or claim['id'] in ids or any(not _finite(claim[k]) or claim[k]<0 for k in numeric) or claim['shares']<=0:
                problem('capitalization',name+': claim incompleto/duplicato/non valido');continue
            start,end=_date(claim['exercisable_from']),_date(claim['expires_on']);cutoff=_date(bound['calendar']['valuation_date'])
            if claim['exercise']!='voluntary_in_the_money' or not start or not end or not start<=cutoff<=end:
                problem('capitalization',name+': esercizio volontario immediatamente disponibile e scadenze documentate richiesti')
            if (name=='warrants' and claim['strike']>=share_price) or (name=='conversions' and claim['face_value']>=share_price*claim['shares']):
                problem('capitalization',name+': conversione/esercizio non economicamente disponibile; non aggiungere cash o debito liberato al FV')
            ids.add(claim['id']);shares+=claim['shares']
            if name=='conversions':debt_removed+=claim['face_value']
            else:exercise_cash+=claim['strike']*claim['shares']
    components=deepcopy(model['components'])
    if not equal(shares,model['shares']) or debt_removed>components['debt']:
        problem('capitalization','Azioni FD/debito convertito non riconciliati')
    components['gross_assets']=value+components.pop('operating_assets')
    components['cash']+=exercise_cash;components['debt']-=debt_removed
    return components


def generate_nav(bundle,*,output_dir,metadata):
    from .dcf_mnav import compute_mnav_values
    digital=bundle['decision']['method_id']=='digital_asset_nav'
    bound=bind_inputs(bundle,DIGITAL if digital else FUND,horizon='snapshot');problem=bound['problem'];results={}
    if not bound['issues']:
        model=bound['values']['model'];components=model['components']
        expected=COMPONENTS-{'gross_assets'}|{'operating_assets'} if digital else COMPONENTS
        if set(components)!=expected or any(not _finite(v) or (k!='equity_adjustments' and v<0) for k,v in components.items()):
            problem('components','Attivita/cash/claims/fees/tax completi e numerici richiesti')
        if model['shares']<=0:problem('shares','Denominatore azioni deve essere positivo')
        for row in bundle['case']['records']:
            if row.get('driver') in ('nav_target','target_basis') and row.get('kind')=='historical':
                problem(row['driver'],'Target analitico non puo essere certificato come storico')
            if not digital and row.get('driver') in ('publication','reported_nav_per_share','reported_nav_precision') and row.get('kind')!='historical':
                problem(row['driver'],'NAV pubblicato richiede osservazione storica ufficiale; stima analista/guidance non e una pubblicazione effettiva')
        if not digital:
            if type(model['reported_nav_precision']) is not int or not 0<=model['reported_nav_precision']<=8:
                problem('reported_nav_precision','Precisione pubblicazione intera tra zero e otto decimali; convenzione half-up richiesta')
            pub=model['publication']; keys={'publisher','publication_date','valuation_date','basis','share_class'}
            if set(pub)!=keys or not _text(pub.get('publisher')) or pub.get('valuation_date')!=bound['calendar']['valuation_date'] or pub.get('share_class')!=bound['perimeter']['share_class'] or pub.get('basis')!='common_equity_net':
                problem('publication','Pubblicazione NAV ufficiale datata/perimetro/classe e base netta richiesti')
            elif not _date(pub['publication_date']) or not _date(pub['valuation_date'])<=_date(pub['publication_date'])<=_date(bundle['case']['as_of']):
                problem('publication','Data pubblicazione non coerente con valore e cutoff informativo')
            if model['policy']!={'liability_basis':'all_claims_in_components','fees_basis':'accrued_fees_in_components',
                'distributions_basis':'payables_deducted','share_basis':'basic_no_convertibles_or_other_dilution'}:
                problem('policy','Basi NAV/claims/fee/distribuzioni/diluizione non supportate o non riconciliate')
    if not bound['issues']:
        if digital:components=digital_components(model,bound)
        if components is not None:
            for scenario in SCENARIOS:
                sc=bound['values'][scenario]
                if sc['nav_target']<=0 or sc['target_basis'] not in (('equity_nav','gross_assets_ev') if digital else ('equity_nav',)):
                    problem('nav_target',scenario+': target positivo e base economica espliciti richiesti');continue
                result=compute_mnav_values({'documented_inputs':True,'components':components,'shares':model['shares'],
                    'nav_target':sc['nav_target'],'target_basis':sc['target_basis']})
                if not digital:
                    try:
                        rounded=Decimal(str(result['nav_per_share'])).quantize(Decimal(1).scaleb(-model['reported_nav_precision']),rounding=ROUND_HALF_UP)
                    except InvalidOperation:
                        problem('reported_nav_per_share','NAV non rappresentabile alla precisione pubblicata');continue
                    if not equal(float(rounded),model['reported_nav_per_share']):
                        problem('reported_nav_per_share','NAV pubblicato non riconciliato ad attivita/claims/azioni e precisione dichiarata; nessun proxy')
                results[scenario]={**result,'fair_value_per_share':result['fair_value_nav'],'value_basis':'equity',
                    'value_description':'Snapshot NAV at the declared balance/price date and explicit premium/discount target.'}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='mnav')
