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
import re
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:  # standalone (python esef.py <TICKER>): il .env lo carica config.py, qui non passa
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

BASE = "https://filings.xbrl.org/api"
class EntitaEsefAssente(LookupError):
    """filings.xbrl.org risponde 404 sull'entita': LEI assente dal repository o endpoint cambiato.
    Revisione 04/10 (R8): prima diventava un elenco vuoto, letto come «nessun deposito» e,
    nello storico DCF, come indice aggiornato. Chi la riceve la dichiara."""


class ContattoMancante(RuntimeError):
    """Storica (02/09): `_headers` non la solleva piu' dal 05/10 (ESEF senza contatto SEC).
    Resta per i chiamanti che la intercettano ancora."""


# User-Agent onesto del progetto per le fonti UE non-SEC (filings.xbrl.org, GLEIF) quando
# SEC_CONTACT_EMAIL manca: nessun dato personale, nessun contatto finto.
UA_GENERICO = "Bellomberg/1.0 (ricerca personale open source, basso volume)"


def _headers() -> dict:
    """Header per filings.xbrl.org, costruiti a ogni chiamata.
    Decisione PM 05/10 sera (Opus 5.5): l'ESEF si attiva SEMPRE. Con SEC_CONTACT_EMAIL il
    contatto resta nello User-Agent (comportamento invariato); senza, UA_GENERICO: la mail
    la esige solo la SEC (sec_edgar._headers), dove la mancanza resta dichiarata.
    02/09 (pubblicazione B2): prima c'era l'email del PM cablata."""
    c = (os.environ.get("SEC_CONTACT_EMAIL") or "").strip()
    if not c:
        return {"User-Agent": UA_GENERICO}
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


_SPA_ESTESA = re.compile(r"societ(?:a'?|à)\s+per\s+azioni", re.I)
# Grafie della stessa forma giuridica (e solo quelle): «S.p.A.» = «SPA» = «Societa' per azioni».
_FORME_GIURIDICHE = [(re.compile(p, re.I), f) for p, f in (
    (r"\bsociet(?:a'?|à)\s+per\s+azioni\b", " spa "), (r"\bs\.?\s?p\.?\s?a\b\.?", " spa "),
    (r"\baktiengesellschaft\b", " ag "), (r"\bn\.\s?v\.", " nv "), (r"\bs\.\s?a\.", " sa "),
    (r"\bs\.\s?e\.", " se "), (r"\bp\.\s?l\.\s?c\.", " plc "), (r"\ba/s\b", " as "),
    (r"\bs\.\s?r\.\s?l\.", " srl "))]


def _norm_esatto(x):
    """Nome per il collegamento automatico: si uniformano SOLO le grafie della forma giuridica;
    «holding», «group», «the» restano (una holding e la sua controllata quotata hanno LEI diversi)."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode().lower()
    t = t.replace("’", "'")
    for regola, forma in _FORME_GIURIDICHE:
        t = regola.sub(forma, t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def _norm_nome(x):
    # Il repository registra anche la forma estesa («… - SOCIETA' PER AZIONI»), da
    # normalizzare come «S.p.A.» (prova reale fase B).
    x = _SPA_ESTESA.sub("S.p.A.", str(x or ""))
    try:
        from bellomberg.valuation.peer_comps import _norm_issuer
        return _norm_issuer(x)
    except Exception:
        return x.lower().strip()


_PAGINA_ENTITA = 10


def _cerca_entita(name, ritmo=None):
    """Ricerca progressiva per nome su /api/entities: (entita', query, usata, errore).

    Audit 11/09 (Fable 5.1, run 10/09 memo #54): la sola query sulle prime tre parole del
    nome Yahoo ("Beispiel AG") tornava 0 entita' mentre il repository registra la forma
    giuridica lunga ("Beispiel Aktiengesellschaft"). Ricerca PROGRESSIVA: nome pieno, poi
    il nome SENZA forma giuridica (la stessa normalizzazione del confronto esatto).
    """
    q_pieno = " ".join(str(name).split()[:3])
    q_core = " ".join(_norm_nome(name).split()[:2])
    query = []
    for q in (q_pieno, q_core):
        if q and q.lower() not in [x.lower() for x in query]:
            query.append(q)
    ritmo = attendi_esef if ritmo is None else ritmo  # REV_G2a R-3: mai una richiesta fuori ritmo
    try:
        import requests
        for q in query:
            ritmo()
            r = requests.get(BASE + "/entities", params={
                "page[size]": _PAGINA_ENTITA,
                "filter": json.dumps([{"name": "name", "op": "ilike", "val": f"%{q}%"}]),
            }, headers=_headers(), timeout=30)
            if not r.ok:
                return [], query, None, f"ricerca entita' ESEF fallita (HTTP {r.status_code})"
            ents = r.json().get("data", [])
            if ents:
                return ents, query, q, None
    except ContattoMancante as e:
        return [], query, None, str(e)   # review 02/09: il nome della classe non dice cosa fare
    except Exception as e:
        return [], query, None, f"ricerca entita' ESEF fallita ({type(e).__name__})"
    return [], query, None, None


def _lei_entita(e):
    a = e.get("attributes") or {}
    return str(a.get("identifier") or e.get("id") or ""), str(a.get("name") or "")


# Paesi con obbligo ESEF (SEE) piu' il Regno Unito: un LEI fuori da qui non porta pacchetti ESEF.
# Scritti in minuscolo e portati in maiuscolo a runtime: il cancello privacy dell'export pubblico
# cerca i ticker corti (sotto 4 lettere) in modo case-sensitive, e un codice paese di due lettere
# maiuscole puo' coincidere con la base di un ticker vero. Il valore a runtime non cambia.
_PAESI_ESEF = {p.upper() for p in (
    "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de", "gr", "hu", "ie", "it", "lv",
    "lt", "lu", "mt", "nl", "pl", "pt", "ro", "sk", "si", "es", "se", "is", "li", "no", "gb")}


def candidati_gleif(nome, *, cerca=None):
    """Candidati LEI da GLEIF (fase F) per gli emittenti assenti da filings.xbrl.org: solo entita'
    attive di paesi ESEF con nome identico (stessa normalizzazione del repository). Univoco solo
    con un nome identico e la pagina non piena; il LEI si riverifica poi sui fatti del pacchetto."""
    if not nome:
        return {"stato": "nessuno", "candidati": [], "motivo": "nome dell'emittente non disponibile"}
    try:
        from bellomberg.market_data import esef_sito  # GLEIF: altro servizio, altro modulo
        record = (cerca or esef_sito.gleif_lei_records)(nome)
    except Exception as exc:
        return {"stato": "errore", "candidati": [], "motivo": f"GLEIF non raggiungibile ({type(exc).__name__})"}
    target, esatti = _norm_esatto(nome), {}
    for rec in record:
        a = (rec or {}).get("attributes") or {}
        ent = a.get("entity") or {}
        lei = str(a.get("lei") or rec.get("id") or "").upper()
        legale = str((ent.get("legalName") or {}).get("name") or "")
        paese = str((ent.get("legalAddress") or {}).get("country") or "").upper()
        if (lei and ent.get("status") == "ACTIVE" and paese in _PAESI_ESEF
                and _norm_esatto(legale) == target):
            esatti.setdefault(lei, legale)
    candidati = [{"lei": l, "nome": n, "origine": "gleif"} for l, n in esatti.items()]
    if len(candidati) == 1 and len(record) < 20:  # pagina non piena (GLEIF_PAGINA)
        return {"stato": "univoco", "candidati": candidati, "motivo": "nome identico su un solo LEI attivo (GLEIF)"}
    if candidati:
        return {"stato": "ambiguo", "candidati": candidati, "motivo": "LEI da GLEIF: conferma necessaria"}
    return {"stato": "nessuno", "candidati": [], "motivo": "nessun LEI attivo con nome identico su GLEIF"}


def candidati_lei(ticker, nome=None, *, ritmo=None):
    """Candidati LEI per l'attivazione automatica: {"stato", "candidati", "motivo"}.

    Piu' severo di resolve_lei (che serve lo storico DCF): univoco solo dal negozio
    privato o con UN solo LEI a nome normalizzato identico; un risultato unico ma non
    identico resta una proposta da confermare. Negozio illeggibile = errore (potrebbe
    contenere un LEI diverso da quello trovato per nome).
    """
    from bellomberg.storage.negozi_privati import carica_lei
    tk = (ticker or "").upper().strip()
    negozio = carica_lei()
    if tk in negozio["lei"]:
        return {"stato": "univoco", "motivo": "LEI dal negozio privato lei_emittenti",
                "candidati": [{"lei": negozio["lei"][tk], "nome": nome or tk, "origine": "negozio"}]}
    if negozio["origine"] == "illeggibile":
        return {"stato": "errore", "candidati": [],
                "motivo": f"negozio lei_emittenti illeggibile ({negozio['motivo']}): correggerlo prima del collegamento"}
    if not nome:
        return {"stato": "nessuno", "candidati": [], "motivo": "nome dell'emittente non disponibile"}
    ents, query, _, errore = _cerca_entita(nome, ritmo=attendi_esef if ritmo is None else ritmo)
    if errore:
        return {"stato": "errore", "candidati": [], "motivo": errore}
    unici = {}
    for e in ents:
        lei, enome = _lei_entita(e)
        if lei and lei not in unici:
            unici[lei] = enome
    target = _norm_esatto(nome)
    esatti = [(lei, n) for lei, n in unici.items() if _norm_esatto(n) == target]
    if len(esatti) == 1 and len(ents) >= _PAGINA_ENTITA:
        # Pagina piena: un omonimo identico puo' stare oltre la prima pagina.
        return {"stato": "ambiguo", "motivo": "risultati oltre la prima pagina: conferma necessaria",
                "candidati": [{"lei": esatti[0][0], "nome": esatti[0][1], "origine": "nome"}]}
    if len(esatti) == 1:
        return {"stato": "univoco", "motivo": "nome identico su un solo emittente ESEF",
                "candidati": [{"lei": esatti[0][0], "nome": esatti[0][1], "origine": "nome"}]}
    if esatti:
        return {"stato": "ambiguo", "motivo": f"{len(esatti)} emittenti ESEF con lo stesso nome",
                "candidati": [{"lei": l, "nome": n, "origine": "nome"} for l, n in esatti]}
    if not unici:
        motivo = "nessuna entita' sul repository ESEF (cercato: " + ", ".join(query) + ")"
        # Fase F: emittente assente dal repository -> LEI da GLEIF, pacchetti dal suo sito.
        g = candidati_gleif(nome)
        if g["stato"] in ("univoco", "ambiguo"):
            return {**g, "motivo": motivo + "; " + g["motivo"]}
        return {"stato": "nessuno", "candidati": [], "motivo": motivo + "; " + g["motivo"]}
    if len(unici) > 5:
        return {"stato": "nessuno", "candidati": [],
                "motivo": f"{len(unici)} entita' ESEF simili, nessuna con nome identico: serve il LEI"}
    return {"stato": "ambiguo", "motivo": "nome simile: conferma necessaria",
            "candidati": [{"lei": l, "nome": n, "origine": "nome_simile"} for l, n in unici.items()]}


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
    # Ricerca progressiva (audit 11/09): vedi _cerca_entita. La guardia di ambiguita' resta.
    ents, query, usata, errore = _cerca_entita(name, ritmo=attendi_esef)  # revisione 04/10: ritmo anche qui
    if errore:
        return None, errore
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


# ============================================================
# FILING AUTOMATICI (fase B): ritmo prudente e indice con cache
# ============================================================
_ESEF_LOCK = threading.Lock()
_ESEF_ULTIMA = [0.0]
_ESEF_INTERVALLO_S = 1.0  # nessun limite pubblicato da filings.xbrl.org: una richiesta al secondo
INDICE_TTL_S = 6 * 3600
_LEI = re.compile(r"[A-Z0-9]{20}")


def _ritmo_path_predefinito():
    """File del ritmo condiviso; DATA_DIR letto a chiamata (i test lo reindirizzano)."""
    from bellomberg.core import paths
    return Path(paths.DATA_DIR) / ".esef_ritmo"


_ritmo_path = _ritmo_path_predefinito


def attendi_esef(*, percorso=None) -> None:
    """Ritmo condiviso tra processi (lock su file) per i percorsi filing automatici."""
    from bellomberg.market_data.sec_edgar import _attendi
    _attendi(Path(percorso) if percorso else _ritmo_path(), _ESEF_INTERVALLO_S, _ESEF_LOCK, _ESEF_ULTIMA)


def _cache_indice_dir():
    from bellomberg.core import paths
    return Path(paths.DATA_DIR) / "esef_cache"


def indice_depositi(lei: str, *, ttl_s: int = INDICE_TTL_S, cache_dir=None, fetch=None) -> Dict[str, Any]:
    """Depositi ESEF di un LEI: {"righe", "origine", "motivo"} con cache su disco.

    Cache fresca e leggibile: nessuna rete. Cache corrotta: si rilegge subito.
    Rete giu' con cache vecchia: si usa e lo si DICHIARA (origine cache_scaduta).
    Senza rete e senza cache: RuntimeError, mai un elenco vuoto spacciato per vero.
    """
    if not isinstance(lei, str) or not _LEI.fullmatch(lei):
        raise ValueError("LEI non valido")
    fetch = fetch or (lambda x: _list_filings(x, ritmo=attendi_esef))
    p = Path(cache_dir or _cache_indice_dir()) / f"indice_{lei}.json"
    cache, motivo_cache = None, None
    try:
        grezzo = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(grezzo, dict) and isinstance(grezzo.get("righe"), list):
            cache, motivo_cache = grezzo["righe"], grezzo.get("motivo")
    except (OSError, ValueError):
        cache = None
    if cache is not None and time.time() - p.stat().st_mtime < ttl_s:
        return {"righe": cache, "origine": "cache", "motivo": motivo_cache}
    motivo = None
    try:
        try:
            righe = fetch(lei)
        except EntitaEsefAssente as exc:
            if cache:
                raise
            # Nessuna copia buona: zero righe, ma col 404 DICHIARATO (resta anche in cache).
            righe, motivo = [], str(exc)
        if not righe and cache:
            # 404 o elenco vuoto per un LEI che aveva depositi: probabile guasto del repository,
            # mai cancellare la copia buona (revisione finale)
            return {"righe": cache, "origine": "cache_scaduta",
                    "motivo": "indice ESEF vuoto per un emittente con depositi: uso la copia precedente"}
    except Exception as exc:
        if cache is None:
            raise RuntimeError(f"indice ESEF non disponibile per {lei}: {type(exc).__name__}: {exc}") from exc
        return {"righe": cache, "origine": "cache_scaduta",
                "motivo": f"indice ESEF non aggiornato ({type(exc).__name__}: {exc}): uso la copia precedente"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"lei": lei, "righe": righe, "motivo": motivo}), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass  # cache best-effort
    return {"righe": righe, "origine": "rete", "motivo": motivo}


def _list_filings(lei: str, max_pages: int = 20, ritmo=None) -> List[Dict[str, Any]]:
    import requests
    from urllib.parse import urljoin, urlsplit, quote
    if max_pages < 1:
        raise ValueError("max_pages deve essere positivo")
    ritmo = attendi_esef if ritmo is None else ritmo  # REV_G2a R-3: chi non lo passa resta nel ritmo
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
        ritmo()
        r = requests.get(url, params={"page[size]": 50} if len(visti) == 1 else None,
                         headers=_headers(), timeout=60)
        if getattr(r, "status_code", 200) == 404 and len(visti) == 1:
            # LEI senza entita' sul repository (prova reale fase F: LEI da GLEIF) o endpoint cambiato:
            # non si distinguono dal 404, quindi si DICHIARA (revisione 04/10), mai un elenco vuoto muto.
            raise EntitaEsefAssente(f"filings.xbrl.org: HTTP 404 per l'entita' {lei} (LEI assente dal "
                                    "repository o endpoint cambiato): nessun elenco depositi")
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
        attendi_esef()  # revisione 04/10: anche lo storico DCF passa dal ritmo condiviso
        h = requests.head(json_url, headers=_headers(), timeout=30, allow_redirects=True)
        size = int(h.headers.get("Content-Length") or 0)
        if size > MAX_JSON_MB * 1e6:
            return {}, f"raw {size/1e6:.0f}MB > guardia {MAX_JSON_MB}MB: filing saltato (dichiarato)"
    except Exception:
        pass  # HEAD best-effort: si tenta comunque il GET
    try:
        attendi_esef()
        raw = requests.get(json_url, headers=_headers(), timeout=300).json()
    except ContattoMancante as e:
        return {}, str(e)
    except Exception as e:
        return {}, f"download/parse fallito ({type(e).__name__})"
    return _fatti_da_raw(raw)[0], None


def _fatti_da_raw(raw: Dict[str, Any], lei: Optional[str] = None) -> Tuple[Dict[str, List[List[Any]]], int]:
    """({concept: [[fy, valore, unita'], ...]}, fatti di altre entita') da un xBRL-JSON gia' in memoria.
    Solo fatti ifrs-full numerici senza assi extra; con `lei`, solo quelli dell'entita' <schema>:<lei>."""
    out: Dict[str, List[List[Any]]] = {}
    altre = 0
    for fact in (raw.get("facts") or {}).values():
        dims = fact.get("dimensions") or {}
        if set(dims.keys()) - _DIM_KEYS_BASE:
            continue  # breakdown dimensionale, non il totale
        if lei and str(dims.get("entity") or "").rpartition(":")[2].upper() != lei.upper():
            altre += 1
            continue
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
    return out, altre


# STORICO DAL SITO (R-FONTI 10/10, Opus 5.5): con il repository filings.xbrl.org fermo (prova reale su un
# emittente italiano del book: fermo al FY2024 il 10/10/2026 mentre il pacchetto ESEF FY2025 e' sul sito
# dell'emittente), lo storico prende
# gli esercizi mancanti dal pacchetto ufficiale trovato sul sito: STESSO percorso della pipeline filing
# (esef_sito.righe_da_cache = cache dell'ultima esplorazione, nessuna esplorazione qui; scarica_pacchetto_json
# = download con tetto, solo l'host del pacchetto, conversione ixbrl_oim in locale). Fatti solo dell'entita'
# con quel LEI. Origine dichiarata per esercizio; nessun pacchetto in cache = buco dichiarato.
ORIGINE_SITO = "pacchetto ESEF ufficiale dal sito dell'emittente, convertito in locale"


def _depositi_dal_sito(lei: str, filings: Dict[str, Any], cache: Dict[str, Any], *, oggi=None,
                       righe_fn=None, scarica_fn=None) -> Dict[str, Any]:
    """{"stato": non_necessario | nessun_pacchetto | ok | parziale | errore, "motivo", "filings": {id: voce}}.

    Riserva MEDIO-4 v2: solo pacchetti dell'ESERCIZIO ANNUALE. La chiusura nel nome deve cadere a 12, 24 o 36
    mesi dall'ultimo esercizio del repository (+/- GIORNI_TOLLERANZA_CHIUSURA: un 30/06 dopo un 31/12 e' una
    semestrale, esclusa e dichiarata) e il pacchetto deve contenere almeno un fatto di DURATA annuale
    dell'entita' che finisce a quella chiusura (nome 2025 ma fatti 2024: rifiutato e dichiarato)."""
    from datetime import date
    from urllib.parse import urlsplit
    from bellomberg.market_data import esef_sito
    oggi = oggi or date.today()
    con_fatti = [str(f.get("period_end"))[:10] for f in filings.values() if f.get("facts")]
    ultimo = max(con_fatti, default=None)
    try:
        righe = (righe_fn or esef_sito.righe_da_cache)(lei)
    except Exception as e:
        righe, errore_righe = [], f"cache dei pacchetti del sito illeggibile ({type(e).__name__})"
    else:
        errore_righe = None
    candidate = [r for r in righe or [] if r.get("period_end") and r.get("json_url")
                 and (not ultimo or str(r["period_end"])[:10] > ultimo)]
    nuove, esclusi = [], []
    for r in candidate:
        perche = _chiusura_non_annuale(ultimo, str(r["period_end"])[:10])
        if perche:
            esclusi.append(f"pacchetto con chiusura {str(r['period_end'])[:10]} escluso ({perche})")
        else:
            nuove.append(r)
    atteso = esef_sito.atteso_piu_recente(ultimo, oggi)
    if not nuove:
        if not atteso and not esclusi:
            return {"stato": "non_necessario", "motivo": None, "filings": {}}
        if not atteso:
            return {"stato": "non_necessario", "motivo": "; ".join(esclusi), "filings": {}}
        return {"stato": "nessun_pacchetto", "filings": {},
                "motivo": (f"repository fermo all'esercizio FY{str(ultimo)[:4]}" if ultimo else "repository senza esercizi")
                          + " e nessun pacchetto ESEF annuale piu' recente dal sito dell'emittente nella cache "
                          "dell'esplorazione"
                          + (f" ({errore_righe})" if errore_righe else
                             f" ({'; '.join(esclusi)})" if esclusi else
                             " (esplorazione del sito non eseguita o senza pacchetti)")}
    archivio = Path(CACHE_DIR) / f"esef_sito_{lei}"
    estratti = cache.setdefault("sito", {})
    out, motivi = {}, list(esclusi)
    for r in sorted(nuove, key=lambda x: x["period_end"]):
        url = r["json_url"]
        host = (urlsplit(url).hostname or "").lower()
        fine = str(r["period_end"])[:10]
        try:
            pacchetto = (scarica_fn or esef_sito.scarica_pacchetto_json)(url, archivio, {host})
            voce = estratti.get(url)
            if not (isinstance(voce, dict) and voce.get("sha256") == pacchetto["sha256"] and voce.get("facts")
                    and voce.get("chiusura_verificata") == fine):
                raw = json.loads(Path(pacchetto["path"]).read_bytes().decode("utf-8-sig"))
                facts, altre = _fatti_da_raw(raw, lei)
                if not facts:
                    raise ValueError(f"nessun fatto ifrs-full dell'entita' LEI {lei} nel pacchetto "
                                     f"({altre} fatti di altre entita')")
                fini = _fini_durate_annuali(raw, lei)
                if fine not in fini:
                    raise ValueError(f"nessun fatto di durata annuale dell'entita' che chiude il {fine} (nome del "
                                     "pacchetto); esercizi annuali nel pacchetto: "
                                     f"{', '.join(sorted(fini)) or 'nessuno'} - periodo del pacchetto diverso dal nome")
                voce = {"sha256": pacchetto["sha256"], "pacchetto_sha256": pacchetto.get("pacchetto_sha256"),
                        "facts": facts, "altre_entita": altre, "chiusura_verificata": fine,
                        "extracted_at": datetime.now().isoformat(timespec="seconds")}
                estratti[url] = voce
            out[url] = {"period_end": r["period_end"], "date_added": None, "facts": voce["facts"],
                        "origine": "sito", "url": url, "sha256": voce["sha256"],
                        "pacchetto_sha256": voce.get("pacchetto_sha256"), "extracted_at": voce.get("extracted_at")}
        except Exception as e:
            motivi.append(f"FY{fine[:4]} dal sito ({host}): {type(e).__name__}: {str(e)[:240]}")
    stato = "ok" if out and not motivi else "parziale" if out else "errore"
    return {"stato": stato, "motivo": "; ".join(motivi) or None, "filings": out}


GIORNI_TOLLERANZA_CHIUSURA = 20  # chiusure a 52/53 settimane e fine mese spostata: +/- 20 giorni


def _chiusura_non_annuale(ultimo: Optional[str], fine: str) -> Optional[str]:
    """Motivo se `fine` NON e' la chiusura attesa di un esercizio dopo `ultimo` (12/24/36 mesi), None se lo e'.
    Senza un ultimo esercizio noto decide solo il controllo dei fatti nel pacchetto."""
    if not ultimo:
        return None
    try:
        a, b = datetime.fromisoformat(ultimo[:10]), datetime.fromisoformat(fine[:10])
    except ValueError:
        return f"data di chiusura non leggibile ({fine})"
    giorni = (b - a).days
    for anni in (1, 2, 3):
        if abs(giorni - 365.25 * anni) <= GIORNI_TOLLERANZA_CHIUSURA:
            return None
    return (f"chiusura a {giorni} giorni dall'ultimo esercizio del repository ({ultimo[:10]}): non e' l'esercizio "
            "annuale successivo, probabile relazione semestrale o infrannuale")


def _fini_durate_annuali(raw: Dict[str, Any], lei: str) -> set:
    """Chiusure (AAAA-MM-GG) dei fatti ifrs-full dell'entita' `lei` con durata annuale (300-400 giorni), senza
    assi. Convenzione xBRL-JSON: la fine «2026-01-01T00:00:00» e' la fine giornata del 31/12."""
    fini = set()
    for fact in (raw.get("facts") or {}).values():
        dims = fact.get("dimensions") or {}
        if set(dims.keys()) - _DIM_KEYS_BASE or "/" not in str(dims.get("period") or ""):
            continue
        if str(dims.get("entity") or "").rpartition(":")[2].upper() != lei.upper():
            continue
        if not str(dims.get("concept") or "").startswith("ifrs-full:"):
            continue
        try:
            st_s, en_s = str(dims["period"]).split("/", 1)
            st, en = datetime.fromisoformat(st_s.replace("Z", "")), datetime.fromisoformat(en_s.replace("Z", ""))
        except ValueError:
            continue
        if 300 <= (en - st).days <= 400:
            fine = en - timedelta(days=1) if (en.hour, en.minute, en.second) == (0, 0, 0) and "T" in en_s else en
            fini.add(fine.date().isoformat())
    return fini


def _etichetta_deposito(f: Dict[str, Any]) -> str:
    anno = str(f.get("period_end") or "?")[:4]
    return f"FY{anno} (" + ("sito dell'emittente" if f.get("origine") == "sito" else "filings.xbrl.org") + ")"


def _refresh_entity_cache(lei: str) -> Dict[str, Any]:
    """Indice filing + estrazioni nuove in cache; i filing gia' estratti non si riscaricano."""
    cache = _load_cache(lei)
    filings = cache.get("filings") or {}
    idx_age = time.time() - float(cache.get("index_fetched_at") or 0)
    known_err = cache.get("index_error")
    if idx_age < INDEX_TTL_S and filings and not known_err:
        return cache
    try:
        listed = _list_filings(lei, ritmo=attendi_esef)
        cache["index_fetched_at"] = time.time()
        cache.pop("index_error", None)
    except (ContattoMancante, EntitaEsefAssente) as e:
        cache["index_error"] = str(e)  # 404: storico tenuto ma dichiarato non aggiornato (revisione 04/10)
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
    # R-FONTI 10/10: esercizi mancanti dal pacchetto ufficiale sul sito dell'emittente (percorso della pipeline)
    sito = _depositi_dal_sito(lei, filings, cache)
    if sito["filings"]:
        _save_cache(lei, cache)
    if not filings and not sito["filings"]:
        return {"error": f"{ticker}: nessun filing ESEF per LEI {lei} "
                         f"({cache.get('index_error') or 'repository vuoto per questa entita'''})"
                         + (f"; sito dell'emittente: {sito['motivo']}" if sito.get("motivo") else "")}
    # fusione per concetto: filing in ordine cronologico, il PIU' RECENTE sovrascrive
    # (restatement/comparativo aggiornato vince sul deposito originale) e, dal 10/10, lo DICHIARA
    ordered = sorted(list(filings.values()) + list(sito["filings"].values()),
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
    if sito["stato"] in ("nessun_pacchetto", "errore", "parziale") and sito.get("motivo"):
        gaps.append(f"esercizi dopo il repository: {sito['motivo']}")
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
    voci_di: Dict[str, List[str]] = {}
    for canon, tags in _canonical_ifrs().items():
        for t in tags:
            voci_di.setdefault(t, []).append(canon)
    da_deposito: Dict[str, Dict[int, str]] = {}
    rideterminazioni: List[Dict[str, Any]] = []
    for f in ordered:
        etichetta = _etichetta_deposito(f)
        for concept, rows in (f.get("facts") or {}).items():
            for fy, val, unit in rows:
                serie = by_concept.setdefault(concept, {})
                prec, fonte_prec = serie.get(int(fy)), da_deposito.get(concept, {}).get(int(fy))
                if (concept in voci_di and fonte_prec and fonte_prec != etichetta
                        and isinstance(prec, (int, float)) and isinstance(val, (int, float))
                        and abs(prec - val) > 1e-9 * max(abs(prec), abs(val), 1.0)):
                    # R-FONTI 10/10: comparativo diverso nel deposito piu' recente: vale il suo, DICHIARATO
                    rideterminazioni.append({
                        "voci": voci_di[concept], "concetto": "ifrs-full:" + concept, "anno": int(fy),
                        "valore_precedente": prec, "deposito_precedente": fonte_prec,
                        "valore_usato": val, "deposito_usato": etichetta,
                        "nota": (f"comparativo rideterminato nel deposito {etichetta.split(' ')[0]}"
                                 if str(f.get("period_end") or "")[:4] > str(fy)
                                 else f"valore aggiornato dal deposito {etichetta}")})
                serie[int(fy)] = val
                da_deposito.setdefault(concept, {})[int(fy)] = etichetta
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

    anni_sito = sorted("FY" + str(f.get("period_end"))[:4] for f in sito["filings"].values())
    out = {"ticker": ticker.upper(), "lei": lei, "lei_note": lei_note,
           "years": all_years, "n_items": len(out_items),
           "items": out_items, "derived": derived,
           "tags_used": tags_used, "units": unit_used,
           # review 17/07 F7: nota DINAMICA — dice quanti esercizi ci sono davvero
           "coverage_note": ("ESEF copre dal FY2020-21 (non 10 anni): %d esercizi per "
                             "questo nome, ultimo sul repository FY%s"
                             % (len(all_years), all_years[-1])) if not anni_sito else (
                             "ESEF copre dal FY2020-21 (non 10 anni): %d esercizi per questo nome, ultimo FY%s; "
                             "%s dal sito dell'emittente (repository filings.xbrl.org fermo)"
                             % (len(all_years), all_years[-1], ", ".join(anni_sito))),
           # review 17/07 F1 finanza: base contabile ESPLICITA — i dual-reporter
           # (es. STM) pubblicano al mercato numeri US GAAP diversi dall'IFRS ESEF
           "accounting_basis": ("IFRS (bilancio ESEF): per i dual-reporter USA "
                                "puo' divergere dai numeri US GAAP di mercato"),
           "_source": "ESEF filings.xbrl.org (annual financial reports, xBRL-JSON)" + (
               " + " + ORIGINE_SITO + " per " + ", ".join(anni_sito) if anni_sito else ""),
           # R-FONTI 10/10: esito del ripiego sul sito dell'emittente (anche quando non serve o manca)
           "sito_emittente": {"stato": sito["stato"], "motivo": sito.get("motivo"), "origine": ORIGINE_SITO,
                              "depositi": [{k: f.get(k) for k in ("period_end", "url", "sha256", "pacchetto_sha256",
                                                                  "extracted_at")}
                                           for f in sorted(sito["filings"].values(), key=lambda x: x["period_end"])]},
           "_timestamp": datetime.now().isoformat(timespec="seconds")}
    if gaps:
        out["gaps"] = gaps
    if rideterminazioni:
        out["rideterminazioni"] = rideterminazioni  # anno sovrapposto: vale il deposito piu' recente, dichiarato
        # v2 (riserva MEDIO-2, stessa regola dello storico SEC): l'anno prima del primo rideterminato resta sulla
        # base del deposito originale; la variazione fra i due NON e' omogenea e si dichiara per voce
        discontinuita = {}
        for canon in sorted({v for x in rideterminazioni for v in x["voci"]}):
            anni = sorted({x["anno"] for x in rideterminazioni if canon in x["voci"]
                           and out_items.get(canon, {}).get(x["anno"]) == x["valore_usato"]})
            if anni and anni[0] - 1 in out_items.get(canon, {}):
                discontinuita[canon] = {
                    "tra": [anni[0] - 1, anni[0]],
                    "motivo": (f"FY{'/'.join(str(a) for a in anni)} rideterminato nel deposito piu' recente; "
                               f"FY{anni[0] - 1} e precedenti restano sulla base del deposito originale: variazione "
                               f"FY{anni[0] - 1}->FY{anni[0]} non omogenea")}
        if discontinuita:
            out["discontinuita"] = discontinuita
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
