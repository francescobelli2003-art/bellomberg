"""Pacchetto "verita' dei numeri" Lotto C (23/07, audit/20, riprogettato dopo
review avversariale): esclusione HARD dei BUY/ADD su modello in sanity BLOCK
(#204b residuo, detect/apply separati) + residui muti news (#4).
I test riproducono la configurazione REALE del motore (lezione misure-circolari):
- i modelli BLOCK vivono nel sidecar `VAL_X_FLAGGED.payload.json` (il canonico
  viene rimosso da dcf_engine): la prima stesura guardava solo il canonico e
  l'esclusione era morta per costruzione (review F2, misurato 6/6 nel report/);
- le decisioni NON esistono quando il memo si finalizza: detect prima, apply
  DOPO extract_and_save_decisions (review F1);
- una riga BUY su posizione gia' in book viene salvata come ADD (review F3).
"""
import json
import os
import sqlite3

import pytest

from bellomberg.storage.memory_db import MemoryDB

# importi inventati
MEMO_BLOCK = """# Memo
## ACTION TABLE
| Action | Ticker | EUR | Timing | Confidence |
|---|---|---|---|---|
| BUY | GAMMA.MI | 5000 | now | HIGH |
| TRIM | GAMMA.MI | 2000 | now | MED |
| BUY | YYY | 3000 | now | MED |
| ACCUMULATE | GAMMA.MI | 1111 | now | LOW |
"""


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    path = str(tmp_path / "data" / "consigliere_test.db")
    return MemoryDB(db_path=path, chroma_path=str(tmp_path / "data" / "chroma"))


def _sidecar(report_dir, ticker, severity, flagged, ts="2026-07-23T10:00:00"):
    """Riproduce la convenzione VERA di dcf_engine: BLOCK -> _FLAGGED, mai
    entrambi i sidecar."""
    os.makedirs(report_dir, exist_ok=True)
    base = "VAL_" + ticker.replace(".", "_")
    name = base + ("_FLAGGED.payload.json" if flagged else ".payload.json")
    with open(os.path.join(report_dir, name), "w", encoding="utf-8") as f:
        json.dump({"ticker": ticker, "_timestamp": ts,
                   "sanity": {"severity": severity, "headline": "x"}}, f)


def _insert_decision(db, memo_id, action, ticker, status="PENDING"):
    with sqlite3.connect(db.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO decisions (memo_id, timestamp, action, ticker, status) "
            "VALUES (?, '2026-07-23T10:00:00', ?, ?, ?)", (memo_id, action, ticker, status))
        return cur.lastrowid


def _decision(db, did):
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone())


def test_flusso_reale_detect_poi_estrazione_poi_apply(db, tmp_path):
    """Ordine di produzione: detect sul memo (decisioni NON ancora in DB) →
    estrazione salva la BUY come ADD (guardia book-aware) → apply chiude.
    Sidecar _FLAGGED (convenzione vera dei BLOCK) + ticker col punto."""
    from bellomberg.agents.action_validator import detect_sanity_exclusions, apply_sanity_exclusions
    report = str(tmp_path / "report")
    _sidecar(report, "GAMMA.MI", "BLOCK", flagged=True)
    _sidecar(report, "YYY", "OK", flagged=False)

    block, pairs = detect_sanity_exclusions(MEMO_BLOCK, report_dir=report)
    assert "BUY GAMMA.MI" in block and "sanity BLOCK" in block
    assert "giudizio sanity del 2026-07-23" in block      # vintage dichiarato (F5)
    assert "ACCUMULATE" in block and "VALUTARE A MANO" in block  # bypass dichiarato (F4)
    assert "YYY" not in block
    assert pairs == [("BUY", "GAMMA.MI")]
    # a questo punto in DB non c'e' NULLA (come nella run vera)

    # estrazione: la guardia book-aware normalizza BUY->ADD (review F3)
    d_add = _insert_decision(db, 7, "ADD", "GAMMA.MI")
    d_trim = _insert_decision(db, 7, "TRIM", "GAMMA.MI")
    d_ok = _insert_decision(db, 7, "BUY", "YYY")

    n = apply_sanity_exclusions(db, 7, pairs)
    assert n == 1
    row = _decision(db, d_add)
    assert row["status"] == "SKIPPED"
    assert "AUTO-ESCLUSA" in (row["outcome_notes"] or "")
    assert row["closed_at"] is not None
    assert _decision(db, d_trim)["status"] == "PENDING"   # TRIM resta al PM
    assert _decision(db, d_ok)["status"] == "PENDING"


def test_sidecar_assente_niente_esclusione(db, tmp_path):
    """MAI escludere per assenza di dato: senza sidecar detect torna vuoto."""
    from bellomberg.agents.action_validator import detect_sanity_exclusions
    block, pairs = detect_sanity_exclusions(
        MEMO_BLOCK, report_dir=str(tmp_path / "report_vuota"))
    assert block == "" and pairs == []


def test_canonico_ok_non_esclude_e_block_canonico_raro_si(tmp_path):
    """Il lookup copre ENTRAMBI i nomi: canonico OK -> niente; e se mai esistesse
    un BLOCK nel canonico (difesa in profondita'), viene beccato comunque."""
    from bellomberg.agents.action_validator import _canonical_sanity
    report = str(tmp_path / "report")
    _sidecar(report, "AAA", "OK", flagged=False)
    _sidecar(report, "BBB", "BLOCK", flagged=False)   # caso teorico
    _sidecar(report, "CCC", "BLOCK", flagged=True)    # caso reale
    assert _canonical_sanity("AAA", report) == ("OK", "2026-07-23")
    assert _canonical_sanity("BBB", report) == ("BLOCK", "2026-07-23")
    assert _canonical_sanity("CCC", report) == ("BLOCK", "2026-07-23")
    assert _canonical_sanity("ZZZ", report) == (None, None)


def test_apply_non_tocca_storia_ne_altri_memo(db, tmp_path):
    """Solo le PENDING del memo corrente: EXECUTED storica e righe di altri
    memo restano intatte (storia vera)."""
    from bellomberg.agents.action_validator import apply_sanity_exclusions
    d_old = _insert_decision(db, 3, "BUY", "GAMMA.MI", status="EXECUTED")
    d_other = _insert_decision(db, 4, "ADD", "GAMMA.MI")
    d_now = _insert_decision(db, 9, "ADD", "GAMMA.MI")
    n = apply_sanity_exclusions(db, 9, [("BUY", "GAMMA.MI")])
    assert n == 1
    assert _decision(db, d_old)["status"] == "EXECUTED"
    assert _decision(db, d_other)["status"] == "PENDING"
    assert _decision(db, d_now)["status"] == "SKIPPED"


def test_portfolio_news_ticker_zero_news_non_sparisce(monkeypatch):
    """Residuo muto #4: un nome a 0 news resta nel dict (prima spariva e
    'assente' == 'zero risultati')."""
    import bellomberg.market_data.news_aggregator as na

    class _FakeDB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "ALFA"}, {"ticker": "GAMMA.MI"}]}

    monkeypatch.setattr(na, "MemoryDB", lambda: _FakeDB())
    monkeypatch.setattr(na, "search_news_for_ticker",
                        lambda t, days=2, max_per_source=3: (
                            [{"title": "n1"}] if t == "ALFA" else []))
    out = na.search_portfolio_news(days=2, max_per_ticker=3)
    assert out["ALFA"] == [{"title": "n1"}]
    assert out["GAMMA.MI"] == []          # presente e vuoto = quiete MISURATA
