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
    # --- DB e cartelle runtime in tmp
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(tmp_path / "cablaggio.db"))
    monkeypatch.setattr(memory_db.MemoryDB, "get_portfolio_summary",
                        lambda self: {"n_positions": 0, "positions": []})
    monkeypatch.setattr(memory_db.MemoryDB, "extract_and_save_decisions",
                        lambda self, memo_id, memo, usage_out=None: [])
    for d in ("research_notes", "models", "report"):
        (tmp_path / d).mkdir()
    monkeypatch.setattr(cm, "RESEARCH_NOTES_DIR", str(tmp_path / "research_notes"))
    monkeypatch.setattr(cm, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(cm, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr("bellomberg.core.paths.DATA_DIR", tmp_path / "runtime-data")
    monkeypatch.setattr("bellomberg.core.paths.REPORT_DIR", tmp_path / "report")
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
        p = tmp_path / "report" / "weekly_finto.pdf"
        p.write_bytes(b"%PDF-1.4 finto")
        return str(p)
    _modulo_finto(monkeypatch, "bellomberg.reporting.pdf_institutional", build_institutional_memo=_pdf_finto)
    _modulo_finto(monkeypatch, "bellomberg.reporting.charts_quant", build_quant_appendix_v2=lambda **k: None)
    _modulo_finto(monkeypatch, "bellomberg.agents.score_history",
                  record_completed_run=lambda *a, **k: {"reason": "finto"})
    monkeypatch.setattr(red_team, "run_red_team", lambda bb, **k: "")
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
                                             "input_tokens": 10, "output_tokens": 10, "api_calls": 1}
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
                           respinto=_RESPINTO)


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
    assert name in _allegati(msg) and "weekly_finto.pdf" in _allegati(msg), _allegati(msg)
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


def test_run_policy_binds_common_preparer_and_emails_new_workbook(run_offline, monkeypatch, tmp_path):
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
    class PreparedDesk(_DeskFinto):
        name = "fundamentals"
        def run(self, round_n):
            if round_n == 1:
                payload = self.bb.valuation_preparer(_bundle())
                assert payload["valuation_usability"]["usable"], payload.get("error")
                thesis = self.bb.memory_db.save_valuation_thesis("SYNTH-EXT", valuation_payload=payload)
                payload["_thesis_saved"] = {"thesis_id": thesis}
                self.bb.record_valuation("SYNTH-EXT", payload, self.name)
            self.bb.write(self.name, round_n, "Synthetic prepared desk report " + "x" * 200)
    monkeypatch.setattr(cm, "SPECIALIST_ORDER", [PreparedDesk, _DeskQuant])
    cm.run_multi_agent()
    bb = run_offline.catturato["bb"]
    assert bb.data["_valuation_preparation"]["status"] == "enabled"
    assert str(journals[0][0]).startswith(str(folder)) and journals[0][1] == "2.50"
    assert stages and "automatic" in run_offline.catturato["sizing_context"]
    payload = bb.valuation_results["SYNTH-EXT"]
    assert payload["preparation"]["proposal"]["approval_status"] == "automatic_non_approved"
    workbook = Path(payload["path"])
    message = run_offline.inviati[0]
    attached = [part for part in message.walk() if part.get_filename() == workbook.name]
    assert len(attached) == 1 and attached[0].get_payload(decode=True) == workbook.read_bytes()
    receipt = json.loads(next(Path(cm.RESEARCH_NOTES_DIR).glob("*_valuations.json")).read_text(encoding="utf-8"))
    assert receipt["valuations"][0]["email_included"] is True


def test_run_invalid_policy_is_visible_without_enabling_preparation(run_offline, tmp_path):
    folder = tmp_path / "runtime-data"
    folder.mkdir()
    (folder / "valuation_automation.json").write_text('{"enabled":true}', encoding="utf-8")
    cm.run_multi_agent()
    bb = run_offline.catturato["bb"]
    assert bb.valuation_preparer is None and bb.data["_valuation_preparation"]["status"] == "error"
    assert "valuation authorization" in run_offline.catturato["sizing_context"]
