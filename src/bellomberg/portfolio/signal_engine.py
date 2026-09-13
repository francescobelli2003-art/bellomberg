"""
signal_engine.py — Motore di SEGNALI QUANTITATIVI OGGETTIVI (#181)

FILOSOFIA: gli agenti AI non devono INVENTARE le idee (un LLM produce consenso,
non alpha). Devono INTERPRETARE segnali oggettivi calcolati in Python da dati reali.
Questo modulo produce quei segnali; l'agente li ranka, spiega, traduce in azione.

Ogni segnale e' un dict normalizzato:
  {
    "ticker": str,
    "category": "volatility|positioning|momentum|factor|insider|risk",
    "name": str,                 # nome leggibile
    "value": float|str,          # valore grezzo
    "context": str,              # percentile / z-score / soglia
    "direction": "bullish|bearish|neutral|caution",
    "strength": int,             # 0-100, |intensita'| della dislocazione
    "reading": str,              # interpretazione 1-2 frasi, pronta per l'agente
    "source": str,               # tool/metodo (citabile)
  }

Tutti i segnali sono guarded: se un dato manca, il segnale viene saltato, mai
un'eccezione propagata. Fonti gia' costruite: vol_surface, positioning_tools,
quiver_data, portfolio_risk, portfolio_factors, yfinance.

Validazione: i segnali usati sono noti in letteratura/industria
(vol risk premium, COT extremes, dealer gamma, insider clustering, cross-sectional
momentum/mean-reversion). Il backtest formale e' il task #148.
"""
import copy
import threading
import time
from datetime import datetime
from typing import Dict, Any, List
from bellomberg.core.language import scoped_language
from bellomberg.core.presentation import error_text, join_messages, message, render_payload

try:
    import numpy as np
    NP_OK = True
except ImportError:
    NP_OK = False


# Ticker con opzioni US note: su questi gira la scansione PIENA anche se un
# domani portassero un suffisso di borsa. Il vecchio taglio `[:20]` per
# dimensione escludeva posizioni prima di consultare questo insieme.
US_OPTIONS = {"MSTR", "FSLR", "NU", "COIN", "PURR"}
# ⚠️ MISURATO col banco mutazioni il 22/08: oggi questo insieme e' INERTE —
# tutti e cinque i membri sono senza punto, quindi `"." not in tk` li instrada
# gia' da solo alla scansione piena, e togliere un elemento non cambia nulla.
# Serve al caso per cui e' nato (un nome con suffisso di borsa che ha comunque
# opzioni US) e quel ramo non e' mai stato esercitato in produzione. NON si
# cancella: un presidio che sembra inerte va tenuto con la sua ragione scritta.
# ⚠️ E anche allora la scansione NON sarebbe "tutti e 4 i rilevatori":
# `scan_ticker` salta il Congresso su qualunque ticker col punto (guardia che
# NON si tocca — e' la famiglia di quella che il 22/08 ha aperto BA.L -> Boeing).

_CAP_MOTIVO = 300


def _motivo_corto(msg, cap=_CAP_MOTIVO):
    """Un motivo di guasto DICHIARA la propria lunghezza invece di mangiarsi il
    payload. 22/08: `fattoriali_ko` era l'unico campo non limitato, stava PRIMA
    dei segnali e non era in nessuna leva di dimagrimento — un `{e}` da qualche
    KB (repr di un DataFrame, corpo HTML di `requests`) avrebbe spinto fuori le
    misure per salvare una stringa d'errore."""
    italian = str(render_payload(msg, language="it"))
    english = str(render_payload(msg, language="en"))
    def shorten(value, template):
        return value if len(value) <= cap else template % (value[:cap], len(value) - cap)
    return message(shorten(italian, "%s...[%d char non riportati]"),
                   shorten(english, "%s...[%d characters omitted]"))


class _DetectorOutcome(str):
    """Canonical status stays stable for coverage; display follows language."""
    def __new__(cls, code, reason=None):
        prefixes = {"queried": ("interrogato", "queried"), "muted": ("MUTO", "MUTED"),
                    "not_applicable": ("NON APPLICABILE", "NOT APPLICABLE")}
        it, en = prefixes[code]
        display = message(it + ": {reason}", en + ": {reason}", reason=reason) if reason is not None else message(it, en)
        result = str.__new__(cls, str(render_payload(display, language="it")))
        result.display = display
        return result


def _failure(error):
    return _DetectorOutcome("muted", _motivo_corto(message("{kind}: {reason}", "{kind}: {reason}",
                            kind=type(error).__name__, reason=error_text(error))))


def _detector_failures(failures):
    labels = {"vol risk premium": ("vol risk premium", "vol risk premium"),
              "dealer gamma": ("dealer gamma", "dealer gamma"),
              "z-score di prezzo": ("z-score di prezzo", "price z-score"),
              "cluster del Congresso": ("cluster del Congresso", "Congress cluster"),
              "scansione": ("scansione", "scan")}
    return join_messages("; ", [message("{name} {reason}", "{name} {reason}",
        name=message(*labels[key]) if key in labels else key,
        reason=getattr(value, "display", value)) for key, value in failures.items()])


def _sig(ticker, category, name, value, context, direction, strength, reading, source):
    return {"ticker": ticker, "category": category, "name": name, "value": value,
            "context": context, "direction": direction,
            "strength": int(max(0, min(100, strength))), "reading": reading, "source": source}


# ============================================================
# SEGNALI INDIVIDUALI (ognuno -> lista di dict, [] se non applicabile)
# ============================================================

@scoped_language
def sig_vol_risk_premium(ticker: str, esiti=None) -> List[Dict[str, Any]]:
    """IV vs realized vol + percentile 1y. Vol cara = vendi premio; a sconto = compra hedge.

    `esiti` (opzionale) e' il canale con cui il rilevatore DICE cosa gli e'
    successo; chi non lo passa ha il comportamento storico. Stessa forma del
    parametro `motivo` di `finnhub_news._api_get` (22/08), e per lo stesso
    motivo: `return []` da solo non distingue «ho guardato e non c'e' niente»
    da «non ho potuto guardare». Le fonti a monte non sollevano — tornano
    `{"error": ...}` — quindi senza questo canale il guasto e' invisibile.
    """
    def _dico(v):
        if esiti is not None:
            esiti["vol risk premium"] = v
    try:
        from bellomberg.portfolio.vol_surface import build_vol_surface
        vs = build_vol_surface(ticker, max_expiries=6)
        if vs.get("error"):
            _dico(_DetectorOutcome("muted", _motivo_corto(vs["error"])))
            return []
        _dico(_DetectorOutcome("queried"))
        out = []
        ivrv = vs.get("iv_rv_spread_front")
        rvp = vs.get("rv_percentile_1y")
        em = vs.get("expected_move_pct")
        emd = vs.get("expected_move_days")
        if ivrv is not None:
            sp = ivrv * 100
            if sp > 3:
                out.append(_sig(ticker, "volatility", message("Premio volatilità", "Vol Risk Premium"), f"{sp:+.1f}pt",
                    message("IV front > realized 30g", "front IV > 30d realized"), "bearish", min(100, 40 + sp * 4),
                    message("Le opzioni di {ticker} sono CARE: IV {spread:.1f} punti sopra la realizzata. Contesto da vendita di premio coperta (covered call), non da acquisto di protezione.",
                            "Options on {ticker} are EXPENSIVE: IV is {spread:.1f} points above realized volatility. Context for covered premium selling (covered call), not buying protection.", ticker=ticker, spread=sp),
                    "vol_surface IV-RV"))
            elif sp < -3:
                out.append(_sig(ticker, "volatility", message("Premio volatilità", "Vol Risk Premium"), f"{sp:+.1f}pt",
                    message("IV front < realized 30g", "front IV < 30d realized"), "caution", min(100, 40 + abs(sp) * 4),
                    message("Le opzioni di {ticker} sono A SCONTO: IV {spread:.1f} punti sotto la realizzata. L'hedge in put costa poco; vendere premio qui e' mal pagato.",
                            "Options on {ticker} are DISCOUNTED: IV is {spread:.1f} points below realized volatility. Put hedges are inexpensive; premium selling is poorly rewarded here.", ticker=ticker, spread=abs(sp)),
                    "vol_surface IV-RV"))
        if rvp is not None and (rvp >= 80 or rvp <= 20):
            d = "caution" if rvp >= 80 else "neutral"
            regime = message("regime compresso, possibile espansione", "compressed regime, possible expansion") if rvp <= 20 else message("regime elevato, tende a rientrare (mean reversion)", "elevated regime, tends to revert (mean reversion)")
            out.append(_sig(ticker, "volatility", message("Regime volatilità realizzata", "Realized Vol Regime"), message("{pct:.0f}° pct", "{pct:.0f}th pct", pct=rvp),
                message("percentile 1 anno", "1-year percentile"), d, abs(rvp - 50) * 2,
                message("La volatilita' realizzata di {ticker} e' al {pct:.0f}° percentile dell'ultimo anno: {regime}.",
                        "Realized volatility on {ticker} is at percentile {pct:.0f} over the last year: {regime}.", ticker=ticker, pct=rvp, regime=regime),
                "vol_surface RV percentile"))
        # Expected Move: CONTESTO informativo, non segnale azionabile -> forza contenuta
        # (sotto i segnali veri come vol risk premium / z-score). Solo se molto ampio.
        if em is not None and em > 12:
            out.append(_sig(ticker, "volatility", message("Movimento atteso", "Expected Move"), f"±{em:.1f}%",
                message("entro {days}g (1σ)", "within {days}d (1σ)", days=emd), "caution", int(min(60, 22 + em)),
                message("Il mercato opzioni prezza per {ticker} un movimento ampio: ±{move:.1f}% entro {days} giorni. Contesto da tenere a mente nel sizing, evento atteso.",
                        "Options price a large move for {ticker}: ±{move:.1f}% within {days} days. Context for sizing around an expected event.", ticker=ticker, move=em, days=emd),
                "vol_surface expected move"))
        return out
    except Exception as e:
        _dico(_failure(e))
        return []


@scoped_language
def sig_dealer_gamma(ticker: str, esiti=None) -> List[Dict[str, Any]]:
    """GEX dealer + gamma flip. Sotto il flip = momentum/amplificazione."""
    def _dico(v):
        if esiti is not None:
            esiti["dealer gamma"] = v
    try:
        from bellomberg.portfolio.positioning_tools import compute_gex
        g = compute_gex(ticker)
        if g.get("error"):
            _dico(_DetectorOutcome("muted", _motivo_corto(g["error"])))
            return []
        _dico(_DetectorOutcome("queried"))
        flip = g.get("gamma_flip_strike")
        spot = g.get("spot_est")
        net = g.get("net_gex_usd_per_1pct")
        if flip is None or spot is None:
            return []
        below = spot < flip
        return [_sig(ticker, "positioning", message("Gamma dealer (GEX)", "Dealer Gamma (GEX)"),
            f"flip {flip}, spot {spot}",
            f"net GEX {net/1e9:+.2f}B$/1%" if net else "",
            "caution" if below else "neutral", 70 if below else 35,
            (message("{ticker} tratta SOTTO il gamma flip ({flip}): i dealer amplificano i movimenti, regime momentum/instabile, attenzione alle accelerazioni.",
                     "{ticker} trades BELOW the gamma flip ({flip}): dealers amplify moves, an unstable momentum regime; watch for acceleration.", ticker=ticker, flip=flip) if below else
             message("{ticker} sopra il gamma flip ({flip}): dealer comprano i dip e vendono i rally, mercato compresso/mean-reverting.",
                     "{ticker} is above the gamma flip ({flip}): dealers buy dips and sell rallies, a compressed, mean-reverting market.", ticker=ticker, flip=flip)),
            "positioning_tools GEX")]
    except Exception as e:
        _dico(_failure(e))
        return []


@scoped_language
def sig_price_zscore(ticker: str, esiti=None) -> List[Dict[str, Any]]:
    """Z-score del prezzo vs media mobile 20/60g: mean-reversion / momentum estremo."""
    def _dico(v):
        if esiti is not None:
            esiti["z-score di prezzo"] = v
    if not NP_OK:
        _dico(_DetectorOutcome("muted", message("numpy non importabile", "numpy cannot be imported")))
        return []
    try:
        import yfinance as yf
        from bellomberg.cli.price_updater import data_ticker as _dt
        h = yf.Ticker(_dt(ticker)).history(period="6mo")["Close"].dropna()
        if len(h) < 60:
            _dico(_DetectorOutcome("muted", message("storico insufficiente ({days} giorni su 60 richiesti)", "insufficient history ({days} days out of 60 required)", days=len(h))))
            return []
        _dico(_DetectorOutcome("queried"))
        px = float(h.iloc[-1])
        ma20, sd20 = float(h.tail(20).mean()), float(h.tail(20).std())
        z = (px - ma20) / sd20 if sd20 > 0 else 0.0
        ret20 = (px / float(h.iloc[-21]) - 1) * 100 if len(h) >= 21 else 0.0
        if abs(z) < 1.5:
            return []
        direction = "bearish" if z > 0 else "bullish"  # estensione -> mean reversion
        side = message("sopra", "above") if z > 0 else message("sotto", "below")
        return [_sig(ticker, "momentum", message("Z-score prezzo (20g)", "Price Z-Score (20d)"), f"{z:+.1f}σ",
            message("{ret:+.1f}% in 20g", "{ret:+.1f}% in 20d", ret=ret20), direction, min(100, abs(z) * 30),
            message("{ticker} e' {z:.1f} deviazioni standard {side} la media a 20 giorni ({ret:+.1f}% nel periodo): statisticamente esteso, rischio di rientro verso la media.",
                    "{ticker} is {z:.1f} standard deviations {side} the 20-day average ({ret:+.1f}% over the period): statistically extended, with a risk of reverting to the mean.", ticker=ticker, z=abs(z), side=side, ret=ret20),
            "yfinance z-score")]
    except Exception as e:
        _dico(_failure(e))
        return []


@scoped_language
def sig_insider_congress(ticker: str, esiti=None) -> List[Dict[str, Any]]:
    """Cluster di acquisti del Congresso USA (Quiver) sul ticker."""
    def _dico(v):
        if esiti is not None:
            esiti["cluster del Congresso"] = v
    try:
        from bellomberg.market_data.quiver_data import quiver_available, get_congress_trades
        if not quiver_available():
            _dico(_DetectorOutcome("muted", message("Quiver non disponibile (chiave assente o non attiva)", "Quiver unavailable (key missing or inactive)")))
            return []
        r = get_congress_trades(ticker, limit=25)
        if r.get("error"):
            _dico(_DetectorOutcome("muted", _motivo_corto(r["error"])))
            return []
        _dico(_DetectorOutcome("queried"))
        if not r.get("trades"):
            return []
        trades = r["trades"]
        buys = [t for t in trades if "purchase" in str(t.get("Transaction", "")).lower()
                or "buy" in str(t.get("Transaction", "")).lower()]
        sells = [t for t in trades if "sale" in str(t.get("Transaction", "")).lower()]
        if len(buys) >= 2 and len(buys) > len(sells):
            names = ", ".join(sorted({str(t.get("Representative", "?")) for t in buys})[:4])
            return [_sig(ticker, "insider", message("Acquisti concentrati del Congresso", "Congress Cluster Buy"), message("{n} acquisti", "{n} purchases", n=len(buys)),
                message("vs {n} vendite", "vs {n} sales", n=len(sells)), "bullish", min(100, 40 + len(buys) * 12),
                message("Cluster di acquisti da parte di {n} membri del Congresso USA su {ticker} ({names}): segnale di smart-money dichiarato, storicamente predittivo.",
                        "Cluster of purchases by {n} US Congress members in {ticker} ({names}): a disclosed smart-money signal, historically predictive.", n=len(buys), ticker=ticker, names=names),
                "quiver congress trading")]
        if len(sells) >= 3 and len(sells) > len(buys) * 2:
            return [_sig(ticker, "insider", message("Vendite concentrate del Congresso", "Congress Cluster Sell"), message("{n} vendite", "{n} sales", n=len(sells)),
                message("vs {n} acquisti", "vs {n} purchases", n=len(buys)), "bearish", min(100, 30 + len(sells) * 8),
                message("Cluster di vendite da parte di {n} membri del Congresso su {ticker}: possibile de-risking degli insider politici.",
                        "Cluster of sales by {n} Congress members in {ticker}: possible de-risking by political insiders.", n=len(sells), ticker=ticker),
                "quiver congress trading")]
        return []
    except Exception as e:
        _dico(_failure(e))
        return []


@scoped_language
def sig_factor_flags(per_holding: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Esposizioni fattoriali estreme dal risultato Fama-French (gia' calcolato).

    ⚠️ PRESIDIO (review 22/08 sera): questa funzione legge SOLO beta_market e
    alpha_tstat, che vengono dalla regressione dei RENDIMENTI del ticker — non
    dai pesi. E' la ragione per cui la cache di `scan_portfolio` puo' usare
    `frozenset(tutti)` come chiave. Se un giorno un segnale qui dentro
    leggesse `weight_pct` (che in `per_holding` C'E'), la chiave diventerebbe
    disonesta in silenzio dopo ogni trade che cambia le quantita': in quel
    caso la chiave della cache VA CAMBIATA insieme a questo codice.
    """
    out = []
    try:
        for tk, h in (per_holding or {}).items():
            beta = h.get("beta_market")
            alpha_t = h.get("alpha_tstat")
            if beta is not None and beta > 1.4:
                out.append(_sig(tk, "factor", message("Beta di mercato elevato", "High Market Beta"), f"β {beta:.2f}",
                    message("Fama-French regionale", "regional Fama-French"), "caution", min(100, (beta - 1) * 60),
                    message("{ticker} ha beta di mercato {beta:.2f}: amplifica i movimenti dell'indice, esposizione direzionale elevata da considerare nel sizing.",
                            "{ticker} has market beta {beta:.2f}: it amplifies index moves, a high directional exposure to consider in sizing.", ticker=tk, beta=beta),
                    "portfolio_factors"))
            if alpha_t is not None and abs(alpha_t) >= 2.0:
                d = "bullish" if alpha_t > 0 else "bearish"
                sign = message("positivo", "positive") if alpha_t > 0 else message("negativo", "negative")
                out.append(_sig(tk, "factor", message("Alpha significativo", "Significant Alpha"), f"t {alpha_t:+.1f}",
                    message("alpha statisticamente significativo", "statistically significant alpha"), d, min(100, abs(alpha_t) * 30),
                    message("{ticker} mostra alpha {sign} statisticamente significativo (t={stat:+.1f}): rendimento non spiegato dai fattori, raro e degno di nota.",
                            "{ticker} shows statistically significant {sign} alpha (t={stat:+.1f}): returns unexplained by factors, rare and noteworthy.", ticker=tk, sign=sign, stat=alpha_t),
                    "portfolio_factors"))
    except Exception:
        pass
    return out


# ============================================================
# AGGREGATORI
# ============================================================

@scoped_language
def scan_ticker(ticker: str, include_congress: bool = True, esiti=None) -> List[Dict[str, Any]]:
    """Tutti i segnali per un singolo ticker, ordinati per forza.

    `esiti` raccoglie, per rilevatore, se ha risposto (`interrogato`), se e'
    stato muto (`MUTO: motivo`) o se non si applica a questo nome
    (`NON APPLICABILE: motivo`). Sono tre stati distinti apposta: un rilevatore
    che non si applica non e' un guasto, e un guasto non e' una misura.
    """
    sigs: List[Dict[str, Any]] = []
    sigs += sig_vol_risk_premium(ticker, esiti=esiti)
    sigs += sig_dealer_gamma(ticker, esiti=esiti)
    sigs += sig_price_zscore(ticker, esiti=esiti)
    if include_congress and "." not in ticker:
        sigs += sig_insider_congress(ticker, esiti=esiti)
    elif esiti is not None:
        esiti["cluster del Congresso"] = _DetectorOutcome("not_applicable",
            message("i trade del Congresso USA non coprono un ticker col punto nel simbolo", "US Congress trades do not cover a ticker containing a dot") if "." in ticker
            else message("escluso dal chiamante", "excluded by the caller"))
    sigs.sort(key=lambda s: -s["strength"])
    return sigs


# ------------------------------------------------------------------
# CACHE IN-PROCESS della scansione (22/08 sera, decisione (a) del PM).
# I numeri MISURATI (review 22/08 sera, `data/consigliere_run.log`): una run
# fa 3-4 scansioni nello stesso processo (2-3 dal desk quant + 1 diretta del
# Capo; le «21 chiamate» della voce di MASTER sono il TOTALE del log su NOVE
# run, non una) a 165,4s (caldo) / 381,4s (freddo) l'una — e F13 rilancia
# l'intera scansione a ogni click di soglia, mentre `min_strength` e' solo un
# filtro a valle. La cache conserva il materiale GREZZO (segnali non filtrati
# + copertura), cosi' soglie diverse condividono la stessa scansione: le 3-4
# scansioni di una run collassano in UNA (da ~8-25 min a 2,8-6,4 min misurati,
# secondo caldo/freddo) e i click di F13 diventano hit. ⚠️ I processi sono
# DUE: il consigliere gira come subprocess separato dall'API, quindi la run
# scalda la SUA cache, non quella che serve F13 — la prima apertura della
# pagina a backend freddo paga ancora la scansione intera.
# Pattern di casa: `benchmark_series._CACHE`. Scelte, tutte deliberate:
#   - TTL 3600s: una run del consigliere dura ~55' e la cache deve coprirla
#     intera; la leva per un refresh vero e' `force=True` (anche endpoint).
#   - Ogni risposta servita da cache DICHIARA la propria eta' (regola PM
#     14/07): campo `cache` nel payload, frase nella nota per il Capo.
#   - Si conserva e si serve una COPIA (deepcopy): chi muta la risposta non
#     avvelena le successive.
#   - I payload `{"error"}` non entrano in cache (nascono PRIMA del blocco).
#     Una scansione DEGRADATA (rilevatori muti, nomi senza misura, fattoriali
#     KO) SI' che entra, dichiarata — ma con TTL CORTO (decisione (g) del PM,
#     22/08 sera-2): un blackout di un minuto dei provider non resta servito
#     per un'ora. Il TTL e il suo motivo stanno nel payload (`cache.ttl_s`,
#     `cache.ttl_motivo`).
#   - Il lock serializza: due chiamate concorrenti = UNA scansione, la
#     seconda ASPETTA la prima (fino a ~6,4 min a freddo) e esce dalla cache.
#   - Orologio MONOTONICO per ts/TTL/eta' (time.monotonic): un NTP step-back
#     con time.time() produceva «eseguita -7199 secondi fa» nel prompt del
#     Capo e un hit prolungato di ore (misurato dalla review 22/08 sera).
_SCAN_CACHE: Dict[Any, Dict[str, Any]] = {}
SCAN_CACHE_TTL_SEC = 3600
SCAN_CACHE_TTL_DEGRADATA_SEC = 300
_SCAN_LOCK = threading.Lock()


def _ttl_della_scansione(grezzo: Dict[str, Any]):
    """Quanto conservare una scansione, e PERCHE' (decisione (g) del PM).
    Pulita -> TTL lungo, nessun motivo. Degradata (rilevatori muti, nomi
    senza misura, fattoriali KO) -> TTL corto e motivo dichiarato: il dato
    e' gia' dichiarato come degradato nella copertura, ma congelarlo un'ora
    trasformerebbe un blackout transitorio in un'ora di misure mancanti."""
    muti = len(grezzo["degradata"]) + len(grezzo["nessuna_misura"])
    ko = bool(grezzo["fattoriali_ko"])
    if not muti and not ko:
        return SCAN_CACHE_TTL_SEC, None
    pezzi = []
    if muti:
        pezzi.append(message("{n} nomi con rilevatori muti o senza misura", "{n} names with muted detectors or no measurement", n=muti))
    if ko:
        pezzi.append(message("fattoriali KO", "factor signals KO"))
    return SCAN_CACHE_TTL_DEGRADATA_SEC, message(
        "scansione DEGRADATA ({reasons}): TTL corto, si riprova allo scadere",
        "DEGRADED scan ({reasons}): short TTL, retry after expiry", reasons=join_messages("; ", pezzi))


def clear_scan_cache():
    """Svuota la cache della scansione (test/conftest; come benchmark_series)."""
    with _SCAN_LOCK:
        _SCAN_CACHE.clear()


@scoped_language
def scan_portfolio(max_tickers=None, min_strength: int = 45,
                   force: bool = False) -> Dict[str, Any]:
    """Edge Scanner: scansiona portfolio + factor flags, ritorna segnali RANKED.
    Solo dislocazioni con forza >= min_strength (le cose che contano davvero).

    Il vecchio limite implicito `max_tickers=20` non era esposto nello schema
    del tool o nell'endpoint: escludeva posizioni piccole anche in presenza
    di segnali forti. La copertura non deve dipendere dalla dimensione.
    Ora il default e' None = tutte; un tetto resta possibile ma si DICHIARA,
    con i nomi delle escluse e la via di recupero (regola PM 14/07).

    22/08 sera (decisione (a) del PM): il risultato GREZZO e' in cache
    in-process (v. `_SCAN_CACHE` qui sopra). `min_strength` resta un filtro a
    valle e non tocca la chiave; `force=True` re-interroga; un `max_tickers`
    esplicito scavalca la cache (seleziona in base all'ORDINE del libro, la
    chiave e' il SET) e il payload lo dichiara in `cache.attiva`.
    """
    try:
        from bellomberg.storage.memory_db import MemoryDB
        db = MemoryDB()
        snap = db.get_portfolio_summary()
        positions = snap.get("positions", [])
    except Exception as e:
        return {"error": message("lettura portafoglio: {reason}", "portfolio fetch: {reason}", reason=error_text(e)), "_timestamp": datetime.now().isoformat()}

    tutti = [p["ticker"] for p in positions]
    if not tutti:
        # CLAUDE.md, LEZIONI: «il backend ricrea un DB vuoto se non trova quello
        # vero: un DB "0 posizioni" = path sbagliato, non dati persi». Uno zero
        # da qui NON e' una misura, ed e' l'unico modo di non farlo sembrare tale.
        return {"error": message("nessuna posizione nel libro: il portafoglio e' vuoto o illeggibile, non e' uno zero misurato",
                                 "no positions in the book: the portfolio is empty or unreadable, not a measured zero"),
                "_timestamp": datetime.now().isoformat()}

    if max_tickers is None:
        # Percorso di produzione (tool, endpoint, Capo): libro intero, cache.
        # La chiave e' il SET dei ticker: l'ordine del libro segue il valore
        # EUR e puo' oscillare fra due letture, ma la scansione e' per-ticker
        # e non dipende dall'ordine. Un libro cambiato = chiave nuova.
        # ⚠️ La chiave e' onesta SOLO finche' i segnali dipendono dai soli
        # ticker: oggi e' cosi' (anche i factor flags usano beta/alpha dalla
        # regressione dei RENDIMENTI, non i pesi — v. presidio su
        # `sig_factor_flags`). Un segnale peso-dipendente futuro la renderebbe
        # disonesta in silenzio dopo un trade che cambia solo le quantita'.
        chiave = frozenset(tutti)
        with _SCAN_LOCK:
            entry = None if force else _SCAN_CACHE.get(chiave)
            # time.monotonic, MAI time.time: il wall-clock che arretra (NTP)
            # dava eta' negative dichiarate al Capo e TTL prolungati di ore.
            if entry is not None and time.monotonic() - entry["ts"] < entry["ttl"]:
                grezzo = copy.deepcopy(entry["raw"])
                eta_s = int(time.monotonic() - entry["ts"])
                ttl, ttl_motivo = entry["ttl"], entry["ttl_motivo"]
            else:
                grezzo = _scansione_grezza(tutti, tutti)
                # il TTL e' DELLA scansione (pulita = lungo, degradata = corto)
                # e viaggia con l'entry: un hit eredita il TTL della scansione
                # che serve, non quello di default.
                ttl, ttl_motivo = _ttl_della_scansione(grezzo)
                _SCAN_CACHE[chiave] = {"ts": time.monotonic(),
                                       "ttl": ttl, "ttl_motivo": ttl_motivo,
                                       "raw": copy.deepcopy(grezzo)}
                eta_s = None  # scansione fresca, eseguita adesso
        cache_info: Dict[str, Any] = {
            "attiva": True,
            "servita_da_cache": eta_s is not None,
            "ttl_s": ttl,
        }
        if ttl_motivo:
            cache_info["ttl_motivo"] = ttl_motivo
        if eta_s is not None:
            cache_info["eta_s"] = eta_s
            cache_info["scansione_delle"] = grezzo["generated"]
    else:
        # Un tetto esplicito seleziona in base all'ORDINE del libro: servirgli
        # la scansione piena dalla cache sarebbe rispondere a un'altra domanda.
        tickers = tutti[:int(max_tickers)]
        grezzo = _scansione_grezza(tickers, tutti)
        cache_info = {"attiva": False,
                      "servita_da_cache": False,
                      "motivo": message("max_tickers esplicito: scansione diretta, fuori cache", "explicit max_tickers: direct scan, cache bypassed")}

    return render_payload(_componi_payload(grezzo, tutti, min_strength, cache_info))


def _scansione_grezza(tickers: List[str], tutti: List[str]) -> Dict[str, Any]:
    """La parte COSTOSA (165-381s misurati): interroga i rilevatori per-ticker
    e i fattoriali, senza filtro di soglia. E' cio' che la cache conserva."""
    all_sigs: List[Dict[str, Any]] = []
    non_scansionate = tutti[len(tickers):]
    # ⚠️ Copertura MISURATA sull'ESITO di ogni rilevatore, non sul fatto che la
    # chiamata non abbia sollevato. La prima stesura (22/08) contava i TENTATIVI
    # e tre revisori su cinque l'hanno bocciata con la stessa prova: NESSUN
    # `sig_*` solleva mai (tutti chiudono con `except Exception: return []`) e le
    # fonti a monte tornano `{"error": ...}` — quindi il ramo `except` era
    # irraggiungibile, e con Polygon giu' il payload DICHIARAVA al Capo misure di
    # volatilita' mai fatte. Prima della cura l'assenza era muta; cosi' sarebbe
    # stata un'affermazione falsa all'agente che alloca.
    piena: List[str] = []
    degradata: Dict[str, str] = {}
    solo_prezzo: List[str] = []
    nessuna_misura: Dict[str, str] = {}
    for tk in tickers:
        esiti: Dict[str, str] = {}
        try:
            if tk in US_OPTIONS or "." not in tk:
                all_sigs += scan_ticker(tk, esiti=esiti)
            else:
                # fuori dagli USA l'unico rilevatore per-ticker applicabile e' il
                # prezzo: niente catene OPRA, niente trade del Congresso.
                all_sigs += sig_price_zscore(tk, esiti=esiti)
        except Exception as e:
            esiti.setdefault("scansione", _failure(e))
        risposti = [k for k, v in esiti.items() if v == "interrogato"]
        muti = {k: v for k, v in esiti.items() if str(v).startswith("MUTO")}
        if not risposti:
            nessuna_misura[tk] = _detector_failures(muti) or message("nessun rilevatore ha risposto", "no detector responded")
        elif muti:
            degradata[tk] = _detector_failures(muti)
        elif tk in US_OPTIONS or "." not in tk:
            piena.append(tk)
        else:
            solo_prezzo.append(tk)

    # Factor flags dal calcolo gia' cachato. E' una SECONDA strada, indipendente
    # dall'elenco qui sopra: copre anche i nomi eventualmente non scansionati.
    fattoriali_su = None
    fattoriali_ko = None
    try:
        from bellomberg.portfolio.portfolio_factors import compute_portfolio_factors
        ff = compute_portfolio_factors()
        # ⚠️ `compute_portfolio_factors` NON solleva sui guasti: torna
        # `{"error": ...}` in SEI rami (import falliti, portfolio fetch, no
        # positions, zero value, FF download KO, no holdings). Guardando solo
        # l'`except` il buco usciva come `fattoriali_su: 0`, cioe' un guasto
        # vestito da misura — la forma gemella di quella curata in `capo.py`,
        # riprodotta qui a 60 righe di distanza. Trovata dalla review 22/08.
        if isinstance(ff, dict) and ff.get("error"):
            fattoriali_ko = _motivo_corto(ff["error"])
        else:
            per_holding = (ff or {}).get("per_holding", {}) or {}
            fattoriali_su = len(per_holding)
            all_sigs += sig_factor_flags(per_holding)
    except Exception as e:
        fattoriali_ko = _motivo_corto(message("{kind}: {reason}", "{kind}: {reason}", kind=type(e).__name__, reason=error_text(e)))

    return {
        "generated": datetime.now().isoformat(),
        "all_sigs": all_sigs,
        "piena": piena,
        "degradata": degradata,
        "solo_prezzo": solo_prezzo,
        "nessuna_misura": nessuna_misura,
        "non_scansionate": non_scansionate,
        "fattoriali_su": fattoriali_su,
        "fattoriali_ko": fattoriali_ko,
    }


def _componi_payload(grezzo: Dict[str, Any], tutti: List[str],
                     min_strength: int,
                     cache_info: Dict[str, Any]) -> Dict[str, Any]:
    """La parte ECONOMICA, rifatta a ogni chiamata: filtro di soglia, conteggi,
    nota di copertura e dichiarazione della cache. `min_strength` vive QUI,
    a valle della cache: e' la ragione per cui soglie diverse condividono la
    stessa scansione."""
    all_sigs = grezzo["all_sigs"]
    piena = grezzo["piena"]
    degradata = grezzo["degradata"]
    solo_prezzo = grezzo["solo_prezzo"]
    nessuna_misura = grezzo["nessuna_misura"]
    non_scansionate = grezzo["non_scansionate"]
    fattoriali_su = grezzo["fattoriali_su"]
    fattoriali_ko = grezzo["fattoriali_ko"]

    strong = [s for s in all_sigs if s["strength"] >= min_strength]
    strong.sort(key=lambda s: -s["strength"])

    by_cat: Dict[str, int] = {}
    for s in strong:
        by_cat[s["category"]] = by_cat.get(s["category"], 0) + 1

    n_scan = len(piena) + len(degradata) + len(solo_prezzo) + len(nessuna_misura)
    n_tot = len(tutti)
    pezzi = [message("Scansionate {n} posizioni su {total}.", "Scanned {n} positions out of {total}.", n=n_scan, total=n_tot)]
    if piena:
        pezzi.append(message("Copertura PIENA (ogni rilevatore applicabile ha risposto) su {n} nomi: {names}.",
                             "FULL coverage (every applicable detector responded) for {n} names: {names}.", n=len(piena), names=", ".join(piena)))
    if solo_prezzo:
        pezzi.append(message(
            "Su {n} nomi l'unico rilevatore PER-TICKER applicabile e' lo z-score di "
            "prezzo ({names}): hanno un punto nel simbolo, cioe' non sono quotati negli "
            "USA, e li' le catene OPRA e i trade del Congresso non esistono — la loro "
            "assenza di segnali di volatilita' NON e' una misura, e' una copertura che "
            "manca. Le esposizioni fattoriali li coprono comunque, dall'altra strada.",
            "For {n} names the only applicable PER-TICKER detector is the price z-score ({names}): their symbols contain a dot, indicating a non-US listing without OPRA chains or Congress trades. Their lack of volatility signals is NOT a measurement: coverage is missing. Factor exposures still cover them through a separate path.",
            n=len(solo_prezzo), names=", ".join(solo_prezzo)))
    if degradata:
        pezzi.append(message("COPERTURA DEGRADATA su {n} nomi (un rilevatore non ha risposto): {reasons}.",
                             "DEGRADED COVERAGE for {n} names (a detector did not respond): {reasons}.", n=len(degradata),
                             reasons=join_messages("; ", [message("{ticker} [{reason}]", "{ticker} [{reason}]", ticker=k, reason=v) for k, v in degradata.items()])))
    if nessuna_misura:
        pezzi.append(message("NESSUNA misura su {n} nomi: {reasons}.", "NO measurement for {n} names: {reasons}.", n=len(nessuna_misura),
                             reasons=join_messages("; ", [message("{ticker} [{reason}]", "{ticker} [{reason}]", ticker=k, reason=v) for k, v in nessuna_misura.items()])))
    if non_scansionate:
        pezzi.append(message("NON scansionate ({n}): {names}.", "NOT scanned ({n}): {names}.", n=len(non_scansionate), names=", ".join(non_scansionate)))
    if fattoriali_su is not None:
        pezzi.append(message("Segnali fattoriali: strada separata dai rilevatori per-ticker, {n} nomi.",
                             "Factor signals: separate path from per-ticker detectors, {n} names.", n=fattoriali_su))
    elif fattoriali_ko:
        pezzi.append(message("Segnali fattoriali NON calcolati ({reason}): quel pezzo di copertura manca.",
                             "Factor signals NOT calculated ({reason}): this coverage is missing.", reason=fattoriali_ko))
    if cache_info.get("servita_da_cache"):
        # La frase arriva sia a F13 (copertura.nota) sia al prompt del Capo
        # (COPERTURA DELLA SCANSIONE). Dice il FATTO, vero in ogni stato in cui
        # viene emessa; NIENTE vie di recupero qui: il Capo non ha tool e F13
        # non manda `force` (lezione 21-22/08 su ask_specialist/get_edge_scan).
        # Accesso DIRETTO alle chiavi, niente `.get(x, 0)`: un default qui e'
        # la classe «scritture VERE: 0» — se le chiavi si disallineassero, la
        # frase direbbe «0 secondi fa» proprio nello stato anomalo. Meglio un
        # KeyError rumoroso. «Conclusa», non «eseguita»: l'eta' conta dalla
        # FINE della scansione (i dati sotto possono avere fino a ~6' in piu').
        pezzi.append(message("Risposta servita dalla cache in-process: scansione conclusa {age} secondi fa (TTL {ttl} s), i rilevatori non sono stati re-interrogati per questa risposta.",
                             "Response served from the in-process cache: scan completed {age} seconds ago (TTL {ttl} s); detectors were not queried again for this response.",
                             age=cache_info["eta_s"], ttl=cache_info["ttl_s"]))

    return {
        "generated": grezzo["generated"],
        "n_signals_total": len(all_sigs),
        "n_signals_strong": len(strong),
        "by_category": by_cat,
        "copertura": {
            "posizioni_totali": n_tot,
            "posizioni_scansionate": n_scan,
            "scansione_piena": piena,
            "scansione_degradata": degradata,
            "solo_prezzo": solo_prezzo,
            "nessuna_misura": nessuna_misura,
            "non_scansionate": non_scansionate,
            "fattoriali_su": fattoriali_su,
            "fattoriali_ko": fattoriali_ko,
            # ⚠️ `recupero` nomina un TOOL: e' per il percorso degli specialisti,
            # che i tool ce l'hanno. NON va iniettato nel prompt del Capo, che in
            # una run non ha tool (lezione del 21/08 sul red team e
            # `ask_specialist`, ripetuta il 22/08 e fermata dalla review).
            "recupero": (message(
                "get_position_doctor(ticker) rifa' i rilevatori su un singolo nome. "
                "⚠️ Con lo STESSO limite: su un ticker col punto il cluster del "
                "Congresso resta escluso e le catene OPRA non esistono, quindi su "
                "quei nomi torna lo stesso z-score che vedi qui — non una misura in "
                "piu'.", "get_position_doctor(ticker) reruns the detectors for one name. ⚠️ The SAME limit applies: for symbols containing a dot, Congress clusters remain excluded and OPRA chains do not exist, so those names return the same price z-score shown here, not an additional measurement.") if non_scansionate or degradata or nessuna_misura else ""),
            "nota": join_messages(" ", pezzi),
        },
        # PRIMA di `signals`: il taglio cieco del tool_result cade in coda, e
        # una dichiarazione che non sopravvive al taglio non dichiara niente.
        "cache": cache_info,
        "signals": strong,
        "_source": "signal_engine.scan_portfolio",
        "_note": message("Segnali oggettivi RANKED per forza. L'agente li interpreta e traduce in azione, non li inventa. Validazione formale: backtest #148.",
                         "Objective signals RANKED by strength. The agent interprets them and translates them into actions rather than inventing them. Formal validation: backtest #148."),
    }


@scoped_language
def position_doctor(ticker: str) -> Dict[str, Any]:
    """Position Doctor: diagnosi completa di UNA posizione con verdetto numerico."""
    sigs = scan_ticker(ticker)
    # Verdetto rule-based dai segnali (l'agente poi argomenta)
    score = 0
    for s in sigs:
        w = s["strength"] / 100.0
        if s["direction"] == "bullish":
            score += w
        elif s["direction"] == "bearish":
            score -= w
        elif s["direction"] == "caution":
            score -= w * 0.5
    verdict = (message("ADD/HOLD — segnali costruttivi", "ADD/HOLD — constructive signals") if score > 0.6 else
               message("TRIM/HEDGE — segnali di cautela prevalenti", "TRIM/HEDGE — caution signals dominate") if score < -0.6 else
               message("HOLD — segnali misti, nessun edge netto", "HOLD — mixed signals, no clear edge"))
    return {
        "ticker": ticker.upper(),
        "net_score": round(score, 2),
        "verdict": verdict,
        # dottrina bilaterale PM 16/07: il verdetto rule-based su segnali correnti e' un
        # INPUT per l'analista, non un ordine — su un titolo in drawdown va pesato coi
        # forward (fair value, livelli, tesi PM) prima di tradurlo in azione.
        "verdict_nota": message("verdetto rule-based dai segnali correnti: input da argomentare, non ordine",
                                "rule-based verdict from current signals: input requiring a rationale, not an order"),
        "n_signals": len(sigs),
        "signals": sigs,
        "_source": "signal_engine.position_doctor",
        "_timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) > 1:
        print(json.dumps(position_doctor(sys.argv[1]), indent=1, ensure_ascii=False)[:3000])
    else:
        r = scan_portfolio()
        print(f"Segnali totali: {r.get('n_signals_total')} | forti: {r.get('n_signals_strong')}")
        print(f"Per categoria: {r.get('by_category')}\n")
        for s in r.get("signals", [])[:15]:
            print(f"[{s['strength']:3d}] {s['ticker']:9s} {s['category']:11s} {s['name']}: {s['reading'][:90]}")
