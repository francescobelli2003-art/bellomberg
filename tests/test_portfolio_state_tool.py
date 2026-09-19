"""B5-bis (02/09, pubblicazione, decisione PM): `agent_tools.tool_get_portfolio_state`
leggeva le liste etf/azioni del portfolio.json (schema di maggio) e le dava al
comitato come «tutte le posizioni» — via vecchia contro la regola «portafoglio =
SOLO il DB». Ora: posizioni dal DB (senza prezzi: per valori e pesi c'e'
get_portfolio_live) + cassa da portfolio.json con la fonte DICHIARATA.
"""
import json
import re

import pytest


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    db_path = tmp_path / "consigliere.db"
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(db_path))
    monkeypatch.setattr(memory_db, "CHROMA_PATH", str(tmp_path / "chroma"))
    pj = tmp_path / "portfolio.json"
    monkeypatch.setattr(memory_db, "PORTFOLIO_JSON_PATH", str(pj))
    db = memory_db.MemoryDB(db_path=str(db_path), chroma_path=str(tmp_path / "chroma"))
    return db, pj


def _json_vecchio_schema(pj, cassa=8):
    pj.write_text(json.dumps({
        "cash_disponibile_eur": cassa,
        "etf": [{"ticker": "VECCHIO.MI", "peso_percentuale": 50}],
        "azioni": [{"ticker": "STANTIO", "tesi": "tesi di maggio"}],
    }), encoding="utf-8")


def test_le_posizioni_vengono_dal_db_non_dal_json(ambiente):
    from bellomberg.agents import agent_tools
    db, pj = ambiente
    _json_vecchio_schema(pj)
    db.log_trade("ABCD.MI", "BUY", 10, 12.5, "EUR", data="2026-01-15T12:00:00")
    with db._conn() as c:
        c.execute("UPDATE positions SET tesi=?, temi_monitoraggio=? WHERE ticker='ABCD.MI'",
                  ("tesi di prova", json.dumps(["rame", "tariffe"])))
    out = agent_tools.tool_get_portfolio_state()
    assert out["source"].startswith("SQLite DB"), out
    assert [p["ticker"] for p in out["positions"]] == ["ABCD.MI"]
    p = out["positions"][0]
    assert p["quantita"] == 10 and p["prezzo_medio"] == 12.5 and p["valuta"] == "EUR"
    assert p["tesi"] == "tesi di prova" and p["temi_monitoraggio"] == ["rame", "tariffe"]
    assert out["n_positions"] == 1
    testo = json.dumps(out)
    assert "VECCHIO.MI" not in testo and "STANTIO" not in testo and "etf" not in out and "azioni" not in out


def test_la_cassa_viene_da_sqlite_con_la_fonte(ambiente):
    from bellomberg.agents import agent_tools
    db, pj = ambiente
    _json_vecchio_schema(pj, cassa=999)
    db.apply_cash_movement("DEPOSIT", 8)
    out = agent_tools.tool_get_portfolio_state()
    assert out["cash_disponibile_eur"] == 8 and out["cash_source"] == "sqlite:cash_state"
    assert out["cash_source_note"] is None


def test_a_book_vuoto_dichiara_e_non_inventa(ambiente):
    from bellomberg.agents import agent_tools
    db, pj = ambiente
    pj.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    db.apply_cash_movement("DEPOSIT", 0.01)
    db.apply_cash_movement("WITHDRAWAL", 0.01)
    out = agent_tools.tool_get_portfolio_state()
    assert out["n_positions"] == 0 and out["positions"] == []
    assert out["cash_disponibile_eur"] == 0 and out["cash_source"] == "sqlite:cash_state"
    assert "hint" in out and "F16" in out["hint"] and "Inserimento operazioni" in out["hint"]


def test_senza_stato_sqlite_la_cassa_e_un_buco_dichiarato(ambiente):
    from bellomberg.agents import agent_tools
    out = agent_tools.tool_get_portfolio_state()      # pj non scritto
    assert out["cash_source"] is None and "non inizializzata" in out["cash_source_note"]
    assert out["n_positions"] == 0


def test_senza_prezzi_lo_dice_e_rimanda_al_tool_live(ambiente):
    from bellomberg.agents import agent_tools
    _, pj = ambiente
    pj.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    out = agent_tools.tool_get_portfolio_state()
    assert "get_portfolio_live" in out["note"]


def test_db_assente_errore_dichiarato_e_nessun_file_creato(tmp_path, monkeypatch):
    """F2 della review: un lettore puro non CREA mai un DB vuoto su path sbagliato
    (lezione «0 posizioni = path sbagliato»): mode=ro, errore col percorso."""
    from bellomberg.agents import agent_tools
    from bellomberg.storage import memory_db
    fantasma = tmp_path / "non_esiste" / "consigliere.db"
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(fantasma))
    monkeypatch.setattr(memory_db, "PORTFOLIO_JSON_PATH", str(tmp_path / "portfolio.json"))
    out = agent_tools.tool_get_portfolio_state()
    assert "error" in out and str(fantasma) in out["error"], out
    assert not fantasma.exists() and not fantasma.parent.exists()


def test_dichiarazioni_in_testa_posizioni_per_ultime(ambiente):
    """F3 della review: il tetto dei tool result (12.000 char) taglia in CODA; sul book
    vero il payload misurava 15.178 char e cadevano proprio cassa e nota."""
    from bellomberg.agents import agent_tools
    db, pj = ambiente
    pj.write_text(json.dumps({"cash_disponibile_eur": 5}), encoding="utf-8")
    db.log_trade("ABCD.MI", "BUY", 10, 12.5, "EUR", data="2026-01-15T12:00:00")
    out = agent_tools.tool_get_portfolio_state()
    chiavi = list(out)
    assert chiavi.index("cash_source") < chiavi.index("positions")
    assert chiavi.index("note") < chiavi.index("positions")
    assert chiavi[-1] == "positions"


def test_se_sfora_il_tetto_lo_dichiara_in_testa(ambiente):
    from bellomberg.agents import agent_tools
    db, pj = ambiente
    pj.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    for i in range(15):
        t = f"LUNGA{i:02d}.MI"
        db.log_trade(t, "BUY", 1, 10.0, "EUR", data="2026-01-15T12:00:00")
        with db._conn() as c:
            c.execute("UPDATE positions SET tesi=? WHERE ticker=?", ("x" * 1000, t))
    out = agent_tools.tool_get_portfolio_state()
    assert list(out)[0] == "_vista" and "TAGLIATE" in out["_vista"], list(out)[:3]
    assert "15" in out["_vista"] or "1" in out["_vista"]   # il peso e' un numero misurato


def test_ordine_per_costo_decrescente_poi_ticker(ambiente):
    """F4 della review: col taglio in coda restano fuori le posizioni PICCOLE, non
    l'ultima lettera dell'alfabeto."""
    from bellomberg.agents import agent_tools
    db, pj = ambiente
    pj.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    db.log_trade("AAA.MI", "BUY", 1, 10.0, "EUR", data="2026-01-15T12:00:00")     # costo 10
    db.log_trade("ZZZ.MI", "BUY", 10, 100.0, "EUR", data="2026-01-15T12:00:00")   # costo 1000
    out = agent_tools.tool_get_portfolio_state()
    assert [p["ticker"] for p in out["positions"]] == ["ZZZ.MI", "AAA.MI"]


def test_temi_null_diventano_lista_vuota(ambiente):
    """F6 della review: nel DB vero 12 posizioni su 30 hanno temi NULL."""
    from bellomberg.agents import agent_tools
    db, pj = ambiente
    pj.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    db.log_trade("NULLI.MI", "BUY", 1, 10.0, "EUR", data="2026-01-15T12:00:00")
    with db._conn() as c:
        c.execute("UPDATE positions SET temi_monitoraggio=NULL WHERE ticker='NULLI.MI'")
    out = agent_tools.tool_get_portfolio_state()
    assert out["positions"][0]["temi_monitoraggio"] == []


def test_hint_non_cita_script_inesistenti(ambiente):
    """F5 della review: l'hint citava scripts/importa_trade_csv.py, che nasce con T6."""
    from bellomberg.agents import agent_tools
    _, pj = ambiente
    pj.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    out = agent_tools.tool_get_portfolio_state()
    import os
    for pezzo in re.findall(r"(?:scripts|tools)/[\w./]+", out.get("hint", "")):
        assert os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), pezzo)), pezzo


def test_lo_schema_del_tool_non_promette_pesi_e_rimanda_al_live():
    from bellomberg.agents import agent_tools
    voce = next(t for t in agent_tools.TOOLS_SCHEMA if t["name"] == "get_portfolio_state")
    assert "peso" not in voce["description"].lower()
    assert "get_portfolio_live" in voce["description"]
    assert "DB" in voce["description"]
