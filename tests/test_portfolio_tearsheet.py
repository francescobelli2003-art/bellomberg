"""Quant fase 1c LOTTO 1 — tearsheet sul TWR ufficiale (23/07 notte).
Offline: twr_payload/metrics iniettati, valori attesi CALCOLATI A MANO.
"""
import math

import pytest

import bellomberg.portfolio.portfolio_tearsheet as pt


def _payload(dates, idx):
    return {"dates": dates, "twr_index": idx, "regime_summary": {"mock": True}}


def _ts(dates, idx, **kw):
    kw.setdefault("metrics", {"mock": True})
    kw.setdefault("rf_annual", 0.0)
    return pt.compute_tearsheet(twr_payload=_payload(dates, idx), **kw)


def test_mensili_composti_e_parzialita_a_mano():
    """Feb (base 27/02, parziale): +10%; Mar (+10% due volte): +21%; anno 33,1%."""
    dates = ["2026-02-27", "2026-02-28", "2026-03-02", "2026-03-03"]
    idx = [100.0, 110.0, 121.0, 133.1]
    out = _ts(dates, idx)
    assert out.get("error") is None
    m = {x["month"]: x for x in out["monthly"]}
    assert m["2026-02"]["return_pct"] == pytest.approx(10.0)
    assert m["2026-02"]["partial"] is True          # base 27/02 dentro il mese
    assert m["2026-03"]["return_pct"] == pytest.approx(21.0)
    assert m["2026-03"]["partial"] is True          # ultimo mese: in corso
    y = {x["year"]: x for x in out["yearly"]}
    assert y["2026"]["return_pct"] == pytest.approx(33.1)
    assert y["2026"]["partial"] is True


def test_primo_mese_completo_se_base_nel_mese_prima():
    """Base 27/02 -> il primo mese di RENDIMENTI e' marzo: NON parziale
    (parziale resta solo l'ultimo, aprile in corso)."""
    dates = ["2026-02-27", "2026-03-02", "2026-03-31", "2026-04-01"]
    idx = [100.0, 110.0, 121.0, 121.0]
    out = _ts(dates, idx)
    m = {x["month"]: x for x in out["monthly"]}
    assert m["2026-03"]["partial"] is False
    assert m["2026-03"]["return_pct"] == pytest.approx(21.0)
    assert m["2026-04"]["partial"] is True


def test_cavallo_anno_e_anno_a_copertura_parziale():
    """Review B7: dicembre->gennaio spezza in due anni; il 2025 coperto SOLO da
    dicembre (mese completo) e' comunque dichiarato parziale come anno."""
    dates = ["2025-11-28", "2025-12-15", "2025-12-31", "2026-01-15"]
    idx = [100.0, 110.0, 121.0, 133.1]
    out = _ts(dates, idx)
    m = {x["month"]: x for x in out["monthly"]}
    assert m["2025-12"]["return_pct"] == pytest.approx(21.0)
    assert m["2025-12"]["partial"] is False        # base in novembre: mese pieno
    y = {x["year"]: x for x in out["yearly"]}
    assert y["2025"]["return_pct"] == pytest.approx(21.0)
    assert y["2025"]["partial"] is True            # copre 1 mese su 12: dichiarato
    assert y["2026"]["return_pct"] == pytest.approx(10.0)
    assert y["2026"]["partial"] is True


def test_anno_composto_dai_fattori_non_arrotondati():
    """Review M1: due mesi che a display fanno 0,00% compongono comunque l'anno
    dai fattori GREZZI (0,004% + 0,004% -> anno 0,01%, non 0,00%)."""
    dates = ["2026-01-30", "2026-02-02", "2026-03-02", "2026-04-01"]
    idx = [100.0, 100.004, 100.0080002, 100.0120005]
    out = _ts(dates, idx)
    m = {x["month"]: x for x in out["monthly"]}
    assert m["2026-02"]["return_pct"] == pytest.approx(0.0)   # arrotondato a video
    assert out["yearly"][0]["return_pct"] == pytest.approx(0.01)  # grezzo composto
    assert all("_factor" not in x for x in out["monthly"])    # interno, non esposto


def test_drawdown_episodi_a_mano():
    """idx 100->90->80->100->95->100: due episodi chiusi (-20% e -5%),
    nessun drawdown corrente."""
    dates = [f"2026-03-0{i}" for i in range(1, 7)]
    idx = [100.0, 90.0, 80.0, 100.0, 95.0, 100.0]
    out = _ts(dates, idx)
    dd = out["drawdowns"]
    assert dd["current"] is None
    assert dd["n_episodes_total"] == 2
    top = dd["top"][0]
    assert top["depth_pct"] == pytest.approx(-20.0)
    assert top["start_date"] == "2026-03-01"
    assert top["trough_date"] == "2026-03-03"
    assert top["recovery_date"] == "2026-03-04"
    assert top["days_to_trough"] == 2 and top["days_total"] == 3
    assert dd["top"][1]["depth_pct"] == pytest.approx(-5.0)


def test_drawdown_aperto_dichiarato():
    dates = ["2026-03-01", "2026-03-02", "2026-03-03"]
    idx = [100.0, 90.0, 95.0]
    out = _ts(dates, idx)
    dd = out["drawdowns"]
    assert dd["current"] is not None and dd["current"]["open"] is True
    assert dd["current"]["depth_pct"] == pytest.approx(-10.0)      # al minimo
    assert dd["current"]["current_dd_pct"] == pytest.approx(-5.0)  # oggi
    assert dd["top"][0]["recovery_date"] is None                   # mai inventata
    # review B1: la voce in top porta il flag open (current = vista, non doppione)
    assert dd["top"][0]["open"] is True


def test_rolling_a_mano_finestra_3():
    """r = 1%,2%,3%,4%; finestra 3: vol = std(ddof=1)*sqrt(252), rf=0.
    Finestra 1: std([1,2,3]%)=1% -> vol 15,87%, sharpe 2%/1%*sqrt(252)=31,75."""
    dates = ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]
    idx = [100.0]
    for r in (0.01, 0.02, 0.03, 0.04):
        idx.append(idx[-1] * (1 + r))
    out = _ts(dates, idx, rolling_windows=(3,))
    blk = out["rolling"]["w3"]
    assert blk["dates"] == ["2026-03-04", "2026-03-05"]   # 4 r, finestra 3 -> 2 punti
    assert blk["vol_annual_pct"][0] == pytest.approx(0.01 * math.sqrt(252) * 100, abs=0.01)
    assert blk["sharpe"][0] == pytest.approx(0.02 / 0.01 * math.sqrt(252), abs=0.01)


def test_rolling_finestra_incompleta_dichiarata_mai_accorciata():
    dates = ["2026-03-01", "2026-03-02", "2026-03-03"]
    idx = [100.0, 101.0, 102.0]
    out = _ts(dates, idx, rolling_windows=(30,))
    assert "w30" not in out["rolling"]
    assert any("rolling 30" in n and "dichiarato" in n for n in out["notes"])


def test_tool_get_tearsheet_registrato_e_dispatch(monkeypatch):
    """Lotto 2 (wiring): registro, desk quant (capo=tutto), dispatch senza rete."""
    import bellomberg.agents.chat_tools as ct
    assert "get_tearsheet" in [t["name"] for t in ct.TOOL_DEFINITIONS]
    assert "get_tearsheet" in [t["name"] for t in ct.get_tools_for_agent("quant")]
    assert "get_tearsheet" in [t["name"] for t in ct.get_tools_for_agent("capo")]
    monkeypatch.setattr(pt, "compute_tearsheet", lambda **kw: {"ok": True})
    out = ct.dispatch("get_tearsheet", {})
    assert out["data"]["ok"] is True                   # _stamp avvolge in data
    assert out["_source"] == "portfolio_tearsheet.compute_tearsheet"


def test_metrics_iniettate_passthrough_e_serie_rotta_dichiarata():
    dates = ["2026-03-01", "2026-03-02"]
    out = _ts(dates, [100.0, 101.0], metrics={"sharpe": 1.23})
    assert out["metrics"] == {"sharpe": 1.23}       # fonte unica, passthrough
    bad = pt.compute_tearsheet(twr_payload={"error": "boom"})
    assert "boom" in bad["error"]
    nan = pt.compute_tearsheet(twr_payload=_payload(dates, [100.0, float("nan")]),
                               metrics={}, rf_annual=0.0)
    assert "error" in nan                            # NaN = dichiarato, mai zitto
