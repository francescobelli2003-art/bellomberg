"""Dated SEC earnings and foreign current reports; no numeric assumptions."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit

from .preparation_exhibits import _fetch_verified, _sec_parts, _target


_CALL_ACCESS = re.compile(
    r'\b(?:(?:you\s+may|please)\s+(?:access|listen\s+to)\s+(?:the\s+)?'
    r'(?:audio\s+conference|conference\s+call|webcast)|(?:a\s+)?replay\s+of\s+'
    r'(?:the\s+)?(?:conference\s+call|webcast)\s+is\s+available)\b'
    r'[^.!?]{0,500}?(https?://[^\s<>"\']+)', re.I)


def _call_references(documents):
    """Disclose explicit text references; never fetch or certify their contents."""
    acquired = {doc['url'] for doc in documents}
    for doc in documents:
        for match in _CALL_ACCESS.finditer(doc['text']):
            url = match[1].rstrip('.,;:)]}')
            yield {'url': url, 'status': 'acquired' if url in acquired else 'not_acquired',
                   'source_reference': {'document_id': doc['id'], 'text_sha256': doc['sha256'],
                       'char_start': match.start(), 'char_end_exclusive': match.end(),
                       'quote': match[0]}}


def collect_earnings_evidence(ticker, *, primary, as_of, archive_root, fetch=None,
                             download=None, max_filings=4):
    """Acquire bounded Item 2.02 filings or foreign reports and their explicit EX-99s.

    The filing date is SEC publication, not an inferred original press-release
    date. Recent-submissions coverage and selection limits stay visible. These
    documents may contain guidance; no extracted guidance or consensus is claimed.
    """
    from bs4 import BeautifulSoup
    from bellomberg.market_data.sec_edgar import get_recent_filings
    from bellomberg.market_data.lettore_trimestrali import scarica_documento, estrai_testo

    out = {"status": "incomplete", "documents": [], "issues": [],
           "numeric_guidance_extracted": False,
           "selection": {"policy": "recent_item_2_02_since_opening", "excluded": [],
                         "max_filings": max_filings, "max_exhibits_per_filing": 4},
           "limitation": "Recent SEC submissions only; 8-K/8-K-A Item 2.02 and explicit EX-99 links. Other guidance channels and 6-K are not covered. No absence of published guidance, economic completeness or forecast is inferred."}

    def issue(reason, url=None):
        out["issues"].append({"source": "SEC earnings releases", "url": url, "reason": reason})

    def document(url, path, raw, digest, published, metadata):
        extracted = estrai_testo(str(path), contenuto=raw)
        if extracted.get("stato") != "ok" or not extracted.get("testo") or extracted.get("pagine_senza_testo"):
            raise ValueError("earnings document text incomplete or unavailable")
        text = extracted["testo"]
        result = {"id": digest, "url": url, "published_at": published, "text": text,
                  "sha256": sha256(text.encode("utf-8")).hexdigest(), "document_sha256": digest,
                  "origin": "SEC earnings filing", "metadata": metadata,
                  "extraction_coverage": {k: extracted.get(k) for k in
                      ("stato", "caratteri", "pagine", "formato", "pagine_senza_testo")}}
        if extracted.get("formato") == "pdf":
            result["page_references"] = [{"pagina": p["pagina"], "inizio": p["inizio"],
                "fine": p["fine"], "sha256": sha256(p["testo"].encode("utf-8")).hexdigest()}
                for p in extracted["riferimenti"]]
        return result

    try:
        if type(max_filings) is not int or max_filings < 1:
            raise ValueError("positive max_filings required")
        if urlsplit(primary.get("url", "")).hostname not in ("www.sec.gov", "sec.gov"):
            out.update(status="not_applicable", reason="Opening source is not a verified SEC filing")
            return out
        cik, _, _ = _sec_parts(primary["url"])
        expected = "CIK:" + cik.zfill(10)
        meta = primary.get("metadata") or {}
        foreign = meta.get('form') in ('6-K', '6-K/A', '20-F', '20-F/A')
        forms = ['6-K', '6-K/A'] if foreign else ['8-K', '8-K/A']
        if foreign:
            out['selection']['policy'] = 'recent_foreign_reports_since_opening'
            out['limitation'] = ('Recent SEC 6-K/6-K-A reports and explicit same-accession EX-99 links only; '
                'supplemental narrative, not a guidance classification. Nonfinancial reports may be included; '
                'selection limits and excluded filings remain visible. No numeric guidance, consensus, '
                'absence of published guidance, economic completeness or forecast is inferred.')
        if meta.get("emittente_id") != expected:
            raise ValueError("opening SEC issuer metadata differs from its URL")
        since, cutoff = date.fromisoformat(meta["report_date"]), date.fromisoformat(as_of)
        if since > cutoff:
            raise ValueError("opening date after information cutoff")
        out["selection"]["opening_date"] = since.isoformat()
        reasons = []
        rows = (fetch or get_recent_filings)(ticker, form_types=forms,
            days=max(1, (date.today() - since).days + 1), max_items=40, motivo=reasons)
        for reason in reasons:
            issue(str(reason))
        if not isinstance(rows, list):
            raise ValueError("earnings catalog must be a list")
        if len(rows) >= 40:
            issue("Recent-filings cap reached; earnings-source coverage may be incomplete")
        candidates, quarantined = {}, set()
        for row in rows:
            try:
                if not isinstance(row, dict):
                    raise ValueError("malformed earnings catalog entry")
                filed = date.fromisoformat(row["filed_date"])
                if filed.isoformat() != row["filed_date"]:
                    raise ValueError("SEC filing date must use exact ISO format")
                items = row.get("items", [] if foreign else None)
                if not isinstance(items, list):
                    raise ValueError("SEC item list missing or malformed")
                if (row.get("form") not in forms or not foreign and "2.02" not in items
                        or not since <= filed <= cutoff):
                    out["selection"]["excluded"].append({"url": row.get("url"), "reason":
                        "outside date/form selection" if foreign else "outside date/form/Item 2.02 selection"})
                    continue
                rcik, accession, _ = _sec_parts(row["url"])
                formatted = accession[:10] + "-" + accession[10:12] + "-" + accession[12:]
                if rcik != cik or row.get("emittente_id") != expected or row.get("accession") != formatted:
                    raise ValueError("earnings catalog issuer/accession differs from verified opening issuer or URL")
                if row["url"] in quarantined:
                    continue
                if row["url"] in candidates and candidates[row["url"]] != row:
                    del candidates[row["url"]]
                    quarantined.add(row["url"])
                    raise ValueError("conflicting metadata for the same earnings filing")
                candidates[row["url"]] = deepcopy(row)
            except (ValueError, TypeError, KeyError) as exc:
                issue(str(exc), row.get("url") if isinstance(row, dict) else None)
        selected = sorted(candidates.values(), key=lambda r: (r["filed_date"], r["accession"]), reverse=True)
        if len(selected) > max_filings:
            issue("Earnings filing selection limit reached; older eligible filings excluded")
            out["selection"]["excluded"].extend({"url": r["url"], "reason": "filing limit"} for r in selected[max_filings:])
        root = Path(archive_root).resolve()
        seen = set()
        for row in selected[:max_filings]:
            try:
                parent = row["url"]
                path, raw, digest = _fetch_verified(parent, root, download or scarica_documento)
                metadata = {k: row.get(k) for k in ("emittente_id", "issuer", "form", "accession", "items")}
                metadata.update(event_date=row.get("report_date"), publication_basis="SEC catalog filing date")
                if foreign:
                    metadata['evidence_role'] = 'foreign_current_report'
                filing = document(parent, path, raw, digest, row["filed_date"], metadata)
                if foreign:
                    if not re.search(r'\bFORM\s+6\s*-\s*K(?:/A)?\b', filing['text'][:10000], re.I):
                        raise ValueError('Foreign current report has no explicit Form 6-K cover')
                    filing['origin'] = 'SEC foreign current filing'
                links = {}
                for tr in BeautifulSoup(raw, "html.parser").find_all("tr"):
                    cells = tr.find_all(("td", "th"), recursive=False)
                    labels = [c.get_text(" ", strip=True) for c in cells if c.get_text(" ", strip=True)]
                    if not labels:
                        continue
                    match = re.fullmatch(r"(?:EX-|EXHIBIT\s+)?(99(?:\.[0-9]+)?)", labels[0], re.I)
                    if not match:
                        continue
                    kind = match[1]
                    urls = {_target(parent, a["href"]) for a in tr.find_all("a", href=True)}
                    if len(urls) != 1 or parent in urls:
                        raise ValueError("earnings EX-" + kind + " has no unique attached document")
                    target = next(iter(urls))
                    if kind in links and links[kind] != target:
                        raise ValueError("conflicting earnings EX-" + kind + " links")
                    links[kind] = target
                if not links and not foreign:
                    raise ValueError("Item 2.02 filing has no supported explicit EX-99 attachment")
                if len(links) > 4:
                    raise ValueError("earnings exhibit count exceeds declared per-filing limit")
                documents = [filing]
                for kind, url in links.items():
                    target, content, rawsha = _fetch_verified(url, root, download or scarica_documento)
                    attached = {**deepcopy(metadata), "form": "EX-" + kind,
                        "primary_document_id": digest, "primary_document_url": parent,
                        "publication_basis": "explicit same-accession exhibit link in SEC-dated " + ('6-K' if foreign else '8-K')}
                    documents.append(document(url, target, content, rawsha, row["filed_date"], attached))
                for doc in documents:
                    if doc["id"] not in seen:
                        out["documents"].append(doc)
                        seen.add(doc["id"])
            except (OSError, ValueError, TypeError, KeyError) as exc:
                issue(str(exc), row.get("url"))
        references = list(_call_references(out['documents']))
        if references:
            out['referenced_materials'] = references
            out['limitation'] += (' Call-reference recognition covers explicit English access/replay '
                'phrases followed by a textual HTTP(S) URL only. Acquisition status does not certify '
                'a transcript or its completeness; unrecognised references may remain.')
            for reference in references:
                if reference['status'] == 'not_acquired':
                    issue('Explicitly referenced call material not acquired', reference['url'])
        out["status"] = "incomplete" if out["issues"] else "ready" if out["documents"] else "not_found"
        if out["status"] == "not_found":
            issue("No supported earnings filing in the selected window; this does not establish absence of company guidance")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        issue(str(exc))
    return out
