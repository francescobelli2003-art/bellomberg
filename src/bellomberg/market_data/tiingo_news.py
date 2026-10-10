"""
tiingo_news.py — Provider Tiingo News API (task #173, stack API a pagamento)

Richiede in .env:  TIINGO_API_KEY=...   (piano Power, $30/mo)
Docs: https://www.tiingo.com/documentation/news

Output normalizzato allo schema item di news_aggregator:
  {title, source, url, published_at, snippet, provider, _tickers}

NOTA: il tagging ticker di Tiingo e' US-centrico. Per i ticker EU (.MI/.L/.VI)
restano attivi i termini per NOME del negozio di news_aggregator
(data/news_search_terms.json); Tiingo si AGGIUNGE
come fonte di qualita' per US + feed mercato generale, non sostituisce tutto.
"""
from bellomberg.core.paths import PROJECT_ROOT
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

try:
    import requests
    REQ_OK = True
except ImportError:
    REQ_OK = False

try:  # carica .env anche in esecuzione standalone
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

TIINGO_KEY = os.environ.get("TIINGO_API_KEY", "")
BASE = "https://api.tiingo.com"

# ── SPENTA per decisione PM (10/10, Opus 5.5) ────────────────────────────────
# Abbonamento News API NON rinnovato: ogni giro tornava HTTP 403 «You do not have
# permission to access the News API» e il feed si dichiarava DEGRADATO per una fonte
# che nessuno paga piu'. Con la fonte spenta: NESSUNA chiamata HTTP, `last_status()`
# dice {"stato": "SPENTA", "motivo": ...} e news_aggregator la elenca in
# `fonti_spente()` (non in `providers_blocked()`: spenta non e' guasta). La chiave nel
# .env resta dov'e'. RIATTIVAZIONE = un interruttore: `TIINGO_NEWS_ENABLED=1` nel .env
# (letto all'import, quindi al prossimo avvio del backend). Pattern: reddit_news.FONTE_SPENTA.
# Il codice di rete resta, dormiente e coperto dai test (che accendono il flag).
_INTERRUTTORE = os.environ.get("TIINGO_NEWS_ENABLED", "").strip()
FONTE_SPENTA = _INTERRUTTORE != "1"
if _INTERRUTTORE not in ("", "0", "1"):
    # valore non valido: si resta SPENTI (il lato sicuro: nessuna spesa, nessun 403), ma si dice
    try:
        print("  [TIINGO] TIINGO_NEWS_ENABLED=%r non valido (attesi 0 o 1): fonte SPENTA"
              % _INTERRUTTORE[:20], flush=True)
    except (OSError, ValueError):
        pass
MOTIVO_SPENTA = ("abbonamento Tiingo News API non rinnovato (decisione PM 10/10); "
                 "riattivazione: TIINGO_NEWS_ENABLED=1 nel .env")
MOTIVO_SPENTA_EN = ("Tiingo News API subscription not renewed (PM decision 10/10); "
                    "to re-enable: TIINGO_NEWS_ENABLED=1 in .env")
if _INTERRUTTORE not in ("", "0", "1"):
    # 10/10 v2 (riserva 5): il valore sbagliato si legge anche in fonti_spente()/rotte, non
    # solo nella riga stampata all'import (che nel processo del backend nessuno guarda)
    MOTIVO_SPENTA = ("valore non valido: %r in TIINGO_NEWS_ENABLED (attesi 0 o 1); %s"
                     % (_INTERRUTTORE[:20], MOTIVO_SPENTA))
    MOTIVO_SPENTA_EN = ("invalid value: %r in TIINGO_NEWS_ENABLED (expected 0 or 1); %s"
                        % (_INTERRUTTORE[:20], MOTIVO_SPENTA_EN))


def tiingo_spenta() -> bool:
    """True se la fonte e' spenta per decisione (letto a ogni chiamata: i test lo cambiano)."""
    return bool(FONTE_SPENTA)


def tiingo_available() -> bool:
    """«Ha senso interrogarla?»: False anche da SPENTA (come reddit_available). Chi deve
    distinguere SPENTA da SENZA_CHIAVE chiede prima `tiingo_spenta()`."""
    return bool(REQ_OK and TIINGO_KEY and not FONTE_SPENTA)


# ── Tiingo DICHIARATO (04/10, B1 — Opus 5.5) ────────────────────────────────
# Fino a oggi `fetch_tiingo_news` tornava `[]` in silenzio su non-200 e su
# qualunque eccezione: un abbonamento scaduto (rinnovo ~11/10) sarebbe stato
# invisibile — nel news_feed.log la parola «tiingo» compariva 0 volte. Ora:
#   - una riga `  [TIINGO] ...` nel log per ogni esito non-live;
#   - `last_status()` = esito dell'ULTIMA chiamata in questo processo (anche
#     `live`: un 200 dopo un 401 «guarisce»), letto da news_aggregator;
#   - `motivo` (lista, forma finnhub_news) per chi chiama.
# La chiave viaggia in QUERYSTRING (`token=`): mai `str(e)` (per requests
# contiene l'URL intero), e corpo/motivo passano per `_maschera`.
PATH_NEWS = "/tiingo/news"
_STATO_LOCK = threading.Lock()
_ULTIMO_STATO: Optional[Dict[str, Any]] = None
_RE_TOKEN = re.compile(r"(token=)[^&\s\"']+", re.IGNORECASE)
# Review RV-N 04/10 (P2): un 200 con lista vuota e' «live», ma il feed GENERALE
# (tickers=None) in condizioni normali non e' mai vuoto (R1: ~600 articoli/giorno).
# Se a scadenza Tiingo rispondesse 200 [] il buco tornerebbe muto: dalla
# SOGLIA-esima chiamata generale consecutiva a 0 articoli lo stato diventa
# VUOTO_SOSPETTO (non-live). I per-ticker vuoti restano live (zero plausibile).
SOGLIA_VUOTI_GENERALI = 3
_VUOTI_GENERALI = 0


def _maschera(testo: str) -> str:
    """Toglie la chiave da un testo che esce dal modulo (log, motivo): sia il
    valore letterale (un corpo che la riecheggia) sia ogni `token=...`."""
    testo = testo or ""
    if TIINGO_KEY:
        testo = testo.replace(TIINGO_KEY, "***")
    return _RE_TOKEN.sub(r"\1***", testo)


def _log_riga(testo: str) -> None:
    """Riga ADDITIVA (pattern quiver_data): se stdout e' morto o rifiuta
    l'encoding si perde LEI, non il contratto verso il chiamante."""
    try:
        print("  [TIINGO] " + testo, flush=True)
    except (OSError, ValueError):
        pass


def _log_http(status: int, path: str, body: str) -> None:
    """`[TIINGO] HTTP <code> on <path>: <corpo>` — corpo su UNA riga, 120 char,
    chiave mascherata (il corpo potrebbe riecheggiarla)."""
    corpo = _maschera(" ".join((body or "")[:120].split()))
    _log_riga(f"HTTP {status} on {path}: {corpo}")


def _log_eccezione(e: Exception, path: str) -> None:
    """Timeout / rete giu' / JSON rotto: TIPO e path, MAI `str(e)` (l'URL con
    `token=` dentro)."""
    _log_riga(f"{type(e).__name__} on {path}")


def _segna_stato(stato: str, http: Optional[int] = None, n_item: int = 0) -> None:
    """Esito dell'ultima chiamata. `quando` e' solo un'ETICHETTA (wall-clock);
    l'eta' si misura su `mono` (time.monotonic, immune ai salti NTP)."""
    global _ULTIMO_STATO
    nuovo = {
        "stato": stato,
        "http": http,
        "path": PATH_NEWS,
        "quando": datetime.now().isoformat(timespec="seconds"),
        "mono": time.monotonic(),
        "n_item": n_item,
    }
    with _STATO_LOCK:
        _ULTIMO_STATO = nuovo


def last_status() -> Optional[Dict[str, Any]]:
    """Esito dell'ultima chiamata a `fetch_tiingo_news` in QUESTO processo.

    None = mai interrogata (chi legge NON deve dichiarare la fonte muta).
    Forma: {"stato": "live"|"HTTP_<code>"|"ERRORE_<Tipo>"|"RISPOSTA_INATTESA"|
    "SENZA_CHIAVE"|"VUOTO_SOSPETTO"|"SPENTA", "http": int|None, "path": "/tiingo/news", "quando": iso,
    "mono": float, "n_item": int}. Restituisce una COPIA.
    """
    with _STATO_LOCK:
        return dict(_ULTIMO_STATO) if _ULTIMO_STATO is not None else None


def reset_status() -> None:
    """Riporta a «mai interrogata» (per i test), contatore dei vuoti incluso."""
    global _ULTIMO_STATO, _VUOTI_GENERALI
    with _STATO_LOCK:
        _ULTIMO_STATO = None
        _VUOTI_GENERALI = 0


def _conta_vuoto_generale(n_item: int) -> int:
    """Feed generale live: +1 se vuoto, azzera se ha articoli. Ritorna il conteggio."""
    global _VUOTI_GENERALI
    with _STATO_LOCK:
        _VUOTI_GENERALI = _VUOTI_GENERALI + 1 if n_item == 0 else 0
        return _VUOTI_GENERALI


def _muto(motivo: Optional[List[str]], testo: str) -> None:
    if motivo is not None:
        motivo.append(_maschera(testo))


def fetch_tiingo_news(tickers: Optional[List[str]] = None,
                      days: int = 2, limit: int = 30,
                      motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """News taggate per ticker. tickers=None -> feed generale di mercato.

    I ticker vanno passati nel formato Tiingo (US plain, minuscolo: 'acme', 'beta').

    Ritorno INVARIATO (lista di item, `[]` su guasto). Il guasto pero' ora e'
    DICHIARATO: riga `[TIINGO]` nel log, `last_status()` aggiornato a OGNI
    chiamata, e — se il chiamante passa `motivo` — il perche' in coda alla
    lista. Lista vuota + `motivo` vuoto = zero MISURATO (200 senza articoli).
    SPENTA (10/10): nessuna rete, stato SPENTA dichiarato, motivo in coda.
    """
    if FONTE_SPENTA:
        _segna_stato("SPENTA")
        _muto(motivo, f"SPENTA: {MOTIVO_SPENTA} — {PATH_NEWS} non interrogato")
        return []
    if not REQ_OK:
        _segna_stato("ERRORE_ImportError")
        _muto(motivo, f"modulo requests non disponibile: {PATH_NEWS} non interrogato")
        return []
    if not TIINGO_KEY:
        _segna_stato("SENZA_CHIAVE")
        _muto(motivo, f"TIINGO_API_KEY mancante in .env: {PATH_NEWS} non interrogato")
        return []
    try:
        params = {
            "token": TIINGO_KEY,
            "limit": max(1, min(limit, 100)),
            "startDate": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
            "sortBy": "publishedDate",
        }
        if tickers:
            params["tickers"] = ",".join(t.lower().strip() for t in tickers if t)
        r = requests.get(f"{BASE}{PATH_NEWS}", params=params, timeout=12)
        if r.status_code != 200:
            _log_http(r.status_code, PATH_NEWS, r.text)
            _segna_stato(f"HTTP_{r.status_code}", http=r.status_code)
            _muto(motivo, f"HTTP {r.status_code} on {PATH_NEWS}: "
                          + " ".join((r.text or "")[:120].split()))
            return []
        dati = r.json()
        if not isinstance(dati, list):
            # RV-N P3: e' proprio il caso in cui Tiingo direbbe il perche'
            # («subscription expired»): il corpo esce, una riga, 120c, mascherato.
            corpo = _maschera(" ".join(str(dati)[:120].split()))
            _log_riga(f"risposta inattesa ({type(dati).__name__}) on {PATH_NEWS}: {corpo}")
            _segna_stato("RISPOSTA_INATTESA", http=200)
            _muto(motivo, f"{PATH_NEWS}: risposta inattesa ({type(dati).__name__}): {corpo}")
            return []
        out: List[Dict[str, Any]] = []
        for a in dati:
            out.append({
                "title": a.get("title", ""),
                "source": a.get("source", "Tiingo"),
                "url": a.get("url", ""),
                "published_at": a.get("publishedDate", ""),
                "snippet": (a.get("description") or "")[:500],
                "provider": "tiingo",
                "_tickers": [t.upper() for t in (a.get("tickers") or [])],
            })
        if not tickers:
            vuoti = _conta_vuoto_generale(len(out))
            if vuoti >= SOGLIA_VUOTI_GENERALI:
                testo = (f"feed generale a 0 articoli per {vuoti} chiamate consecutive "
                         f"on {PATH_NEWS}: abbonamento/piano da verificare")
                _log_riga(testo)
                _segna_stato("VUOTO_SOSPETTO", http=200, n_item=0)
                _muto(motivo, testo)
                return out
        _segna_stato("live", http=200, n_item=len(out))
        return out
    except Exception as e:
        _log_eccezione(e, PATH_NEWS)
        _segna_stato(f"ERRORE_{type(e).__name__}")
        _muto(motivo, f"{type(e).__name__} on {PATH_NEWS}")
        return []


if __name__ == "__main__":
    # smoke test: python tiingo_news.py [TICKER ...]
    import sys
    if tiingo_spenta():
        print("Tiingo News SPENTA: " + MOTIVO_SPENTA)
    elif not tiingo_available():
        print("TIINGO_API_KEY mancante in .env (o requests non installato)")
    else:
        # 04/09 (criterio (1)): i simboli di prova arrivano da riga di comando, non dal
        # sorgente (qui c'erano tre posizioni del PM). Default: un simbolo US qualsiasi.
        items = fetch_tiingo_news(sys.argv[1:] or ["AAPL"], days=2, limit=10)
        print(f"{len(items)} news")
        for it in items[:10]:
            print("-", it["published_at"][:16], "|", ",".join(it["_tickers"][:4]), "|", it["title"][:70])
