"""Round-trip decisioni e tesi di valutazione su DB temporaneo (collaudi
15-21/07). L'estrazione strutturata dell'ACTION TABLE (chiamata Sonnet) e'
stubbata con errore: si esercita il fallback regex DICHIARATO dal contratto
+ la persistenza vera. price_updater stubbato (il book-guard lo importa).
"""
import json
import sys
import types

import pytest

from bellomberg.agents import action_table_extract
from bellomberg.storage.memory_db import MemoryDB

MEMO = """# Memo di test

## ACTION TABLE
| Action | Ticker | EUR | Timing | Confidence |
|--------|--------|-----|--------|------------|
| BUY | GAMMA.MI | 10k | questa settimana | ALTA |
| TRIM | ALFA | 5.000 | entro venerdi | MEDIA |
"""


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    monkeypatch.setattr(action_table_extract, "extract_rows_structured",
                        lambda memo, usage_out=None: {"error": "test offline"})
    pu = types.ModuleType("price_updater")
    pu.get_fx_to_eur = lambda cur: 1.0
    pu.get_fx_sources = lambda: {}
    monkeypatch.setitem(sys.modules, 'bellomberg.cli.price_updater', pu)
    # 26/07 sera-5 (Opus 5, voce I-3 passo 0): senza questi due stub il test
    # AVVELENAVA la produzione. build_specialist_memory_context (usato sotto)
    # chiama memory_db.py:1912 -> scorekeeper.compute_scorecard(db=None), che
    # apriva il DB VERO e riscriveva data/scorekeeper_snapshot.json degradato
    # (i 32 fetch prezzi fallivano contro lo stub `pu`, che non ha data_ticker).
    # La guardia autouse di conftest ora lo INTERCETTA (ProduzioneToccata);
    # qui si stubba la fonte, perche' il track record non c'entra col veto.
    from bellomberg.agents import scorekeeper
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist",
                        lambda *_a, **_k: "")
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo",
                        lambda *_a, **_k: "")
    return MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))


def test_round_trip_decisioni_e_canale_veti(db):
    # come nel flusso reale: prima il memo (decisions.memo_id e' FK su memos)
    memo_id = db.save_memo(MEMO, title="Memo di test")
    assert memo_id is not None

    ids = db.extract_and_save_decisions(memo_id=memo_id, memo_markdown=MEMO)
    assert len(ids) == 2

    by_ticker = {r["ticker"]: r for r in db.get_recent_decisions(10)}
    assert by_ticker["GAMMA.MI"]["action"] == "BUY"
    assert by_ticker["GAMMA.MI"]["eur_amount"] == 10000.0   # "10k" convertito
    assert by_ticker["ALFA"]["action"] == "TRIM"
    assert by_ticker["ALFA"]["eur_amount"] == 5000.0       # "5.000" europeo
    assert all(r["status"] == "PENDING" for r in by_ticker.values())

    # veto: SKIPPED con feedback -> PRIMO nel canale dedicato dei veti
    # (21/07: i veti non decadono spinti fuori dalle note di esecuzione)
    veto_id = by_ticker["ALFA"]["id"]
    assert db.update_decision(veto_id, status="SKIPPED",
                              pm_feedback="niente covered call, basta proporle")
    fb = db.get_decisions_with_pm_feedback(5)
    assert fb and fb[0]["id"] == veto_id
    assert fb[0]["status"] == "SKIPPED"


def test_flag_veto_opzione_a(db):
    """F10 opzione A (23/07): veto ETERNO — motivo obbligatorio, SKIPPED+flag,
    SEMPRE nel canale della run fuori dalla finestra n, revoca con storia."""
    memo_id = db.save_memo(MEMO, title="Memo di test")
    ids = db.extract_and_save_decisions(memo_id=memo_id, memo_markdown=MEMO)
    by_ticker = {r["ticker"]: r for r in db.get_recent_decisions(10)}
    alfa_id = by_ticker["ALFA"]["id"]
    gamma_id = by_ticker["GAMMA.MI"]["id"]

    # motivo vuoto = rifiutato (il bottone in UI resta spento, l'API fa 400)
    assert db.set_decision_veto(alfa_id, "   ") is None

    row = db.set_decision_veto(alfa_id, "niente covered call su ALFA, nemmeno varianti")
    assert row and row["veto"] == 1 and row["status"] == "SKIPPED"
    assert row["veto_at"] and row["veto_revoked_at"] is None

    # il veto sta nel canale PRIMA e FUORI dalla finestra n: anche con n=1 e
    # una nota di esecuzione piu' recente sull'altra decisione, il veto c'e'
    assert db.update_decision(gamma_id, status="EXECUTED", pm_feedback="comprati 10k")
    fb = db.get_decisions_with_pm_feedback(1)
    assert fb[0]["id"] == alfa_id and fb[0]["veto"] == 1

    # revoca: flag spento, motivo e data restano in storia, esce dal canale veti
    rev = db.revoke_decision_veto(alfa_id)
    assert rev and rev["veto"] == 0 and rev["veto_revoked_at"]
    assert rev["veto_reason"] and rev["veto_at"]
    fb2 = db.get_decisions_with_pm_feedback(5)
    assert all(not r.get("veto") for r in fb2)
    # doppia revoca = None (nessun veto attivo)
    assert db.revoke_decision_veto(alfa_id) is None

    # review 23/07 (MEDIA-6): il veto su una decisione GIA' eseguita non riscrive
    # la storia — status resta EXECUTED, il divieto vale comunque
    row2 = db.set_decision_veto(gamma_id, "mai piu' add su GAMMA a questi prezzi")
    assert row2 and row2["veto"] == 1 and row2["status"] == "EXECUTED"

    # il blocco BINDING coi veti sta IN TESTA al contesto specialisti (ALTA-1:
    # prima stava in coda e il cap del caller lo troncava)
    ctx = db.build_specialist_memory_context("fundamentals")
    assert "⛔ VETO ATTIVO" in ctx
    assert ctx.index("BINDING") < ctx.index("Your last 3 reports") if "Your last 3 reports" in ctx else True


def test_canale_veti_fallback_colonna_mancante(db, capsys):
    """ALTA-2: su un DB SENZA la colonna veto (pre-migrazione 2) il canale va in
    legacy DICHIARATO; un OperationalError diverso invece PROPAGA."""
    import sqlite3 as _sq
    memo_id = db.save_memo(MEMO, title="Memo di test")
    db.extract_and_save_decisions(memo_id=memo_id, memo_markdown=MEMO)
    rows = db.get_recent_decisions(10)
    db.update_decision(rows[0]["id"], status="SKIPPED", pm_feedback="basta proporla")

    # simula DB pre-migrazione: via le colonne veto (SQLite >= 3.35)
    with _sq.connect(db.db_path) as conn:
        for col in ("veto", "veto_reason", "veto_at", "veto_revoked_at"):
            conn.execute(f"ALTER TABLE decisions DROP COLUMN {col}")

    fb = db.get_decisions_with_pm_feedback(5)          # niente eccezione
    assert fb and fb[0]["pm_feedback"] == "basta proporla"
    assert "modalita' legacy" in capsys.readouterr().out  # fallback DICHIARATO, non zitto


def test_round_trip_tesi_valutazione(db):
    tid = db.save_valuation_thesis(
        "gamma.mi", variant_view="ROE 12% sostenibile post-rerating",
        growth_path=[0.12, 0.11, 0.10], price=8.0, fair_value=13.41,
        terminal_growth=0.02, engine="bank", memo_id=7,
        sanity_severity="OK", sanity_headline=None,
        profile_key="banks-diversified")
    assert tid is not None

    hist = db.get_valuation_history("GAMMA.MI", n=5)
    assert len(hist) == 1
    t = hist[0]
    assert t["ticker"] == "GAMMA.MI"                    # upper-case in salvataggio
    assert t["fair_value"] == 13.41
    assert json.loads(t["growth_path"]) == [0.12, 0.11, 0.10]
    assert t["engine"] == "bank"
    assert t["sanity_severity"] == "OK"
    assert t["profile_key"] == "banks-diversified"
