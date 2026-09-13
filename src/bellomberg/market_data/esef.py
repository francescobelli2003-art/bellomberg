"""
esef.py (V3, MASTER_TODO §9-sexies n.3) - STORICO FONDAMENTALE riga-per-riga dai
filing ESEF europei via filings.xbrl.org (gratuito, ufficiale, deterministico).

Copre le societa' EU quotate SENZA filing SEC (i .MI del book) con lo STESSO CONTRATTO di sec_xbrl.get_financial_history:
items {voce_canonica: {fy: valore}} in unita' piene + derivate. Cosi' dcf_engine,
il foglio Historical e il mid-cycle dei ciclici lo consumano senza modifiche.

MISURE 17/07 (probe, non ipotesi):
  - API ~0,4-1,5s a chiamata; xBRL-JSON dei prospetti 0,03-0,3 MB (eccezione: una banca
    del book ~76MB: tagging integrale) -> si scarica, si ESTRAE, si butta il raw (cache
    delle sole serie estratte).
  - ogni filing porta DUE anni (corrente + comparativo): i buchi di deposito si
    ricolmano dal comparativo dell'anno dopo (caso reale FY2023 di un emittente del book).
  - copertura dal FY2020-21 (l'ESEF nasce li'): ~5 anni max, LIMITE DICHIARATO
    nel payload (niente "10 anni" promessi e non mantenuti).
  - LEI del book verificati via /api/entities (nel negozio privato lei_emittenti).
Facts: si tengono SOLO quelli senza assi dimensionali extra (ComponentsOfEquityAxis
e simili sono breakdown, non il totale di bilancio).
"""
from bellomberg.core.paths import DATA_DIR, PROJECT_ROOT
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:  # standalone (python esef.py <TICKER>): il .env lo carica config.py, qui non passa
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

BASE = "https://filings.xbrl.org/api"
class ContattoMancante(RuntimeError):
    """SEC_CONTACT_EMAIL assente: filings.xbrl.org vuole un contatto nello User-Agent."""


def _headers() -> dict:
    """Header per filings.xbrl.org, costruiti a ogni chiamata; senza contatto,
    errore dichiarato (regola 14/07) — i chiamanti stanno in try/except.
    02/09 (pubblicazione B2): prima c'era l'email del PM cablata."""
    c = (os.environ.get("SEC_CONTACT_EMAIL") or "").strip()
    if not c:
        raise ContattoMancante(
            "SEC_CONTACT_EMAIL assente nel .env: filings.xbrl.org (ESEF) vuole "
            "un contatto nello User-Agent (SEC_CONTACT_EMAIL=tua@email nel .env)")
    return {"User-Agent": f"Bellomberg research (contatto: {c})"}
CACHE_DIR = str(DATA_DIR / "xbrl_cache")
INDEX_TTL_S = 7 * 24 * 3600          # l'indice filing si rilegge ogni 7 giorni
MAX_JSON_MB = 150                     # guardia dichiarata sui raw abnormi (il raw da 76MB di una banca del book passa)
_DIM_KEYS_BASE = {"concept", "entity", "period", "unit", "language"}

# I LEI del book, misurati 17/07 via /api/entities, vivono nel negozio privato
# (negozi_privati.carica_lei: data/lei_emittenti.json, forma in lei_emittenti.example.json).
# La risoluzione per NOME (sotto) e' il fallback per i ticker nuovi, dichiarata nel payload.

# voce canonica -> tag ifrs-full alternativi (il primo comanda, gli altri riempiono
# gli anni mancanti). Base = stessa lista IFRS di sec_xbrl (contratto identico);
# qui si AGGIUNGONO i tag visti nei filing ESEF reali (probe del 17/07 su un emittente del book).
_ESEF_EXTRA_TAGS = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "operating_income": ["ProfitLossFromOperatingActivities", "OperatingIncome"],
    "cfo": ["CashFlowsFromUsedInOperatingActivities",
            "CashFlowsFromUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              "PurchaseOfPropertyPlantAndEquipment"],
}
# mapping bancario DEDICATO (audit/12 R7): le banche non hanno "revenue"/EBITDA -
# le righe che servono a RI/DDM e al mid-cycle bancario. Tag MISURATI sui filing
# della banca del book, 17/07 (review: commissioni nette FY2023 1.316M = pubblicato; impairment
# IFRS9 = serie del costo del rischio 2020-25 per V4). Regola 14/07: NIENTE
# fallback dissimili (interbancario != clientela, servizi generici != commissioni):
# se un filing non tagga la voce, il buco resta e si dichiara (n_items).
# NOTE PER V4 (misurate): lo schema bancario Circ.262 NON tagga una riga "equity
# totale" senza assi (book value: da yfinance/bilancio, dichiarare la fonte);
# CET1/RWA sono Pillar 3, NON esistono nell'ESEF.
BANK_ROWS = {
    "interest_revenue": ["InterestRevenueCalculatedUsingEffectiveInterestMethod",
                         "RevenueFromInterest", "InterestIncomeOnLoansAndAdvancesToCustomers"],
    "interest_expense_bank": ["InterestExpense"],
    "fee_commission_net": ["FeeAndCommissionIncomeExpense"],
    "fee_commission_income": ["FeeAndCommissionIncome"],
    "impairment_ifrs9": ["ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9"],
    "trading_income": ["TradingIncomeExpense"],
    "loans_to_customers": ["LoansAndAdvancesToCustomers"],
    "loans_to_banks": ["LoansAndAdvancesToBanks"],
    "deposits_from_customers": ["DepositsFromCustomers"],
    "deposits_from_banks": ["DepositsFromBanks"],
}


def _canonical_ifrs() -> Dict[str, List[str]]:
    """{voce: [tag ifrs]} — base condivisa con sec_xbrl + integrazioni ESEF + banca."""
    from bellomberg.market_data.sec_xbrl import CANONICAL
    out: Dict[str, List[str]] = {}
    for canon, (_gaap, ifrs) in CANONICAL.items():
        tags = list(ifrs)
        for extra in _ESEF_EXTRA_TAGS.get(canon, []):
            if extra not in tags:
                tags.append(extra)
        out[canon] = tags
    for canon, tags in BANK_ROWS.items():
        out.setdefault(canon, list(tags))
    return out


def _cache_path(lei: str) -> str:
    return os.path.join(CACHE_DIR, f"esef_{lei}.json")


def _load_cache(lei: str) -> Dict[str, Any]:
    try:
        with open(_cache_path(lei), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(lei: str, cache: Dict[str, Any]) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(lei), "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass  # cache best-effort: la fonte resta l'API


def resolve_lei(ticker: str, company_name: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(lei, nota). Prima il negozio privato dei LEI, poi ricerca per NOME su /api/entities
    (fallback dichiarato). Ambiguita' o zero match = errore dichiarato, mai un guess."""
    tk = (ticker or "").upper().strip()
    from bellomberg.storage.negozi_privati import carica_lei
    negozio = carica_lei()
    if tk in negozio["lei"]:
        return negozio["lei"][tk], f"LEI dal negozio privato lei_emittenti ({tk})"
    nota_negozio = ("negozio lei_emittenti %s (%s); " % (negozio["origine"], negozio["motivo"])
                    if negozio["origine"] in ("assente", "illeggibile") else "")
    name = company_name
    if not name:
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            name = info.get("longName") or info.get("shortName")
        except Exception:
            name = None
    if not name:
        return None, f"{nota_negozio}{tk}: LEI non nel negozio e nome societa' non ottenibile (dichiarato)"
    try:
        from bellomberg.valuation.peer_comps import _norm_issuer
        _norm = _norm_issuer
    except Exception:
        def _norm(x):
            return str(x or "").lower().strip()
    # Audit 11/09 (Fable 5.1, run 10/09 memo #54): la sola query sulle prime tre parole del
    # nome Yahoo ("Beispiel AG") tornava 0 entita' mentre il repository registra la forma
    # giuridica lunga ("Beispiel Aktiengesellschaft"): storico ESEF n.d. per un emittente
    # che c'era, e il DCF chiesto dal PM finiva in "filings: Fonte senza dati". Ricerca
    # PROGRESSIVA: nome pieno, poi il nome SENZA forma giuridica (_norm: la stessa
    # normalizzazione del confronto esatto qui sotto). La guardia di ambiguita' resta.
    q_pieno = " ".join(str(name).split()[:3])
    q_core = " ".join(_norm(name).split()[:2])
    query = []
    for q in (q_pieno, q_core):
        if q and q.lower() not in [x.lower() for x in query]:
            query.append(q)
    ents = []
    usata = None
    try:
        import requests
        for q in query:
            r = requests.get(BASE + "/entities", params={
                "page[size]": 10,
                "filter": json.dumps([{"name": "name", "op": "ilike", "val": f"%{q}%"}]),
            }, headers=_headers(), timeout=30)
            if not r.ok:
                return None, f"ricerca entita' ESEF fallita (HTTP {r.status_code})"
            ents = r.json().get("data", [])
            if ents:
                usata = q
                break
    except ContattoMancante as e:
        return None, str(e)   # review 02/09: il nome della classe non dice cosa fare
    except Exception as e:
        return None, f"ricerca entita' ESEF fallita ({type(e).__name__})"
    if not ents:
        return None, (f"'{name}': nessuna entita' sul repository ESEF (cercato: "
                      + ", ".join(query) + "; societa' non-UE o non quotata UE?)")
    target = _norm(name)
    exact = [e for e in ents
             if _norm((e.get("attributes") or {}).get("name")) == target]
    pick = exact if exact else ents
    if len(pick) > 1:
        cand = "; ".join(str((e.get("attributes") or {}).get("name")) for e in pick[:5])
        return None, f"'{name}': {len(pick)} entita' ambigue su ESEF ({cand}) - serve LEI esplicito"
    e = pick[0]
    lei = (e.get("attributes") or {}).get("identifier") or e.get("id")
    ename = (e.get("attributes") or {}).get("name")
    return lei, (f"LEI risolto per NOME su filings.xbrl.org: {ename} "
                 f"(query '{usata}', fallback dichiarato)")


def _list_filings(lei: str, max_pages: int = 20) -> List[Dict[str, Any]]:
    import requests
    from urllib.parse import urljoin, urlsplit, quote
    if max_pages < 1:
        raise ValueError("max_pages deve essere positivo")
    url = BASE + f"/entities/{quote(lei, safe='')}/filings"
    percorso = urlsplit(url).path
    visti, filings = set(), []
    while url:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "filings.xbrl.org" or parts.path != percorso:
            raise ValueError("pagina ESEF fuori dall'endpoint dell'emittente")
        if url in visti or len(visti) >= max_pages:
            raise ValueError("paginazione ESEF ciclica o limite pagine raggiunto: catalogo incompleto")
        visti.add(url)
        r = requests.get(url, params={"page[size]": 50} if len(visti) == 1 else None,
                         headers=_headers(), timeout=60)
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload.get("data"), list):
            raise ValueError("risposta ESEF senza elenco data")
        filings.extend(payload["data"])
        prossimo = (payload.get("links") or {}).get("next")
        if isinstance(prossimo, dict):
            prossimo = prossimo.get("href")
        url = urljoin(url, prossimo) if prossimo else None
    out = []
    for f in filings:
        a = f.get("attributes") or {}
        ju = a.get("json_url")
        entry = {"id": f.get("id") or a.get("fxo_id") or a.get("period_end") or "?",
                 "period_end": a.get("period_end"),
                 "date_added": a.get("date_added"), "emittente_id": f"LEI:{lei}",
                 "fonte": "filings.xbrl.org (repository non esaustivo)",
                 "language": a.get("language"), "country": a.get("country")}
        for campo in ("report_url", "package_url"):
            entry[campo] = urljoin("https://filings.xbrl.org", a[campo]) if a.get(campo) else None
        if not entry["report_url"]:
            entry["no_report"] = True
        if ju:
            entry["json_url"] = ("https://filings.xbrl.org" + ju) if ju.startswith("/") else ju
        else:
            # review 17/07 F1a (caso reale FY2025 di un emittente del book): il filing esiste sull'indice
            # ma SENZA xBRL-JSON — va dichiarato come buco, non scartato zitto
            entry["no_json"] = True
        out.append(entry)
    # ordine: piu' vecchio prima -> i filing recenti SOVRASCRIVONO (restatement vince)
    out.sort(key=lambda x: (str(x.get("period_end") or ""), str(x.get("date_added") or "")))
    return out


def _fy_from_period(period: str) -> Optional[int]:
    """FY da un periodo xBRL-JSON. Durata 'start/end' ~1 anno -> anno di (end - 1g);
    instant 'datetime' -> anno del giorno prima se e' il 1 gennaio a mezzanotte
    (convenzione xBRL: istante = fine giornata precedente), altrimenti anno della data."""
    if not period:
        return None
    try:
        if "/" in period:
            st_s, en_s = period.split("/", 1)
            st = datetime.fromisoformat(st_s.replace("Z", ""))
            en = datetime.fromisoformat(en_s.replace("Z", ""))
            days = (en - st).days
            if days < 300 or days > 400:   # solo esercizi annuali (come sec_xbrl)
                return None
            return (en - timedelta(days=1)).year
        dt = datetime.fromisoformat(period.replace("Z", ""))
        if dt.month == 1 and dt.day == 1 and dt.hour == 0:
            return dt.year - 1
        return dt.year
    except Exception:
        return None


def _extract_filing_facts(json_url: str) -> Tuple[Dict[str, List[List[Any]]], Optional[str]]:
    """Scarica il xBRL-JSON e ritorna ({concept: [[fy, valore, unita'], ...]}, err).
    Tiene SOLO i fact numerici senza assi extra. Il raw non si salva mai."""
    import requests
    try:
        h = requests.head(json_url, headers=_headers(), timeout=30, allow_redirects=True)
        size = int(h.headers.get("Content-Length") or 0)
        if size > MAX_JSON_MB * 1e6:
            return {}, f"raw {size/1e6:.0f}MB > guardia {MAX_JSON_MB}MB: filing saltato (dichiarato)"
    except Exception:
        pass  # HEAD best-effort: si tenta comunque il GET
    try:
        raw = requests.get(json_url, headers=_headers(), timeout=300).json()
    except ContattoMancante as e:
        return {}, str(e)
    except Exception as e:
        return {}, f"download/parse fallito ({type(e).__name__})"
    out: Dict[str, List[List[Any]]] = {}
    for fact in (raw.get("facts") or {}).values():
        dims = fact.get("dimensions") or {}
        if set(dims.keys()) - _DIM_KEYS_BASE:
            continue  # breakdown dimensionale, non il totale
        concept = str(dims.get("concept") or "")
        if not concept.startswith("ifrs-full:"):
            continue
        try:
            val = float(fact.get("value"))
        except (TypeError, ValueError):
            continue  # fact testuale
        fy = _fy_from_period(dims.get("period") or "")
        if fy is None:
            continue
        unit = str(dims.get("unit") or "").replace("iso4217:", "").replace("xbrli:", "")
        out.setdefault(concept.split(":", 1)[1], []).append([fy, val, unit])
    return out, None


def _refresh_entity_cache(lei: str) -> Dict[str, Any]:
    """Indice filing + estrazioni nuove in cache; i filing gia' estratti non si riscaricano."""
    cache = _load_cache(lei)
    filings = cache.get("filings") or {}
    idx_age = time.time() - float(cache.get("index_fetched_at") or 0)
    known_err = cache.get("index_error")
    if idx_age < INDEX_TTL_S and filings and not known_err:
        return cache
    try:
        listed = _list_filings(lei)
        cache["index_fetched_at"] = time.time()
        cache.pop("index_error", None)
    except ContattoMancante as e:
        cache["index_error"] = str(e)
        return cache
    except Exception as e:
        cache["index_error"] = f"indice filing non raggiungibile ({type(e).__name__})"
        return cache
    for f in listed:
        fid = str(f["id"])
        if f.get("no_json"):
            filings[fid] = {"period_end": f.get("period_end"), "date_added": f.get("date_added"),
                            "no_json": True}
            continue
        if fid in filings and filings[fid].get("facts"):
            continue
        facts, err = _extract_filing_facts(f["json_url"])
        filings[fid] = {"period_end": f.get("period_end"), "date_added": f.get("date_added"),
                        "facts": facts, "extract_error": err,
                        "extracted_at": datetime.now().isoformat(timespec="seconds")}
    cache["filings"] = filings
    _save_cache(lei, cache)
    return cache


def get_esef_history(ticker: str, years: int = 10, company_name: Optional[str] = None) -> Dict[str, Any]:
    """Storico annuale riga-per-riga dai filing ESEF. Contratto = sec_xbrl."""
    lei, lei_note = resolve_lei(ticker, company_name=company_name)
    if not lei:
        return {"error": f"{ticker}: {lei_note}"}
    cache = _refresh_entity_cache(lei)
    filings = cache.get("filings") or {}
    if not filings:
        return {"error": f"{ticker}: nessun filing ESEF per LEI {lei} "
                         f"({cache.get('index_error') or 'repository vuoto per questa entita'''})"}
    # fusione per concetto: filing in ordine cronologico, il PIU' RECENTE sovrascrive
    # (restatement/comparativo aggiornato vince sul deposito originale)
    ordered = sorted(filings.values(),
                     key=lambda x: (str(x.get("period_end") or ""), str(x.get("date_added") or "")))
    # review 17/07 F1: i BUCHI del repository si DICHIARANO (filing senza xBRL-JSON
    # — caso reale FY2025 di un emittente del book — o con estrazione fallita), mai anni spariti zitti
    gaps = []
    for f in ordered:
        _fy = str(f.get("period_end") or "?")[:4]
        if f.get("no_json"):
            gaps.append(f"FY{_fy}: filing sul repository SENZA xBRL-JSON (non estraibile)")
        elif f.get("extract_error"):
            gaps.append(f"FY{_fy}: estrazione fallita ({f['extract_error']})")
    gaps = sorted(set(gaps))
    # review 17/07 F2: indice non aggiornabile = staleness DICHIARATA nel payload
    index_note = None
    if cache.get("index_error"):
        _ts = cache.get("index_fetched_at")
        _quando = (datetime.fromtimestamp(float(_ts)).strftime("%d/%m/%Y") if _ts else "mai")
        index_note = ("indice filing NON aggiornato (%s; ultimo refresh riuscito: %s): "
                      "possibili depositi recenti mancanti" % (cache["index_error"], _quando))
    by_concept: Dict[str, Dict[int, Any]] = {}
    unit_count: Dict[str, Dict[str, int]] = {}
    for f in ordered:
        for concept, rows in (f.get("facts") or {}).items():
            for fy, val, unit in rows:
                by_concept.setdefault(concept, {})[int(fy)] = val
                uc = unit_count.setdefault(concept, {})
                uc[unit] = uc.get(unit, 0) + 1
    if not by_concept:
        errs = "; ".join(sorted({str(f.get("extract_error")) for f in ordered if f.get("extract_error")}))
        return {"error": f"{ticker}: filing ESEF presenti ma nessun fact estraibile ({errs or 'tagging atipico'})"}

    out_items: Dict[str, Dict[int, Any]] = {}
    tags_used: Dict[str, str] = {}
    unit_used: Dict[str, str] = {}
    for canon, tags in _canonical_ifrs().items():
        merged: Dict[int, Any] = {}
        used: List[str] = []
        for tag in tags:  # il primo tag comanda, gli altri riempiono i buchi (come sec_xbrl)
            ser = by_concept.get(tag)
            if not ser:
                continue
            added = False
            for fy, val in ser.items():
                if fy not in merged:
                    merged[fy] = val
                    added = True
            if added:
                used.append(tag)
        if merged:
            yrs = sorted(merged.keys())[-years:]
            out_items[canon] = {y: merged[y] for y in yrs}
            tags_used[canon] = "ifrs-full:" + "+".join(used)
            _uc: Dict[str, int] = {}
            for t in used:
                for u, n in (unit_count.get(t) or {}).items():
                    _uc[u] = _uc.get(u, 0) + n
            unit_used[canon] = max(_uc, key=_uc.get) if _uc else ""
    if not out_items:
        return {"error": f"{ticker}: fact ESEF estratti ma nessuna voce canonica mappata "
                         "(tassonomia atipica): segnalare per estendere il mapping"}
    all_years = sorted({y for s in out_items.values() for y in s.keys()})[-years:]

    def g(item, y):
        v = out_items.get(item, {}).get(y)
        return float(v) if isinstance(v, (int, float)) else None

    # derivate: stessa semantica di sec_xbrl (il consumatore non distingue le fonti)
    derived: Dict[str, Dict[int, Any]] = {"gross_margin": {}, "operating_margin": {}, "net_margin": {},
                                          "capex_pct_revenue": {}, "payout_total": {}, "fcf": {}}
    for y in all_years:
        rev, gp, oi, ni = g("revenue", y), g("gross_profit", y), g("operating_income", y), g("net_income", y)
        cfo, capex = g("cfo", y), g("capex", y)
        div, bb = g("dividends_paid", y), g("buyback", y)
        if rev:
            if gp is not None: derived["gross_margin"][y] = round(gp / rev, 4)
            if oi is not None: derived["operating_margin"][y] = round(oi / rev, 4)
            if ni is not None: derived["net_margin"][y] = round(ni / rev, 4)
            if capex is not None: derived["capex_pct_revenue"][y] = round(abs(capex) / rev, 4)
        if ni and ni > 0:
            tot = (abs(div) if div else 0) + (abs(bb) if bb else 0)
            if tot: derived["payout_total"][y] = round(tot / ni, 3)
        if cfo is not None and capex is not None:
            derived["fcf"][y] = cfo - abs(capex)
    derived = {k: v for k, v in derived.items() if v}

    out = {"ticker": ticker.upper(), "lei": lei, "lei_note": lei_note,
           "years": all_years, "n_items": len(out_items),
           "items": out_items, "derived": derived,
           "tags_used": tags_used, "units": unit_used,
           # review 17/07 F7: nota DINAMICA — dice quanti esercizi ci sono davvero
           "coverage_note": ("ESEF copre dal FY2020-21 (non 10 anni): %d esercizi per "
                             "questo nome, ultimo sul repository FY%s"
                             % (len(all_years), all_years[-1])),
           # review 17/07 F1 finanza: base contabile ESPLICITA — i dual-reporter
           # (es. STM) pubblicano al mercato numeri US GAAP diversi dall'IFRS ESEF
           "accounting_basis": ("IFRS (bilancio ESEF): per i dual-reporter USA "
                                "puo' divergere dai numeri US GAAP di mercato"),
           "_source": "ESEF filings.xbrl.org (annual financial reports, xBRL-JSON)",
           "_timestamp": datetime.now().isoformat(timespec="seconds")}
    if gaps:
        out["gaps"] = gaps
    if index_note:
        out["index_note"] = index_note
    _mixed = sorted(c for c, t in tags_used.items() if "+" in t)
    if _mixed:
        out["mixed_series"] = _mixed  # perimetri di tag diversi tra anni: dichiarato
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if not sys.argv[1:]:
        print("uso: python esef.py <TICKER> [...]", file=sys.stderr)
        sys.exit(2)
    for t in sys.argv[1:]:
        r = get_esef_history(t)
        if r.get("error"):
            print(t, "ERRORE:", r["error"])
            continue
        print(f"\n=== {t} | LEI {r['lei']} | anni {r['years']} | voci {r['n_items']}")
        for k in ("revenue", "operating_income", "net_income", "equity", "cfo", "capex",
                  "interest_revenue", "loans_to_customers"):
            if k in r["items"]:
                print(f"  {k:22s}", {y: f"{v/1e6:,.0f}M" for y, v in r["items"][k].items()},
                      r["units"].get(k, ""))
