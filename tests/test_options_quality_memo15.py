"""Options provider boundaries, synthetic HTTP and dates only."""
from datetime import date,timedelta
from types import SimpleNamespace
import pytest
from bellomberg.market_data import polygon_data as p
from bellomberg.agents import specialist_scores as s


@pytest.mark.parametrize('expirations', [[], ['2000-01-01', 'bad']])
def test_yahoo_no_valid_expiry_keeps_legacy_label_and_machine_code(monkeypatch, expirations):
    from bellomberg.agents import agent_tools as a
    calls = []
    monkeypatch.setattr(p, 'polygon_available', lambda: False)
    monkeypatch.setattr(a, '_get_ibkr_options', lambda *args, **kwargs: None)
    monkeypatch.setattr(a, 'YFINANCE_AVAILABLE', True)
    monkeypatch.setattr(a.yf, 'Ticker', lambda ticker: SimpleNamespace(
        options=expirations, option_chain=lambda expiry: calls.append(expiry)))
    out = a._options_data_usa('ZZTEST')
    assert out['error_code'] == 'expiry_no_valid_available'
    assert 'Nessuna opzione disponibile' in out['error']
    assert 'nessuna scadenza valida per la richiesta' in out['error']
    assert out['available_expiries'] == expirations and calls == []


@pytest.mark.parametrize('kind', ['empty', 'past', 'near_only'])
def test_polygon_no_valid_expiry_keeps_legacy_label_and_machine_code(monkeypatch, kind):
    today = date.today()
    expirations = {'empty': [], 'past': ['2000-01-01'],
                  'near_only': [str(today), str(today + timedelta(days=1))]}[kind]
    coverage = {'status': 'COMPLETE', 'requests': 1}
    calls = []
    monkeypatch.setattr(p, 'polygon_available', lambda: True)
    monkeypatch.setattr(p, 'get_option_expirations', lambda ticker: {
        'expirations': expirations, 'coverage': coverage})
    monkeypatch.setattr(p, 'get_options_chain', lambda *args, **kwargs: calls.append(args))
    out = p.get_options_summary_polygon('ZZTEST')
    assert out['error_code'] == 'expiry_no_valid_available'
    assert 'Nessuna opzione disponibile' in out['error']
    assert 'nessuna scadenza valida per la richiesta' in out['error']
    assert out['coverage'] == coverage and calls == []


def test_yahoo_explicit_unavailable_error_is_not_no_valid_default(monkeypatch):
    from bellomberg.agents import agent_tools as a
    calls = []
    monkeypatch.setattr(p, 'polygon_available', lambda: False)
    monkeypatch.setattr(a, '_get_ibkr_options', lambda *args, **kwargs: None)
    monkeypatch.setattr(a, 'YFINANCE_AVAILABLE', True)
    monkeypatch.setattr(a.yf, 'Ticker', lambda ticker: SimpleNamespace(
        options=['2099-01-16'], option_chain=lambda expiry: calls.append(expiry)))
    out = a._options_data_usa('ZZTEST', expiry='2099-01-23')
    assert out == {'error': 'expiry_requested_unavailable: 2099-01-23',
                   'available_expiries': ['2099-01-16']}
    assert calls == []


def test_polygon_explicit_chain_error_kept_distinct(monkeypatch):
    calls = []
    monkeypatch.setattr(p, 'polygon_available', lambda: True)
    def chain(ticker, expiry, **kwargs):
        calls.append(expiry)
        return {'error': 'no data', 'coverage': {'status': 'UNAVAILABLE'}}
    monkeypatch.setattr(p, 'get_options_chain', chain)
    out = p.get_options_summary_polygon('ZZTEST', expiry='2099-01-23')
    assert out == {'error': 'no data', 'coverage': {'status': 'UNAVAILABLE'},
                   'expiry_coverage': None, '_source': 'polygon options summary'}
    assert calls == ['2099-01-23']

@pytest.fixture
def http(monkeypatch):
    calls=[]
    monkeypatch.setattr(p,'POLYGON_KEY','SYNTHETIC_OPTIONS_KEY')
    monkeypatch.setattr(p,'REQ_OK',True)
    def arm(responses):
        def get(url,**kw):
            calls.append((url,kw))
            status,payload=responses[min(len(calls)-1,len(responses)-1)]
            return SimpleNamespace(status_code=status,text='apiKey=SYNTHETIC_OPTIONS_KEY',json=lambda:payload)
        monkeypatch.setattr(p.requests,'get',get)
    return arm,calls

@pytest.mark.parametrize('status',[401,403,429])
def test_expirations_stops_native_http(http,status):
    arm,calls=http;arm([(status,{})])
    out=p.get_option_expirations('ZZTEST')
    assert len(calls)==1
    assert out['error']==f'HTTP {status}'
    assert out['coverage']['status']=='UNAVAILABLE'

@pytest.mark.parametrize('status',[401,403,429])
def test_summary_stops_after_partial_expiry_error(http,status):
    arm,calls=http;arm([(200,{'results':[{'expiration_date':'2099-01-16'}],'next_url':p.BASE+'/next'}),(status,{})])
    out=p.get_options_summary_polygon('ZZTEST')
    assert len(calls)==2 # no future window, no snapshot HTTP
    assert out['error']==f'HTTP {status}'
    assert out['coverage']['status']=='PARTIAL'
    assert out['coverage']['requests']==2


def test_chain_partial_error_keeps_rows_and_error(http):
    arm,calls=http;arm([(200,{'results':[{'details':{'ticker':'X','expiration_date':'2099-01-16','contract_type':'call','strike_price':10}}],'next_url':p.BASE+'/next'}),(503,{})])
    out=p.get_options_chain('ZZTEST','2099-01-16')
    assert out['n_contracts']==1 and len(calls)==2
    assert out['coverage']['status']=='PARTIAL'
    assert out['coverage']['errors']==['HTTP 503']
    assert out['coverage']['rows_observed']==1


def test_chain_output_cap_disclosed(http):
    arm,calls=http;arm([(200,{'results':[{'details':{'ticker':str(i),'expiration_date':'2099-01-16','strike_price':i}} for i in range(3)]})])
    out=p.get_options_chain('ZZTEST','2099-01-16',max_contracts=2)
    assert len(out['chain'])==2
    assert out['coverage']['status']=='PARTIAL'
    assert out['coverage']['rows_observed']==3 and out['coverage']['rows_returned']==2
    assert 'output_limit' in out['coverage']['issues']

@pytest.mark.parametrize('expiry,reason',[(None,'missing'),('garbage','invalid'),('2000-01-01','past')])
def test_score_unavailable_date_visible_to_scoreboard(expiry,reason):
    out=s.options_score('ZZTEST',options_data={'nearest_expiry':expiry,'atm_iv_call_pct':18,'atm_iv_put_pct':21,'put_call_oi_ratio':1})
    assert out['score'] is None and out['max_score'] is None
    assert reason in out['unavailable_reason']
    text=s.format_scoreboard({'options':out})
    assert reason in text and 'None/None' not in text

@pytest.mark.parametrize('field',['nearest_expiry','expiry_used'])
@pytest.mark.parametrize('days',[0,1,2])
def test_score_today_and_future_unchanged(field,days):
    # fix score 09/10 (Opus 5.5): P/C e ATM IV di UNA scadenza sono informativi (il regime si
    # legge su struttura VIX e superficie a ~30g, qui non passate): niente punti, n.d. dichiarato;
    # una scadenza a 0-1 giorni non porta nemmeno il valore informativo
    out=s.options_score('ZZTEST',options_data={field:str(date.today()+timedelta(days=days)),'atm_iv_call_pct':18,'atm_iv_put_pct':21,'put_call_oi_ratio':1})
    assert out['score'] is None and out['max_score'] is None and out['unavailable_reason']=='no_regime_metric'
    pc=[r for r in out['info'] if 'una scadenza' in r[0]][0]
    assert (pc[1].startswith('1.00') if days>=2 else pc[1].startswith('n.d.: scadenza a')), pc


def test_yahoo_default_filters_expiries_and_empty_request(monkeypatch):
    from bellomberg.agents import agent_tools as a
    import pandas as pd
    # fix 09/10 (Opus 5.5, audit SCORE-VOL-QUANT §1.1): il default di yfinance salta lo 0DTE e
    # l'1DTE come Polygon (min_days=2). Prima questo test BLOCCAVA lo 0DTE come scelta: un P/C e
    # un'ATM IV di una scadenza che muore oggi misurano la microstruttura di chiusura, non il regime.
    chosen=[]
    today=date.today()
    def chain(e):
        chosen.append(e)
        return SimpleNamespace(calls=pd.DataFrame(),puts=pd.DataFrame())
    tk=SimpleNamespace(options=['garbage','2000-01-01','2099-01-16',str(today),str(today+timedelta(days=1)),
                                str(today+timedelta(days=2))],option_chain=chain)
    monkeypatch.setattr(p,'polygon_available',lambda:False)
    monkeypatch.setattr(a,'_get_ibkr_options',lambda *args,**kw:None)
    monkeypatch.setattr(a,'YFINANCE_AVAILABLE',True)
    monkeypatch.setattr(a.yf,'Ticker',lambda t:tk)
    out=a._options_data_usa('ZZTEST',expiry='')
    assert chosen==[str(today+timedelta(days=2))] and out['error']=='Chain vuota'
    chosen.clear()
    a._get_yfinance_oi_only('ZZTEST')
    assert chosen==[str(today+timedelta(days=2))]
    chosen.clear()
    a._options_data_usa('ZZTEST',expiry=str(today))   # la data ESPLICITA non si sostituisce
    assert chosen==[str(today)]


def test_yahoo_explicit_absent_not_replaced(monkeypatch):
    from bellomberg.agents import agent_tools as a
    chosen=[]
    monkeypatch.setattr(p,'polygon_available',lambda:False)
    monkeypatch.setattr(a,'_get_ibkr_options',lambda *args,**kw:None)
    monkeypatch.setattr(a,'YFINANCE_AVAILABLE',True)
    monkeypatch.setattr(a.yf,'Ticker',lambda t:SimpleNamespace(options=['2099-01-16'],option_chain=lambda e:chosen.append(e)))
    out=a._options_data_usa('ZZTEST',expiry='2099-01-23')
    assert 'error' in out and chosen==[]

def test_unavailable_score_preamble_no_numeric_ratio_and_zero_preserved():
    unavailable=s.options_score('ZZTEST',options_data={'put_call_oi_ratio':1})
    block=s.format_score_block(unavailable)
    assert 'expiry_missing' in block and 'None/None' not in block
    # fix score 09/10: lo zero vero del P/C resta (informativo, scadenza >= 2 giorni)
    zero=s.options_score('ZZTEST',options_data={'nearest_expiry':str(date.today()+timedelta(days=3)),'put_call_oi_ratio':0})
    assert zero['score'] is None and zero['metrics']['put_call_oi']==0
    assert any(r[1].startswith('0.00') for r in zero['info'])


def test_summary_partial_chain_exposes_coverage(http):
    arm,calls=http
    rows=[{'details':{'contract_type':side,'strike_price':10,'expiration_date':'2099-01-16'},'open_interest':10,'implied_volatility':.2} for side in ('call','put')]
    arm([(200,{'results':rows,'next_url':p.BASE+'/next'}),(429,{})])
    out=p.get_options_summary_polygon('ZZTEST','2099-01-16')
    assert len(calls)==2 and out['partial'] is True
    # fix 09/10 (Opus 5.5, audit SCORE-VOL-QUANT §1.1): prima questo test asseriva un P/C (=1)
    # emesso da una chain interrotta da un HTTP 429, cioe' il fallback silenzioso. Righe
    # mancanti = P/C e max pain n.d. DICHIARATI col motivo; i conteggi grezzi restano visibili.
    assert out['coverage']['errors']==['HTTP 429'] and out['put_call_oi_ratio'] is None
    assert 'HTTP 429' in out['put_call_oi_ratio_nd'] and out['max_pain_strike'] is None
    assert out['total_call_oi']==10 and out['total_put_oi']==10


def test_polygon_default_two_days_but_explicit_today_preserved(monkeypatch):
    chosen=[]
    today=date.today()
    monkeypatch.setattr(p,'polygon_available',lambda:True)
    monkeypatch.setattr(p,'get_option_expirations',lambda t:{'expirations':['bad','2000-01-01',str(today),str(today+timedelta(days=1)),str(today+timedelta(days=2))]})
    monkeypatch.setattr(p,'get_options_chain',lambda t,e,**k:chosen.append(e) or {'error':'fake empty chain'})
    p.get_options_summary_polygon('ZZTEST',' ')
    p.get_options_summary_polygon('ZZTEST',str(today))
    assert chosen==[str(today+timedelta(days=2)),str(today)]


def test_ibkr_default_rejects_old_dates_at_native_boundary(monkeypatch):
    from test_core_audit_regressions import _ibkr
    import sys
    a,_,expiries=_ibkr(monkeypatch)
    # fix 09/10: default IBKR >= 2 giorni (niente 0DTE), stessa regola di Polygon e yfinance
    today=date.today().strftime('%Y%m%d')
    in2=(date.today()+timedelta(days=2)).strftime('%Y%m%d')
    monkeypatch.setattr(sys.modules['ib_async'].IB,'reqSecDefOptParams',lambda *args:[SimpleNamespace(exchange='SMART',expirations={'invalid','20000101','20990116',today,in2},strikes={100})])
    out=a._get_ibkr_options('ZZTEST')
    assert expiries==[in2,in2] and out['nearest_expiry']==in2


def test_conflicting_expiry_is_unavailable():
    out=s.options_score('ZZTEST',options_data={'expiry_used':'2099-01-16','nearest_expiry':'20990123','put_call_oi_ratio':1})
    assert out['score'] is None and out['unavailable_reason']=='expiry_conflicting'

def test_real_specialist_preamble_caches_unavailable_without_zero(monkeypatch):
    from bellomberg.agents.specialists import base
    board=SimpleNamespace(data={},memory_db=None,mark_specialist_start=lambda *args:None)
    actor=base.Specialist(board,client=object())
    actor.name='options'
    calls=[]
    def compute():
        calls.append(1)
        return s.options_score('ZZTEST',options_data={'put_call_oi_ratio':1})
    monkeypatch.setattr(actor,'compute_score',compute)
    first=actor._build_round_context(0)
    second=actor._build_round_context(0)
    assert calls==[1]
    assert 'expiry_missing' in first and 'expiry_missing' in second
    assert 'None/None' not in first and board.data['_score_cache']['options']['score'] is None
    assert not board.data['_score_errors']
    rows=s.collect_scoreboard(board.data['_score_cache'])
    row=next(row for row in rows if row['key']=='options')
    assert row['score'] is None and row['max_score'] is None and 'expiry_missing' in row['verdict']

@pytest.mark.parametrize('value',[True,False,17,'2099-02-30','2099-W01-1'])
def test_score_invalid_expiry_types(value):
    out=s.options_score(options_data={'nearest_expiry':value,'put_call_oi_ratio':1})
    assert out['score'] is None and out['unavailable_reason']=='expiry_invalid'


def test_expiration_window_and_page_limits_are_disclosed(http):
    arm,calls=http;arm([(200,{'results':[{'expiration_date':'2099-01-16'}],'next_url':p.BASE+'/next'})])
    out=p.get_option_expirations('ZZTEST')
    assert len(calls)==7 and out['coverage']['pages_received']==7
    assert out['coverage']['status']=='PARTIAL' and out['coverage']['issues']==['page_limit']*4


def test_chain_complete_empty_and_partial_are_distinct(http):
    arm,calls=http;arm([(200,{'results':[]})])
    out=p.get_options_chain('ZZTEST','2099-01-16')
    assert out['error']=='no data' and out['coverage']['status']=='UNAVAILABLE'
    assert out['coverage']['requests']==out['coverage']['pages_received']==1


def test_complete_chain_summary_keeps_measured_values(http):
    arm,calls=http
    rows=[{'details':{'contract_type':side,'strike_price':10,'expiration_date':'2099-01-16'},'open_interest':oi,'implied_volatility':.2} for side,oi in [('call',10),('put',20)]]
    arm([(200,{'results':rows})])
    out=p.get_options_summary_polygon('ZZTEST','2099-01-16')
    assert len(calls)==1 and out['partial'] is False and out['coverage']['status']=='COMPLETE'
    assert out['total_call_oi']==10 and out['total_put_oi']==20 and out['put_call_oi_ratio']==2

@pytest.mark.parametrize('bad',[False,None,'', 'garbage'])
def test_invalid_declared_expiry_not_hidden_by_alias(bad):
    out=s.options_score(options_data={'expiry_used':bad,'nearest_expiry':'2099-01-16','put_call_oi_ratio':1})
    assert out['score'] is None and out['unavailable_reason'].startswith('expiry_')

@pytest.mark.parametrize('foreign',[None,'bad','2099-02-30','2099-01-23'])
def test_polygon_summary_excludes_wrong_or_missing_row_expiry(http,foreign):
    arm,calls=http
    rows=[{'details':{'contract_type':side,'strike_price':10,'expiration_date':'2099-01-16'},'open_interest':oi,'implied_volatility':.2} for side,oi in [('call',10),('put',20)]]
    rows.append({'details':{'contract_type':'put','strike_price':10,'expiration_date':foreign},'open_interest':10000,'implied_volatility':.9})
    arm([(200,{'results':rows})])
    out=p.get_options_summary_polygon('ZZTEST','2099-01-16')
    assert out['total_put_oi']==20 and out['put_call_oi_ratio']==2
    assert out['expiry_used']=='2099-01-16' and out['partial'] is True
    assert out['coverage']['rows_rejected_expiry']==1 and out['coverage']['rows_used']==2
    assert out['coverage']['status']=='PARTIAL'
    assert len(out['coverage']['expiry_rejections'])==1


def test_polygon_wrong_expiry_cannot_supply_missing_put(http):
    arm,calls=http
    rows=[{'details':{'contract_type':side,'strike_price':10,'expiration_date':expiry},'open_interest':10} for side,expiry in [('call','2099-01-16'),('put','2099-01-23')]]
    arm([(200,{'results':rows})])
    out=p.get_options_summary_polygon('ZZTEST','2099-01-16')
    assert 'error' in out and 'put_call_oi_ratio' not in out
    assert out['coverage']['rows_rejected_expiry']==1 and out['coverage']['rows_used']==1


def test_raw_chain_preserves_mismatching_row_for_review(http):
    arm,calls=http
    arm([(200,{'results':[{'details':{'ticker':'WRONG','contract_type':'put','strike_price':10,'expiration_date':'2099-01-23'},'open_interest':10}]})])
    out=p.get_options_chain('ZZTEST','2099-01-16')
    assert out['chain'][0]['expiry']=='2099-01-23' and out['chain'][0]['oi']==10

def test_missing_key_is_local_not_configured_and_never_http(monkeypatch):
    monkeypatch.setattr(p,'POLYGON_KEY','')
    monkeypatch.setattr(p,'REQ_OK',True)
    calls=[]
    monkeypatch.setattr(p.requests,'get',lambda *a,**k:calls.append(1))
    out=p.get_options_summary_polygon('ZZTEST')
    assert not p.polygon_available() and calls==[]
    assert out['error']=='POLYGON_API_KEY mancante' and 'HTTP' not in out['error']
