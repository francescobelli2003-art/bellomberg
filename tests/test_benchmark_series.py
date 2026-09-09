"""Serie benchmark ufficiale (25/07) — offline: fetch moccato, payload TWR
iniettato, valori attesi CALCOLATI A MANO. Convenzioni della voce:
carry-forward CONTATO, testa senza dato ESCLUSA, errori DICHIARATI.
"""
import pytest

import bellomberg.market_data.benchmark_series as bsm
from bellomberg.market_data.benchmark_series import build_series, compute_benchmark_series


D = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]


def test_allineamento_perfetto_rebase_e_ritorni_a_mano():
    """px USD 100->110->121->133,1 con fx 1,25 fisso: EUR 80/88/96,8/106,48,
    indice 100/110/121/133,1, ritorni +10% ciascuno."""
    px = {d: v for d, v in zip(D, [100.0, 110.0, 121.0, 133.1])}
    fx = {d: 1.25 for d in D}
    out = build_series(D, px, fx, "SPY")
    assert out["error"] is None
    assert out["base_date"] == D[0]
    assert out["close_eur"] == pytest.approx([80.0, 88.0, 96.8, 106.48])
    assert out["index"] == pytest.approx([100.0, 110.0, 121.0, 133.1])
    assert out["ret_daily"] == pytest.approx([0.10, 0.10, 0.10])
    assert out["carried_days"] == 0
    assert out["leading_dropped"] == 0
    assert out["coverage_pct"] == pytest.approx(100.0)
    assert out["currency"] == "EUR" and out["total_return"] is True


def test_carry_forward_contato_non_zitto():
    """Buco al 3o giorno (festivita' USA): chiusura portata avanti, ritorno 0
    quel giorno, carried_days=1 e coverage 75% DICHIARATI."""
    px = {D[0]: 100.0, D[1]: 110.0, D[3]: 133.1}     # manca D[2]
    fx = {d: 1.25 for d in D}
    out = build_series(D, px, fx, "SPY")
    assert out["error"] is None
    assert out["close_eur"] == pytest.approx([80.0, 88.0, 88.0, 106.48])
    assert out["ret_daily"] == pytest.approx([0.10, 0.0, 0.21])
    assert out["carried_days"] == 1
    assert out["native_days"] == 3
    assert out["coverage_pct"] == pytest.approx(75.0)
    assert out["carried_flags"] == [False, False, True, False]  # QUALI, non solo quanti


def test_testa_senza_dato_esclusa_mai_inventata():
    """Benchmark disponibile solo dal 3o giorno TWR: i primi 2 sono ESCLUSI
    (leading_dropped=2), base al primo giorno vero, niente carry inventato."""
    px = {D[2]: 121.0, D[3]: 133.1}
    fx = {d: 1.25 for d in D}
    out = build_series(D, px, fx, "SPY")
    assert out["error"] is None
    assert out["leading_dropped"] == 2
    assert out["base_date"] == D[2]
    assert out["dates"] == [D[2], D[3]]
    assert out["index"] == pytest.approx([100.0, 110.0])


def test_buffer_pre_base_alimenta_il_primo_giorno_come_carry():
    """Chiusura del venerdi' PRIMA della base TWR: il lunedi' senza dato
    nativo usa quella (carry contato), non viene escluso."""
    pre = "2026-02-27"
    px = {pre: 100.0, D[1]: 110.0}                   # D[0] senza dato nativo
    fx = {pre: 1.25, D[1]: 1.25}
    out = build_series([D[0], D[1]], px, fx, "SPY")
    assert out["error"] is None
    assert out["leading_dropped"] == 0
    assert out["carried_days"] == 1
    assert out["close_eur"] == pytest.approx([80.0, 88.0])
    assert out["ret_daily"] == pytest.approx([0.10])


def test_fx_mancante_equivale_a_buco_prezzo():
    """px presente ma cambio assente quel giorno: il giorno NON e' nativo
    (niente conversione inventata), va in carry-forward."""
    px = {d: v for d, v in zip(D, [100.0, 110.0, 121.0, 133.1])}
    fx = {D[0]: 1.25, D[1]: 1.25, D[3]: 1.25}        # manca fx su D[2]
    out = build_series(D, px, fx, "SPY")
    assert out["carried_days"] == 1
    assert out["close_eur"][2] == pytest.approx(88.0)  # carry di D[1]


def test_fx_zero_equivale_a_buco_prezzo():
    """Cambio 0 (dato rotto): niente divisione, il giorno va in carry."""
    px = {d: v for d, v in zip(D, [100.0, 110.0, 121.0, 133.1])}
    fx = {D[0]: 1.25, D[1]: 1.25, D[2]: 0.0, D[3]: 1.25}
    out = build_series(D, px, fx, "SPY")
    assert out["carried_days"] == 1
    assert out["close_eur"][2] == pytest.approx(88.0)


def test_date_twr_disordinate_o_duplicate_normalizzate():
    """Review B2: input non ordinato/duplicato -> stesso output dell'ordinato."""
    px = {d: v for d, v in zip(D, [100.0, 110.0, 121.0, 133.1])}
    fx = {d: 1.25 for d in D}
    atteso = build_series(D, px, fx, "SPY")
    mescolato = build_series([D[2], D[0], D[3], D[0], D[1]], px, fx, "SPY")
    assert mescolato["dates"] == atteso["dates"]
    assert mescolato["index"] == pytest.approx(atteso["index"])
    assert mescolato["carried_days"] == 0


def test_eur_passthrough_senza_cambio():
    px = {D[0]: 80.0, D[1]: 88.0}
    out = build_series([D[0], D[1]], px, None, "XEUR", quote_currency="EUR")
    assert out["error"] is None
    assert out["close_eur"] == pytest.approx([80.0, 88.0])


def test_valuta_non_supportata_dichiarata():
    out = build_series(D, {D[0]: 1.0}, None, "X", quote_currency="GBP")
    assert "non supportata" in out["error"]


def test_provider_vuoto_dichiarato():
    out = build_series(D, {}, {}, "SPY")
    assert "nessuna chiusura" in out["error"]


def test_giorni_insufficienti_dichiarati():
    """1 solo giorno allineato: sotto MIN_ALIGNED_DAYS, errore coi conteggi."""
    px = {D[3]: 133.1}
    fx = {D[3]: 1.25}
    out = build_series(D, px, fx, "SPY")
    assert "insufficienti" in out["error"]
    assert out["leading_dropped"] == 3


def _twr_payload(dates):
    return {"dates": dates, "twr_index": [100.0 + i for i in range(len(dates))]}


def test_wrapper_fetch_moccato_payload_cache_e_force(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(ticker, start, auto_adjust):
        calls["n"] += 1
        if ticker == "EURUSD=X":
            assert auto_adjust is False
            return {d: 1.25 for d in D}
        assert auto_adjust is True                    # total-return: scelta PM 25/07
        assert start < D[0]                           # buffer in testa
        return {d: v for d, v in zip(D, [100.0, 110.0, 121.0, 133.1])}

    monkeypatch.setattr(bsm, "_fetch_close", fake_fetch)
    bsm.clear_cache()
    out = compute_benchmark_series("SPY", twr_payload=_twr_payload(D))
    assert out["error"] is None
    assert out["index"] == pytest.approx([100.0, 110.0, 121.0, 133.1])
    assert "total-return" in out["src"] and "EURUSD=X" in out["src"]
    assert calls["n"] == 2                            # px + fx
    # cache: seconda chiamata senza force NON rifetcha
    again = compute_benchmark_series("SPY", twr_payload=_twr_payload(D))
    assert calls["n"] == 2 and again["index"] == out["index"]
    # force: rifetcha
    compute_benchmark_series("SPY", force=True, twr_payload=_twr_payload(D))
    assert calls["n"] == 4
    bsm.clear_cache()


def test_wrapper_twr_in_errore_dichiarato_e_non_cachato(monkeypatch):
    monkeypatch.setattr(bsm, "_fetch_close",
                        lambda *a, **k: pytest.fail("non deve fetchare"))
    bsm.clear_cache()
    out = compute_benchmark_series("SPY", twr_payload={"error": "boom"})
    assert "serie TWR in errore" in out["error"]
    assert "SPY|USD" not in bsm._CACHE                # l'errore non si cacha


def test_advanced_metrics_riusa_la_serie_ufficiale(monkeypatch):
    """Fonte unica: portfolio_metrics con benchmark == book deve dare beta 1
    e nota 'ufficiale EUR total-return' (niente builder duplicato)."""
    np = pytest.importorskip("numpy")
    import bellomberg.portfolio.advanced_metrics as am

    dates, idx, v = [], [], 100.0
    rets = [0.02, -0.01] * 6                          # 12 ritorni variabili
    dates.append("2026-01-05")
    for i, r in enumerate(rets):
        v *= (1 + r)
        idx.append(v)
        dates.append(f"2026-01-{6 + i:02d}")
    idx = [100.0] + idx                               # 13 punti, 12 ritorni

    monkeypatch.setattr('bellomberg.portfolio.twr_engine.compute_twr_payload',
                        lambda force=False: {"dates": dates, "twr_index": idx})
    monkeypatch.setattr(
        'bellomberg.market_data.benchmark_series.compute_benchmark_series',
        lambda ticker="SPY", force=False, twr_payload=None, quote_currency="USD": {
            "dates": dates, "ret_daily": list(rets), "carried_days": 0,
            "carried_flags": [False] * len(dates), "error": None})
    m = am.portfolio_metrics()
    assert "twr_index ufficiale" in m["_source"]
    assert m["benchmark"]["beta"] == pytest.approx(1.0)
    assert m["benchmark"]["alpha_annual_pct"] == pytest.approx(0.0, abs=1e-6)
    assert "ufficiale EUR total-return" in m["benchmark_alignment"]


def test_advanced_metrics_esclude_i_giorni_carry_dal_beta(monkeypatch):
    """Semantica pre-refactor: nei giorni carry il benchmark e' fermo (ret 0)
    mentre il book si muove -> ESCLUSI dal pairing, beta resta 1 sui nativi.
    (Se entrassero, le coppie (book, 0) romperebbero beta=1.)"""
    pytest.importorskip("numpy")
    import bellomberg.portfolio.advanced_metrics as am

    dates, idx, v = ["2026-01-05"], [100.0], 100.0
    rets = [0.02, -0.01] * 6
    for i, r in enumerate(rets):
        v *= (1 + r)
        idx.append(v)
        dates.append(f"2026-01-{6 + i:02d}")

    bench_rets = list(rets)
    flags = [False] * len(dates)
    for pos in (3, 6):                    # dates[3]/dates[6] = giorni carry
        bench_rets[pos - 1] = 0.0         # ret_daily[i] <-> dates[1:][i]
        flags[pos] = True

    monkeypatch.setattr('bellomberg.portfolio.twr_engine.compute_twr_payload',
                        lambda force=False: {"dates": dates, "twr_index": idx})
    monkeypatch.setattr(
        'bellomberg.market_data.benchmark_series.compute_benchmark_series',
        lambda ticker="SPY", force=False, twr_payload=None, quote_currency="USD": {
            "dates": dates, "ret_daily": bench_rets, "carried_days": 2,
            "carried_flags": flags, "error": None})
    m = am.portfolio_metrics()
    assert m["benchmark"]["beta"] == pytest.approx(1.0)
    assert "10 giorni comuni" in m["benchmark_alignment"]
    assert "2g carry-forward esclusi" in m["benchmark_alignment"]


def test_advanced_metrics_sovrapposizione_corta_dichiarata_niente_tail_align(monkeypatch):
    """Review B1: date presenti ma solo 5 giorni comuni -> NIENTE beta
    posizionale (classe 'beta artefatto 0,04'), nota dichiarata."""
    pytest.importorskip("numpy")
    import bellomberg.portfolio.advanced_metrics as am

    dates, idx, v = ["2026-01-05"], [100.0], 100.0
    rets = [0.02, -0.01] * 6
    for i, r in enumerate(rets):
        v *= (1 + r)
        idx.append(v)
        dates.append(f"2026-01-{6 + i:02d}")
    b_dates = dates[:6] + [f"2026-06-{10 + i}" for i in range(len(dates) - 6)]

    monkeypatch.setattr('bellomberg.portfolio.twr_engine.compute_twr_payload',
                        lambda force=False: {"dates": dates, "twr_index": idx})
    monkeypatch.setattr(
        'bellomberg.market_data.benchmark_series.compute_benchmark_series',
        lambda ticker="SPY", force=False, twr_payload=None, quote_currency="USD": {
            "dates": b_dates, "ret_daily": list(rets), "carried_days": 0,
            "carried_flags": [False] * len(b_dates), "error": None})
    m = am.portfolio_metrics()
    assert "benchmark" not in m
    assert "sovrapposizione insufficiente" in m["benchmark_alignment"]
