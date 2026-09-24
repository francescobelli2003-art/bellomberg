"""Loss-declared prompt projection for staged valuation preparation.

The source dossier and its document catalog remain untouched. Complete structured
JSON texts keep their original character layout, so evidence JSON pointers retain
their meaning. Bank narrative projection requires an explicit, verified excerpt
manifest. This is a context-size choice, not economic certification.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re


_FORECAST_STAGES = frozenset({"forecast", "bear", "base", "bull"})
_OPENING_SOURCES_REPLACED_BY_DOCUMENTS = frozenset({"financials", "filings"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_THEME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_BLANK_LINES = re.compile(r"\n(?:[ \t\r\u00a0]*\n){2,}")


def project_excerpt_layout(body, policy=None):
    """Opt-in blank-line compression; content lines and source text stay intact."""
    if policy is None:
        return body
    if policy != "collapse_blank_lines_v1":
        raise ValueError("unknown excerpt layout projection")
    return _BLANK_LINES.sub("\n\n", body)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _structured_kind(document, parsed, ticker):
    """Identify supported structured shapes in the verified opening catalog.

    Shape recognition is deliberately conservative. It does not authenticate a
    source; input_preparation's catalog and fact validators retain that role.
    """
    if not isinstance(parsed, dict):
        return None
    facts = parsed.get("facts")
    if not isinstance(facts, list) or not facts or any(not isinstance(f, dict) for f in facts):
        return None
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return None
    ident = document.get("id")
    if (metadata.get('normalizer') == 'statement_tables_v1'
            and ident == 'statement-tables-' + str(metadata.get('source_document_id'))
            and all(f.get('taxonomy') == 'reported-statement' and
                    {'value', 'unit', 'end', 'proof'} <= f.keys() for f in facts)):
        return 'printed_statement'
    cik = str(parsed.get("cik") or "")
    accession = metadata.get("accession")
    if (cik.isdecimal() and isinstance(accession, str) and accession
            and metadata.get("emittente_id") == "CIK:" + cik.zfill(10)
            and ident == "xbrl-" + cik.zfill(10) + "-" + accession
            and all(isinstance(f.get("taxonomy"), str)
                    and isinstance(f.get("concept"), str)
                    and isinstance(f.get("unit"), str)
                    and isinstance(f.get("observation"), dict) for f in facts)):
        return "xbrl"
    observation = parsed.get("observation")
    if (isinstance(observation, dict) and metadata.get("ticker") == ticker
            and parsed.get("symbol") == ticker and observation.get("symbol") == ticker
            and isinstance(metadata.get("price_as_of"), str)
            and observation.get("date") == metadata["price_as_of"]
            and all({"value", "unit", "end"} <= f.keys() for f in facts)):
        return "price"
    listing = parsed.get("listing")
    if (isinstance(listing, dict) and metadata.get("ticker") == ticker
            and isinstance(metadata.get("share_class"), str)
            and bool(metadata["share_class"].strip())
            and listing.get("symbol") == ticker
            and "shares_per_quote" in listing
            and all({"value", "unit", "end"} <= f.keys() for f in facts)):
        return "listing"
    return None


def _project_excerpts(documents, classifications, descriptions, manifest):
    """Apply an exhaustive, byte-pinned narrative selection to a prompt copy.

    The original document SHA remains source identity, deliberately *not* the
    projected text's SHA. Evidence validation must use the original dossier.
    """
    if not isinstance(manifest, list):
        raise ValueError("excerpt manifest must be a list")
    narrative = {}
    all_ids = set()
    full_json = 0
    for index, (document, (is_json, _)) in enumerate(zip(documents, classifications)):
        ident = document.get("id")
        if not isinstance(ident, str) or not ident or ident in all_ids:
            raise ValueError("excerpt manifest requires unique document IDs")
        all_ids.add(ident)
        if is_json:
            full_json += 1
        else:
            narrative[ident] = index
    entries = {}
    for entry in manifest:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_id"), str):
            raise ValueError("excerpt manifest entry requires source_id")
        ident = entry["source_id"]
        if ident in entries:
            raise ValueError("duplicate excerpt manifest source_id: " + ident)
        entries[ident] = entry
    if set(entries) != set(narrative):
        raise ValueError("excerpt manifest must cover exactly all narrative documents")

    original_bytes = selected_bytes = projected_bytes = removed_layout_bytes = 0
    layout_projection_used = False
    themes = set()
    for ident, index in narrative.items():
        document = documents[index]
        entry = entries[ident]
        layout = entry.get("layout_projection")
        project_excerpt_layout("", layout)  # Reject unknown policies before projecting.
        body = document["text"]
        body_bytes = len(body.encode("utf-8"))
        body_digest = descriptions[index]["original_text_sha256"]
        if entry.get("original_text_sha256_utf8") != body_digest:
            raise ValueError("stale excerpt manifest text SHA-256: " + ident)
        if ("original_text_utf8_bytes" in entry
                and entry["original_text_utf8_bytes"] != body_bytes):
            raise ValueError("stale excerpt manifest text length: " + ident)
        if ("original_document_sha256_bytes" in entry
                and entry["original_document_sha256_bytes"] != document.get("document_sha256")):
            raise ValueError("stale excerpt manifest document SHA-256: " + ident)
        for field in ("url", "published_at"):
            if field in entry and entry[field] != document.get(field):
                raise ValueError("stale excerpt manifest " + field + ": " + ident)
        metadata = document.get("metadata")
        for field in ("form", "report_date"):
            if field in entry and entry[field] != (metadata.get(field) if isinstance(metadata, dict) else None):
                raise ValueError("stale excerpt manifest " + field + ": " + ident)
        spans = entry.get("excerpts")
        if not isinstance(spans, list) or not spans:
            raise ValueError("excerpt manifest needs at least one span: " + ident)
        pieces = []
        span_report = []
        previous_end = 0
        doc_selected_bytes = 0
        doc_selected_chars = 0
        doc_layout_chars = doc_layout_bytes = 0
        for number, span in enumerate(spans, 1):
            if not isinstance(span, dict) or "text" in span:
                raise ValueError("excerpt text must be read from original document")
            start, end = span.get("char_start"), span.get("char_end_exclusive")
            theme = span.get("theme")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body)
                    or (number > 1 and start < previous_end)):
                raise ValueError("invalid or overlapping excerpt offsets: " + ident)
            if not isinstance(theme, str) or not _THEME.fullmatch(theme):
                raise ValueError("invalid excerpt theme: " + ident)
            piece = body[start:end]
            digest = sha256(piece.encode("utf-8")).hexdigest()
            if not isinstance(span.get("excerpt_sha256_utf8"), str) or not _SHA256.fullmatch(span["excerpt_sha256_utf8"]) or span["excerpt_sha256_utf8"] != digest:
                raise ValueError("excerpt SHA-256 mismatch: " + ident)
            piece_bytes = len(piece.encode("utf-8"))
            if ("excerpt_chars" in span and span["excerpt_chars"] != len(piece)) or ("excerpt_utf8_bytes" in span and span["excerpt_utf8_bytes"] != piece_bytes):
                raise ValueError("excerpt length mismatch: " + ident)
            first_line = body.count("\n", 0, start) + 1
            last_line = body.count("\n", 0, end - 1) + 1
            if (("line_start" in span) != ("line_end" in span)
                    or ("line_start" in span and (span["line_start"] != first_line or span["line_end"] != last_line))):
                raise ValueError("excerpt line location mismatch: " + ident)
            displayed = project_excerpt_layout(piece, layout)
            doc_layout_chars += len(piece) - len(displayed)
            doc_layout_bytes += piece_bytes - len(displayed.encode("utf-8"))
            pieces.append(f"[SOURCE EXCERPT {number} original_chars={start}:{end} sha256={digest}]\n{displayed}\n[/SOURCE EXCERPT]")
            span_report.append({"theme": theme, "char_start": start, "char_end_exclusive": end,
                                "excerpt_sha256_utf8": digest, "excerpt_chars": len(piece),
                                "excerpt_utf8_bytes": piece_bytes,
                                "line_start": first_line, "line_end": last_line})
            previous_end = end
            doc_selected_bytes += piece_bytes
            doc_selected_chars += len(piece)
            themes.add(theme)
        projected = "\n".join(pieces)
        document["text"] = projected
        projected_size = len(projected.encode("utf-8"))
        descriptions[index].update(view="verified_excerpts", reason="explicit manifest spans copied from original narrative",
                                   excerpts=span_report, selected_source_chars=doc_selected_chars,
                                   excluded_source_chars=len(body) - doc_selected_chars,
                                   selected_source_utf8_bytes=doc_selected_bytes,
                                   excluded_source_utf8_bytes=body_bytes - doc_selected_bytes,
                                   projected_text_sha256=sha256(projected.encode("utf-8")).hexdigest(),
                                   projected_text_utf8_bytes=projected_size)
        original_bytes += body_bytes
        selected_bytes += doc_selected_bytes
        projected_bytes += projected_size
        if layout is not None:
            layout_projection_used = True
            removed_layout_bytes += doc_layout_bytes
            descriptions[index].update(layout_projection=layout, removed_layout_chars=doc_layout_chars,
                                       removed_layout_utf8_bytes=doc_layout_bytes,
                                       layout_limitation="Only repeated blank lines are compressed; quote an unchanged original passage also present in the displayed excerpt.")
            for field in ("selection_rationale", "coverage_limitations"):
                if field in entry:
                    if not isinstance(entry[field], str) or not entry[field].strip():
                        raise ValueError("excerpt " + field + " must be nonempty text")
                    descriptions[index][field] = entry[field]
    summary = {"narrative_documents": len(narrative), "full_json_documents": full_json,
            "original_narrative_utf8_bytes": original_bytes,
            "selected_source_utf8_bytes": selected_bytes,
            "omitted_source_utf8_bytes": original_bytes - selected_bytes,
            "projected_narrative_utf8_bytes": projected_bytes,
            "declared_themes": sorted(themes), "semantic_coverage_certified": False,
            "limitation": "Offsets and hashes verify copied text only; thematic completeness and economic facts are not certified. Validate citations against the original dossier."}
    if layout_projection_used:
        summary["removed_layout_utf8_bytes"] = removed_layout_bytes
    return summary


def select_stage_view(dossier, stage, *, excerpt_manifest=None):
    """Deep-copy a dossier for a prompt, declaring every model-stage exclusion.

    The model view drops narrative document text and the redundant financials/
    filings acquisition envelopes only when one unambiguous price observation,
    one listing and at least one XBRL fact document are present for this ticker.
    Every JSON document is retained whole, including unclassified JSON. Banks
    retain narratives by default; an explicit manifest can project them to
    pinned excerpts without asserting legal-entity or economic coverage.
    FCFF/bank forecasts also allow explicit manifests; without one they receive
    an unchanged deep copy, including every acquired source envelope.
    """
    if stage not in {"model", *_FORECAST_STAGES}:
        raise ValueError("unknown preparation stage: " + str(stage))
    if not isinstance(dossier, dict):
        raise TypeError("dossier must be an object")
    if excerpt_manifest is not None and dossier.get("method_id") not in ("operating_fcff", "bank_residual_income"):
        raise ValueError("excerpt manifest is supported only for FCFF/bank preparation")
    view = deepcopy(dossier)
    if stage != "model" and excerpt_manifest is None:
        return view
    documents = view.get("documents")
    if not isinstance(documents, list):
        raise ValueError("model-stage documents must be a list")
    ticker = view.get("ticker")
    classifications, descriptions, counts = [], [], {"xbrl": 0, "price": 0, "listing": 0}
    for index, document in enumerate(documents):
        if not isinstance(document, dict) or not isinstance(document.get("text"), str):
            raise ValueError("model-stage document text missing at index " + str(index))
        body = document["text"]
        digest = sha256(body.encode("utf-8")).hexdigest()
        declared = document.get("sha256")
        if declared is not None and (not isinstance(declared, str) or declared.lower() != digest):
            raise ValueError("document text SHA-256 mismatch at index " + str(index))
        try:
            parsed = json.loads(body)
            is_json = True
        except ValueError:
            parsed, is_json = None, False
        kind = _structured_kind(document, parsed, ticker) if is_json else None
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
        classifications.append((is_json, kind))
        descriptions.append({"id": document.get("id"), "kind": kind or ("other_json" if is_json else "narrative"),
                             "original_text_sha256": digest, "original_text_chars": len(body),
                             "original_text_bytes": len(body.encode("utf-8"))})
    triplet = counts["xbrl"] >= 1 and counts["price"] == 1 and counts["listing"] == 1
    reducible = stage == "model" and triplet and view.get("method_id") == "operating_fcff"
    balance_origins = {d['metadata'].get('source_document_id') for d in documents
        if isinstance(d.get('metadata'), dict) and d['metadata'].get('normalizer') == 'balance_sheet_v1'}
    excerpt_projection = None
    if excerpt_manifest is not None:
        excerpt_projection = _project_excerpts(documents, classifications, descriptions, excerpt_manifest)
    for document, (is_json, kind), row in zip(documents, classifications, descriptions):
        if 'statement_table_fields' in document:
            packet = _canonical(document.pop('statement_table_fields')).encode('utf-8')
            row['statement_layout_packet'] = {'view': 'excluded', 'sha256': sha256(packet).hexdigest(),
                'original_bytes': len(packet), 'reason': 'Acquisition layout retained in original source catalog; normalized observations remain visible.'}
        if 'balance_detail_fields' in document:
            packet = _canonical(document.pop('balance_detail_fields')).encode('utf-8')
            row['balance_layout_packet'] = {'view': 'excluded', 'sha256': sha256(packet).hexdigest(),
                'original_bytes': len(packet), 'reason': 'Acquisition layout retained in original source catalog; normalized components remain visible.'}
        if 'balance_sheet_fields' in document:
            packet = _canonical(document.pop('balance_sheet_fields')).encode('utf-8')
            row['balance_sheet_layout_packet'] = {'view': 'excluded', 'sha256': sha256(packet).hexdigest(),
                'original_bytes': len(packet), 'reason': 'Acquisition layout retained in original source catalog; normalized balance coverage remains visible.'}
        if row.get("view") == "verified_excerpts":
            continue
        if reducible and not is_json and document.get('id') not in balance_origins:
            del document["text"]
            row.update(view="metadata_only", reason="narrative text excluded from opening prompt; original in source catalog")
        else:
            row.update(view="full_text", reason="structured JSON and original pointers retained" if is_json
                       else "primary balance narrative retained for sourced economic classification" if document.get('id') in balance_origins
                       else "full narrative retained: method coverage or structured opening triplet insufficient for exclusion")
    sources = view.get("acquired_sources")
    source_report = []
    if isinstance(sources, dict):
        for name, source in list(sources.items()):
            row = {"name": name, "view": "full"}
            if reducible and name in _OPENING_SOURCES_REPLACED_BY_DOCUMENTS:
                try:
                    encoded = _canonical(source).encode("utf-8")
                except (TypeError, ValueError, OverflowError):
                    row["reason"] = "non-JSON acquisition retained"
                else:
                    del sources[name]
                    row.update(view="excluded", original_sha256=sha256(encoded).hexdigest(),
                               original_bytes=len(encoded),
                               reason="opening facts available as retained structured documents; original in source dossier")
            source_report.append(row)
    view["stage_view"] = {"stage": stage, "purpose": "prompt_projection_only",
                          "structured_triplet_present": triplet,
                          "structured_documents": counts,
                          "documents": descriptions, "acquired_sources": source_report,
                          "limitation": "Exclusions reduce context only; they do not certify source coverage, facts or valuation. Consolidated XBRL never replaces bank legal-entity narratives."}
    if excerpt_projection is not None:
        view["stage_view"]["excerpt_projection"] = excerpt_projection
    if counts.get('printed_statement'):
        view['stage_view']['printed_statement_evidence'] = (
            'Verified printed statements are a distinct source family, not SEC XBRL tags. '
            'Use /facts/N/value, /facts/N/unit, /facts/N/end; duration facts also have start. '
            'Reported Revenue supports the same annual+current_ytd-prior_ytd TTM operation '
            'when issuer, concept, unit and periods reconcile. Do not mix source families or '
            'treat omitted cells as zero. Selected current operating components do not certify complete NWC.')
    return view
