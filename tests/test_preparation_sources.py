"""Free source acquisition for the shared preparer, using only local fixtures."""
from hashlib import sha256
import pytest


def _primary(tmp_path):
    raw = b'''<ix:nonNumeric name="dei:Security12bTitle" contextRef="listed">Common stock</ix:nonNumeric>
<ix:nonNumeric name="dei:TradingSymbol" contextRef="listed">SYNTH</ix:nonNumeric>
<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="listed">Synthetic exchange</ix:nonNumeric>'''
    path = tmp_path / "statement.html"
    path.write_bytes(raw)
    digest = sha256(raw).hexdigest()
    return {"id": digest, "document_sha256": digest, "archive_path": str(path),
        "url": "https://example.org/issuer/annual", "text": "Synthetic statement",
        "published_at": "2026-02-01", "metadata": {"report_date": "2025-12-31", "form": "10-K"}}


def _setup(monkeypatch, tmp_path):
    from bellomberg.valuation import valuation_sources
    primary = _primary(tmp_path)
    monkeypatch.setattr(valuation_sources, "collect_documents", lambda *a, **k: {
        "status": "ready", "documents": [primary], "issues": [], "coverage": {"reused": 1}})
    monkeypatch.setattr(valuation_sources, "company_facts_documents", lambda *a, **k: {
        "status": "ready", "documents": [{"id": "synthetic-xbrl", "text": "{}"}], "issues": []})
    return primary


def test_primary_xbrl_listing_and_exact_day_price_share_one_catalog(tmp_path, monkeypatch):
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    primary = _setup(monkeypatch, tmp_path)
    calls = []
    def price(ticker, on):
        calls.append((ticker, on))
        return {"symbol": ticker, "date": on, "currency": "EUR", "close": 12.5}
    result = collect_preparation_evidence("SYNTH", as_of="2026-02-02", archive_root=tmp_path, price_fetch=price)
    assert calls == [("SYNTH", "2025-12-31")]
    assert result["preparation_ready"] and len(result["documents"]) == 4
    assert result["selection"]["opening_date"] == "2025-12-31"
    assert all("archive_path" not in doc for doc in result["documents"])
    assert result["documents"][0]["id"] == primary["id"]


@pytest.mark.parametrize('market_status', ['ready', 'incomplete'])
def test_market_references_join_common_catalog_without_certifying_wacc(tmp_path, monkeypatch, market_status):
    from bellomberg.valuation import market_reference_evidence
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    _setup(monkeypatch, tmp_path)
    calls = []
    docs = [{'id': 'synthetic-market', 'text': '{"facts":[]}'}] if market_status == 'ready' else []
    issues = [] if docs else [{'source': 'market references', 'reason': 'synthetic unavailable source'}]
    def market(**kwargs):
        calls.append(kwargs)
        return {'status': market_status, 'documents': docs, 'issues': issues,
                'limitation': 'references only, no WACC'}
    monkeypatch.setattr(market_reference_evidence, 'collect_market_references', market)
    result = collect_preparation_evidence('SYNTH', as_of='2026-02-02', archive_root=tmp_path,
        financial_currency='USD', price_fetch=lambda ticker,on: {'symbol':ticker, 'date':on, 'currency':'USD', 'close':12.5})
    assert calls[0]['currency'] == 'USD' and calls[0]['as_of'] == '2026-02-02'
    assert calls[0]['ticker'] == 'SYNTH'
    assert result['preparation_ready']  # This flag proves the opening sources only.
    assert result['market_references']['status'] == market_status
    assert result['market_references']['limitation'] == 'references only, no WACC'
    assert result['documents'][4:] == docs
    assert result['issues'] == issues
    assert result['status'] == ('ready' if docs else 'partial')


@pytest.mark.parametrize("problem", ["outside", "changed", "missing_price"])
def test_missing_or_unattested_opening_evidence_stops_before_proposer(tmp_path, monkeypatch, problem):
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from test_input_preparation import _bundle
    primary = _setup(monkeypatch, tmp_path)
    if problem == "outside":
        primary["archive_path"] = str(tmp_path.parent / "untrusted.html")
    elif problem == "changed":
        (tmp_path / "statement.html").write_text("changed source", encoding="utf-8")
    def price(ticker, on):
        if problem == "missing_price":
            raise ValueError("no price at requested opening")
        return {"symbol": ticker, "date": on, "currency": "EUR", "close": 12.5}
    report = collect_preparation_evidence("SYNTH", as_of="2026-02-02", archive_root=tmp_path, price_fetch=price)
    assert not report["preparation_ready"] and report["documents"] == []
    assert report["issues"] and report["acquired_document_index"]
    result = prepare_and_generate(_bundle(), documents=report["documents"], source_report=report,
        propose=lambda *a: pytest.fail("known source gap must not spend"), output_dir=tmp_path / "models")
    assert not result["valuation_usability"]["usable"] and result.get("path") is None


def test_quarter_keeps_annual_and_comparative_facts_without_changing_opening_price(tmp_path, monkeypatch):
    from copy import deepcopy
    from bellomberg.valuation import valuation_sources
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    annual = _setup(monkeypatch, tmp_path)
    quarterly = {**deepcopy(annual), "id": "quarterly", "published_at": "2026-08-01",
                 "metadata": {"report_date": "2026-06-30", "form": "10-Q"}}
    previous = {**deepcopy(quarterly), "id": "comparative", "published_at": "2025-08-01",
                "metadata": {"report_date": "2025-06-30", "form": "10-Q"}}
    monkeypatch.setattr(valuation_sources, "collect_documents", lambda *a, **k: {
        "status": "ready", "documents": [quarterly, annual, previous], "issues": [], "coverage": {}})
    facts_called = []
    def facts(docs, **kwargs):
        facts_called.extend(doc["id"] for doc in docs)
        return {"status": "ready", "documents": [{"id": "xbrl-" + doc["id"], "text": "{}"} for doc in docs], "issues": []}
    monkeypatch.setattr(valuation_sources, "company_facts_documents", facts)
    prices = []
    def price(ticker, on):
        prices.append(on)
        return {"symbol": ticker, "date": on, "currency": "EUR", "close": 12.5}
    result = collect_preparation_evidence("SYNTH", as_of="2026-08-02", archive_root=tmp_path, price_fetch=price)
    assert result["preparation_ready"]
    assert prices == ["2026-06-30"]
    assert facts_called == ["quarterly", annual["id"], "comparative"]
    assert result["selection"]["annual_document_id"] == annual["id"]
    assert "ledgers" in result["selection"]["limitation"]


def test_missing_annual_prevents_paid_preparation(tmp_path, monkeypatch):
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    primary = _setup(monkeypatch, tmp_path)
    primary["metadata"]["form"] = "10-Q"
    result = collect_preparation_evidence("SYNTH", as_of="2026-02-02", archive_root=tmp_path,
        price_fetch=lambda ticker, on: {"symbol": ticker, "date": on, "currency": "EUR", "close": 12.5})
    assert not result["preparation_ready"] and not result["documents"]
    assert any(issue["source"] == "annual coverage" for issue in result["issues"])


@pytest.mark.parametrize('fx_status', ['ready', 'incomplete'])
def test_known_cross_currency_requires_exact_day_fx_before_paid_preparation(tmp_path,monkeypatch,fx_status):
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation import fx_evidence, market_reference_evidence
    _setup(monkeypatch,tmp_path)
    observed=[]
    def fx(**kwargs):
        observed.append(kwargs)
        return {'status':fx_status,'documents':[{'id':'synthetic-fx','text':'{}'}] if fx_status=='ready' else [],
                'issues':[] if fx_status=='ready' else [{'source':'FX','reason':'missing date'}]}
    monkeypatch.setattr(fx_evidence,'collect_fx_evidence',fx)
    monkeypatch.setattr(market_reference_evidence,'collect_market_references',lambda **k:{
        'status':'not_requested','documents':[],'issues':[]})
    result=collect_preparation_evidence('SYNTH',as_of='2026-02-02',archive_root=tmp_path,
        financial_currency='USD',price_fetch=lambda ticker,on:{'symbol':ticker,'date':on,'close':12.5,'currency':'EUR'})
    assert observed[0]['financial_currency']=='USD' and observed[0]['quote_currency']=='EUR'
    assert observed[0]['on']=='2025-12-31'
    assert result['preparation_ready']==(fx_status=='ready')
    if fx_status=='incomplete': assert not result['documents']
