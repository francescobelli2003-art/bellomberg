"""Documents acquired from public catalogs, never from a user's method-record set."""
from hashlib import sha256
from pathlib import Path

import pytest


def source(tmp_path, content=b"Synthetic issuer annual report: revenue 125 million USD."):
    path = tmp_path / "report.txt"
    path.write_bytes(content)
    return {"stato": "verificato", "path": str(path), "sha256": sha256(content).hexdigest(),
            "url": "https://example.org/issuer/report", "filed_date": "2026-02-05",
            "metadati": {"emittente_id": "SYNTH", "periodo_fine": "2025-12-31"}}


def test_reuses_filing_diff_verified_bytes_and_deduplicates(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    candidate = source(tmp_path)
    checked = []
    def catalog(ticker):
        checked.append(ticker)
        return {"stato": "ok", "documenti": [], "motivi": []}
    def forbidden(*a, **k): raise AssertionError("No download needed for verified source")
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [candidate, candidate]}],
        catalog=catalog, download=forbidden)
    assert checked == ["SYNTH"]
    assert len(result["documents"]) == 1
    doc = result["documents"][0]
    assert doc["published_at"] == "2026-02-05"
    assert doc["document_sha256"] == candidate["sha256"]
    assert doc["sha256"] == sha256(doc["text"].encode()).hexdigest()
    assert "125 million USD" in doc["text"]
    assert result["coverage"]["deduplicated"] == 1
    assert result["coverage"]["download_attempted"] == 0
    assert result["coverage"]["accepted"] == 1


def test_newer_catalog_filing_beats_old_archive_under_cap(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    old = source(tmp_path, b"Old synthetic filing")
    newer = tmp_path / "newer.txt"
    newer.write_bytes(b"New synthetic filing")
    entry = {"ticker": "SYNTH", "form": "10-K", "filed_date": "2026-02-25",
             "url": "https://www.sec.gov/Archives/newer.htm"}
    calls = []
    def download(url, dest_dir, **kwargs):
        calls.append(url)
        return {"stato": "ok", "path": str(newer), "sha256": sha256(newer.read_bytes()).hexdigest()}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [old]}],
        catalog=lambda _: {"stato": "ok", "documenti": [entry], "motivi": []},
        download=download, max_documents=1)
    assert [doc["published_at"] for doc in result["documents"]] == ["2026-02-25"]
    assert calls == [entry["url"]]
    assert result["coverage"]["download_attempted"] == 1
    assert result["coverage"]["downloaded"] == result["coverage"]["accepted"] == 1
    assert result["coverage"]["limited"] == 1


def test_catalog_match_reuses_verified_archived_bytes_without_download(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    archived = source(tmp_path)
    archived["url"] = "https://www.sec.gov/Archives/exact.htm"
    entry = {"ticker": "SYNTH", "form": "10-K", "filed_date": archived["filed_date"],
             "url": archived["url"]}
    def forbidden(*a, **k): raise AssertionError("Matching verified bytes must be reused")
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [archived]}],
        catalog=lambda _: {"stato": "ok", "documenti": [entry], "motivi": []},
        download=forbidden)
    assert len(result["documents"]) == 1
    assert result["documents"][0]["document_sha256"] == archived["sha256"]
    assert result["documents"][0]["metadata"]["report_date"] == "2025-12-31"
    assert result["coverage"]["reused"] == result["coverage"]["accepted"] == 1
    assert result["coverage"]["download_attempted"] == 0


def test_bad_catalog_entry_does_not_abort_valid_entries_and_attempts_are_counted(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    raw = b"Synthetic valid report"
    downloaded = tmp_path / "valid.txt"
    downloaded.write_bytes(raw)
    valid = {"ticker": "SYNTH", "form": "10-K", "filed_date": "2026-02-20",
             "url": "https://www.sec.gov/Archives/valid.htm"}
    malformed = {"ticker": "SYNTH", "form": "10-Q", "filed_date": "not-a-date",
                 "url": "https://www.sec.gov/Archives/bad.htm"}
    attempts = []
    def download(url, dest_dir, **kwargs):
        attempts.append(url)
        return {"stato": "ok", "path": str(downloaded), "sha256": sha256(raw).hexdigest()}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        catalog=lambda _: {"stato": "ok", "documenti": [malformed, valid], "motivi": []},
        download=download)
    assert attempts == [valid["url"]]
    assert result["documents"][0]["published_at"] == valid["filed_date"]
    assert result["coverage"]["download_attempted"] == result["coverage"]["accepted"] == 1
    assert any("date" in issue["reason"] for issue in result["issues"])


def test_failed_newest_download_does_not_hide_older_archive(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    old = source(tmp_path)
    recent = {"ticker": "SYNTH", "form": "10-K", "filed_date": "2026-02-25",
              "url": "https://www.sec.gov/Archives/unavailable.htm"}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [old]}],
        catalog=lambda _: {"stato": "ok", "documenti": [recent], "motivi": []},
        download=lambda *a, **k: {"stato": "errore", "motivo": "fixture unavailable"},
        max_documents=1)
    assert [doc["published_at"] for doc in result["documents"]] == [old["filed_date"]]
    assert result["status"] == "partial"
    assert result["coverage"]["download_attempted"] == 1
    assert result["coverage"]["downloaded"] == 0
    assert result["coverage"]["reused"] == result["coverage"]["accepted"] == 1


def test_unavailable_catalog_keeps_verified_archive_with_explicit_freshness_gap(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    archived = source(tmp_path)
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [archived]}],
        catalog=lambda _: {"stato": "errore", "documenti": [], "motivi": ["fixture unavailable"]},
        download=lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected download")))
    assert len(result["documents"]) == 1 and result["status"] == "partial"
    assert result["coverage"]["catalog_status"] == "errore"
    assert any("freshness unverified" in issue["reason"] for issue in result["issues"])


def test_one_malformed_archive_candidate_does_not_hide_valid_candidate(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    good = source(tmp_path)
    invalid = {**good, "filed_date": "not-a-date"}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [invalid, good]}],
        catalog=lambda _: {"stato": "ok", "documenti": [], "motivi": []})
    assert len(result["documents"]) == 1 and result["status"] == "partial"
    assert result["coverage"]["excluded"] == 1
    assert result["coverage"]["accepted"] == 1


def test_failed_catalog_download_then_next_entry_can_fill_cap(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    raw = b"Older but verified synthetic filing"
    path = tmp_path / "older.txt"
    path.write_bytes(raw)
    entries = [{"ticker": "SYNTH", "form": "10-K", "filed_date": day,
                "url": "https://www.sec.gov/Archives/" + name + ".htm"}
               for day, name in (("2026-02-25", "newer"), ("2026-02-20", "older"))]
    attempts = []
    def download(url, dest_dir, **kwargs):
        attempts.append(url)
        if url == entries[0]["url"]:
            return {"stato": "errore", "motivo": "fixture unavailable"}
        return {"stato": "ok", "path": str(path), "sha256": sha256(raw).hexdigest()}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        catalog=lambda _: {"stato": "ok", "documenti": entries, "motivi": []},
        download=download, max_documents=1, max_download_attempts=2)
    assert attempts == [entry["url"] for entry in entries]
    assert result["documents"][0]["published_at"] == "2026-02-20"
    assert result["coverage"]["download_attempted"] == 2
    assert result["coverage"]["downloaded"] == result["coverage"]["accepted"] == 1


def test_new_install_fetches_source_documents_without_private_archive(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    candidate = source(tmp_path)
    entry = {"ticker": "SYNTH", "form": "10-K", "url": "https://www.sec.gov/Archives/synthetic.htm",
             "filed_date": "2026-02-05", "report_date": "2025-12-31", "emittente_id": "CIK:SYNTH"}
    called = []
    def download(url, dest_dir, **kwargs):
        called.append((url, kwargs))
        return {"stato": "ok", "path": candidate["path"], "sha256": candidate["sha256"]}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        catalog=lambda ticker: {"stato": "ok", "documenti": [entry], "motivi": []}, download=download)
    assert result["documents"][0]["url"] == entry["url"]
    assert called[0][1]["public_only"] is True
    assert called[0][1]["host_consentiti"] == ["www.sec.gov"]
    assert result["coverage"]["downloaded"] == 1


def test_undated_future_changed_or_other_issuer_document_is_not_evidence(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    original = source(tmp_path)
    candidates = [{**original, "filed_date": None}, {**original, "filed_date": "2027-01-01"},
                  {**original, "sha256": "a" * 64}]
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": candidates},
                        {"ticker": "OTHER", "candidati": [original]}],
        catalog=lambda t: {"stato": "non_disponibile", "documenti": [], "motivi": ["No SEC issuer"]})
    assert result["documents"] == []
    assert len(result["issues"]) >= 4
    assert result["status"] == "incomplete"


def test_source_outside_archive_is_not_read(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    candidate = source(tmp_path)
    archive = tmp_path / "allowed"
    archive.mkdir()
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=archive,
        filing_results=[{"ticker": "SYNTH", "candidati": [candidate]}],
        catalog=lambda t: {"stato": "errore", "documenti": [], "motivi": ["catalog unavailable"]})
    assert result["documents"] == []
    assert "outside" in str(result["issues"])


def test_structured_facts_preserve_exact_unit_period_and_filing_identity():
    from bellomberg.valuation.valuation_sources import company_facts_documents
    import json
    source_doc = {"url": "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/annual.htm",
                  "published_at": "2026-02-05", "metadata": {"emittente_id": "CIK:0000000001"}}
    observation = {"val": 125000000, "end": "2025-12-31", "filed": "2026-02-05",
                   "accn": "0000000001-26-000001", "form": "10-K"}
    payload = {"cik": 1, "entityName": "SYNTH", "facts": {"us-gaap": {"Revenue": {
        "label": "Revenue", "units": {"USD": [observation,
            {**observation, "filed": "2027-02-05", "val": 999}]}}}}}
    result = company_facts_documents([source_doc], as_of="2026-03-01", fetch=lambda _: payload)
    assert result["status"] == "ready", result
    doc = result["documents"][0]
    fact = json.loads(doc["text"])["facts"][0]
    assert fact["unit"] == "USD" and fact["observation"] == observation
    assert doc["published_at"] == "2026-02-05"
    assert doc["extraction_coverage"]["selected_facts"] == 1
    payload["cik"] = 2
    result = company_facts_documents([source_doc], as_of="2026-03-01", fetch=lambda _: payload)
    assert result["status"] == "incomplete" and result["documents"] == []


def test_failed_downloads_are_bounded_and_do_not_hide_reusable_history(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    candidate = source(tmp_path)
    entries = [{"ticker": "SYNTH", "form": "10-K", "filed_date": f"2026-02-{day:02}",
                "url": f"https://www.sec.gov/Archives/edgar/data/1/filing-{day}.htm"}
               for day in (10, 11, 12)]
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(args[0])
        return {"stato": "errore", "motivo": "synthetic network outage"}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [candidate]}], max_documents=1,
        catalog=lambda *_: {"stato": "ok", "documenti": entries}, download=unavailable)
    assert len(calls) == result["coverage"]["download_attempted"] == 1
    assert result["coverage"]["reused"] == 1 and result["status"] == "partial"
    assert any("attempt limit" in row["reason"] for row in result["issues"])


def test_opening_and_full_annual_roles_are_selected_before_download_cap(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    entries = [{"ticker": "SYNTH", "form": form, "filed_date": filed, "report_date": on,
                "url": f"https://www.sec.gov/Archives/{name}.txt"}
               for name, form, filed, on in (
                   ("latest", "10-Q", "2026-08-05", "2026-06-30"),
                   ("quarter-amendment", "10-Q/A", "2026-07-04", "2026-03-31"),
                   ("previous-quarter", "10-Q", "2026-05-01", "2026-03-31"),
                   ("annual-amendment", "10-K/A", "2026-04-01", "2025-12-31"),
                   ("annual", "10-K", "2026-02-01", "2025-12-31"),
                   ("comparative", "10-Q", "2025-08-05", "2025-06-30"))]
    calls = []
    def download(url, *_a, **_k):
        calls.append(url)
        raw = ("Synthetic verified statement for " + url).encode()
        target = tmp_path / url.rsplit("/", 1)[-1]
        target.write_bytes(raw)
        return {"stato": "ok", "path": str(target), "sha256": sha256(raw).hexdigest()}
    result = collect_documents("SYNTH", as_of="2026-09-01", archive_root=tmp_path,
        catalog=lambda *_: {"stato": "ok", "documenti": entries}, download=download,
        max_documents=3, selection_policy="opening_annual_comparative")
    assert [url.rsplit("/", 1)[-1] for url in calls] == ["latest.txt", "annual.txt", "comparative.txt"]
    assert result["coverage"]["accepted"] == 3


def matched_filing(tmp_path, form="6-K"):
    archived = source(tmp_path)
    archived["url"] = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/report.htm"
    archived["metadati"].update(emittente_id="CIK:0000000001", tipo="semestrale")
    entry = {"ticker": "SYNTH", "form": form, "filed_date": archived["filed_date"],
             "url": archived["url"], "emittente_id": "CIK:0000000001",
             "issuer": "Synthetic Issuer SA", "report_date": "2025-12-31",
             "accession": "0000000001-26-000001"}
    return archived, entry


def collect_matched(tmp_path, archived, entry):
    from bellomberg.valuation.valuation_sources import collect_documents
    calls = []
    def download(*args, **kwargs):
        calls.append(args[0])
        return {"stato": "errore", "motivo": "unexpected download"}
    result = collect_documents("SYNTH", as_of="2026-03-01", archive_root=tmp_path,
        filing_results=[{"ticker": "SYNTH", "candidati": [archived]}],
        catalog=lambda _: {"stato": "ok", "documenti": [entry]}, download=download)
    return result, calls


@pytest.mark.parametrize("form", ["6-K", "6-K/A", "20-F", "10-Q"])
def test_verified_filing_recovers_catalog_identity_with_provenance(tmp_path, form):
    from copy import deepcopy
    archived, entry = matched_filing(tmp_path, form)
    before = deepcopy(archived)
    result, calls = collect_matched(tmp_path, archived, entry)
    assert calls == [] and result["coverage"]["reused"] == 1
    doc = result["documents"][0]
    meta = doc["metadata"]
    assert {key: meta[key] for key in ("issuer", "form", "accession")} == {
        key: entry[key] for key in ("issuer", "form", "accession")}
    assert meta["periodo_fine"] == meta["report_date"] == entry["report_date"]
    proof = meta["catalog_reconciliation"]
    assert proof["url"] == entry["url"] and proof["filed_date"] == entry["filed_date"]
    assert proof["document_sha256"] == archived["sha256"] == doc["document_sha256"]
    assert proof["fields"]["emittente_id"] == entry["emittente_id"]
    assert archived == before  # Source verification metadata are not rewritten.


@pytest.mark.parametrize("field,value", [
    ("emittente_id", "CIK:0000000002"), ("report_date", "2025-06-30"),
    ("issuer", "Different Issuer"), ("form", "20-F"),
    ("accession", "0000000001-26-000002"),
])
def test_conflicting_6k_metadata_cannot_reenter_as_an_unmatched_archive(tmp_path, field, value):
    archived, entry = matched_filing(tmp_path)
    archived["metadati"][field] = value
    result, calls = collect_matched(tmp_path, archived, entry)
    assert result["documents"] == [] and result["status"] == "incomplete"
    assert calls == []  # A 6-K catalog label alone is not financial qualification.
    assert any("disagree" in item["reason"] for item in result["issues"])


@pytest.mark.parametrize("gap", ["unverified", "changed_bytes", "issuer", "period", "type"])
def test_6k_catalog_does_not_supply_missing_financial_verification(tmp_path, gap):
    archived, entry = matched_filing(tmp_path)
    if gap == "unverified":
        archived["stato"] = "scaricato"
    elif gap == "changed_bytes":
        archived["sha256"] = "f" * 64
    else:
        archived["metadati"].pop({"issuer": "emittente_id", "period": "periodo_fine",
                                  "type": "tipo"}[gap])
    result, calls = collect_matched(tmp_path, archived, entry)
    assert result["documents"] == [] and calls == []


@pytest.mark.parametrize("field", ["url", "filed_date"])
def test_catalog_metadata_needs_exact_document_reference(tmp_path, field):
    archived, entry = matched_filing(tmp_path)
    entry[field] = (entry[field] + "?different=1" if field == "url" else "2026-02-06")
    result, calls = collect_matched(tmp_path, archived, entry)
    assert calls == []
    assert "issuer" not in result["documents"][0]["metadata"]
    assert "catalog_reconciliation" not in result["documents"][0]["metadata"]
