"""freschezza_trimestrale.py - CASCATA DI FONTI per i numeri dell'ULTIMO periodo pubblicato
(FRESCHEZZA, 06/10/2026, Opus 5.5).

Richiesta PM (06/10 notte): «la Trade Idea va fatta con i DATI AGGIORNATI, non al 2024; non
devono esserci lacune». Misura che l'ha motivata: nella Trade Idea del 06/10 i desk avevano chiamato
get_financial_history 22 volte e ricevuto anni 2018-2024, perche' il companyfacts SEC di
un'estera ha solo i 20-F (i 6-K trimestrali non sono XBRL) e il tool prendeva solo fp=FY.

Livelli, nell'ordine in cui si USANO (ci si ferma al primo che ha il periodo atteso):
  0. periodo atteso: dal calendario dei risultati del fornitore (data passata piu' recente,
     altrimenti la prossima) sulla griglia dei trimestri dell'emittente (fiscale se la SEC o
     il fornitore ne mostrano una non solare); senza calendario, regola dei 45 giorni DICHIARATA.
  1. SEC XBRL companyfacts: ultimo 10-Q/10-K/20-F con period_end, filing_date, accession.
  2. archivio Filing (SEC/ESEF/sito IR) dalla fase documenti della Trade Idea (contesto).
  3. comunicato dei risultati dell'emittente su un circuito ufficiale: oggi SEC EDGAR
     (8-K Item 2.02 / 6-K, l'allegato e' il testo della societa'). I circuiti newswire
     (Business Wire, GlobeNewswire, PR Newswire) NON sono interrogati da questo codice:
     dichiarato nel livello.
  4. fornitore dati (Yahoo Finance via yfinance): bilanci trimestrali, etichetta «non filing».
Nessun livello col periodo atteso = lacuna esplicita col motivo di OGNI livello.
Tempo massimo duro: ogni chiamata di rete gira in un thread daemon col tempo che resta;
nessuna eccezione esce da `passo_freschezza` / `blocco_per_tool`.
"""
from __future__ import annotations

import calendar
import html as _html
import json
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

CONTRATTO = "freschezza-trimestrale/1"
TOLLERANZA_GIORNI = 10          # esercizi a 52/53 settimane: la fine trimestre si sposta di qualche giorno
GIORNI_TERMINE = 45             # regola senza calendario: un trimestre e' atteso 45 giorni dopo la chiusura
FINESTRA_CALENDARIO = 200       # una data dei risultati piu' vecchia (o lontana) di cosi' non fa testo
TEMPO_TOOL_S = 30.0             # tetto della cascata chiamata da un tool senza cache (per titolo)
TTL_CACHE_S = 12 * 3600
TTL_LACUNA_S = 3600
MAX_ACCESSION = 6
MAX_DOCUMENTI = 3
MAX_ESTRATTO = 1500             # il blocco sta intero nella ricevuta del tool (R-CASCATA)

ETICHETTE = {
    1: "SEC XBRL companyfacts (deposito ufficiale {form})",
    2: "archivio Filing dell'emittente (SEC/ESEF/sito IR, documenti nel dossier)",
    3: "comunicato ufficiale dell'emittente via SEC EDGAR ({form})",
    4: "fornitore dati (Yahoo Finance via yfinance), non filing",
}
NOMI = {1: "sec_companyfacts", 2: "archivio_filing", 3: "comunicato_emittente", 4: "fornitore_dati"}
CIRCUITI_NON_COPERTI = ("circuiti newswire (Business Wire, GlobeNewswire, PR Newswire) non interrogati "
                        "da questo codice: solo SEC EDGAR come circuito dei comunicati")

_RIGHE_FORNITORE = {
    "income": ("Total Revenue", "Operating Income", "Net Income", "Net Income Common Stockholders",
               "Diluted EPS", "Basic EPS", "Diluted Average Shares"),
    "balance": ("Cash And Cash Equivalents", "Total Debt", "Stockholders Equity", "Total Assets",
                "Ordinary Shares Number"),
    "cashflow": ("Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"),
}
_VALORI_SEC = ("revenue", "operating_income", "net_income", "eps_diluted", "shares_diluted", "cash",
               "lt_debt", "equity", "total_assets", "cfo", "capex")


# ------------------------------------------------------------------ date e griglia

def _data(valore: Any) -> Optional[date]:
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    try:
        return date.fromisoformat(str(valore)[:10])
    except (TypeError, ValueError):
        return None


def _piu_mesi(d: date, mesi: int, fine_mese: bool) -> date:
    m = d.month - 1 + mesi
    anno, mese = d.year + m // 12, m % 12 + 1
    ultimo = calendar.monthrange(anno, mese)[1]
    return date(anno, mese, ultimo if fine_mese else min(d.day, ultimo))


def _fine_prima(giorno: date, ancora: Optional[date]) -> date:
    """L'ultima fine trimestre della griglia STRETTAMENTE prima di `giorno`."""
    if ancora is None:
        q = (giorno.month - 1) // 3 * 3          # mese 0-based d'inizio del trimestre di `giorno`
        fine = date(giorno.year, q + 1, 1) - timedelta(days=1)
        return fine
    fine_mese = ancora.day == calendar.monthrange(ancora.year, ancora.month)[1]
    k = 0
    if ancora < giorno:
        while _piu_mesi(ancora, 3 * (k + 1), fine_mese) < giorno:
            k += 1
        return _piu_mesi(ancora, 3 * k, fine_mese)
    while _piu_mesi(ancora, 3 * k, fine_mese) >= giorno:
        k -= 1
    return _piu_mesi(ancora, 3 * k, fine_mese)


def _griglia_fiscale(ancora: Optional[date]) -> Optional[date]:
    """Un'ancora che cade su una fine trimestre solare non cambia la griglia."""
    if ancora is None:
        return None
    if ancora.month in (3, 6, 9, 12) and ancora.day == calendar.monthrange(ancora.year, ancora.month)[1]:
        return None
    return ancora


def _fine_dopo(fine: date, griglia: Optional[date]) -> date:
    """La fine trimestre successiva a `fine` sulla stessa griglia."""
    if griglia is None:
        return _piu_mesi(fine, 3, True)
    return _piu_mesi(fine, 3, griglia.day == calendar.monthrange(griglia.year, griglia.month)[1])


def periodo_atteso(oggi: date, *, date_risultati: List[Any], ancora: Any = None) -> Dict[str, Any]:
    """Ultimo periodo che l'emittente ha GIA' pubblicato a `oggi` (stima dichiarata con la sua base)."""
    griglia = _griglia_fiscale(_data(ancora))
    nome_griglia = "fiscale" if griglia else "solare"
    date_ok = sorted({d for d in (_data(x) for x in date_risultati or []) if d is not None})
    passate = [d for d in date_ok if d <= oggi and (oggi - d).days <= FINESTRA_CALENDARIO]
    future = [d for d in date_ok if d > oggi and (d - oggi).days <= FINESTRA_CALENDARIO]
    usata = None
    if passate:
        p = passate[-1]
        fine = _fine_prima(p, griglia)
        base = ("ultima data dei risultati gia' passata %s (calendario del fornitore): pubblica il "
                "periodo chiuso il %s" % (p.isoformat(), fine.isoformat()))
        usata = p
    elif future:
        n = future[0]
        fine = _fine_prima(_fine_prima(n, griglia), griglia)
        base = ("prossima data dei risultati %s (calendario del fornitore, nessuna data passata utile): "
                "l'ultimo periodo gia' pubblicato e' quello prima" % n.isoformat())
    else:
        fine = _fine_prima(oggi - timedelta(days=GIORNI_TERMINE - 1), griglia)
        base = ("calendario dei risultati n.d.: regola dei %d giorni dopo la chiusura del trimestre "
                "(stima, non un annuncio)" % GIORNI_TERMINE)
    if griglia:
        base += "; griglia fiscale dall'ultima chiusura osservata %s" % griglia.isoformat()
    return {"periodo": fine.isoformat(), "base": base, "griglia": nome_griglia,
            "data_risultati": usata.isoformat() if usata else None,
            "prossimi_risultati": future[0].isoformat() if future else None,
            # senza una data futura nel calendario: il trimestre dopo l'atteso piu' GIORNI_TERMINE (stima,
            # serve solo a non tenere in cache un «aggiornato» oltre la probabile data dei risultati)
            "prossimi_risultati_stimati": None if future else
                (_fine_dopo(fine, griglia) + timedelta(days=GIORNI_TERMINE)).isoformat()}


def _copre(periodo: Optional[str], atteso: str) -> bool:
    p, a = _data(periodo), _data(atteso)
    return bool(p and a and p >= a - timedelta(days=TOLLERANZA_GIORNI))


# ------------------------------------------------------------------ tempo duro

class _TempoScaduto(Exception):
    pass


# Thread di rete scaduti (oltre il loro tempo) e ancora vivi: si tolgono da soli quando terminano.
_SCADUTI: set = set()
_SCADUTI_LOCK = threading.Lock()


def scaduti_appesi() -> int:
    with _SCADUTI_LOCK:
        for t in [t for t in _SCADUTI if not t.is_alive()]:
            _SCADUTI.discard(t)
        return len(_SCADUTI)


def _entro(fn: Callable[[], Any], scadenza: float, orologio: Callable[[], float] = time.monotonic) -> Any:
    """Esegue `fn` in un thread daemon e aspetta al massimo fino a `scadenza` (monotonic)."""
    resto = scadenza - orologio()
    if resto <= 0:
        raise _TempoScaduto("tempo della fase esaurito prima della chiamata")
    esito: Dict[str, Any] = {}

    def corpo():
        try:
            esito["ok"] = fn()
        except BaseException as exc:            # noqa: B902 - riportata al chiamante, mai persa
            esito["exc"] = exc
        finally:
            with _SCADUTI_LOCK:
                _SCADUTI.discard(threading.current_thread())   # terminato: non e' piu' appeso
    t = threading.Thread(target=corpo, daemon=True, name="freschezza-fonte")
    t.start()
    t.join(resto)
    if t.is_alive():
        # R-CASCATA 2a verifica: si contano solo i thread OLTRE la loro scadenza, non quelli al lavoro
        with _SCADUTI_LOCK:
            if t.is_alive():
                _SCADUTI.add(t)
        raise _TempoScaduto("fonte oltre il tempo massimo (%.0f s)" % max(resto, 0))
    if "exc" in esito:
        raise esito["exc"]
    return esito.get("ok")


def _motivo(exc: BaseException) -> str:
    # solo il tipo (e il testo nostro per il tempo): il testo di rete puo' portare URL con querystring
    if isinstance(exc, _TempoScaduto):
        return str(exc)
    return type(exc).__name__


# ------------------------------------------------------------------ fonti vive

class FontiVive:
    """Le fonti vere: SEC (ritmo e User-Agent di sec_edgar) e yfinance. Solo GET gratuite."""

    def cik(self, ticker: str):
        from bellomberg.market_data import sec_edgar
        ambiguo = sec_edgar.ticker_ambiguo_per_cik(ticker)
        if ambiguo:
            return None, ambiguo
        motivo: List[str] = []
        cik = sec_edgar.lookup_cik(ticker, motivo)
        return cik, ("; ".join(motivo) or None)

    def companyfacts(self, cik: str):
        from bellomberg.market_data import sec_xbrl
        esito: Dict[str, Any] = {}
        dati = sec_xbrl._fetch_companyfacts(cik, esito=esito)
        if isinstance(dati, dict):
            dati = dict(dati, _lettura=esito)
        return dati

    def _get(self, url: str, *, json_ok: bool):
        import requests
        from bellomberg.market_data import sec_edgar
        sec_edgar.attendi_sec()
        h = dict(sec_edgar._headers())
        if not json_ok:
            h["Accept"] = "text/html,text/plain,*/*"
        r = requests.get(url, headers=h, timeout=20)
        if r.status_code != 200:
            raise ConnectionError("HTTP %d" % r.status_code)
        if json_ok:
            return r.json()
        return r.content[:3_000_000].decode(r.encoding or "utf-8", errors="replace")

    def submissions(self, cik: str):
        return self._get("https://data.sec.gov/submissions/CIK%s.json" % cik, json_ok=True)

    def indice(self, cik: str, accession: str):
        url = "https://www.sec.gov/Archives/edgar/data/%d/%s/index.json" % (int(cik), accession.replace("-", ""))
        dati = self._get(url, json_ok=True)
        return [it.get("name") for it in ((dati.get("directory") or {}).get("item") or []) if it.get("name")]

    def documento(self, url: str):
        return self._get(url, json_ok=False)

    def fornitore(self, ticker: str):
        import yfinance as yf
        from bellomberg.market_data.consensus_estimates import provider_symbol
        simbolo = provider_symbol(ticker)
        tk = yf.Ticker(simbolo)
        out: Dict[str, Any] = {"simbolo": simbolo, "valuta": None, "trimestrali": {}, "date_risultati": []}
        for nome, attr in (("income", "quarterly_income_stmt"), ("balance", "quarterly_balance_sheet"),
                           ("cashflow", "quarterly_cashflow")):
            try:
                df = getattr(tk, attr)
            except Exception as exc:
                out.setdefault("errori", []).append("%s: %s" % (nome, type(exc).__name__))
                continue
            if df is None or getattr(df, "empty", True):
                continue
            tab: Dict[str, Dict[str, float]] = {}
            for col in df.columns:
                fine = str(col)[:10]
                riga = {}
                for nome_riga in _RIGHE_FORNITORE[nome]:
                    if nome_riga in df.index:
                        v = df.loc[nome_riga, col]
                        try:
                            v = float(v)
                        except (TypeError, ValueError):
                            continue
                        if v == v:                       # NaN escluso
                            riga[nome_riga] = v
                if riga:
                    tab[fine] = riga
            if tab:
                out["trimestrali"][nome] = tab
        try:
            ed = tk.get_earnings_dates(limit=12)
            if ed is not None and not ed.empty:
                for idx, row in ed.iterrows():
                    eps = row.get("Reported EPS")
                    try:
                        eps = float(eps)
                        eps = eps if eps == eps else None
                    except (TypeError, ValueError):
                        eps = None
                    out["date_risultati"].append({"data": str(idx)[:10], "eps_riportato": eps})
        except Exception as exc:
            out.setdefault("errori", []).append("date_risultati: %s" % type(exc).__name__)
        try:
            cal = tk.calendar
            grezze = cal.get("Earnings Date") if isinstance(cal, dict) else None
            for g in (grezze if isinstance(grezze, (list, tuple)) else [grezze] if grezze else []):
                if _data(g):
                    out["date_risultati"].append({"data": _data(g).isoformat(), "eps_riportato": None})
        except Exception as exc:
            out.setdefault("errori", []).append("calendario: %s" % type(exc).__name__)
        try:
            out["valuta"] = (tk.info or {}).get("financialCurrency")
        except Exception as exc:
            out.setdefault("errori", []).append("valuta: %s" % type(exc).__name__)
        return out


# ------------------------------------------------------------------ livelli

def _livello(n: int, stato: str, motivo: Optional[str] = None, **campi) -> Dict[str, Any]:
    return {"livello": n, "nome": NOMI[n], "stato": stato, "motivo": motivo, **campi}


def _livello_sec(facts: Any, atteso: str, oggi: date) -> Dict[str, Any]:
    from bellomberg.market_data.sec_xbrl import quarterly_history
    if not isinstance(facts, dict) or not isinstance(facts.get("facts"), dict):
        return _livello(1, "assente", "companyfacts non disponibile (risposta vuota o senza 'facts')")
    q = quarterly_history(facts["facts"], fino_al=oggi.isoformat())   # mai un deposito dopo il cutoff
    lp = q.get("latest_period")
    if not lp:
        return _livello(1, "assente", "companyfacts senza voci mappate da 10-Q/10-K/20-F")
    valori = {k: lp["values"][k] for k in _VALORI_SEC if k in lp["values"]}
    cumulati = lp.get("cumulati_da_inizio_esercizio_giorni") or {}
    note = ["%s: cumulato da inizio esercizio (%s giorni)" % (k, cumulati[k]) for k in valori if k in cumulati]
    lettura = facts.get("_lettura") or {}
    return _livello(1, "ok" if _copre(lp["period_end"], atteso) else "vecchio",
                    None if _copre(lp["period_end"], atteso) else
                    "ultimo periodo XBRL %s (%s %s) anteriore all'atteso %s" % (
                        lp["period_end"], lp["form"], lp.get("fp") or "", atteso),
                    period_end=lp["period_end"], filing_date=lp.get("filing_date"), form=lp.get("form"),
                    fp=lp.get("fp"), accession=lp.get("accession"), tipo=lp.get("tipo"),
                    etichetta=ETICHETTE[1].format(form=lp.get("form")), valori=valori,
                    metric_metadata={k: v for k, v in (lp.get("metric_metadata") or {}).items() if k in valori},
                    unita={k: v for k, v in (lp.get("units_non_usd") or {}).items() if k in valori} or None,
                    nota="; ".join(note) or None,
                    da_cache_sec=lettura.get("da_cache"), letto_il=lettura.get("letto_il"))


def _livello_filing(contesto: Any, atteso: str) -> Dict[str, Any]:
    if not isinstance(contesto, dict):
        return _livello(2, "assente", "fase documenti Filing non eseguita per questa chiamata (nessun contesto)")
    run = contesto.get("run_filing")
    if not isinstance(run, dict):
        return _livello(2, "assente", "archivio Filing senza run in questa fase: %s" % (
            contesto.get("motivo") or contesto.get("stato") or "motivo n.d."))
    periodo = run.get("ultimo_periodo")
    if _copre(periodo, atteso):
        return _livello(2, "ok", None, period_end=str(periodo)[:10], etichetta=ETICHETTE[2],
                        run_filing=run.get("id"), valori={},
                        nota="documenti nel dossier (read_company_dossier): numeri da leggere nel testo")
    return _livello(2, "vecchio" if periodo else "assente",
                    "archivio Filing: ultimo periodo %s, atteso %s (run %s, stato %s)" % (
                        periodo or "n.d.", atteso, run.get("id"), run.get("status")),
                    period_end=str(periodo)[:10] if periodo else None)


_MESI = ("january|february|march|april|may|june|july|august|september|october|november|december")
_FRASE_PERIODO = re.compile(
    r"(?:quarter|three[- ]months?|three[- ]month period|quarterly period|half[- ]year|six[- ]months?|"
    r"first half|second quarter|third quarter|fourth quarter|first quarter|fiscal year|year|period)"
    r"s?\s+(?:ended|ending)\s+(?:on\s+)?"
    r"(?:(" + _MESI + r")\s+(\d{1,2}),?\s+(\d{4})|(\d{1,2})\s+(" + _MESI + r"),?\s+(\d{4}))", re.I)
# R-CASCATA (07/10): un 6-K sul buyback («period ended ...») NON e' un comunicato dei risultati. Servono
# almeno due segnali diversi fra ricavi/vendite, utile/perdita netta, EPS, risultato operativo, «results».
_SEGNALI_RISULTATO = re.compile(
    r"\b(revenues?|net sales|total sales|net (?:income|profit|loss|earnings)|earnings per share|\bEPS\b|"
    r"operating (?:income|profit|result)|(?:financial|quarterly|half[- ]year|interim|annual) results|"
    r"net interest income)\b", re.I)
MIN_SEGNALI_RISULTATO = 2


def _e_comunicato_risultati(testo: str) -> bool:
    trovati = {re.sub(r"s$", "", " ".join(m.group(1).lower().split())) for m in _SEGNALI_RISULTATO.finditer(testo or "")}
    return len(trovati) >= MIN_SEGNALI_RISULTATO


_DOC_COMUNICATO = re.compile(r"ex[-_]?99|press|release|result|earn|pr\d|\dq\d|q\d", re.I)


def _testo(grezzo: str) -> str:
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", grezzo or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


_ORDINALI = {"first": 3, "1st": 3, "second": 6, "2nd": 6, "third": 9, "3rd": 9, "fourth": 12, "4th": 12}
_MESI_DA_INIZIO = {"three": 3, "six": 6, "nine": 9, "twelve": 12}
_FRASE_SOLARE = re.compile(r"\b(?:(first|1st|second|2nd|third|3rd|fourth|4th) quarter|"
                           r"first (three|six|nine|twelve) months|(first) half(?:[- ]year)?)(?: of)? (20\d\d)\b", re.I)


def _periodi_solari(testo: str) -> List[date]:
    """«second quarter of 2026», «first six months of 2026», «first half 2026»: chiusura del periodo
    SOLARE (vale solo con esercizio solare: chi chiama lo usa solo sulla griglia solare)."""
    out = []
    for m in _FRASE_SOLARE.finditer(testo):
        mese = (_ORDINALI.get((m.group(1) or "").lower()) or _MESI_DA_INIZIO.get((m.group(2) or "").lower())
                or (6 if m.group(3) else None))
        if mese:
            anno = int(m.group(4))
            out.append(date(anno, mese, calendar.monthrange(anno, mese)[1]))
    return out


def _periodi_nel_testo(testo: str) -> List[date]:
    mesi = {m: i + 1 for i, m in enumerate(_MESI.split("|"))}
    out = []
    for m in _FRASE_PERIODO.finditer(testo):
        try:
            if m.group(1):
                out.append(date(int(m.group(3)), mesi[m.group(1).lower()], int(m.group(2))))
            else:
                out.append(date(int(m.group(6)), mesi[m.group(5).lower()], int(m.group(4))))
        except (ValueError, KeyError):
            continue
    return out


def _livello_comunicato(fonti, cik: Optional[str], atteso: str, oggi: date, scadenza: float,
                        orologio, data_risultati: Optional[date] = None, solare: bool = True) -> Dict[str, Any]:
    if not cik:
        return _livello(3, "assente", "nessun CIK SEC: comunicati via EDGAR non interrogabili; "
                        + CIRCUITI_NON_COPERTI)
    sub = _entro(lambda: fonti.submissions(cik), scadenza, orologio)
    recenti = ((sub or {}).get("filings") or {}).get("recent") or {}
    forms = recenti.get("form") or []
    cand = []
    for i, form in enumerate(forms):
        try:
            depositato = _data(recenti["filingDate"][i])
            acc = recenti["accessionNumber"][i]
        except (KeyError, IndexError):
            continue
        items = (recenti.get("items") or [""] * len(forms))[i] or ""
        if depositato is None or depositato > oggi or depositato < _data(atteso):
            continue
        if form == "8-K" and "2.02" in items or form == "6-K":
            primario = (recenti.get("primaryDocument") or [""] * len(forms))[i]
            cand.append((depositato, form, acc, primario))
    cand.sort(reverse=True)
    if data_risultati is not None:
        # un'estera deposita molti 6-K: prima quelli vicini alla data dei risultati del calendario
        cand.sort(key=lambda c: abs((c[0] - data_risultati).days))
    if not cand:
        return _livello(3, "assente", "nessun 8-K Item 2.02 / 6-K depositato fra %s e %s; %s" % (
            atteso, oggi.isoformat(), CIRCUITI_NON_COPERTI))
    visti = []
    for depositato, form, acc, primario in cand[:MAX_ACCESSION]:
        nomi = _entro(lambda: fonti.indice(cik, acc), scadenza, orologio) or []
        docs = [n for n in nomi if re.search(r"\.(htm|html|txt)$", n, re.I) and "index" not in n.lower()
                and not n.endswith(acc + ".txt")]
        docs.sort(key=lambda n: (0 if _DOC_COMUNICATO.search(n) and n != primario else 1 if n == primario else 2))
        for nome in docs[:MAX_DOCUMENTI]:
            url = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (int(cik), acc.replace("-", ""), nome)
            testo = _testo(_entro(lambda: fonti.documento(url), scadenza, orologio) or "")
            periodi = [p for p in _periodi_nel_testo(testo) if p <= oggi]
            dedotto = False
            if not periodi and solare:
                periodi = [p for p in _periodi_solari(testo) if p <= oggi]
                dedotto = bool(periodi)
            if periodi and not _e_comunicato_risultati(testo):
                visti.append("%s %s: periodo %s ma non e' un comunicato dei risultati (segnali assenti)"
                             % (form, nome, max(periodi).isoformat()))
                continue
            visti.append("%s %s: %s" % (form, nome, max(periodi).isoformat() if periodi else "nessun periodo"))
            if periodi and _copre(max(periodi).isoformat(), atteso):
                fine = max(periodi)
                m = next((m for m in _FRASE_PERIODO.finditer(testo)), None)
                inizio = max(0, (m.start() if m else 0) - 200)
                etichetta = ETICHETTE[3].format(form=("8-K Item 2.02" if form == "8-K" else "6-K") + ", " + nome)
                return _livello(3, "ok", None, period_end=fine.isoformat(), filing_date=depositato.isoformat(),
                                form=form, accession=acc, url=url, etichetta=etichetta,
                                estratto=testo[inizio:inizio + MAX_ESTRATTO], valori={},
                                nota="numeri nel testo del comunicato (estratto): non sono campi strutturati"
                                     + ("; periodo dedotto da una frase senza data di chiusura (esercizio "
                                        "solare)" if dedotto else ""),
                                circuiti=CIRCUITI_NON_COPERTI)
    return _livello(3, "assente", "comunicati letti senza il periodo atteso %s: %s; %s" % (
        atteso, "; ".join(visti) or "nessun documento", CIRCUITI_NON_COPERTI))


def _livello_fornitore(forn: Any, atteso: str, oggi: date, griglia: Optional[date]) -> Dict[str, Any]:
    if not isinstance(forn, dict):
        return _livello(4, "assente", "fornitore dati senza risposta")
    tab = forn.get("trimestrali") or {}
    fini = sorted({f for t in tab.values() for f in t if (_data(f) or oggi) <= oggi and _data(f)})
    errori = "; ".join(forn.get("errori") or []) or None
    comuni = dict(filing_date=None, etichetta=ETICHETTE[4], valuta=forn.get("valuta") or "n.d.",
                  simbolo=forn.get("simbolo"), errori=errori)
    bilanci = None
    if fini and (tab.get("income") or {}):
        fini_ce = [f for f in fini if f in (tab.get("income") or {})]
        fine = fini_ce[-1] if fini_ce else fini[-1]
        valori = {}
        for t in ("income", "balance", "cashflow"):
            valori.update((tab.get(t) or {}).get(fine) or {})
        bilanci = (fine, valori)
        if _copre(fine, atteso):
            return _livello(4, "ok", None, period_end=fine, valori=valori, **comuni)
    # solo l'EPS riportato alla data dei risultati: periodo DEDOTTO dalla griglia, dichiarato.
    # Serve quando i bilanci trimestrali del fornitore mancano o sono piu' vecchi (misura 06/10: un
    # titolo giapponese con un solo trimestre del 2025 e l'EPS del 2026 alla data dei risultati).
    con_eps = sorted((d, r["eps_riportato"]) for r in forn.get("date_risultati") or []
                     if isinstance(r, dict) and r.get("eps_riportato") is not None
                     and (d := _data(r.get("data"))) and d <= oggi)
    if con_eps:
        d, eps = con_eps[-1]
        fine = _fine_prima(d, griglia).isoformat()
        nota = ("solo l'EPS riportato alla data dei risultati %s; periodo dedotto dalla data; bilanci "
                "trimestrali del fornitore %s" % (d.isoformat(), "fermi al " + bilanci[0] if bilanci else "assenti"))
        if _copre(fine, atteso) or not bilanci or fine > bilanci[0]:
            ok = _copre(fine, atteso)
            return _livello(4, "ok" if ok else "vecchio", None if ok else
                            "solo EPS del %s (periodo %s) anteriore all'atteso %s" % (d.isoformat(), fine, atteso),
                            period_end=fine, valori={"EPS riportato": eps}, nota=nota, **comuni)
    if bilanci:
        return _livello(4, "vecchio", "ultimo trimestre del fornitore %s anteriore all'atteso %s" % (
            bilanci[0], atteso), period_end=bilanci[0], valori=bilanci[1], **comuni)
    return _livello(4, "assente", "fornitore dati senza bilanci trimestrali ne' EPS riportato"
                    + ("; " + errori if errori else ""))


# ------------------------------------------------------------------ cascata

def cascata(ticker: str, *, as_of: Any, scadenza_monotonic: float, contesto: Any = None,
            fonti: Any = None, orologio: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    t0 = orologio()
    fonti = fonti if fonti is not None else FontiVive()
    oggi = _data(as_of)
    if oggi is None:
        raise ValueError("as_of non e' una data")
    livelli: Dict[int, Dict[str, Any]] = {}
    prelievi: Dict[str, Any] = {}

    def preleva(nome, fn):
        try:
            prelievi[nome] = {"ok": _entro(fn, scadenza_monotonic, orologio)}
        except BaseException as exc:            # noqa: B902
            prelievi[nome] = {"err": exc}
        return prelievi[nome]

    if isinstance(contesto, dict) and contesto.get("stato") == "non_applicabile":
        return {"contract": CONTRATTO, "ticker": ticker, "as_of": oggi.isoformat(), "stato": "non_applicabile",
                "motivo": "strumento senza bilancio societario: " + str(contesto.get("motivo") or "n.d."),
                "periodo_atteso": None, "periodo_trovato": None, "fonte_usata": None, "livelli": [],
                "lacuna": None, "secondi": round(orologio() - t0, 2)}
    # CIK: dal ticker; se rifiutato (suffisso senza alias) dal profilo Filing della fase
    cik, motivo_cik = None, None
    r = preleva("cik", lambda: fonti.cik(ticker))
    if "ok" in r and isinstance(r["ok"], tuple):
        cik, motivo_cik = r["ok"]
    elif "err" in r:
        motivo_cik = "CIK SEC: " + _motivo(r["err"])
    profilo = contesto.get("profilo") if isinstance(contesto, dict) else None
    if not cik and isinstance(profilo, dict) and profilo.get("cik"):
        cik = str(profilo["cik"]).zfill(10)
        motivo_cik = (motivo_cik or "") + " [CIK dal profilo Filing dell'emittente]"
    facts = preleva("companyfacts", lambda: fonti.companyfacts(cik)) if cik else None
    forn = preleva("fornitore", lambda: fonti.fornitore(ticker))
    # griglia: ultima chiusura osservata (SEC, poi fornitore) per gli esercizi non solari
    ancora = None
    if facts and "ok" in facts and isinstance(facts["ok"], dict):
        try:
            from bellomberg.market_data.sec_xbrl import quarterly_history
            lp = quarterly_history((facts["ok"] or {}).get("facts") or {}, fino_al=oggi.isoformat()).get("latest_period")
            ancora = lp["period_end"] if lp else None
        except Exception:
            ancora = None
    f_ok = forn.get("ok") if isinstance(forn.get("ok"), dict) else {}
    if ancora is None:
        fini = sorted(f for f in ((f_ok.get("trimestrali") or {}).get("income") or {}) if (_data(f) or oggi) <= oggi)
        ancora = fini[-1] if fini else None
    date_ris = [r.get("data") for r in f_ok.get("date_risultati") or [] if isinstance(r, dict)]
    atteso = periodo_atteso(oggi, date_risultati=date_ris, ancora=ancora)
    if "err" in forn:
        atteso["base"] += "; calendario del fornitore non letto (%s)" % _motivo(forn["err"])
    a = atteso["periodo"]
    griglia = _griglia_fiscale(_data(ancora))

    # 1. SEC companyfacts
    if not cik:
        livelli[1] = _livello(1, "assente", motivo_cik or "nessun CIK SEC")
    elif "err" in facts:
        livelli[1] = _livello(1, "tempo_scaduto" if isinstance(facts["err"], _TempoScaduto) else "errore",
                              "companyfacts: " + _motivo(facts["err"]))
    else:
        livelli[1] = _livello_sec(facts["ok"], a, oggi)
    # 2. archivio Filing (fase documenti)
    if livelli[1]["stato"] != "ok":
        livelli[2] = _livello_filing(contesto, a)
    # 3. comunicato dell'emittente
    if all(l["stato"] != "ok" for l in livelli.values()):
        try:
            livelli[3] = _livello_comunicato(fonti, cik, a, oggi, scadenza_monotonic, orologio,
                                             _data(atteso.get("data_risultati")), solare=griglia is None)
        except BaseException as exc:            # noqa: B902
            livelli[3] = _livello(3, "tempo_scaduto" if isinstance(exc, _TempoScaduto) else "errore",
                                  "comunicati EDGAR: " + _motivo(exc))
    # 4. fornitore dati
    if all(l["stato"] != "ok" for l in livelli.values()):
        if "err" in forn:
            livelli[4] = _livello(4, "tempo_scaduto" if isinstance(forn["err"], _TempoScaduto) else "errore",
                                  "fornitore dati: " + _motivo(forn["err"]))
        else:
            livelli[4] = _livello_fornitore(forn.get("ok"), a, oggi, griglia)
    for n in (1, 2, 3, 4):
        livelli.setdefault(n, _livello(n, "non_eseguito", "un livello precedente ha il periodo atteso"))
    usato = next((livelli[n] for n in (1, 2, 3, 4) if livelli[n]["stato"] == "ok"), None)
    lacuna = None
    if usato is None:
        lacuna = ("nessuna fonte ha il periodo atteso %s: " % a) + " | ".join(
            "L%d %s: %s" % (n, livelli[n]["stato"], livelli[n].get("motivo") or "") for n in (1, 2, 3, 4))
    scaduto = any(l["stato"] == "tempo_scaduto" for l in livelli.values())
    return {"contract": CONTRATTO, "ticker": ticker, "as_of": oggi.isoformat(),
            "stato": "aggiornato" if usato else ("tempo_scaduto" if scaduto else "lacuna"),
            "periodo_atteso": atteso, "periodo_trovato": usato.get("period_end") if usato else None,
            "fonte_usata": ({"livello": usato["livello"], "nome": usato["nome"], "etichetta": usato.get("etichetta")}
                            if usato else None),
            "cik": cik, "livelli": [livelli[n] for n in (1, 2, 3, 4)], "lacuna": lacuna,
            "secondi": round(orologio() - t0, 2)}


# ------------------------------------------------------------------ cache su disco

def _cartella() -> str:
    from bellomberg.core import paths
    return os.path.join(str(paths.DATA_DIR), "freschezza_cache")


def _file_cache(ticker: str) -> str:
    return os.path.join(_cartella(), re.sub(r"[^A-Za-z0-9._-]", "_", ticker.upper()) + ".json")


def _scrivi_cache(esito: Dict[str, Any]) -> None:
    try:
        os.makedirs(_cartella(), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_cartella(), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"scritto": time.time(), "esito": esito}, f, ensure_ascii=False, default=str)
            os.replace(tmp, _file_cache(esito["ticker"]))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except Exception as exc:
        print("[freschezza] cache non scritta per %s: %s" % (esito.get("ticker"), type(exc).__name__))


def _leggi_cache(ticker: str, oggi: date, ttl_s: float) -> Optional[Dict[str, Any]]:
    try:
        with open(_file_cache(ticker), "r", encoding="utf-8") as f:
            dati = json.load(f)
        esito = dati["esito"]
        if esito.get("stato") != "aggiornato":
            ttl_s = min(ttl_s, TTL_LACUNA_S)    # una lacuna o un tempo scaduto si riprovano prima
        if time.time() - float(dati["scritto"]) > ttl_s:
            return None
        if _data(esito.get("as_of")) and _data(esito["as_of"]) > oggi:
            return None                         # calcolata a un cutoff successivo: puo' vedere il futuro
        atteso = esito.get("periodo_atteso") or {}
        prossimi = _data(atteso.get("prossimi_risultati")) or _data(atteso.get("prossimi_risultati_stimati"))
        if prossimi and prossimi <= oggi:
            return None                         # nel frattempo e' passata una data dei risultati: ricalcola
        return dict(esito, _scritto=dati["scritto"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ------------------------------------------------------------------ interfacce

def passo_freschezza(ticker, *, as_of, scadenza_monotonic, contesto=None, fonti=None, stop=None):
    """Passo della fase documenti pre-R0 (APERTO-NUOVI): mai solleva, rispetta la scadenza."""
    try:
        esito = cascata(ticker, as_of=as_of, scadenza_monotonic=scadenza_monotonic,
                        contesto=contesto if isinstance(contesto, dict) else None, fonti=fonti)
    except BaseException as exc:                 # noqa: B902 - la run arriva in fondo sempre
        return {"contract": CONTRATTO, "ticker": ticker, "stato": "lacuna", "periodo_trovato": None,
                "fonte_usata": None, "livelli": [], "lacuna": "cascata non eseguita: " + _motivo(exc)}
    if esito.get("stato") in ("aggiornato", "lacuna", "tempo_scaduto") and not (stop is not None and stop.is_set()):
        _scrivi_cache(esito)          # dopo la fine della run (stop) non si scrive piu' nulla
    return esito


def _compatto(esito: Dict[str, Any], *, da_cache: bool, scritto: Optional[float] = None) -> Dict[str, Any]:
    usato = next((l for l in esito.get("livelli") or [] if l.get("stato") == "ok"), None)
    out = {"stato": esito.get("stato"),
           "periodo_atteso": (esito.get("periodo_atteso") or {}).get("periodo"),
           "base_periodo_atteso": (esito.get("periodo_atteso") or {}).get("base"),
           "da_cache": da_cache, "calcolato_il": (datetime.fromtimestamp(scritto).isoformat(timespec="seconds")
                                                  if scritto else datetime.now().isoformat(timespec="seconds")),
           "lacuna": esito.get("lacuna"),
           # R-CASCATA (07/10): livelli compatti (il motivo intero sta nella lacuna e nel checkpoint) e
           # SENZA date: la data del blocco e' solo quella del livello usato
           "livelli": [{k: v for k, v in (("livello", l.get("livello")), ("stato", l.get("stato")),
                                          ("motivo", (l.get("motivo") or "")[:160] or None))
                        if v is not None} for l in esito.get("livelli") or [] if l.get("stato") != "non_eseguito"]}
    if usato:
        out.update({k: usato.get(k) for k in ("livello", "etichetta", "period_end", "period_start", "period_type",
                                              "duration", "fiscal_year_label", "issuer_identity", "definition",
                                              "metric_metadata",
                                              "source_receipt", "observed_at", "tipo", "filing_date", "form",
                                              "fp", "accession", "url", "valori", "unita", "valuta", "nota",
                                              "estratto")
                    if usato.get(k) not in (None, {}, "")})
        out.setdefault("valori", {})
        out.setdefault("filing_date", None)
    else:
        out.update({"livello": None, "etichetta": None, "period_end": None, "filing_date": None, "valori": {}})
    return out


# R-CASCATA (07/10): budget della cascata chiamata dai tool (chat, weekly, desk). Un titolo al massimo
# TEMPO_TOOL_S; nella finestra di FINESTRA_BUDGET_S al massimo BUDGET_FINESTRA_S secondi vivi in tutto
# (27 titoli della weekly: ~7-14 s l'uno misurati, il resto dalla cache 12 h); oltre: lacuna dichiarata.
# I thread daemon scaduti non si possono uccidere: le GET hanno timeout propri (20-30 s); con
# MAX_THREAD_VIVI thread ancora appesi non se ne aprono altri.
FINESTRA_BUDGET_S = 1800
BUDGET_FINESTRA_S = 240
MAX_THREAD_VIVI = 6
_BUDGET: Dict[str, Any] = {"lock": threading.Lock(), "per_titolo": {}, "spesa": []}


def _budget_residuo() -> float:
    ora = time.monotonic()
    with _BUDGET["lock"]:
        _BUDGET["spesa"] = [(t, d) for t, d in _BUDGET["spesa"] if ora - t < FINESTRA_BUDGET_S]
        return BUDGET_FINESTRA_S - sum(d for _, d in _BUDGET["spesa"])


def _lacuna_tool(motivo: str) -> Dict[str, Any]:
    return {"stato": "lacuna", "lacuna": "ultimo periodo non calcolato: " + motivo, "livello": None,
            "period_end": None, "valori": {}, "da_cache": False}


def blocco_per_tool(ticker: str, *, oggi: Optional[date] = None, ttl_s: float = TTL_CACHE_S,
                    tempo_max_s: float = TEMPO_TOOL_S, fonti: Any = None, in_run: bool = False,
                    stop: Any = None, scadenza_massima: Optional[float] = None) -> Dict[str, Any]:
    """Blocco «ultimo_periodo_pubblicato» per get_financial_history / get_fundamentals. Mai solleva.

    `in_run` (main 07/10, regola PM «nessuna lacuna»): nelle run (weekly, Trade Idea) il tetto
    complessivo NON si applica; restano il tempo per titolo, il lock per titolo e il limite dei
    thread appesi. Il tetto complessivo vale solo per la chat interattiva."""
    try:
        oggi = oggi or date.today()
        salvato = _leggi_cache(ticker, oggi, ttl_s)
        if salvato is not None:
            return _compatto(salvato, da_cache=True, scritto=salvato.get("_scritto"))
        # R-CASCATA (07/10): un lock per titolo (i desk paralleli della weekly aspettano e leggono la
        # cache invece di ricalcolare) e un tetto complessivo di secondi vivi nella finestra.
        with _BUDGET["lock"]:
            lock = _BUDGET["per_titolo"].setdefault(ticker.upper(), threading.Lock())
        attesa = time.monotonic()
        if not lock.acquire(timeout=tempo_max_s):
            return _lacuna_tool("calcolo dello stesso titolo gia' in corso oltre %.0f s" % tempo_max_s)
        try:
            salvato = _leggi_cache(ticker, oggi, ttl_s)
            if salvato is not None:
                return _compatto(salvato, da_cache=True, scritto=salvato.get("_scritto"))
            appesi = scaduti_appesi()
            if appesi >= MAX_THREAD_VIVI and in_run:
                # nelle run si aspetta che si liberino, entro il tempo del titolo (regola PM: niente lacune)
                fino = attesa + tempo_max_s
                while appesi >= MAX_THREAD_VIVI and time.monotonic() < fino:
                    time.sleep(min(0.2, max(0.0, fino - time.monotonic())))
                    appesi = scaduti_appesi()
                if appesi >= MAX_THREAD_VIVI:
                    return _lacuna_tool("%d chiamate di rete precedenti ancora appese oltre il loro tempo dopo "
                                        "%.0f s di attesa: nessuna nuova chiamata finche' non terminano"
                                        % (appesi, tempo_max_s))
            elif appesi >= MAX_THREAD_VIVI:
                return _lacuna_tool("%d chiamate di rete precedenti ancora appese oltre il loro tempo: "
                                    "nessuna nuova chiamata finche' non terminano" % appesi)
            residuo = tempo_max_s if in_run else _budget_residuo()
            if residuo <= 1:
                return _lacuna_tool("tetto complessivo di %d s di cascata viva in %d min esaurito"
                                    % (BUDGET_FINESTRA_S, FINESTRA_BUDGET_S // 60))
            inizio = time.monotonic()
            try:
                scadenza = inizio + min(tempo_max_s, residuo, max(1.0, tempo_max_s - (inizio - attesa)))
                if scadenza_massima is not None:
                    scadenza = min(scadenza, scadenza_massima)
                esito = passo_freschezza(ticker, as_of=oggi.isoformat(), fonti=fonti, stop=stop,
                                         scadenza_monotonic=scadenza)
            finally:
                with _BUDGET["lock"]:
                    _BUDGET["spesa"].append((time.monotonic(), time.monotonic() - inizio))
            return _compatto(esito, da_cache=False)
        finally:
            lock.release()
    except BaseException as exc:                 # noqa: B902
        return {"stato": "lacuna", "lacuna": "ultimo periodo non calcolato: " + _motivo(exc),
                "livello": None, "period_end": None, "valori": {}}


PRERISCALDA_WORKER = 4
PRERISCALDA_MAX_S = 180.0      # tempo massimo complessivo del preriscaldamento (R-CASCATA N4)
_PRERISCALDAMENTI: List[Any] = []


def ferma_preriscaldamenti(attesa_s: float = 0.0) -> int:
    """Fine della run: ferma ogni preriscaldamento ancora vivo (nessun titolo nuovo, nessuna scrittura
    di cache, nessuna riga di log). Ritorna quanti erano ancora vivi."""
    vivi = 0
    for t, ferma in list(_PRERISCALDAMENTI):
        ferma.set()
        if t.is_alive():
            vivi += 1
            if attesa_s > 0:
                t.join(attesa_s)
    _PRERISCALDAMENTI[:] = [(t, f) for t, f in _PRERISCALDAMENTI if t.is_alive()]
    return vivi


def preriscalda(tickers: Any, *, as_of: Any = None, worker: int = PRERISCALDA_WORKER, fonti: Any = None,
                log: Optional[Callable[[str], None]] = None, stop: Any = None,
                tempo_max_s: float = PRERISCALDA_MAX_S) -> Dict[str, Any]:
    """Preriscaldamento della cache all'inizio di una run (main 07/10): i titoli della run in parallelo
    (pochi worker), cosi' i desk leggono dalla cache. Mai solleva: ogni esito e ogni guasto e' nel
    riepilogo, e un titolo che finisce in lacuna resta una lacuna DICHIARATA nel suo blocco."""
    from concurrent.futures import ThreadPoolExecutor
    riepilogo: Dict[str, Any] = {"titoli": 0, "aggiornati": [], "lacune": {}, "errori": {}, "non_eseguiti": []}
    stop = stop if stop is not None else threading.Event()
    fine = time.monotonic() + tempo_max_s
    try:
        oggi = _data(as_of) or date.today()
        lista = []
        for t in tickers or []:
            t = str(t or "").strip()
            if t and t.upper() not in {x.upper() for x in lista}:
                lista.append(t)
        riepilogo["titoli"] = len(lista)

        def uno(t):
            if stop.is_set() or time.monotonic() >= fine:
                return t, "non_eseguito", None
            try:
                return t, blocco_per_tool(t, oggi=oggi, fonti=fonti, in_run=True, stop=stop,
                                          scadenza_massima=fine), None
            except BaseException as exc:          # noqa: B902 - la run non cade mai per il preriscaldamento
                return t, None, exc
        with ThreadPoolExecutor(max_workers=max(1, int(worker)), thread_name_prefix="freschezza-pre") as ex:
            for t, b, exc in ex.map(uno, lista):
                if b == "non_eseguito":
                    riepilogo["non_eseguiti"].append(t)
                elif exc is not None:
                    riepilogo["errori"][t] = type(exc).__name__
                elif (b or {}).get("stato") == "aggiornato":
                    riepilogo["aggiornati"].append(t)
                else:
                    riepilogo["lacune"][t] = str((b or {}).get("lacuna") or "n.d.")[:200]
    except BaseException as exc:                  # noqa: B902
        riepilogo["errori"]["_preriscaldamento"] = type(exc).__name__
    if log is not None and not stop.is_set():
        try:
            log("  Freschezza: preriscaldamento %d titoli, aggiornati %d, lacune %d (%s), errori %d, non eseguiti "
                "per tempo massimo %d" % (riepilogo["titoli"], len(riepilogo["aggiornati"]), len(riepilogo["lacune"]),
                   ", ".join(sorted(riepilogo["lacune"]))[:300] or "nessuna", len(riepilogo["errori"]),
                   len(riepilogo["non_eseguiti"])))
        except Exception:
            pass
    return riepilogo


def preriscalda_in_background(tickers: Any, *, as_of: Any = None, log: Optional[Callable[[str], None]] = None,
                              fonti: Any = None) -> threading.Thread:
    """Il preriscaldamento in un thread daemon: la run non aspetta (i desk che chiedono un titolo in
    corso di calcolo aspettano il suo lock e leggono la cache)."""
    ferma = threading.Event()
    t = threading.Thread(target=preriscalda, args=(list(tickers or []),),
                         kwargs={"as_of": as_of, "log": log, "fonti": fonti, "stop": ferma},
                         daemon=True, name="freschezza-preriscaldamento")
    t.stop = ferma
    _PRERISCALDAMENTI.append((t, ferma))
    t.start()
    return t


def sezione_dossier(esito: Any) -> Optional[Dict[str, Any]]:
    """Riepilogo per il dossier della Trade Idea (desk e Capo): fonte, periodo, lacuna motivata."""
    if not isinstance(esito, dict):
        return None
    c = _compatto(esito, da_cache=False) if esito.get("livelli") is not None else {}
    return {"contract": CONTRATTO, "stato": esito.get("stato"),
            "periodo_atteso": (esito.get("periodo_atteso") or {}).get("periodo"),
            "base_periodo_atteso": (esito.get("periodo_atteso") or {}).get("base"),
            "periodo_trovato": esito.get("periodo_trovato"),
            "livello": c.get("livello"), "etichetta": c.get("etichetta"),
            "filing_date": c.get("filing_date"), "url": c.get("url"),
            "lacuna": esito.get("lacuna") or (esito.get("motivo") if esito.get("stato") not in
                                              ("aggiornato", "non_applicabile") else None),
            "livelli": c.get("livelli") or [],
            "istruzione": ("I numeri di questo periodo stanno nella ricevuta di get_financial_history e "
                           "get_fundamentals (chiave ultimo_periodo_pubblicato): chiama il tool col ticker e cita "
                           "[src: get_financial_history] o [src: get_fundamentals] con as_of = period_end. "
                           "L'etichetta della fonte va riportata (fornitore dati = non filing).")}


def as_of_freschezza(tool_name: str, blackboard) -> dict:
    """{"as_of": cutoff della run} per i due tool col blocco dell'ultimo periodo, {} altrimenti
    (get_valuation usa as_of con un altro contratto: non si tocca). Cutoff assente: {} = oggi, dichiarato
    dal blocco stesso (`calcolato_il`)."""
    if tool_name not in ("get_fundamentals", "get_financial_history"):
        return {}
    cutoff = ((getattr(blackboard, "data", None) or {}).get("_data_cutoff")
              if isinstance(getattr(blackboard, "data", None), dict) else None)
    return {"as_of": str(cutoff)[:10]} if cutoff else {}
