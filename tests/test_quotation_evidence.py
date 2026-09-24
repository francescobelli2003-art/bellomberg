"""Listing identity is derived only from an unambiguous ordinary-share cover row."""
import json
import pytest


def _cover(title="Common Stock, par value per share", symbol="SYNTH", context="ordinary"):
    return (f'<ix:nonNumeric name="dei:Security12bTitle" contextRef="{context}">{title}</ix:nonNumeric>'
            f'<ix:nonNumeric name="dei:TradingSymbol" contextRef="{context}">{symbol}</ix:nonNumeric>'
            f'<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="{context}">Synthetic Exchange</ix:nonNumeric>').encode()


def test_ordinary_identity_derives_one_but_does_not_default_adr_ratio():
    from bellomberg.valuation.quotation_evidence import listing_identity
    result = listing_identity(_cover(), "SYNTH")
    assert result["status"] == "verified"
    assert result["shares_per_quote"] == 1
    assert result["basis"] == "one_listed_ordinary_share_is_one_share_of_the_same_class"
    for title in ("American Depositary Shares", "Preferred Stock", "Units", "Senior Notes"):
        result = listing_identity(_cover(title), "SYNTH")
        assert result["status"] == "incomplete"
        assert "shares_per_quote" not in result


def test_identity_rejects_cross_context_and_ambiguous_classes():
    from bellomberg.valuation.quotation_evidence import listing_identity
    wrong = _cover().replace(b'name="dei:TradingSymbol" contextRef="ordinary"',
                             b'name="dei:TradingSymbol" contextRef="other"')
    assert listing_identity(wrong, "SYNTH")["status"] == "incomplete"
    ambiguous = _cover() + _cover("Class A Common Stock", context="class-a")
    assert listing_identity(ambiguous, "SYNTH")["status"] == "incomplete"
    assert listing_identity(_cover(symbol="OTHER"), "SYNTH")["status"] == "incomplete"


def test_quote_preserves_exact_close_date_unit_and_source():
    from bellomberg.valuation.quotation_evidence import historical_quote_document
    result = historical_quote_document("SYNTH", on="2025-12-31", as_of="2026-02-01",
        fetch=lambda *a: {"symbol": "SYNTH", "date": "2025-12-31", "close": 123.75, "currency": "USD"})
    assert result["status"] == "ready"
    doc = result["documents"][0]
    fact = json.loads(doc["text"])["facts"][0]
    assert fact == {"value": 123.75, "unit": "USD per share", "end": "2025-12-31"}
    assert doc["origin"] == "yfinance_unadjusted_close"


@pytest.mark.parametrize("change", [{"symbol": "OTHER"}, {"date": "2025-12-30"}, {"currency": None}, {"close": None}])
def test_quote_has_no_previous_day_currency_or_price_fallback(change):
    from bellomberg.valuation.quotation_evidence import historical_quote_document
    raw = {"symbol": "SYNTH", "date": "2025-12-31", "close": 123.75, "currency": "USD", **change}
    result = historical_quote_document("SYNTH", on="2025-12-31", as_of="2026-02-01", fetch=lambda *a: raw)
    assert result["status"] == "incomplete" and not result["documents"]
