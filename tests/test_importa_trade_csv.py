"""B6 (02/09, pubblicazione): l'import dei trade da CSV di chiunque, al posto dello
script coi 35 lotti del PM. Dry-run di default e MISURATO (conteggio prima/dopo:
lezione «una garanzia dichiarata dev'essere una misura»). Tutte le righe si validano
PRIMA di scrivere: una rotta = zero scritture.
"""
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tools.ops import importa_trade_csv as itc

CSV_OK = """data,ticker,azione,quantita,prezzo,valuta,note
2026-01-15,ABCD.MI,BUY,10,12.50,EUR,primo acquisto
16/01/2026,WXYZ,BUY,3,101.2,USD,
2026-02-01,ABCD.MI,SELL,4,13.00,EUR,presa di profitto
"""
CSV_ROTTO = """data,ticker,azione,quantita,prezzo,valuta,note
2026-01-15,ABCD.MI,COMPRA,10,12.50,EUR,
2026-01-15,,BUY,10,12.50,EUR,
2026-01-15,ABCD.MI,BUY,-1,12.50,EUR,
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self: None)
    return MemoryDB(db_path=str(tmp_path / "t.db"), chroma_path=str(tmp_path / "chroma"))


def _n(db):
    with db._conn() as c:
        return c.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]


def test_dry_run_non_scrive_e_lo_misura(tmp_path, db):
    p = tmp_path / "t.csv"
    p.write_text(CSV_OK, encoding="utf-8")
    r = itc.importa(db, itc.leggi_csv(str(p)), apply=False)
    assert (r["lette"], r["scritte"], r["prima"], r["dopo"]) == (3, 0, 0, 0)
    assert _n(db) == 0


def test_apply_scrive_tutte_le_righe(tmp_path, db):
    p = tmp_path / "t.csv"
    p.write_text(CSV_OK, encoding="utf-8")
    r = itc.importa(db, itc.leggi_csv(str(p)), apply=True)
    assert (r["lette"], r["scritte"], r["prima"], r["dopo"]) == (3, 3, 0, 3)
    with db._conn() as c:
        rows = c.execute("SELECT ticker, action, quantita, prezzo, valuta, data "
                         "FROM trade_history ORDER BY id").fetchall()
    assert tuple(rows[1]) == ("WXYZ", "BUY", 3.0, 101.2, "USD", "2026-01-16T12:00:00")


def test_riga_rotta_ferma_tutto_prima_di_scrivere(tmp_path, db):
    p = tmp_path / "t.csv"
    p.write_text(CSV_ROTTO, encoding="utf-8")
    errori = itc.valida(itc.leggi_csv(str(p)))
    assert len(errori) == 3 and "riga 2" in errori[0] and "azione" in errori[0]
    r = itc.importa(db, itc.leggi_csv(str(p)), apply=True)
    assert r["scritte"] == 0 and _n(db) == 0 and r["errori"]


def test_intestazione_sbagliata_e_un_errore_dichiarato(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("date,symbol,side,qty\n2026-01-15,ABCD.MI,BUY,10\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        itc.leggi_csv(str(p))
    assert "ticker" in str(e.value) and "azione" in str(e.value)


def test_guardia_valute_anticipata_zero_scritture(tmp_path, db):
    """La guardia valute del DB (log_trade) e' ANTICIPATA dal replay del libro
    (review 02/09, F1): prima scriveva la riga 2 e si fermava alla 3 (1 scritta);
    ora la promessa «una riga rotta = zero scritture» vale anche qui."""
    p = tmp_path / "t.csv"
    p.write_text("data,ticker,azione,quantita,prezzo,valuta,note\n"
                 "2026-01-15,ABCD.MI,BUY,10,12.50,EUR,\n"
                 "2026-01-16,ABCD.MI,BUY,1,14.00,USD,valuta sbagliata\n"
                 "2026-01-17,ABCD.MI,BUY,1,15.00,EUR,\n", encoding="utf-8")
    r = itc.importa(db, itc.leggi_csv(str(p)), apply=True)
    assert r["scritte"] == 0 and r["dopo"] == 0 and _n(db) == 0
    assert any("riga 3" in e and "valuta" in e.lower() for e in r["errori"]), r["errori"]


def test_esempio_csv_del_repo_e_valido():
    righe = itc.leggi_csv(os.path.join(REPO, "tools", "ops", "esempio_trade.csv"))
    assert len(righe) >= 3 and itc.valida(righe) == []


def test_esempio_importato_su_db_tmp(db):
    r = itc.importa(db, itc.leggi_csv(os.path.join(REPO, "tools", "ops", "esempio_trade.csv")), apply=True)
    assert r["scritte"] == 3 and r["errori"] == []
    with db._conn() as c:
        row = c.execute("SELECT quantita, prezzo_medio FROM positions WHERE ticker='ABCD.MI'").fetchone()
    assert (row[0], row[1]) == (6, 12.5)


def test_script_vecchio_coi_lotti_del_pm_non_e_piu_in_radice():
    assert not os.path.exists(os.path.join(REPO, "import_user_trades.py"))
    # la seconda meta' vale solo dove c'e' l'attic: nel repo pubblico la quarantena non esce, e
    # il file che NON deve stare in radice non c'e' proprio (P2/T9, 03/09)
    if os.path.isdir(os.path.join(REPO, "archive", "private", "attic")):
        assert os.path.exists(os.path.join(REPO, "archive", "private", "attic", "oneshot", "import_user_trades.py"))
    for f in ("src/bellomberg/portfolio/portfolio_analytics.py", "app/src/pages/PerformancePage.tsx"):
        assert "import_user_trades" not in open(os.path.join(REPO, f), encoding="utf-8").read(), f


# ---------------------------------------------------------------------------
# Dalla review (02/09): correttezza contabile dello strumento (allocano soldi veri)
# ---------------------------------------------------------------------------
def _csv(tmp_path, testo):
    p = tmp_path / "t.csv"
    p.write_text("data,ticker,azione,quantita,prezzo,valuta,note\n" + testo, encoding="utf-8")
    return str(p)


def test_sell_senza_posizione_rifiutata_prima_di_scrivere(tmp_path, db):
    """F1: log_trade scrive una riga ORFANA (POST /trade la rifiuta con 400): il
    replay del NAV porterebbe la quantita' sotto zero."""
    p = _csv(tmp_path, "2026-01-15,ABCD.MI,SELL,4,13.00,EUR,\n")
    r = itc.importa(db, itc.leggi_csv(p), apply=True)
    assert r["scritte"] == 0 and _n(db) == 0
    assert any("riga 2" in e and "senza posizione" in e for e in r["errori"]), r["errori"]


def test_sell_oltre_il_posseduto_rifiutata(tmp_path, db):
    p = _csv(tmp_path, "2026-01-15,ABCD.MI,BUY,10,12.50,EUR,\n2026-01-20,ABCD.MI,SELL,25,13.00,EUR,\n")
    r = itc.importa(db, itc.leggi_csv(p), apply=True)
    assert r["scritte"] == 0 and _n(db) == 0
    assert any("riga 3" in e and "posseduto" in e for e in r["errori"]), r["errori"]


def test_valuta_diversa_dalla_posizione_rifiutata_prima_di_scrivere(tmp_path, db):
    """La guardia valute del DB viene ANTICIPATA: zero scritture, non una."""
    p = _csv(tmp_path, "2026-01-15,ABCD.MI,BUY,10,12.50,EUR,\n2026-01-16,ABCD.MI,BUY,1,14.00,USD,\n")
    r = itc.importa(db, itc.leggi_csv(p), apply=True)
    assert r["scritte"] == 0 and _n(db) == 0
    assert any("riga 3" in e and "valuta" in e.lower() for e in r["errori"]), r["errori"]


def test_righe_scritte_in_ordine_di_data_non_di_file(tmp_path, db):
    """F2: log_trade ricalcola il carico in ordine di CHIAMATA. Stessi 3 trade,
    file disordinato: cronologico = carico 15 e realized 150; disordinato = 20 e 200."""
    p = _csv(tmp_path, "2026-03-01,ABCD.MI,SELL,10,30,EUR,\n"
                       "2026-01-01,ABCD.MI,BUY,10,10,EUR,\n"
                       "2026-02-01,ABCD.MI,BUY,10,20,EUR,\n")
    r = itc.importa(db, itc.leggi_csv(p), apply=True)
    assert r["scritte"] == 3 and r["errori"] == []
    with db._conn() as c:
        pm = c.execute("SELECT prezzo_medio FROM positions WHERE ticker='ABCD.MI'").fetchone()[0]
        real = c.execute("SELECT realized_local FROM trade_history WHERE action='SELL'").fetchone()[0]
    assert pm == 15 and real == 150


def test_data_e_prezzo_rotti_dichiarati(tmp_path):
    p = _csv(tmp_path, "31/02/2026,ABCD.MI,BUY,1,10,EUR,data impossibile\n"
                       "2026-01-15,ABCD.MI,BUY,1,0,EUR,prezzo zero\n"
                       "2026-01-15,ABCD.MI,BUY,nan,10,EUR,quantita nan\n"
                       "2099-01-01,ABCD.MI,BUY,1,10,EUR,data futura\n")
    errori = itc.valida(itc.leggi_csv(p))
    assert len(errori) == 4, errori
    assert "riga 2" in errori[0] and "data" in errori[0]
    assert "riga 3" in errori[1] and "prezzo" in errori[1]
    assert "riga 4" in errori[2] and "quantita" in errori[2]
    assert "riga 5" in errori[3] and "futur" in errori[3]


def test_virgola_decimale_accettata_se_non_ambigua(tmp_path, db):
    """Lezione F7 (input type=number): il PM scrive la virgola. «10,5» = 10.5;
    «1.234,5» e' ambiguo e si rifiuta."""
    p = _csv(tmp_path, "2026-01-15,ABCD.MI,BUY,\"10,5\",\"12,50\",EUR,\n")
    r = itc.importa(db, itc.leggi_csv(p), apply=True)
    assert r["scritte"] == 1, r["errori"]
    with db._conn() as c:
        row = c.execute("SELECT quantita, prezzo FROM trade_history").fetchone()
    assert (row[0], row[1]) == (10.5, 12.5)
    p2 = _csv(tmp_path, "2026-01-15,ABCD.MI,BUY,\"1.234,5\",10,EUR,\n")
    assert any("ambigu" in e for e in itc.valida(itc.leggi_csv(p2)))


def test_vendite_in_valuta_realized_eur_nd(tmp_path, db, monkeypatch):
    """F5: log_trade calcola realized_eur col cambio di OGGI (rete); un import e'
    retrodatato per definizione: n.d. dichiarato batte un numero col cambio sbagliato."""
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda ccy: 0.9)
    p = _csv(tmp_path, "2026-01-15,WXYZ,BUY,10,100,USD,\n2026-02-15,WXYZ,SELL,4,120,USD,\n")
    r = itc.importa(db, itc.leggi_csv(p), apply=True)
    assert r["scritte"] == 2, r["errori"]
    with db._conn() as c:
        row = c.execute("SELECT realized_local, realized_eur FROM trade_history WHERE action='SELL'").fetchone()
    assert row[0] == 80 and row[1] is None
    assert r["vendite_in_valuta"] == 1 and "backfill_realized" in r["nota"]


def test_main_rifiuta_con_backend_attivo(tmp_path, db, monkeypatch):
    monkeypatch.setattr(itc, "_porta_8765_occupata", lambda: True)
    p = _csv(tmp_path, "2026-01-15,ABCD.MI,BUY,1,10,EUR,\n")
    assert itc.main([p, "--apply", "--db", db.db_path]) == 2
    assert _n(db) == 0


def test_main_apply_fa_backup_sano_e_dichiara_la_cassa(tmp_path, db, monkeypatch, capsys):
    import glob
    import sqlite3
    monkeypatch.setattr(itc, "_porta_8765_occupata", lambda: False)
    p = _csv(tmp_path, "2026-01-15,ABCD.MI,BUY,1,10,EUR,\n")
    assert itc.main([p, "--apply", "--db", db.db_path]) == 0
    bk = glob.glob(db.db_path + ".pre_import_*.bak")
    assert len(bk) == 1
    c = sqlite3.connect(bk[0])
    assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert c.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0] == 0   # PRE import
    c.close()
    out = capsys.readouterr().out
    assert "cassa" in out.lower() and "cash_state" in out and "portfolio.json" not in out
    assert _n(db) == 1


def test_main_db_inesistente_si_ferma_senza_crearlo(tmp_path, monkeypatch):
    monkeypatch.setattr(itc, "_porta_8765_occupata", lambda: False)
    p = _csv(tmp_path, "2026-01-15,ABCD.MI,BUY,1,10,EUR,\n")
    fantasma = tmp_path / "cartella_nuova" / "x.db"
    assert itc.main([p, "--db", str(fantasma)]) == 2
    assert not fantasma.exists() and not fantasma.parent.exists()
