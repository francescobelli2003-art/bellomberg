"""Real documented family adapters through tools, persistence, research and F17."""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import pytest
from functools import partial
from datetime import date, timedelta

from test_sector_operating_drivers import DAY, bundle_for, operating_records
from test_sector_valuation_integration import isolated_tools
from test_valuation_snapshot_persistence import db
from _prerequisiti_node import APP, richiedi_node_modules
from test_sector_bank_capital import bank_bundle,bank_records
from test_sector_rab_drivers import rab_bundle,rab_records
from test_sector_nav_drivers import nav_bundle,nav_records
from test_insurance_valuation import insurance_bundle,pc_records
from test_insurance_life import life_bundle,life_records
from test_real_estate_valuation import property_bundle,property_records
from test_property_development import developer_bundle,developer_records
from test_resources_valuation import resource_bundle,resource_records
from test_development_valuation import development_bundle,development_records
from test_sotp_documented import sotp_bundle,sotp_records

CASES=[(bundle_for,operating_records,'operating_fcff','capex_pct')]+[
    (partial(bank_bundle,profile=profile),bank_records,'bank_residual_income','capital.subsidiaries.0.required_statutory_capital')
    for profile in ('bank','balance_sheet_lender','mortgage_lender')]
CASES.append((rab_bundle,rab_records,'regulated_rab','cash_capex'))
CASES.extend((partial(nav_bundle,profile=p),partial(nav_records,profile=p),
    'digital_asset_nav' if p=='dat' else 'fund_nav','nav_target') for p in ('cef','investment_holding','dat'))
CASES.append((insurance_bundle,pc_records,'insurance_pc_distributable_equity','insurance.0.claims_paid'))
CASES.append((life_bundle,life_records,'insurance_life_distributable_equity','insurance.0.closing_policy_reserve'))
CASES.append((property_bundle,property_records,'property_nav','property_capex'))
CASES.append((developer_bundle,developer_records,'property_development_fcff','project_forecasts'))
CASES.append((resource_bundle,resource_records,'resources_asset_dcf','forecasts'))
CASES.append((development_bundle,development_records,'development_rnpv','probabilities'))
CASES.append((sotp_bundle,sotp_records,'mixed_business_sotp','central_cash_cost'))


@pytest.mark.parametrize('days_after', [1, 30])
@pytest.mark.parametrize('factory', [bundle_for, sotp_bundle])
def test_documented_value_survives_later_read_in_committee_database_f17_and_score(tmp_path, monkeypatch, db, days_after, factory):
    from bellomberg.valuation import dcf_engine, dcf_quality, sector_analysis
    from bellomberg.agents import specialist_scores
    from test_sector_valuation_api import endpoint

    source = factory()
    symbol = source['case']['ticker']
    good = dcf_engine.generate_valuation(symbol, prepared_bundle=source, output_dir=str(tmp_path))
    assert good['valuation_usability']['usable'], good['valuation_usability']
    fair_value = good['fair_value_base']
    db.save_valuation_thesis(symbol, valuation_payload=good, fair_value=fair_value, price=good['price'])

    class ReadingDate(date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(DAY) + timedelta(days=days_after)

    monkeypatch.setattr(dcf_quality, 'date', ReadingDate)
    snapshot = db.get_valuation_snapshot(good['snapshot_id'], generation_id=good['generation_id'])
    assert snapshot['fair_value_base'] == fair_value
    assert snapshot['valuation_decision']['as_of'] == DAY
    db.save_valuation_thesis(symbol, valuation_payload=good, fair_value=fair_value, price=good['price'])
    assert db.get_valuation_history(symbol)[0]['fair_value'] == fair_value
    block = sector_analysis.valuation_results_block({symbol: good})
    assert str(fair_value) in block and DAY in block
    assert json.loads(block.splitlines()[-1])['information_cutoff'] == DAY
    model = endpoint(tmp_path, db.get_latest_valuation_snapshots())['models'][0]
    assert model['fair_value'] == fair_value and model['valuation_usability']['usable']
    monkeypatch.setattr(specialist_scores.cl, 'carica_veicoli',
                        lambda: {'origine': 'synthetic', 'veicoli': {}, 'motivo': None})
    # 09/10 (Opus 5.5): MOS sul prezzo corrente; qui l'oggetto e' la lettura tardiva del valore
    from test_score_fondamentali_news_correzione import prezzo_corrente_uguale_al_modello
    prezzo_corrente_uguale_al_modello(monkeypatch)
    score = specialist_scores.fundamentals_score(
        {'positions': [{'ticker': symbol, 'peso_pct': 100}]}, valuations={symbol: good})
    assert score is not None and score['metrics']['n_valued'] == 1
    blocked = deepcopy(good)
    blocked['sanity']['severity'] = 'BLOCK'
    assert dcf_quality.normalize_valuation_payload(blocked)['fair_value_base'] is None


@pytest.mark.parametrize('factory,records_factory,method,missing',CASES)
def test_documented_research_persistence_score_f17_and_render(tmp_path,monkeypatch,db,factory,records_factory,method,missing):
    from bellomberg.valuation import dcf_engine,sector_analysis
    from bellomberg.core import current_facts
    from bellomberg.storage import memory_db
    from bellomberg.agents import specialist_scores
    from test_sector_valuation_api import endpoint
    source=factory(); symbol=source['case']['ticker']
    with db._conn() as conn:
        decision_id=conn.execute("INSERT INTO decisions(timestamp,action,ticker,status) VALUES (?,'RESEARCH',?,'PENDING')",(DAY,symbol)).lastrowid
    monkeypatch.setattr(memory_db,'SQLITE_PATH',db.db_path)
    providers={key:(lambda *a,source=value,**k:deepcopy(source)) for key,value in source['case']['sources'].items()}
    bundles={}; links={}
    research=current_facts.research_block(sector_bundles=bundles,providers=providers,as_of=DAY,decision_links=links)
    assert symbol in bundles,research
    prepared=sector_analysis.revise_sector_analysis(bundles[symbol],analysis_context=source['analysis_context'])
    good=dcf_engine.generate_valuation(symbol,prepared_bundle=prepared,output_dir=str(tmp_path))
    assert good['valuation_usability']['usable'],good['valuation_usability']
    db.save_valuation_thesis(symbol,valuation_payload=good,fair_value=good['fair_value_base'],price=good['price'])
    db.link_valuation_snapshot(good['snapshot_id'],generation_id=good['generation_id'],decision_id=decision_id)
    assert db.get_valuation_snapshot(good['snapshot_id'],generation_id=good['generation_id'])['fair_value_base']==good['fair_value_base']
    monkeypatch.setattr(specialist_scores,'REPORT_DIR',tmp_path)
    monkeypatch.setattr(specialist_scores.cl,'carica_veicoli',lambda: {'origine':'synthetic','veicoli':{},'motivo':None})
    # 09/10 (Opus 5.5): MOS sul prezzo corrente; qui l'oggetto e' la persistenza documentata
    from test_score_fondamentali_news_correzione import prezzo_corrente_uguale_al_modello
    prezzo_corrente_uguale_al_modello(monkeypatch)
    score=specialist_scores.fundamentals_score({'positions':[{'ticker':symbol,'peso_pct':100}]})
    assert score is not None and score['metrics']['n_valued']==1
    good_ui=endpoint(tmp_path,db.get_latest_valuation_snapshots())['models'][0]
    assert good_ui['valuation_usability']['usable'],good_ui
    records=[r for r in records_factory() if not(r['driver']==missing and r['scenario']=='base')]
    bad=dcf_engine.generate_valuation(symbol,prepared_bundle=factory(records),output_dir=str(tmp_path))
    db.save_valuation_thesis(symbol,valuation_payload=bad)
    bad_ui=endpoint(tmp_path,db.get_latest_valuation_snapshots())['models'][0]
    assert not bad_ui['valuation_usability']['usable'] and bad_ui['fair_value'] is None
    assert Path(good['path']).is_file()
    models=tmp_path/'documented-ui.json'
    models.write_text(json.dumps({'complete':good_ui,'incomplete':bad_ui,'method':method,'missing':missing}),encoding='utf8')
    if os.environ.get('BELLOMBERG_OFFLINE_TEST_SANDBOX'):
        pytest.skip('Backend/DB/score/F17 assertions completed; Node rendering is not run inside '
                    'the subprocess-blocking harness and requires the separate guarded Node proof. '
                    'Frozen fixture: ' + str(models))
    # Prerequisito dichiarato (ZR 05/10): senza `npm ci` in app/ il rosso dice cosa fare, non uno stack
    # di node; cartella app/ ancorata al repo, non alla cwd di pytest.
    node = richiedi_node_modules('react', 'react-dom', 'typescript')
    rendered=subprocess.run([node,'--test','tests/product/sector-valuation.cjs'],cwd=APP,
        env={**os.environ,'DOCUMENTED_VALUATION_FIXTURE':str(models)},capture_output=True,text=True)
    assert rendered.returncode==0,rendered.stdout+rendered.stderr


# Contratto ATTUALE (ZR 05/10, Z1): il test chat/build/cache/comitato per i 14 metodi documentati e' in
# archive/private/attic/tests_excel_archiviato_20261005/test_documented_consumers_legacy.py (dispatch get_valuation archiviato
# dal 1326312, censimento Z4). Qui ogni famiglia riceve il contratto dichiarato, senza toccare il ramo.
from _contratto_excel_archiviato import blinda_ramo_archiviato, file_in, verifica_archiviato


@pytest.mark.parametrize('factory,records_factory,method,missing',CASES)
def test_documented_families_meet_the_archived_contract(isolated_tools,monkeypatch,factory,records_factory,method,missing):
    tools=isolated_tools; bundle=factory(); symbol=bundle['case']['ticker']
    assert bundle['decision']['method_id']==method  # la famiglia e' davvero quella del caso
    prima=file_in(tools.directory)
    chiamate=blinda_ramo_archiviato(monkeypatch)
    risposta=tools.chat.dispatch('get_valuation',{'ticker':symbol},prepared_bundle=bundle)
    verifica_archiviato(risposta,chiamate,cartella=tools.directory,prima=prima)
    assert tools.memory.reads==0 and tools.memory.saved==[]
