"""A parent-only bridge over actual, separately gated equity valuations."""
from copy import deepcopy
import json
from .operating_adapter import SCHEMA as OPERATING_SCHEMA
from .documented_inputs import bind_inputs,finish_documented
from .dcf_quality import SCENARIOS,_finite,_text,_date
from .capital_inputs import equal
from .method_registry import is_record_method

SCHEMA={k:OPERATING_SCHEMA[k] for k in ('perimeter','calendar','quotation','shares')}
SCHEMA.update({
    'policy':('segment_perimeters','contract','scope','future','contract','model'),
    'segments':('segment_perimeters','contract','sotp','opening','contract','model'),
    'children':('segment_valuations','contract','sotp','opening','contract','model'),
    'parent_balance':('group_equity_bridge','contract','sotp','opening','contract','model'),
    'segment_fx':('segment_valuations','contract','sotp','opening','contract','scenario'),
    'claims':('group_equity_bridge','contract','sotp','opening','contract','scenario'),
    'central_cash_cost':('central_costs','money','cash','future','path','scenario'),
    'central_discount_rate':('discount_rate_inputs','ratio','valuation','future','number','scenario'),
    'central_growth':('central_costs','ratio','valuation','future','number','scenario')})
POLICY={'coverage':'all_reported_segments_and_ordinary_participations_no_omitted_businesses',
    'intercompany':'none_between_sotp_perimeters_except_ordinary_ownership_and_dividends',
    'cross_holdings':'none_between_sotp_perimeters',
    'guarantees':'none_between_sotp_perimeters_or_to_outsiders',
    'capital_calls':'fully_paid_holdings_no_contractual_parent_support_no_positive_modeled_calls',
    'central_costs':'parent_only_newly_incurred_after_tax_expense_equals_current_cash_no_new_accruals_or_deferrals_excludes_all_opening_claim_settlements_interest_child_costs_and_investment_book_value',
    'terminal_costs':'last_forecast_normalized_parent_cash_cost_grows_at_sourced_rate',
    'valuation':'proportional_common_equity_no_implicit_synergy_or_holding_discount',
    'claims_scope':'allocation_of_consumed_model_bridge_inputs_not_total_consolidated_financial_liabilities'}
CAPITAL_METHODS={'bank_residual_income','insurance_pc_distributable_equity','insurance_life_distributable_equity','managed_care_distributable_equity'}
FINITE_METHODS={'property_development_fcff','resources_asset_dcf','development_rnpv'}
NAV_METHODS={'fund_nav','digital_asset_nav','property_nav'}


def record(bundle,driver,scenario):
    matches=[(i,r) for i,r in enumerate(bundle['case']['records']) if r.get('driver')==driver and r.get('scenario')==scenario]
    if len(matches)!=1:raise ValueError('Record univoco richiesto: '+scenario+'/'+driver)
    return matches[0]


def claim_targets(bundle,scenario):
    """Map consumed balance drivers, without treating bank deposits as EV debt."""
    method=bundle['decision']['method_id'];targets=[]
    def add(driver,scope,role,path=(),*,no_debt=False):
        index,r=record(bundle,driver,scope);value=r['value']
        for key in path:value=value[key]
        amount=0. if no_debt and value is True else value
        if not _finite(amount):raise ValueError('Claims: valore finanziario non numerico')
        targets.append({'record_index':index,'record_ref':{'driver':driver,'scenario':scope,'path':list(path)},
            'entity':r['entity'],'amount':amount,'role':role})
    if method=='operating_fcff':
        add('net_debt',scenario,'net_debt');add('equity_adjustments',scenario,'equity_adjustments')
    elif method in CAPITAL_METHODS:
        add('capital.parent_opening_debt',scenario,'debt');add('capital.parent_opening_cash',scenario,'cash')
    elif method=='regulated_rab':
        add('opening_debt','model','debt');add('opening_cash','model','cash')
    elif method in NAV_METHODS:
        for key in ('cash','debt','preferred','other_liabilities','accrued_fees','distributions_payable','tax','equity_adjustments'):
            add('components','model',key,(key,))
    elif method in FINITE_METHODS:
        add('opening_cash','model','cash');schedule=record(bundle,'debt_schedule','model')[1]['value']
        if schedule=={'no_debt':True}:add('debt_schedule','model','debt',('no_debt',),no_debt=True)
        else:
            for loan in schedule:add('debt_schedule','model','debt',(loan,'settlement_value'))
    else:raise ValueError('Metodo child senza raccordo claims documentato: '+str(method))
    return targets


def _identity(bundle):
    method=bundle['decision']['method_id'];p=record(bundle,'perimeter','model')[1]['value']
    if method=='managed_care_distributable_equity':
        return p['consolidated_entity'],{p['consolidated_entity'],p['parent_entity'],*[s['id'] for s in p['subsidiaries']]},p
    entities={p['entity']}
    if method in CAPITAL_METHODS:
        legal=record(bundle,'legal_structure','model')[1]['value'];entities.update([legal['parent_entity'],*[s['id'] for s in legal['subsidiaries']]])
    return p['entity'],entities,p


def _projection(payload,scenario):
    return (payload['managed_care'] if payload['method']=='managed_care_distributable_equity' else payload['calculation_details'])['scenarios'][scenario]


def _has_calls(payload,bundle,scenario):
    method=bundle['decision']['method_id'];p=_projection(payload,scenario)
    if method in FINITE_METHODS:
        funding=record(bundle,'funding',scenario)[1]['value']
        if any(c['terms']=='draw_when_needed_existing_shareholders_no_new_shares'
               for c in funding['commitments'].values()):
            return True  # A sourced irrevocable commitment is support even if undrawn.
    if method=='managed_care_distributable_equity':return any(r['funding_required']>0 for r in p['capital']['rows'])
    if method in CAPITAL_METHODS or method=='regulated_rab':
        rows=p['rows']+([p['continuing']] if method=='regulated_rab' else [])
        return any(r['funding_required']>0 for r in rows)
    if method=='property_nav':return record(bundle,'funding',scenario)[1]['value']['equity_contribution']>0
    if method in ('property_development_fcff','resources_asset_dcf'):
        return any(r['funding_at_start']>0 or r['funding_at_end']>0 for r in p['ledger'])
    if method=='development_rnpv':
        return any(r['original_holder_cashflow_at_start']<0 or r['original_holder_cashflow_at_end']<0
            for o in p['outcomes'] if o['reachable'] for r in o['ledger'])
    if method=='operating_fcff':
        # Without a financing ledger, negative FCFF or positive net debt cannot
        # certify absence of a parent call. Acquisition, not a solvency inference.
        return any(v<0 for v in p['rows']['ufcf']) or record(bundle,'net_debt',scenario)[1]['value']>0
    return False  # NAV: fully paid holdings, no modeled future calls; policy required.


def _validate_claims(claims,parent,children,scenario,problem):
    if set(claims)!={'items'} or not isinstance(claims['items'],list) or not claims['items']:
        problem('claims','Inventario claims completo per parent e child richiesto');return None
    expected={};seen=set();totals={k:0. for k in ('cash','debt','preferred','other_liabilities')};observed={}
    for name,(bundle,payload) in children.items():
        consumed={r['record_index'] for r in payload['input_consumption']['consumed_records']}
        for t in claim_targets(bundle,scenario):
            if t['record_index'] not in consumed:problem('claims',name+': riferimento non consumato dal child')
            key=(name,json.dumps(t['record_ref'],sort_keys=True));expected[key]=t;observed[key]=[]
    required={'claim_id','allocation','legal_entity','currency','amount','role','measurement_basis','record_ref','source'}
    for item in claims['items']:
        if (not isinstance(item,dict) or set(item)!=required or
                any(not _text(item[k]) for k in ('claim_id','allocation','legal_entity','currency','role','measurement_basis','source')) or
                item['claim_id'] in seen or not _finite(item['amount'])):
            problem('claims','Strumento duplicato, non numerico o senza identificazione/fonte completa');continue
        seen.add(item['claim_id']);name=item['allocation']
        if name=='PARENT':
            if (item['legal_entity']!=parent['legal_entity'] or item['currency']!=parent['currency'] or
                    item['measurement_basis']!='standalone_parent_settlement' or item['record_ref'] is not None or
                    item['role'] not in totals or item['amount']<0):
                problem('claims','Parent: solo proprie poste standalone, nessun debito o book value dei child');continue
            totals[item['role']]+=item['amount'];continue
        if not isinstance(item['record_ref'],dict):problem('claims','Child: riferimento preciso al record consumato richiesto');continue
        key=(name,json.dumps(item['record_ref'],sort_keys=True));target=expected.get(key)
        if target is None or name not in children:
            problem('claims','Allocazione/riferimento child non presente nei driver finanziari consumati');continue
        child_currency=children[name][1]['financial_currency']
        roles={'debt','cash'} if target['role']=='net_debt' else {target['role']}
        if (item['legal_entity']!=target['entity'] or item['currency']!=child_currency or
                item['measurement_basis']!='consumed_model_bridge' or item['role'] not in roles or
                (item['role']!='equity_adjustments' and item['amount']<0)):
            problem('claims','Entita/base/valuta/segno non corrisponde al driver child consumato');continue
        observed[key].append(item)
    for key,target in expected.items():
        items=observed[key];net=target['role']=='net_debt'
        actual=sum((-1 if net and i['role']=='cash' else 1)*i['amount'] for i in items)
        if not items or (net and {i['role'] for i in items}!={'cash','debt'}) or not equal(actual,target['amount']):
            problem('claims',key[0]+': inventario strumenti non riconciliato al driver '+target['record_ref']['driver'])
    for role,total in totals.items():
        if not any(i.get('allocation')=='PARENT' and i.get('role')==role for i in claims['items'] if isinstance(i,dict)):
            problem('claims','Parent: assenza della categoria '+role+' non vale zero')
        if (not equal(total,parent[role]) if role!='debt' else total<parent[role]):
            problem('claims','Parent: '+role+' non riconciliato al bilancio standalone/settlement non inferiore al book debt')
    return totals


def generate_sotp(bundle,*,output_dir,metadata):
    from .dcf_engine import generate_valuation
    from .dcf_buyside_v3 import _dcf_value
    from .dcf_mnav import _common_equity_nav
    from .sector_analysis import validate_bundle
    bound=bind_inputs(bundle,SCHEMA);problem=bound['problem'];results={};children={}
    if not bound['issues']:
        model=bound['values']['model'];segments=model['segments'];parent=model['parent_balance']
        previous=_date(bound['calendar']['valuation_date'])
        for period in bound['calendar']['periods']:
            end=_date(period['end'])
            if (end.year,end.month,end.day)!=(previous.year+1,previous.month,previous.day):
                problem('calendar','Costi centrali e terminale richiedono anni interi; nessuna annualizzazione implicita')
            previous=end
        if model['policy']!=POLICY:problem('policy','Perimetri indipendenti/claims/costi/supporto parent non documentati o non supportati')
        parent_keys={'legal_entity','cash','debt','preferred','other_liabilities','investments_book','other_assets','common_equity','basis','cash_basis','source'}
        if (set(parent)!=parent_keys or not _text(parent['legal_entity']) or not _text(parent['source']) or
                not isinstance(parent['investments_book'],dict) or set(parent['investments_book'])!=set(segments) or
                any(not _finite(v) or v<0 for v in parent['investments_book'].values()) or
                any(not _finite(parent[k]) or parent[k]<0 for k in ('cash','debt','preferred','other_liabilities','other_assets')) or
                not _finite(parent['common_equity']) or parent['other_assets']!=0 or
                parent['basis']!='standalone_parent_excluding_child_assets_debt_income_and_funding' or parent['cash_basis']!='unrestricted'):
            problem('parent_balance','Bilancio standalone, investimenti separati e inventario completo parent richiesti')
        elif not equal(parent['cash']+sum(parent['investments_book'].values())-sum(parent[k] for k in ('debt','preferred','other_liabilities')),parent['common_equity']):
            problem('parent_balance','Attivo/passivo/equity parent non riconciliati')
        declared=bundle['decision']['segments']
        if (not segments or set(segments)!=set(model['children']) or not isinstance(declared,list) or
                any(set(d)!={'id','business_model'} or not _text(d['id']) for d in declared) or
                len({d['id'] for d in declared})!=len(declared) or {d['id'] for d in declared}!=set(segments)):
            problem('segments','Manifest ed evidenze devono coprire tutti i segmenti riportati, senza omissioni o duplicati')
        if model['shares']<=0:problem('shares','Azioni parent positive richieste')
        if not bound['issues']:
            parent={**parent,'currency':bound['perimeter']['currency']};used={parent['legal_entity'],bound['perimeter']['entity']}
            profiles={d['id']:d['business_model'] for d in declared}
            for name,segment in segments.items():
                try:
                    child=validate_bundle(model['children'][name],model['children'][name]['case']['ticker'])
                    decision=child['decision'];method=decision['method_id']
                    if not is_record_method(decision) or method=='mixed_business_sotp':raise ValueError('Child senza adapter documentato o SOTP ricorsiva non supportata')
                    entity,legal,perimeter=_identity(child);calendar=record(child,'calendar','model')[1]['value']
                    if (set(segment)!={'entity','legal_entities','profile_id','held_common_shares_m','share_class'} or
                            not isinstance(segment['legal_entities'],list) or any(not _text(v) for v in segment['legal_entities']) or
                            len(set(segment['legal_entities']))!=len(segment['legal_entities']) or set(segment['legal_entities'])!=legal or legal&used or
                            segment['entity']!=entity or segment['profile_id']!=decision['profile_id'] or profiles[name]!=decision['profile_id'] or
                            segment['share_class']!=perimeter['share_class'] or not _finite(segment['held_common_shares_m']) or segment['held_common_shares_m']<=0 or
                            child['case']['as_of']!=bundle['case']['as_of'] or calendar['valuation_date']!=bound['calendar']['valuation_date']):
                        raise ValueError('Perimetro/ownership/classe/cutoff child non riconciliato')
                    used.update(legal)
                    payload=generate_valuation(child['case']['ticker'],prepared_bundle=child,output_dir=output_dir)
                    if not payload['valuation_usability']['usable']:
                        raise ValueError('Child FV n.d.: '+'; '.join(payload['valuation_usability']['reasons']))
                    children[name]=(child,payload)
                except (ValueError,TypeError,KeyError,IndexError) as exc:
                    problem('children',str(name)+': '+str(exc))
            for scenario in SCENARIOS:
                sc=bound['values'][scenario];parts=[]
                if set(children)!=set(segments) or set(sc['segment_fx'])!=set(segments):
                    problem('children','Componenti o cambi incompleti: nessun totale parziale');continue
                totals=_validate_claims(sc['claims'],parent,children,scenario,problem)
                for name,(child,payload) in children.items():
                    try:
                        fx=sc['segment_fx'][name];currency=payload['financial_currency']
                        if (not isinstance(fx,dict) or set(fx)!={'currency','to_group_rate','as_of'} or fx['currency']!=currency or
                                not _finite(fx['to_group_rate']) or fx['to_group_rate']<=0 or
                                (currency==parent['currency'] and fx['to_group_rate']!=1) or fx['as_of']!=bound['calendar']['valuation_date']):
                            raise ValueError('Valuta/cambio/date child non riconciliati')
                        driver='capital.shares_m' if child['decision']['method_id'] in CAPITAL_METHODS else 'shares'
                        scope=scenario if driver.startswith('capital.') else 'model';index,r=record(child,driver,scope);denominator=r['value']
                        if index not in {c['record_index'] for c in payload['input_consumption']['consumed_records']} or not _finite(denominator) or denominator<=0:
                            raise ValueError('Denominatore child non consumato/non valido')
                        held=segments[name]['held_common_shares_m']
                        if held>denominator:raise ValueError('Azioni possedute oltre denominatore della stessa base')
                        if (child['decision']['method_id']=='digital_asset_nav' and
                                held>record(child,'capitalization','model')[1]['value']['basic_shares']):
                            raise ValueError('Azioni ordinarie possedute oltre basic emesse; warrant/convertibili parent fuori perimetro')
                        if _has_calls(payload,child,scenario):raise ValueError('Richiamo ai soci/funding child: acquisire ledger aggregato parent prima della SOTP')
                        projection=_projection(payload,scenario);raw=projection['fair_value_per_share']
                        if not _finite(raw) or raw<=0:raise ValueError('Equity grezza child non utilizzabile')
                        parts.append({'segment':name,'method_id':child['decision']['method_id'],'value_basis':'equity',
                            'raw_equity_value':raw*denominator,'valuation_shares_m':denominator,'held_common_shares_m':held,
                            'ownership_fraction':held/denominator,'fx_to_parent':fx['to_group_rate'],
                            'owned_equity_value':raw*held*fx['to_group_rate'],'snapshot_id':payload['snapshot_id'],
                            'generation_id':payload['generation_id'],'path':payload['path']})
                    except (ValueError,TypeError,KeyError,IndexError,ArithmeticError) as exc:problem('segment_valuations',name+': '+str(exc))
                rate=sc['central_discount_rate'];growth=sc['central_growth']
                if any(v<0 for v in sc['central_cash_cost']) or not rate>max(0.,growth) or growth<=-1:
                    problem('central_cash_cost','Costi/tasso/crescita perpetua parent fuori dominio');continue
                if totals is not None and len(parts)==len(segments):
                    try:
                        cost=_dcf_value({'documented_inputs':True,'discount_periods':bound['times'],'shares_m':1.,
                            'net_debt':0.,'equity_adjustments':[]},sc['central_cash_cost'],rate,growth)
                        equity=_common_equity_nav(sum(p['owned_equity_value'] for p in parts),totals['cash'],totals['debt'],
                            totals['preferred'],totals['other_liabilities']+cost,0.)
                        results[scenario]={'fair_value_per_share':equity/model['shares'],'value_basis':'equity','segments':parts,
                            'parent_claims':totals,'central_cost_pv':cost,'equity_value':equity,
                            'claim_inventory':sc['claims']['items'],'claims_scope':POLICY['claims_scope']}
                    except ArithmeticError as exc:problem('central_cash_cost','PV parent non rappresentabile: '+type(exc).__name__)
    metadata={**metadata,'child_valuations':{name:payload for name,(_,payload) in children.items()}}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='mixed_business_sotp')
