"""Synthetic SEC exhibits must be acquired, or block paid preparation explicitly."""
from hashlib import sha256
from pathlib import Path
import pytest

from bellomberg.valuation.preparation_exhibits import collect_preparation_exhibits


PARENT = "https://www.sec.gov/Archives/edgar/data/123456/000012345626000001/annual.htm"


def primary(tmp_path, *, href="financials.htm", number="13", description="Annual Report",
            label_tag="td"):
    raw = (f'<html><table><tr><{label_tag}>{number}</{label_tag}><td><a href="{href}">{description}</a>'
           '</td></tr></table></html>').encode()
    path = tmp_path / "annual.htm"
    path.write_bytes(raw)
    digest = sha256(raw).hexdigest()
    return {"id": digest, "document_sha256": digest, "url": PARENT,
            "archive_path": str(path), "published_at": "2026-02-01",
            "metadata": {"form": "10-K", "report_date": "2025-12-31", "emittente_id": "CIK:0000123456"}}


def download_fixture(tmp_path, calls, *, problem=None):
    def download(url, dest_dir, **kwargs):
        calls.append(url)
        assert kwargs["public_only"] is True
        assert kwargs["host_consentiti"] == ["www.sec.gov", "sec.gov"]
        path = tmp_path / "financials.htm"
        path.write_text("<html>Synthetic financial statements and parent cash restrictions.</html>", encoding="utf-8")
        row = {"stato": "ok", "path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "url_finale": url}
        if problem == "unavailable":
            return {"stato": "errore", "motivo": "synthetic source unavailable"}
        if problem == "changed": row["sha256"] = "0" * 64
        if problem == "outside": row["path"] = str(tmp_path.parent / "not-in-archive.htm")
        if problem == "redirect": row["url_finale"] = url.replace("financials.htm", "other.htm")
        if problem == "no_final": row.pop("url_finale")
        if problem == "relative_final": row["url_finale"] = "financials.htm"
        if problem == "empty": path.write_text("<html></html>", encoding="utf-8"); row["sha256"] = sha256(path.read_bytes()).hexdigest()
        return row
    return download


def test_explicit_financial_exhibit_has_verified_bytes_dates_and_parent_lineage(tmp_path):
    filing = primary(tmp_path)
    calls = []
    result = collect_preparation_exhibits([filing, filing], archive_root=tmp_path, as_of="2026-02-02",
                                         download=download_fixture(tmp_path, calls))
    assert result["status"] == "ready" and not result["issues"]
    assert calls == [PARENT.replace("annual.htm", "financials.htm")]
    document, = result["documents"]
    assert document["metadata"]["form"] == "EX-13"
    assert document["metadata"]["primary_document_id"] == filing["id"]
    assert document["published_at"] == filing["published_at"]
    assert document["sha256"] == sha256(document["text"].encode()).hexdigest()
    assert "restrictions" in document["text"] and "archive_path" not in document


@pytest.mark.parametrize("href", [
    "https://example.org/financials.htm", "../000012345625000001/financials.htm",
    "https://www.sec.gov/Archives/edgar/data/654321/000012345626000001/financials.htm",
    "http://www.sec.gov/Archives/edgar/data/123456/000012345626000001/financials.htm",
    "financials.htm?unverified=1", "https://user@www.sec.gov/Archives/edgar/data/123456/000012345626000001/financials.htm",
])
def test_outside_accession_or_ambiguous_link_blocks_without_download(tmp_path, href):
    result = collect_preparation_exhibits([primary(tmp_path, href=href)], archive_root=tmp_path,
        as_of="2026-02-02", download=lambda *a, **k: pytest.fail("unsafe download"))
    assert result["status"] == "incomplete" and not result["documents"]
    assert "same SEC accession" in result["issues"][0]["reason"]


@pytest.mark.parametrize("problem", ["unavailable", "changed", "outside", "redirect",
                                     "no_final", "relative_final", "empty"])
def test_failed_or_unattested_attachment_is_a_visible_gap(tmp_path, problem):
    result = collect_preparation_exhibits([primary(tmp_path)], archive_root=tmp_path, as_of="2026-02-02",
                                         download=download_fixture(tmp_path, [], problem=problem))
    assert result["status"] == "incomplete" and result["issues"] and not result["documents"]


@pytest.mark.parametrize("href", ["#financials", "annual.htm"])
def test_same_document_link_without_verified_section_is_incomplete(tmp_path, href):
    filing = primary(tmp_path, href=href)
    result = collect_preparation_exhibits([filing], archive_root=tmp_path, as_of="2026-02-02",
        download=lambda *a, **k: pytest.fail("inline section needs no download"))
    assert result["status"] == "incomplete" and not result["documents"]
    assert result["declared"] and result["issues"]


@pytest.mark.parametrize("number,label_tag", [("13", "th"), ("Exhibit 13", "td"),
                                                ("21", "th"), ("Exhibit 21", "td")])
def test_explicit_exhibit_declaration_in_header_or_expanded_label_cannot_be_ignored(
        tmp_path, number, label_tag):
    filing = primary(tmp_path, number=number,
                     description="Annual Report" if "13" in number else "Subsidiaries",
                     label_tag=label_tag)
    calls = []
    result = collect_preparation_exhibits([filing], archive_root=tmp_path,
        as_of="2026-02-02", download=download_fixture(tmp_path, calls, problem="unavailable"))
    assert result["status"] == "incomplete" and not result["documents"]
    assert len(result["declared"]) == 1 and result["issues"] and len(calls) == 1


def test_unrelated_exhibits_are_ignored(tmp_path):
    filing = primary(tmp_path, number="10.3", description="Compensation plan")
    result = collect_preparation_exhibits([filing], archive_root=tmp_path, as_of="2026-02-02",
        download=lambda *a, **k: pytest.fail("unrelated exhibit"))
    assert result["status"] == "not_applicable"


@pytest.mark.parametrize("problem", ["changed", "future", "missing_link", "conflict"])
def test_primary_corruption_or_ambiguous_declaration_blocks_before_download(tmp_path, problem):
    filing = primary(tmp_path)
    if problem == "changed": filing["document_sha256"] = "0" * 64
    elif problem == "future": filing["published_at"] = "2026-02-03"
    else:
        path = Path(filing["archive_path"])
        raw = (b'<table><tr><td>13</td><td>Annual Report</td></tr></table>' if problem == "missing_link" else
               path.read_bytes() + b'<table><tr><td>13</td><td><a href="other.htm">Annual Report</a></td></tr></table>')
        path.write_bytes(raw)
        filing["id"] = filing["document_sha256"] = sha256(raw).hexdigest()
    result = collect_preparation_exhibits([filing], archive_root=tmp_path, as_of="2026-02-02",
        download=lambda *a, **k: pytest.fail("unattested primary"))
    assert result["status"] == "incomplete" and not result["documents"]


@pytest.mark.parametrize("unavailable", [False, True])
def test_shared_collector_includes_exhibit_or_stops_before_ai(tmp_path, monkeypatch, unavailable):
    from test_preparation_sources import _setup
    from test_input_preparation import _bundle
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation.preparation_service import prepare_and_generate
    filing = _setup(monkeypatch, tmp_path)
    raw = Path(filing["archive_path"]).read_bytes() + (
        b'<table><tr><td>21</td><td><a href="financials.htm">Subsidiaries</a></td></tr></table>')
    Path(filing["archive_path"]).write_bytes(raw)
    filing.update(url=PARENT, id=sha256(raw).hexdigest(), document_sha256=sha256(raw).hexdigest())
    result = collect_preparation_evidence("SYNTH", as_of="2026-02-02", archive_root=tmp_path,
        download=download_fixture(tmp_path, [], problem="unavailable" if unavailable else None),
        price_fetch=lambda ticker, on: {"symbol": ticker, "date": on, "currency": "EUR", "close": 12.5})
    assert result["preparation_ready"] is not unavailable
    if unavailable:
        assert result["documents"] == [] and result["issues"]
        model = prepare_and_generate(_bundle(), documents=result["documents"], source_report=result,
            propose=lambda *a: pytest.fail("missing declared exhibit must not spend"), output_dir=tmp_path / "models")
        assert not model["valuation_usability"]["usable"]
    else:
        assert any(doc.get("metadata", {}).get("form") == "EX-21" for doc in result["documents"])


@pytest.mark.parametrize("row", [
    '<tr><td>13</td><td><a href="#missing-section">Annual Report</a></td></tr>',
    '<tr><th>13</th><td><a href="missing.htm">Annual Report</a></td></tr>',
    '<tr><td>Exhibit 21</td><td><a href="missing.htm">Subsidiaries</a></td></tr>',
])
def test_declared_but_unverified_exhibit_blocks_paid_preparation(tmp_path, monkeypatch, row):
    from test_preparation_sources import _setup
    from test_input_preparation import _bundle
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation.preparation_service import prepare_and_generate

    filing = _setup(monkeypatch, tmp_path)
    path = Path(filing["archive_path"])
    raw = path.read_bytes() + ("<table>" + row + "</table>").encode()
    path.write_bytes(raw)
    filing.update(url=PARENT, id=sha256(raw).hexdigest(), document_sha256=sha256(raw).hexdigest())
    report = collect_preparation_evidence("SYNTH", as_of="2026-02-02", archive_root=tmp_path,
        download=download_fixture(tmp_path, [], problem="unavailable"),
        price_fetch=lambda ticker, on: {"symbol": ticker, "date": on, "currency": "EUR", "close": 12.5})
    assert not report["preparation_ready"] and not report["documents"]
    assert report["supplemental_exhibits"]["declared"] and report["issues"]
    model = prepare_and_generate(_bundle(), documents=report["documents"], source_report=report,
        propose=lambda *a: pytest.fail("unverified declared exhibit must not spend"),
        output_dir=tmp_path / "models")
    assert not model["valuation_usability"]["usable"]
