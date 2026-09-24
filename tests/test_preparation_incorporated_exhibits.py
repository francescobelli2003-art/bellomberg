"""An incorporation reference needs its own SEC issuer, document and date proof."""
from hashlib import sha256
from pathlib import Path

import pytest

from bellomberg.valuation.preparation_exhibits import collect_preparation_exhibits
from test_preparation_exhibits import primary


TARGET = "https://www.sec.gov/Archives/edgar/data/123456/000012345620000002/subs.htm"
INDEX = TARGET.replace("subs.htm", "0000123456-20-000002-index.html")


def setup_reference(tmp_path, *, href=TARGET, description=None, issue=None):
    filing = primary(tmp_path, number="21.1", href=href, description=description or
        "Subsidiaries incorporated herein by reference to Exhibit 21.1 of a prior registration")
    index = '''<html><title>EDGAR Filing Documents for 0000123456-20-000002</title>
      <div>SEC Accession No. 0000123456-20-000002</div>
      <div class="formGrouping"><div class="infoHead">Filing Date</div>
        <div class="info">2020-03-05</div></div>
      <div class="companyInfo"><span class="companyName">Synthetic Parent (Filer)
        <a href="/cgi-bin/browse-edgar?CIK=0000123456">0000123456</a></span></div>
      <table summary="Document Format Files"><tr><th>Seq</th><th>Description</th>
      <th>Document</th><th>Type</th><th>Size</th></tr><tr><td>1</td><td>Subsidiaries</td>
      <td><a href="subs.htm">subs.htm</a></td><td>EX-21.1</td><td>123</td></tr></table></html>'''
    if issue == "future": index = index.replace("2020-03-05", "2026-02-02")
    if issue == "invalid_date": index = index.replace("2020-03-05", "unknown")
    if issue == "wrong_issuer": index = index.replace("CIK=0000123456", "CIK=0000654321")
    if issue == "wrong_accession": index = index.replace("0000123456-20-000002", "0000123456-20-000003")
    if issue == "wrong_type": index = index.replace("EX-21.1", "EX-10.1")
    if issue == "wrong_document": index = index.replace("subs.htm", "other.htm")
    if issue == "duplicate_date": index = index.replace('</html>',
        '<div class="infoHead">Filing Date</div><div class="info">2020-03-05</div></html>')
    if issue == "duplicate_document": index = index.replace('</table>',
        '<tr><td>2</td><td>Subsidiaries</td><td><a href="subs.htm">subs.htm</a></td>'
        '<td>EX-21</td><td>123</td></tr></table>')
    calls = []

    def download(url, destination, **kwargs):
        calls.append(url)
        assert kwargs == {"host_consentiti": ["www.sec.gov", "sec.gov"], "public_only": True}
        assert url in (INDEX, TARGET)
        if issue == "index_unavailable" and url == INDEX:
            return {"stato": "errore", "motivo": "unavailable"}
        content = index.encode() if url == INDEX else b'<html>Subsidiaries: Synthetic Bank</html>'
        path = Path(destination) / ("index.html" if url == INDEX else "subs.htm")
        path.write_bytes(content)
        receipt = {"stato": "ok", "path": str(path), "url_finale": url,
                   "sha256": sha256(content).hexdigest()}
        if url == INDEX:
            if issue == "index_changed": receipt["sha256"] = "0" * 64
            if issue == "index_redirect": receipt["url_finale"] = url.replace("index.html", "other.html")
            if issue == "index_outside": receipt["path"] = str(tmp_path.parent / "outside.html")
        return receipt

    return filing, calls, download


@pytest.mark.parametrize("padded", [False, True])
def test_incorporated_exhibit_keeps_original_date_and_verified_reference(tmp_path, padded):
    href = TARGET.replace("/123456/", "/0000123456/") if padded else TARGET
    filing, calls, download = setup_reference(tmp_path, href=href)
    result = collect_preparation_exhibits([filing, filing], archive_root=tmp_path,
        as_of="2026-02-02", download=download)
    assert result["status"] == "ready" and not result["issues"]
    assert calls == [INDEX, TARGET]
    doc, = result["documents"]
    assert doc["published_at"] == "2020-03-05"
    assert doc["metadata"]["primary_document_published_at"] == "2026-02-01"
    assert doc["metadata"]["primary_document_id"] == filing["id"]
    assert doc["metadata"]["accession"] == "000012345620000002"
    assert "report_date" not in doc["metadata"]  # Do not date an old inventory at the new opening.
    assert doc["metadata"]["incorporation_index_url"] == INDEX
    assert doc["metadata"]["incorporation_index_sha256"] == sha256((tmp_path / "index.html").read_bytes()).hexdigest()
    assert doc["document_sha256"] == sha256((tmp_path / "subs.htm").read_bytes()).hexdigest()


@pytest.mark.parametrize("issue", ["future", "invalid_date", "wrong_issuer", "wrong_accession",
    "wrong_type", "wrong_document", "duplicate_date", "duplicate_document", "index_unavailable",
    "index_changed", "index_redirect", "index_outside"])
def test_incorporation_never_substitutes_parent_date_or_unverified_index(tmp_path, issue):
    filing, calls, download = setup_reference(tmp_path, issue=issue)
    result = collect_preparation_exhibits([filing], archive_root=tmp_path,
        as_of="2026-02-02", download=download)
    assert result["status"] == "incomplete" and result["issues"] and not result["documents"]
    assert calls == [INDEX]  # Reject before fetching the unproved exhibit.


@pytest.mark.parametrize("change", ["no_incorporation", "different_issuer", "issuer_metadata"])
def test_unqualified_reference_cannot_expand_source_boundary(tmp_path, change):
    kwargs = {}
    if change == "no_incorporation": kwargs["description"] = "Subsidiaries"
    if change == "different_issuer": kwargs["href"] = TARGET.replace("/123456/", "/654321/")
    filing, calls, download = setup_reference(tmp_path, **kwargs)
    if change == "issuer_metadata": filing["metadata"]["emittente_id"] = "CIK:0000654321"
    result = collect_preparation_exhibits([filing], archive_root=tmp_path,
        as_of="2026-02-02", download=download)
    assert result["status"] == "incomplete" and result["issues"] and not result["documents"]
    assert calls == []


@pytest.mark.parametrize("issue", [None, "wrong_issuer", "future"])
def test_shared_preparer_uses_verified_incorporation_or_stops_before_payment(tmp_path, monkeypatch, issue):
    from test_preparation_sources import _setup
    from test_input_preparation import _bundle
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation.preparation_service import prepare_and_generate

    selected = _setup(monkeypatch, tmp_path)
    original = Path(selected["archive_path"]).read_bytes()
    filing, calls, download = setup_reference(tmp_path, issue=issue)
    path = Path(filing["archive_path"])
    raw = original + path.read_bytes()
    path.write_bytes(raw)
    selected.update(filing)
    selected.update(id=sha256(raw).hexdigest(), document_sha256=sha256(raw).hexdigest())
    result = collect_preparation_evidence("SYNTH", as_of="2026-02-02", archive_root=tmp_path,
        download=download,
        price_fetch=lambda ticker, on: {"symbol": ticker, "date": on, "currency": "EUR", "close": 12.5})
    assert result["preparation_ready"] is (issue is None)
    if issue is None:
        exhibit, = [d for d in result["documents"] if d.get("metadata", {}).get("form") == "EX-21"]
        assert exhibit["published_at"] == "2020-03-05"
    else:
        assert result["documents"] == [] and result["issues"]
        outcome = prepare_and_generate(_bundle(), documents=[], source_report=result,
            propose=lambda *a: pytest.fail("unverified incorporation must not spend"), output_dir=tmp_path / "models")
        assert not outcome["valuation_usability"]["usable"]
