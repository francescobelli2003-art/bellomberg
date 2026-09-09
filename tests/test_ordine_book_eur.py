"""Voce 10 di MASTER_TODO §9-quattuortrigies (riparata 01/08/2026, Fable 5).

get_portfolio() ordina con `ORDER BY quantita * prezzo_medio` in valuta
NATIVA: i titoli di Londra quotati in GBX (pence) entrano x100 e il libro
usciva `IOTA.L, PHI.L, ALFA, VOLT.MI...` a prescindere dal valore vero
(confermato sui dati veri il 28/07). get_portfolio_summary() converte in EUR
ma NON riordinava: ora il libro si riordina sul valore EUR convertito.
"""
from bellomberg.storage import memory_db
from bellomberg.cli import price_updater
from bellomberg.storage.memory_db import MemoryDB


class _DBFinto:
    """Solo cio' che get_portfolio_summary usa: self.get_portfolio()."""

    def get_portfolio(self):
        # ordine d'ingresso = quello del difetto: IOTA.L (GBX, x100) davanti
        return [
            {"ticker": "IOTA.L", "valuta": "GBX", "valore_mercato": 4_200_000.0,
             "pl_eur": None, "price_stale": False},
            {"ticker": "ALFA", "valuta": "USD", "valore_mercato": 90_000.0,
             "pl_eur": None, "price_stale": False},
            {"ticker": "ENI.MI", "valuta": "EUR", "valore_mercato": 60_000.0,
             "pl_eur": None, "price_stale": False},
        ]


def test_il_libro_si_ordina_sul_valore_eur(monkeypatch, tmp_path):
    # FX deterministici; cwd su tmp (dal 27/08 la cassa viene da PORTFOLIO_JSON_PATH,
    # che il conftest punta sotto tmp: file assente -> cash 0 DICHIARATO, cash_source None)
    monkeypatch.setattr(price_updater, "get_fx_to_eur",
                        lambda cur: {"GBX": 0.0117, "USD": 0.92}[cur])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(memory_db, "read_cash_state", lambda _path: {
        "cash_eur": 0.0, "cash_source": None,
        "cash_source_note": "fixture senza saldo"})
    monkeypatch.setattr(_DBFinto, "db_path", str(tmp_path / "finto.db"), raising=False)

    summ = MemoryDB.get_portfolio_summary(_DBFinto())

    # EUR veri: ALFA 82.800 > ENI 60.000 > IOTA.L 49.140 (4,2M pence)
    ordine = [p["ticker"] for p in summ["positions"]]
    assert ordine == ["ALFA", "ENI.MI", "IOTA.L"], ordine
    assert summ["fx_incomplete"] is None
