"""F43(1) — `cash_source`: una chiave chiude DUE voci del frontend (25/08, decisione PM).

(a) `compute_nav_history` leggeva la cassa da `portfolio.json` con path
RELATIVO e `except Exception: pass`: col cwd sbagliato (script lanciato da
un'altra cartella, task pianificato) la cassa valeva 0 e `nav_total_eur`
diventava identico a `nav_eur` senza che nessun campo lo dicesse — descritto
dalla chat frontend leggendo `portfolio_analytics.py:577-583` (ponte F43 (1)).
Lo stesso zero zitto stava in `memory_db.get_portfolio_summary` (la cassa che
vedono agenti e UI in `/portfolio`); `agent_tools.tool_get_portfolio_state`
aveva il path relativo ma tornava `{"error"}`.
(b) «`cash_disponibile_eur: 0` significa due cose» valeva per `GET /portfolio`
(lo 0 zitto di sopra), NON per `POST /cash/movement`, che fin dal 12/08 fa 503
su file assente/illeggibile e risponde `null` (mai 0) su scrittura fallita:
li' `cash_source` e' coerenza di contratto, non una cura (review 27/08).

Cura: UN helper, `memory_db.leggi_cassa_portfolio()`, su `PORTFOLIO_JSON_PATH`
(ancorato alla radice del repo, non al cwd) che torna `cash_eur` +
`cash_source` («portfolio.json» SOLO quando la chiave e' stata letta e
CONVERTITA: `null`/booleani/stringhe non numeriche sono buchi, non zeri) +
`cash_source_note` (il motivo; nome distinto dal `cash_note` dei POST, che
significa «movimento registrato ma cassa non aggiornata»). I tre lettori lo
usano, gli SCRITTORI (trade, movimento) usano lo stesso percorso ancorato, i
payload portano le due chiavi (additive), il Capo e la vista compatta degli
agenti vedono la dichiarazione, lo snapshot NAV ufficiale NON persiste uno
zero non misurato, e la suite non legge mai il `portfolio.json` vero del PM.

Zero rete, DB su tmp, `portfolio.json` su tmp (mai quello vero).
"""
import json
import os
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from bellomberg.storage import memory_db
from bellomberg.storage.memory_db import MemoryDB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# catturato in COLLECTION, prima che la fixture autouse del conftest lo redirezioni
PERCORSO_VERO = memory_db.PORTFOLIO_JSON_PATH

def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    return MemoryDB(db_path=str(tmp_path / "data" / "consigliere_test.db"),
                    chroma_path=str(tmp_path / "data" / "chroma"))






# ------------------------------------------------------------------ helper

















# ------------------------------------------------ cablaggio: /portfolio





# ------------------------------------- cablaggio: analytics/nav_history

def _nav_history_offline(monkeypatch):
    import bellomberg.portfolio.portfolio_analytics as pa
    monkeypatch.setattr(pa, "NUMPY_OK", True)
    monkeypatch.setattr(pa, "YF_OK", True)
    monkeypatch.setattr(pa, "_trade_history", lambda: [
        {"ticker": "AAA.MI", "action": "BUY", "quantita": 10, "prezzo": 100.0,
         "valuta": "EUR", "data": "2026-08-03"}])
    idx = pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05"])
    monkeypatch.setattr(pa, "_download_prices_for_history",
                        lambda tickers, s, e, salta: pd.DataFrame({"AAA.MI": [100.0, 110.0, 120.0]}, index=idx))
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: pd.DataFrame())
    pa._ANALYTICS_CACHE.clear()
    return pa








# ------------------------------------------- cablaggio: tool degli agenti



# ---------------------------------- chi DECIDE vede la dichiarazione (review)

def test_la_vista_compatta_degli_agenti_porta_le_due_chiavi_prima_delle_posizioni():
    import bellomberg.agents.chat_tools as ct
    p = {"source": "memory_db", "n_positions": 0, "positions": [],
         "totale_valore_mercato_eur": 0.0, "cash_disponibile_eur": 0.0,
         "cash_source": None, "cash_source_note": "cash_state SQLite non inizializzata: cassa 0 NON misurata",
         "nav_total_eur": 0.0, "totale_pl_eur": 0.0, "timestamp": "t"}

    out = ct._compatta_portfolio_live(p)

    assert out["cash_source"] is None
    assert "NON misurata" in out["cash_source_note"]
    s = json.dumps(out, ensure_ascii=False)
    assert s.find('"cash_source"') < s.find('"positions"')


def _capo_offline(monkeypatch):
    from bellomberg.agents import capo
    from bellomberg.valuation import cef_lookthrough
    from bellomberg.core import current_facts
    from bellomberg.portfolio import signal_engine
    chiamate = []
    memo = SimpleNamespace(content=[SimpleNamespace(type="text", text="## SINTESI\n" + "ok " * 200)],
                           stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=100, output_tokens=100))

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return memo

    class _Messages:
        def stream(self, **kw):
            chiamate.append(kw)
            return _Stream()

    class _Anthropic:
        def __init__(self, **kw):
            self.messages = _Messages()
    monkeypatch.setattr(capo, "OpenRouterClient", _Anthropic)
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "(fatti finti)")
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(cef_lookthrough, "capo_block", lambda: "")
    monkeypatch.setattr(signal_engine, "scan_portfolio", lambda **k: {"signals": []})
    return capo, chiamate


def _book(cash_source, nota):
    return {"source": "memory_db", "fx_incomplete": None, "stale_positions": None,
            "n_positions": 0, "positions": [], "totale_valore_mercato_eur": 100000.0,
            "cash_disponibile_eur": 0.0, "cash_source": cash_source, "cash_source_note": nota,
            "nav_total_eur": 100000.0, "totale_pl_eur": 0.0, "timestamp": "2026-08-27T10:00:00"}


def test_il_capo_legge_che_la_cassa_e_un_buco_non_uno_zero(monkeypatch):
    """Review 27/08: `cash = ... or 0` scriveva «Cash liquido EUR 0 … QUESTO E'
    MOLTO CASH FERMO» senza la nota. Chi alloca soldi veri deve leggerla."""
    capo, chiamate = _capo_offline(monkeypatch)

    capo.run_capo(SimpleNamespace(data={"macro": {2: "report macro finto"}}),
                  portfolio_data=_book(None, "cash_state SQLite non inizializzata: cassa 0 NON misurata"))

    um = chiamate[0]["messages"][0]["content"]
    assert "CASSA NON MISURATA" in um and "cash_state SQLite non inizializzata" in um, um[:2000]


def test_il_capo_non_grida_se_la_cassa_e_letta(monkeypatch):
    capo, chiamate = _capo_offline(monkeypatch)

    capo.run_capo(SimpleNamespace(data={"macro": {2: "report macro finto"}}),
                  portfolio_data=_book("sqlite:cash_state", None))

    assert "CASSA NON MISURATA" not in chiamate[0]["messages"][0]["content"]


# ------------------------------- lo snapshot NAV ufficiale (review, twr_engine)

def _snapshot_db(db, riassunto):
    class _DB:
        db_path = db.db_path

        def get_portfolio_summary(self):
            return riassunto
    return _DB()


def _righe_snapshot(db):
    with sqlite3.connect(db.db_path) as c:
        return c.execute("SELECT date, nav_total_eur, cash_eur FROM nav_snapshots").fetchall()


def test_lo_snapshot_nav_non_persiste_uno_zero_non_misurato(db):
    from bellomberg.portfolio import twr_engine
    out = twr_engine.record_nav_snapshot(_snapshot_db(db, {
        "nav_total_eur": 100000.0, "totale_valore_mercato_eur": 100000.0,
        "cash_disponibile_eur": 0.0, "cash_source": None,
        "cash_source_note": "cash_state SQLite non inizializzata: cassa 0 NON misurata"}))

    assert out["ok"] is False and "NON misurata" in out["reason"], out
    assert _righe_snapshot(db) == []


def test_lo_snapshot_nav_con_cassa_letta_scrive_come_prima(db):
    from bellomberg.portfolio import twr_engine
    out = twr_engine.record_nav_snapshot(_snapshot_db(db, {
        "nav_total_eur": 100500.0, "totale_valore_mercato_eur": 100000.0,
        "cash_disponibile_eur": 500.0, "cash_source": "sqlite:cash_state", "cash_source_note": None}))

    assert out["ok"] is True, out
    righe = _righe_snapshot(db)
    assert len(righe) == 1 and righe[0][1] == 100500.0 and righe[0][2] == 500.0
