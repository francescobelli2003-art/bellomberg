"""Deterministic numerical proofs against primary structured tool documents."""
import json
import re
from datetime import date, timedelta
from math import fsum, isclose, isfinite


def _finite(value):
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


def _pointer(document, path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("JSON pointer assoluto richiesto")
    node = document
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not part.isascii() or not part.isdigit() or str(int(part)) != part:
                raise ValueError("indice JSON pointer non valido")
            node = node[int(part)]
        else:
            node = node[part]
    return node


def operating_working_capital_components(documents=None):
    """Recognized operating components; unknown components need explicit review.

    This is not a claim that a selected subset exhausts an issuer's balance sheet.
    Total assets/liabilities, cash and financing never become operating NWC here.
    """
    from bellomberg.market_data.sec_xbrl import CANONICAL
    signs = {}
    for key in ("receivables", "inventory"):
        gaap, ifrs = CANONICAL[key]
        signs.update({("us-gaap", name): 1 for name in gaap})
        signs.update({("ifrs-full", name): 1 for name in ifrs})
    signs.update({("us-gaap", name): -1 for name in (
        "AccountsPayableCurrent", "ContractWithCustomerLiabilityCurrent")})
    # Expand new prompt contracts only when this concept is actually supplied.
    # Unaffected catalogs keep their already-paid contract identity. The fact
    # compiler (no catalog argument) recognizes the full supported vocabulary.
    deferred = ('us-gaap', 'DeferredRevenueCurrent')
    if documents is None:
        signs[deferred] = -1
    else:
        for document in documents:
            try:
                parsed = json.loads(document['text'])
            except (ValueError, TypeError, KeyError):
                continue
            facts = parsed.get('facts') if isinstance(parsed, dict) else None
            if isinstance(facts, list) and any(isinstance(fact, dict) and
                    (fact.get('taxonomy'), fact.get('concept')) == deferred for fact in facts):
                signs[deferred] = -1
                break
    signs.update({("ifrs-full", "CurrentTradePayables"): -1})
    signs.update({('reported-statement', name): sign for name, sign in
                  (('Inventories', 1), ('TradeReceivables', 1), ('TradePayables', -1), ('ContractLiabilities', -1))})
    return signs


def revenue_concepts():
    from bellomberg.market_data.sec_xbrl import CANONICAL
    gaap, ifrs = CANONICAL['revenue']
    return ({('us-gaap', name) for name in gaap} | {('ifrs-full', name) for name in ifrs}
            | {('reported-statement', 'Revenue')})


def entity_name_key(value):
    """Compare display case only; preserve punctuation, spacing and non-ASCII.

    This is not an alias/identity resolver. Source CIK/RSSD, accounting scope,
    original names and receipt checks remain unchanged.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))


def same_entity_name(left, right):
    key = entity_name_key(left)
    return key is not None and key == entity_name_key(right)


def parent_regulatory_components(driver, taxonomy="fr-y-9lp-pc"):
    """Exact per-form balances; neither distributability nor total debt is inferred."""
    return {
        "fr-y-9lp-pc": {"opening_parent_equity": {"BHCP3210": 1, "BHCP3283": -1},
                       "capital.parent_opening_cash": {"BHCP5993": 1, "BHCP0010": 1}},
        "fr-y-9sp-sc": {"opening_parent_equity": {"BHSP3210": 1, "BHSP3283": -1},
                       "capital.parent_opening_cash": {"BHSP5993": 1, "BHSP0010": 1}},
        "sec-parent-us-gaap": {"opening_parent_equity": {"StockholdersEquity": 1, "PreferredStockValue": -1},
                               "capital.parent_opening_cash": {"CashAndCashEquivalentsAtCarryingValue": 1}},
    }.get(taxonomy, {}).get(driver)


def structured_fact_proof(item, evidence, unit, period, *, scale, require_annual=False,
                          allowed_concepts=None, concept_signs=None, expected_entity=None,
                          regulatory_components=None, parent_cash_pointer_repairs=None):
    """Read exact values/periods, a signed sum, or a three-observation revenue TTM.

    No arbitrary arithmetic, assumed missing operands, mixed-period openings or
    unproved adjustment is accepted. Source authenticity and economic selection
    remain outside this arithmetic proof.
    """
    try:
        catalog = {doc["id"]: doc for doc in evidence}
        seen, pointer_repairs = set(), []
        economic_sides, economic_metrics = set(), set()
        regulatory_terms, observation_kinds, regulatory_families = [], [], set()
        def observed(fact, coefficient=None, *, ttm=False):
            ids, pointers = fact.get("evidence_ids"), fact.get("evidence_pointer")
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in catalog:
                raise ValueError("fonte JSON primaria univoca richiesta")
            if not isinstance(pointers, dict) or set(pointers) != {"value", "unit", "period"}:
                raise ValueError("pointer value/unit/period obbligatori")
            value_pointer = pointers['value']
            repair_root = re.fullmatch(r"(/facts/(?:0|[1-9][0-9]*))/end", str(value_pointer))
            repair = (parent_cash_pointer_repairs is not None and coefficient is not None
                      and repair_root is not None and value_pointer == pointers['period']
                      and pointers['unit'] == repair_root[1] + '/unit')
            if repair:
                value_pointer = repair_root[1] + '/value'
            match = re.fullmatch(r"(/facts/(?:0|[1-9][0-9]*))/(value|observation/val)", str(value_pointer))
            if not match:
                raise ValueError("misura primaria normalizzata /facts/index richiesta")
            root, field = match.groups()
            parent = root + ("/observation" if field == "observation/val" else "")
            if pointers["unit"] != root + "/unit" or pointers["period"] != parent + "/end":
                raise ValueError("valore, unita e periodo devono appartenere alla stessa osservazione")
            identity = ids[0], value_pointer
            if identity in seen:
                raise ValueError("misura duplicata nella riconciliazione")
            seen.add(identity)
            raw = json.loads(catalog[ids[0]]["text"])
            source_fact = _pointer(raw, root)
            metadata = catalog[ids[0]].get("metadata")
            document = catalog[ids[0]]
            printed = isinstance(metadata, dict) and metadata.get('normalizer') == 'statement_tables_v1'
            if ttm and field != 'observation/val' and not printed:
                raise ValueError('TTM requires verified structured filing observations')
            sec_source = (document.get("origin") == "SEC_XBRL_tool"
                          or str(document.get("id", "")).startswith("xbrl-")
                          or str(document.get("url", "")).startswith("https://data.sec.gov/api/xbrl/companyfacts/")
                          or isinstance(raw, dict) and "cik" in raw
                          or isinstance(metadata, dict) and str(metadata.get("emittente_id", "")).startswith("CIK:"))
            if sec_source and expected_entity is not None:
                issuer = raw.get("issuer") if isinstance(raw, dict) else None
                if not same_entity_name(issuer, expected_entity):
                    raise ValueError("entita SEC normalizzata assente o diversa dal perimetro del driver")
            declared_entity = metadata.get("entity") if isinstance(metadata, dict) else None
            if isinstance(source_fact, dict) and ("entity" in source_fact or declared_entity is not None):
                entity = source_fact.get("entity", declared_entity)
                if (not same_entity_name(entity, expected_entity)
                        or (isinstance(metadata, dict) and "entity" in metadata and metadata["entity"] != entity)):
                    raise ValueError("entita della misura diversa dal perimetro del driver o dal documento")
            concept = (source_fact.get("taxonomy"), source_fact.get("concept")) if isinstance(source_fact, dict) else None
            if concept and concept[0] == 'reported-statement' and not printed:
                raise ValueError('reported statement observations require a verified normalization')
            regulatory = (concept and concept[0] in ("fr-y-9lp-pc", "fr-y-9sp-sc", "fdic-ris", "sec-parent-us-gaap")) or (
                isinstance(metadata, dict) and metadata.get("normalizer") in ("regulatory_pdf_v1", "fdic_financials_v1", "sec_parent_inline_v1"))
            observation_kinds.append(bool(regulatory))
            if regulatory:
                scope = "bank_institution" if concept[0] == "fdic-ris" else "parent_only"
                if (regulatory_components is None or concept[0] not in regulatory_components
                        or not isinstance(metadata, dict) or metadata.get("scope") != scope
                        or source_fact.get("scope") != scope or not expected_entity
                        or not same_entity_name(source_fact.get("entity"), expected_entity)):
                    raise ValueError("misura regolamentare non ammessa per driver/entita/perimetro")
                regulatory_terms.append((concept[1], 1 if coefficient is None else coefficient))
                regulatory_families.add(concept[0])
            if repair:
                if (not regulatory or concept[0] not in ('fr-y-9lp-pc', 'fr-y-9sp-sc')
                        or regulatory_components[concept[0]] != parent_regulatory_components(
                            'capital.parent_opening_cash', concept[0])):
                    raise ValueError('normalizzazione pointer riservata al ponte cassa parent regolamentare')
                pointer_repairs.append({'source_id': ids[0], 'supplied_value_pointer': pointers['value'],
                    'validated_value_pointer': value_pointer,
                    'reason': 'value duplicated the same normalized fact end pointer; quoted amount, unit, date, entity and complete cash components verified'})
            if concept_signs is not None:
                if concept not in concept_signs or coefficient != concept_signs[concept]:
                    raise ValueError("componente NWC operativo o segno economico non riconosciuto")
                # These tags may describe the same current customer advances.
                # A signed sum cannot establish that they are disjoint balances.
                metric = (('us-gaap', 'ContractWithCustomerLiabilityCurrent')
                          if concept == ('us-gaap', 'DeferredRevenueCurrent') else concept)
                if metric in economic_metrics:
                    raise ValueError("componente NWC duplicato fra documenti/osservazioni")
                economic_metrics.add(metric)
                economic_sides.add(coefficient)
            if allowed_concepts is not None and (
                    not isinstance(source_fact, dict)
                    or (source_fact.get("taxonomy"), source_fact.get("concept")) not in allowed_concepts):
                raise ValueError("concept XBRL non ammesso per il driver storico")
            value = _pointer(raw, value_pointer)
            source_unit = _pointer(raw, pointers["unit"])
            source_period = _pointer(raw, pointers["period"])
            if not ttm and period is not None and source_period != period:
                raise ValueError("periodo fonte diverso dal saldo iniziale: rollforward non implicito")
            if require_annual and not ttm:
                start = date.fromisoformat(_pointer(raw, parent + "/start"))
                end = date.fromisoformat(source_period)
                if not 364 <= (end - start).days + 1 <= 371:
                    raise ValueError("ricavi annuali richiesti: dato infrannuale non annualizzato implicitamente")
            if not _finite(value) or not _finite(fact.get("quoted_value")) or value != fact["quoted_value"]:
                raise ValueError("cifra proposta diversa dal valore JSON della fonte")
            if not isinstance(source_unit, str) or source_unit != fact.get("quoted_unit"):
                raise ValueError("unita proposta diversa dall'unita JSON della fonte")
            conversion = scale(source_unit, unit)
            if conversion is None:
                raise ValueError("scala/valuta JSON non riconciliata")
            amount = value * conversion
            if not ttm:
                return amount
            if printed:
                from .preparation_exhibits import _sec_parts
                from .statement_table_evidence import PREFIX, TAXONOMY
                cik, accession, _ = _sec_parts(document['url'])
                origin_id = metadata.get('source_document_id')
                if (field != 'value' or concept != (TAXONOMY, 'Revenue')
                        or not isinstance(origin_id, str) or not re.fullmatch(r'[0-9a-f]{64}', origin_id)
                        or document['id'] != PREFIX + origin_id or document['document_sha256'] != origin_id
                        or metadata.get('emittente_id') != 'CIK:' + cik.zfill(10)
                        or metadata.get('accession') != accession or metadata.get('scope') != 'consolidated'
                        or source_fact.get('statement') != 'income' or source_fact.get('scope') != 'consolidated'):
                    raise ValueError('TTM: printed statement provenance or revenue scope differs')
                start = date.fromisoformat(_pointer(raw, root + '/start'))
                end = date.fromisoformat(source_period)
                if start > end or end > date.fromisoformat(metadata['report_date']):
                    raise ValueError('TTM: printed statement period differs from filing')
                return {'amount': amount, 'issuer': cik.zfill(10), 'concept': concept,
                        'unit': source_unit, 'start': start, 'end': end}
            metadata = catalog[ids[0]].get("metadata")
            accession = metadata.get("accession") if isinstance(metadata, dict) else None
            raw_cik = raw.get("cik") if isinstance(raw, dict) else None
            if (not str(raw_cik).isdecimal() or len(str(raw_cik)) > 10
                    or not isinstance(accession, str) or not re.fullmatch(r"[0-9]{18}", accession)):
                raise ValueError("TTM: identita CIK/accession SEC assente")
            cik = str(raw_cik).zfill(10)
            document = catalog[ids[0]]
            if (metadata.get("emittente_id") != "CIK:" + cik
                    or document["id"] != "xbrl-" + cik + "-" + accession
                    or document.get("url") != "https://data.sec.gov/api/xbrl/companyfacts/CIK" + cik + ".json"):
                raise ValueError("TTM: fonte XBRL e issuer CIK discordanti")
            observation = source_fact.get("observation") if isinstance(source_fact, dict) else None
            accn = observation.get("accn") if isinstance(observation, dict) else None
            if (not isinstance(accn, str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accn)
                    or accn.replace("-", "") != accession
                    or observation.get("filed") != document.get("published_at")):
                raise ValueError("TTM: observation non appartiene al filing citato")
            start_raw = _pointer(raw, parent + "/start")
            if not isinstance(start_raw, str) or not isinstance(source_period, str):
                raise ValueError("TTM: inizio/fine periodo ISO richiesti")
            start, end = date.fromisoformat(start_raw), date.fromisoformat(source_period)
            if start.isoformat() != start_raw or end.isoformat() != source_period or start > end:
                raise ValueError("TTM: intervallo osservato non valido")
            return {"amount": amount, "issuer": cik, "concept": concept, "unit": source_unit,
                    "start": start, "end": end}
        if "calculation" in item:
            if any(key in item for key in ("evidence_pointer", "evidence_quote", "quoted_value", "quoted_unit")):
                raise ValueError("calcolo e cifra citata non possono essere mescolati")
            calculation = item["calculation"]
            if not isinstance(calculation, dict) or set(calculation) != {"operation", "terms"}:
                raise ValueError("calcolo documentale incompleto o campi non consumati")
            terms = calculation["terms"]
            if calculation["operation"] == "sum":
                if not isinstance(terms, list) or not 2 <= len(terms) <= 32:
                    raise ValueError("riconciliazione richiede da 2 a 32 operandi documentati")
                amounts = []
                for term in terms:
                    if not isinstance(term, dict) or set(term) != {"coefficient", "evidence_ids", "evidence_pointer", "quoted_value", "quoted_unit"}:
                        raise ValueError("operando incompleto o campi non consumati")
                    coefficient = term["coefficient"]
                    if type(coefficient) is not int or coefficient not in (-1, 1):
                        raise ValueError("solo segni +1/-1, nessun fattore arbitrario")
                    amounts.append(coefficient * observed(term, coefficient))
                if concept_signs is not None and economic_sides != {-1, 1}:
                    raise ValueError("ponte NWC richiede attivita e passivita operative documentate")
                expected = fsum(amounts)
            elif calculation["operation"] == "trailing_twelve_months":
                revenue = revenue_concepts()
                if not require_annual or allowed_concepts != revenue or concept_signs is not None or period is None:
                    raise ValueError("TTM consentito solo per ricavi storici al periodo opening")
                if not isinstance(terms, dict) or set(terms) != {"annual", "current_ytd", "prior_ytd"}:
                    raise ValueError("TTM richiede annual, current_ytd e prior_ytd esatti")
                if any(not isinstance(term, dict) or set(term) !=
                       {"evidence_ids", "evidence_pointer", "quoted_value", "quoted_unit"}
                       for term in terms.values()):
                    raise ValueError("TTM: operando privo di prova primaria o con campi non consumati")
                proven = {name: observed(terms[name], ttm=True) for name in
                          ("annual", "current_ytd", "prior_ytd")}
                declared_ids = item.get("evidence_ids")
                used_ids = {term["evidence_ids"][0] for term in terms.values()}
                if (not isinstance(declared_ids, list) or len(declared_ids) != len(set(declared_ids))
                        or set(declared_ids) != used_ids):
                    raise ValueError("TTM: evidence_ids del driver diversi dalle fonti degli operandi")
                if (len({row["issuer"] for row in proven.values()}) != 1
                        or len({row["concept"] for row in proven.values()}) != 1
                        or len({row["unit"] for row in proven.values()}) != 1):
                    raise ValueError("TTM: issuer, concept e valuta degli operandi devono coincidere")
                annual, current, prior = (proven[name] for name in ("annual", "current_ytd", "prior_ytd"))
                annual_days = (annual["end"] - annual["start"]).days + 1
                current_days = (current["end"] - current["start"]).days + 1
                prior_days = (prior["end"] - prior["start"]).days + 1
                if (not 364 <= annual_days <= 371 or current["start"] != annual["end"] + timedelta(days=1)
                        or prior["start"] != annual["start"] or prior["end"] >= annual["end"]
                        or current["end"].isoformat() != period or not 0 < current_days < annual_days
                        or not 0 < prior_days < annual_days
                        or not 364 <= annual_days + current_days - prior_days <= 371):
                    raise ValueError("TTM: periodi annual/YTD non riconciliati a dodici mesi")
                expected = fsum((annual["amount"], current["amount"], -prior["amount"]))
            else:
                raise ValueError("operazione documentale non supportata")
        else:
            if "evidence_quote" in item:
                raise ValueError("prova letterale e JSON mescolate")
            expected = observed(item)
        if regulatory_components is not None:
            if not regulatory_terms or not all(observation_kinds) or len(regulatory_families) != 1:
                raise ValueError("ponte regolamentare incompleto o famiglie mescolate")
            components = regulatory_components[next(iter(regulatory_families))]
            if (len(regulatory_terms) != len(components)
                    or len({name for name, _ in regulatory_terms}) != len(regulatory_terms)
                    or dict(regulatory_terms) != components):
                raise ValueError("ponte regolamentare incompleto o segni/codici diversi dalla definizione del saldo")
        if not _finite(item.get("value")) or not isclose(item["value"], expected, rel_tol=1e-10, abs_tol=1e-8):
            raise ValueError("valore non riconciliato alla cifra/operazione documentata")
    except (ValueError, KeyError, IndexError, TypeError, ArithmeticError) as exc:
        return str(exc)
    if parent_cash_pointer_repairs is not None:
        parent_cash_pointer_repairs.extend(pointer_repairs)
    return None
