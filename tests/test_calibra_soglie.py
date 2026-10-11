"""tools/ops/calibra_soglie.py: le funzioni PURE della calibrazione (10/10/2026, Opus 5.5).
Oracoli indipendenti (numpy/pandas), serie sintetiche, zero rete."""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tools.ops import calibra_soglie as cs


@pytest.mark.parametrize("q", [0, 3, 20, 50, 80, 97, 100])
def test_percentile_come_numpy_lineare(q):
    xs = [0.83, 1.27, 0.91, 0.88, 1.04, 0.79, 0.97, 0.86, 1.13]
    assert cs.percentile(xs, q) == pytest.approx(float(np.percentile(xs, q)))


def test_percentile_rifiuta_serie_vuota_e_q_fuori_scala():
    with pytest.raises(ValueError):
        cs.percentile([], 50)
    with pytest.raises(ValueError):
        cs.percentile([1.0], 101)


def test_realizzata_come_vol_surface():
    rng = np.random.default_rng(7)
    giorni = [date(2031, 1, 1) + timedelta(days=i) for i in range(60)]
    prezzi = 100 * np.cumprod(1 + rng.normal(0, 0.011, 60))
    mia = cs.realizzata_21(dict(zip(giorni, prezzi)))
    s = pd.Series(prezzi, index=giorni).pct_change().dropna()
    oracolo = (s.rolling(21).std().dropna() * math.sqrt(252) * 100)
    assert len(mia) == len(oracolo)
    for d, v in oracolo.items():
        assert mia[d] == pytest.approx(v)


def test_rapporti_solo_sulle_date_comuni():
    a = {date(2031, 1, 2): 18.0, date(2031, 1, 3): 19.0, date(2031, 1, 6): 20.0}
    b = {date(2031, 1, 3): 20.0, date(2031, 1, 6): 25.0, date(2031, 1, 7): 21.0}
    assert cs.rapporti_allineati(a, b) == [(date(2031, 1, 3), 0.95), (date(2031, 1, 6), 0.8)]


def test_lettori_csv_cboe_e_fred():
    cboe = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2031,1,1,1,17.31\n01/03/2031,1,1,1,x\n"
    assert cs.leggi_cboe(cboe) == {date(2031, 1, 2): 17.31}
    fred = "observation_date,ZZSERIE\n2031-01-02,3.17\n2031-01-03,.\n2031-01-06,\n"
    assert cs.leggi_fred(fred) == {date(2031, 1, 2): 3.17}


def test_funding_pesato_regole_dello_score():
    base = cs.HL_BASE_ANN_PCT / (24 * 365 * 100)       # tasso orario al base
    ore = {1: base, 2: base * 3}
    per_coin = {"BTC": dict(ore), "ETH": dict(ore), "SOL": {1: base * 5}}
    scarti = cs.funding_pesato(per_coin, {"BTC": 2.0, "ETH": 1.0, "SOL": 1.0})
    # ora 1: SOL presente (peso 1 su 4) a 5x il base; ora 2: SOL assente, BTC/ETH a 3x
    assert scarti == pytest.approx([cs.HL_BASE_ANN_PCT * (1 / 4) * 4, cs.HL_BASE_ANN_PCT * 2])
    # senza ETH (obbligatorio) nessuna ora conta
    assert cs.funding_pesato({"BTC": ore, "SOL": ore}, {"BTC": 1.0, "SOL": 1.0}) == []
