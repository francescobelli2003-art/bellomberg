"""Correzione backend Vol Deck (audit 09/10, Opus 5.5): test con ASSERZIONI NUMERICHE.

Gli esperimenti E1-E8 dell'audit (AUDIT-VOLDECK-prove) stampavano e non
asserivano: qui diventano test. Solo input sintetici, zero rete, zero DB
personale. Gli oracoli (delta BS, smile, interpolazioni) sono scritti QUI, non
importati dal codice sotto test.
"""
from datetime import date, datetime, timedelta, timezone
from math import erf, log, sqrt
import sqlite3
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bellomberg.market_data import polygon_data as provider
from bellomberg.portfolio import vol_surface as vol

NY = ZoneInfo("America/New_York")
# mercoledi' 07/10/2026 12:00 New York (mercato aperto): orologio fisso per tutti i test
NOW_NY = datetime(2026, 10, 7, 12, 0, tzinfo=NY)


def dte(days):
    return (NOW_NY.date() + timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(provider, "polygon_available", lambda: True)
    monkeypatch.setattr(provider, "_get", lambda *a, **k: pytest.fail("rete"))
    monkeypatch.setattr(vol, "_ny_now", lambda: NOW_NY, raising=False)
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: SimpleNamespace(fast_info={"lastPrice": None})))
    vol._CHAIN_CACHE.clear()


def yf_spot(monkeypatch, spot, closes=None):
    def history(**k):
        if closes is None:
            raise RuntimeError("nessuna storia")
        import pandas as pd
        return pd.DataFrame({"Close": closes},
                            index=pd.date_range("2025-10-01", periods=len(closes), freq="B"))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: SimpleNamespace(fast_info={"lastPrice": spot}, history=history,
                                         earnings_dates=None)))


# ---- oracoli indipendenti --------------------------------------------------

def smile(m):  # smile equity: skew -0.4 per unita' di moneyness + curvatura
    return 0.20 - 0.40 * (m - 1) + 0.60 * (m - 1) ** 2


def bs_delta(S, K, T, s, typ):
    d1 = (log(S / K) + 0.5 * s * s * T) / (s * sqrt(T))
    n = (1 + erf(d1 / sqrt(2))) / 2
    return n if typ == "call" else n - 1


def row(K, typ, S, T, oi=100, iv=None, **extra):
    iv = smile(K / S) if iv is None else iv
    return {"contract": f"O:X{typ[0]}{K}", "type": typ, "strike": float(K), "iv": iv,
            "delta": bs_delta(S, K, T, iv, typ), "oi": oi, "volume": 0, **extra}


def lin(x, x1, y1, x2, y2):
    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)


def delta_interp(chain, typ, target):
    pts = sorted((c["delta"], c["iv"]) for c in chain if c["type"] == typ)
    for (d1, v1), (d2, v2) in zip(pts, pts[1:]):
        if d1 <= target <= d2:
            return lin(target, d1, v1, d2, v2)
    raise AssertionError("oracolo: nessun intervallo")


# ---- M1: 25 delta (v2 review 10/10: smile lineare in strike + delta BS ricalcolato) ----

def bisect(f, lo, hi, n=200):
    flo = f(lo)
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def true25(S, T, typ, sm=smile):
    """Oracolo: K del 25Δ risolto sullo smile CONTINUO, IV in quel K."""
    tgt = 0.25 if typ == "call" else -0.25
    K = bisect(lambda K: bs_delta(S, K, T, sm(K / S), typ) - tgt, S * 0.3, S * 3.0)
    return sm(K / S)


def strike25(chain, S, T, typ):
    """Oracolo del metodo: smile lineare in strike sui contratti del tipo."""
    pts = sorted((c["strike"], c["iv"]) for c in chain if c["type"] == typ)
    siv = lambda K: next(lin(K, k1, v1, k2, v2) for (k1, v1), (k2, v2) in zip(pts, pts[1:]) if k1 <= K <= k2)
    tgt = 0.25 if typ == "call" else -0.25
    K = bisect(lambda K: bs_delta(S, K, T, siv(K), typ) - tgt, pts[0][0], pts[-1][0])
    return siv(K), K


def test_E1_rr25_bf25_strike_lineare_e_delta_bs_ricalcolato_su_strike_radi():
    S, T = 100.0, 30 / 365
    chain = [row(K, t, S, T) for K in range(90, 115, 5) for t in ("call", "put")]
    m = vol._slice_metrics(chain, S, T)
    (c25, kc), (p25, kp) = strike25(chain, S, T, "call"), strike25(chain, S, T, "put")
    assert m["iv_25d_call"] == pytest.approx(c25, abs=1e-4)
    assert m["iv_25d_put"] == pytest.approx(p25, abs=1e-4)
    assert m["k25_call"] == pytest.approx(kc, abs=1e-3) and m["k25_put"] == pytest.approx(kp, abs=1e-3)
    assert m["rr25"] == pytest.approx(c25 - p25, abs=1e-4)
    assert m["bf25"] == pytest.approx(0.5 * (c25 + p25) - 0.20, abs=1e-4)
    # valore vero (smile continuo): errore < 0,01 punti di vol
    rr_true = true25(S, T, "call") - true25(S, T, "put")
    assert rr_true == pytest.approx(-0.0309, abs=1e-4)
    assert abs(m["rr25"] - rr_true) < 1e-4
    # la v1.6 (lineare in delta del provider) dava -0,0324 (-0,15 punti); la v1 -0,0400
    d_old = delta_interp(chain, "call", 0.25) - delta_interp(chain, "put", -0.25)
    assert abs(d_old - rr_true) > 10 * abs(m["rr25"] - rr_true)
    lo, hi = m["rr25_call_strikes"]
    assert hi - lo == 5.0 and lo <= kc <= hi
    lo, hi = m["rr25_put_strikes"]
    assert hi - lo == 5.0 and lo <= kp <= hi


def test_rr25_smile_ripido_strike_da_10_errore_contenuto():
    """Single name ripido, 7 giorni, strike ogni 10: la v1.6 sbagliava di -3,2 punti."""
    sm = lambda m: 0.35 - 0.80 * (m - 1) + 2.0 * (m - 1) ** 2
    S, T = 100.0, 7 / 365
    chain = [row(K, t, S, T, iv=sm(K / S)) for K in range(50, 161, 10) for t in ("call", "put")]
    m = vol._slice_metrics(chain, S, T)
    rr_true = true25(S, T, "call", sm) - true25(S, T, "put", sm)
    assert abs(m["rr25"] - rr_true) < 0.001                 # < 0,1 punti (misurato -0,074)
    d_old = delta_interp(chain, "call", 0.25) - delta_interp(chain, "put", -0.25)
    assert abs(d_old - rr_true) > 0.02                      # v1.6: -3,2 punti


def test_rr25_nd_se_il_delta_non_e_raggiunto_o_manca_il_tempo():
    S, T = 100.0, 30 / 365
    # solo strike vicini all'ATM: nessuna call fino a delta 0,25 ne' put fino a -0,25
    chain = [row(K, t, S, T) for K in (98, 99, 100, 101, 102) for t in ("call", "put")]
    m = vol._slice_metrics(chain, S, T)
    assert m["rr25"] is None and m["bf25"] is None and m["iv_25d_call"] is None
    assert "25" in m["rr25_reason"] and "estrapolato" in m["rr25_reason"]
    wide = [row(K, t, S, T) for K in range(90, 115, 5) for t in ("call", "put")]
    nt = vol._slice_metrics(wide, S)          # niente T e niente scadenza nei contratti
    assert nt["rr25"] is None and "tempo a scadenza" in nt["rr25_reason"]
    # con la scadenza nei contratti T si ricava (16:00 New York)
    e = dte(30)
    via = vol._slice_metrics([dict(c, expiry=e) for c in wide], S)
    assert via["rr25"] == pytest.approx(vol._slice_metrics(wide, S, vol._t_years(e, NOW_NY))["rr25"])


# ---- M2: una sola ATM, la stessa della griglia (E7) ------------------------

def test_E7_atm_interpolata_coincide_con_la_griglia():
    S, T = 102.4, 30 / 365
    chain = [row(K, t, S, T) for K in range(80, 130, 5) for t in ("call", "put")]
    m = vol._slice_metrics(chain, S)
    m1, m2 = 100 / S, 105 / S  # put 100 (K<=S) e call 105 (K>S) a cavallo di K/S=1
    expected = lin(1.0, m1, smile(m1), m2, smile(m2))
    assert m["atm_iv"] == pytest.approx(expected, abs=1e-4)
    assert m["atm_iv"] == pytest.approx(0.2004, abs=1e-4)      # prima 0,2097
    assert m["iv_grid"][8] == m["atm_iv"]                       # una sola definizione
    assert m["atm_strikes_used"] == [100.0, 105.0]
    # put 100 assente: interpolazione fra put 95 e call 105, mai media di due strike scelti a parte
    m2b = vol._slice_metrics([c for c in chain if not (c["type"] == "put" and c["strike"] == 100.0)], S)
    mp = 95 / S
    assert m2b["atm_iv"] == pytest.approx(lin(1.0, mp, smile(mp), m2, smile(m2)), abs=1e-4)
    assert m2b["atm_iv"] == m2b["iv_grid"][8]


def test_smile_noto_atm_griglia_esatta():
    S, T = 100.0, 45 / 365
    chain = [row(80 + 2.5 * i, t, S, T) for i in range(17) for t in ("call", "put")]
    m = vol._slice_metrics(chain, S)
    assert m["atm_iv"] == pytest.approx(0.2000, abs=1e-4)
    for mny, iv in zip(vol.MONEYNESS_GRID, m["iv_grid"]):
        assert iv == pytest.approx(smile(mny), abs=1e-4), mny


def test_spot_fuori_dagli_strike_osservati_non_estrapola():
    S, T = 100.0, 30 / 365
    chain = [row(K, t, S, T) for K in range(60, 95, 5) for t in ("call", "put")]
    metrics, why = vol._slice_detail(chain, S)
    assert metrics is None and "estrapolata" in why


# ---- A3: contratti rettificati esclusi (E2) ---------------------------------

def test_E2_rettificati_esclusi_e_contati():
    S, T = 100.0, 30 / 365
    chain = [row(80 + 2.5 * i, t, S, T) for i in range(17) for t in ("call", "put")]
    adj = [dict(row(100.0, "put", S, T, oi=5000, iv=0.55), contract="O:X1P100", adjusted=True),
           dict(row(100.0, "call", S, T, oi=5000, iv=0.55), contract="O:X1C100", adjusted=True),
           dict(row(97.5, "put", S, T, oi=9000, iv=0.70), contract="O:X2P97", multiplier=10)]
    out = vol._slice_metrics(chain + adj, S)
    assert out["atm_iv"] == pytest.approx(0.2000, abs=1e-4)   # prima 0,55
    assert out["iv_grid"][8] == pytest.approx(0.2000, abs=1e-4)
    assert out["n_rettificati_esclusi"] == 3
    # moltiplicatore standard dichiarato = contratto normale
    std = vol._slice_metrics([dict(c, multiplier=100) for c in chain], S)
    assert std["n_rettificati_esclusi"] == 0


# ---- M3: quote non negoziabili fuori dallo smile (E8) -----------------------

def test_E8_bid_zero_spread_incrociate_stantie_escluse_e_contate():
    S, T = 100.0, 30 / 365
    fresh = "2026-10-07T15:55:00+00:00"
    chain = [row(80 + 2.5 * i, t, S, T, bid=1.0, ask=1.1, quote_timestamp=fresh)
             for i in range(17) for t in ("call", "put")]

    def spoil(c):
        if c["type"] == "put" and c["strike"] <= 85:          # ala put 80-85: bid 0, IV 0,90
            return dict(c, iv=0.90, bid=0.0, ask=0.05)
        if c["type"] == "put" and c["strike"] == 87.5:        # spread: ask = 10 x bid
            return dict(c, iv=0.80, bid=0.05, ask=0.50)
        if c["type"] == "call" and c["strike"] == 120:        # quota incrociata
            return dict(c, iv=0.80, bid=0.60, ask=0.50)
        if c["type"] == "call" and c["strike"] == 117.5:      # quota di 2 ore prima
            return dict(c, iv=0.80, quote_timestamp="2026-10-07T13:55:00+00:00")
        return c
    out = vol._slice_metrics([spoil(c) for c in chain], S)
    assert out["quote_discards"] == {"no_bid": 3, "wide_spread": 1, "crossed": 1, "stale": 1}
    assert out["n_quote_scartate"] == 6 and out["n_quote_non_verificabili"] == 0
    assert out["iv_grid"][:4] == [None, None, None, None]     # 0,80..0,875 non osservate: null
    assert out["iv_grid"][4] == pytest.approx(smile(0.90), abs=1e-4)
    assert out["iv_grid"][-2:] == [None, None]                 # 1,175 e 1,20 scartate
    assert max(v for v in out["iv_grid"] if v is not None) < 0.30   # prima 0,90
    assert out["quote_time_min"] == out["quote_time_max"] == fresh


def test_bid_ask_assenti_non_svuotano_lo_smile_ma_si_contano():
    S, T = 100.0, 30 / 365
    chain = [row(80 + 2.5 * i, t, S, T) for i in range(17) for t in ("call", "put")]
    out = vol._slice_metrics(chain, S)
    assert out["n_quote_scartate"] == 0 and out["n_quote_non_verificabili"] == 34
    assert out["n_quote_senza_timestamp"] == 34


# ---- A1: chain grande, nessun troncamento silenzioso (E3) ---------------------

def _snap_pages(S, strikes, expiry, T, underlying=None):
    rows = []
    for typ in ("call", "put"):  # ordine per ticker: tutte le call prima delle put
        for K in strikes:
            iv = smile(K / S)
            rows.append({"details": {"ticker": f"O:SPY{typ[0]}{K}", "contract_type": typ,
                                     "strike_price": K, "expiration_date": expiry},
                         "greeks": {"delta": bs_delta(S, K, T, iv, typ), "gamma": .01},
                         "implied_volatility": iv, "open_interest": 100, "day": {"volume": 5},
                         **({"underlying_asset": underlying} if underlying else {})})
    return [rows[i:i + 250] for i in range(0, len(rows), 250)]


@pytest.mark.parametrize("S", [560.0, 650.0])
def test_E3_ramo_campionato_legge_la_chain_completa(monkeypatch, S):
    yf_spot(monkeypatch, S)
    e = dte(20)
    pages = _snap_pages(S, list(range(400, 751)), e, 20 / 365)
    seen = {"chain": 0}

    def get(path, params=None):
        if "reference" in path:
            return {"results": [{"expiration_date": e}]}
        i = seen["chain"]; seen["chain"] += 1
        nxt = f"https://api.polygon.io/v3/snapshot/options/SPY?cursor=p{i + 1}" if i + 1 < len(pages) else None
        return {"results": pages[i], **({"next_url": nxt} if nxt else {})}
    monkeypatch.setattr(provider, "_get", get)
    out = vol.build_vol_surface("SPY", max_expiries=1, include_context=False)
    s = out["slices"][0]
    assert seen["chain"] == len(pages) == 3
    assert s["n_calls"] == s["n_puts"] == 351
    assert s["atm_iv"] == pytest.approx(0.2000, abs=1e-4)      # S=650: prima 0,2351
    r = out["coverage"]["rows"][0]
    assert r["status"] == "loaded" and r["chain_complete"] is True and r["n_contracts"] == 702
    assert out["coverage"]["complete"] and out["partial"] is False
    for mny, iv in zip(vol.MONEYNESS_GRID, s["iv_grid"]):
        if 400 <= mny * S <= 750:
            assert iv == pytest.approx(smile(mny), abs=2e-4), mny
        else:
            assert iv is None, mny                                 # mai estrapolata


def test_ramo_campionato_chain_interrotta_e_partial_dichiarato(monkeypatch):
    yf_spot(monkeypatch, 560.0)
    e = dte(20)
    pages = _snap_pages(560.0, list(range(400, 751)), e, 20 / 365)
    seen = {"chain": 0}

    def get(path, params=None):
        if "reference" in path:
            return {"results": [{"expiration_date": e}]}
        i = seen["chain"]; seen["chain"] += 1
        if i == 2:
            return {"error": "HTTP 502"}
        return {"results": pages[i], "next_url": f"https://api.polygon.io/x?cursor=p{i + 1}"}
    monkeypatch.setattr(provider, "_get", get)
    out = vol.build_vol_surface("SPY", max_expiries=1, include_context=False)
    r = out["coverage"]["rows"][0]
    assert r["status"] == "partial" and r["chain_complete"] is False and "502" in r["reason"]
    assert out["coverage"]["partial_expiries"] == [e] and out["partial"] is True
    assert out["slices"][0]["chain_complete"] is False


def test_get_options_chain_taglio_centrato_sullo_spot_e_dichiarato(monkeypatch):
    e = dte(20)
    pages = _snap_pages(650.0, list(range(400, 751)), e, 20 / 365)
    calls = {"n": 0}

    def get(path, params=None):
        i = calls["n"]; calls["n"] += 1
        return {"results": pages[i], **({"next_url": f"https://x?cursor=p{i + 1}"} if i + 1 < len(pages) else {})}
    monkeypatch.setattr(provider, "_get", get)
    out = provider.get_options_chain("SPY", e, max_contracts=400, spot=650.0)
    strikes = [c["strike"] for c in out["chain"]]
    assert len(strikes) == 400 and min(strikes) == 550 and max(strikes) == 749
    assert sum(1 for c in out["chain"] if c["type"] == "call") == 200
    cov = out["coverage"]
    assert cov["status"] == "PARTIAL" and "output_limit" in cov["issues"]
    assert cov["truncation"]["applied"] and cov["truncation"]["reference"] == 650.0
    assert cov["truncation"]["rows_dropped"] == 302
    assert out["_timestamp"].endswith("+00:00")


def test_get_options_chain_senza_spot_usa_il_sottostante_osservato_poi_la_mediana(monkeypatch):
    e = dte(20)
    under = {"ticker": "SPY", "price": 700.0, "last_updated": 1_700_000_000_000_000_000}
    for pages, ref, src in ((_snap_pages(650.0, list(range(400, 751)), e, 20 / 365, under), 700.0, "underlying"),
                            (_snap_pages(650.0, list(range(400, 751)), e, 20 / 365), 575.0, "mediana")):
        calls = {"n": 0}

        def get(path, params=None, pages=pages, calls=calls):
            i = calls["n"]; calls["n"] += 1
            return {"results": pages[i], **({"next_url": f"https://x?cursor=p{i + 1}"} if i + 1 < len(pages) else {})}
        monkeypatch.setattr(provider, "_get", get)
        out = provider.get_options_chain("SPY", e, max_contracts=400)
        t = out["coverage"]["truncation"]
        assert t["reference"] == ref and src in t["reference_source"]
        strikes = [c["strike"] for c in out["chain"]]
        assert min(strikes) < ref < max(strikes)


def test_get_options_chain_porta_quota_rettifica_e_moltiplicatore(monkeypatch):
    e = dte(20)
    raw = {"details": {"ticker": "O:A", "contract_type": "call", "strike_price": 100,
                       "expiration_date": e, "shares_per_contract": 10,
                       "additional_underlyings": [{"ticker": "B"}]},
           "last_quote": {"bid": 1.0, "ask": 1.2, "last_updated": 1_791_000_000_000_000_000,
                          "timeframe": "DELAYED"},
           "implied_volatility": .3}
    monkeypatch.setattr(provider, "_get", lambda *a, **k: {"results": [raw]})
    c = provider.get_options_chain("SPY", e)["chain"][0]
    assert (c["bid"], c["ask"], c["multiplier"], c["adjusted"], c["quote_timeframe"]) == (1.0, 1.2, 10.0, True, "DELAYED")
    assert c["quote_timestamp"].endswith("+00:00")


# ---- M7: selezione scadenze che tiene la coda lunga -------------------------

def _spy_calendar():
    out = [dte(d) for d in range(0, 61) if (NOW_NY.date() + timedelta(days=d)).weekday() < 5]
    d = NOW_NY.date().replace(day=1)
    for _ in range(14):                          # terzi venerdi' per 14 mesi
        first_fri = d + timedelta(days=(4 - d.weekday()) % 7)
        out.append((first_fri + timedelta(days=14)).isoformat())
        d = (d + timedelta(days=32)).replace(day=1)
    return sorted(set(out))


def test_M7_tetto_4_tiene_front_coda_e_cavallo_dei_30_giorni():
    rows = vol._select_expiries(_spy_calendar(), 4, 120)
    days = [r["days"] for r in rows]
    assert len(rows) == 4 and days == sorted(days) and days[0] == 2
    all_days = [(date.fromisoformat(e) - NOW_NY.date()).days for e in _spy_calendar()]
    assert days[-1] == max(d for d in all_days if d <= 120) == 100   # coda lunga (prima tagliata)
    assert any(d <= 30 for d in days[:-1]) and any(30 <= d for d in days[1:])
    assert rows[-1]["kind"] == "monthly_standard"
    # tetto 3: la coda e' tenuta dalla PROTEZIONE, non dalla centralita' del taglio
    assert [r["days"] for r in vol._select_expiries(_spy_calendar(), 3, 120)] == [2, 30, 100]


def test_M7_tetto_6_mensili_standard_e_nessun_quasi_doppione():
    rows = vol._select_expiries(_spy_calendar(), 6, 120)
    days = [r["days"] for r in rows]
    assert len(rows) == 6 and days[-1] == 100
    assert all(r["kind"] == "monthly_standard" for r in rows if r["days"] > 42)
    assert all(date.fromisoformat(r["expiry"]).weekday() == 4 for r in rows)


# ---- A2: IV rank su IV a scadenza costante (E6) ---------------------------

def _iv_db(tmp_path):
    db = tmp_path / "iv.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE iv_history (ticker, snap_date, expiry, days, atm_iv, rr25, bf25, spot, spot_source, source)")
    return db, c


def test_E6_rank_stabile_con_term_structure_identica(tmp_path):
    from bellomberg.market_data import iv_history as ih
    db, c = _iv_db(tmp_path)
    term = lambda d: 0.15 + 0.002 * d
    d0 = date(2026, 9, 1)
    for i in range(20):
        front = 6 - (i % 5)                     # il front scorre 6..2 e salta
        for dd in (front, front + 7, 30):
            c.execute("INSERT INTO iv_history VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("SPY", (d0 + timedelta(days=i)).isoformat(), f"e{dd}", dd, term(dd), None, None, None, None, "t"))
    c.commit(); c.close()
    ctx = ih.get_iv_context("SPY", db_path=str(db))
    assert ctx["error"] is None and ctx["tenor_days"] == 30
    assert ctx["iv_30d_current"] == pytest.approx(0.21) and ctx["iv_min"] == ctx["iv_max"] == pytest.approx(0.21)
    assert ctx["iv_percentile"] == 100.0 and ctx["n_obs"] == 20
    assert ctx["iv_front_percentile"] == pytest.approx(20.0)   # il difetto resta visibile solo come secondario


def test_rank_stabile_quando_nessuna_scadenza_cade_a_30_giorni(tmp_path):
    from bellomberg.market_data import iv_history as ih
    db, c = _iv_db(tmp_path)
    w = lambda T: 0.0005 + 0.04 * T / 365     # varianza totale lineare in T: interpolazione esatta
    d0 = date(2026, 9, 1)
    for i in range(25):
        front = 9 - (i % 7)
        for dd in range(front, front + 60, 7):
            T = dd / 365
            c.execute("INSERT INTO iv_history VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("SPY", (d0 + timedelta(days=i)).isoformat(), f"e{dd}", dd, sqrt(w(dd) / T), None, None, None, None, "t"))
    c.commit(); c.close()
    ctx = ih.get_iv_context("SPY", db_path=str(db))
    expected = sqrt(w(30) / (30 / 365))
    assert ctx["iv_30d_current"] == pytest.approx(expected, abs=1e-4)
    assert ctx["iv_max"] - ctx["iv_min"] < 1e-4 and ctx["iv_percentile"] == 100.0


def test_iv_30d_interpolazione_in_varianza_totale_numerica():
    from bellomberg.market_data.iv_history import constant_maturity_iv
    out = constant_maturity_iv([(7, 0.30), (20, 0.20), (41, 0.25), (90, 0.27)])
    w20, w41 = 0.20 ** 2 * 20, 0.25 ** 2 * 41
    assert out["iv"] == pytest.approx(sqrt((w20 + (w41 - w20) * 10 / 21) / 30), abs=1e-12)
    assert out["iv"] == pytest.approx(0.23376, abs=1e-5)
    assert (out["days_low"], out["days_high"]) == (20, 41)
    nd = constant_maturity_iv([(7, 0.3), (20, 0.2)])
    assert nd["iv"] is None and "sopra" in nd["reason"]


def test_ultima_foto_senza_cavallo_dei_30g_rank_nd_non_quello_di_ieri(tmp_path):
    from bellomberg.market_data import iv_history as ih
    db, c = _iv_db(tmp_path)
    for day, rows in (("2026-09-01", ((20, .2), (40, .25))), ("2026-09-02", ((20, .21), (40, .26))),
                      ("2026-09-03", ((5, .3), (12, .3)))):
        for dd, iv in rows:
            c.execute("INSERT INTO iv_history VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("SPY", day, f"e{dd}", dd, iv, None, None, None, None, "t"))
    c.commit(); c.close()
    ctx = ih.get_iv_context("SPY", db_path=str(db))
    assert ctx.get("iv_percentile") is None and "2026-09-03" in ctx["error"]
    assert ctx["n_days_excluded_no_bracket"] == 1 and ctx["n_obs"] == 2


def test_collector_marca_le_scadenze_parziali_e_il_rank_le_conta(tmp_path, monkeypatch):
    from bellomberg.market_data import iv_history as ih
    from bellomberg.storage.memory_db import SCHEMA_SQL
    path = str(tmp_path / "s.db")
    conn = sqlite3.connect(path); conn.executescript(SCHEMA_SQL); conn.close()

    def fake(t, **kw):
        return {"term_structure": [{"expiry": "a", "days": 20, "atm_iv": .2}, {"expiry": "b", "days": 40, "atm_iv": .25}],
                "coverage": {"rows": [{"expiry": "b", "status": "partial", "reason": "HTTP 502"}]}}
    monkeypatch.setattr("bellomberg.portfolio.vol_surface.build_vol_surface", fake)
    for day in ("2026-09-01", "2026-09-02"):
        ih.save_daily_snapshot(db_path=path, tickers=("SPY",), snap_date=day)
    src = [r[0] for r in sqlite3.connect(path).execute("SELECT source FROM iv_history WHERE expiry='b'")]
    assert all("PARTIAL: HTTP 502" in s and s.startswith("vol_surface v1.6") for s in src)
    # MA-2 (review v2): di default le righe PARTIAL sono FUORI dal rank, contate
    ctx = ih.get_iv_context("SPY", db_path=path, min_obs=2)
    assert ctx.get("iv_percentile") is None and "righe escluse" in ctx["error"] and "partial" in ctx["error"]
    assert ctx["excluded_days"]["partial"] == 2 and ctx["excluded_rows"]["partial"] == 2
    # diagnostica dichiarata: rimesse dentro, si contano come prima
    diag = ih.get_iv_context("SPY", db_path=path, min_obs=2, include_flagged=True)
    assert diag["flagged_included"] is True
    assert diag["n_obs_partial"] == 2 and diag["current_partial"] is True and diag["n_obs_legacy_v15"] == 0


# ---- M8 / B1 / B2 / M6: orizzonti coerenti e orari dichiarati -----------------

def _flat_chain(expiry, iv, S=100.0, stamp="2026-10-07T15:50:00+00:00", timeframe="DELAYED"):
    return {"chain": [{"contract": f"O:{expiry}{t}{K}", "type": t, "strike": float(K), "expiry": expiry,
                       "iv": iv, "delta": None, "oi": 10, "volume": 1, "bid": 1.0, "ask": 1.05,
                       "quote_timestamp": stamp, "quote_timeframe": timeframe}
                      for K in range(80, 125, 5) for t in ("call", "put")],
            "complete": True, "spot": None}


def test_M8_iv_rv_sullo_stesso_orizzonte_e_alias_deprecati(monkeypatch):
    import numpy as np
    closes = [100.0]
    for i in range(80):
        closes.append(closes[-1] * (1.01 if i % 3 else 0.985))
    yf_spot(monkeypatch, 100.0, closes)
    ivs = {dte(20): 0.20, dte(41): 0.25, dte(70): 0.27}
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, e, cursor=None: _flat_chain(e, ivs[e]))
    out = vol.build_vol_surface("XX01", expiries=list(ivs), include_context=True)
    rets = np.diff(np.array(closes)) / np.array(closes[:-1])
    rv21 = float(np.std(rets[-21:], ddof=1) * sqrt(252))
    w20, w41 = .04 * 20, .0625 * 41
    iv30 = sqrt((w20 + (w41 - w20) * 10 / 21) / 30)
    assert out["iv_30d"] == pytest.approx(round(iv30, 4)) and out["iv_30d_bracket_days"] == [20, 41]
    assert out["realized_vol_21d"] == pytest.approx(round(rv21, 4))
    assert out["iv_rv_spread_30d"] == pytest.approx(round(round(iv30, 4) - rv21, 4))
    assert out["iv_rv_spread_front"] == out["iv_rv_spread_30d"]       # alias deprecato dichiarato
    assert out["realized_vol_30d"] == out["realized_vol_21d"]
    assert set(out["deprecated_fields"]) == {"iv_rv_spread_front", "realized_vol_30d"}
    # B1: la pendenza misura davvero 60 giorni (interpolata fra 41 e 70)
    w70 = .27 ** 2 * 70
    iv60 = sqrt((w41 + (w70 - w41) * 19 / 29) / 60)
    assert out["term_slope_front_to_60d"] == pytest.approx(round(round(iv60, 4) - 0.20, 4))
    assert out["expected_move_days"] == 30
    assert out["expected_move_pct"] == pytest.approx(round(round(iv30, 4) * sqrt(30 / 365) * 100, 1))
    # M6: orari dichiarati, fuso esplicito
    assert datetime.fromisoformat(out["_timestamp"]).utcoffset() == timedelta(0)
    assert out["quote_time_min"] == out["quote_time_max"] == "2026-10-07T15:50:00+00:00"
    assert out["data_delay"] == "DELAYED" and out["spot_timestamp"] and out["spot_timestamp_kind"]
    assert out["market_session"]["market_open"] is True and out["market_session"]["session_date"] == "2026-10-07"
    assert out["snapshot_kind"] == "new_fetch"


def test_M6_weekend_dichiara_la_seduta_del_venerdi(monkeypatch):
    sat = datetime(2026, 10, 10, 11, 0, tzinfo=NY)
    s = vol._market_session(sat)
    assert s["market_open"] is False and s["session_date"] == "2026-10-09"
    assert vol._market_session(datetime(2026, 10, 12, 8, 0, tzinfo=NY))["session_date"] == "2026-10-09"
    assert vol._market_session(datetime(2026, 10, 12, 17, 0, tzinfo=NY))["session_date"] == "2026-10-12"


def test_B3_giorni_e_tempo_a_scadenza_sul_calendario_di_new_york():
    # 20:30 New York = 02:30 italiane del giorno dopo: la data locale darebbe 6 giorni
    late = datetime(2026, 10, 9, 20, 30, tzinfo=NY)
    assert vol._days_to_expiry("2026-10-16", late) == 7
    t = vol._t_years("2026-10-16", datetime(2026, 10, 9, 15, 0, tzinfo=NY))
    assert t == pytest.approx((7 + 1 / 24) / 365, abs=1e-9)
    assert vol._t_years("2026-10-09", datetime(2026, 10, 9, 17, 0, tzinfo=NY)) == 0.0


# ---- B4 / B5 / A4 --------------------------------------------------------------

def test_B4_scadenza_senza_contratti_motivo_giusto(monkeypatch):
    yf_spot(monkeypatch, 100.0)
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, e, cursor=None: {"chain": [], "complete": True})
    out = vol.build_vol_surface("XX01", expiries=[dte(9)], include_context=False)
    r = out["coverage"]["rows"][0]
    assert r["status"] == "excluded" and "nessun contratto listato" in r["reason"] and r["n_contracts"] == 0


def test_B5_stop_dichiarato_dopo_429(monkeypatch):
    yf_spot(monkeypatch, 100.0)
    asked = []

    def chain(t, e, cursor=None):
        asked.append(e)
        return {"error": "HTTP 429", "chain": []}
    monkeypatch.setattr(vol, "get_chain_detail", chain)
    dates = [dte(9), dte(16), dte(23)]
    out = vol.build_vol_surface("XX01", expiries=dates, include_context=False)
    assert asked == [dates[0]]
    rows = out["coverage"]["rows"]
    assert [r["status"] for r in rows] == ["error"] * 3
    assert all("429" in r["reason"] for r in rows[1:]) and out["coverage"]["stopped_after_rate_limit"]


def test_A4_tetto_scadenze_esplicite_422(monkeypatch):
    from fastapi import HTTPException
    import bellomberg.api.bellomberg_api as api
    dates = [dte(9 + 7 * i) for i in range(vol.MAX_FETCH_EXPIRIES + 1)]
    monkeypatch.setattr(vol, "get_chain_detail", lambda *a, **k: pytest.fail("refetch oltre il tetto"))
    with pytest.raises(HTTPException) as exc:
        api.get_vol_surface("XX01", expiries=",".join(dates))
    assert exc.value.status_code == 422 and "download" in str(exc.value.detail)


def test_A4_contesto_sull_istantanea_del_job_senza_refetch(monkeypatch):
    from bellomberg.portfolio import options_download as od
    from bellomberg.portfolio import positioning_tools as pt
    from bellomberg.market_data import iv_history as ih
    mgr = od.OptionsDownloadManager()
    e = dte(20)
    contracts = {f"O:{t}{K}": {**c, "contract": f"O:{t}{K}"} for c in _flat_chain(e, 0.22)["chain"]
                 for t, K in [(c["type"], c["strike"])]}
    job = {"id": "j1", "ticker": "XX01", "expirations": [e], "download_complete": True,
           "spot": 100.0, "spot_source": "yfinance lastPrice", "spot_timestamp": "2026-10-07T15:51:00+00:00",
           "spot_timeframe": None, "snapshot_at": "2026-10-07T15:52:00+00:00", "updated_at": "x",
           "_rows": {e: {"data": contracts, "complete": True, "error": None}},
           "_worker": False, "_access": 0.0}
    from time import monotonic
    job["_access"] = monotonic()
    mgr._jobs["j1"] = job
    monkeypatch.setattr(vol, "get_chain_detail", lambda *a, **k: pytest.fail("chain riscaricata"))
    monkeypatch.setattr(provider, "get_options_chain", lambda *a, **k: pytest.fail("chain riscaricata"))
    monkeypatch.setattr(ih, "get_iv_context", lambda t, **k: {"error": "stub"})
    monkeypatch.setattr(pt, "compute_gex", lambda t, **k: {"error": "stub gex"})
    closes = [100 * (1.01 if i % 2 else 0.99) ** (i % 2) for i in range(60)]
    yf_spot(monkeypatch, 999.0, closes)
    out = mgr.context("j1")
    assert out["snapshot_at"] == "2026-10-07T15:52:00+00:00" and out["snapshot_kind"] == "download_snapshot"
    assert out["spot_est"] == 100.0 and out["spot_timestamp"] == "2026-10-07T15:51:00+00:00"
    assert out["slices"][0]["atm_iv"] == pytest.approx(0.22)
    assert out["iv_history_context"] == {"error": "stub"} and out["gex"] == {"error": "stub gex"}
    assert out["realized_vol_21d"] is not None
    assert "gex_fetched_at" in out["context_sources"] and out["context_sources"]["surface"]


def test_A4_rotta_contesto_registrata():
    from bellomberg.api.options_routes import create_options_router
    router = create_options_router(lambda: None)
    assert "/options/download/{job_id}/context" in {r.path for r in router.routes}


# ---- SPOT: Polygon primario, yfinance ripiego, proxy n.d. (ordine PM 10/10) ------

def _chain_with_underlying(expiry, price, ns=1_791_400_000_000_000_000):
    page = _flat_chain(expiry, 0.25, S=price)
    page.update(spot=price, spot_timestamp_ns=ns, spot_timeframe="DELAYED")
    return page


def test_spot_primario_polygon_della_stessa_istantanea_yfinance_non_letto(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: pytest.fail("yfinance letto con Polygon disponibile")))
    e = dte(20)
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, x, cursor=None: _chain_with_underlying(x, 101.0))
    out = vol.build_vol_surface("XX01", expiries=[e], include_context=False)
    assert out["spot_est"] == 101.0 and out["spot_source"] == "Polygon underlying snapshot"
    assert out["spot_qualified"] is True and out["spot_fallback"] is False
    assert out["spot_timestamp"] == datetime.fromtimestamp(1_791_400_000_000_000_000 / 1e9, timezone.utc).isoformat()
    assert out["spot_timeframe"] == "DELAYED" and out["metrics_qualified"] is True


def test_spot_yfinance_solo_ripiego_dichiarato(monkeypatch):
    yf_spot(monkeypatch, 100.0)
    e = dte(20)
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, x, cursor=None: _flat_chain(x, 0.25))
    out = vol.build_vol_surface("XX01", expiries=[e], include_context=False)
    assert out["spot_source"] == "yfinance lastPrice" and out["spot_fallback"] is True
    assert out["spot_qualified"] is False and out["slices"][0]["atm_iv"] == pytest.approx(0.25)


def test_spot_proxy_metriche_nd_e_storico_non_scritto(tmp_path, monkeypatch):
    from bellomberg.market_data import iv_history as ih
    from bellomberg.storage.memory_db import SCHEMA_SQL
    e = dte(20)
    monkeypatch.setattr(provider, "get_option_expirations", lambda t: {"expirations": [e]})
    page = _flat_chain(e, 0.25)
    for c in page["chain"]:
        c["delta"] = 0.5 if (c["type"] == "call" and c["strike"] == 100.0) else (0.9 if c["type"] == "call" else -0.5)
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, x, cursor=None: page)
    out = vol.build_vol_surface("XX01", include_context=False)
    assert out["spot_proxy"] == "proxy_strike_delta50" and out["metrics_qualified"] is False
    s = out["slices"][0]
    assert s["atm_iv"] is None and s["atm_iv_unqualified"] == pytest.approx(0.25)
    assert out["iv_30d"] is None and out["expected_move_pct"] is None
    path = str(tmp_path / "p.db")
    conn = sqlite3.connect(path); conn.executescript(SCHEMA_SQL); conn.close()
    res = ih.save_daily_snapshot(db_path=path, tickers=("XX01",), snap_date="2026-10-07")
    assert "spot proxy" in res["errors"]["XX01"] and res["saved"] == {}
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM iv_history").fetchone()[0] == 0


def test_download_spot_dalle_pagine_polygon_senza_yfinance(monkeypatch):
    from bellomberg.portfolio import options_download as od
    from time import monotonic, sleep
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: pytest.fail("yfinance letto prima/al posto di Polygon")))
    e = dte(20)
    rows = [{"details": {"ticker": f"O:T{t}{K}", "contract_type": t, "strike_price": K, "expiration_date": e},
             "implied_volatility": .2, "open_interest": 10, "day": {"volume": 1},
             "underlying_asset": {"ticker": "TEST", "price": 102.0, "last_updated": 1_791_400_000_000_000_000,
                                  "timeframe": "DELAYED"}}
            for K in range(80, 125, 5) for t in ("call", "put")]
    monkeypatch.setattr(provider, "_get", lambda *a, **k: {"results": rows})
    mgr = od.OptionsDownloadManager()
    job = mgr.start("TEST", [e])
    end = monotonic() + 5
    while mgr.status(job["id"])["state"] not in ("complete", "error") and monotonic() < end:
        sleep(.01)
    st = mgr.status(job["id"])
    assert st["state"] == "complete" and st["spot"] == 102.0 and st["spot_qualified"] is True
    out = mgr.surface(job["id"])
    assert out["spot_source"] == "Polygon underlying snapshot" and out["spot_qualified"] is True


# =============================================================================
# REVIEW v2 (10/10, Opus 5.5): MA-1..MA-3, M-4..M-8, rr25, bassi, mutanti M1-M13
# =============================================================================

QT = "2026-10-07T15:50:00+00:00"        # ora delle quote di _flat_chain (11:50 New York)


def _bars(start_utc, closes, tz="America/New_York"):
    """Barre 1m sintetiche con fuso (come yfinance intraday)."""
    import pandas as pd
    idx = pd.date_range(start_utc, periods=len(closes), freq="min", tz="UTC")
    return pd.DataFrame({"Close": closes}, index=idx.tz_convert(tz) if tz else idx.tz_localize(None))


def yf_bars(monkeypatch, bars, last=None, calls=None):
    def history(**k):
        if calls is not None:
            calls.append(k)
        if k.get("interval") != "1m":
            raise RuntimeError("nel test solo barre 1m")
        if bars is None:
            raise RuntimeError("barre assenti")
        return bars
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: SimpleNamespace(fast_info={"lastPrice": last}, history=history, earnings_dates=None)))


# ---- MA-1: spot = barra 1m allineata all'ora delle quote ---------------------

def test_MA1_spot_barra_1m_allineata_qualificata_senza_lastprice(monkeypatch):
    calls = []
    # barre 15:45..15:55 UTC, chiusura 100,0 + 0,1*i: la barra delle 15:50 chiude a 100,5
    yf_bars(monkeypatch, _bars("2026-10-07T15:45:00", [100.0 + 0.1 * i for i in range(11)]), last=999.0, calls=calls)
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, x, cursor=None: _flat_chain(x, 0.25))
    out = vol.build_vol_surface("XX01", expiries=[dte(20)], include_context=False)
    assert out["spot_est"] == pytest.approx(100.5)
    assert out["spot_source"] == "yfinance 1m bar aligned to quote time"
    assert out["spot_qualified"] is True and out["spot_fallback"] is False
    assert out["spot_timestamp"] == "2026-10-07T15:50:00+00:00"
    al = out["spot_alignment"]
    assert al["anchor_quote_time"] == QT and al["offset_seconds"] == 0 and al["tolerance_seconds"] == 120
    assert al["rule"] and calls and calls[0]["interval"] == "1m"
    assert out["iv_grid_qualified"] is True and out["slices"][0]["iv_grid_qualified"] is True


def test_MA1_barra_fuori_tolleranza_ripiego_lastprice_non_qualificato(monkeypatch):
    # barre solo fino alle 15:46: la piu' vicina e' a 240 s dall'ora delle quote
    yf_bars(monkeypatch, _bars("2026-10-07T15:40:00", [99.0] * 7), last=101.0)
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, x, cursor=None: _flat_chain(x, 0.25))
    out = vol.build_vol_surface("XX01", expiries=[dte(20)], include_context=False)
    assert out["spot_est"] == 101.0 and out["spot_source"] == "yfinance lastPrice"
    assert out["spot_qualified"] is False and out["spot_fallback"] is True
    assert out["spot_alignment"]["offset_seconds"] == 240
    assert "tolleranza" in str(out["spot_alignment"]["reason"]) and "tolleranza" in str(out["spot_reason"])
    # M-5: griglia costruita su K/S di uno spot non qualificato = non qualificata
    assert out["iv_grid_qualified"] is False and out["slices"][0]["iv_grid_qualified"] is False


def test_MA1_resolve_spot_regole_dichiarate():
    from datetime import timezone as tz
    from bellomberg.market_data import spot_alignment as sa
    anchor = datetime(2026, 10, 7, 15, 50, 30, tzinfo=tz.utc)
    loader = lambda bars: (lambda t, a, b: bars)
    last = lambda t: 77.0
    # parita' a 60 s: la barra PRECEDENTE (mai uno sguardo al futuro a parita')
    tie = _bars("2026-10-07T15:49:00", [10.0, float("nan"), 12.0])
    r = sa.resolve_spot("X", anchor=anchor, bars_loader=loader(tie), last_price_loader=last)
    assert r["spot"] == 10.0 and r["spot_alignment"]["offset_seconds"] == 60 and r["spot_qualified"] is True
    # bordo: 120 s esatti qualificato, 180 s no (ripiego lastPrice dichiarato)
    r = sa.resolve_spot("X", anchor=anchor, bars_loader=loader(_bars("2026-10-07T15:52:00", [11.0])), last_price_loader=last)
    assert r["spot"] == 11.0 and r["spot_qualified"] is True
    r = sa.resolve_spot("X", anchor=anchor, bars_loader=loader(_bars("2026-10-07T15:53:00", [11.0])), last_price_loader=last)
    assert r["spot"] == 77.0 and r["spot_qualified"] is False and r["spot_fallback"] is True
    # barre senza fuso: non collocabili, mai indovinate
    r = sa.resolve_spot("X", anchor=anchor, bars_loader=loader(_bars("2026-10-07T15:50:00", [11.0], tz=None)), last_price_loader=last)
    assert r["spot"] == 77.0 and r["spot_alignment"]["bar_start"] is None
    # niente ancora, niente lastPrice: None col motivo (il chiamante decide il proxy)
    r = sa.resolve_spot("X", anchor=None, bars_loader=loader(tie), last_price_loader=lambda t: None)
    assert r["spot"] is None and "assente" in str(r["spot_reason"])
    # prezzo Polygon con orario: primario, barre mai lette
    boom = lambda *a: pytest.fail("barre lette con prezzo Polygon presente")
    r = sa.resolve_spot("X", underlying={"price": 50.0, "timestamp_ns": 1_791_400_000_000_000_000}, anchor=anchor,
                        bars_loader=boom, last_price_loader=boom)
    assert r["spot"] == 50.0 and r["spot_source"] == "Polygon underlying snapshot" and r["spot_qualified"] is True
    # M9: prezzo Polygon SENZA orario = non qualificato
    r = sa.resolve_spot("X", underlying={"price": 50.0}, anchor=anchor, bars_loader=boom, last_price_loader=boom)
    assert r["spot_qualified"] is False and r["spot_timestamp"] is None


def test_MA1_ancora_robusta_a_un_contratto_con_orologio_avanti():
    from bellomberg.market_data.spot_alignment import quote_anchor
    stamps = [QT] * 10 + ["2026-10-07T16:10:00+00:00"]
    assert quote_anchor(stamps).isoformat() == QT
    spread = [f"2026-10-07T15:{m:02d}:00+00:00" for m in range(40, 51)]
    assert quote_anchor(spread).isoformat() == QT                     # quote_time_max vero
    assert quote_anchor([None, "x"]) is None


def test_MA1_download_spot_da_barra_allineata(monkeypatch):
    from bellomberg.portfolio import options_download as od
    from time import monotonic
    e = dte(20)
    contracts = {c["contract"]: c for c in _flat_chain(e, 0.22)["chain"]}
    yf_bars(monkeypatch, _bars("2026-10-07T15:48:00", [100.0, 100.2, 100.4, 100.6]), last=999.0)
    mgr = od.OptionsDownloadManager()
    mgr._jobs["j2"] = {"id": "j2", "ticker": "XX01", "expirations": [e], "download_complete": True,
                       "spot": None, "spot_source": None, "spot_timestamp": None, "spot_timeframe": None,
                       "spot_qualified": False, "spot_fallback": False, "spot_alignment": None, "spot_error": None,
                       "snapshot_at": "2026-10-07T15:52:00+00:00", "updated_at": "x",
                       "_rows": {e: {"data": contracts, "complete": True, "error": None}},
                       "_worker": False, "_access": monotonic(), "_spot_attempted": False, "_pause": False}
    out = mgr.surface("j2")
    assert out["spot_est"] == pytest.approx(100.4) and out["spot_qualified"] is True
    assert out["spot_source"] == "yfinance 1m bar aligned to quote time"
    assert out["spot_alignment"]["offset_seconds"] == 0 and out["iv_grid_qualified"] is True


# ---- M1 (mutante): spot Polygon = l'osservazione PIU' RECENTE; stessa pagina ---

def test_M1_spot_polygon_piu_recente_fra_le_scadenze(monkeypatch):
    e1, e2 = dte(20), dte(41)
    pages = {e1: _chain_with_underlying(e1, 100.0, ns=1_791_400_000_000_000_000),
             e2: _chain_with_underlying(e2, 101.0, ns=1_791_400_060_000_000_000)}
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, x, cursor=None: pages[x])
    out = vol.build_vol_surface("XX01", expiries=[e1, e2], include_context=False)
    assert out["spot_est"] == 101.0
    assert out["spot_timestamp"] == datetime.fromtimestamp(1_791_400_060, timezone.utc).isoformat()


def test_complete_chain_prezzo_e_orario_dalla_stessa_pagina(monkeypatch):
    e = dte(20)
    T = 1_791_400_000_000_000_000
    base = _flat_chain(e, 0.25)["chain"]

    def pages(p3_price, p3_ns):
        book = {None: {"chain": base[:6], "spot": 570.0, "spot_timestamp_ns": T, "spot_timeframe": "DELAYED",
                       "next_cursor": "c2", "complete": False},
                "c2": {"chain": base[6:12], "spot": None, "spot_timestamp_ns": None, "next_cursor": "c3", "complete": False},
                "c3": {"chain": base[12:], "spot": p3_price, "spot_timestamp_ns": p3_ns, "spot_timeframe": "REAL-TIME",
                       "next_cursor": None, "complete": True}}
        return lambda t, x, cursor=None: book[cursor]
    monkeypatch.setattr(vol, "get_chain_detail", pages(575.0, T - 60_000_000_000))   # pagina 3 PIU' VECCHIA
    out = vol._complete_chain("XX01", e)
    assert (out["spot"], out["spot_timestamp_ns"], out["spot_timeframe"]) == (570.0, T, "DELAYED")
    monkeypatch.setattr(vol, "get_chain_detail", pages(575.0, T + 60_000_000_000))   # pagina 3 piu' recente
    out = vol._complete_chain("XX01", e)
    assert (out["spot"], out["spot_timestamp_ns"], out["spot_timeframe"]) == (575.0, T + 60_000_000_000, "REAL-TIME")
    assert len(out["chain"]) == len(base) and out["complete"] is True


# ---- MA-2: rank senza righe v1.5 / PARTIAL / SPOT_FALLBACK -------------------

def test_MA2_rank_esclude_le_righe_v15_e_dichiara_i_conteggi(tmp_path):
    from bellomberg.market_data import iv_history as ih
    db, c = _iv_db(tmp_path)
    d0, ts = date(2026, 8, 3), (lambda days: 0.18 + 0.0005 * days)
    n_legacy = n_clean = 0
    for i in range(75):
        d = d0 + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        legacy = i < 30
        n_legacy += legacy
        n_clean += not legacy
        src = ih.LEGACY_SOURCE_PREFIX + " (Polygon chains)" if legacy else ih.SOURCE_TAG
        for days in ((3, 38, 45, 56) if legacy else (7, 28, 35, 98)):
            iv = ts(days) + (0.035 if legacy else 0.0)          # v1.5: ATM +3,5 punti (troncamento)
            c.execute("INSERT INTO iv_history VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("SPY", d.isoformat(), f"e{days}", days, iv, None, None, None, None, src))
    c.commit(); c.close()
    ctx = ih.get_iv_context("SPY", db_path=str(db))
    assert ctx["error"] is None and ctx["iv_percentile"] == 100.0       # term structure identica: rank pieno
    assert ctx["n_obs"] == n_clean == 33 and ctx["excluded_days"]["legacy_v15"] == n_legacy == 22
    assert ctx["excluded_rows"]["legacy_v15"] == 4 * n_legacy and ctx["iv_min"] == ctx["iv_max"]
    diag = ih.get_iv_context("SPY", db_path=str(db), include_flagged=True)
    assert diag["iv_percentile"] == pytest.approx(60.0) and diag["n_obs_legacy_v15"] == 22   # il difetto


def test_MA2_M3_spot_fallback_marcato_dal_collector_e_letto_dal_rank(tmp_path, monkeypatch):
    from bellomberg.market_data import iv_history as ih
    from bellomberg.storage.memory_db import SCHEMA_SQL
    path = str(tmp_path / "f.db")
    conn = sqlite3.connect(path); conn.executescript(SCHEMA_SQL); conn.close()
    state = {"fallback": True}
    monkeypatch.setattr("bellomberg.portfolio.vol_surface.build_vol_surface", lambda t, **kw: {
        "term_structure": [{"expiry": "a", "days": 20, "atm_iv": .2}, {"expiry": "b", "days": 40, "atm_iv": .25}],
        "spot_fallback": state["fallback"], "coverage": {"rows": []}})
    days = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    days = [d for d in days if date.fromisoformat(d).weekday() < 5]
    for d in days[:5]:
        ih.save_daily_snapshot(db_path=path, tickers=("SPY",), snap_date=d)
    src = {r[0] for r in sqlite3.connect(path).execute("SELECT source FROM iv_history")}
    assert src == {ih.SOURCE_TAG + ih.SPOT_FALLBACK_MARK + "yfinance"}
    state["fallback"] = False
    for d in days[5:]:
        ih.save_daily_snapshot(db_path=path, tickers=("SPY",), snap_date=d)
    ctx = ih.get_iv_context("SPY", db_path=path)
    assert ctx["excluded_days"]["spot_fallback"] == 5 and ctx["n_obs"] == len(days) - 5 == 17
    assert "insufficiente" in ctx["error"] and "minime 20" in ctx["error"]       # storia minima dichiarata
    ok = ih.get_iv_context("SPY", db_path=path, min_obs=10)
    assert ok["error"] is None and ok["n_obs"] == 17 and ok["min_obs"] == 10


# ---- M-4: riferimento stale robusto ------------------------------------------

def test_M4_un_contratto_20_minuti_piu_nuovo_non_rende_stantie_le_altre():
    S, T = 100.0, 30 / 365
    chain = [row(80 + 2.5 * i, t, S, T, bid=1.0, ask=1.1, quote_timestamp=QT)
             for i in range(17) for t in ("call", "put")]
    chain[10] = dict(chain[10], quote_timestamp="2026-10-07T16:10:00+00:00")
    m = vol._slice_metrics(chain, S, T)
    assert m["quote_discards"] == {} and m["n_calls"] == m["n_puts"] == 17
    assert m["atm_iv"] == pytest.approx(0.20, abs=1e-4)
    # una quota davvero vecchia (2 ore) resta scartata
    chain[11] = dict(chain[11], quote_timestamp="2026-10-07T13:50:00+00:00")
    assert vol._slice_metrics(chain, S, T)["quote_discards"] == {"stale": 1}


# ---- M-5 / M10: proxy -> griglia e 25Δ n.d. ----------------------------------

def test_M5_M10_proxy_azzera_griglia_e_25d(monkeypatch):
    e = dte(20)
    T = vol._t_years(e, NOW_NY)
    chain = [dict(row(K, t, 100.0, T), expiry=e) for K in range(70, 135, 5) for t in ("call", "put")]
    monkeypatch.setattr(provider, "get_option_expirations", lambda t: {"expirations": [e]})
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, x, cursor=None: {"chain": chain, "complete": True})
    out = vol.build_vol_surface("XX01", include_context=False)
    assert out["spot_proxy"] == "proxy_strike_delta50" and out["spot_est"] == 100.0
    s = out["slices"][0]
    assert s["iv_grid"] == [None] * len(vol.MONEYNESS_GRID) and s["iv_grid_qualified"] is False
    assert s["iv_grid_unqualified"][8] == pytest.approx(0.20, abs=1e-4)
    assert s["iv_25d_call"] is None and s["iv_25d_put"] is None
    assert s["iv_25d_call_unqualified"] is not None and s["rr25_unqualified"] < 0
    assert out["iv_grid_qualified"] is False


# ---- M-6: 429 su una pagina di continuazione ---------------------------------

def test_M6_429_su_continuazione_ferma_e_tiene_il_parziale(monkeypatch):
    yf_spot(monkeypatch, 100.0)
    asked = []
    full = _flat_chain("x", 0.25)["chain"]

    def chain(t, e, cursor=None):
        asked.append((e, cursor))
        if cursor is None:
            return {"chain": [dict(c, expiry=e) for c in full], "complete": False, "next_cursor": "p2"}
        return {"error": "HTTP 429", "chain": []}
    monkeypatch.setattr(vol, "get_chain_detail", chain)
    dates = [dte(9), dte(16), dte(23)]
    out = vol.build_vol_surface("XX01", expiries=dates, include_context=False)
    assert asked == [(dates[0], None), (dates[0], "p2")]               # niente scadenze dopo il 429
    rows = out["coverage"]["rows"]
    assert rows[0]["status"] == "partial" and "429" in str(rows[0]["reason"])
    assert [r["status"] for r in rows[1:]] == ["error", "error"] and all("429" in str(r["reason"]) for r in rows[1:])
    assert out["coverage"]["stopped_after_rate_limit"] is True and out["partial"] is True


# ---- M-7: payload compatto del tool del comitato -----------------------------

def _surface_for_tool(monkeypatch):
    closes = [100.0 * (1.01 if i % 3 else 0.985) ** i for i in range(80)]
    yf_spot(monkeypatch, 100.0, closes)
    ivs = {dte(20): 0.20, dte(41): 0.25, dte(70): 0.27, dte(98): 0.28}
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, e, cursor=None: _flat_chain(e, ivs[e]))
    return vol.build_vol_surface("XX01", expiries=list(ivs), include_context=True)


def test_M7_tool_summary_numeri_e_stati_senza_testi_di_metodo(monkeypatch):
    import json
    from bellomberg.core.presentation import render_payload
    full = _surface_for_tool(monkeypatch)
    t = vol.tool_summary(full)
    for k in ("spot_qualified", "spot_fallback", "partial", "data_delay", "session_date", "iv_30d",
              "term_structure", "interpretation", "expected_move_pct", "iv_grid_qualified", "coverage"):
        assert k in t, k
    for k in ("spot_basis", "quality_filters", "rr25_method", "atm_method", "iv_model_note", "moneyness_basis",
              "realized_vol_30d", "iv_rv_spread_front", "deprecated_fields", "slices", "moneyness_grid"):
        assert k not in t, k
    assert t["term_structure"][0] == {k: full["term_structure"][0][k] for k in
                                      ("expiry", "days", "atm_iv", "rr25", "bf25", "pc_oi_ratio", "chain_complete")}
    assert t["session_date"] == "2026-10-07" and t["iv_30d"] == full["iv_30d"]
    size = lambda p: len(json.dumps(render_payload(p), ensure_ascii=False, default=str))
    before = size({k: v for k, v in full.items() if k not in ("slices", "moneyness_grid")})
    assert size(t) < 0.5 * before


def test_M7_chat_tool_usa_il_payload_compatto(monkeypatch):
    from bellomberg.agents import chat_tools
    from bellomberg.market_data import iv_history as ih
    full = _surface_for_tool(monkeypatch)
    monkeypatch.setattr(vol, "build_vol_surface", lambda t, **k: full)
    monkeypatch.setattr(ih, "get_iv_context", lambda t, **k: {"iv_30d_current": .2, "iv_percentile": 50.0,
                                                              "iv_min": .1, "iv_max": .3, "n_obs": 30,
                                                              "basis": "x" * 400, "error": None})
    out = chat_tools.dispatch("get_vol_surface_summary", {"ticker": "XX01"})
    data = out["data"]
    assert "spot_basis" not in data and "realized_vol_30d" not in data and data["spot_qualified"] is False
    assert data["iv_history_context"] == {"iv_30d_current": .2, "iv_percentile": 50.0, "iv_min": .1,
                                          "iv_max": .3, "n_obs": 30, "error": None}


# ---- M-8: spread di pochi tick ammesso ---------------------------------------

def test_M8_spread_tre_tick_ammesso_oltre_la_soglia_relativa():
    q = lambda b, a: vol._quote_defect({"iv": .5, "bid": b, "ask": a}, None)
    assert q(0.05, 0.20) is None                  # relativo 120%, 3 tick da 0,05: ammesso
    assert q(0.05, 0.25) == "wide_spread"         # 4 tick
    assert q(1.0, 3.5) == "wide_spread"           # relativo 111% e 50 tick
    assert q(0.0, 0.10) == "no_bid"


def test_M8_single_name_20_dollari_25d_disponibile():
    from math import exp
    sm = lambda m: 0.50 - 0.3 * (m - 1) + 0.8 * (m - 1) ** 2
    S, T = 20.0, 9 / 365

    def price(K, typ):
        s = sm(K / S)
        d1 = (log(S / K) + 0.5 * s * s * T) / (s * sqrt(T)); d2 = d1 - s * sqrt(T)
        N = lambda x: (1 + erf(x / sqrt(2))) / 2
        return S * N(d1) - K * N(d2) if typ == "call" else K * N(-d2) - S * N(-d1)
    chain = []
    for K in [x / 2 for x in range(20, 61)]:
        for typ in ("call", "put"):
            px = price(K, typ)
            bid = max(0.0, (int((px - 0.05) / 0.05)) * 0.05)
            ask = (int((px + 0.05) / 0.05) + 1) * 0.05
            chain.append(row(K, typ, S, T, iv=sm(K / S), bid=round(bid, 2), ask=round(ask, 2)))
    m = vol._slice_metrics(chain, S, T)
    assert m["rr25"] is not None and m["quote_discards"].get("wide_spread", 0) == 0
    rr_true = true25(S, T, "call", sm) - true25(S, T, "put", sm)
    assert abs(m["rr25"] - rr_true) < 0.002


def test_rr25_r_q_dichiarati_nel_payload(monkeypatch):
    out = _surface_for_tool(monkeypatch)
    assert out["rr25_rate_r"] == 0.0 and out["rr25_dividend_q"] == 0.0 and out["rr25_rate_note"]
    assert out["quality_filters"]["tick_reference"] == 0.05 and out["quality_filters"]["min_spread_ticks"] == 3
    assert out["quality_filters"]["stale_reference_percentile"] == 75


# ---- BASSI: cambio d'ora, festivi, mensile quasi-doppione ---------------------

def test_t_years_attraversa_il_cambio_d_ora_in_utc():
    t = vol._t_years("2026-11-06", datetime(2026, 10, 30, 12, 0, tzinfo=NY))
    assert t * 365 * 24 == pytest.approx(7 * 24 + 4 + 1, abs=1e-9)      # 01/11: +1 ora
    t = vol._t_years("2026-03-13", datetime(2026, 3, 6, 12, 0, tzinfo=NY))
    assert t * 365 * 24 == pytest.approx(7 * 24 + 4 - 1, abs=1e-9)      # 08/03: -1 ora


@pytest.mark.parametrize("now, session", [
    (datetime(2026, 11, 26, 12, 0), "2026-11-25"),     # Thanksgiving
    (datetime(2026, 12, 25, 12, 0), "2026-12-24"),     # Natale
    (datetime(2026, 4, 3, 12, 0), "2026-04-02"),       # Good Friday
    (datetime(2026, 7, 3, 12, 0), "2026-07-02"),       # Independence Day (osservato)
    (datetime(2027, 1, 1, 12, 0), "2026-12-31"),       # Capodanno
    (datetime(2026, 1, 2, 8, 0), "2025-12-31"),        # pre-apertura dopo Capodanno
    (datetime(2026, 11, 30, 8, 0), "2026-11-27"),      # lunedi' pre-apertura -> venerdi'
])
def test_market_session_festivi_nyse(now, session):
    s = vol._market_session(now.replace(tzinfo=NY))
    assert s["market_open"] is False and s["session_date"] == session
    assert "2025-2028" in str(s["holidays_basis"])


def test_market_session_fuori_dagli_anni_coperti_lo_dichiara():
    s = vol._market_session(datetime(2030, 11, 28, 12, 0, tzinfo=NY))
    assert "non modellati" in str(s["holidays_basis"]) and "non modellati" in str(s["note"])
    assert vol._market_session(datetime(2026, 11, 27, 12, 0, tzinfo=NY))["market_open"] is True


def test_select_expiries_mensile_non_quasi_doppione_e_venerdi_preferito():
    # 07/10: settimanali a venerdi' fino al 13/11 (37g), lunedi' 16/11 (40g);
    # novembre oltre i 42 giorni ha solo il giovedi' 19/11 (43g): quasi-doppione
    # del 16/11 -> fuori. Dicembre: martedi' 01/12 e venerdi' 11/12, niente 18/12.
    cal = [dte(d) for d in (2, 9, 16, 23, 30, 37, 40, 43)] + ["2026-12-01", "2026-12-11", "2027-01-15"]
    rows = vol._select_expiries(cal, 12, 120)
    got = {r["expiry"]: r["kind"] for r in rows}
    assert dte(43) not in got and dte(40) in got
    assert got.get("2026-12-11") == "monthly_friday" and "2026-12-01" not in got
    assert got["2027-01-15"] == "monthly_standard"


# ---- mutanti sopravvissuti alla review: M5, M7, M8, M11, M13, M6(download) ----

def test_M5_protetta_la_prima_scadenza_sopra_i_30_giorni():
    cal = [dte(d) for d in (2, 9, 16, 23, 37, 44)] + ["2026-12-18", "2027-01-15"]
    days = [r["days"] for r in vol._select_expiries(cal, 4, 120)]
    assert days[0] == 2 and days[-1] == 100 and 23 in days and 37 in days


def test_M7_cono_porta_le_scadenze_parziali(monkeypatch, tmp_path):
    from bellomberg.storage import negozi_privati
    import bellomberg.portfolio.vol_cone as vc
    p = tmp_path / "iv_tickers.json"
    p.write_text('{"tickers": ["SPY"]}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_IV", str(p))
    vc._CACHE.clear()
    monkeypatch.setattr(vc, "_fetch_closes", lambda t: [100.0 * 1.001 ** i for i in range(130)])
    monkeypatch.setattr(vol, "build_vol_surface", lambda t, **k: {
        "slices": [{"expiry": dte(20), "days": 20, "atm_iv": .2}],
        "coverage": {"complete": False, "partial_expiries": [dte(20)], "excluded": [], "errors": []}})
    out = vc.compute_vol_cone("SPY")
    vc._CACHE.clear()
    assert out["implied"]["partial_expiries"] == [dte(20)] and out["implied"]["coverage_complete"] is False


def test_M8_arbitraggio_di_calendario_segnalato():
    from bellomberg.market_data.iv_history import constant_maturity_iv
    assert constant_maturity_iv([(20, 0.30), (40, 0.20)])["calendar_arbitrage"] is True    # w 1,8 -> 1,6
    assert constant_maturity_iv([(20, 0.20), (40, 0.25)])["calendar_arbitrage"] is False


def test_M11_pendenza_nd_se_il_front_e_gia_a_60_giorni(monkeypatch):
    yf_spot(monkeypatch, 100.0)
    ivs = {dte(60): 0.22, dte(90): 0.25}
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, e, cursor=None: _flat_chain(e, ivs[e]))
    out = vol.build_vol_surface("XX01", expiries=list(ivs), include_context=False)
    assert out["iv_60d"] == pytest.approx(0.22)
    assert out["term_slope_front_to_60d"] is None and "60" in str(out["term_slope_reason"])


def test_M13_iv_oltre_il_range_scartata():
    S, T = 100.0, 30 / 365
    chain = [row(80 + 2.5 * i, t, S, T) for i in range(17) for t in ("call", "put")]
    chain[0] = dict(chain[0], iv=6.0)
    chain[1] = dict(chain[1], iv=4.99)
    m = vol._slice_metrics(chain, S, T)
    assert m["quote_discards"] == {"iv_out_of_range": 1}


def test_M6_download_polygon_sostituisce_il_ripiego_yfinance():
    from bellomberg.portfolio import options_download as od
    from time import monotonic
    mgr = od.OptionsDownloadManager()
    e = dte(20)
    job = {"id": "j3", "ticker": "XX01", "spot": 99.0, "spot_source": "yfinance lastPrice", "spot_qualified": False,
           "spot_fallback": True, "spot_alignment": {"offset_seconds": 400}, "_rows": {}, "expirations": [],
           "_created": monotonic(), "current_expiry": e, "updated_at": "x"}
    mgr._add_expiry(job, e)
    page = {"chain": [], "complete": True, "spot": 101.0, "spot_timestamp_ns": 1_791_400_000_000_000_000,
            "spot_timeframe": "DELAYED", "_timestamp": datetime.now(timezone.utc).isoformat()}
    mgr._accept_page(job, job["_rows"][e], None, page)
    assert job["spot"] == 101.0 and job["spot_source"] == "Polygon underlying snapshot"
    assert job["spot_qualified"] is True and job["spot_fallback"] is False and job["spot_alignment"] is None



def test_prezzo_della_protezione_cablato_in_superficie_sintesi_e_tool(monkeypatch):
    """v3 10/10 (revisione, mutazioni M3/M4/M5): build_vol_surface calcola il RAPPORTO IV/RV,
    lo consegna a _interpret (non la differenza) e lo espone nel payload e nel tool compatto,
    con la lettura dalle soglie condivise (core/soglie_score.prezzo_protezione)."""
    from bellomberg.core import soglie_score as soglie
    from bellomberg.core.language import language_context
    closes = [100.0]
    for i in range(80):
        closes.append(closes[-1] * (1.01 if i % 3 else 0.985))
    yf_spot(monkeypatch, 100.0, closes)
    ivs = {dte(20): 0.20, dte(41): 0.25, dte(70): 0.27}
    monkeypatch.setattr(vol, "get_chain_detail", lambda t, e, cursor=None: _flat_chain(e, ivs[e]))
    with language_context("it"):
        out = vol.build_vol_surface("XX01", expiries=list(ivs), include_context=True)
    atteso_codice, atteso_rapporto = soglie.prezzo_protezione(out["iv_30d"], out["realized_vol_21d"])
    assert out["iv_rv_ratio_30d"] == pytest.approx(round(atteso_rapporto, 4), abs=1e-3)
    assert out["protection_price"] == atteso_codice is not None
    # _interpret riceve il RAPPORTO: la frase cita lo stesso numero «x»
    assert "rapporto {:.2f}x".format(out["iv_rv_ratio_30d"]) in str(out["interpretation"])
    compatto = vol.tool_summary(out)
    assert compatto["protection_price"] == out["protection_price"]
    assert compatto["iv_rv_ratio_30d"] == out["iv_rv_ratio_30d"]
    # mutare la soglia cambia codice e frase insieme
    soglia = out["iv_rv_ratio_30d"] - 0.01
    monkeypatch.setattr(soglie, "VRP_CARA", soglia)
    monkeypatch.setattr(soglie, "VRP_SCONTO", min(soglie.VRP_SCONTO, soglia - 0.01))
    vol._CHAIN_CACHE.clear()
    with language_context("it"):
        cara = vol.build_vol_surface("XX01", expiries=list(ivs), include_context=True)
    assert cara["protection_price"] == "EXPENSIVE" and "CARE" in str(cara["interpretation"])
