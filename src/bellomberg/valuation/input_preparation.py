"""Compile a sourced economic plan into unapproved documented method inputs.

The proposer supplies economic judgments, not record metadata. This module does no
network or database I/O, and never records a human approval. ``prepared`` means the
plan was compiled, not that its economics or the resulting fair value are sound.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from math import isclose, isfinite
import json
import re

from .method_records_archive import SOURCE_ID as ARCHIVE_SOURCE
from .method_registry import get_method_requirements
from .sector_analysis import revise_sector_analysis, validate_bundle


SCENARIOS = ("bear", "base", "bull")
KINDS = {"historical", "company_guidance", "analyst_estimate"}
_RECORD_TYPES = {"operating_fcff", "bank_residual_income", "fund_nav"}


def _issue(field, code, reason):
    return {"field": field, "code": code, "reason": reason, "blocking": True}


def _day(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except ValueError:
        return None


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _url(value):
    from .document_evidence import valid_source_url
    return valid_source_url(value)


def _catalog(documents, cutoff):
    catalog, issues, provenance = {}, [], {}
    if not isinstance(documents, list):
        return {}, [_issue("documents", "invalid_catalog", "documents deve essere una lista")], {}
    if cutoff is None:
        return {}, [_issue("documents", "invalid_cutoff", "Cutoff del bundle non ISO")], {}
    for index, raw in enumerate(documents):
        name = f"documents[{index}]"
        if not isinstance(raw, dict):
            issues.append(_issue(name, "invalid_document", "documento non strutturato"))
            continue
        ident = raw.get("id")
        if not _text(ident) or ident in catalog:
            issues.append(_issue(name, "invalid_id", "ID documento assente o duplicato: " + repr(ident)))
            continue
        if not _url(raw.get("url")):
            issues.append(_issue(ident, "invalid_url", "URL fonte non verificabile"))
            continue
        from .document_evidence import source_dates
        try:
            dates = source_dates(raw, cutoff)
        except (ValueError, TypeError, OverflowError) as exc:
            issues.append(_issue(ident, "invalid_publication_date", "Data fonte non verificabile: " + str(exc)))
            continue
        body = raw.get("text")
        if not _text(body):
            issues.append(_issue(ident, "missing_text", "Testo fonte assente: citazioni non verificabili"))
            continue
        actual_hash = sha256(body.encode("utf-8")).hexdigest()
        if raw.get("page_references") is not None:
            from .document_evidence import verify_page_references
            try:
                verify_page_references(body, raw["page_references"])
            except (ValueError, TypeError) as exc:
                issues.append(_issue(ident, "invalid_page_references", str(exc)))
                continue
        supplied_hash = raw.get("sha256")
        if supplied_hash is not None and (not isinstance(supplied_hash, str)
                                         or not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_hash)
                                         or supplied_hash.lower() != actual_hash):
            issues.append(_issue(ident, "hash_mismatch", "SHA-256 dichiarato non corrisponde ai byte del testo"))
            continue
        document_hash = raw.get("document_sha256")
        if document_hash is not None and (not isinstance(document_hash, str)
                                          or not re.fullmatch(r"[0-9a-fA-F]{64}", document_hash)):
            issues.append(_issue(ident, "invalid_document_hash", "SHA-256 del documento grezzo non valido"))
            continue
        if document_hash and re.fullmatch(r"[0-9a-fA-F]{64}", ident) and ident.lower() != document_hash.lower():
            issues.append(_issue(ident, "document_identity_mismatch", "ID documento diverso dall'hash dei byte grezzi"))
            continue
        catalog[ident] = {"id": ident, "url": raw["url"], **dates,
                          "text": body, "sha256": actual_hash,
                          "document_sha256": document_hash.lower() if document_hash else None,
                          "metadata": deepcopy(raw.get("metadata")),
                          "page_references": deepcopy(raw.get("page_references")),
                          "extraction_coverage": deepcopy(raw.get("extraction_coverage", raw.get("coverage")))}
        provenance[ident] = {"url": raw["url"], **deepcopy(dates),
                             "sha256": actual_hash if supplied_hash is not None else None,
                             "sha256_status": "verified" if supplied_hash is not None else "not_provided",
                             "document_sha256": document_hash.lower() if document_hash else None,
                             "document_sha256_status": "declared_not_rechecked" if document_hash else "not_provided",
                             "metadata": deepcopy(raw.get("metadata")),
                             "page_references": deepcopy(raw.get("page_references")),
                             "extraction_coverage": deepcopy(raw.get("extraction_coverage", raw.get("coverage"))),
                             "origin_check": "offline_reference_only"}
        if raw.get("pdf_form_fields") is not None:
            catalog[ident]["pdf_form_fields"] = deepcopy(raw["pdf_form_fields"])
            provenance[ident]["pdf_form_fields"] = deepcopy(raw["pdf_form_fields"])
        if raw.get("inline_parent_fields") is not None:
            catalog[ident]["inline_parent_fields"] = deepcopy(raw["inline_parent_fields"])
            provenance[ident]["inline_parent_fields"] = deepcopy(raw["inline_parent_fields"])
        if raw.get("statement_table_fields") is not None:
            catalog[ident]["statement_table_fields"] = deepcopy(raw["statement_table_fields"])
            provenance[ident]["statement_table_fields"] = deepcopy(raw["statement_table_fields"])
        if raw.get("balance_detail_fields") is not None:
            catalog[ident]["balance_detail_fields"] = deepcopy(raw["balance_detail_fields"])
            provenance[ident]["balance_detail_fields"] = deepcopy(raw["balance_detail_fields"])
    for ident, document in list(catalog.items()):
        metadata = document.get("metadata")
        fdic = (ident.startswith("fdic-facts-") or isinstance(metadata, dict)
                and metadata.get("normalizer") == "fdic_financials_v1")
        inline = (ident.startswith("sec-parent-") or isinstance(metadata, dict)
                  and metadata.get("normalizer") == "sec_parent_inline_v1")
        from .statement_shares_evidence import is_statement_shares
        statement_shares = is_statement_shares(document)
        from .statement_table_evidence import NORMALIZER as TABLE_NORMALIZER, PREFIX as TABLE_PREFIX
        statement_tables = (ident.startswith(TABLE_PREFIX) or isinstance(metadata, dict)
                            and metadata.get('normalizer') == TABLE_NORMALIZER)
        from .fund_nav_statement import NORMALIZER as NAV_NORMALIZER, PREFIX as NAV_PREFIX
        nav_statement = (ident.startswith(NAV_PREFIX) or isinstance(metadata, dict)
                         and metadata.get('normalizer') == NAV_NORMALIZER)
        from .foreign_listing_evidence import NORMALIZER as LISTING_NORMALIZER, PREFIX as LISTING_PREFIX
        foreign_listing = (ident.startswith(LISTING_PREFIX) or isinstance(metadata, dict)
                           and metadata.get('normalizer') == LISTING_NORMALIZER)
        from .fx_evidence import NORMALIZER as FX_NORMALIZER, PREFIX as FX_PREFIX
        fx = ident.startswith(FX_PREFIX) or isinstance(metadata, dict) and metadata.get('normalizer') == FX_NORMALIZER
        from .operating_wc_evidence import NORMALIZER as WC_NORMALIZER, PREFIX as WC_PREFIX
        working_capital = ident.startswith(WC_PREFIX) or isinstance(metadata, dict) and metadata.get('normalizer') == WC_NORMALIZER
        from .balance_detail_evidence import NORMALIZER as DETAIL_NORMALIZER, PREFIX as DETAIL_PREFIX
        balance_details = ident.startswith(DETAIL_PREFIX) or isinstance(metadata, dict) and metadata.get('normalizer') == DETAIL_NORMALIZER
        if not (fdic or inline or statement_shares or statement_tables or nav_statement or foreign_listing or fx or working_capital or balance_details or ident.startswith("regulatory-facts-") or
                isinstance(metadata, dict) and metadata.get("normalizer") == "regulatory_pdf_v1"):
            continue
        try:
            origin_id = metadata.get("source_document_id") if isinstance(metadata, dict) else None
            if not isinstance(origin_id, str) or origin_id == ident or origin_id not in catalog:
                raise ValueError("PDF originale assente dal catalogo")
            if balance_details:
                from .balance_detail_evidence import normalize_balance_details
                normalized = normalize_balance_details(catalog[origin_id])
            elif working_capital:
                from .operating_wc_evidence import normalize_operating_wc
                normalized = normalize_operating_wc(catalog[origin_id], catalog[metadata['primary_document_id']],
                    catalog[metadata['statement_document_id']], on=metadata['report_date'], as_of=metadata['as_of'])
                if date.fromisoformat(metadata['as_of']) > cutoff:
                    raise ValueError('working-capital disclosure after information cutoff')
            elif fx:
                from .fx_evidence import normalize_fx
                expected = normalize_fx(catalog[origin_id], financial_currency=metadata['financial_currency'],
                    quote_currency=metadata['quote_currency'], on=metadata['report_date'], as_of=metadata['as_of'])
                if date.fromisoformat(metadata['as_of']) > cutoff:
                    raise ValueError('FX evidence after information cutoff')
                normalized = {'status': 'ready', 'documents': [expected]}
            elif foreign_listing:
                from .foreign_listing_evidence import normalize_foreign_listing
                normalized = normalize_foreign_listing(catalog[origin_id], catalog[metadata['annual_document_id']],
                    catalog[metadata['quote_document_id']], ticker=metadata['ticker'],
                    on=metadata['report_date'], as_of=metadata['as_of'])
                if date.fromisoformat(metadata['as_of']) > cutoff:
                    raise ValueError('listing identity after information cutoff')
            elif nav_statement:
                from .fund_nav_statement import normalize_fund_statement
                normalized = normalize_fund_statement(catalog[origin_id])
            elif statement_tables:
                from .statement_table_evidence import normalize_statement_tables
                normalized = normalize_statement_tables(catalog[origin_id])
            elif statement_shares:
                from .statement_shares_evidence import normalize_statement_shares
                normalized = normalize_statement_shares(catalog[origin_id], list(catalog.values()))
            elif inline:
                from .parent_inline_evidence import normalize_parent_inline
                normalized = normalize_parent_inline(catalog[origin_id], expected_report_date=metadata.get("report_date"))
            elif fdic:
                from .fdic_evidence import normalize_fdic_financials
                normalized = normalize_fdic_financials(catalog[origin_id], expected_cert=metadata.get("cert"),
                    expected_rssd=metadata.get("rssd"), expected_parent_rssd=metadata.get("parent_rssd"),
                    expected_entity=metadata.get("entity"), expected_report_date=metadata.get("report_date"))
            else:
                from .regulatory_evidence import normalize_regulatory_pdf
                normalized = normalize_regulatory_pdf(catalog[origin_id], expected_form=metadata.get("form"),
                    expected_entity=metadata.get("entity"), expected_report_date=metadata.get("report_date"))
            if normalized.get("status") != "ready" or len(normalized.get("documents", [])) != 1:
                raise ValueError("PDF originale non ricompilabile: " + repr(normalized.get("issues")))
            expected = normalized["documents"][0]
            for key in ("id", "url", "text", "sha256", "document_sha256", "metadata"):
                if document.get(key) != expected.get(key):
                    raise ValueError("normalizzazione diversa dal PDF originale: " + key)
            if source_dates(document, cutoff) != source_dates(expected, cutoff):
                raise ValueError("date della normalizzazione diverse dal PDF originale")
        except (ValueError, TypeError, KeyError) as exc:
            issues.append(_issue(ident, "unverified_regulatory_normalization", str(exc)))
            del catalog[ident]
            provenance.pop(ident, None)
    if not catalog:
        issues.append(_issue("documents", "missing_sources", "Nessuna fonte documentale verificabile nel catalogo"))
    return catalog, issues, provenance


def _calendar(plan, cutoff, *, method=None):
    issues = []
    model = plan.get("model") if isinstance(plan.get("model"), dict) else {}
    perimeter = (model.get("perimeter") or {}).get("value") if isinstance(model.get("perimeter"), dict) else None
    calendar = (model.get("calendar") or {}).get("value") if isinstance(model.get("calendar"), dict) else None
    if not isinstance(perimeter, dict) or set(perimeter) != {"entity", "currency", "share_class"} \
            or not _text(perimeter.get("entity")) or not _text(perimeter.get("share_class")) \
            or not isinstance(perimeter.get("currency"), str) \
            or not re.fullmatch(r"[A-Z]{3}", perimeter["currency"]):
        issues.append(_issue("perimeter", "invalid_perimeter", "Perimetro, valuta ISO e classe azionaria espliciti richiesti"))
    if not isinstance(calendar, dict) or set(calendar) != {"valuation_date", "periods", "discount_convention"}:
        issues.append(_issue("calendar", "invalid_calendar", "Calendario con valuation_date, periods e discount_convention richiesto"))
        return perimeter or {}, {}, None, issues
    opening = _day(calendar.get("valuation_date"))
    if opening is None or opening > cutoff:
        issues.append(_issue("calendar", "invalid_valuation_date", "Data valore non ISO o futura rispetto al cutoff"))
    periods = calendar.get("periods")
    if method == 'fund_nav':
        if periods != [] or calendar.get('discount_convention') != 'snapshot':
            issues.append(_issue('calendar', 'snapshot_required', 'NAV: fotografia datata con periods=[] e discount_convention=snapshot richiesta'))
        return perimeter or {}, calendar, calendar.get('valuation_date'), issues
    if not isinstance(periods, list) or len(periods) != 10:
        issues.append(_issue("calendar", "horizon", "Nuovo candidato FCFF/banca: 10 esercizi annui espliciti richiesti"))
        return perimeter or {}, calendar, None, issues
    previous = opening
    for index, period in enumerate(periods):
        start = _day(period.get("start")) if isinstance(period, dict) and set(period) == {"start", "end"} else None
        end = _day(period.get("end")) if isinstance(period, dict) else None
        if start is None or end is None or previous is None or start != previous + timedelta(days=1) or end < start:
            issues.append(_issue("calendar", "periods", f"Periodo {index}: date non valide o non contigue"))
            break
        if not 364 <= (end - start).days + 1 <= 371:
            issues.append(_issue("calendar", "annual_period_required", f"Periodo {index}: esercizio annuale richiesto (anche calendario 52/53 settimane)"))
        previous = end
    if calendar.get("discount_convention") not in ("annual_end", "ACT/365F"):
        issues.append(_issue("calendar", "discount_convention", "Convenzione di sconto esplicita non supportata"))
    span = "|".join(p["start"] + "/" + p["end"] for p in periods) if not issues else None
    return perimeter or {}, calendar, span, issues


def _bank_schema(plan, perimeter):
    from .bank_adapter import SCHEMA as BASE
    from .distributable_equity import CAPITAL_FIELDS, SUB_FIELDS, SUB_PATHS, CASH_SIGNS
    from .managed_care_adapter import _descriptor
    from .input_evidence import entity_name_key, same_entity_name

    schema = deepcopy(BASE)
    model = plan.get("model") if isinstance(plan.get("model"), dict) else {}
    legal = (model.get("legal_structure") or {}).get("value") if isinstance(model.get("legal_structure"), dict) else None
    if (not isinstance(legal, dict)
            or set(legal) != {"parent_entity", "subsidiaries", "capital_basis", "accounting_basis"}
            or legal.get("capital_basis") != "common_equity" or legal.get("accounting_basis") != "GAAP"
            or not _text(legal.get("parent_entity")) or not isinstance(legal.get("subsidiaries"), list)
            or not legal["subsidiaries"]):
        return schema, {}, [_issue("legal_structure", "bank_legal_structure",
                                   "legal_structure GAAP/common_equity ed entita legali esplicite richiesti; capitale non compilabile")]
    subsidiaries = legal["subsidiaries"]
    if any(not isinstance(sub, dict) or set(sub) != {"id", "regime"}
           or not _text(sub.get("id")) or not _text(sub.get("regime")) for sub in subsidiaries):
        return schema, {}, [_issue("legal_structure", "bank_entities", "Subsidiary ID/regime non completi; capitale non compilabile")]
    ids = [sub["id"] for sub in subsidiaries]
    # A legal parent can report both consolidated and parent-only statements.
    # The driver and its source proof distinguish those accounting scopes.
    group = perimeter.get("entity")
    if (not _text(group) or len({entity_name_key(identity) for identity in ids}) != len(ids)
            or any(same_entity_name(identity, legal["parent_entity"]) or same_entity_name(identity, group)
                   for identity in ids)):
        return schema, {}, [_issue("legal_structure", "bank_entities",
                                   "Subsidiaries distinte tra loro e da parent/consolidato; emittente esplicito richiesto")]
    names = ["capital." + key for key in sorted(CAPITAL_FIELDS - {"subsidiaries", "parent_cash_flows"})]
    names += ["capital.parent_cash_flows." + key for key in sorted(CASH_SIGNS)]
    names += [f"capital.subsidiaries.{index}." + key
              for index in range(len(ids)) for key in sorted(SUB_FIELDS - {"id"})]
    entities = {"opening_parent_equity": legal["parent_entity"]}
    for driver in names:
        field, unit, basis, timing = _descriptor(driver)
        if driver == "capital.terminal_equity":
            field, basis = "terminal_assumptions", "valuation"
        key = driver.split(".")[-1]
        shape = "text" if unit == "text" else "path" if (key in SUB_PATHS or ".parent_cash_flows." in driver
            or key in ("parent_cash_minimum", "parent_gaap_net_income", "consolidation_adjustments", "discount_periods")) else "number"
        schema[driver] = (field, unit, basis, timing, shape, "scenario")
        if ".subsidiaries." in driver:
            entities[driver] = ids[int(driver.split(".")[2])]
        elif field == "parent_ledger":
            entities[driver] = legal["parent_entity"]
    return schema, entities, []


def _source_scale(quoted, target):
    if quoted == target:
        return 1.
    quantities = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
    def split(unit):
        if re.fullmatch(r"[A-Z]{3}", unit):
            return unit, 1.
        if unit == "shares":
            return "shares", 1.
        match = re.fullmatch(r"([A-Z]{3}) (thousand|million|billion)", unit)
        if match:
            return match[1], quantities[match[2]]
        match = re.fullmatch(r"(thousand|million|billion) shares", unit)
        return ("shares", quantities[match[1]]) if match else None
    a, b = split(quoted), split(target)
    return a[1] / b[1] if a and b and a[0] == b[0] else None


def _fact_proof(driver, item, evidence, unit, period=None, *, expected_entity=None, bank_context=None,
                pointer_repairs=None):
    """A cited ID alone cannot prove a historical or management number."""
    from .statement_shares_evidence import is_statement_shares, statement_share_proof
    if any(is_statement_shares(doc) for doc in evidence):
        return statement_share_proof(driver, item, evidence, unit, period, expected_entity, _source_scale)
    from .bank_evidence import is_consolidation, consolidation_proof
    if is_consolidation(item):
        if driver != 'opening_consolidation_adjustments':
            return 'consolidation_equity consentita solo per la riconciliazione iniziale bancaria'
        if any(key in item for key in ('evidence_pointer', 'evidence_quote', 'quoted_value',
                                      'quoted_unit', 'period_quote', 'facts')):
            return 'riconciliazione aggregata e altre forme di prova non possono essere mescolate'
        return consolidation_proof(item, evidence, unit, period, group_entity=expected_entity,
            context=bank_context, prove=_fact_proof, scale=_source_scale)
    from .input_evidence import parent_regulatory_components
    regulatory_components = parent_regulatory_components(driver)
    if regulatory_components is not None:
        regulatory_components = {taxonomy: parent_regulatory_components(driver, taxonomy)
                                 for taxonomy in ("fr-y-9lp-pc", "fr-y-9sp-sc", "sec-parent-us-gaap")}
    if regulatory_components is not None and not ("evidence_pointer" in item or "calculation" in item):
        return "saldo parent: serve un ponte regolamentare normalizzato, non una citazione letterale"
    if "evidence_pointer" in item or "calculation" in item:
        if "period_quote" in item:
            return "period_quote non applicabile a una prova JSON strutturata"
        from .input_evidence import structured_fact_proof, operating_working_capital_components
        if driver == 'capital.parent_opening_debt' and any(
                isinstance(doc.get('metadata'), dict) and doc['metadata'].get('normalizer') == 'regulatory_pdf_v1'
                for doc in evidence):
            from .parent_debt_evidence import parent_debt_policy, validate_parent_debt_source
            problem = validate_parent_debt_source(item, evidence, period, expected_entity)
            if problem:
                return problem
            policy = parent_debt_policy()
            regulatory_components = {policy['taxonomy']: policy['principal_components']}
        if any(str(doc.get("id", "")).startswith("fdic-facts-")
               or isinstance(doc.get("metadata"), dict)
               and doc["metadata"].get("normalizer") == "fdic_financials_v1" for doc in evidence):
            from .fdic_evidence import bank_components
            components = bank_components(driver)
            if components is not None:
                regulatory_components = {"fdic-ris": components}
        allowed_concepts = None
        concept_signs = None
        if driver == "opening_nwc":
            if "calculation" not in item:
                return "NWC operativo: serve un ponte di componenti operativi, non una voce patrimoniale generica"
            concept_signs = operating_working_capital_components()
        if driver == "historical_revenue":
            from .input_evidence import revenue_concepts
            allowed_concepts = revenue_concepts()
        if driver == 'liquidity_opening_cash':
            allowed_concepts = {('fdic-ris', 'CHBAL'), ('us-gaap', 'CashAndCashEquivalentsAtCarryingValue'),
                                ('ifrs-full', 'CashAndCashEquivalents')}
        return structured_fact_proof(item, evidence, unit, period, scale=_source_scale,
                                     require_annual=driver == "historical_revenue",
                                     allowed_concepts=allowed_concepts, concept_signs=concept_signs,
                                     expected_entity=expected_entity,
                                     regulatory_components=regulatory_components,
                                     parent_cash_pointer_repairs=(pointer_repairs if driver == 'capital.parent_opening_cash' else None))
    if len(evidence) != 1:
        return "fatto storico/guidance: una fonte primaria univoca richiesta"
    quote, quoted_value, quoted_unit = (item.get(key) for key in ("evidence_quote", "quoted_value", "quoted_unit"))
    if not _text(quote) or quote not in evidence[0]["text"]:
        return "citazione letterale assente dal documento"
    if driver == 'liquidity_opening_cash' and (not expected_entity
            or expected_entity.casefold() not in quote.casefold() or not re.search(r'\bcash\b', quote, re.I)):
        return 'opening cash quote must identify the legal bank and cash measure'
    if driver == "opening_nwc" and not re.search(r"\b(?:net|operating) working capital\b", quote, re.I):
        return "la citazione non identifica il capitale circolante netto/operativo"
    quotation_facts = ("price", "shares_per_quote", "financial_to_quote_rate", "quote_units_per_currency")
    if period is not None and driver not in quotation_facts:
        from .literal_fact_evidence import literal_period_matches
        span = item.get("period_quote")
        if not _day(period) or not literal_period_matches(span, quote, period, evidence[0]['text']):
            return "periodo e misura non uniti in un estratto letterale contiguo e univoco"
    elif "period_quote" in item:
        return "period_quote non applicabile a un fatto senza periodo opening"
    if not _finite(quoted_value) or not _text(quoted_unit):
        return "cifra e unita citate obbligatorie"
    unit_parts = quoted_unit.split()
    if any(not re.search(r"\b" + re.escape(part) + r"\b", quote) for part in unit_parts):
        return "unita citata non presente nell'estratto"
    from .literal_fact_evidence import literal_number
    seen = literal_number(quote)
    if seen is None or not isclose(seen, quoted_value, rel_tol=0, abs_tol=1e-12):
        return "estratto numerico ambiguo o cifra citata non presente: isolare una sola misura"
    scale = _source_scale(quoted_unit, unit)
    if scale is None:
        return "scala/valuta della fonte non riconciliata all'unita del driver"
    if not _finite(item.get("value")) or not isclose(item["value"], quoted_value * scale, rel_tol=1e-10, abs_tol=1e-8):
        return "valore del driver diverso dalla cifra citata dopo la conversione di scala"
    return None


def _quotation_proof(item, evidence):
    value, facts = item.get("value"), item.get("facts")
    if not isinstance(value, dict) or not isinstance(facts, dict):
        return "quotazione: contratto e facts documentati richiesti"
    units = {"price": str(value.get("quote_unit")) + " per share",
             "shares_per_quote": "shares per quote",
             "financial_to_quote_rate": str(value.get("quote_currency")) + " per " + str(value.get("financial_currency")),
             "quote_units_per_currency": str(value.get("quote_unit")) + " per " + str(value.get("quote_currency"))}
    identities = {"financial_to_quote_rate": value.get("financial_currency") == value.get("quote_currency"),
                  "quote_units_per_currency": value.get("quote_unit") == value.get("quote_currency")}
    required = set(units) - {key for key, same in identities.items() if same and value.get(key) == 1.}
    if set(facts) != required:
        return "prove numeriche richieste per " + ", ".join(sorted(required))
    catalog = {doc["id"]: doc for doc in evidence}
    for key in sorted(required):
        fact = facts[key]
        if not isinstance(fact, dict) or set(fact) - {"evidence_ids", "evidence_quote", "quoted_value", "quoted_unit", "date_quote", "evidence_pointer"}:
            return key + ": prova non strutturata o campi sconosciuti"
        ids = fact.get("evidence_ids")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in catalog:
            return key + ": fonte primaria univoca richiesta fra quelle del driver"
        sources = [catalog[ids[0]]]
        # Listing identity is a disclosed deterministic unit conversion. A ratio
        # for another share class cannot attest this quotation's conversion.
        origin = (sources[0].get("metadata") or {})
        if key == "shares_per_quote" and origin.get("basis") == "one_listed_ordinary_share_is_one_share_of_the_same_class":
            if value.get("share_class") != origin.get("share_class"):
                return "classe quotata diversa dall'identita primaria verificata"
        problem = _fact_proof(key, {**fact, "value": value.get(key)}, sources, units[key], value.get("price_as_of"))
        if problem:
            return key + ": " + problem
        if key == "price" and "evidence_pointer" not in fact:
            # A date elsewhere in the same document cannot date this price.
            # Require one literal, contiguous excerpt containing both claims.
            day, span, quote = value.get("price_as_of"), fact.get("date_quote"), fact.get("evidence_quote")
            if (not _day(day) or not _text(span) or not _text(quote)
                    or span.count(day) != 1 or span.count(quote) != 1
                    or re.findall(r"\b\d{4}-\d{2}-\d{2}\b", span) != [day]
                    or sources[0]["text"].count(span) != 1):
                return "data e prezzo non uniti in un estratto letterale contiguo e univoco"
    return None


def _compile(plan, schema, entities, perimeter, calendar, span, catalog, cutoff, *, pointer_repairs=None, method=None):
    from .record_semantics import is_opening_equity_bridge
    from .fund_nav_preparation import JUDGMENTS, prove_nav
    nav = method == 'fund_nav'
    records, issues, expiry_policies = [], [], {}
    model = plan.get("model")
    scenarios = plan.get("scenarios")
    if not isinstance(model, dict) or not isinstance(scenarios, dict) or set(scenarios) != set(SCENARIOS) \
            or any(not isinstance(scenarios[s], dict) for s in SCENARIOS):
        return [], [_issue("proposal", "invalid_plan", "Servono model e scenari bear/base/bull come mappe di driver")], {}
    if any(not isinstance(driver, str) for driver in model) or any(
            not isinstance(driver, str) for scope in SCENARIOS for driver in scenarios[scope]):
        return [], [_issue("proposal", "invalid_driver_name", "I nomi dei driver devono essere stringhe")], {}
    expected_model = {key for key, value in schema.items() if value[-1] == "model"}
    expected_scenarios = {key for key, value in schema.items() if value[-1] == "scenario"}
    for scope, values, expected in [("model", model, expected_model)] + [
            (scenario, scenarios[scenario], expected_scenarios) for scenario in SCENARIOS]:
        for driver in sorted(set(values) - expected):
            issues.append(_issue(driver, "unconsumed_driver", f"{scope}.{driver}: driver non consumato dal metodo"))
        for driver in sorted(expected - set(values)):
            issues.append(_issue(driver, "missing_driver", f"{scope}.{driver}: input documentato mancante"))
        for driver in sorted(set(values) & expected):
            driver_repairs = []
            item = values[driver]
            label = scope + "." + driver
            if not isinstance(item, dict):
                issues.append(_issue(driver, "invalid_driver", label + ": driver non strutturato"))
                continue
            allowed = {"value", "kind", "evidence_ids", "rationale", "valid_until", "valid_until_basis",
                       "evidence_quote", "quoted_value", "quoted_unit", "period_quote", "facts", "evidence_pointer", "calculation"}
            if set(item) - allowed:
                issues.append(_issue(driver, "unused_proposal_field", label + ": campi non consumati " + repr(sorted(set(item)-allowed))))
                continue
            field, unit, basis, timing, shape, _ = schema[driver]
            opening_bridge = is_opening_equity_bridge(driver, schema[driver])
            if unit in ("money", "price", "money_per_policy"):
                unit = perimeter["currency"] + {"money": " million", "price": " per share",
                                                 "money_per_policy": " per policy"}[unit]
            kind = item.get("kind")
            ids = item.get("evidence_ids")
            if not isinstance(kind, str) or kind not in KINDS or not _text(item.get("rationale")):
                issues.append(_issue(driver, "invalid_judgment", label + ": kind o rationale non valido"))
                continue
            # The legacy capital descriptor dates Ke at opening, but it is a
            # prospective valuation judgment, not an observed balance/quotation.
            nav_judgment = nav and driver in JUDGMENTS
            if nav_judgment and kind != 'analyst_estimate':
                issues.append(_issue(driver, 'nav_judgment_required', label + ': scelta analitica NAV, non un fatto storico o guidance'))
                continue
            if timing == "opening" and driver != "capital.ke" and not nav_judgment and kind != "historical":
                issues.append(_issue(driver, "opening_fact_required", label + ": saldo/quotazione iniziale richiede un fatto storico verificabile"))
                continue
            if timing in ("future", "terminal") and kind == "historical" and not opening_bridge:
                issues.append(_issue(driver, "historical_forecast", label + ": storico non utilizzabile come forecast"))
                continue
            if (not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids)
                    or len(ids) != len(set(ids)) or any(x not in catalog for x in ids)):
                issues.append(_issue(driver, "invalid_citation", label + ": evidence_ids assenti/duplicati/inventati: " + repr(ids)))
                continue
            evidence = [catalog[x] for x in ids]
            observed = max(_day(doc["available_at"]) for doc in evidence)
            expiry = _day(item.get("valid_until"))
            if expiry is None or expiry < cutoff or expiry < observed or item.get("valid_until_basis") is None:
                issues.append(_issue(driver, "invalid_expiry", label + ": valid_until corrente e policy esplicita richiesti"))
                continue
            expiry_basis = item["valid_until_basis"]
            same_day = (isinstance(expiry_basis, dict)
                        and expiry_basis == {"policy": "same_day", "as_of": cutoff.isoformat()}
                        and expiry == cutoff)
            source_expiry = (_text(expiry_basis) and item["valid_until"] in expiry_basis
                             and any(expiry_basis in source["text"] for source in evidence))
            if not (same_day or source_expiry):
                issues.append(_issue(driver, "unverified_expiry", label + ": solo policy same_day o scadenza letterale nella fonte citata"))
                continue
            if kind in ("historical", "company_guidance"):
                if driver in ('shares', 'capital.shares_m'):
                    from .statement_shares_evidence import share_selection_problem
                    selection_error = share_selection_problem(item, catalog, entities.get(driver, perimeter['entity']),
                        calendar['valuation_date'], perimeter['share_class'])
                    if selection_error:
                        issues.append(_issue(driver, 'source_disagreement', label + ': ' + selection_error))
                        continue
                if kind == "company_guidance" and any(
                        key in item for key in ("evidence_pointer", "calculation")):
                    issues.append(_issue(driver, "unverified_fact", label
                        + ": osservazioni JSON storiche non certificano guidance futura"))
                    continue
                proof_error = (_quotation_proof(item, evidence) if driver == "quotation"
                               else prove_nav(driver, item, evidence, unit, calendar['valuation_date'], perimeter, model, _fact_proof) if nav
                               else _fact_proof(driver, item, evidence, unit,
                                                calendar["valuation_date"] if timing == "opening" or opening_bridge else None,
                                                expected_entity=entities.get(driver, perimeter["entity"]),
                                                bank_context=plan, pointer_repairs=driver_repairs))
                if proof_error:
                    issues.append(_issue(driver, "unverified_fact", label + ": " + proof_error))
                    continue
            elif any(key in item for key in ("evidence_quote", "quoted_value", "quoted_unit", "period_quote", "evidence_pointer", "calculation")) or (
                    'facts' in item and driver != 'liquidity_bridge'):
                issues.append(_issue(driver, "mixed_evidence", label + ": prove di fatto in una stima: separare osservazioni e giudizio"))
                continue
            if "facts" in item and driver not in ('quotation', 'liquidity_bridge') and not (nav and driver == 'components'):
                issues.append(_issue(driver, "unused_facts", label + ": facts consentiti solo per quotation/liquidity_bridge"))
                continue
            if driver == 'liquidity_bridge':
                from .bank_liquidity_evidence import prove_opening_cash
                cash_error = prove_opening_cash(item, evidence, calendar['valuation_date'], perimeter['currency'],
                    (model.get('legal_structure') or {}).get('value'), prove=_fact_proof)
                if cash_error:
                    issues.append(_issue(driver, 'unverified_opening_cash', label + ': ' + cash_error))
                    continue
            value = item.get("value")
            if value is None or value == "" or value == [] or value == {}:
                issues.append(_issue(driver, "missing_value", label + ": valore assente"))
                continue
            if shape == "number" and not _finite(value):
                issues.append(_issue(driver, "invalid_number", label + ": numero finito richiesto"))
                continue
            if shape == "path" and (not isinstance(value, list) or len(value) != len(calendar["periods"])
                                    or any(not _finite(n) for n in value)):
                issues.append(_issue(driver, "invalid_path", label + ": percorso numerico per ogni esercizio richiesto"))
                continue
            if shape == "contract" and not isinstance(value, dict):
                issues.append(_issue(driver, "invalid_contract", label + ": contratto strutturato richiesto"))
                continue
            if shape == "text" and not _text(value):
                issues.append(_issue(driver, "invalid_text", label + ": testo richiesto"))
                continue
            if driver == 'capital.distribution_policy' and value != 'full_sweep_after_buffers':
                issues.append(_issue(driver, 'unsupported_policy', label + ': serve full_sweep_after_buffers; nessun dividendo fisso implicito'))
                continue
            period = calendar["valuation_date"] if timing == "opening" else calendar["periods"][-1]["end"] if timing == "terminal" else span
            from .bank_evidence import is_consolidation, CONSOLIDATION_DISCLOSURE
            rationale = (CONSOLIDATION_DISCLOSURE if is_consolidation(item) else '') + item["rationale"]
            if driver_repairs:
                rationale = 'SOURCE POINTER NORMALIZED: ' + json.dumps(driver_repairs, sort_keys=True) + '\n' + rationale
                if pointer_repairs is not None:
                    pointer_repairs.extend(dict(row, driver=driver, scenario=scope) for row in driver_repairs)
            if driver in ('shares', 'capital.shares_m'):
                from .statement_shares_evidence import share_conflict_disclosure
                rationale = share_conflict_disclosure(evidence) + rationale
            records.append({"field": field, "driver": driver, "scenario": scope, "value": deepcopy(value),
                            "entity": entities.get(driver, perimeter["entity"]), "period": period,
                            "unit": unit, "accounting_basis": basis,
                            "source_id": evidence[0]["url"], "source_locator": ",".join(ids),
                            "as_of": observed.isoformat(), "valid_until": expiry.isoformat(),
                            "kind": kind, "rationale": rationale})
            expiry_policies[label] = (deepcopy(expiry_basis) if same_day else
                                      {"policy": "source_literal", "quote": expiry_basis,
                                       "evidence_ids": deepcopy(ids)})
    # Check this model-wide policy as soon as its dependent paths arrive, including
    # partial paid stages. Missing paths remain missing; they never imply zero.
    for row in records:
        if row['driver'] != 'capdev_amortization_years':
            continue
        life = row['value']
        if type(life) is not int or life < 0:
            issues.append(_issue(row['driver'], 'invalid_research_policy',
                'capdev_amortization_years: intero positivo oppure 0 = non applicabile richiesti'))
        elif life == 0 and any(value != 0 for item in records
                if item['driver'] in ('capdev_pct', 'opening_intangible_amortization') for value in item['value']):
            issues.append(_issue(row['driver'], 'invalid_research_policy',
                'capdev_amortization_years: non applicabile incompatibile con capitalizzazione o ammortamento iniziale'))
    return records, issues, expiry_policies


def has_approved_inputs(bundle):
    source = bundle["case"].get("sources", {}).get("method_inputs", {})
    return bool(source.get("source_id") == ARCHIVE_SOURCE and source.get("status") == "ok" and source.get("records"))


def prepare_method_inputs(bundle, *, documents, propose, source_report=None):
    """Return a candidate revision of an already acquired sector bundle.

    ``propose(dossier, contract)`` returns ``model`` and ``scenarios`` driver maps,
    plus ``scenario_rationale``. Each driver declares value, kind, evidence_ids,
    rationale, valid_until and valid_until_basis. The pilot accepts
    ``{"policy": "same_day", "as_of": cutoff}`` with expiry equal to cutoff, or
    an actual dated expiry quote from a cited document. Historical/guidance scalar facts
    additionally declare evidence_quote, quoted_value and quoted_unit.
    """
    original = validate_bundle(bundle, bundle["case"]["ticker"])
    decision, case = original["decision"], original["case"]
    method = decision.get("method_id")
    provenance = {"origin": "automatic_non_approved", "documents": {}, "expiry_policies": {}}
    if source_report is not None:
        provenance["source_acquisition"] = deepcopy({k: v for k, v in source_report.items() if k != "documents"})
    if has_approved_inputs(original):
        return {"bundle": original, "status": "approved_reused", "issues": [], "proposal": None,
                "provenance": {"origin": "archive_approved", "documents": {}}}
    if decision.get("decision_status") != "resolved" or method not in _RECORD_TYPES \
            or decision.get("support_status") != "integrated":
        reason = "Metodo non risolto o fuori dal perimetro FCFF/banca/fund NAV del preparatore: " + str(method)
        return {"bundle": original, "status": "unsupported", "issues": [_issue("method", "unsupported", reason)],
                "proposal": None, "provenance": provenance}
    if case.get("assumptions"):
        reason = "Parametri legacy presenti: " + ", ".join(sorted(case["assumptions"]))
        return {"bundle": original, "status": "incomplete", "issues": [_issue("assumptions", "legacy", reason)],
                "proposal": None, "provenance": provenance}
    cutoff = _day(case.get("as_of"))
    if method == 'fund_nav' and isinstance(documents, list):
        from .fund_nav_statement import statement_documents
        documents, statement_report = statement_documents(documents)
        provenance['fund_nav_statement_observations'] = statement_report
    catalog, issues, doc_provenance = _catalog(documents, cutoff)
    provenance["documents"] = doc_provenance
    if issues:
        return {"bundle": original, "status": "incomplete", "issues": issues,
                "proposal": None, "provenance": provenance}
    from .operating_adapter import SCHEMA as OPERATING, REVENUE_BASES, REVENUE_ALTERNATIVE_BASES
    from .bank_adapter import SCHEMA as BANK
    from .nav_adapter import FUND
    requirements = get_method_requirements(method)
    contract = {"method_id": method, "method_version": requirements["method_version"],
                "schema": deepcopy({'operating_fcff': OPERATING, 'bank_residual_income': BANK, 'fund_nav': FUND}[method]),
                "requirements": deepcopy(requirements),
                "scenarios": list(SCENARIOS), "standard_horizon_years": 0 if method == 'fund_nav' else 10,
                "expiry_policy": {"policy": "same_day", "as_of": case["as_of"]},
                "bank_dynamic_capital": method == "bank_residual_income"}
    from bellomberg.core.paths import PROJECT_ROOT
    # Shipped, public method contract: reuse it rather than maintaining a second
    # economic definition in the prompt. Missing installed docs fail explicitly.
    guide = PROJECT_ROOT / "docs" / "guide" / "sector-valuations.md"
    if not guide.is_file():
        return {"bundle": original, "status": "incomplete",
                "issues": [_issue("contract", "missing_contract", "Contratto del metodo non installato: " + str(guide))],
                "proposal": None, "provenance": provenance}
    headings = {"operating_fcff": ("## Operating FCFF", "## Bank and balance-sheet"),
                "bank_residual_income": ("## Bank and balance-sheet", "## Regulated networks"),
                "fund_nav": ("## Funds, investment holdings and digital-asset NAV", "## Property/casualty insurance")}
    guide_text = guide.read_text(encoding="utf-8")
    start, end = headings[method]
    contract["common_record_contract"] = guide_text[guide_text.index("## Common record contract"):guide_text.index("## Operating FCFF")]
    contract["method_contract"] = guide_text[guide_text.index(start):guide_text.index(end)]
    contract["driver_envelope"] = {
        "value": "Exact driver value per the shared/method contract; never insert missing facts",
        "kind": "historical | company_guidance | analyst_estimate",
        "kind_policy": "perimeter and calendar are analyst_estimate modeling choices (scope, horizon, discount convention), supported by cited sources. Drivers timed opening require historical facts, except capital.ke which is an analyst estimate. Drivers timed future/terminal must not be historical, except FCFF net_debt/equity_adjustments: their legacy future period denotes scenario scope, while their economic balance date is calendar.valuation_date. These opening claims may carry historical proofs at that exact date. Analyst estimates cite evidence_ids and rationale without numerical fact-proof fields; preserve observed facts separately in their historical drivers.",
        "evidence_ids": "IDs of visible supporting documents",
        "rationale": "Company-specific evidence and reasoning",
        "valid_until": case["as_of"],
        "valid_until_basis": deepcopy(contract["expiry_policy"]),
        "quotation_fact_fields": ["evidence_ids", "evidence_quote", "quoted_value", "quoted_unit", "date_quote", "evidence_pointer"],
        "structured_fact_policy": "Use evidence_pointer with quoted_value/quoted_unit; period_quote is not applicable to structured JSON proof. Do not add other keys to quotation facts."}
    if method == "operating_fcff":
        from .input_evidence import operating_working_capital_components
        profile = decision.get("profile_id")
        contract["revenue_build_basis"] = REVENUE_BASES.get(profile)
        contract["revenue_build_alternatives"] = list(REVENUE_ALTERNATIVE_BASES.get(profile, ()))
        contract["historical_revenue_policy"] = {
            "basis": "Full-year observed revenue ending at calendar.valuation_date",
            "interim_operation": "trailing_twelve_months",
            "terms": ["annual", "current_ytd", "prior_ytd"],
            "formula": "annual + current_ytd - prior_ytd",
            "requirements": "SEC XBRL accession-bound observations; same issuer, concept and currency; contiguous annual/current YTD and comparable prior YTD; exact operand citations",
            "limitation": "No quarter multiplication, implicit annualization or forecast opening revenue"}
        if any((doc.get('metadata') or {}).get('normalizer') == 'statement_tables_v1' for doc in catalog.values()):
            contract['historical_revenue_policy'].update(
                requirements='SEC accession-bound XBRL or verified consolidated printed revenue; all three operands must use the same issuer, taxonomy/concept and currency, with reconciled annual/current YTD/prior YTD periods and exact citations. Do not mix XBRL and printed concepts within one TTM calculation.',
                printed_statement_format={
                    'normalizer': 'statement_tables_v1', 'taxonomy': 'reported-statement', 'concept': 'Revenue',
                    'evidence_pointer': {'value': '/facts/{index}/value', 'unit': '/facts/{index}/unit',
                                         'period': '/facts/{index}/end'},
                    'start_pointer': '/facts/{index}/start',
                    'operand_keys': ['evidence_ids', 'evidence_pointer', 'quoted_value', 'quoted_unit'],
                    'instructions': 'For printed statements use these flat value/end pointers, not XBRL observation/val or observation/end. Each operand cites exactly one verified normalized document and its actual quoted value/unit. The compiler reads start from the same fact; do not add start to evidence_pointer. Select cumulative YTD, not the standalone quarter. Keep statement scale explicit; the result may use deterministic same-currency scaling to the driver unit.'})
        contract["opening_nwc_structured_policy"] = {
            "basis": "signed operating components; total assets, cash and financing excluded",
            "components": [{"taxonomy": taxonomy, "concept": concept, "coefficient": sign}
                           for (taxonomy, concept), sign in sorted(operating_working_capital_components(catalog.values()).items())],
            "limitation": "Recognition is not proof of completeness; disclose missing operating components, never silently omit them."}
    elif method == 'bank_residual_income':
        from .input_evidence import parent_regulatory_components
        contract["parent_regulatory_policy"] = {
            "scope": "parent_only",
            "forms": [{"form": form, "taxonomy": taxonomy,
                       "driver_components": {driver: parent_regulatory_components(driver, taxonomy) for driver in
                           ("opening_parent_equity", "capital.parent_opening_cash")}}
                      for form, taxonomy in (("FR Y-9LP", "fr-y-9lp-pc"), ("FR Y-9SP", "fr-y-9sp-sc"))],
            "sec_inline": {"taxonomy": "sec-parent-us-gaap", "opening_parent_equity": "StockholdersEquity minus PreferredStockValue", "capital.parent_opening_cash": "CashAndCashEquivalentsAtCarryingValue, direct pointer"},
            "requirement": "Use normalized parent-only FR Y-9LP PC, FR Y-9SP SC or SEC inline-XBRL facts. Generic JSON and literal quotes are unsupported. Use exactly one source family's signed components, same legal entity and opening date. SEC parent scope requires the explicit ParentCompanyMember dimension; its preferred-capital zero may be a verified issuer-capital disclosure, never an absent value. Gross balances do not prove distributable liquidity or a complete debt perimeter."}
        contract["bank_fdic_policy"] = {
            "scope": "bank_institution", "taxonomy": "fdic-ris",
            "opening_gaap_equity": "EQ minus EQPP", "opening_statutory_capital": "RBCT1C (CET1), direct pointer",
            "opening_gaap_to_statutory_equity": "RBCT1C minus EQ plus EQPP",
            "requirement": "Only for capital.subsidiaries.<index> opening drivers. Use exact normalized bank name and date; signed sums for bridges. A regulatory top holder is not proof of direct/full ownership or transferability. Never substitute these bank observations for parent-only or group accounts."}
        contract["bank_consolidation_policy"] = {
            "driver": "opening_consolidation_adjustments", "operation": "consolidation_equity",
            "terms": "group, parent, subsidiaries (map keyed by every legal_structure subsidiary ID)",
            "balance_shape": "Each balance has exactly value, evidence_ids (one normalized document), calculation={operation:sum,terms:[signed source facts]}",
            "group_components": "SEC GAAP StockholdersEquity minus PreferredStockValue",
            "parent_components": "Exact common-equity components in parent_regulatory_policy",
            "subsidiary_components": "FDIC EQ minus EQPP",
            "formula": "group minus parent minus sum(subsidiaries)",
            "requirements": "Exact opening date, reporting entities and units; union of operand IDs; balances must match the model and every supplied scenario opening. Source the complete legal perimeter independently.",
            "limitation": "An aggregate reconciliation of reported balances, not proof of individual elimination entries or of rounding as the cause of a difference. Preserve that distinction in the rationale."}
        from .distributable_equity import CAPITAL_FIELDS, SUB_FIELDS, CASH_SIGNS
        contract["bank_capital_drivers"] = {
            "capital": sorted(CAPITAL_FIELDS - {"subsidiaries", "parent_cash_flows"}),
            "parent_cash_flows": sorted(CASH_SIGNS),
            "subsidiary": sorted(SUB_FIELDS - {"id"}),
            "subsidiary_naming": "capital.subsidiaries.<zero_based_index>.<field>"}
        from .managed_care_adapter import _descriptor
        capital_names = (["capital." + key for key in contract["bank_capital_drivers"]["capital"]]
                         + ["capital.parent_cash_flows." + key for key in contract["bank_capital_drivers"]["parent_cash_flows"]]
                         + ["capital.subsidiaries.0." + key for key in contract["bank_capital_drivers"]["subsidiary"]])
        contract["bank_capital_mapping"] = {name: _descriptor(name) for name in capital_names}
    if method == 'fund_nav':
        from .fund_nav_preparation import POLICY
        contract['fund_nav_preparation'] = deepcopy(POLICY)
        contract['driver_envelope']['kind_policy'] = 'Fund NAV: perimeter, calendar, policy, nav_target and target_basis are analyst_estimate. All remaining drivers are historical and require the fund_nav_preparation proofs. No future periods or discount rate.'
    dossier = {"ticker": case["ticker"], "as_of": case["as_of"],
               "method_id": method, "decision": deepcopy(decision),
               "acquired_sources": {name: deepcopy(value) for name, value in case["sources"].items()
                                    if name != "method_inputs"},
               "acquisition_tasks": deepcopy(original["acquisition_tasks"]),
               "document_acquisition": deepcopy(provenance.get("source_acquisition")),
               "documents": [deepcopy(catalog[doc["id"]]) for doc in documents]}
    if method == 'fund_nav':
        dossier['fund_nav_statement_observations'] = deepcopy(provenance.get('fund_nav_statement_observations'))
    try:
        plan = propose(deepcopy(dossier), deepcopy(contract))
    except Exception as exc:
        return {"bundle": original, "status": "incomplete",
                "issues": [_issue("proposal", "proposal_error", type(exc).__name__ + ": " + str(exc))],
                "proposal": None, "provenance": provenance}
    candidate = {"method_id": method, "method_version": requirements["method_version"],
                 "plan": deepcopy(plan), "method_records": [], "analysis_context": {},
                 "approval_status": "automatic_non_approved"}
    if not isinstance(plan, dict):
        return {"bundle": original, "status": "incomplete", "issues": [_issue("proposal", "invalid_plan", "Piano non strutturato")],
                "proposal": candidate, "provenance": provenance}
    try:
        json.dumps(plan, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        return {"bundle": original, "status": "incomplete",
                "issues": [_issue("proposal", "invalid_json", "Piano non rappresentabile in JSON finito: " + str(exc))],
                "proposal": candidate, "provenance": provenance}
    perimeter, calendar, span, structural = _calendar(plan, cutoff, method=method)
    issues.extend(structural)
    rationale = plan.get("scenario_rationale")
    if not isinstance(rationale, dict) or set(rationale) != set(SCENARIOS) \
            or any(not _text(rationale[s]) for s in SCENARIOS):
        issues.append(_issue("scenario_rationale", "missing_rationale", "Motivazioni bear/base/bull esplicite richieste"))
    else:
        candidate["analysis_context"] = {"scenario_rationale": deepcopy(rationale)}
    schema = contract["schema"]
    entities = {}
    if method == "bank_residual_income":
        schema, entities, bank_issues = _bank_schema(plan, perimeter)
        issues.extend(bank_issues)
    if issues:
        return {"bundle": original, "status": "incomplete", "issues": issues,
                "proposal": candidate, "provenance": provenance}
    records, compilation, expiry_policies = _compile(plan, schema, entities, perimeter, calendar, span, catalog, cutoff, method=method)
    candidate["method_records"] = records
    provenance["expiry_policies"] = expiry_policies
    if compilation:
        return {"bundle": original, "status": "incomplete", "issues": compilation,
                "proposal": candidate, "provenance": provenance}
    try:
        revised = revise_sector_analysis(original, method_records=records,
                                         analysis_context=candidate["analysis_context"])
    except (TypeError, ValueError) as exc:
        return {"bundle": original, "status": "incomplete",
                "issues": [_issue("method_inputs", "invalid_revision", type(exc).__name__ + ": " + str(exc))],
                "proposal": candidate, "provenance": provenance}
    issues = [_issue(issue.get("field", "method_inputs"), issue.get("code", "validation"),
                     issue.get("message") or issue.get("reason") or "Input non valido")
              for issue in revised["decision"].get("issues", []) if issue.get("blocking")]
    if revised["decision"].get("missing_fields"):
        issues.append(_issue("method_inputs", "missing_fields",
                             "Campi del metodo non coperti: " + ", ".join(revised["decision"]["missing_fields"])))
    return {"bundle": revised, "status": "incomplete" if issues else "prepared", "issues": issues,
            "proposal": candidate, "provenance": provenance}
