"""Primary document catalog for input preparation, reusing Filing Diff acquisitions.

No model, human approval or method-record archive is accessed here. Source dates
are publication dates from the catalog, never the date of a download. Unsupported
markets and undated IR documents remain explicit coverage gaps.
"""
from datetime import date
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import json
import re


def collect_documents(ticker, *, as_of, archive_root, filing_results=(), catalog=None,
                      download=None, max_documents=4, max_download_attempts=None,
                      selection_policy="latest"):
    """Reconcile verified local filings with the live SEC catalog before capping."""
    from bellomberg.market_data.lettore_trimestrali import estrai_testo, scarica_documento

    cutoff = date.fromisoformat(as_of)
    if type(max_documents) is not int or max_documents < 1:
        raise ValueError("max_documents must be a positive integer")
    if max_download_attempts is None:
        max_download_attempts = max_documents
    if type(max_download_attempts) is not int or max_download_attempts < 1:
        raise ValueError("max_download_attempts must be a positive integer")
    if selection_policy not in ("latest", "opening_annual_comparative"):
        raise ValueError("unknown document selection policy")
    root = Path(archive_root).resolve()
    documents, issues, archived, entries = [], [], [], []
    coverage = {"downloaded": 0, "reused": 0, "deduplicated": 0,
                "excluded": 0, "limited": 0, "max_documents": max_documents,
                "download_attempted": 0, "accepted": 0,
                "max_download_attempts": max_download_attempts,
                "selection_policy": selection_policy,
                "catalog_checked": False, "catalog_status": None}

    def issue(source, reason, *, excluded=False):
        issues.append({"source": source, "reason": reason})
        if excluded:
            coverage["excluded"] += 1

    def published_on(value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("publication date missing or invalid")
        if date.fromisoformat(value) > cutoff:
            raise ValueError("publication date after information cutoff")
        return value

    def load(candidate, *, origin):
        try:
            if not isinstance(candidate, dict):
                raise TypeError("document entry must be an object")
            published = published_on(candidate.get("filed_date"))
            url = candidate.get("url")
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("source URL missing or invalid")
            path = Path(candidate["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("source path outside archive root")
            raw = path.read_bytes()
            digest = sha256(raw).hexdigest()
            if digest != candidate.get("sha256"):
                raise ValueError("source bytes differ from archived SHA256")
            extracted = estrai_testo(str(path), contenuto=raw)
            if extracted.get("stato") not in ("ok", "parziale") or not extracted.get("testo", "").strip():
                raise ValueError("document text unavailable: " + str(extracted.get("motivo")))
            if extracted.get("pagine_senza_testo"):
                raise ValueError("document contains pages without extractable text")
            text = extracted["testo"]
            metadata = candidate.get("metadati")
            if not isinstance(metadata, dict):
                metadata = {key: candidate.get(key) for key in
                            ("emittente_id", "issuer", "form", "report_date", "accession")}
            else:
                metadata = dict(metadata)
                # Filing Diff names the verified accounting end "periodo_fine";
                # the preparation selector uses the equivalent report_date key.
                if not metadata.get("report_date") and metadata.get("periodo_fine"):
                    metadata["report_date"] = metadata["periodo_fine"]
            return {"id": digest, "url": url, "published_at": published,
                    "text": text, "sha256": sha256(text.encode("utf-8")).hexdigest(),
                    "document_sha256": digest, "origin": origin, "archive_path": str(path),
                    "metadata": metadata,
                    "extraction_coverage": {key: extracted.get(key) for key in
                        ("stato", "caratteri", "pagine", "formato", "pagine_senza_testo")}}
        except Exception as exc:
            issue(candidate.get("url") if isinstance(candidate, dict) else origin,
                  type(exc).__name__ + ": " + str(exc), excluded=True)
            return None

    for result in filing_results:
        if not isinstance(result, dict):
            issue("filing_diff", "malformed Filing Diff result", excluded=True)
            continue
        if str(result.get("ticker", "")).upper() != ticker.upper():
            issue("filing_diff", "different issuer ticker; document excluded", excluded=True)
            continue
        candidates = result.get("candidati", [])
        if not isinstance(candidates, list):
            issue("filing_diff", "candidate list malformed", excluded=True)
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                issue("filing_diff", "document entry must be an object", excluded=True)
                continue
            if candidate.get("stato") in ("verificato", "duplicato"):
                doc = load(candidate, origin="filing_diff")
                if doc is not None:
                    archived.append(doc)
        reasons = result.get("motivi") or []
        if not isinstance(reasons, list):
            issue("filing_diff", "reasons list malformed")
        else:
            for reason in reasons:
                issue("filing_diff", str(reason))

    # Local history never suppresses a freshness check. Catalog failures are
    # visible, and archived documents remain usable only with partial coverage.
    try:
        if catalog is None:
            from bellomberg.market_data.sec_edgar import get_filing_catalog
            catalog = get_filing_catalog
        response = catalog(ticker)
        if not isinstance(response, dict):
            raise TypeError("SEC catalog response must be an object")
        coverage["catalog_checked"] = True
        coverage["catalog_status"] = response.get("stato")
        reasons = response.get("motivi") or []
        if not isinstance(reasons, list):
            issue("SEC", "catalog reasons list malformed")
        else:
            for reason in reasons:
                issue("SEC", str(reason))
        if response.get("stato") not in ("ok", "parziale"):
            issue("SEC", "catalog status: " + str(response.get("stato")) + "; freshness unverified")
        else:
            if response["stato"] == "parziale":
                issue("SEC", "catalog partial; freshness coverage incomplete")
            raw_entries = response.get("documenti", [])
            if not isinstance(raw_entries, list):
                issue("SEC", "catalog document list malformed")
            else:
                for ordinal, entry in enumerate(raw_entries):
                    if not isinstance(entry, dict):
                        issue("SEC", "catalog entry must be an object", excluded=True)
                        continue
                    if entry.get("form") not in ("10-K", "10-Q", "20-F", "10-K/A", "10-Q/A", "20-F/A",
                                                 "6-K", "6-K/A"):
                        continue
                    url = entry.get("url")
                    try:
                        if str(entry.get("ticker", ticker)).upper() != ticker.upper():
                            raise ValueError("different issuer ticker")
                        parsed = urlsplit(url)
                        if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov"
                                or parsed.username or parsed.password or parsed.port not in (None, 443)):
                            raise ValueError("catalog document outside verified SEC HTTPS host")
                        published = published_on(entry.get("filed_date"))
                    except (TypeError, ValueError) as exc:
                        issue(url, type(exc).__name__ + ": " + str(exc), excluded=True)
                        continue
                    entries.append((published, ordinal, url, entry))
    except Exception as exc:
        issue("SEC", type(exc).__name__ + ": " + str(exc) + "; freshness unverified")

    # All candidates compete by filing date before max_documents is applied.
    archive_unique = {}
    for doc in archived:
        key = (doc["url"], doc["published_at"], doc["document_sha256"])
        if key in archive_unique:
            coverage["deduplicated"] += 1
        else:
            archive_unique[key] = doc
    by_reference = {}
    for doc in archive_unique.values():
        by_reference.setdefault((doc["url"], doc["published_at"]), []).append(doc)
    pool, matched, excluded_local, seen_entries = [], set(), set(), set()
    for published, ordinal, url, entry in entries:
        key = (url, published)
        if key in seen_entries:
            coverage["deduplicated"] += 1
            continue
        seen_entries.add(key)
        matches = by_reference.get(key, [])
        if len(matches) > 1:
            issue(url, "conflicting verified archive bytes for one catalog filing", excluded=True)
            excluded_local.update(id(doc) for doc in matches)
            matches = []
        local = matches[0] if matches else None
        needs_financial_proof = entry.get("form") in ("6-K", "6-K/A")
        if local is not None:
            metadata = local["metadata"]
            fields = {name: entry[name] for name in
                      ("emittente_id", "issuer", "form", "report_date", "accession") if entry.get(name)}
            conflicts = [name for name, value in fields.items()
                         if metadata.get(name) and metadata[name] != value]
            missing_proof = needs_financial_proof and (
                metadata.get("tipo") not in ("annuale", "semestrale", "trimestrale", "nove_mesi")
                or any(not metadata.get(name) or not fields.get(name)
                       for name in ("emittente_id", "report_date")))
            if conflicts or missing_proof:
                reason = ("archive/catalog metadata disagree: " + ", ".join(conflicts)
                          if conflicts else "6-K financial issuer/period/type verification missing")
                issue(url, reason, excluded=True)
                excluded_local.add(id(local))
                local = None
            else:
                # Catalog identity supplements, never overwrites, byte-verified
                # metadata. Keep the exact reference and hash for downstream use.
                additions = {name: value for name, value in fields.items() if not metadata.get(name)}
                if additions:
                    metadata.update(additions)
                    metadata["catalog_reconciliation"] = {
                        "source": "SEC submissions", "url": url, "filed_date": published,
                        "document_sha256": local["document_sha256"], "fields": fields}
                matched.add(id(local))
        # A 6-K may be a press release or unrelated notice. Only an already
        # qualified financial document can enter this pool; no catalog shortcut.
        if needs_financial_proof and local is None:
            continue
        pool.append((published, 1, -ordinal, url, entry, local))
    for doc in archive_unique.values():
        if id(doc) not in matched and id(doc) not in excluded_local:
            pool.append((doc["published_at"], 0, 0, doc["url"], None, doc))
    pool.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)

    if selection_policy == "opening_annual_comparative":
        dated = []
        for item in pool:
            metadata = item[5]["metadata"] if item[5] is not None else item[4]
            on = metadata.get("report_date")
            try:
                period = date.fromisoformat(on)
                if period > cutoff:
                    continue
            except (TypeError, ValueError):
                continue
            dated.append((period, item[0], metadata.get("form"), item))
        if dated:
            latest = max(dated, key=lambda row: row[:2])
            required = [latest[3]]
            # An amendment may contain only proxy/administrative changes. Keep
            # the full annual report instead of treating /A as its replacement.
            annuals = [row for row in dated if row[2] in ("10-K", "20-F")
                       and row[0] <= latest[0]]
            if annuals:
                required.append(max(annuals, key=lambda row: row[:2])[3])
            comparatives = [row for row in dated if row[0] < latest[0]
                           and (row[0].month, row[0].day) == (latest[0].month, latest[0].day)]
            if comparatives and latest[2] not in ("10-K", "10-K/A", "20-F", "20-F/A"):
                required.append(max(comparatives, key=lambda row: row[:2])[3])
            ordered = []
            for item in required + pool:
                if item not in ordered:
                    ordered.append(item)
            pool = ordered

    seen_bytes = set()
    for index, (_published, _priority, _order, url, entry, local) in enumerate(pool):
        if len(documents) >= max_documents:
            coverage["limited"] += len(pool) - index
            break
        if local is None:
            if coverage["download_attempted"] >= max_download_attempts:
                coverage["limited"] += 1
                issue(url, "download attempt limit reached; source not acquired")
                continue
            coverage["download_attempted"] += 1
            try:
                fetched = (download or scarica_documento)(url, str(root),
                    host_consentiti=["www.sec.gov"], public_only=True)
                if not isinstance(fetched, dict) or fetched.get("stato") != "ok":
                    detail = fetched.get("motivo") if isinstance(fetched, dict) else fetched
                    raise ValueError("download failed: " + str(detail))
                doc = load({**entry, "path": fetched.get("path"), "sha256": fetched.get("sha256")},
                           origin="SEC")
            except Exception as exc:
                issue(url, type(exc).__name__ + ": " + str(exc), excluded=True)
                continue
            if doc is None:
                continue
        else:
            doc = local
        if doc["document_sha256"] in seen_bytes:
            coverage["deduplicated"] += 1
            continue
        seen_bytes.add(doc["document_sha256"])
        documents.append(doc)
        coverage["reused" if local is not None else "downloaded"] += 1
    coverage["accepted"] = len(documents)
    if coverage["limited"]:
        issue("catalog", "document limit reached; coverage partial")
    return {"status": "ready" if documents and not issues else "partial" if documents else "incomplete",
            "documents": documents, "issues": issues, "coverage": coverage}


def company_facts_documents(filing_documents, *, as_of, fetch=None):
    """Expose primary XBRL values without conflating units, periods or accessions.

    Reuses the SEC client/cache. These are documented selections from a structured
    tool response, not claims that an entire filing has been reconciled. A filing
    absent from the cache is a declared coverage gap, never filled from older data.
    """
    documents, issues = [], []
    try:
        cutoff = date.fromisoformat(as_of)
        catalog, ciks = {}, set()
        for doc in filing_documents:
            url = urlsplit(doc["url"])
            if url.hostname != "www.sec.gov":
                continue
            match = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d{18})/[^/]+", url.path)
            issuer = (doc.get("metadata") or {}).get("emittente_id")
            if not match or issuer != "CIK:" + str(int(match[1])).zfill(10):
                raise ValueError("filing CIK metadata does not match primary SEC URL")
            if date.fromisoformat(doc["published_at"]) > cutoff:
                raise ValueError("filing published after cutoff")
            ciks.add(str(int(match[1])).zfill(10))
            catalog[match[2]] = doc["published_at"]
        if len(ciks) != 1:
            raise ValueError("one verified SEC issuer required for structured facts")
        cik = next(iter(ciks))
        if fetch is None:
            from bellomberg.market_data.sec_xbrl import _fetch_companyfacts
            fetch = _fetch_companyfacts
        response = fetch(cik)
        if not isinstance(response, dict) or str(int(response.get("cik", -1))).zfill(10) != cik:
            raise ValueError("companyfacts issuer unavailable or mismatched")
        canonical = json.dumps(response, sort_keys=True, ensure_ascii=False, allow_nan=False)
        response_hash = sha256(canonical.encode("utf-8")).hexdigest()
        grouped = {accession: [] for accession in catalog}
        for taxonomy, concepts in response["facts"].items():
            for concept, data in concepts.items():
                for unit, observations in data["units"].items():
                    for observation in observations:
                        accession = str(observation.get("accn", "")).replace("-", "")
                        if accession not in catalog or observation.get("filed") != catalog[accession]:
                            continue
                        if date.fromisoformat(observation["end"]) > cutoff:
                            continue
                        grouped[accession].append({"taxonomy": taxonomy, "concept": concept,
                            "label": data.get("label"), "unit": unit, "observation": observation})
        for accession, facts in grouped.items():
            if not facts:
                issues.append({"source": "SEC companyfacts", "reason": "no tagged facts for filing " + accession})
                continue
            text = json.dumps({"cik": cik, "issuer": response.get("entityName"), "facts": facts},
                              ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            documents.append({"id": "xbrl-" + cik + "-" + accession,
                "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK" + cik + ".json",
                "published_at": catalog[accession], "text": text,
                "sha256": sha256(text.encode("utf-8")).hexdigest(), "origin": "SEC_XBRL_tool",
                "metadata": {"emittente_id": "CIK:" + cik, "accession": accession,
                             "canonical_response_sha256": response_hash},
                "extraction_coverage": {"status": "selected_filing_facts", "selected_facts": len(facts),
                    "limitation": "tagged consolidated facts only; no legal-entity completeness certification"}})
    except (OSError, ValueError, TypeError, KeyError) as exc:
        issues.append({"source": "SEC companyfacts", "reason": type(exc).__name__ + ": " + str(exc)})
    return {"status": "ready" if documents and not issues else "partial" if documents else "incomplete",
            "documents": documents, "issues": issues}
