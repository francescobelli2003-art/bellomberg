"""Cataloghi documentali: identita', periodi, URL originali e pagine complete."""
import pytest
from bellomberg.market_data import sec_edgar, esef


class Risposta:
    status_code = 200
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        return self.payload
    def raise_for_status(self):
        pass


def righe(form, accession, period):
    return {"form": [form], "accessionNumber": [accession], "filingDate": ["2026-03-01"],
            "reportDate": [period], "items": ["1.01,2.02"],
            "primaryDocument": ["report.htm"], "primaryDocDescription": ["Results"]}


def test_sec_preserves_metadata_and_reads_archive(monkeypatch):
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000001234")
    pages = [
        {"cik": "1234", "name": "Example Corporation", "filings": {
            "recent": righe("10-K", "0000001234-26-000001", "2025-12-31"),
            "files": [{"name": "CIK0000001234-submissions-001.json"}]}},
        righe("20-F", "0000001234-25-000001", "2024-12-31")]
    calls = []
    def get(url, **kw):
        calls.append(url)
        return Risposta(pages.pop(0))
    monkeypatch.setattr(sec_edgar.requests, "get", get)
    r = sec_edgar.get_filing_catalog("EXAMPLE", days=5000)
    assert r["stato"] == "ok" and len(r["documenti"]) == 2
    assert len(calls) == 2
    doc = r["documenti"][0]
    assert doc["report_date"] == "2025-12-31"
    assert doc["items"] == ["1.01", "2.02"]
    assert doc["emittente_id"] == "CIK:0000001234"
    assert doc["issuer"] == "Example Corporation"
    assert doc["url"].endswith("/1234/000000123426000001/report.htm")


def test_sec_ambiguous_european_symbol_never_contacts_us_namesake(monkeypatch):
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})
    def forbidden(*a, **k):
        pytest.fail("CIK americano cercato per un ticker europeo non verificato")
    monkeypatch.setattr(sec_edgar, "lookup_cik", forbidden)
    r = sec_edgar.get_filing_catalog("EXAMPLE.MI")
    assert r["stato"] == "errore" and r["motivi"]


@pytest.mark.parametrize("name", ["https://evil.example/submissions.json", "../other.json"])
def test_sec_archive_path_must_belong_to_requested_cik(monkeypatch, name):
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000001234")
    monkeypatch.setattr(sec_edgar.requests, "get", lambda *a, **k: Risposta({
        "cik": "1234", "filings": {"recent": righe("10-K", "acc", "2025-12-31"),
                                   "files": [{"name": name}]}}))
    r = sec_edgar.get_filing_catalog("EXAMPLE")
    assert r["stato"] == "parziale" and r["motivi"]


def test_sec_archive_failure_keeps_recent_but_declares_gap(monkeypatch):
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000001234")
    def get(url, **kw):
        if "submissions-001" in url:
            raise sec_edgar.requests.Timeout("offline")
        return Risposta({"cik": "1234", "filings": {
            "recent": righe("6-K", "acc", "2025-12-31"),
            "files": [{"name": "CIK0000001234-submissions-001.json"}]}})
    monkeypatch.setattr(sec_edgar.requests, "get", get)
    r = sec_edgar.get_filing_catalog("EXAMPLE", days=5000)
    assert r["stato"] == "parziale" and len(r["documenti"]) == 1
    assert "offline" in " ".join(r["motivi"])


@pytest.mark.parametrize('issue', [None, 'issuer', 'truncated', 'conflict'])
def test_publication_catalog_keeps_earnings_separate_and_declares_gaps(monkeypatch, issue):
    monkeypatch.setattr(sec_edgar, 'lookup_cik', lambda *a, **k: '0000001234')
    monkeypatch.setattr(sec_edgar, '_headers', lambda: {'User-Agent': 'synthetic-test'})
    rows = [righe(form, f'0000001234-26-{i:06d}', '2025-12-31')
            for i, form in enumerate(('10-K', '8-K', '8-K', '8-K/A'), 1)]
    rows[2]['items'] = ['7.01']
    recent = {key: [row[key][0] for row in rows] for key in rows[0]}
    archive = righe('8-K', '0000001234-26-000002', '2025-12-31')
    if issue == 'conflict': archive['primaryDocument'] = ['changed.htm']
    files = [{'name': 'CIK0000001234-submissions-001.json'}]
    if issue == 'truncated': files.append({'name': 'CIK0000001234-submissions-002.json'})
    def get(url, **kw):
        if 'submissions-' in url: return Risposta(archive)
        return Risposta({'cik': '999' if issue == 'issuer' else '1234',
                         'filings': {'recent': recent, 'files': files}})
    monkeypatch.setattr(sec_edgar.requests, 'get', get)
    result = sec_edgar.get_publication_catalog('EXAMPLE', days=5000, max_pages=2)
    if issue:
        assert result['stato'] != 'ok' and result['motivi']
    else:
        assert result['stato'] == 'ok' and result['emittente_id'] == 'CIK:0000001234'
        assert sorted(row['form'] for row in result['documenti']) == ['10-K', '8-K', '8-K/A']
        normal = sec_edgar.get_filing_catalog('EXAMPLE', days=5000, max_pages=2)
        assert normal['stato'] == 'ok' and [row['form'] for row in normal['documenti']] == ['10-K']


def test_esef_keeps_html_zip_and_all_pages(monkeypatch):
    import requests
    pages = [{"data": [{"id": "new", "attributes": {"period_end": "2025-12-31",
               "report_url": "/reports/new.xhtml", "package_url": "/reports/new.zip"}}],
              "links": {"next": "/api/entities/TEST/filings?page[number]=2"}},
             {"data": [{"id": "old", "attributes": {"period_end": "2024-12-31",
               "json_url": "/reports/old.json"}}], "links": {}}]
    calls = []
    def get(url, **kw):
        calls.append(url)
        return Risposta(pages.pop(0))
    monkeypatch.setattr(requests, "get", get)
    filings = esef._list_filings("TEST")
    assert len(calls) == 2 and len(filings) == 2
    assert filings[-1]["report_url"] == "https://filings.xbrl.org/reports/new.xhtml"
    assert filings[-1]["package_url"].endswith("new.zip")
    assert filings[-1]["no_json"] is True
    assert filings[-1]["emittente_id"] == "LEI:TEST"
    assert filings[0]["no_report"] is True


@pytest.mark.parametrize("next_url", ["https://evil.example/page2",
                                      "https://filings.xbrl.org/api/entities/TEST/filings"])
def test_esef_wrong_origin_or_pagination_loop_declared(monkeypatch, next_url):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Risposta({"data": [], "links": {"next": next_url}}))
    with pytest.raises(ValueError):
        esef._list_filings("TEST")
