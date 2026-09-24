"""Dated document evidence, without pretending retrieval is publication.

The PDF entry point reuses the existing reader on the exact hashed bytes. It
attests extraction and page positions, not the economics or issuer metadata.
Receipt authenticity remains the acquiring caller's responsibility.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import re
from urllib.parse import urlsplit


def valid_source_url(raw):
    """Syntactic validation only; this does not perform DNS or network access."""
    if not isinstance(raw, str) or not raw or any(ch.isspace() or ord(ch) < 32 for ch in raw):
        return False
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        if (parts.scheme not in ("http", "https") or not host or parts.username is not None
                or parts.password is not None or (parts.port is not None and parts.port == 0)):
            return False
        try:
            ip_address(host)
            return True
        except ValueError:
            ascii_host = host.rstrip(".").encode("idna").decode("ascii")
            return len(ascii_host) <= 253 and all(re.fullmatch(
                r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in ascii_host.split("."))
    except (ValueError, UnicodeError):
        return False


def _day(raw):
    if not isinstance(raw, str):
        raise ValueError("data ISO richiesta")
    value = date.fromisoformat(raw)
    if value.isoformat() != raw:
        raise ValueError("data ISO richiesta")
    return value


def _receipt(document):
    receipt = document.get("retrieval")
    if not isinstance(receipt, dict) or set(receipt) != {"url", "document_sha256", "retrieved_at"}:
        raise ValueError("ricevuta download URL/SHA-256/timestamp richiesta")
    digest = document.get("document_sha256")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or receipt["document_sha256"] != digest or receipt["url"] != document.get("url")):
        raise ValueError("ricevuta download diversa da URL/SHA-256 del documento")
    raw = receipt["retrieved_at"]
    if not isinstance(raw, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", raw):
        raise ValueError("timestamp download ISO con timezone richiesto")
    stamp = datetime.fromisoformat(raw)
    return stamp.astimezone(timezone.utc).date()


def source_dates(document, cutoff):
    """Return an explicit availability basis; no metadata-date fallbacks.

    Unknown publication requires opt-in plus an exact download receipt. It can
    support an analysis only from the observed download day (UTC), never from
    the report period, HTTP Last-Modified or PDF creation/modification date.
    """
    published_raw = document.get("published_at")
    published = _day(published_raw) if published_raw is not None else None
    basis = document.get("availability_basis", "publication")
    if published is not None:
        if basis != "publication":
            raise ValueError("pubblicazione e disponibilita dichiarate con basi discordanti")
        available = published
        if document.get("retrieval") is not None and _receipt(document) < published:
            raise ValueError("ricevuta download precedente alla pubblicazione dichiarata")
    elif basis == "observed_download":
        available = _receipt(document)
    else:
        raise ValueError("pubblicazione ignota: disponibilita osservata non documentata")
    if available > cutoff:
        raise ValueError("fonte disponibile dopo il cutoff")
    if "available_at" in document and document["available_at"] != available.isoformat():
        raise ValueError("available_at diversa dalla base documentata")
    return {"published_at": published.isoformat() if published else None,
            "available_at": available.isoformat(), "availability_basis": basis,
            "retrieval": deepcopy(document.get("retrieval"))}


def verify_page_references(body, references):
    """Check complete physical-page offsets against the original extracted text."""
    if not isinstance(references, list) or not references:
        raise ValueError("riferimenti pagine PDF assenti")
    position = 0
    for number, page in enumerate(references, 1):
        if (not isinstance(page, dict) or set(page) != {"pagina", "inizio", "fine", "sha256"}
                or type(page["pagina"]) is not int or page["pagina"] != number
                or type(page["inizio"]) is not int or page["inizio"] != position
                or type(page["fine"]) is not int or not position <= page["fine"] <= len(body)):
            raise ValueError("offset/numero pagina PDF non coerente")
        end = page["fine"]
        if sha256(body[position:end].encode("utf-8")).hexdigest() != page["sha256"]:
            raise ValueError("SHA-256 pagina PDF diverso dal testo")
        if number < len(references) and body[end:end + 1] != "\n":
            raise ValueError("separatore pagine PDF non coerente")
        position = end + 1
    if references[-1]["fine"] != len(body):
        raise ValueError("riferimenti PDF non coprono il testo originale")


def pdf_document(path, *, archive_root, retrieval, cutoff, published_at=None, metadata=None):
    """Read an archived PDF with a pinned acquisition receipt; no I/O network.

    No date or legal-entity claim is inferred from the filename or PDF metadata.
    No economic fact is fabricated from a page: the resulting text still needs
    exact citations and the preparer's ordinary numerical/semantic checks.
    """
    from bellomberg.market_data.lettore_trimestrali import estrai_testo

    source = Path(path).resolve()
    if not source.is_relative_to(Path(archive_root).resolve()):
        raise ValueError("document outside verified archive")
    raw = source.read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise ValueError("documento non PDF")
    digest = sha256(raw).hexdigest()
    if not isinstance(retrieval, dict) or retrieval.get("document_sha256") != digest:
        raise ValueError("SHA-256 dei byte diverso dalla ricevuta download")
    url = retrieval.get("url")
    if not valid_source_url(url):
        raise ValueError("URL originale HTTP(S) richiesto")
    document = {"id": digest, "url": url, "document_sha256": digest,
                "published_at": published_at, "retrieval": deepcopy(retrieval),
                "availability_basis": "publication" if published_at is not None else "observed_download"}
    _receipt(document)
    document.update(source_dates(document, _day(cutoff)))
    extraction = estrai_testo(str(source), contenuto=raw)
    if extraction.get("stato") != "ok" or extraction.get("formato") != "pdf":
        raise ValueError("PDF non estraibile: " + str(extraction.get("motivo")))
    body = extraction["testo"]
    document.update(text=body, sha256=sha256(body.encode("utf-8")).hexdigest(),
        metadata=deepcopy(metadata), page_references=[{
            "pagina": page["pagina"], "inizio": page["inizio"], "fine": page["fine"],
            "sha256": sha256(page["testo"].encode("utf-8")).hexdigest()}
            for page in extraction["riferimenti"]],
        extraction_coverage={"format": "pdf", "pages": extraction["pagine"],
            "pages_without_text": deepcopy(extraction.get("pagine_senza_testo", [])),
            "characters": len(body), "raw_bytes": len(raw), "raw_sha256_checked": True,
            "warnings": deepcopy(extraction.get("avvisi", [])),
            "limitation": "Text extraction does not certify table columns, units, report dates, legal entities or economic completeness."})
    verify_page_references(body, document["page_references"])
    from .pdf_form_evidence import extract_pdf_form_fields
    fields = extract_pdf_form_fields(raw)
    if fields is not None:
        document["pdf_form_fields"] = fields
        document["extraction_coverage"]["form_text_fields"] = len(fields["fields"])
    return document
