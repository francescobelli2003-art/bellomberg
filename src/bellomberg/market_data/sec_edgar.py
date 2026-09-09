"""
BELLOMBERG - SEC EDGAR Filings & Insider Trading

API completamente gratuite, no key richiesta. Solo User-Agent obbligatorio.
Docs: https://www.sec.gov/edgar/sec-api-documentation

USA QUESTI ENDPOINT:
1. CIK lookup        : https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=...
2. Recent filings    : https://data.sec.gov/submissions/CIK{cik}.json
3. Form 4 (insider)  : https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=...&type=4
4. 13F-HR (holdings) : per institutional investor (Pershing CIK=1336528, Berkshire CIK=1067983, etc.)

LIMITS: 10 req/sec, fair use. Custom UA obbligatorio.

API:
    get_recent_filings(ticker, form_types=["8-K", "4"], days=7) -> list
    get_insider_trades(ticker, days=30) -> list[dict] con buy/sell/value
    get_13f_holdings(investor_name) -> list[dict] con positions + valore
    get_8k_events(ticker, days=30) -> list[dict] eventi materiali
"""
from bellomberg.core.paths import PROJECT_ROOT
import os
import time
import re
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from xml.etree import ElementTree as ET

try:  # standalone (python sec_edgar.py, probe): il .env lo carica config.py, qui non passa
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


class ContattoMancante(RuntimeError):
    """SEC_CONTACT_EMAIL assente: la SEC esige un contatto nello User-Agent."""


def _contatto() -> str:
    return (os.environ.get("SEC_CONTACT_EMAIL") or "").strip()


def _headers() -> dict:
    """Header per la SEC, costruiti A OGNI CHIAMATA (cosi' .env e monkeypatch
    valgono senza reload). La fair-access policy SEC vuole «Nome App
    contatto@email»: senza SEC_CONTACT_EMAIL nel .env NON si chiama la SEC con
    un contatto finto — errore dichiarato (regola 14/07); ogni chiamante sta
    in un try/except Exception che lo mette nel motivo/payload.
    02/09 (pubblicazione B2): prima qui c'era l'email del PM come default."""
    c = _contatto()
    if not c:
        raise ContattoMancante(
            "SEC_CONTACT_EMAIL assente nel .env: la SEC richiede un contatto "
            "nello User-Agent (aggiungi SEC_CONTACT_EMAIL=tua@email al .env; "
            "la SEC la usa solo per contattarti in caso di abuso: qualsiasi email valida)")
    return {"User-Agent": f"Bellomberg Personal Terminal {c}",
            "Accept": "application/json"}

# I CIK dei filer 13F seguiti (slug -> CIK) vivono nel negozio privato delle istituzioni
# (negozi_privati.carica_istituzioni: data/istituzioni.json, forma in istituzioni.example.json),
# lo stesso di agent_tools e cef_lookthrough: un solo posto che dice chi si segue.
def istituzioni_13f() -> Dict[str, Any]:
    """{"cik": {slug: CIK}, "origine", "motivo"}: riletto a ogni chiamata; negozio assente o
    illeggibile = mappa VUOTA con origine e motivo, che chi la usa DICHIARA (mai un ripiego)."""
    from bellomberg.storage.negozi_privati import carica_istituzioni
    r = carica_istituzioni()
    return {"cik": r["istituzioni"]["cik"], "origine": r["origine"], "motivo": r["motivo"]}


def _log(msg: str):
    # pipe stdout morta (backend zombie, autopsia (40)): log perso, mai eccezione.
    # Blindato il 22/08 (voce E): il ramo «non e' un filer» di lookup_cik prima
    # non loggava affatto, ora si'; con la pipe morta l'OSError finiva nell'
    # `except Exception` che richiama `_perche`, e la seconda OSError USCIVA
    # dalla funzione — `get_recent_filings` chiama lookup_cik FUORI dal proprio
    # try, quindi sarebbe morto l'intero blocco SEC invece del singolo ticker.
    try:
        print(f"[SEC] {msg}", flush=True)
    except OSError:
        pass


# ============================================================
# CIK LOOKUP (cached)
# ============================================================
_CIK_CACHE: Dict[str, str] = {}


# review ESEF 17/07 (ALTA): stesso emittente, ticker diverso tra listino e SEC: lo strip del
# suffisso da' una base che NON esiste nel file SEC (l'ADR ha un altro simbolo) e senza alias lo
# storico cadeva sull'ESEF IFRS mentre il mercato/yfinance parlavano US GAAP (sull'emittente
# misurato: utile FY2025 IFRS +80% rispetto all'US GAAP). Gli alias VERIFICATI vivono nel negozio
# privato degli alias (negozi_privati.carica_alias, sezione sec: BASE di listino -> ticker SEC).
def _alias_sec() -> Dict[str, str]:
    from bellomberg.storage.negozi_privati import carica_alias
    return carica_alias()["alias"]["sec"]


def ticker_ambiguo_per_cik(ticker: str) -> Optional[str]:
    """Il motivo per cui NON si puo' risolvere il CIK di questo ticker senza
    rischiare di agganciare un OMONIMO americano — oppure None se e' sicuro.

    `lookup_cik` cerca su `ticker.upper().split(".")[0]`, cioe' butta il
    suffisso di listino e confronta il resto con un file di ticker **US**:
    `BA.L` (BAE Systems) diventa `BA` e trova **Boeing**, `TRN.MI` (Terna)
    trova Trinity Industries. Il repo l'aveva gia' misurato il 17/07
    (audit/12: un .MI del book risolveva a un fondo US omonimo) e li' non fece
    danno solo perche' il percorso XBRL riceveva un 404 a valle.
    I filing invece esistono, e tornerebbero intitolati col ticker CHIESTO
    (v. `get_corporate_events_for_portfolio`, che scrive f"{ticker}: 8-K ..."):
    dati veri di un'altra societa', firmati come misura sul nome sbagliato.
    Sicuri sono due soli casi: il ticker senza suffisso, e gli alias VERIFICATI
    a mano nel negozio privato degli alias (sezione sec).
    """
    t = (ticker or "").upper().strip()
    if "." not in t:
        return None
    if t.split(".")[0] in _alias_sec():
        return None
    return ("%s ha un suffisso di listino e la risoluzione del CIK lo butta "
            "(%s -> %s), quindi potrebbe agganciare un OMONIMO quotato negli "
            "USA e restituire i filing di un'altra societa': non interrogata "
            "apposta. Se %s deposita davvero presso la SEC, serve un alias "
            "verificato nel negozio privato data/alias_fonti.json (sezione sec; "
            "forma in alias_fonti.example.json)."
            % (t, t, t.split(".")[0], t))


def lookup_cik(ticker: str, motivo: Optional[List[str]] = None) -> Optional[str]:
    """Trova il CIK dato un ticker (base di listino -> CIK a 10 cifre).

    `None` qui significa TRE cose diverse — l'emittente non e' nell'elenco SEC,
    l'elenco non ha risposto, la chiamata e' esplosa — e chi chiamava non poteva
    distinguerle. Voce E (22/08): passa una lista in `motivo` e ci trovi quale
    dei tre. Serve a non scrivere «non e' un filer SEC» quando la verita' e' che
    la rete era giu': sarebbe inventare una misura, cioe' il difetto che stiamo
    curando. Chi non passa `motivo` ha il comportamento storico.
    """
    t = ticker.upper().split(".")[0]
    t = _alias_sec().get(t, t)
    if t in _CIK_CACHE:
        return _CIK_CACHE[t]

    def _perche(testo):
        _log(f"  {testo}")
        if motivo is not None:
            motivo.append(testo)

    try:
        # SEC mantiene un file con tutti i ticker -> CIK
        url = "https://www.sec.gov/files/company_tickers.json"
        r = requests.get(url, headers=_headers(), timeout=10)
        if r.status_code != 200:
            _perche(f"elenco ticker SEC: HTTP {r.status_code} (non e' un verdetto "
                    f"su {t}, e' l'elenco che non ha risposto)")
            return None
        data = r.json()
        for _, v in data.items():
            if v.get("ticker", "").upper() == t:
                cik = str(v.get("cik_str", "")).zfill(10)
                _CIK_CACHE[t] = cik
                return cik
        _perche(f"{t} assente dall'elenco dei filer SEC (nessun CIK): "
                f"non deposita presso la SEC")
        return None
    except ContattoMancante as e:
        # Errore di CONFIGURAZIONE, non di rete (review 02/09): se nessuno raccoglie
        # il motivo, risale al chiamante (che ha il suo except dichiarante) — mai
        # un None che si legge «non e' un filer SEC».
        if motivo is None:
            raise
        _perche(f"lookup_cik({t}) error: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        _perche(f"lookup_cik({t}) error: {type(e).__name__}: {e}")
    return None


# ============================================================
# RECENT FILINGS
# ============================================================
def get_recent_filings(ticker: str, form_types: Optional[List[str]] = None,
                        days: int = 30, max_items: int = 30,
                        motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Ritorna i filing recenti per un ticker. Form types comuni: 8-K, 10-K, 10-Q, 4, S-1, 13D, 13G.

    `motivo`: lista opzionale dove finisce la RAGIONE di una lista vuota. Voce E
    (22/08): senza, una lista vuota da 403/500/timeout era indistinguibile da
    «nessun filing nel periodo», e chi chiamava dichiarava all'agente di aver
    interrogato la SEC quando la SEC non aveva risposto.
    """
    cik = lookup_cik(ticker, motivo=motivo)
    if not cik:
        return []
    form_types = form_types or ["8-K", "10-Q", "10-K", "4"]
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=_headers(), timeout=10)
        if r.status_code != 200:
            _msg = f"submissions HTTP {r.status_code} for {ticker}"
            _log("  " + _msg)
            if motivo is not None:
                motivo.append(_msg)
            return []
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        primary_descs = recent.get("primaryDocDescription", [])

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        out = []
        for i in range(min(len(forms), 1000)):
            form = forms[i]
            fdate = dates[i] if i < len(dates) else ""
            if form not in form_types:
                continue
            if fdate < cutoff:
                continue
            acc = accessions[i].replace("-", "") if i < len(accessions) else ""
            doc = primary_docs[i] if i < len(primary_docs) else ""
            desc = primary_descs[i] if i < len(primary_descs) else ""
            url_filing = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}"
            out.append({
                "ticker": ticker,
                "form": form,
                "filed_date": fdate,
                "description": desc,
                "url": url_filing,
                "accession": accessions[i] if i < len(accessions) else "",
            })
            if len(out) >= max_items:
                break
        return out
    except ContattoMancante as e:
        if motivo is None:   # configurazione mancante: risale (v. lookup_cik)
            raise
        _msg = f"get_recent_filings({ticker}) error: {type(e).__name__}: {e}"
        _log("  " + _msg)
        motivo.append(_msg)
        return []
    except Exception as e:
        _msg = f"get_recent_filings({ticker}) error: {type(e).__name__}: {e}"
        _log("  " + _msg)
        if motivo is not None:
            motivo.append(_msg)
        return []


# ============================================================
# 8-K MATERIAL EVENTS
# ============================================================
EIGHTK_ITEM_MAP = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of Material Agreement",
    "2.01": "Completion of Acquisition or Disposition",
    "2.02": "Results of Operations (Earnings)",
    "2.05": "Costs Associated with Exit Activities",
    "3.01": "Notice of Delisting",
    "3.02": "Unregistered Sales of Equity Securities",
    "4.01": "Changes in Registrant Accountant",
    "4.02": "Non-Reliance on Previously Issued Financials",
    "5.01": "Changes in Control",
    "5.02": "Departure/Appointment of Directors/Officers",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}


def get_8k_events(ticker: str, days: int = 30,
                   motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Lista degli 8-K (eventi materiali) per un ticker. `motivo`: v. get_recent_filings."""
    filings = get_recent_filings(ticker, form_types=["8-K"], days=days, max_items=20,
                                  motivo=motivo)
    # Enrich: classify by item code (richiede parse del doc, troppo costoso; ritorniamo metadata)
    for f in filings:
        f["event_type"] = "8-K Material Event"
    return filings


# ============================================================
# FORM 4 - INSIDER TRADES
# ============================================================
def get_insider_trades(ticker: str, days: int = 30, max_items: int = 30,
                        motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Ritorna i Form 4 (insider buy/sell) per un ticker. Parse XML del filing.
    `motivo`: v. get_recent_filings."""
    filings = get_recent_filings(ticker, form_types=["4"], days=days, max_items=max_items,
                                  motivo=motivo)
    trades = []
    for f in filings:
        try:
            # Form 4 ha .xml accessibile via /xslF345X05/<doc>
            # Costruisci URL del primary doc XML
            url = f.get("url", "")
            if not url:
                continue
            # 202-A2: il primaryDocument dei Form 4 e' l'HTML RENDERIZZATO (prefisso xslF345X05/):
            # i regex sui tag XML falliscono -> Unknown/0/$0. L'XML grezzo sta allo stesso
            # path SENZA il prefisso xsl.
            raw_url = url.replace("/xslF345X05/", "/") if "/xslF345X05/" in url else url
            r = requests.get(raw_url, headers=_headers(), timeout=10)
            if r.status_code != 200 or "<ownershipDocument" not in r.text:
                # fallback: cerca il .xml nella cartella del filing via index.json
                try:
                    folder = raw_url.rsplit("/", 1)[0]
                    idx = requests.get(folder + "/index.json", headers=_headers(), timeout=10).json()
                    xmls = [it["name"] for it in idx.get("directory", {}).get("item", [])
                            if str(it.get("name", "")).lower().endswith(".xml")]
                    for name in xmls:
                        r2 = requests.get(folder + "/" + name, headers=_headers(), timeout=10)
                        if r2.status_code == 200 and "<ownershipDocument" in r2.text:
                            r = r2
                            break
                except Exception:
                    pass
            if r.status_code != 200:
                continue
            # Parse minimo: cerchiamo issuerTradingSymbol, reportingOwner.rptOwnerName,
            # transactionDate, transactionShares, transactionPricePerShare, transactionCode (P/S)
            xml_text = r.text
            # Form 4 is often HTML wrap of XML; estraiamo via regex
            owner = _xml_extract(xml_text, "rptOwnerName") or "Unknown"
            relation = _xml_extract(xml_text, "officerTitle") or _xml_extract(xml_text, "directorIndicator") or ""
            # audit/11 §2: un Form 4 ha spesso MOLTE nonDerivativeTransaction (i filing
            # di un insider molto attivo ne hanno decine): prima si leggeva solo la PRIMA -> shares/value
            # sottostimati anche di 10-50x. Ora: tutte le transazioni, aggregate per codice.
            txs = _form4_transactions(xml_text)
            if not txs:  # documento atipico: comportamento pre-fix (primo match)
                txs = [{"date": _xml_extract(xml_text, "transactionDate.value"),
                        "code": _xml_extract(xml_text, "transactionCode"),
                        "shares": _xml_extract(xml_text, "transactionShares.value"),
                        "price": _xml_extract(xml_text, "transactionPricePerShare.value")}]
            groups: Dict[str, Dict[str, Any]] = {}
            for tx in txs:
                code = (tx.get("code") or "?").strip() or "?"
                try:
                    sh = float((tx.get("shares") or "0").replace(",", ""))
                except Exception:
                    sh = 0.0
                try:
                    px = float((tx.get("price") or "0").replace(",", ""))
                except Exception:
                    px = 0.0
                g = groups.setdefault(code, {"shares": 0.0, "value": 0.0, "date": None, "n": 0})
                g["shares"] += sh
                g["value"] += sh * px
                g["n"] += 1
                d = tx.get("date")
                if d and (g["date"] is None or d < g["date"]):
                    g["date"] = d
            for t_code, g in groups.items():
                shares_n = g["shares"]
                value_usd = g["value"]
                price_n = round(value_usd / shares_n, 4) if shares_n else 0.0
                t_date = g["date"] or f.get("filed_date", "")
                action = {"P": "BUY", "S": "SELL", "A": "GRANT", "M": "OPTION_EX", "D": "DISP"}.get(t_code, t_code)
                trades.append({
                    "ticker": ticker,
                    "owner": owner,
                    "relation": relation,
                    "trade_date": t_date,
                    "action": action,
                    "code": t_code,
                    "shares": shares_n,
                    "price_usd": price_n,  # prezzo MEDIO ponderato se piu' transazioni
                    "value_usd": value_usd,
                    "n_transactions": g["n"],
                    "filed_date": f.get("filed_date"),
                    "url": url,
                })
        except Exception as e:
            _log(f"  insider parse error for {ticker}: {e}")
            continue
        time.sleep(0.15)  # rate limit politeness
    return trades


def _form4_transactions(xml_text: str) -> List[Dict[str, Any]]:
    """Tutte le <nonDerivativeTransaction> di un Form 4 (audit/11: prima si estraeva solo
    il primo match nel documento). Stesso approccio regex del resto del modulo."""
    out = []
    for m in re.finditer(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
                         xml_text, re.DOTALL):
        blk = m.group(1)
        out.append({
            "date": _xml_extract(blk, "transactionDate.value"),
            "code": _xml_extract(blk, "transactionCode"),
            "shares": _xml_extract(blk, "transactionShares.value"),
            "price": _xml_extract(blk, "transactionPricePerShare.value"),
        })
    return out


def _xml_extract(text: str, tag: str) -> Optional[str]:
    """Estrae il primo match di <tag>...</tag> (case-sensitive), con o senza valore nested in <value>."""
    # Handle nested tag.value pattern
    if "." in tag:
        parent, child = tag.split(".", 1)
        # find <parent>...<child>VAL</child>
        pat = re.compile(rf"<{parent}>(.*?)</{parent}>", re.DOTALL)
        m = pat.search(text)
        if m:
            inner = m.group(1)
            pat2 = re.compile(rf"<{child}>(.*?)</{child}>", re.DOTALL)
            m2 = pat2.search(inner)
            if m2:
                return m2.group(1).strip()
        return None
    pat = re.compile(rf"<{tag}>(.*?)</{tag}>", re.DOTALL)
    m = pat.search(text)
    return m.group(1).strip() if m else None


# ============================================================
# 13F HOLDINGS (institutional)
# ============================================================
class InvestitoreSconosciuto(ValueError):
    """Slug 13F non mappato: KO DICHIARATO con gli slug disponibili (05/09, chat 8c, lotto 3
    del criterio (1)). Prima `get_13f_holdings` tornava `[]` con un `_log` che nessun modello
    legge, e il dispatcher rispondeva «count: 0» come se fosse una misura (regola PM 14/07:
    il buco si dichiara). E' un ValueError: i chiamanti generici lo prendono come tale."""


def get_13f_holdings(investor: str, max_items: int = 30) -> List[Dict[str, Any]]:
    """Pulla l'ultimo 13F-HR di un institutional investor noto, per slug (gli slug del negozio
    privato delle istituzioni). Slug sconosciuto = InvestitoreSconosciuto con l'elenco dei
    disponibili; negozio assente o illeggibile = InvestitoreSconosciuto che lo DICHIARA.
    """
    ist = istituzioni_13f()
    cik = ist["cik"].get((investor or "").strip().lower())
    if not cik:
        if ist["origine"] in ("assente", "illeggibile"):
            raise InvestitoreSconosciuto(
                "negozio delle istituzioni %s (%s): nessun gestore 13F interrogabile"
                % (ist["origine"], ist["motivo"]))
        raise InvestitoreSconosciuto(
            "investitore 13F sconosciuto: %r. Slug disponibili: %s"
            % (investor, ", ".join(sorted(ist["cik"]))))
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=_headers(), timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        # Trova l'ultimo 13F-HR
        idx_latest_13f = None
        for i, f in enumerate(forms):
            if f == "13F-HR":
                idx_latest_13f = i
                break
        if idx_latest_13f is None:
            return []
        acc = accessions[idx_latest_13f].replace("-", "")
        filing_date = dates[idx_latest_13f]
        # L'XML delle holdings e' in: /Archives/edgar/data/<cik>/<acc>/infotable.xml
        base_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
        # Prima trova l'index per scoprire il filename dell'infotable
        index_url = f"{base_url}/index.json"
        idx_r = requests.get(index_url, headers=_headers(), timeout=10)
        if idx_r.status_code != 200:
            return []
        idx_data = idx_r.json()
        # Fable 5 16/07: il file può chiamarsi form13fInfoTable.xml MA ANCHE
        # informationtable.xml — e "infotable" NON è substring di "informationtable":
        # quei filing tornavano [] MUTO. primary_doc.xml è la cover, va escluso.
        info_xml_name = None
        for item in idx_data.get("directory", {}).get("item", []):
            name = item.get("name", "")
            low = name.lower()
            if name.endswith(".xml") and "primary_doc" not in low and (
                    "infotable" in low or "informationtable" in low):
                info_xml_name = name
                break
        if not info_xml_name:
            _log(f"  13F {investor}: nessun infotable/informationtable .xml nell'index "
                 f"({base_url}/index.json) — buco dichiarato")
            return []
        xml_r = requests.get(f"{base_url}/{info_xml_name}", headers=_headers(), timeout=15)
        if xml_r.status_code != 200:
            _log(f"  13F {investor}: HTTP {xml_r.status_code} su {info_xml_name}")
            return []
        # Parse XML namespace-agnostico (Fable 5 16/07). Il vecchio approccio strappava
        # le DICHIARAZIONI xmlns via regex ma non i PREFISSI nei tag (<ns1:infoTable>):
        # ET falliva con "unbound prefix" su ogni filing moderno e il tool tornava []
        # da mesi. Ora si parsa l'XML ORIGINALE e si matcha sul localname del tag.
        def _local(tag):
            return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]

        def _findtext_local(el, name):
            for c in el.iter():
                if _local(c.tag) == name:
                    return c.text
            return None

        holdings = []
        try:
            root = ET.fromstring(xml_r.content)  # bytes: rispetta l'encoding dichiarato
            # UNITÀ DEL VALUE (regola SEC): dal 3/01/2023 i 13F riportano DOLLARI PIENI;
            # prima erano MIGLIAIA. Il vecchio "*1000" fisso avrebbe gonfiato di 1000x
            # ogni filing recente (MSFT 2,09B -> 2,09T). Si decide dalla filing date.
            _mult = 1 if str(filing_date) >= "2023-01-03" else 1000
            for table in root.iter():
                if _local(table.tag) != "infoTable":
                    continue
                issuer = _findtext_local(table, "nameOfIssuer") or ""
                title = _findtext_local(table, "titleOfClass") or ""
                cusip = _findtext_local(table, "cusip") or ""
                value_raw = _findtext_local(table, "value") or "0"
                shrs = _findtext_local(table, "sshPrnamt") or "0"
                try:
                    value_usd = int(round(float(str(value_raw).replace(",", "")))) * _mult
                except Exception:
                    value_usd = 0
                try:
                    shares = int(round(float(str(shrs).replace(",", ""))))
                except Exception:
                    shares = 0
                holdings.append({
                    "investor": investor,
                    "filing_date": filing_date,
                    "issuer": issuer,
                    "title_of_class": title,
                    "cusip": cusip,
                    "shares": shares,
                    "value_usd": value_usd,
                })
            if not holdings:
                _log(f"  13F {investor}: XML parsato ma zero <infoTable> ({info_xml_name})")
        except Exception as e:
            _log(f"  13F XML parse error: {e}")
            return []
        # Ordina per value desc
        holdings.sort(key=lambda h: -h.get("value_usd", 0))
        return holdings[:max_items]
    except ContattoMancante:
        raise   # configurazione mancante: la funzione non ha `motivo`, risale al chiamante
    except Exception as e:
        _log(f"  get_13f_holdings({investor}) error: {e}")
        return []


# ============================================================
# AGGREGATE EVENTS (for /news/corporate-events endpoint)
# ============================================================
def get_corporate_events_for_portfolio(portfolio_tickers: List[str],
                                         days: int = 14,
                                         motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Pulla 8-K + insider Form 4 per tutti i ticker del portfolio.
    Ritorna lista unificata ordinata per data desc.

    `motivo`: lista opzionale dove finisce la ragione di ogni ramo caduto. Voce E
    (22/08): i tre blocchi qui sotto inghiottivano 403/500/timeout con un
    `return []` e un `except: pass`, quindi una lista vuota poteva significare
    «nessun evento» oppure «la SEC non ha risposto» — e chi chiamava dichiarava
    all'agente di averla interrogata. `ok_sec` era un letterale, non una misura.
    """
    events = []
    for ticker in portfolio_tickers:
        # 8-K
        for f in get_8k_events(ticker, days=days, motivo=motivo):
            events.append({
                "ticker": ticker,
                "type": "8-K",
                "title": f"{ticker}: 8-K Material Event filed",
                "snippet": f.get("description", "")[:200],
                "date": f.get("filed_date", ""),
                "url": f.get("url", ""),
                "importance": 4,
            })
        time.sleep(0.2)
        # 202-A2b: filing STRUTTURALI (il caso di una DAT del book: aveva un S-1 a maggio - raccolta
        # capitale, LA notizia per una DAT - ma il pannello guardava solo 8-K + Form 4)
        STRUCT_FORMS = {
            "S-1": ("Registrazione titoli (raccolta capitale)", 5),
            "S-1/A": ("Registrazione titoli - emendamento", 4),
            "424B4": ("Prospetto offerta (pricing)", 5),
            "424B5": ("Prospetto offerta (pricing)", 5),
            "10-Q": ("Trimestrale 10-Q", 4),
            "10-K": ("Annuale 10-K", 4),
            "SC 13D": ("Stake attivista >5%", 5),
            "SC 13G": ("Stake passivo >5%", 3),
        }
        try:
            for f in get_recent_filings(ticker, form_types=list(STRUCT_FORMS.keys()),
                                         days=days, max_items=8, motivo=motivo):
                label, imp = STRUCT_FORMS.get(f.get("form", ""), (f.get("form", "filing"), 3))
                events.append({
                    "ticker": ticker,
                    "type": f.get("form", ""),
                    "title": f"{ticker}: {f.get('form', '')} - {label}",
                    "snippet": (f.get("description", "") or label)[:200],
                    "date": f.get("filed_date", ""),
                    "url": f.get("url", ""),
                    "importance": imp,
                })
        except Exception as e:
            # era `except Exception: pass` — un ramo muto dentro la fonte che
            # il payload dichiara «interrogata» (voce E, 22/08).
            _msg = f"filing strutturali ({ticker}) error: {type(e).__name__}: {e}"
            _log("  " + _msg)
            if motivo is not None:
                motivo.append(_msg)
        time.sleep(0.2)
        # Form 4 (top 5 per ticker)
        for t in get_insider_trades(ticker, days=days, max_items=5, motivo=motivo):
            verb = "ACQUISTA" if t["action"] == "BUY" else ("VENDE" if t["action"] == "SELL" else t["action"])
            events.append({
                "ticker": ticker,
                "type": "Form 4",
                "title": f"{ticker}: {t['owner']} ({t['relation'][:30]}) {verb} {int(t['shares']):,} azioni "
                         f"a ${t['price_usd']:.2f} (valore ${t['value_usd']:,.0f})",
                "snippet": f"Insider {t['owner']} {verb} shares on {t['trade_date']}",
                "date": t.get("trade_date") or t.get("filed_date", ""),
                "url": t.get("url", ""),
                "importance": 5 if t["action"] == "BUY" and t["value_usd"] > 500000 else 3,
                "metadata": t,
            })
        time.sleep(0.2)
    # Sort by date desc
    events.sort(key=lambda e: e.get("date", ""), reverse=True)
    return events


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "13f":
        if len(sys.argv) < 3:
            print("uso: python sec_edgar.py 13f <slug del negozio privato delle istituzioni>",
                  file=sys.stderr)
            sys.exit(2)
        investor = sys.argv[2]
        h = get_13f_holdings(investor)
        print(f"=== {investor} - {len(h)} holdings ===")
        for x in h[:20]:
            print(f"  ${x['value_usd']:>15,} | {x['issuer'][:40]:40s} | {x['shares']:>15,} shares")
    elif len(sys.argv) > 1:
        t = sys.argv[1].upper()
        print(f"\n=== {t} - 8-K events (30d) ===")
        for e in get_8k_events(t):
            print(f"  {e['filed_date']} | {e['description'][:60]}")
        print(f"\n=== {t} - Insider trades (30d) ===")
        for tr in get_insider_trades(t, max_items=10):
            print(f"  {tr['trade_date']} | {tr['owner'][:25]:25s} | {tr['action']:6s} | "
                  f"{int(tr['shares']):>12,} @ ${tr['price_usd']:>8.2f} = ${tr['value_usd']:>15,.0f}")
    else:
        print("uso: python sec_edgar.py <TICKER>  oppure  python sec_edgar.py 13f <slug>", file=sys.stderr)
        sys.exit(2)
