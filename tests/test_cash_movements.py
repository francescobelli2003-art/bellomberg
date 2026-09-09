# -*- coding: utf-8 -*-
"""Canale versamenti/prelievi (voce PM 12/08: "come se fosse un trade").

`MemoryDB.log_cash_movement`: registro DEPOSIT/WITHDRAWAL con guardie PRIMA
dell'INSERT (rifiuto = zero scritture, misurato col conteggio): tipo solo
DEPOSIT/WITHDRAWAL, importo > 0, prelievo oltre la cassa dichiarata coi due
numeri, data ISO o rifiuto. `get_cash_movements`: lettura per il rendering.
La cassa operativa (portfolio.json) resta al chiamante, stesso split del
percorso trade — qui si collauda il registro.
"""
import sys
import types

import pytest

from bellomberg.storage.memory_db import MemoryDB


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    pu = types.ModuleType("price_updater")
    pu.get_fx_to_eur = lambda cur: 1.0
    pu.get_fx_sources = lambda: {}
    monkeypatch.setitem(sys.modules, 'bellomberg.cli.price_updater', pu)
    return MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))


def _conta(db):
    with db._conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM cash_movements").fetchone()[0]


def test_deposit_scrive_riga_canonica(db):
    mid = db.log_cash_movement("deposit", 7000, data="2026-08-12",
                               nota="bonifico")
    assert isinstance(mid, int) and mid > 0
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM cash_movements WHERE id=?",
                           (mid,)).fetchone()
    assert row["type"] == "DEPOSIT"          # canonico anche da input minuscolo
    assert row["amount_eur"] == 7000.0
    assert row["date"] == "2026-08-12"
    assert row["note"] == "bonifico"


def test_withdrawal_entro_cassa_passa(db):
    mid = db.log_cash_movement("WITHDRAWAL", 500, cassa_disponibile=1000.0)
    with db._conn() as conn:
        row = conn.execute("SELECT type FROM cash_movements WHERE id=?",
                           (mid,)).fetchone()
    assert row["type"] == "WITHDRAWAL"


def test_withdrawal_oltre_cassa_rifiutato_coi_due_numeri(db):
    prima = _conta(db)
    with pytest.raises(ValueError) as exc:
        db.log_cash_movement("WITHDRAWAL", 40000, cassa_disponibile=1234.56)
    msg = str(exc.value)
    assert "40000" in msg and "1234.56" in msg
    assert _conta(db) == prima               # rifiuto = zero scritture, misurato


def test_tipo_sconosciuto_rifiutato(db):
    prima = _conta(db)
    with pytest.raises(ValueError):
        db.log_cash_movement("BONIFICO", 100)
    assert _conta(db) == prima


@pytest.mark.parametrize("importo", [0, -50, "abc", None,
                                     float("inf"), float("-inf"), float("nan")])
def test_importo_non_positivo_non_numerico_o_non_finito_rifiutato(db, importo):
    # inf/nan: json.loads accetta Infinity/NaN (lenienza python) e arrivano
    # fino al modello — l'unica difesa e' il ramo isfinite (review 12/08)
    prima = _conta(db)
    with pytest.raises(ValueError):
        db.log_cash_movement("DEPOSIT", importo)
    assert _conta(db) == prima


def test_importo_arrotondato_a_2_decimali_all_insert(db):
    mid = db.log_cash_movement("DEPOSIT", 100.019)
    with db._conn() as conn:
        row = conn.execute("SELECT amount_eur FROM cash_movements WHERE id=?",
                           (mid,)).fetchone()
    assert row["amount_eur"] == 100.02   # registro e cassa non divergono di sub-cent


@pytest.mark.parametrize("data_in,attesa", [
    ("2026-08-12T23:59:59", "2026-08-12"),  # il verbatim spostava il flusso di
    ("20260812", "2026-08-12"),             # giorno nel TWR lessicografico e
])                                          # l'IRR lo droppava zitto (review)
def test_data_normalizzata_al_giorno_iso(db, data_in, attesa):
    mid = db.log_cash_movement("DEPOSIT", 100, data=data_in)
    with db._conn() as conn:
        row = conn.execute("SELECT date FROM cash_movements WHERE id=?",
                           (mid,)).fetchone()
    assert row["date"] == attesa


def test_data_non_iso_rifiutata(db):
    prima = _conta(db)
    with pytest.raises(ValueError) as exc:
        db.log_cash_movement("DEPOSIT", 100, data="ieri sera")
    assert "ieri sera" in str(exc.value)
    assert _conta(db) == prima


def test_data_default_oggi(db):
    from datetime import datetime
    mid = db.log_cash_movement("DEPOSIT", 100)
    with db._conn() as conn:
        row = conn.execute("SELECT date FROM cash_movements WHERE id=?",
                           (mid,)).fetchone()
    assert row["date"] == datetime.now().strftime("%Y-%m-%d")


def test_duplicato_stessa_terna_rifiutato_senza_conferma(db):
    # delega PM 13/08 "come meglio credi": doppio click = doppio deposito era
    # il finding 2 della review; terna (data,tipo,importo) come il dedup
    # gia' esistente in setup_twr_tables
    db.log_cash_movement("DEPOSIT", 7000, data="2026-08-13")
    prima = _conta(db)
    with pytest.raises(ValueError) as exc:
        db.log_cash_movement("DEPOSIT", 7000, data="2026-08-13")
    assert "conferma" in str(exc.value).lower()
    assert _conta(db) == prima


def test_duplicato_con_conferma_esplicita_passa(db):
    db.log_cash_movement("DEPOSIT", 7000, data="2026-08-13")
    mid = db.log_cash_movement("DEPOSIT", 7000, data="2026-08-13",
                               conferma=True)
    assert mid > 0 and _conta(db) == 2


def test_terna_diversa_non_e_duplicato(db):
    db.log_cash_movement("DEPOSIT", 7000, data="2026-08-13")
    db.log_cash_movement("DEPOSIT", 7000, data="2026-08-14")
    db.log_cash_movement("WITHDRAWAL", 7000, data="2026-08-13",
                         cassa_disponibile=100000)
    db.log_cash_movement("DEPOSIT", 6999.99, data="2026-08-13")
    assert _conta(db) == 4


def test_importo_oltre_soglia_rifiutato_senza_conferma(db):
    # finding 5 della review: il fat-finger con uno zero di troppo passava liscio
    prima = _conta(db)
    with pytest.raises(ValueError) as exc:
        db.log_cash_movement("DEPOSIT", 100000.01)
    msg = str(exc.value)
    assert "100000" in msg and "conferma" in msg.lower()
    assert _conta(db) == prima


def test_importo_oltre_soglia_con_conferma_passa(db):
    mid = db.log_cash_movement("DEPOSIT", 300000, conferma=True)
    assert mid > 0


def test_importo_alla_soglia_passa_senza_conferma(db):
    mid = db.log_cash_movement("DEPOSIT", 100000)   # = soglia, non oltre
    assert mid > 0


def test_get_cash_movements_ordine_e_forma(db):
    db.log_cash_movement("DEPOSIT", 5000, data="2026-01-15", nota="iniziale")
    db.log_cash_movement("DEPOSIT", 7000, data="2026-08-12")
    db.log_cash_movement("WITHDRAWAL", 100, data="2026-08-12",
                         cassa_disponibile=50000)
    rows = db.get_cash_movements()
    assert len(rows) == 3
    assert rows[0]["date"] >= rows[-1]["date"]         # piu' recenti prima
    assert rows[-1]["date"] == "2026-01-15"
    assert {"id", "date", "type", "amount_eur", "note",
            "created_at"} <= set(rows[0].keys())
    assert db.get_cash_movements(limit=1)[0]["type"] == "WITHDRAWAL"


# ------------------------------------------------ F43 (5), 31/08: due chiavi
# `conferma` era UNA chiave per DUE guardie: confermare per la soglia disarmava
# anche il doppio-click — proprio nel ramo dove il doppio invio e' possibile
# (F37 del frontend, decisione PM 25/08). Ora ogni guardia ha la sua chiave;
# `conferma` resta come alias di ENTRAMBE per non rompere i chiamanti.

def test_conferma_soglia_non_disarma_la_guardia_duplicato(db):
    db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31", conferma=True)
    prima = _conta(db)
    with pytest.raises(ValueError) as exc:
        db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31",
                             conferma_soglia=True)
    assert "GUARDIA DUPLICATO" in str(exc.value)
    assert _conta(db) == prima


def test_conferma_duplicato_non_disarma_la_guardia_soglia(db):
    prima = _conta(db)
    with pytest.raises(ValueError) as exc:
        db.log_cash_movement("DEPOSIT", 150000, conferma_duplicato=True)
    assert "GUARDIA IMPORTO" in str(exc.value)
    assert _conta(db) == prima


def test_conferma_soglia_passa_la_sola_soglia(db):
    mid = db.log_cash_movement("DEPOSIT", 150000, conferma_soglia=True)
    assert mid > 0


def test_conferma_duplicato_passa_il_secondo_movimento_vero(db):
    db.log_cash_movement("DEPOSIT", 100, data="2026-08-31")
    mid = db.log_cash_movement("DEPOSIT", 100, data="2026-08-31",
                               conferma_duplicato=True)
    assert mid > 0 and _conta(db) == 2


def test_alias_conferma_spegne_entrambe_come_prima(db):
    db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31", conferma=True)
    mid = db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31",
                               conferma=True)
    assert mid > 0 and _conta(db) == 2


def test_i_rifiuti_suggeriscono_la_chiave_specifica(db):
    with pytest.raises(ValueError) as soglia:
        db.log_cash_movement("DEPOSIT", 150000)
    assert "conferma_soglia" in str(soglia.value)
    # review 31/08 (MEDIO): la coda «conferma=true» e' PORTANTE — la regex di
    # app/src/lib/cassa.ts:603 decide il bottone «CONFERMO, E' VOLUTO» su quel
    # letterale, e in «conferma_soglia=true» dopo «conferma» c'e' un underscore
    assert "conferma=true" in str(soglia.value)
    db.log_cash_movement("DEPOSIT", 100, data="2026-08-31")
    with pytest.raises(ValueError) as dup:
        db.log_cash_movement("DEPOSIT", 100, data="2026-08-31")
    assert "conferma_duplicato" in str(dup.value)
    assert "conferma=true" in str(dup.value)


def test_le_due_chiavi_insieme_equivalgono_all_alias(db):
    """review 31/08: la coppia che il frontend mandera' al posto dell'alias."""
    db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31", conferma=True)
    mid = db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31",
                               conferma_soglia=True, conferma_duplicato=True)
    assert mid > 0 and _conta(db) == 2


def test_a_guardie_entrambe_scoperte_parla_prima_la_soglia(db):
    """review 31/08: 150k duplicato senza chiavi — il rifiuto che il PM vede
    e' QUELLO della soglia (sta prima della SELECT): inchiodato, non implicito."""
    db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31", conferma=True)
    with pytest.raises(ValueError) as exc:
        db.log_cash_movement("DEPOSIT", 150000, data="2026-08-31")
    assert "GUARDIA IMPORTO" in str(exc.value)


# --------------------------------------- cablaggio: POST /cash/movement
# review 31/08 (MEDIO): senza questi due test le righe che passano le chiavi
# nuove a valle nell'endpoint si potevano cancellare a suite VERDE

def _pj_ancorato(tmp_path, monkeypatch, cash):
    import json as _json
    import bellomberg.storage.memory_db as _md
    p = tmp_path / "altrove" / "portfolio.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(_json.dumps({"cash_disponibile_eur": cash}), encoding="utf-8")
    monkeypatch.setattr(_md, "PORTFOLIO_JSON_PATH", str(p))
    return p


def test_endpoint_passa_conferma_soglia_a_valle(db, tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    _pj_ancorato(tmp_path, monkeypatch, 100.0)
    monkeypatch.setattr(bellomberg_api, "get_db", lambda: db)
    out = bellomberg_api.post_cash_movement(bellomberg_api.CashMovementIn(
        tipo="DEPOSIT", importo_eur=150000, data="2026-08-31", conferma_soglia=True))
    assert out["ok"] is True


def test_endpoint_passa_conferma_duplicato_a_valle(db, tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    _pj_ancorato(tmp_path, monkeypatch, 100.0)
    monkeypatch.setattr(bellomberg_api, "get_db", lambda: db)
    body = dict(tipo="DEPOSIT", importo_eur=100, data="2026-08-31")
    bellomberg_api.post_cash_movement(bellomberg_api.CashMovementIn(**body))
    out = bellomberg_api.post_cash_movement(bellomberg_api.CashMovementIn(
        **body, conferma_duplicato=True))
    assert out["ok"] is True
