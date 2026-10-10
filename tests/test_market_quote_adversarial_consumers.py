"""Independent consumer probes: observed quotes cannot replace model economics."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from bellomberg.agents import specialist_scores
from bellomberg.core.language import language_context
from bellomberg.valuation import dcf_engine, market_quote, sector_analysis
from test_market_quote import acquired_bundle, quote_info
from test_market_quote_consumers import calculated_payload
from test_sotp_documented import sotp_bundle, sotp_records


@pytest.mark.parametrize("delivery", ["provided", "sidecar"])
def test_scorer_measures_current_price_and_declares_stale_or_absent_quote_nd(tmp_path, monkeypatch, delivery):
    payload = calculated_payload(tmp_path)
    assert payload["valuation_usability"]["usable"]
    before = deepcopy(payload)
    sidecar = Path(payload["path"]).with_suffix(".payload.json")
    saved = sidecar.read_bytes()
    workbook = Path(payload["path"])
    workbook_bytes = workbook.read_bytes()
    monkeypatch.setattr(specialist_scores, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(specialist_scores.cl, "carica_veicoli", lambda: {
        "origine": "synthetic", "motivo": None, "veicoli": {}})

    calls = []
    def forbidden(*args, **kwargs):
        calls.append("unexpected I/O or regeneration")
        pytest.fail("Scoring must read supplied evidence, without DB, network or regeneration")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(dcf_engine, "generate_valuation", forbidden)

    original_view = market_quote.market_quote_view
    read_day = "2026-09-10"
    monkeypatch.setattr(market_quote, "market_quote_view", lambda block, *, as_of=None, usable=True:
        original_view(block, as_of=read_day if as_of is None else as_of, usable=usable))
    portfolio = {"positions": [{"ticker": "SYNTH", "peso_pct": 100.}]}
    def score(value):
        with language_context("en"):
            return specialist_scores.fundamentals_score(
                portfolio, valuations={"SYNTH": value} if delivery == "provided" else None)

    fresh = score(payload)
    read_day = "2026-09-19"
    stale = score(payload)
    legacy = deepcopy(payload)
    legacy.pop("market_quote")
    if delivery == "sidecar":
        legacy_sidecar = json.loads(saved)
        legacy_sidecar.pop("market_quote")
        sidecar.write_text(json.dumps(legacy_sidecar, allow_nan=False), encoding="utf-8")
    missing = score(legacy)

    # Contratto cambiato per ordine PM 09/10 (Opus 5.5): il margine di sicurezza si misura
    # sul prezzo CORRENTE osservato. Il fair value resta quello del modello (nessuna
    # rivalutazione); una quotazione vecchia o assente rende il nome n.d., mai il prezzo
    # del modello in silenzio. Qui l'unico nome: score n.d. DICHIARATO (v2 10/10: dict col
    # motivo, non None; il portafoglio non porta un prezzo del book, quindi niente seconda fonte).
    for nd in (stale, missing):
        assert nd["score"] is None and nd["max_score"] is None
        assert nd["verdict"].startswith("n/a - current price n/a on 1/1 names"), nd["verdict"]
    assert fresh is not None and fresh["max_score"] == 3
    canonical = next(payload[k] for k in ("fair_value", "fair_value_final", "fair_value_weighted",
                                          "fair_value_blend", "fair_value_base") if payload.get(k) is not None)
    observed = payload["market_quote"]["upside_base_pct"]
    expected = round((canonical / payload["fair_value_base"] * (1 + observed / 100) - 1) * 100, 1)
    assert fresh["metrics"]["mos_book_pct"] == expected
    assert fresh["metrics"]["mos_book_pct"] != 41.3, "non il margine al prezzo del modello"
    comparison = next(row for row in fresh["lines"] if row[0] == "  Observed price comparison SYNTH")
    assert comparison[2] is None
    assert "observed price 17.8% (ok)" in comparison[1]
    assert "model upside 41.3%" in comparison[1]
    assert payload == before and workbook.read_bytes() == workbook_bytes
    if delivery == "provided":
        assert sidecar.read_bytes() == saved
    else:
        assert json.loads(sidecar.read_text(encoding="utf-8")) == legacy_sidecar
    assert calls == []


def _with_observation(bundle, price):
    info = quote_info()
    info.update(symbol=bundle["case"]["ticker"], regularMarketPrice=price)
    return acquired_bundle(bundle, info)


def test_sotp_compares_parent_quote_without_averaging_child_upside(tmp_path, monkeypatch):
    rows = sotp_records()
    children = next(row for row in rows if row["driver"] == "children")["value"]
    for name in children:
        children[name] = _with_observation(children[name], 30.)
    baseline = dcf_engine.generate_valuation("SYNTH-HOLD", prepared_bundle=sotp_bundle(rows),
                                           output_dir=str(tmp_path / "without-parent-quote"))
    bundle = _with_observation(sotp_bundle(rows), 40.)
    result = dcf_engine.generate_valuation("SYNTH-HOLD", prepared_bundle=bundle,
                                         output_dir=str(tmp_path / "with-parent-quote"))
    assert baseline["valuation_usability"]["usable"] and result["valuation_usability"]["usable"]
    assert result["fair_value_base"] == baseline["fair_value_base"]
    assert result["price"] == baseline["price"] == 50.
    assert result["upside_pct"] == baseline["upside_pct"]
    assert result["sanity"] == baseline["sanity"]
    expected = round((result["fair_value_base"] / 40. - 1) * 100, 1)
    assert result["market_quote"]["upside_base_pct"] == expected
    assert {value["market_quote"]["symbol"] for value in result["child_valuations"].values()} == {
        "SYNTH-BANK", "SYNTH-REIT"}
    child_upside = [value["market_quote"]["upside_base_pct"] for value in result["child_valuations"].values()]
    assert expected not in child_upside and expected != sum(child_upside) / len(child_upside)

    before = deepcopy(result)
    original_view = market_quote.market_quote_view
    monkeypatch.setattr(market_quote, "market_quote_view", lambda block, *, as_of=None, usable=True:
        original_view(block, as_of="2026-09-10" if as_of is None else as_of, usable=usable))
    text = sector_analysis.valuation_results_block({"SYNTH-HOLD": result})
    committee_rows = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    assert len(committee_rows) == 1
    assert committee_rows[0]["ticker"] == "SYNTH-HOLD"
    assert committee_rows[0]["upside_today_pct"] == expected
    assert committee_rows[0]["market_quote"]["symbol"] == "SYNTH-HOLD"
    assert "SYNTH-BANK" not in text and "SYNTH-REIT" not in text
    assert result == before
