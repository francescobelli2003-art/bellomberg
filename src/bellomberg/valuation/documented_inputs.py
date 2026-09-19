"""Shared binding and output for documented adapters; no economic defaults.

Calculations belong to the existing family engines. Every accepted record is
bound to one declared driver; schemas reject extra, stale and unconsumed data.
"""
from bellomberg.core.language import scoped_language, text as _lt
from bellomberg.reporting.i18n_excel import label as _xt
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import re

from .dcf_quality import SCENARIOS, _date, _finite, _text, _evidence_issues, _portable
from .method_registry import get_method_requirements


def bind_inputs(bundle, schema, *, entities=None, horizon='forecast', forward_period=None):
    """schema: driver -> (field, unit, basis, timing, shape, scope).

timing is opening/future/terminal; shape is number/path/text/contract. Scope model
or scenario is explicit. The caller validates each contract's exact keys.
"""
    case=bundle['case']; rows=case['records']; issues=[]; consumed=[]; evidence=[]
    values={'model':{}, **{s:{} for s in SCENARIOS}}
    def problem(driver, reason):
        issues.append({'field':schema[driver][0] if driver in schema else str(driver),
                       'status':'data_missing','reason':str(driver)+': '+reason})
    if case['assumptions']:
        problem('assumptions','Parametri legacy non consumati: '+', '.join(sorted(case['assumptions']))+'; usare i driver documentati del metodo')
    context=bundle['analysis_context']
    if set(context)-{'scenario_rationale','revisions'}:
        problem('analysis_context','Campi non consumati nel contratto documentato')
    rationale=context.get('scenario_rationale')
    if not isinstance(rationale,dict) or set(rationale)!=set(SCENARIOS) or any(not _text(v) for v in rationale.values()):
        problem('scenario_rationale','Motivazioni esplicite bear/base/bull richieste')
    if context.get('revisions'):
        problem('revisions','Revisioni non riconciliate: fornire nuovo caso documentato; storia acquisizioni conservata')
    def structure(name):
        matches=[r for r in rows if r.get('driver')==name and r.get('scenario')=='model']
        return matches[0]['value'] if len(matches)==1 and isinstance(matches[0].get('value'),dict) else {}
    perimeter=structure('perimeter'); calendar=structure('calendar'); quotation=structure('quotation')
    if set(perimeter)!={'entity','currency','share_class'} or any(not _text(v) for v in perimeter.values()):
        problem('perimeter','Entita, valuta e classe azioni esplicite richieste')
    currency=perimeter.get('currency')
    if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3}',currency):
        problem('perimeter','Valuta ISO richiesta')
    if set(calendar)!={'valuation_date','periods','discount_convention'}:
        problem('calendar','Calendario completo richiesto, campi estranei non consumati')
    opening=_date(calendar.get('valuation_date')); periods=calendar.get('periods')
    cutoff=_date(case['as_of'])
    if not opening or opening>cutoff:
        problem('calendar','Data valore non valida o futura rispetto al cutoff informativo')
    valid_periods=isinstance(periods,list) and bool(periods) and all(isinstance(p,dict) and set(p)=={'start','end'} and _date(p['start']) and _date(p['end']) for p in periods)
    times=[]; span=None
    if horizon=='snapshot':
        if periods!=[] or calendar.get('discount_convention')!='snapshot':
            problem('calendar','NAV richiede fotografia datata senza orizzonte/sconto fittizio')
        if forward_period is not None:
            if (not isinstance(forward_period,dict) or set(forward_period)!={'start','end'} or
                    not _date(forward_period.get('start')) or not _date(forward_period.get('end')) or not opening):
                problem('forward_year','Periodo forward separato completo richiesto')
            else:
                start,end=_date(forward_period['start']),_date(forward_period['end'])
                if start!=opening+timedelta(days=1) or (end.year,end.month,end.day)!=(opening.year+1,opening.month,opening.day):
                    problem('forward_year','Anno forward intero successivo al NAV richiesto')
                span=forward_period['start']+'/'+forward_period['end']
    elif not valid_periods:
        problem('calendar','Periodi start/end espliciti richiesti')
    elif opening:
        previous=opening
        for i,p in enumerate(periods):
            start,end=_date(p['start']),_date(p['end'])
            if start!=previous+timedelta(days=1) or end<start:
                problem('calendar','Periodi non contigui/crescenti dal cutoff dei saldi')
            convention=calendar.get('discount_convention')
            if convention=='annual_end':
                if (end.month,end.day)!=(previous.month,previous.day) or end.year!=previous.year+1:
                    problem('calendar','annual_end richiede esercizi interi annuali; usare ACT/365F per intervalli diversi')
                times.append(float(i+1))
            elif convention=='ACT/365F': times.append((end-opening).days/365.)
            else: problem('calendar','Convenzione sconto non supportata')
            previous=end
        span='|'.join(p['start']+'/'+p['end'] for p in periods)
    quote_keys={'financial_currency','quote_currency','quote_unit','quote_units_per_currency',
                'financial_to_quote_rate','shares_per_quote','share_class','price','price_as_of'}
    if set(quotation)!=quote_keys:
        problem('quotation','Quotazione, conversione e share class esplicite richieste')
    else:
        for key in ('price','financial_to_quote_rate','quote_units_per_currency','shares_per_quote'):
            if not _finite(quotation[key]) or quotation[key]<=0: problem('quotation',key+' deve essere positivo')
        if all(_finite(quotation[k]) for k in ('financial_to_quote_rate','quote_units_per_currency','shares_per_quote')):
            scale=quotation['financial_to_quote_rate']*quotation['quote_units_per_currency']*quotation['shares_per_quote']
            if not _finite(scale) or scale<=0:problem('quotation','Conversione complessiva non rappresentabile; niente overflow/underflow silenzioso')
        qc=quotation['quote_currency']; qu=quotation['quote_unit']
        if not isinstance(qc,str) or not re.fullmatch('[A-Z]{3}',qc): problem('quotation','Valuta quotazione ISO richiesta')
        if quotation['financial_currency']!=currency or quotation['share_class']!=perimeter.get('share_class'):
            problem('quotation','Valuta/classe non coerente col perimetro')
        if quotation['price_as_of']!=calendar.get('valuation_date'):
            problem('quotation','Prezzo e saldi devono avere stessa data valore; nessun rollforward implicito')
        if qc==currency and quotation['financial_to_quote_rate']!=1: problem('quotation','Cambio stessa valuta diverso da uno')
        if not ((qu==qc and quotation['quote_units_per_currency']==1) or
                (qc=='GBP' and qu in ('GBX','GBp') and quotation['quote_units_per_currency']==100)):
            problem('quotation','Scala quotazione non riconciliata')
    seen=set()
    for index,row in enumerate(rows):
        driver=row.get('driver'); scenario=row.get('scenario')
        if not isinstance(driver,str) or driver not in schema or not isinstance(scenario,str) or scenario not in values:
            problem(str(driver),'Driver/scenario non consumato'); continue
        field,unit,basis,timing,shape,scope=schema[driver]
        errors=list(row.get('validation_issues') or [])
        allowed={'field','driver','scenario','value','entity','period','unit','accounting_basis','source_id',
                 'as_of','valid_until','kind','rationale','status','provider','validation_issues','source_locator'}
        if set(row)-allowed: errors.append('Campi record non consumati')
        identity=(scenario,driver)
        if identity in seen: errors.append('Driver/scenario duplicato')
        seen.add(identity)
        if (scope=='model')!=(scenario=='model'): errors.append('Scope modello/scenario errato')
        expected={'field':field,'unit':currency+{'money':' million','price':' per share','money_per_policy':' per policy'}[unit] if unit in ('money','price','money_per_policy') and isinstance(currency,str) else unit,
                  'accounting_basis':basis,'entity':(entities or {}).get(driver,perimeter.get('entity')),
                  'period':calendar.get('valuation_date') if timing=='opening' else periods[-1]['end'] if timing=='terminal' and valid_periods else span}
        errors.extend(k+' non coerente col driver' for k,v in expected.items() if row.get(k)!=v)
        proof={'scenario':scenario,'driver':driver,'values':deepcopy(row.get('value')),'kind':row.get('kind'),
               'source':row.get('source_id'),'source_date':row.get('as_of'),'valid_until':row.get('valid_until'),
               'metric':driver,'basis':row.get('accounting_basis'),'rationale':row.get('rationale'),
               'entity':row.get('entity'),'period':row.get('period'),'unit':row.get('unit')}
        errors.extend(_evidence_issues(proof,cutoff))
        if timing in ('future','terminal') and (shape in ('number','path') or scenario!='model' and driver!='accounting_policies') and row.get('kind')=='historical':
            errors.append('Osservazione storica non sostituisce una previsione')
        value=row.get('value')
        if shape=='number' and not _finite(value): errors.append('Numero finito esplicito richiesto')
        if shape=='path' and (not isinstance(value,list) or len(value)!=len(times) or any(not _finite(v) for v in value)):
            errors.append('Percorso numerico completo richiesto per ogni periodo')
        if shape=='text' and not _text(value): errors.append('Testo esplicito richiesto')
        if shape=='contract' and not isinstance(value,dict): errors.append('Contratto strutturato richiesto')
        if errors:
            problem(driver,'; '.join(errors)); continue
        values[scenario][driver]=deepcopy(value)
        consumed.append({'record_index':index,**{key:row[key] for key in ('field','scenario','driver','entity','period','source_id')}})
        evidence.append({'scenario':scenario,'driver':driver,'values':deepcopy(value),'evidence':proof,'issues':[]})
    for driver,definition in schema.items():
        for scenario in ('model',) if definition[-1]=='model' else SCENARIOS:
            if driver not in values[scenario]: problem(driver,scenario+': input documentato mancante/non consumato')
    return {'values':values,'issues':issues,'consumed':consumed,'evidence':evidence,'times':times,
            'perimeter':perimeter,'calendar':calendar,'quotation':quotation,'problem':problem}


def finish_documented(bundle, bound, scenarios, *, metadata, output_dir, engine):
    """One common quality/sanity/Excel/sidecar result for the new record adapters."""
    from .dcf_engine import sanity_check, _write_payload_sidecar, REPORT_DIR
    from .dcf_quality import normalize_valuation_payload
    issues=bound['issues']; method=bundle['decision']['method_id']; quote=bound['quotation']
    def nonfinite(value):
        if isinstance(value,dict): return any(nonfinite(v) for v in value.values())
        if isinstance(value,list): return any(nonfinite(v) for v in value)
        return isinstance(value,(int,float)) and not isinstance(value,bool) and not _finite(value)
    if nonfinite(scenarios):
        bound['problem']('calculation','Risultato aritmetico non finito: acquisire driver validi; nessun valore di ripiego')
        scenarios=_portable(scenarios)
    required=[f['field'] for f in get_method_requirements(method)['fields']]
    consumed_fields=sorted({r['field'] for r in bound['consumed']})
    missing=sorted((set(required)-set(consumed_fields)) | set(bundle['case']['assumptions']))
    complete=not issues and not missing and len(bound['consumed'])==len(bundle['case']['records'])
    result={**metadata,'ticker':bundle['case']['ticker'],'engine':engine,'method':method,'ok':complete,
        'currency':quote.get('quote_unit'),'financial_currency':bound['perimeter'].get('currency'),'price':quote.get('price'),
        'valuation_date':bound['calendar'].get('valuation_date'),
        'valuation_basis':'Documented scenario valuation at opening balances/quotation cutoff; regenerate to change assumptions.',
        'analytical_quality':{'method_id':method,'status':'DOCUMENTATA' if complete else 'INCOMPLETA',
            'record_adapter':True,'snapshot':{'forecast_years':[p['end'] for p in bound['calendar'].get('periods',[]) if isinstance(p,dict) and 'end' in p]},
            'issues':[p['reason'] for p in issues], 'rows':bound['evidence']},
        'input_consumption':{'status':'complete' if complete else 'incomplete','consumed_fields':consumed_fields,
            'unconsumed_fields':missing,'consumed_records':bound['consumed']},
        'acquisition_tasks':deepcopy(bundle['acquisition_tasks'])+deepcopy(issues),
        'fair_value_weighted':None, 'calculation_details':{'scenarios':scenarios}}
    if complete:
        factor=quote['financial_to_quote_rate']*quote['quote_units_per_currency']*quote['shares_per_quote']
        checks={}
        for scenario in SCENARIOS:
            value=scenarios.get(scenario,{}).get('fair_value_per_share')
            result['fair_value_'+scenario]=round(value*factor,2) if _finite(value) else None
            check=sanity_check(result['fair_value_'+scenario],quote['price'])
            if not _finite(value) or value<=0:
                check.update(status='incomplete',severity='BLOCK',exclude_from_action_table=True)
            checks[scenario]=check
        severity=max((c['severity'] for c in checks.values()),key={'OK':0,'WARN':1,'BLOCK':2}.get)
        result['sanity']={**checks['base'],'severity':severity,'method_id':method,
            'exclude_from_action_table':severity=='BLOCK','scenario_checks':checks}
        result['upside_pct']=checks['base'].get('upside_pct')
    else:
        result['sanity']={'status':'incomplete','severity':'BLOCK','method_id':method,'exclude_from_action_table':True,
                          'headline':'Dati o riconciliazioni incompleti: FV n.d.'}
    from .market_quote import build_market_quote
    result['market_quote']=build_market_quote(bundle,quote,{s:result.get('fair_value_'+s) for s in SCENARIOS})
    result=normalize_valuation_payload(result,expected_decision=bundle['decision'],as_of=bundle['case']['as_of'])
    if not result['valuation_usability']['usable']:
        result['ok']=False
        result['error']='FV n.d.; '+'; '.join([p['reason'] for p in issues]+result['valuation_usability']['reasons'])
    if bound['calendar'].get('periods') or bound['calendar'].get('discount_convention')=='snapshot':
        result['path']=build_documented_workbook(result,output_dir or REPORT_DIR)
        return _write_payload_sidecar(result)
    return result


@scoped_language
def build_documented_workbook(payload, output_dir):
    """Immutable numeric snapshot, shared quality sheets; no second formula engine."""
    from openpyxl import Workbook
    from .dcf_quality_sheet import append_quality_sheet, append_sector_quality_sheet
    directory=Path(output_dir); directory.mkdir(parents=True,exist_ok=True)
    symbol=re.sub(r'[^A-Za-z0-9_-]','_',payload['ticker'])
    path=directory/('VAL_'+symbol+'_'+payload['generation_id']+'.xlsx')
    wb=Workbook(); ws=wb.active; ws.title='Valuation'
    for row in [[payload['method'],payload['ticker']],[_xt('Fair value Base'),payload.get('fair_value_base')],
                [_xt('Valuation date'),payload.get('valuation_date')],[_xt('Basis'),payload['valuation_basis']],
                [_xt('Assumptions'),_xt('Regenerate the documented inputs; this workbook is a calculation snapshot.')]]: ws.append(row)
    from bellomberg.reporting.valuation_quote import append_market_quote_rows
    append_market_quote_rows(ws,payload.get('market_quote'),usable=payload['valuation_usability']['usable'])
    def flatten(value,prefix=''):
        if isinstance(value,dict):
            for key,child in value.items(): yield from flatten(child,prefix+'.'+str(key) if prefix else str(key))
        elif isinstance(value,list):
            for i,child in enumerate(value): yield from flatten(child,prefix+'.'+str(i))
        else: yield prefix,value
    for scenario,data in payload['calculation_details']['scenarios'].items():
        sheet=wb.create_sheet(scenario)
        for key,value in flatten(data): sheet.append([key,value])
    append_quality_sheet(wb,payload['analytical_quality']); append_sector_quality_sheet(wb,payload)
    for sheet in wb:
        sheet.freeze_panes='B2'; sheet.column_dimensions['A'].width=55
        for row in sheet:
            for cell in row:
                if isinstance(cell.value,str): cell.data_type='s'
    wb.save(path); wb.close()
    return str(path)
