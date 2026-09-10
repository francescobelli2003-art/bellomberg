"""Documented public routes cannot consume a legacy prior beside their records."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine,sector_analysis
from test_documented_consumers import CASES


@pytest.mark.parametrize('factory,records_factory,method,missing',CASES)
@pytest.mark.parametrize('assumptions',[{'peers':['SYNTH-PEER']},{'method_weights':{'premium':1.}}])
def test_legacy_prior_inputs_are_explicitly_unconsumed(tmp_path,monkeypatch,factory,records_factory,method,missing,assumptions):
    original=factory();source=original['case']
    providers={k:(lambda *a,v=v,**kw:deepcopy(v)) for k,v in source['sources'].items()}
    prepared=sector_analysis.prepare_sector_analysis(source['ticker'],as_of=source['as_of'],providers=providers,
        user_context={'assumptions':assumptions,'analysis_context':original['analysis_context']})
    def forbidden(*a,**k):raise AssertionError('Legacy priors entered a documented route')
    monkeypatch.setattr(dcf_engine,'_generate_valuation_legacy',forbidden)
    result=dcf_engine.generate_valuation(source['ticker'],prepared_bundle=prepared,output_dir=str(tmp_path))
    assert result['valuation_decision']['method_id']==method
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert set(assumptions)<=set(result['input_consumption']['unconsumed_fields'])
    assert any('legacy' in t.get('reason','').lower() for t in result['acquisition_tasks'])
