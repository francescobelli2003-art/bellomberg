# -*- coding: utf-8 -*-
"""Una decisione ESEGUITA di recente non torna in tavola come se non lo fosse (audit 10/09).

Il caso (audit 10/09): il PM esegue un ADD proposto da un memo e chiede una valutazione;
una run successiva ripropone un ADD sullo
stesso titolo. Nessuno aveva detto al comitato che la decisione era gia' eseguita:
  - il blocco «PM feedback (BINDING)» dei desk rendeva "#N ADD TICKER | PM: eseguito..."
    SENZA stato ne' importo eseguito, e i desk l'hanno letto come un ordine da eseguire;
  - la riga ESEGUITE del Capo portava l'importo PROPOSTO, mai quello eseguito;
  - il trade aveva linked_decision_id NULL (il blotter non lo invia): "esegue decisione
    #N" non compariva;
  - il validator controlla solo le riproposte PENDING, e non conosce il budget di stress
    quando VINCOLA (capacita' dispiegabile 0) sui nomi NUOVI.

Simboli, importi e date INVENTATI; DB su tmp; nessun LLM.
"""
from datetime import datetime, timedelta

import pytest

from bellomberg.storage.memory_db import MemoryDB
from bellomberg.agents.action_validator import build_validator_block


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    from bellomberg.agents import scorekeeper
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist", lambda *_a, **_k: "")
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo", lambda *_a, **_k: "")
    from bellomberg.agents import reflection
    monkeypatch.setattr(reflection, "get_latest_lesson_block", lambda *_a, **_k: "")
    return MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _semina(db, ore_fa_decisione=7, ore_fa_trade=6, quantita=2.0, prezzo=1234.5, proposto=4200.0,
            azione="ADD", verso_trade="BUY", link=None):
    ora = datetime.now()
    memo_id = db.save_memo("# memo di prova", title="prova")
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO decisions (memo_id, timestamp, action, ticker, eur_amount, timing, confidence, "
            "status, pm_feedback, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (memo_id, _iso(ora - timedelta(hours=ore_fa_decisione)), azione, "ALFA.DE", proposto,
             "a mercato", "MEDIA", "EXECUTED", "ordine fatto, ricalcolare la valutazione",
             _iso(ora - timedelta(hours=ore_fa_decisione - 0.5))))
        dec_id = cur.lastrowid
        conn.execute(
            "INSERT INTO trade_history (ticker, action, quantita, prezzo, valuta, data, pm_rationale, "
            "linked_decision_id) VALUES (?,?,?,?,?,?,?,?)",
            ("ALFA.DE", verso_trade, quantita, prezzo, "EUR", _iso(ora - timedelta(hours=ore_fa_trade)),
             "eseguito come consigliato", link))
    return dec_id


# ------------------------------------------------ 1. il legame trade->decisione si inferisce

def test_il_capo_legge_quanto_e_stato_eseguito_davvero(db):
    dec_id = _semina(db)
    ctx = db.build_capo_memory_context(max_chars=99999)
    assert "ESEGUITA" in ctx and "#%d" % dec_id in ctx, ctx
    assert "2,469 EUR" in ctx, ctx            # 2 x 1234,5 = 2.469
    assert "59%" in ctx, ctx                   # dei 4.200 proposti
    assert "inferito" in ctx.lower(), ctx
    assert "esegue decisione #%d" % dec_id in ctx, ctx


def test_il_legame_esplicito_vince_e_non_si_dichiara_inferito(db):
    dec_id = _semina(db)
    with db._conn() as conn:
        conn.execute("UPDATE trade_history SET linked_decision_id=?", (dec_id,))
    ctx = db.build_capo_memory_context(max_chars=99999)
    assert "esegue decisione #%d" % dec_id in ctx
    assert "legame inferito" not in ctx


def test_un_trade_di_verso_opposto_o_troppo_lontano_non_si_lega(db):
    # TRIM dopo un ADD: verso opposto -> nessun legame
    dec_id = _semina(db, verso_trade="TRIM")
    ctx = db.build_capo_memory_context(max_chars=99999)
    assert "esegue decisione #%d" % dec_id not in ctx
    # stesso verso ma 10 giorni dopo: fuori finestra
    dec2 = _semina(db, ore_fa_decisione=24 * 12, ore_fa_trade=24 * 1)
    ctx = db.build_capo_memory_context(max_chars=99999)
    assert "esegue decisione #%d" % dec2 not in ctx


def test_la_regola_del_capo_nomina_le_eseguite_di_recente(db):
    _semina(db)
    ctx = db.build_capo_memory_context(max_chars=99999)
    assert "ESEGUITA di recente" in ctx, ctx
    assert "NOVITA'" in ctx, ctx


# ------------------------------------------------ 2. i desk vedono lo stato nel blocco BINDING

def test_il_blocco_binding_dei_desk_porta_stato_e_importo_eseguito(db):
    dec_id = _semina(db)
    righe, _ = db._pm_binding_parts()
    riga = [r for r in righe if r.startswith("#%d " % dec_id)][0]
    assert "ESEGUITA il" in riga, riga
    assert "59%" in riga and "2,469 EUR" in riga, riga
    assert "| PM:" in riga, riga


def test_una_decisione_skipped_nel_blocco_binding_dice_skipped(db):
    memo_id = db.save_memo("# memo", title="m")
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO decisions (memo_id, timestamp, action, ticker, eur_amount, status, pm_feedback) "
            "VALUES (?,?,?,?,?,?,?)", (memo_id, _iso(datetime.now()), "BUY", "BETA", 4800.0, "SKIPPED",
                                       "rinviare in attesa dei conti trimestrali"))
    righe, _ = db._pm_binding_parts()
    riga = [r for r in righe if r.startswith("#%d " % cur.lastrowid)][0]
    assert "SKIPPED" in riga and "| PM:" in riga, riga


# ------------------------------------------------ 3. il validator flagga la riproposta post-esecuzione

MEMO_ADD = """
## ACTION TABLE

| Action | Ticker | EUR | Timing | Confidence |
|---|---|---|---|---|
| ADD | ALFA.DE | 2.500 | a mercato entro il 18/09 | MEDIA |
"""

MEMO_ADD_NOVITA = MEMO_ADD.replace("a mercato entro il 18/09", "NOVITA': guidance alzata il 12/09; a mercato")

SIZING_LIBERO = {
    "positions": [{"ticker": "ALFA.DE", "remaining_capacity_eur": 5150.0, "verdict": "spazio +5,150€ entro il limite"}],
    "summary": {"invested_capital_eur": 250000.0, "params": {"single_base_pct": 10.0},
                "deployable_from_cash_eur": 9000.0,
                "stress_var_budget": {"binding": False, "gfc_status": "OK"}},
}


def _righe(blocco):
    return [r for r in blocco.split("\n") if r.startswith("- ")]


def test_add_su_ticker_eseguito_da_poco_e_flaggato_come_riproposta(db):
    dec_id = _semina(db)
    blocco = build_validator_block(MEMO_ADD, SIZING_LIBERO, db)
    righe = [r for r in _righe(blocco) if "RIPROPOSTA POST-ESECUZIONE" in r]
    assert len(righe) == 1, blocco
    assert "#%d" % dec_id in righe[0] and "ALFA.DE" in righe[0], righe[0]
    assert "NOVITA'" in righe[0], righe[0]


def test_con_novita_dichiarata_in_riga_non_e_una_riproposta_cieca(db):
    _semina(db)
    blocco = build_validator_block(MEMO_ADD_NOVITA, SIZING_LIBERO, db)
    assert not [r for r in _righe(blocco) if "RIPROPOSTA POST-ESECUZIONE" in r], blocco


def test_senza_trade_recente_nessun_flag(db):
    _semina(db, ore_fa_decisione=24 * 12, ore_fa_trade=24 * 11)
    blocco = build_validator_block(MEMO_ADD, SIZING_LIBERO, db)
    assert not [r for r in _righe(blocco) if "RIPROPOSTA POST-ESECUZIONE" in r], blocco


# ------------------------------------------------ 4. il budget di stress che VINCOLA si vede anche sui nomi nuovi

SIZING_VINCOLATO = {
    "positions": [{"ticker": "ALFA.DE", "remaining_capacity_eur": 0.0, "verdict": "spazio +5,150€ entro il limite"}],
    "summary": {"invested_capital_eur": 250000.0, "params": {"single_base_pct": 10.0},
                "deployable_from_cash_eur": 0.0, "total_room_existing_eur": 0.0,
                "stress_var_budget": {"binding": True, "gfc_status": "SFORATO", "additional_capacity_eur": 0.0,
                                      "gfc_replay_nav_pct": -28.0, "budget_gfc_replay_nav_pct": -25.0}},
}

MEMO_BUY_NUOVO = """
## ACTION TABLE

| Action | Ticker | EUR | Timing | Confidence |
|---|---|---|---|---|
| BUY | NUOVO.MI | 7.000 | 2.000 a mercato, 5.000 dopo il FOMC | MEDIA |
| ADD | ALFA.DE | 2.500 | a mercato entro il 18/09 | MEDIA |
"""


def test_buy_su_nome_nuovo_con_budget_vincolante_e_flaggato():
    blocco = build_validator_block(MEMO_BUY_NUOVO, SIZING_VINCOLATO, None)
    righe = [r for r in _righe(blocco) if "NUOVO.MI" in r]
    assert righe, blocco
    assert any("VINCOLA" in r and "deroga NON dichiarata" in r for r in righe), righe


def test_la_riga_sull_esistente_non_si_contraddice_piu():
    blocco = build_validator_block(MEMO_BUY_NUOVO, SIZING_VINCOLATO, None)
    riga = [r for r in _righe(blocco) if "ALFA.DE" in r][0]
    assert "~0€ — spazio" not in riga, riga
    assert "VINCOLA" in riga and "+5,150€" in riga and "deroga NON dichiarata" in riga, riga


def test_con_sopra_policy_dichiarata_il_budget_vincolante_e_una_nota():
    memo = MEMO_BUY_NUOVO.replace("2.000 a mercato, 5.000 dopo il FOMC", "SOPRA POLICY: budget di stress sforato, size minima")
    blocco = build_validator_block(memo, SIZING_VINCOLATO, None)
    riga = [r for r in _righe(blocco) if "NUOVO.MI" in r][0]
    assert "DEROGA DICHIARATA" in riga, riga


# ------------------------------------------------ 5. lo stato accompagna le parole del PM ovunque

def test_le_parole_dirette_del_capo_portano_lo_stato(db):
    dec_id = _semina(db)
    ctx = db.build_capo_memory_context(max_chars=99999)
    riga = [l for l in ctx.split("\n") if l.startswith("PAROLE DIRETTE DEL PM")][0]
    assert ("#%d ADD ALFA.DE [ESEGUITA il" % dec_id) in riga, riga


def test_il_piede_del_validator_porta_lo_stato_della_decisione_citata(db):
    dec_id = _semina(db)
    blocco = build_validator_block(MEMO_ADD_NOVITA, SIZING_LIBERO, db)
    riga = [r for r in _righe(blocco) if "feedback DIRETTO del PM" in r and "ALFA.DE" in r][0]
    assert ("#%d ADD (memo #1, ESEGUITA)" % dec_id) in riga, riga
