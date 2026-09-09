"""Guardia di plausibilita' prezzi (specifica ok PM 01/08/2026, Fable 5).

Proposta il 27/07, decisa il 01/08: tre controlli sul POST /trade, pensati
sulle classi GIA' VISTE nel progetto:
  - virgola mangiata da input number: 158,50 -> 15850 (x100), stava in F7 da mesi;
  - GBX/GBP adiacenti nella tendina: 42 GBP invece di 4.200 GBX (x100 permanente);
  - valute mischiate nel prezzo medio: trade #46/#47 ALFA in EUR su book USD
    (misurato dal ponte F22, GIA' in produzione).

Regole: (1) valuta discorde dalla posizione -> RIFIUTO (nessuna conversione
zitta) · (2) oltre x3/:3 dall'ultimo prezzo noto -> RIFIUTO col confronto dei
due numeri; fra +-30% e x3 -> AVVISO dichiarato, la scrittura passa ·
(3) positivita' server-side. DIVIDEND e' ESENTE dai controlli 1-2 (dichiarato:
il "prezzo" e' il dividendo per azione — ratio senza senso — e IOTA.L paga
dividendi USD su quotazione GBX); resta la positivita'.
"""
from bellomberg.cli.guardia_prezzi import AVVISO_PCT, RIFIUTO_RATIO, controlla_trade


def test_virgola_mangiata_x100_rifiutata():
    g = controlla_trade(prezzo=15850, quantita=10, valuta_trade="EUR",
                        valuta_posizione="EUR", ultimo_prezzo=158.50, azione="BUY")
    assert g["esito"] == "rifiuto"
    assert "15850" in g["motivo"] and "158.5" in g["motivo"]


def test_gbp_al_posto_di_gbx_rifiutato_dal_ratio():
    # stessa etichetta valuta ma prezzo in sterline su quotazione in pence
    g = controlla_trade(prezzo=42, quantita=100, valuta_trade="GBX",
                        valuta_posizione="GBX", ultimo_prezzo=4200, azione="BUY")
    assert g["esito"] == "rifiuto"


def test_valute_mischiate_caso_alfa_rifiutato():
    # il trade #46 vero: BUY 1 @ 150 EUR su posizione quotata USD
    g = controlla_trade(prezzo=150, quantita=1, valuta_trade="EUR",
                        valuta_posizione="USD", ultimo_prezzo=150.04, azione="BUY")
    assert g["esito"] == "rifiuto"
    assert "EUR" in g["motivo"] and "USD" in g["motivo"]


def test_crollo_legittimo_passa_pulito():
    g = controlla_trade(prezzo=75, quantita=10, valuta_trade="EUR",
                        valuta_posizione="EUR", ultimo_prezzo=100, azione="BUY")
    assert g["esito"] == "ok"


def test_scostamento_forte_ma_plausibile_da_avviso_e_passa():
    g = controlla_trade(prezzo=155, quantita=10, valuta_trade="EUR",
                        valuta_posizione="EUR", ultimo_prezzo=100, azione="BUY")
    assert g["esito"] == "avviso"
    assert "+55.0%" in g["motivo"]


def test_dividend_esente_da_valuta_e_scala():
    # IOTA.L: dividendo USD per azione su quotazione GBX — legittimo
    g = controlla_trade(prezzo=0.5, quantita=100, valuta_trade="USD",
                        valuta_posizione="GBX", ultimo_prezzo=4200, azione="DIVIDEND")
    assert g["esito"] == "ok"


def test_positivita_vale_anche_per_dividend():
    assert controlla_trade(prezzo=0, quantita=100, valuta_trade="USD",
                           valuta_posizione=None, ultimo_prezzo=None,
                           azione="DIVIDEND")["esito"] == "rifiuto"
    assert controlla_trade(prezzo=10, quantita=-1, valuta_trade="EUR",
                           valuta_posizione=None, ultimo_prezzo=None,
                           azione="BUY")["esito"] == "rifiuto"


def test_buy_nuovo_senza_storico_solo_positivita():
    g = controlla_trade(prezzo=10, quantita=5, valuta_trade="EUR",
                        valuta_posizione=None, ultimo_prezzo=None, azione="BUY")
    assert g["esito"] == "ok"


def test_nan_e_infinito_rifiutati():
    # review 01/08: NaN supera sia `<= 0` sia i ratio — da curl passerebbe
    assert controlla_trade(prezzo=float("nan"), quantita=1, valuta_trade="EUR",
                           azione="BUY")["esito"] == "rifiuto"
    assert controlla_trade(prezzo=10, quantita=float("inf"), valuta_trade="EUR",
                           azione="BUY")["esito"] == "rifiuto"


def test_il_motivo_dichiara_la_data_del_riferimento():
    g = controlla_trade(prezzo=15850, quantita=1, valuta_trade="EUR",
                        valuta_posizione="EUR", ultimo_prezzo=158.50,
                        azione="BUY", ultimo_prezzo_data="2026-07-31 17:45:02")
    assert g["esito"] == "rifiuto"
    assert "2026-07-31" in g["motivo"]


def test_soglie_dichiarate():
    # le soglie decise dal PM il 01/08: cambiarle e' una decisione, non un refactor
    assert RIFIUTO_RATIO == 3.0
    assert AVVISO_PCT == 0.30
