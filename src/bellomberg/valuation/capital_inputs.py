"""Documented legal-entity binding and reconciliations shared by financial firms."""
from copy import deepcopy
from math import isclose

from .documented_inputs import bind_inputs
from .managed_care_adapter import _descriptor, _put
from .distributable_equity import CAPITAL_FIELDS, SUB_FIELDS, SUB_PATHS, CASH_SIGNS, project_distributable_equity
from .dcf_quality import _finite, _text


def equal(a,b):
    return _finite(a) and _finite(b) and isclose(a,b,rel_tol=1e-9,abs_tol=1e-8)


def bind_capital_inputs(bundle,schema,*,entity_schema=None):
    schema=deepcopy(schema); records=bundle['case']['records']
    structures=[r.get('value') for r in records if r.get('driver')=='legal_structure' and r.get('scenario')=='model']
    legal=structures[0] if len(structures)==1 and isinstance(structures[0],dict) else {}
    subs=legal.get('subsidiaries'); errors=[]
    if (set(legal)!={'parent_entity','subsidiaries','capital_basis','accounting_basis'} or
            legal.get('capital_basis')!='common_equity' or legal.get('accounting_basis')!='GAAP'):
        errors.append('Serve struttura GAAP/common_equity esplicita; total capital, IFRS e basi miste non riconciliate non supportati')
    if not isinstance(subs,list) or not subs or any(not isinstance(s,dict) or set(s)!={'id','regime'} or not _text(s['id']) or not _text(s['regime']) for s in subs):
        errors.append('Entita legali e regimi completi richiesti'); subs=[]
    ids=[s['id'] for s in subs]; parent=legal.get('parent_entity')
    group=next((r.get('value',{}).get('entity') for r in records if r.get('driver')=='perimeter' and isinstance(r.get('value'),dict)),None)
    # Parent-only and consolidated reporting can name the same legal issuer.
    if (not _text(parent) or not _text(group) or len(set(ids))!=len(ids)
            or any(identity in (parent,group) for identity in ids)):
        errors.append('Subsidiaries distinte tra loro e da parent/consolidato; emittenti espliciti richiesti')
    entities={'opening_parent_equity':parent}
    drivers=['capital.'+key for key in CAPITAL_FIELDS-{'subsidiaries','parent_cash_flows'}]
    drivers+=['capital.parent_cash_flows.'+key for key in CASH_SIGNS]
    drivers += ['capital.subsidiaries.'+str(i)+'.'+key for i in range(len(ids)) for key in SUB_FIELDS-{'id'}]
    for driver in drivers:
        field,unit,basis,timing=_descriptor(driver)
        if driver=='capital.terminal_equity': field,basis='terminal_assumptions','valuation'
        key=driver.split('.')[-1]
        shape='text' if unit=='text' else 'path' if (key in SUB_PATHS or '.parent_cash_flows.' in driver or key in ('parent_cash_minimum','parent_gaap_net_income','consolidation_adjustments','discount_periods')) else 'number'
        schema[driver]=(field,unit,basis,timing,shape,'scenario')
        if '.subsidiaries.' in driver:entities[driver]=ids[int(driver.split('.')[2])]
        elif field=='parent_ledger':entities[driver]=parent
    for i,identity in enumerate(ids):
        for driver,definition in (entity_schema or {}).items():
            name='insurance.'+str(i)+'.'+driver
            schema[name]=definition;entities[name]=identity
    bound=bind_inputs(bundle,schema,entities=entities)
    for error in errors:bound['problem']('legal_structure',error)
    bound['legal_ids']=ids
    return bound


def assemble_capital(values,ids):
    result={'subsidiaries':[{'id':i} for i in ids]}
    for driver,value in values.items():
        if driver.startswith('capital.'):_put(result,driver[8:],value)
    return result


def constraint_paths(item, n):
    """Validate one entity's declared constraints and calculate each binding path."""
    if (not isinstance(item, dict) or set(item) != {'basis', 'constraints'}
            or item['basis'] != 'common_equity' or not isinstance(item['constraints'], list)
            or not item['constraints']):
        raise ValueError('elenco completo dei vincoli common-equity applicabili richiesto')
    limits = {}
    for row in item['constraints']:
        if (not isinstance(row, dict) or set(row) != {'id', 'exposure', 'ratio', 'buffer', 'absolute_floor', 'terminal_requirement'}
                or not _text(row['id']) or row['id'] in limits
                or any(not isinstance(row[k], list) or len(row[k]) != n
                       or any(not _finite(x) or x < 0 for x in row[k])
                       for k in ('exposure', 'ratio', 'buffer', 'absolute_floor'))
                or not _finite(row['terminal_requirement']) or row['terminal_requirement'] < 0):
            raise ValueError('vincolo incompleto/duplicato/non numerico')
        path = [max(e*r+b, f) for e,r,b,f in zip(row['exposure'],row['ratio'],row['buffer'],row['absolute_floor'])]
        if any(not _finite(x) for x in path):
            raise ValueError('requisito calcolato non finito')
        limits[row['id']] = path
    return limits


def validate_constraints(capital,constraints,liquidity,problem):
    """Regulatory and cash bridges; nothing inferred from an institution label."""
    ids={s['id'] for s in capital['subsidiaries']}; n=len(capital['discount_periods'])
    def path(v):return isinstance(v,list) and len(v)==n and all(_finite(x) for x in v)
    if not isinstance(constraints,dict) or set(constraints)!=ids:
        problem('capital_constraints','Vincoli richiesti per ogni entita'); return
    if not isinstance(liquidity,dict) or set(liquidity)!=ids:
        problem('liquidity_bridge','Ponte liquidita richiesto per ogni entita'); return
    fees=[0.]*n; taxes=[0.]*n
    for sub in capital['subsidiaries']:
        identity=sub['id']; item=constraints[identity]; bridge=liquidity[identity]
        try:
            limits = constraint_paths(item, n)
        except ValueError as exc:
            problem('capital_constraints', identity+': '+str(exc)); continue
        for i in range(n):
            if not equal(max(v[i] for v in limits.values()),sub['required_statutory_capital'][i]):
                problem('capital_constraints',identity+': capitale richiesto non riconciliato ai vincoli nel periodo '+str(i))
        keys={'opening_cash','operating_cash','investing_cash','financing_cash','parent_fees_paid','parent_tax_paid'}
        if not isinstance(bridge,dict) or set(bridge)!=keys or not _finite(bridge['opening_cash']) or bridge['opening_cash']<0 or any(not path(bridge[k]) for k in keys-{'opening_cash'}):
            problem('liquidity_bridge',identity+': movimenti cash completi richiesti'); continue
        cash=bridge['opening_cash']
        for i in range(n):
            cash+=sum(bridge[k][i] for k in ('operating_cash','investing_cash','financing_cash'))-bridge['parent_fees_paid'][i]-bridge['parent_tax_paid'][i]
            if not equal(cash,sub['liquidity_before_transfers'][i]):
                problem('liquidity_bridge',identity+': saldo prima dei trasferimenti non riconciliato nel periodo '+str(i))
            cash+=sub['proposed_contribution'][i]-sub['proposed_distribution'][i]
            fees[i]+=bridge['parent_fees_paid'][i]; taxes[i]+=bridge['parent_tax_paid'][i]
    for key,total in (('admin_fees_received',fees),('tax_transfers_received',taxes)):
        if any(not equal(a,b) for a,b in zip(total,capital['parent_cash_flows'][key])):
            problem('liquidity_bridge','Trasferimenti entita non riconciliati a parent '+key)


def continuing_capital_coverage(opening,closing,required,g):
    """All-year coverage when retained capital flows and requirements grow at g.

    S(t)=S(0)+d*((1+g)**t-1)/g and R(t)=R(1)*(1+g)**(t-1).
    With positive g, coverage needs the first year and the asymptotic coefficient;
    at zero g it needs nonnegative retention; at negative g it needs a nonnegative
    limiting balance. These endpoint conditions cover every intervening year.
    This arithmetic proof does not certify the economic/source assumptions.
    """
    if (not all(_finite(x) for x in (opening,closing,required,g)) or g<=-1
            or min(opening,closing,required)<0):
        raise ValueError('Saldi/requisiti/crescita continuing non validi')
    retained=closing-opening
    left,right=(retained*(1+g),g*required) if g>0 else (retained,g*opening) if g<0 else (retained,0.)
    if not all(_finite(x) for x in (retained,left,right,left-right)):
        raise ValueError('Copertura continuing non finita')
    return {'retained_capital':retained,'perpetual_margin':left-right,
            'sustainable':(closing>=required or equal(closing,required)) and (left>=right or equal(left,right))}


def validate_terminal(terminal,capital,ledger,closing_book,sc,problem,*,terminal_income,allow_retained_flows=False):
    """One forward year proves continuing cash and capital, without reusing TV as cash."""
    keys={'capital','capital_constraints','liquidity_bridge'}
    retained_mode=(isinstance(terminal,dict) and allow_retained_flows
                   and terminal.get('statutory_projection')=='retained_flows_at_g')
    if not isinstance(terminal,dict) or set(terminal)!=(keys|{'statutory_projection'} if retained_mode else keys):
        problem('terminal_ledger','Un anno continuing di ledger, vincoli e liquidita richiesto');return
    cap=terminal['capital']; income=terminal_income
    result=project_distributable_equity(cap,[income],[ledger['rows'][-1]['year']+1])
    if result['status']!='CALCOLABILE':
        for issue in result['issues']:problem('terminal_ledger',issue)
        return
    last=ledger['rows'][-1]
    if (cap['discount_periods']!=[1.] or cap['ke']!=capital['ke'] or cap['shares_m']!=capital['shares_m'] or
            not equal(cap['parent_opening_cash'],last['parent_closing_cash']) or
            not equal(cap['parent_opening_debt'],last['parent_closing_debt']) or cap['terminal_equity']!=0):
        problem('terminal_ledger','Calendario/azioni/Ke/saldi continuing non riconciliati; valore finale deve essere zero nel solo anno prova')
    by_id={s['id']:s for s in cap['subsidiaries']}
    if set(by_id)!={s['id'] for s in capital['subsidiaries']}:
        problem('terminal_ledger','Perimetro entita differente dal forecast');return
    for sub,end in zip(capital['subsidiaries'],last['subsidiaries']):
        nxt=by_id[sub['id']]
        book=sub['opening_gaap_equity']+sum(sub['gaap_net_income'])+sum(sub['proposed_contribution'])-sum(sub['proposed_distribution'])
        if not equal(nxt['opening_gaap_equity'],book) or not equal(nxt['opening_statutory_capital'],end['closing_statutory_capital']):
            problem('terminal_ledger',sub['id']+': saldi iniziali continuing non riconciliati')
        bridge=terminal['liquidity_bridge']
        if not isinstance(bridge,dict) or not isinstance(bridge.get(sub['id']),dict) or not equal(bridge[sub['id']].get('opening_cash'),end['closing_liquidity']):
            problem('terminal_ledger',sub['id']+': liquidita iniziale continuing non riconciliata')
        constraints=sc['capital_constraints'][sub['id']]['constraints']
        if not equal(nxt['required_statutory_capital'][0],max(c['terminal_requirement'] for c in constraints)):
            problem('terminal_ledger',sub['id']+': requisito terminale non riconciliato')
    validate_constraints(cap,terminal['capital_constraints'],terminal['liquidity_bridge'],problem)
    growth=1+sc['terminal_growth']
    continuing=result['rows'][0]
    for key in ('cash','debt'):
        if not equal(continuing['parent_closing_'+key],cap['parent_opening_'+key]*growth):
            problem('terminal_ledger','Saldo parent '+key+' non sostenibile alla crescita continuing dichiarata')
    for end in continuing['subsidiaries']:
        sub=by_id[end['id']]
        if retained_mode:
            try:
                coverage=continuing_capital_coverage(sub['opening_statutory_capital'],
                    end['closing_statutory_capital'],sub['required_statutory_capital'][0],sc['terminal_growth'])
                if not coverage['sustainable']:
                    problem('terminal_ledger',end['id']+': capitale insufficiente nella prosecuzione perpetua dei flussi')
            except ValueError as exc:
                problem('terminal_ledger',end['id']+': '+str(exc))
        elif not equal(end['closing_statutory_capital'],sub['opening_statutory_capital']*growth):
            problem('terminal_ledger',end['id']+': capitale continuing non cresce alla base dichiarata')
        bridge=terminal['liquidity_bridge']
        if isinstance(bridge,dict) and isinstance(bridge.get(end['id']),dict):
            opening_cash=bridge[end['id']].get('opening_cash')
            if not _finite(opening_cash) or not equal(end['closing_liquidity'],opening_cash*growth):
                problem('terminal_ledger',end['id']+': liquidita continuing non sostenibile')
        item=terminal['capital_constraints'].get(end['id']) if isinstance(terminal['capital_constraints'],dict) else None
        if isinstance(item,dict) and isinstance(item.get('constraints'),list):
            for constraint in item['constraints']:
                if isinstance(constraint,dict) and all(k in constraint for k in ('terminal_requirement','exposure','ratio','buffer','absolute_floor')) and all(isinstance(constraint[k],list) and len(constraint[k])==1 and _finite(constraint[k][0]) for k in ('exposure','ratio','buffer','absolute_floor')):
                    current=max(constraint['exposure'][0]*constraint['ratio'][0]+constraint['buffer'][0],constraint['absolute_floor'][0])
                    if not equal(constraint['terminal_requirement'],current*growth):
                        problem('terminal_ledger',end['id']+': requisito successivo non sostenibile alla crescita continuing')
    distribution=result['rows'][0]['shareholder_net_distribution']
    if not equal(income-distribution,closing_book*sc['terminal_growth']) or distribution<0:
        problem('terminal_ledger','Distribuzione continuing non sostiene crescita del common equity; niente payout o finanziamento implicito')
    return result
