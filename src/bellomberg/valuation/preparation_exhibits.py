"""Acquire financial statements and subsidiary lists explicitly attached to SEC filings.

Same-accession links and index-verified incorporation references are supported.
Acquisition does not certify complete economic inputs or a current legal inventory.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit


def _sec_parts(url):
    target = urlsplit(url)
    match = re.fullmatch(r"/Archives/edgar/data/([0-9]{1,10})/([0-9]{18})/([A-Za-z0-9_.-]+)", target.path)
    if (target.scheme != "https" or target.hostname not in ("www.sec.gov", "sec.gov")
            or target.username or target.password or target.port not in (None, 443)
            or target.query or not match or int(match[1]) == 0):
        raise ValueError("exhibit must be a document in the same SEC accession")
    return str(int(match[1])), match[2], match[3]


def _target(parent, href, *, incorporated=False):
    source = _sec_parts(parent)
    target = _sec_parts(urljoin(parent, href))
    if target[0] != source[0] or (target[1] != source[1] and not incorporated):
        raise ValueError("exhibit must be a document in the same SEC accession or an explicit same-issuer incorporation reference")
    return "https://www.sec.gov/Archives/edgar/data/" + "/".join(target)


def _fetch_verified(url, root, download):
    fetched = download(url, str(root), host_consentiti=["www.sec.gov", "sec.gov"], public_only=True)
    if fetched.get("stato") != "ok":
        raise ValueError("exhibit source unavailable: " + str(fetched.get("motivo")))
    final = fetched.get("url_finale")
    if not isinstance(final, str) or urlsplit(final).scheme != "https":
        raise ValueError("exhibit final HTTPS URL missing")
    if _target(url, final) != url:
        raise ValueError("exhibit redirected to a different document")
    path = Path(fetched["path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("exhibit outside verified archive")
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    if digest != fetched.get("sha256"):
        raise ValueError("exhibit byte SHA256 changed")
    return path, raw, digest


def _incorporation_proof(url, kind, *, primary, root, download):
    """A prior exhibit retains its original filing date, proven by its own index."""
    from bs4 import BeautifulSoup
    cik, accession, _ = _sec_parts(url)
    if (primary.get("metadata") or {}).get("emittente_id") != "CIK:" + cik.zfill(10):
        raise ValueError("incorporation issuer differs from primary filing metadata")
    formatted = accession[:10] + "-" + accession[10:12] + "-" + accession[12:]
    index_url = url.rsplit("/", 1)[0] + "/" + formatted + "-index.html"
    _, raw, digest = _fetch_verified(index_url, root, download)
    soup = BeautifulSoup(raw, "html.parser")
    accessions = re.findall(r"SEC Accession No\.\s*([0-9]{10}-[0-9]{2}-[0-9]{6})", soup.get_text(" ", strip=True))
    if accessions != [formatted]:
        raise ValueError("incorporation index accession missing or ambiguous")
    issuers = []
    for name in soup.select(".companyInfo .companyName"):
        for anchor in name.find_all("a", href=True):
            issuers.extend(parse_qs(urlsplit(anchor["href"]).query).get("CIK", []))
    if issuers != [cik.zfill(10)]:
        raise ValueError("incorporation index issuer missing or ambiguous")
    dates = []
    for heading in soup.select(".infoHead"):
        if heading.get_text(" ", strip=True) == "Filing Date":
            value = heading.find_next_sibling()
            if value is None or "info" not in value.get("class", []):
                raise ValueError("incorporation index filing date missing")
            dates.append(value.get_text(" ", strip=True))
    if (len(dates) != 1 or date.fromisoformat(dates[0]).isoformat() != dates[0]
            or date.fromisoformat(dates[0]) > date.fromisoformat(primary["published_at"])):
        raise ValueError("incorporation index filing date invalid or after referencing filing")
    matches = []
    for table in soup.find_all("table", attrs={"summary": "Document Format Files"}):
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) != 5:
                continue
            anchors = cells[2].find_all("a", href=True)
            if len(anchors) == 1 and _target(index_url, anchors[0]["href"]) == url:
                matches.append(cells[3].get_text(" ", strip=True))
    if len(matches) != 1 or not re.fullmatch("EX-" + kind + r"(?:\.[0-9]+)?", matches[0]):
        raise ValueError("incorporation index does not identify one matching exhibit type and document")
    return dates[0], {"accession": accession, "incorporation_index_url": index_url,
        "incorporation_index_sha256": digest,
        "primary_document_published_at": primary["published_at"],
        "publication_basis": "original filing date in verified SEC index of explicitly incorporated exhibit"}


def collect_preparation_exhibits(primary_documents, *, archive_root, as_of, download=None):
    from bs4 import BeautifulSoup
    from bellomberg.market_data.lettore_trimestrali import estrai_testo, scarica_documento

    root, cutoff = Path(archive_root).resolve(), date.fromisoformat(as_of)
    documents, issues, declared = [], [], []
    accepted = set()
    for primary in primary_documents:
        meta = primary.get("metadata") or {}
        if meta.get("form") not in ("10-K", "20-F"):
            continue
        parent = primary.get("url", "")
        if urlsplit(parent).hostname not in ("www.sec.gov", "sec.gov"):
            continue
        try:
            parent_url = _target(parent, parent)
            path = Path(primary["archive_path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("primary filing outside verified archive")
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != primary["document_sha256"]:
                raise ValueError("primary filing byte SHA256 changed")
            published = primary["published_at"]
            if date.fromisoformat(published) > cutoff:
                raise ValueError("primary filing publication after cutoff")
            soup = BeautifulSoup(raw, "html.parser")
            links = {}
            for row in soup.find_all("tr"):
                cells = row.find_all(("td", "th"), recursive=False)
                if not cells:
                    continue
                label = cells[0].get_text(" ", strip=True)
                marker = re.fullmatch(r"(?:EX-|EXHIBIT\s+)?(13|21)(?:\.\d+)?", label, re.I)
                if not marker:
                    continue
                kind = marker[1]
                description = " ".join(c.get_text(" ", strip=True) for c in cells[1:])
                relevant = r"annual\s+report|financial\s+statements" if kind == "13" else r"subsidiar"
                if not re.search(relevant, description, re.I):
                    continue
                declared.append({"primary_document_id": primary["id"], "exhibit": kind,
                                 "description": description})
                incorporated = bool(re.search(r"\bincorporated\b.*\bby\s+reference\b", description, re.I))
                urls = {_target(parent_url, a["href"], incorporated=incorporated) for a in row.find_all("a", href=True)}
                if len(urls) != 1:
                    raise ValueError("declared exhibit " + kind + " has no unique document link")
                url = urls.pop()
                if url == parent_url:
                    raise ValueError("declared exhibit " + kind +
                                     " points to primary without a verified section")
                if kind in links and links[kind] != url:
                    raise ValueError("conflicting links for exhibit " + kind)
                links[kind] = url
            for kind, url in links.items():
                key = (primary["id"], kind, url)
                if key in accepted:
                    continue
                document_meta = {**deepcopy(meta), "form": "EX-" + kind,
                    "primary_document_id": primary["id"], "primary_document_url": parent_url,
                    "publication_basis": "explicit same-accession exhibit link in dated primary filing"}
                document_date = published
                if _sec_parts(parent_url)[1] != _sec_parts(url)[1]:
                    document_date, proof = _incorporation_proof(url, kind, primary=primary,
                        root=root, download=download or scarica_documento)
                    document_meta.pop("report_date", None)
                    document_meta.update(proof)
                target, content, digest = _fetch_verified(url, root, download or scarica_documento)
                extracted = estrai_testo(str(target), contenuto=content)
                if extracted.get("stato") != "ok" or not extracted.get("testo") or extracted.get("pagine_senza_testo"):
                    raise ValueError("exhibit text incomplete or unavailable")
                text = extracted["testo"]
                document = {"id": digest, "url": url, "published_at": document_date,
                    "text": text, "sha256": sha256(text.encode("utf-8")).hexdigest(),
                    "document_sha256": digest, "origin": "SEC attached exhibit",
                    "metadata": document_meta,
                    "extraction_coverage": {k: extracted.get(k) for k in
                        ("stato", "caratteri", "pagine", "formato", "pagine_senza_testo")}}
                if extracted.get("formato") == "pdf":
                    document["page_references"] = [{"pagina": p["pagina"], "inizio": p["inizio"],
                        "fine": p["fine"], "sha256": sha256(p["testo"].encode("utf-8")).hexdigest()}
                        for p in extracted["riferimenti"]]
                documents.append(document)
                accepted.add(key)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({"source": "SEC attached exhibits", "primary_document_id": primary.get("id"),
                           "reason": type(exc).__name__ + ": " + str(exc)})
    return {"status": "incomplete" if issues else "ready" if declared else "not_applicable",
            "documents": documents, "issues": issues, "declared": declared,
            "limitation": "Only explicit EX-13/EX-21 links: same accession or same-issuer incorporation verified against its original SEC index. Original dates retained; no inferred current ownership or numerical completeness."}
