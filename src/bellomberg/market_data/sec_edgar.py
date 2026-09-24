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
                **_filing_metadata(recent, i, cik, data.get("name")),
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


def _filing_metadata(righe, indice, cik, issuer):
    def campo(nome):
        valori = righe.get(nome, [])
        return valori[indice] if indice < len(valori) else None
    return {"emittente_id": f"CIK:{str(cik).zfill(10)}", "issuer": issuer,
            "report_date": campo("reportDate"),
            "items": [x.strip() for x in (campo("items") or "").split(",") if x.strip()],
            "fonte": "SEC EDGAR"}


def get_filing_catalog(ticker: str, days: int = 1100, max_pages: int = 20) -> dict:
    """I-20: indice annuali/intermedi inclusi 20-F/6-K e archivi submissions.

    Non assume che un 6-K sia un bilancio: periodo/sezioni vanno verificati sul
    documento. Limiti, errori e assenze viaggiano insieme ai risultati parziali.
    Nessuna cache su disco. Il percorso storico degli altri consumer resta invariato.
    """
    return _publication_catalog(ticker, days, max_pages, earnings=False)


def get_publication_catalog(ticker: str, days: int = 400, max_pages: int = 2) -> dict:
    """Free discovery only: statement notices plus 8-K Item 2.02 releases.

    Does not classify 8-K as a financial statement or extract numeric guidance.
    Other guidance channels remain outside this bounded SEC catalog.
    """
    return _publication_catalog(ticker, days, max_pages, earnings=True)


def _publication_catalog(ticker, days, max_pages, *, earnings):
    out = {"stato": "ok", "documenti": [], "motivi": [], "fonte": "SEC EDGAR"}
    try:
        if days <= 0 or max_pages < 1:
            raise ValueError("days e max_pages devono essere positivi")
        ambiguo = ticker_ambiguo_per_cik(ticker)
        if ambiguo:
            raise ValueError(ambiguo)
        cik = lookup_cik(ticker, motivo=out["motivi"])
        if not cik:
            raise ValueError("CIK non disponibile")
        cik = str(int(cik)).zfill(10)
        base = "https://data.sec.gov/submissions/"
        response = requests.get(base + f"CIK{cik}.json", headers=_headers(), timeout=30)
        response.raise_for_status()
        data = response.json()
        if str(int(data["cik"])).zfill(10) != cik:
            raise ValueError("CIK della risposta diverso dall'emittente richiesto")
        if earnings:
            out["emittente_id"] = "CIK:" + cik
        filings = data["filings"]
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        forms = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A",
                 "40-F", "40-F/A", "6-K", "6-K/A"}
        visti = {}

        def aggiungi(righe):
            for i, form in enumerate(righe["form"]):
                is_earnings = earnings and form in ("8-K", "8-K/A")
                if form not in forms and not is_earnings:
                    continue
                metadata = _filing_metadata(righe, i, cik, data.get("name"))
                if is_earnings and "2.02" not in metadata["items"]:
                    continue
                fdate = righe["filingDate"][i]
                if fdate < cutoff:
                    continue
                acc, doc = righe["accessionNumber"][i], righe["primaryDocument"][i]
                if not acc or not doc:
                    out["motivi"].append(f"{form} {fdate}: accession/documento primario mancante")
                    continue
                entry = {"ticker": ticker, "form": form, "filed_date": fdate,
                    "accession": acc, "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}",
                    **metadata}
                if acc in visti:
                    if visti[acc] != entry:
                        out["motivi"].append(f"{acc}: metadati discordanti tra pagine SEC")
                    continue
                visti[acc] = entry
                out["documenti"].append(entry)

        aggiungi(filings["recent"])
        archivi = [f for f in filings.get("files", []) if not f.get("filingTo") or f["filingTo"] >= cutoff]
        for i, archivio in enumerate(archivi, 1):
            if i >= max_pages:
                out["motivi"].append(f"limite {max_pages} pagine: storico non completo")
                break
            nome = archivio.get("name", "")
            if not re.fullmatch(rf"CIK{cik}-submissions-\d+\.json", nome):
                out["motivi"].append("nome archivio SEC non valido per questo CIK")
                continue
            try:
                time.sleep(0.12)  # fair access anche sui piccoli archivi
                response = requests.get(base + nome, headers=_headers(), timeout=30)
                response.raise_for_status()
                aggiungi(response.json())
            except Exception as exc:
                out["motivi"].append(f"archivio {nome}: {type(exc).__name__}: {exc}")
    except Exception as exc:
        out["motivi"].append(f"{type(exc).__name__}: {exc}")
    out["documenti"].sort(key=lambda d: (d["filed_date"], d["accession"]), reverse=True)
    if out["motivi"]:
        out["stato"] = "parziale" if out["documenti"] else "errore"
    return out


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
# codice di transazione SEC -> azione nei dati (metadata.action: codici, restano tali)
_AZIONI_FORM4 = {"P": "BUY", "S": "SELL", "A": "GRANT", "M": "OPTION_EX", "D": "DISP"}
_CODICE_DA_AZIONE = {azione: codice for codice, azione in _AZIONI_FORM4.items()}

# 13/09: la frase del titolo e dello snippet si sceglie PER CODICE. Prima il verbo era uno slot
# davanti alle azioni e i codici non tradotti (GRANT, OPTION_EX, DISP, la lettera nuda)
# finivano dentro la frase italiana. I testi seguono la definizione SEC dei codici (Investor
# Bulletin «Insider Transactions and Forms 3, 4, and 5», che rimanda alle General Instructions
# del Form 4): M e' «exercise or conversion of derivative security», non solo opzioni; G e' un
# dono «by or to the insider» e qui la direzione non si legge, quindi la frase e' neutra.
# Stringhe semplici: message() si costruisce all'uso, nella lingua del giro.
# codice -> (coda titolo it, coda titolo en, coda snippet it, coda snippet en)
_FRASI_FORM4 = {
    "P": ("ACQUISTA {shares} azioni a ${price} (valore ${value})",
          "BUYS {shares} shares at ${price} (value ${value})",
          "ACQUISTA azioni il {date}", "BUYS shares on {date}"),
    "S": ("VENDE {shares} azioni a ${price} (valore ${value})",
          "SELLS {shares} shares at ${price} (value ${value})",
          "VENDE azioni il {date}", "SELLS shares on {date}"),
    "A": ("RICEVE {shares} azioni dalla società (assegnazione, premio o altra acquisizione) "
          "a ${price} (valore ${value})",
          "ACQUIRES {shares} shares from the company (grant, award or other acquisition) "
          "at ${price} (value ${value})",
          "RICEVE azioni dalla società (assegnazione, premio o altra acquisizione) il {date}",
          "ACQUIRES shares from the company (grant, award or other acquisition) on {date}"),
    "M": ("ACQUISISCE {shares} azioni da esercizio o conversione di derivati a ${price} (valore ${value})",
          "ACQUIRES {shares} shares by exercise or conversion of derivatives at ${price} (value ${value})",
          "ACQUISISCE azioni da esercizio o conversione di derivati il {date}",
          "ACQUIRES shares by exercise or conversion of derivatives on {date}"),
    "D": ("VENDE O TRASFERISCE {shares} azioni alla società a ${price} (valore ${value})",
          "SELLS OR TRANSFERS {shares} shares back to the company at ${price} (value ${value})",
          "VENDE O TRASFERISCE azioni alla società il {date}",
          "SELLS OR TRANSFERS shares back to the company on {date}"),
    "F": ("USA {shares} azioni ricevute dalla società per pagare prezzo di esercizio o imposte, "
          "a ${price} (valore ${value})",
          "USES {shares} shares received from the company to pay exercise price or tax liability, "
          "at ${price} (value ${value})",
          "USA azioni ricevute dalla società per pagare prezzo di esercizio o imposte il {date}",
          "USES shares received from the company to pay exercise price or tax liability on {date}"),
    "G": ("REGISTRA UNA DONAZIONE (FATTA O RICEVUTA) DI {shares} azioni a ${price} (valore ${value})",
          "REPORTS A GIFT (MADE OR RECEIVED) OF {shares} shares at ${price} (value ${value})",
          "REGISTRA UNA DONAZIONE (FATTA O RICEVUTA) DI azioni il {date}",
          "REPORTS A GIFT (MADE OR RECEIVED) OF shares on {date}"),
}
# un codice senza frase dedicata si NOMINA (la definizione e' nelle istruzioni SEC), mai nudo
_FRASE_FORM4_ALTRO_CODICE = (
    "REGISTRA UN'OPERAZIONE CON CODICE SEC {code} SU {shares} azioni a ${price} (valore ${value})",
    "REPORTS A SEC CODE {code} TRANSACTION ON {shares} shares at ${price} (value ${value})",
    "REGISTRA UN'OPERAZIONE CON CODICE SEC {code} il {date}",
    "REPORTS A SEC CODE {code} TRANSACTION on {date}")
# «?» = il documento non porta un transactionCode: si dice, non si mostra il punto di domanda
_FRASE_FORM4_SENZA_CODICE = (
    "REGISTRA UN'OPERAZIONE SENZA CODICE DI TRANSAZIONE SU {shares} azioni a ${price} (valore ${value})",
    "REPORTS A TRANSACTION WITHOUT A TRANSACTION CODE ON {shares} shares at ${price} (value ${value})",
    "REGISTRA UN'OPERAZIONE SENZA CODICE DI TRANSAZIONE il {date}",
    "REPORTS A TRANSACTION WITHOUT A TRANSACTION CODE on {date}")


def _titolare_form4(xml_text: str):
    """(titolare, ruolo) del PRIMO reportingOwner di un Form 4.

    13/09: il ruolo si leggeva da officerTitle oppure da <directorIndicator>, un tag che lo
    schema ownership della SEC NON ha: per un amministratore senza carica esecutiva o per un
    azionista oltre il 10% il ruolo restava vuoto e il titolo diceva «X () VENDE». I tag veri
    sono isDirector, isOfficer, isTenPercentOwner, isOther (valori true/false nei filing
    recenti, 1/0 nei vecchi) piu' officerTitle e otherText, che restano come nella fonte.
    Piu' flag insieme si uniscono. Nessun ruolo = "" (il titolo non scrive parentesi vuote).
    Titolare assente = frase nostra che lo dichiara, mai «Unknown» (resta una stringa: chi
    legge `owner` nei dati non riceve un None)."""
    from bellomberg.core.presentation import message, join_messages
    m = re.search(r"<reportingOwner>(.*?)</reportingOwner>", xml_text, re.DOTALL)
    blocco = m.group(1) if m else xml_text

    def acceso(tag):
        return (_xml_extract(blocco, tag) or "").strip().lower() in ("1", "true")

    ruoli = []
    if acceso("isDirector"):
        ruoli.append(message("amministratore", "director"))
    titolo = _xml_extract(blocco, "officerTitle")
    if titolo:
        ruoli.append(titolo)
    elif acceso("isOfficer"):
        ruoli.append(message("dirigente", "officer"))
    if acceso("isTenPercentOwner"):
        ruoli.append(message("azionista oltre il 10%", "10% owner"))
    altro = _xml_extract(blocco, "otherText")
    if altro:
        ruoli.append(altro)
    elif acceso("isOther"):
        ruoli.append(message("altro rapporto", "other relationship"))
    titolare = (_xml_extract(blocco, "rptOwnerName")
                or message("titolare non indicato nel filing", "owner not stated in the filing"))
    return titolare, join_messages(", ", ruoli)


def get_insider_trades(ticker: str, days: int = 30, max_items: int = 30,
                        motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Ritorna i Form 4 (insider buy/sell) per un ticker. Parse XML del filing.
    `motivo`: v. get_recent_filings."""
    from bellomberg.core.presentation import message, error_text
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
            if r.status_code != 200 or "<ownershipDocument" not in r.text:
                # 13/09: prima qui fermava solo un HTTP diverso da 200. Una pagina 200 SENZA
                # <ownershipDocument> (l'HTML renderizzato, con l'index.json che non trova
                # l'XML) arrivava al parse e ne usciva «Unknown», codice «?», 0 azioni: il
                # pannello mostrava un'operazione mai letta. Ora si salta e si DICHIARA, e lo
                # stesso vale per l'HTTP diverso da 200, che prima cadeva zitto (regola PM 14/07).
                _msg = message("Form 4 {url}: documento XML non trovato (HTTP {status}), operazione non letta",
                               "Form 4 {url}: XML document not found (HTTP {status}), transaction not read",
                               url=url, status=r.status_code)
                _log("  " + _msg)
                if motivo is not None:
                    motivo.append(_msg)
                continue
            # Parse minimo: cerchiamo issuerTradingSymbol, reportingOwner.rptOwnerName,
            # transactionDate, transactionShares, transactionPricePerShare, transactionCode (P/S)
            xml_text = r.text
            # Form 4 is often HTML wrap of XML; estraiamo via regex
            owner, relation = _titolare_form4(xml_text)
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
                action = _AZIONI_FORM4.get(t_code, t_code)
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
            # 13/09: prima solo nel log, e la lista vuota si leggeva «nessun insider»
            _msg = message("Form 4 {url}: errore di lettura ({kind}: {cause}), operazione non letta",
                           "Form 4 {url}: read error ({kind}: {cause}), transaction not read",
                           url=f.get("url", ""), kind=type(e).__name__, cause=error_text(e))
            _log(f"  insider parse error for {ticker}: " + _msg)
            if motivo is not None:
                motivo.append(_msg)
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
    from bellomberg.core.presentation import message, error_text

    def display_number(value, spec):
        english = format(value, spec)
        return message(english.translate(str.maketrans(",.", ".,")), english)

    events = []
    for ticker in portfolio_tickers:
        # 8-K
        for f in get_8k_events(ticker, days=days, motivo=motivo):
            events.append({
                "ticker": ticker,
                "type": "8-K",
                "title": message("{ticker}: depositato evento rilevante 8-K", "{ticker}: 8-K Material Event filed", ticker=ticker),
                "snippet": f.get("description", "")[:200],
                "title_origin": "bellomberg", "snippet_origin": "source", "presentation_languages": ["it", "en"],
                "date": f.get("filed_date", ""),
                "url": f.get("url", ""),
                "importance": 4,
            })
        time.sleep(0.2)
        # 202-A2b: filing STRUTTURALI (il caso di una DAT del book: aveva un S-1 a maggio - raccolta
        # capitale, LA notizia per una DAT - ma il pannello guardava solo 8-K + Form 4)
        STRUCT_FORMS = {
            "S-1": (message("Registrazione titoli (raccolta capitale)", "Securities registration (capital raising)"), 5),
            "S-1/A": (message("Registrazione titoli - emendamento", "Securities registration - amendment"), 4),
            "424B4": (message("Prospetto offerta (prezzo)", "Offering prospectus (pricing)"), 5),
            "424B5": (message("Prospetto offerta (prezzo)", "Offering prospectus (pricing)"), 5),
            "10-Q": (message("Trimestrale 10-Q", "Quarterly report 10-Q"), 4),
            "10-K": (message("Annuale 10-K", "Annual report 10-K"), 4),
            "SC 13D": (message("Partecipazione attivista >5%", "Activist stake >5%"), 5),
            "SC 13G": (message("Partecipazione passiva >5%", "Passive stake >5%"), 3),
            # 13/09: dal 18/12/2024 le Schedule 13D/13G sono XML strutturato (EDGAR Release
            # 23.4) e l'indice le registra come «SCHEDULE 13D»/«SCHEDULE 13G» (verificato su un
            # indice EDGAR vero del 2025: «Form SCHEDULE 13G/A»). Con le sole chiavi «SC ...» le
            # partecipazioni oltre il 5% depositate col nome nuovo sparivano zitte. Le /A
            # (emendamenti) restano fuori come prima, per entrambi i nomi.
            "SCHEDULE 13D": (message("Partecipazione attivista >5%", "Activist stake >5%"), 5),
            "SCHEDULE 13G": (message("Partecipazione passiva >5%", "Passive stake >5%"), 3),
        }
        try:
            for f in get_recent_filings(ticker, form_types=list(STRUCT_FORMS.keys()),
                                         days=days, max_items=8, motivo=motivo):
                label, imp = STRUCT_FORMS.get(f.get("form", ""), (f.get("form", "filing"), 3))
                events.append({
                    "ticker": ticker,
                    "type": f.get("form", ""),
                    "title": message("{ticker}: {form} - {label}", "{ticker}: {form} - {label}",
                                     ticker=ticker, form=f.get("form", ""), label=label),
                    "snippet": f["description"][:200] if f.get("description") else label,
                    "title_origin": "bellomberg", "snippet_origin": "source" if f.get("description") else "bellomberg",
                    "presentation_languages": ["it", "en"],
                    "date": f.get("filed_date", ""),
                    "url": f.get("url", ""),
                    "importance": imp,
                })
        except Exception as e:
            # era `except Exception: pass` — un ramo muto dentro la fonte che
            # il payload dichiara «interrogata» (voce E, 22/08).
            _msg = message("Filing strutturali ({ticker}): errore {kind}: {cause}",
                           "Structural filings ({ticker}) error: {kind}: {cause}",
                           ticker=ticker, kind=type(e).__name__, cause=error_text(e))
            _log("  " + _msg)
            if motivo is not None:
                motivo.append(_msg)
        time.sleep(0.2)
        # Form 4 (top 5 per ticker)
        for t in get_insider_trades(ticker, days=days, max_items=5, motivo=motivo):
            # 13/09: frase per codice SEC (v. _FRASI_FORM4), ruolo senza parentesi vuote e senza
            # il taglio a 30 caratteri che spezzava a meta' parola un officerTitle della fonte
            codice = t.get("code") or _CODICE_DA_AZIONE.get(t["action"], t["action"])
            frasi = (_FRASE_FORM4_SENZA_CODICE if codice == "?"
                     else _FRASI_FORM4.get(codice, _FRASE_FORM4_ALTRO_CODICE))
            ruolo = message(" ({relation})", " ({relation})", relation=t["relation"]) if t["relation"] else ""
            valori = dict(ticker=ticker, owner=t['owner'], ruolo=ruolo, code=codice,
                          shares=display_number(int(t['shares']), ','), price=display_number(t['price_usd'], '.2f'),
                          value=display_number(t['value_usd'], ',.0f'), date=t['trade_date'])
            events.append({
                "ticker": ticker,
                "type": "Form 4",
                "title": message("{ticker}: {owner}{ruolo} " + frasi[0], "{ticker}: {owner}{ruolo} " + frasi[1], **valori),
                "snippet": message("Insider {owner} " + frasi[2], "Insider {owner} " + frasi[3], **valori),
                "title_origin": "bellomberg", "snippet_origin": "bellomberg", "presentation_languages": ["it", "en"],
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
