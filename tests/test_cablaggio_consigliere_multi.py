# -*- coding: utf-8 -*-
"""Il CABLAGGIO di `run_multi_agent` (dossier 50 par. 3, punti 2-4): tre fix dell'11/09 erano
«implementati» e «testati» sull'helper, ma nessuna batteria percorreva l'orchestratore.

  1. F13: `corpo_valutazioni(bb.valuation_results, dcf_files)` arriva DAVVERO al mittente
     come `body_extra`, e l'Excel generato dalla run e' allegato;
  2. F14: la sonda dei modelli scrive in `_tool_health.ko` e il KO arriva al Capo
     nell'HEALTH-CHECK del prompt (via `sizing_context`);
  3. F31: Polymarket con ogni endpoint fallito (count 0 + fetch_warnings) finisce in `ko`,
     non in `ok`.

Qui gira `run_multi_agent()` VERO, da cima a fondo, senza rete e senza DB vivo: DB SQLite
in tmp (nome diverso da consigliere.db: il conftest ha una rete di sicurezza sul nome),
desk FINTI al posto del roster (scrivono in blackboard come i veri, uno «genera» l'Excel),
moduli pesanti (rischio, Monte Carlo, sizing, PDF, reflection, validator, ...) sostituiti
da finti in sys.modules, Capo finto che CATTURA cio' che riceve, SMTP finto che conserva
il messaggio. Cio' che NON gira e' dichiarato: i desk veri, il Capo vero, il red team.

Lezione «testare l'helper non e' testare il cablaggio»: ognuna delle tre prove cade se si
cancella la riga che COLLEGA (misurato il 12/09 con tre mutazioni: v. rapporto del lotto).
"""
import sys
import types
from types import SimpleNamespace

import pytest

import bellomberg.reporting.email_sender as es
from bellomberg.agents import consigliere_multi as cm
from bellomberg.agents import agent_tools, red_team, scorekeeper
from bellomberg.core import llm_client
from bellomberg.storage import memory_db


# --------------------------------------------------------------- i finti

class _SMTP:
    inviati = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        pass

    def send_message(self, msg):
        _SMTP.inviati.append(msg)


class _DeskFinto:
    """Un desk che NON chiama LLM: scrive un report per round in blackboard come i veri."""
    name = "quant"
    xlsx_path = None    # il fixture lo punta nel REPORT_DIR di tmp (solo per fundamentals)

    def __init__(self, blackboard):
        self.bb = blackboard

    def run(self, round_n):
        self.run_result_status = "complete"
        if self.name == "fundamentals" and round_n == 1:
            # Real adapter/workbook/storage, synthetic inputs; only the LLM desk is replaced.
            from pathlib import Path
            from test_sector_operating_drivers import bundle_for
            from bellomberg.valuation.dcf_engine import generate_valuation
            payload = generate_valuation("ALFA", prepared_bundle=bundle_for(symbol="ALFA"),
                                         output_dir=str(Path(self.xlsx_path).parent))
            thesis = self.bb.memory_db.save_valuation_thesis("ALFA", valuation_payload=payload)
            payload["_thesis_saved"] = {"thesis_id": thesis}
            self.bb.record_valuation("ALFA", payload, self.name)
        self.bb.write(self.name, round_n, "REPORT %s R%d (finto) " % (self.name, round_n) + "x" * 200)


class _DeskFundamentals(_DeskFinto):
    name = "fundamentals"


class _DeskQuant(_DeskFinto):
    name = "quant"


def _modulo_finto(monkeypatch, nome, **attrs):
    m = types.ModuleType(nome)
    for k, v in attrs.items():
        setattr(m, k, v)
    monkeypatch.setitem(sys.modules, nome, m)
    return m


@pytest.fixture
def run_offline(monkeypatch, tmp_path):
    """Rende `run_multi_agent()` eseguibile offline. Ritorna cio' che i finti hanno catturato."""
    # This fixture exercises the archived workbook contract explicitly. New
    # research tests opt back into the ordinary current contract before running.
    native_weekly_contract = cm._weekly_contract
    monkeypatch.setattr(cm, '_weekly_contract', lambda **kwargs:
                        native_weekly_contract(analysis_mode=kwargs.get('analysis_mode')))
    # --- DB e cartelle runtime in tmp
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: self.__dict__.update(
        chroma_client=None, col_memos=None, col_decisions=None, col_feedback=None))
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(tmp_path / "cablaggio.db"))
    memory_db.MemoryDB()  # Existing, deliberately empty test book before the paid-run prerequisite.
    monkeypatch.setattr("bellomberg.agents.filing_context.SQLITE_PATH", str(tmp_path / "cablaggio.db"))
    def _archivio_filing_assente():
        raise FileNotFoundError("archivio filing di prova assente")   # mai l'archivio di produzione
    monkeypatch.setattr(cm, "_filing_service", _archivio_filing_assente)
    monkeypatch.setattr(memory_db.MemoryDB, "get_portfolio_summary",
                        lambda self: {"n_positions": 0, "positions": []})
    monkeypatch.setattr(memory_db.MemoryDB, "extract_and_save_decisions",
                        lambda self, memo_id, memo, usage_out=None, **kwargs: [])
    for d in ("research_notes", "models", "report"):
        (tmp_path / d).mkdir()
    monkeypatch.setattr(cm, "RESEARCH_NOTES_DIR", str(tmp_path / "research_notes"))
    monkeypatch.setattr(cm, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(cm, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr("bellomberg.core.paths.DATA_DIR", tmp_path / "runtime-data")
    monkeypatch.setattr("bellomberg.core.paths.REPORT_DIR", tmp_path / "report")
    # charts_institutional.DIR nasce da core.paths.REPORT_DIR all'IMPORT: se il modulo era gia'
    # importato (suite intera) puntava alla cartella report dell'albero e i test che rimontano
    # il renderer PDF vero ci disegnavano i grafici (tripwire spia scritture, ZR 05/10).
    import bellomberg.reporting.charts_institutional as _charts_inst
    monkeypatch.setattr(_charts_inst, "DIR", str(tmp_path / "report" / "inst_charts"))
    monkeypatch.setenv("CONSIGLIERE_PARALLEL", "1")
    # --- tool locali e sonde esterne
    monkeypatch.setattr(cm, "tool_get_macro_dashboard", lambda: {"indicators": {}})
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda q, max_results=10: {"results": [], "count": 0,
                                                   "fetch_warnings": ["TLS handshake failed (finto)"]})
    monkeypatch.setattr(agent_tools, "tool_get_hyperliquid_intel", lambda: {})
    _modulo_finto(monkeypatch, "bellomberg.portfolio.portfolio_analytics",
                  compute_var_contribution=lambda: {"error": "finto"},
                  compute_nav_history=lambda: {"error": "finto"})
    _modulo_finto(monkeypatch, "bellomberg.portfolio.advanced_metrics",
                  portfolio_metrics=lambda: {"error": "finto"}, reconcile_betas=lambda: {})
    _modulo_finto(monkeypatch, "bellomberg.market_data.news_aggregator", get_feed=lambda limit=5: [])
    _modulo_finto(monkeypatch, "bellomberg.portfolio.twr_engine",
                  build_recon_note=lambda nav, snaps: None, _load_snapshots=lambda: [],
                  RECON_TOLERANCE_PCT=1.0)
    _modulo_finto(monkeypatch, "bellomberg.core.freshness",
                  check_and_update=lambda cur: {"checked": 0, "stale": []},
                  format_for_capo=lambda r: "", format_for_memo=lambda r: "")
    monkeypatch.setattr(scorekeeper, "compute_scorecard",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("scorekeeper finto")))
    # --- moduli pesanti del post-Capo
    _modulo_finto(monkeypatch, "bellomberg.portfolio.portfolio_risk",
                  compute_portfolio_risk=lambda: {"error": "finto"})
    _modulo_finto(monkeypatch, "bellomberg.portfolio.portfolio_montecarlo",
                  run_monte_carlo=lambda **k: {"error": "finto"})
    # Quant snapshot moves these existing appendix acquisitions before the mocked renderer.
    # Fake only producer boundaries; keep the dispatcher/checkpoints/replay logic real.
    quant_acquisitions = []
    def _quant_input(name):
        def acquire():
            quant_acquisitions.append(name)
            return {"error": "finto", "status": "error"}
        return acquire
    _modulo_finto(monkeypatch, "bellomberg.portfolio.portfolio_garch",
                  compute_portfolio_garch=_quant_input("garch"))
    _modulo_finto(monkeypatch, "bellomberg.portfolio.portfolio_factors",
                  compute_portfolio_factors=_quant_input("factors"))
    _modulo_finto(monkeypatch, "bellomberg.market_data.macro_rates",
                  get_us_curve=_quant_input("rates_us"), get_bund_curve=_quant_input("rates_de"),
                  get_jgb_curve=_quant_input("rates_jp"), get_eu_hy_credit=_quant_input("rates_credit"))
    _modulo_finto(monkeypatch, "bellomberg.portfolio.sizing_engine",
                  compute_sizing=lambda *a, **k: {"error": "finto"}, format_for_capo=lambda s: "")
    _modulo_finto(monkeypatch, "bellomberg.agents.specialist_scores",
                  format_scoreboard=lambda c: "", collect_scoreboard=lambda c: [])
    _modulo_finto(monkeypatch, "bellomberg.reporting.memo_linter", build_linter_block=lambda memo, p: "")
    _modulo_finto(monkeypatch, "bellomberg.agents.action_validator",
                  build_validator_block=lambda *a, **k: "",
                  detect_sanity_exclusions=lambda memo: ("", []),
                  apply_sanity_exclusions=lambda *a, **k: 0)
    _modulo_finto(monkeypatch, "bellomberg.agents.reflection",
                  generate_lesson=lambda memo, memo_id=None, usage_out=None: None)

    def _pdf_finto(**kw):
        from pathlib import Path
        p = Path(kw.get("output_path") or tmp_path / "report" / "weekly_finto.pdf")
        p.write_bytes(b"%PDF-1.4 finto")
        return str(p)
    _modulo_finto(monkeypatch, "bellomberg.reporting.pdf_institutional", build_institutional_memo=_pdf_finto)
    _modulo_finto(monkeypatch, "bellomberg.reporting.charts_quant", build_quant_appendix_v2=lambda **k: None)
    _modulo_finto(monkeypatch, "bellomberg.agents.score_history",
                  record_completed_run=lambda *a, **k: {"reason": "finto"})
    monkeypatch.setattr(red_team, "run_red_team", lambda bb, **k:
                        bb.write("_red_team", 1, "Synthetic complete challenge") or "Synthetic complete challenge")
    # --- sonda modelli: uno slug respinto, gli altri ok
    sondati = []
    _RESPINTO = "claude-opus-5"   # CAPO_MODEL/CONSIGLIERE_MODEL di prova del conftest

    def _sonda(slugs, client=None, **k):
        sondati.extend(slugs)
        return {s: ({"ok": False, "motivo": "HTTP 403 gate (finto)", "durata_s": 0.0}
                    if s == _RESPINTO else {"ok": True, "motivo": None, "durata_s": 0.0})
                for s in dict.fromkeys(slugs)}
    monkeypatch.setattr(llm_client, "sonda_modelli", _sonda)
    # --- Capo finto: cattura blackboard e contesto, non chiama nessuno
    catturato = {}

    def _capo(bb, portfolio_data=None, memory_db=None, sizing_context=None, scoring_context=None):
        catturato["bb"] = bb
        catturato["sizing_context"] = sizing_context or ""
        return "MEMO FINTO " + "m" * 100, {"model": "m/finto", "in": 10, "out": 10,
                                             "input_tokens": 10, "output_tokens": 10, "api_calls": 1,
                                             "complete": True, "stop_reason": "end_turn"}
    monkeypatch.setattr(cm, "run_capo", _capo)
    # --- desk finti al posto del roster
    monkeypatch.setattr(_DeskFinto, "xlsx_path", str(tmp_path / "report" / "VAL_ALFA.xlsx"))
    monkeypatch.setattr(cm, "SPECIALIST_ORDER", [_DeskFundamentals, _DeskQuant])
    # --- SMTP finto
    monkeypatch.setattr(es, "EMAIL_FROM", "a@b.c")
    monkeypatch.setattr(es, "EMAIL_PASSWORD", "x")
    monkeypatch.setattr(es, "EMAIL_TO", "d@e.f")
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", _SMTP)
    _SMTP.inviati.clear()
    return SimpleNamespace(catturato=catturato, sondati=sondati, inviati=_SMTP.inviati,
                           respinto=_RESPINTO, native_weekly_contract=native_weekly_contract,
                           quant_acquisitions=quant_acquisitions)


def _html(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8")
    raise AssertionError("parte html assente")


def _allegati(msg):
    return [part.get_filename() for part in msg.walk() if part.get_filename()]


# ----------------------------------------------------------------- 1. email

def test_il_corpo_valutazioni_e_l_excel_della_run_arrivano_al_mittente(run_offline):
    cm.run_multi_agent()
    assert len(run_offline.inviati) == 1, "l'email della run deve partire UNA volta"
    msg = run_offline.inviati[0]
    html = _html(msg)
    assert "Valutazioni richieste dal comitato" in html
    from pathlib import Path
    import json
    payload = run_offline.catturato["bb"].valuation_results["ALFA"]
    name = Path(payload["path"]).name
    assert "ALFA" in html and str(payload["fair_value_base"]) in html, html
    assert name in html and "Nessun modello Excel allegato" not in html, html
    assert name in _allegati(msg) and any(filename.endswith('_memo.pdf') for filename in _allegati(msg)), _allegati(msg)
    receipt = json.loads(next(Path(cm.RESEARCH_NOTES_DIR).glob("*_valuations.json")).read_text(encoding="utf-8"))
    assert receipt["email_status"] == "sent"
    assert receipt["valuations"][0]["email_included"] is True
    assert receipt["valuations"][0]["generation_id"] == payload["generation_id"]
    assert len(receipt["attempts"]) == 1


def test_smtp_ko_non_registra_excel_incluso_nella_mail(run_offline, monkeypatch):
    from pathlib import Path
    import json
    def disconnected(*args, **kwargs):
        raise OSError("synthetic SMTP outage")
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", disconnected)
    cm.run_multi_agent()
    receipt = json.loads(next(Path(cm.RESEARCH_NOTES_DIR).glob("*_valuations.json")).read_text(encoding="utf-8"))
    assert receipt["email_status"] == "not_sent"
    assert receipt["mime_attachments"], "MIME preparato, ma non spedito"
    assert receipt["valuations"][0]["email_included"] is False


# -------------------------------------------------------- 2. sonda modelli

def test_la_sonda_modelli_scrive_in_tool_health_e_il_ko_arriva_al_capo(run_offline):
    cm.run_multi_agent()
    assert run_offline.respinto in run_offline.sondati, run_offline.sondati   # gli slug del .env di prova
    ko = run_offline.catturato["bb"].data["_tool_health"]["ko"]
    riga = "modello " + run_offline.respinto + " -> HTTP 403 gate (finto)"
    assert riga in ko, ko
    ctx = run_offline.catturato["sizing_context"]
    assert "HEALTH-CHECK PRE-RUN: TOOL NON DISPONIBILI" in ctx, ctx[:400]
    assert riga in ctx, ctx


# ------------------------------------------------------------ 3. Polymarket

def test_polymarket_con_tutti_gli_endpoint_falliti_finisce_in_ko(run_offline):
    cm.run_multi_agent()
    th = run_offline.catturato["bb"].data["_tool_health"]
    assert "polymarket -> TLS handshake failed (finto)" in th["ko"], th["ko"]
    assert not [r for r in th["ok"] if r.startswith("polymarket")], th["ok"]
    assert "polymarket -> TLS handshake failed (finto)" in run_offline.catturato["sizing_context"]


def test_run_without_policy_records_preparation_disabled(run_offline):
    cm.run_multi_agent()
    bb = run_offline.catturato["bb"]
    assert bb.valuation_preparer is None
    assert bb.data["_valuation_preparation"] == {"status": "disabled", "reason": "configuration_absent"}
    assert "configuration_absent" in run_offline.catturato["sizing_context"]


@pytest.mark.parametrize("desk_requests", [True, False])
def test_run_policy_binds_common_preparer_and_emails_new_workbook(run_offline, monkeypatch, tmp_path, desk_requests):
    import json
    from pathlib import Path
    from bellomberg.valuation import preparation_ai
    from test_input_preparation import _bundle, _documents, _operating_plan
    from test_preparation_runtime import _policy
    folder = tmp_path / "runtime-data"
    folder.mkdir()
    (folder / "valuation_automation.json").write_text(json.dumps(_policy()), encoding="utf-8")
    plan, stages, journals = _operating_plan(), [], []
    def factory(journal, *, authorized_usd):
        journals.append((journal, authorized_usd))
        def propose(dossier, contract):
            spec = contract["preparation_stage"]
            stages.append(spec)
            source = plan["model"] if spec["scope"] == "model" else plan["scenarios"][spec["scope"]]
            return {"drivers": {name: source[name] for name in spec["drivers"]},
                    "rationale": "Synthetic economic source analysis"}
        return propose
    monkeypatch.setattr(preparation_ai, "configured_proposer", factory)
    monkeypatch.setattr("bellomberg.valuation.preparation_sources.collect_preparation_evidence",
        lambda *a, **k: {"status": "ready", "documents": _documents(), "issues": []})
    monkeypatch.setattr(memory_db.MemoryDB, "get_portfolio_summary", lambda self:
        {"n_positions": 2, "positions": [{"ticker": "SYNTH-EXT"}, {"ticker": "SYNTH-EXT"}]})
    monkeypatch.setattr(cm, "_try_correlation_matrix", lambda positions: None)
    monkeypatch.setattr("bellomberg.agents.chat_tools.REPORT_DIR", tmp_path / "report")
    _acquired = _bundle()
    from bellomberg.valuation import sector_analysis
    original_acquire = sector_analysis.prepare_sector_analysis
    def acquire(ticker, **kwargs):
        if (kwargs.get("user_context") or {}).get("method_records"):
            return original_acquire(ticker, **kwargs)
        assert ticker == "SYNTH-EXT"
        return _acquired
    monkeypatch.setattr(sector_analysis, "prepare_sector_analysis", acquire)
    from bellomberg.storage.valuation_versions import ensure_schema
    with memory_db.MemoryDB()._conn() as conn:
        ensure_schema(conn)
    seen_by_red_team, seen_by_r2 = [], []
    monkeypatch.setattr(red_team, "run_red_team", lambda bb, **k:
        (seen_by_red_team.append(dict(bb.valuation_results)),
         bb.write("_red_team", 1, "Synthetic complete challenge"))[1] or "Synthetic complete challenge")
    class PreparedDesk(_DeskFinto):
        name = "fundamentals"
        def run(self, round_n):
            self.run_result_status = "complete"
            if round_n == 1 and desk_requests:
                payload = self.bb.valuation_preparer(_bundle())
                assert payload["valuation_usability"]["usable"], payload.get("error")
                thesis = self.bb.memory_db.save_valuation_thesis("SYNTH-EXT", valuation_payload=payload)
                payload["_thesis_saved"] = {"thesis_id": thesis}
                self.bb.record_valuation("SYNTH-EXT", payload, self.name)
            if round_n == 2:
                from bellomberg.agents.specialists.base import Specialist
                desk = Specialist(self.bb, client=object())
                seen_by_r2.append(desk._build_round_context(2))
                if not desk_requests:
                    # A bare follow-up must reuse the same run, not pay/acquire again.
                    # ZR 05/10: la copertura del comitato (ramo legacy) riceve oggi il contratto
                    # ARCHIVIATO (get_valuation -> excel_archived): il seguito riusa quell'esito,
                    # senza una seconda chiamata (corpo storico in archive/private/attic/tests_excel_archiviato_20261005/).
                    follow_up = desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH-EXT"})
                    assert follow_up["data"]["code"] == "excel_archived"
                    assert follow_up["data"]["reused_in_run"] is True
                    assert follow_up["data"] == {**self.bb.valuation_results["SYNTH-EXT"], "reused_in_run": True}
            self.bb.write(self.name, round_n, "Synthetic prepared desk report " + "x" * 200)
    monkeypatch.setattr(cm, "SPECIALIST_ORDER", [PreparedDesk, _DeskQuant])
    if not desk_requests:
        # ZR 05/10 (contratto archiviato, commit 1326312): la copertura del comitato chiede
        # get_valuation UNA volta, riceve excel_archived, non paga alcuna preparazione, e la
        # consegna si ferma dichiarando il workbook non consegnabile: nessuna email.
        from bellomberg.agents import chat_tools
        from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
        native_dispatch, valuation_calls = chat_tools.dispatch, []
        def counting_dispatch(name, *args, **kwargs):
            if name == "get_valuation":
                valuation_calls.append(kwargs.get("caller"))
            return native_dispatch(name, *args, **kwargs)
        monkeypatch.setattr(chat_tools, "dispatch", counting_dispatch)
        with pytest.raises(WeeklyRunBlocked, match="Workbook richiesto non consegnabile: SYNTH-EXT: Generazione Excel archiviata"):
            cm.run_multi_agent()
        assert valuation_calls == ["committee-orchestrator"]
        assert stages == [] and run_offline.inviati == []
        assert run_offline.catturato["bb"].valuation_results["SYNTH-EXT"]["code"] == "excel_archived"
        assert not list((tmp_path / "report").rglob("*.xlsx"))
        return
    cm.run_multi_agent()
    bb = run_offline.catturato["bb"]
    assert bb.data["_valuation_preparation"]["status"] == "enabled"
    assert str(journals[0][0]).startswith(str(folder)) and journals[0][1] == "2.50"
    assert stages and "automatic" in run_offline.catturato["sizing_context"]
    payload = bb.valuation_results["SYNTH-EXT"]
    assert payload["preparation"]["proposal"]["approval_status"] == "automatic_non_approved"
    assert seen_by_red_team[0]["SYNTH-EXT"]["generation_id"] == payload["generation_id"]
    assert payload["generation_id"] in seen_by_r2[0]
    assert len(stages) == 13, "Duplicate holdings or a bare R2 call must not repeat preparation"
    workbook = Path(payload["path"])
    message = run_offline.inviati[0]
    attached = [part for part in message.walk() if part.get_filename() == workbook.name]
    assert len(attached) == 1 and attached[0].get_payload(decode=True) == workbook.read_bytes()
    receipt = json.loads(next(Path(cm.RESEARCH_NOTES_DIR).glob("*_valuations.json")).read_text(encoding="utf-8"))
    assert receipt["valuations"][0]["email_included"] is True
    assert sum(a["specialist"] == "committee-orchestrator" for a in receipt["attempts"]) == (not desk_requests)


def test_run_invalid_policy_is_visible_without_enabling_preparation(run_offline, tmp_path):
    folder = tmp_path / "runtime-data"
    folder.mkdir()
    (folder / "valuation_automation.json").write_text('{"enabled":true}', encoding="utf-8")
    cm.run_multi_agent()
    bb = run_offline.catturato["bb"]
    assert bb.valuation_preparer is None and bb.data["_valuation_preparation"]["status"] == "error"
    assert "valuation authorization" in run_offline.catturato["sizing_context"]


def test_run_sintetica_contesto_filing_tre_fonti_e_un_titolo_lento(run_offline, monkeypatch, tmp_path):
    """Criterio di successo della fase D: la run aggiorna i profili scaduti prima del passo
    filing, entro la scadenza assoluta; il titolo lento è dichiarato NON AGGIORNATO."""
    import sqlite3
    import threading
    import time
    from bellomberg.market_data.filing_service import FilingService
    from bellomberg.storage.filing_store import FilingStore, ensure_schema
    db = tmp_path / "cablaggio.db"
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    base = {"lingua": "en", "perimetro": "consolidato",
            "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
            "sezioni": {"rischi": {"inizio": "Risk Factors", "fine": "Properties"}}}
    profili = {
        "NOVA.DE": {**base, "tipo": "trimestrale", "cik": "0009990001", "emittente_id": "CIK:0009990001",
                    "varianti": [{"tipo": "trimestrale", "forme_sec": ["10-Q"]}]},
        "KORE.MI": {**base, "tipo": "annuale", "cik": "0009990002", "emittente_id": "CIK:0009990002",
                    "varianti": [{"tipo": "annuale", "forme_sec": ["20-F"]}, {"tipo": "semestrale", "forme_sec": ["6-K"]}]},
        "ACME.PA": {**base, "tipo": "annuale", "lei": "999900ACMEPA0000001", "emittente_id": "LEI:999900ACMEPA0000001"},
        "LENTO.MI": {**base, "tipo": "annuale", "cik": "0009990004", "emittente_id": "CIK:0009990004"},
    }
    for t, p in profili.items():
        store.set_profile(t, {**p, "ticker": t}, interval_hours=24)

    def cit(t, s="rischi"):
        return {"url": "https://www.sec.gov/Archives/x.htm", "sha256": "b" * 64, "sezione": s,
                "inizio": 0, "fine": len(t), "pagine_fisiche": [], "testo": t}

    # Cantiere zero rossi 05/10 (TIMING): prima LENTO dormiva 3 s e gli altri tre dovevano finire
    # entro 1 s REALE dall'avvio della run (rosso possibile sotto carico). Ora LENTO si ferma su un
    # evento liberato alla fine (in corso per costruzione) e wait() del pre-run e' sostituita:
    # registra il timeout che il codice calcola (scadenza assoluta, <= FILING_ATTESA_MAX_S), lascia
    # finire gli altri tre (rete larga 30 s) e guarda i futuri senza attendere oltre.
    from concurrent.futures import wait as wait_vera
    from bellomberg.market_data import filing_prerun
    lento_libero = threading.Event()
    attese = []

    def wait_registrata(fs, timeout=None):
        attese.append(timeout)
        fine = time.monotonic() + 30
        while sum(not f.done() for f in fs) > 1 and time.monotonic() < fine:
            time.sleep(0.01)
        return wait_vera(fs, timeout=0)
    monkeypatch.setattr(filing_prerun, "wait", wait_registrata)

    def pipeline(profilo, archivio):
        if profilo["ticker"] == "LENTO.MI":
            lento_libero.wait(30)
        meta = lambda d: {"metadati": {"periodo_fine": d}}
        return {"stato": "ok", "motivi": [], "variante": profilo.get("tipo"),
                "coppia": {"prima": meta("2025-06-30"), "dopo": meta("2026-06-30")},
                "numeri": {"stato": "ok", "voci": [{"voce": "ricavi", "delta_pct": 8.5}]},
                "confronto_corrente": {"stato": "ok", "cambiamenti": [
                    {"tipo": "aggiunto", "dopo": cit(f"{profilo['ticker']} new risk " * 20)}]}}

    monkeypatch.setattr(cm, "_filing_service", lambda: FilingService(
        store, tmp_path / "arch", pipeline=pipeline, indexer=lambda *a: {"status": "skipped"}))
    monkeypatch.setattr(cm, "FILING_ATTESA_MAX_S", 1.0)
    monkeypatch.setattr(memory_db.MemoryDB, "get_portfolio_summary", lambda self: {
        "n_positions": 4, "positions": [{"ticker": t} for t in profili]})
    # Il portafoglio sintetico (solo ticker) farebbe partire get_valuation per ogni titolo e la
    # matrice di correlazione (fonti esterne): passi estranei al filing, stubbati qui.
    monkeypatch.setattr(cm, "_ensure_portfolio_valuations", lambda *a, **k: None)
    monkeypatch.setattr(cm, "_try_correlation_matrix", lambda *a, **k: None)
    try:
        cm.run_multi_agent()
        ctx = run_offline.catturato["bb"].data["_filing_context"]
        assert len(attese) == 1 and 0 <= attese[0] <= 1.0, attese   # scadenza assoluta dall'avvio
        for t in ("NOVA.DE", "KORE.MI", "ACME.PA"):
            riga = next(l for l in ctx.splitlines() if l.startswith(t + " · "))
            assert "aggiornato" in riga and "NON AGGIORNATO" not in riga
        assert "NOVA.DE · SEC 10-Q CIK 0009990001" in ctx
        assert "ACME.PA · ESEF LEI 999900ACMEPA0000001" in ctx
        assert "numeri ricavi +8,5%" in ctx and "[C1-dopo]" in ctx
        lento = next(l for l in ctx.splitlines() if l.startswith("LENTO.MI · "))
        assert "NON AGGIORNATO: aggiornamento oltre 1 s (in corso)" in lento
        assert ctx.splitlines()[-1].startswith("TRONCAMENTI:")
    finally:
        lento_libero.set()
        # I thread del pool non sono daemon: si attende LENTO.MI per non lasciarlo al test successivo.
        for th in threading.enumerate():
            if th.name.startswith("filing-prerun"):
                th.join(timeout=10)
    # il run lento si conclude dopo la run, nell'archivio (nessun secondo run)
    assert [r["status"] for r in store.list_runs("LENTO.MI")] == ["ok"]
    print("\n--- _filing_context (run sintetica) ---\n" + ctx)


def test_servizio_filing_rotto_non_blocca_la_run(run_offline, monkeypatch):
    """Errore nel costruire il servizio: la run prosegue, la freschezza viene dall'archivio."""
    def _rotto():
        raise RuntimeError("archivio filing assente (finto)")
    monkeypatch.setattr(cm, "_filing_service", _rotto)
    monkeypatch.setattr(memory_db.MemoryDB, "get_portfolio_summary", lambda self: {
        "n_positions": 1, "positions": [{"ticker": "NOVA.DE"}]})
    monkeypatch.setattr(cm, "_ensure_portfolio_valuations", lambda *a, **k: None)  # v. test precedente
    monkeypatch.setattr(cm, "_try_correlation_matrix", lambda *a, **k: None)
    cm.run_multi_agent()
    ctx = run_offline.catturato["bb"].data["_filing_context"]
    assert ctx.splitlines()[1].startswith("NOVA.DE · ")


def test_fine_run_chiude_il_pool_del_pre_run(run_offline, monkeypatch):
    """Revisione finale M8: a fine run i lavori pre-run non partiti si annullano."""
    from bellomberg.market_data import filing_prerun
    eventi = []

    class _Finto:
        def __init__(self, service, tickers, **kw):
            eventi.append("init")

        def avvia(self):
            eventi.append("avvia")

        def esiti(self):
            return {}

        def chiudi(self):
            eventi.append("chiudi")
    monkeypatch.setattr(filing_prerun, "AggiornamentoPreRun", _Finto)
    monkeypatch.setattr(cm, "_filing_service", lambda: object())
    monkeypatch.setattr(memory_db.MemoryDB, "get_portfolio_summary", lambda self: {
        "n_positions": 1, "positions": [{"ticker": "NOVA.DE"}]})
    monkeypatch.setattr(cm, "_ensure_portfolio_valuations", lambda *a, **k: None)
    monkeypatch.setattr(cm, "_try_correlation_matrix", lambda *a, **k: None)
    cm.run_multi_agent()
    assert eventi == ["init", "avvia", "chiudi"]


# ------------------------------------------ 4. freschezza del funding Hyperliquid (09/10)

def test_freschezza_funding_usa_l_istante_dichiarato_dal_tool(run_offline, monkeypatch):
    """Fix 09/10 (Opus 5.5): il funding dei major entra nel controllo di freschezza con la
    data di acquisizione dichiarata dal tool, non piu' «data osservazione assente». Il
    valore al tasso base 10,95% passa com'e': la freschezza la decide la data."""
    visti = []
    # due percorsi (storico e release-freshness/1, scelto dal contratto della run): entrambi spiati
    _modulo_finto(monkeypatch, "bellomberg.core.freshness",
                  check_and_update=lambda cur: visti.append(cur) or {"checked": len(cur), "stale": []},
                  check_release_freshness=lambda cur, as_of: visti.append(cur) or {"checked": len(cur), "stale": []},
                  project_macro_observation=lambda ind: dict(ind),
                  format_for_capo=lambda r: "", format_for_memo=lambda r: "")
    monkeypatch.setattr(agent_tools, "tool_get_hyperliquid_intel", lambda: {
        "fetched_at_utc": "2031-03-17T11:00:00+00:00",
        "top_10_perps_by_oi": [{"asset": "BTC", "funding_annualized_pct": 10.95, "oi_usd_m": 900},
                               {"asset": "ZQALT", "funding_annualized_pct": 99.0, "oi_usd_m": 5}]})
    cm.run_multi_agent()
    assert visti, "il controllo di freschezza non e' stato chiamato"
    riga = visti[0]["hl_funding:BTC"]
    assert riga["value"] == 10.95 and riga["obs_date"] == "2031-03-17", riga
    assert riga["retrieved_at"] == "2031-03-17T11:00:00+00:00"
    assert "hl_funding:ZQALT" not in visti[0]
