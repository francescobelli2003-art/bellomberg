"""The ledger warning is authored presentation; its count does not alter cash."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload


@pytest.mark.parametrize("count", [0, 1, 2])
def test_pre_snapshot_ledger_note_uses_singular_without_changing_series(monkeypatch, count):
    from bellomberg.portfolio import twr_engine as twr
    from bellomberg.portfolio import portfolio_analytics as analytics

    def forbidden(*args, **kwargs):
        raise AssertionError("No database connection is allowed in this synthetic test")

    monkeypatch.setattr(twr, "connect_sqlite", forbidden)
    monkeypatch.setattr(twr, "MemoryDB", lambda: SimpleNamespace(get_opening_positions=lambda: []))
    snapshots = [{"date": "2024-01-03", "nav_total_eur": 100},
                 {"date": "2024-01-04", "nav_total_eur": 101}]
    ledger = [{"date": "2024-01-02", "type": "DEPOSIT", "amount_eur": 10} for _ in range(count)]
    original = deepcopy((snapshots, ledger))
    monkeypatch.setattr(twr, "_load_snapshots", lambda: snapshots)
    monkeypatch.setattr(twr, "get_cash_movements", lambda: ledger)
    monkeypatch.setattr(analytics, "compute_nav_history", lambda: {
        "dates": ["2024-01-03"], "nav_eur": [100], "cost_basis_eur": [100],
        "realized_sales_eur": [0],
    })
    with language_context("it"):
        result = twr.get_official_series()
    italian = render_payload(result, language="it")
    english = render_payload(result, language="en")
    it_notes = [n for n in italian["notes"] if "preced" in n]
    en_notes = [n for n in english["notes"] if "preced" in n]
    if count:
        assert len(it_notes) == len(en_notes) == 1
        assert it_notes[0].startswith(f"{count} " + ("movimento del ledger precede" if count == 1 else "movimenti del ledger precedono"))
        assert en_notes[0].startswith(f"{count} " + ("ledger movement precedes" if count == 1 else "ledger movements precede"))
    else:
        assert not it_notes and not en_notes
    for payload in [italian, english]:
        assert payload["dates"] == ["2024-01-03", "2024-01-04"]
        assert payload["values_eur"] == [100.0, 101.0]
        assert payload["flows_eur"] == [100.0, 0]
    assert (snapshots, ledger) == original
    historical = {"notes": ["1 ledger movements precede: original historical text"]}
    assert render_payload(historical, language="it") == historical
