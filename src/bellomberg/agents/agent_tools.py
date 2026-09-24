"""
Tools per il consigliere Opus agentico.

Ogni tool e' una funzione Python + uno schema JSON che descrive a Opus
quando e come usarlo. Opus deciche autonomamente quali tool chiamare
nel suo loop di ragionamento.

Tool disponibili:
1. search_news(query, max_results) - cerca news fresche su qualsiasi tema
2. get_market_data(ticker, period) - prezzi + indicatori tecnici (SMA, RSI, drawdown)
3. get_portfolio_state() - stato corrente del portafoglio dell'investitore
4. compare_assets(tickers, period) - performance relativa tra 2+ asset
5. read_recent_briefings(n) - legge gli ultimi N briefing daily per contesto storico
"""
import os
import json
import glob
from bellomberg.valuation.sector_analysis import method_records_schema

from bellomberg.core.config import TAVILY_API_KEY
from bellomberg.core.paths import MODELS_DIR
import requests as _req

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


# === TOOL SCHEMAS (formato richiesto da Anthropic SDK) ===
#
# ⚠️ REGISTRO LEGACY — FALLBACK-ONLY: **NON aggiungere qui i tool nuovi.**
# Il registro VIVO che serve gli specialisti del comitato e' `chat_tools.TOOL_DEFINITIONS`
# col subset per-agente di `chat_tools.get_tools_for_agent` (#195, 10/06).
# specialists/base.py:573 usa quello; questa lista la tocca SOLO se l'import di
# chat_tools fallisce (:574), cioe' in pratica mai.
# Le IMPLEMENTAZIONI (`tool_*`) e `TOOL_DISPATCHER` restano qui e sono vive: e' da qui
# che chat_tools importa. Quello che NON vive e' lo SCHEMA aggiunto solo qui.
# Costo gia' pagato (voce I-1, dossier audit/22): `get_yield_curves` e' stato costruito,
# documentato e ORDINATO nel prompt macro (specialists/macro.py:29) restando invisibile
# agli agenti per 3 run — perche' il commit lo aveva aggiunto QUI e basta.
# Regola: tool nuovo = schema in chat_tools.TOOL_DEFINITIONS + ramo di dispatch +
# nome nel subset dell'agente che lo deve usare. Tre posti, non uno.
TOOLS_SCHEMA = [
    {
        "name": "tavily_search",
        "description": "PRIMARY TOOL for verifying recent events. Searches the web in real-time via Tavily. USE THIS FIRST for any company, executive, market event you need to verify. Returns titles, URLs, and content snippets from top results. Works for English and Italian queries. PREFER THIS over search_news for current events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Be specific: 'Acme Bank nuovo CEO maggio 2026', 'Globex Holdings plc results this week', 'Solana SOL token latest'. Use Italian for Italian companies."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results (1-10, default 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_portfolio_live",
        "description": "GROUND TRUTH for portfolio composition - reads the LIVE INTERNAL DATABASE (SQLite consigliere.db, updated automatically by the user's trades, same source as the Dashboard and Performance pages, with live prices). Returns EXACT current weights (% Port), market values EUR, live prices, P/L EUR and P/L%. **ALWAYS use this for any weight/value/PnL claim**. Do NOT tell the user to edit any Excel file: the database is the source of truth and is updated when trades are logged. Never invent or estimate weights - call this and quote exactly. Every row also carries `tipo`: the vehicle's nature as DECLARED in the PM's vehicle registry (closed-end fund, ETF, crypto treasury, operating company, bank...), or `non dichiarato` when that symbol has no entry - a declared gap, never a guess from the ticker.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "search_news",
        "description": "Cerca news e articoli recenti su qualsiasi tema, ticker, o evento. Combina NewsAPI, Marketaux, TheNewsAPI, GNews e Yahoo Finance. Usa quando hai bisogno di approfondire un argomento, verificare una notizia, o cercare contesto specifico.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query di ricerca, es. 'bitcoin treasury company sell 2026', 'Acme Bank Globex merger', 'DEX perpetual exchange competition 2026'"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Numero massimo di news da restituire (1-20, default 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_market_data",
        "description": "Ottieni dati di mercato per uno strumento: prezzo corrente, variazione overnight/settimanale/mensile, indicatori tecnici (SMA20, SMA50, RSI14), distanza dai massimi a 52 settimane, volume. Usa per analizzare il momentum tecnico di una posizione o di un nuovo candidato.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker Yahoo Finance, es. 'AAPL', 'ENEL.MI', 'BTC-USD', '^GDAXI'"
                },
                "period": {
                    "type": "string",
                    "description": "Periodo storico: '1mo', '3mo', '6mo', '1y', '2y'. Default '3mo'",
                    "default": "3mo"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_portfolio_state",
        "description": "Stato del portafoglio DAL DB (fonte unica): posizioni attive con quantita', prezzo medio di carico, valuta, data di apertura, tesi e temi di monitoraggio, piu' la cassa da portfolio.json con la fonte dichiarata. SENZA prezzi di mercato: per valori, P&L e allocazione usa get_portfolio_live. Usa all'inizio per il contesto qualitativo (tesi e temi).",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "compare_assets",
        "description": "Confronta la performance relativa di 2 o piu' asset nello stesso periodo. Utile per: confrontare una posizione con i suoi competitor, valutare un'idea di switch (es. 'meglio MSFT o GOOGL?'), verificare correlazione storica.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista di ticker da confrontare, es. ['ENEL.MI', 'IBE.MC', 'NEE']"
                },
                "period": {
                    "type": "string",
                    "description": "Periodo: '1mo', '3mo', '6mo', '1y'. Default '3mo'",
                    "default": "3mo"
                }
            },
            "required": ["tickers"]
        }
    },
    {
        "name": "get_options_data",
        "description": "Get options market data for a US ticker: implied volatility (ATM), put/call open interest ratio, max pain strike, total OI, greeks ATM. Fonte primaria POLYGON (professionale, supporta il parametro expiry); fallback automatico IBKR TWS poi yfinance se Polygon non disponibile. Il campo 'data_source' dice quale fonte ha risposto. Solo ticker US.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "US ticker only (e.g. 'AAPL', 'TSLA', 'AMD')"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_13f_filing",
        "description": "Get the latest 13F-HR filing of a US institutional investor (top holdings, recent changes). Useful to track what well-known US filers (value, activist, macro and quant managers) are actually doing. Institutions are pre-mapped by slug: an unknown key gets a DECLARED error that lists the available slugs. NOT useful for European funds (no 13F obligation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "institution": {
                    "type": "string",
                    "description": "Slug of the institution, lowercase (unknown = declared error listing the available keys)"
                }
            },
            "required": ["institution"]
        }
    },
    {
        "name": "get_polymarket_events",
        "description": "Search Polymarket for prediction markets on political/geopolitical events. Polymarket is the leading info source for current probabilities on elections, conflicts, deals, etc. Returns yes_price (probability 0-1), volume, end_date. USE THIS for any political/election theme (a presidential runoff, US politics, a nuclear deal probability, Fed cut probability) - polls are lagging, prediction markets are leading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword, e.g. 'presidential election 2026', 'Iran nuclear deal 2026', 'Fed rate cut June 2026'"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max markets to return (1-10, default 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_fundamentals",
        "description": "Get fundamental data for a stock: PE, PB, market cap, revenue growth YoY, gross margin, operating margin, FCF, debt/equity, current ratio, ROE, ROA, analyst price target. CRITICAL for evaluating new position ideas (balance sheet quality, valuation). Works for US and major European stocks via yfinance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Yahoo Finance ticker, e.g. 'AAPL', 'ENEL.MI', 'RIO.L'"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_hyperliquid_intel",
        "description": "DEEP intelligence on the Hyperliquid DEX (official API): open interest, funding rates, 24h volumes and premium for the top perps by OI, funding extremes (long/short pressure), the HIP-3 builder dexs (TradFi 24/7 + pre-IPO) and the detail of ONE asset you name in focus_asset (no default: without it, top perps only, declared in focus_note). This is the native DEX data: use it for the tape of any perp quoted there, including the DEX's own token. For mNAV/treasury data of a Digital Asset Treasury that holds a token, use tavily_search with a query of the form '<DAT ticker> DAT mNAV <token> holdings': dedicated trackers exist and the search finds them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "focus_asset": {
                    "type": "string",
                    "description": "Opzionale: UN asset del dex principale da dettagliare (es. 'BTC', 'ETH', 'SOL'). Senza: nessun default, solo top perps, dichiarato in focus_note"
                }
            }
        }
    },
    {
        "name": "read_recent_briefings",
        "description": "Leggi gli ultimi N briefing giornalieri salvati. Utile per identificare temi ricorrenti, vedere cosa hai gia' analizzato la settimana scorsa, evitare ripetizioni.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Numero di briefing piu' recenti da leggere (1-7, default 3)",
                    "default": 1
                }
            }
        }
    }
]


# === IMPLEMENTAZIONI ===

def tool_search_news(query, max_results=10):
    """Cerca news multi-fonte via news_sources (il v1 fetcher.py e' in attic dal 02/09).

    Opus 4.8 15/07 (P0 skip dichiarato): il payload dichiara l'esito di OGNI fonte.
    Prima ritornava solo {query, count, news}: con la quota esaurita i provider tornavano
    [] muto e count=0 significava insieme "nessuna notizia" e "sono cieco". Peggio: yfinance
    NON ha limiter, quindi restava vivo e il giro sembrava sano mentre 3 fonti su 4 erano
    spente — un parziale travestito da completo, che e' l'inganno peggiore del vuoto.
    """
    try:
        from bellomberg.market_data.news_sources import (fetch_marketaux, fetch_thenewsapi, fetch_gnews,
                                  fetch_yfinance_news, reset_status, last_status)
    except ImportError as e:
        return {"error": "Modulo news non disponibile: " + str(e)}

    reset_status()
    risultati = []
    # Multi-fonte
    risultati.extend(fetch_marketaux(query=query, max_news=5))
    risultati.extend(fetch_thenewsapi(query=query, max_news=5))
    risultati.extend(fetch_gnews(query=query, max_news=15))  # Opus 4.8 16/07: GNews Essential real-time, bacino piu' ampio

    # Se sembra un ticker, prova anche yfinance
    _yf_tentato = len(query) <= 10 and (query.isupper() or "." in query)
    if _yf_tentato:
        risultati.extend(fetch_yfinance_news(query, max_news=5))

    # Dedup grezza
    visti = set()
    unici = []
    for n in risultati:
        chiave = (n.get("titolo", "") or "")[:50].lower().strip()
        if chiave and chiave not in visti:
            visti.add(chiave)
            unici.append({
                "titolo": n.get("titolo", ""),
                "fonte": n.get("fonte", ""),
                "descrizione": (n.get("descrizione", "") or "")[:300],
                "url": n.get("url", ""),
                "data": n.get("data", ""),
            })
        if len(unici) >= max_results:
            break

    # --- Opus 4.8 15/07: dichiarazione per-fonte (regola PM 14/07) --------------------
    # Vocabolario chiuso: live | SKIP_BUDGET | SKIP_COOLDOWN | SKIP_DISABLED | NO_KEY |
    # HTTP_<code> | ERROR | DISABLED/NO_LIB | non_interrogata. Nessuno stato inventato:
    # una fonte che non ha registrato nulla NON e' "ok", e' 'ignota' e va detto.
    _esiti = last_status()
    _fonti = {}
    for _f in ("marketaux", "thenewsapi", "gnews"):
        _fonti[_f] = _esiti.get(_f, "ignota")
    _fonti["yfinance"] = _esiti.get("yfinance", "non_interrogata" if not _yf_tentato else "ignota")
    _mute = sorted([f for f, s in _fonti.items() if s not in ("live", "non_interrogata")])
    _vive = sorted([f for f, s in _fonti.items() if s == "live"])
    # denominatore = le fonti CANDIDATE, non tutte: con una query non-ticker yfinance non
    # viene mai interrogata, e contarla direbbe "3 su 4" mentre copertura dice "NESSUNA".
    _candidate = [f for f, s in _fonti.items() if s != "non_interrogata"]

    out = {"query": query, "count": len(unici), "news": unici, "fonti": _fonti}
    if _mute:
        # NOMINARE chi e' degradato, come fa il pannello costi con gli agenti non prezzati.
        out["copertura"] = "PARZIALE" if _vive else "NESSUNA"
        out["fonti_mute"] = _mute
        out["avviso"] = (
            "COPERTURA DEGRADATA: %d fonti su %d interrogate non hanno risposto (%s). Il "
            "numero di news qui sotto NON misura il flusso reale: e' cio' che resta delle "
            "fonti vive (%s). NON interpretare 'poche notizie' come 'mercato tranquillo'."
            % (len(_mute), len(_candidate), ", ".join("%s=%s" % (f, _fonti[f]) for f in _mute),
               ", ".join(_vive) or "nessuna")
        )
    return out


def tool_get_market_data(ticker, period="3mo"):
    """Prezzo + indicatori tecnici via yfinance."""
    if not YFINANCE_AVAILABLE:
        return {"error": "yfinance non disponibile"}

    try:
        # alias broker->dati (suffisso del broker -> suffisso yfinance): fonte unica in price_updater
        from bellomberg.cli.price_updater import data_ticker
        ticker_data = data_ticker(ticker)
        tk = yf.Ticker(ticker_data)
        hist = tk.history(period=period)
        if len(hist) < 20:
            return {"error": "Dati insufficienti per " + ticker}

        import numpy as np
        close = hist['Close'].replace([np.inf, -np.inf], np.nan).dropna()
        close = close[close > 0]
        if len(close) < 20:
            return {"error": "Dati di prezzo validi insufficienti per " + ticker}
        volume = hist['Volume']

        prezzo = float(close.iloc[-1])
        prezzo_5d = float(close.iloc[-6]) if len(close) >= 6 else None
        prezzo_20d = float(close.iloc[-21]) if len(close) >= 21 else None
        prezzo_60d = float(close.iloc[-61]) if len(close) >= 61 else None

        sma20 = float(close.tail(20).mean())
        sma50 = float(close.tail(50).mean()) if len(close) >= 50 else None

        massimo = float(close.max())
        minimo = float(close.min())
        # audit/11 §4: lo schema promette "distanza dai massimi a 52 SETTIMANE" ma il max
        # era sul periodo richiesto (default 3mo): il 52w si calcola su storia 1y dedicata.
        dist_max_note = None
        try:
            if period == "1y":
                massimo_52w = massimo
            else:
                h1y = tk.history(period="1y")
                annual = h1y["Close"].replace([np.inf, -np.inf], np.nan).dropna()
                annual = annual[annual > 0]
                if annual.empty:
                    raise ValueError("nessun prezzo valido nella storia 1y")
                massimo_52w = float(annual.max())
        except Exception as error:
            massimo_52w = None
            dist_max_note = "Massimo 52 settimane n.d.: " + str(error)

        # RSI 14
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = float((100 - (100 / (1 + rs))).iloc[-1]) if not gain.empty else None

        vol_medio = float(volume.tail(20).mean())
        vol_oggi = float(volume.iloc[-1])

        def pct(a, b):
            return round((a - b) / b * 100, 2) if b else None

        return {
            "ticker": ticker,
            "px": round(prezzo, 4),
            "var_1d": pct(prezzo, float(close.iloc[-2])) if len(close) >= 2 else None,
            "var_20d": pct(prezzo, prezzo_20d) if prezzo_20d else None,
            "var_60d": pct(prezzo, prezzo_60d) if prezzo_60d else None,
            "vs_sma20_pct": pct(prezzo, sma20),
            "vs_sma50_pct": pct(prezzo, sma50) if sma50 else None,
            "rsi14": round(rsi, 1) if rsi is not None and np.isfinite(rsi) else None,
            "dist_max_pct": pct(prezzo, massimo_52w),
            "dist_max_note": dist_max_note,
            "price_asof": str(close.index[-1]),
            "source": "yfinance daily Close (not an intraday quote)",
            "vol_ratio_20d": round(vol_oggi / vol_medio, 2) if np.isfinite(vol_oggi) and np.isfinite(vol_medio) and vol_medio > 0 else None,
        }
    except Exception as e:
        return {"error": "Errore market data " + ticker + ": " + str(e)}


def tool_get_portfolio_state():
    """Stato del portafoglio DAL DB + cassa da portfolio.json, fonte dichiarata.

    B5 (02/09, decisione PM): prima restituiva il portfolio.json INTERO, comprese
    le liste etf/azioni dello schema di maggio. Tool DORMIENTE del registro legacy
    (audit/24:163, audit/25:320: nessun desk lo riceve dal registro vivo di
    chat_tools) — bonificato perche' pubblico e perche' e' l'unico canale che
    porta tesi e temi_monitoraggio (regola di casa: portafoglio = SOLO il DB).
    Senza prezzi di mercato: per valori, P&L e allocazione c'e' get_portfolio_live.

    Lettura in mode=ro (pattern current_facts/cef_lookthrough): un lettore puro non
    CREA mai un DB vuoto su un percorso sbagliato — file assente = errore dichiarato
    col percorso (lezione «0 posizioni = path sbagliato»). Dichiarazioni in TESTA e
    `positions` per ULTIMA: il tetto dei tool result taglia in coda (misura 02/09 sul
    book vero: 15.178 char > 12.000, cadevano proprio cassa e nota), e se sfora lo dice.
    """
    import sqlite3 as _sq
    from bellomberg.storage.memory_db import SQLITE_PATH, leggi_cassa_portfolio
    try:
        cx = _sq.connect("file:" + SQLITE_PATH.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            cx.row_factory = _sq.Row
            rows = cx.execute(
                "SELECT ticker, nome, quantita, prezzo_medio, valuta, data_apertura, tesi, "
                "temi_monitoraggio FROM positions WHERE is_active=1 AND quantita > 0 "
                "ORDER BY quantita * COALESCE(prezzo_medio, 0) DESC, ticker").fetchall()
        finally:
            cx.close()
    except Exception as e:
        return {"error": "posizioni dal DB non lette (%s): %s: %s"
                         % (SQLITE_PATH, type(e).__name__, e)}
    positions = []
    for r in rows:
        temi = r["temi_monitoraggio"]
        if isinstance(temi, str):
            try:
                temi = json.loads(temi)
            except ValueError:
                temi = [temi]   # testo libero non JSON: si passa com'e', dentro una lista
        if not isinstance(temi, list):
            temi = [temi] if temi not in (None, "", []) else []
        positions.append({"ticker": r["ticker"], "nome": r["nome"], "quantita": r["quantita"],
                          "prezzo_medio": r["prezzo_medio"], "valuta": r["valuta"],
                          "data_apertura": r["data_apertura"], "tesi": r["tesi"],
                          "temi_monitoraggio": temi})
    cassa = leggi_cassa_portfolio()
    out = {"source": "SQLite DB: positions + cash_state atomico",
           "cash_disponibile_eur": cassa["cash_eur"], "cash_source": cassa["cash_source"],
           "cash_source_note": cassa["cash_source_note"],
           "note": "senza prezzi di mercato: per valore, P&L e allocazione usa get_portfolio_live",
           "n_positions": len(positions)}
    if not positions:
        out["hint"] = ("Nessuna posizione attiva nel DB: si aprono dalla pagina Inserimento operazioni, F16 "
                       "(VERSAMENTO, poi BUY), o con tools/ops/importa_trade_csv.py")
    out["positions"] = positions          # ultima: se il tetto taglia, taglia qui
    try:
        from bellomberg.agents.chat_tools import TETTO_TOOL_RESULT as _tetto   # fonte unica del tetto
    except Exception:
        _tetto = 12000
    peso = len(json.dumps(out, default=str))
    if peso > _tetto:
        out = {"_vista": ("ATTENZIONE: payload %d char > tetto %d: le posizioni in coda "
                          "(le piu' piccole per costo) saranno TAGLIATE a valle" % (peso, _tetto)),
               **out}
    return out


def tool_compare_assets(tickers, period="3mo"):
    """Performance relativa di N asset nello stesso periodo."""
    if not YFINANCE_AVAILABLE:
        return {"error": "yfinance non disponibile"}

    risultati = []
    # audit/11 §4: stesso alias broker->dati di get_market_data (prima i ticker mappati
    # sparivano dal confronto senza errore)
    from bellomberg.cli.price_updater import data_ticker
    for ticker in tickers:
        try:
            tk = yf.Ticker(data_ticker(ticker))
            hist = tk.history(period=period)
            if len(hist) < 2:
                risultati.append({"ticker": ticker, "error": "dati insufficienti"})
                continue
            close = hist['Close']
            primo = float(close.iloc[0])
            ultimo = float(close.iloc[-1])
            var_periodo = round((ultimo - primo) / primo * 100, 2) if primo else None
            # audit/11 §5: le crypto hanno ~365 bar/anno — sqrt(252) sottostimava la
            # loro vol di ~17% proprio nella tabella di confronto cross-asset
            _tu = ticker.upper()
            _ann = 365 if (_tu.endswith("-USD") or _tu.endswith("-EUR")) else 252
            volatilita = round(float(close.pct_change().std() * (_ann ** 0.5) * 100), 2)

            risultati.append({
                "ticker": ticker,
                "prezzo_iniziale": round(primo, 4),
                "prezzo_finale": round(ultimo, 4),
                "performance_periodo_pct": var_periodo,
                "volatilita_annualizzata_pct": volatilita,
            })
        except Exception as e:
            risultati.append({"ticker": ticker, "error": str(e)})

    return {"period": period, "comparison": risultati}


def tool_read_recent_briefings(n=3):
    """Legge gli ultimi N briefing dalla cache VIVA di briefing_engine.
    audit/11 §4: prima leggeva report/briefing_*.md, pattern scritto solo dal briefing v1
    (oggi in attic/): serviva al Capo file fermi al 25/05 come 'ultimi giornalieri'."""
    try:
        from bellomberg.cli.briefing_engine import _load_cache
        cache = _load_cache()
        entries = [v for k, v in cache.items()
                   if not str(k).startswith("_") and isinstance(v, dict) and v.get("briefing_md")]
        entries.sort(key=lambda e: e.get("generated_at") or "", reverse=True)
        briefings = [{
            "period": e.get("period"),
            "generated_at": e.get("generated_at"),
            "contenuto": (e.get("briefing_md") or "")[:800],
        } for e in entries[:max(int(n), 1)]]
        out = {"count": len(briefings), "briefings": briefings}
        if not briefings:
            out["note"] = "cache briefing vuota (data/briefing_cache.json)"
        return out
    except Exception as e:
        return {"error": str(e)}



def tool_tavily_search(query, max_results=5):
    """Cerca sul web via Tavily API. Real-time, no delay."""
    if not TAVILY_API_KEY:
        return {"error": "TAVILY_API_KEY non configurata nel .env. Registrati su tavily.com"}
    try:
        r = _req.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": min(max_results, 10),
                "include_answer": True,
            },
            timeout=15,
        )
        if r.status_code != 200:
            return {"error": "Tavily HTTP " + str(r.status_code) + ": " + r.text[:200]}
        data = r.json()
        risultati_clean = []
        for res in data.get("results", []):
            risultati_clean.append({
                "title": res.get("title", "")[:200],
                "url": res.get("url", ""),
                "content": res.get("content", "")[:600],
                "score": res.get("score"),
                # audit 11/09 (run 10/09 memo #53): il «35% di rialzo Fed» preso dal web e'
                # finito in tabella scenari senza data; Tavily la data la manda e qui si buttava
                "published_date": res.get("published_date"),
            })
        return {
            "query": query,
            "answer": data.get("answer", ""),
            "results": risultati_clean,
            "nota": ("published_date = data di pubblicazione dichiarata dalla fonte (None = non "
                     "fornita): un numero preso dal web vale alla sua data — cita il vintage, "
                     "non presentarlo come quota corrente"),
        }
    except Exception as e:
        return {"error": "Tavily error: " + str(e)}



def _get_ibkr_options(ticker, port=7496, expiry=None):
    """
    Prova IBKR TWS / IB Gateway locale, richiedendo dati delayed-frozen.
    Richiede:
      - TWS o IB Gateway in esecuzione su localhost
      - API abilitata in Configure -> Settings -> API -> Enable ActiveX and Socket Clients
      - Port 7497 (paper trading) o 7496 (live)
      - 'Read-Only API' attivo (per sicurezza)
      - pip install ib_insync
    Ritorna dict con stessa struttura di yfinance + greeks reali, oppure None se KO.
    """
    # Workaround Python 3.14: crea event loop esplicitamente prima dell'import
    # (eventkit, dep di ib_async/ib_insync, usa pattern asyncio deprecato)
    try:
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass

    # Prova prima ib_async (fork attivamente mantenuto, supporta Python 3.14)
    # Poi fallback a ib_insync (vecchio)
    IB = Stock = Option = None
    try:
        from ib_async import IB, Stock, Option  # type: ignore
    except ImportError:
        try:
            from ib_insync import IB, Stock, Option  # type: ignore
        except ImportError:
            return None  # silently skip - fallback a yfinance

    ib = IB()
    try:
        # connect breve timeout: se TWS non risponde subito, fallback
        ib.connect("127.0.0.1", port, clientId=42, timeout=4, readonly=True)
    except Exception:
        try:
            ib.disconnect()
        except Exception:
            pass
        return None

    try:
        # IMPORTANTE: market data type 4 = delayed-frozen (GRATIS, no subscription)
        # 1=live (subscription), 2=frozen, 3=delayed (15min), 4=delayed-frozen
        requested_market_data_type = None
        try:
            ib.reqMarketDataType(4)
            requested_market_data_type = 4
        except Exception:
            pass

        # Silenzia i log di errore (ib_async stampa errori 200/10089 in modo verboso)
        try:
            import logging as _lg
            _lg.getLogger("ib_async").setLevel(_lg.CRITICAL)
            _lg.getLogger("ib_async.wrapper").setLevel(_lg.CRITICAL)
        except Exception:
            pass

        # 1. Definizione contratto azione (SMART = best venue)
        stock = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(stock)

        # 2. Snapshot del prezzo spot (con fallback yfinance se IBKR no subscription)
        spot = 0.0
        spot_source = None
        try:
            spot_ticker = ib.reqMktData(stock, "", snapshot=True)
            ib.sleep(2)
            mp = spot_ticker.marketPrice()
            if mp and mp == mp:  # not NaN
                spot = float(mp)
                spot_source = "IBKR marketPrice"
            elif spot_ticker.last and spot_ticker.last == spot_ticker.last:
                spot = float(spot_ticker.last)
                spot_source = "IBKR last"
            elif spot_ticker.close and spot_ticker.close == spot_ticker.close:
                spot = float(spot_ticker.close)
                spot_source = "IBKR close"
        except Exception:
            pass

        # Fallback yfinance se IBKR non ha dato spot
        if not spot and YFINANCE_AVAILABLE:
            try:
                spot = float(yf.Ticker(ticker).history(period="1d")['Close'].iloc[-1])
                spot_source = "yfinance daily Close (not an intraday quote)"
            except Exception:
                pass

        if not spot:
            try:
                ib.cancelMktData(stock)
            except Exception:
                pass
            return None

        # 3. Ottieni catena opzioni
        chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
        if not chains:
            return None
        chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
        expirations = sorted(chain.expirations)
        if not expirations:
            return None
        if expiry:
            from datetime import date
            nearest = date.fromisoformat(expiry).strftime("%Y%m%d")
            if nearest not in expirations:
                return {"data_source": "IBKR_TWS_error", "error_ibkr":
                        "Scadenza richiesta non disponibile: " + expiry,
                        "available_expiries": expirations}
        else:
            nearest = expirations[0]
        # Strike vicini ad ATM (15% range)
        strikes = sorted([s for s in chain.strikes if 0.85 * spot <= s <= 1.15 * spot])
        if not strikes:
            strikes = sorted(chain.strikes)[:20]

        # 4. Richiedi contratti opzione (call + put) per ogni strike vicino ad ATM
        contracts_raw = []
        for s in strikes:
            for right in ("C", "P"):
                contracts_raw.append(Option(ticker, nearest, s, right, "SMART"))
        # qualifyContracts segna conId solo a contratti validi; scartiamo i non qualificati
        try:
            ib.qualifyContracts(*contracts_raw)
        except Exception:
            pass
        contracts = [c for c in contracts_raw if c.conId]
        if not contracts:
            return None

        # 5. Richiedi market data + greeks (genericTickList "100,101,104,106" = OI, IV, greeks)
        tickers_data = []
        for c in contracts:
            t = ib.reqMktData(c, "100,101,104,106", snapshot=False, regulatorySnapshot=False)
            tickers_data.append((c, t))
        ib.sleep(5)  # attendi 5s per ricevere tutti i greeks (call + put)

        calls_data = []
        puts_data = []
        for c, t in tickers_data:
            try:
                iv = None
                delta = gamma = vega = theta = None
                if t.modelGreeks:
                    iv = float(t.modelGreeks.impliedVol or 0) or None
                    delta = float(t.modelGreeks.delta or 0) or None
                    gamma = float(t.modelGreeks.gamma or 0) or None
                    vega = float(t.modelGreeks.vega or 0) or None
                    theta = float(t.modelGreeks.theta or 0) or None
                # audit/11 §4: l'`or 0` copriva solo il ramo put (precedenza operatori) e
                # ib_insync popola gli OI anche con NaN: int(None)/int(nan) buttava
                # l'intera riga -> OI totale/put-call/max pain su chain parziale.
                _oi_raw = t.callOpenInterest if c.right == "C" else t.putOpenInterest
                _oi = int(_oi_raw) if (_oi_raw is not None and _oi_raw == _oi_raw) else 0
                row = {
                    "strike": float(c.strike),
                    "bid": float(t.bid or 0) or None,
                    "ask": float(t.ask or 0) or None,
                    "last": float(t.last or 0) or None,
                    "iv": iv,
                    "delta": delta,
                    "gamma": gamma,
                    "vega": vega,
                    "theta": theta,
                    "openInterest": _oi,
                }
                if c.right == "C":
                    calls_data.append(row)
                else:
                    puts_data.append(row)
            except Exception:
                continue

        # 6. Calcoli aggregati
        atm_call = min(calls_data, key=lambda x: abs(x["strike"] - spot)) if calls_data else None
        atm_put = min(puts_data, key=lambda x: abs(x["strike"] - spot)) if puts_data else None

        total_call_oi = sum(c["openInterest"] for c in calls_data)
        total_put_oi = sum(p["openInterest"] for p in puts_data)
        pc_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi else None

        # Max pain
        all_strikes = sorted(set([c["strike"] for c in calls_data] + [p["strike"] for p in puts_data]))
        max_pain_strike, max_pain_loss = None, None
        for s in all_strikes:
            call_pain = sum(max(0, s - c["strike"]) * c["openInterest"] for c in calls_data)
            put_pain = sum(max(0, p["strike"] - s) * p["openInterest"] for p in puts_data)
            total_pain = call_pain + put_pain
            if max_pain_loss is None or total_pain < max_pain_loss:
                max_pain_loss = total_pain
                max_pain_strike = s

        # Gamma exposure totale (proxy per dealer hedging pressure)
        gex = sum((c.get("gamma") or 0) * c["openInterest"] * 100 * spot * spot * 0.01 for c in calls_data)
        gex -= sum((p.get("gamma") or 0) * p["openInterest"] * 100 * spot * spot * 0.01 for p in puts_data)

        return {
            "data_source": ("IBKR_TWS_requested_delayed_frozen" if requested_market_data_type == 4
                            else "IBKR_TWS_market_data_type_unknown"),
            "requested_market_data_type": requested_market_data_type,
            "reported_option_market_data_types": sorted({getattr(t, "marketDataType", None)
                for _, t in tickers_data if getattr(t, "marketDataType", None) in (1, 2, 3, 4)}),
            "market_data_note": "Richiesta delayed-frozen; il tipo ricevuto dipende dai permessi IBKR. Nessuna garanzia real-time.",
            "ticker": ticker,
            "spot": round(spot, 2),
            "spot_source": spot_source,
            "nearest_expiry": nearest,
            "atm_iv_call_pct": round(atm_call["iv"] * 100, 1) if atm_call and atm_call["iv"] else None,
            "atm_iv_put_pct": round(atm_put["iv"] * 100, 1) if atm_put and atm_put["iv"] else None,
            "atm_call_delta": round(atm_call["delta"], 3) if atm_call and atm_call["delta"] else None,
            "atm_put_delta": round(atm_put["delta"], 3) if atm_put and atm_put["delta"] else None,
            "atm_call_bid_ask_spread_pct": round((atm_call["ask"] - atm_call["bid"]) / atm_call["bid"] * 100, 1)
                                          if atm_call and atm_call["bid"] and atm_call["ask"] else None,
            "put_call_oi_ratio": pc_ratio,
            "interpretation_pc": "bearish if >1.0, bullish if <0.7" if pc_ratio else None,
            "max_pain_strike": max_pain_strike,
            "max_pain_vs_spot_pct": round((max_pain_strike - spot) / spot * 100, 2) if max_pain_strike else None,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "gamma_exposure_usd_m": round(gex / 1_000_000, 2) if gex else None,
            "gex_interpretation": "Positive GEX = dealers long gamma -> mean reverting / pinning. Negative GEX = dealers short gamma -> trend amplification, vol explosive.",
            "n_strikes_analyzed": len(calls_data) + len(puts_data),
        }
    except Exception as e:
        return {"data_source": "IBKR_TWS_error", "error_ibkr": str(e)}
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def _get_yfinance_oi_only(ticker, expiry=None):
    """Helper: estrae SOLO open interest, put/call ratio, max pain da yfinance.
    Usato per integrare IBKR quando IBKR delayed gratis non manda OI."""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return None
        if expiry:
            from datetime import date
            nearest = date.fromisoformat(expiry).isoformat()
            if nearest not in expirations:
                return None
        else:
            nearest = expirations[0]
        chain = tk.option_chain(nearest)
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty:
            return None

        total_call_oi = int(calls['openInterest'].fillna(0).sum())
        total_put_oi = int(puts['openInterest'].fillna(0).sum())
        pc_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi else None

        # Max pain
        all_strikes = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
        max_pain_loss = None
        max_pain_strike = None
        for s in all_strikes:
            call_pain = ((s - calls['strike']).clip(lower=0) * calls['openInterest'].fillna(0)).sum()
            put_pain = ((puts['strike'] - s).clip(lower=0) * puts['openInterest'].fillna(0)).sum()
            total_pain = float(call_pain + put_pain)
            if max_pain_loss is None or total_pain < max_pain_loss:
                max_pain_loss = total_pain
                max_pain_strike = float(s)

        return {
            "nearest_expiry": nearest,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "put_call_oi_ratio": pc_ratio,
            "interpretation_pc": "bearish if >1.0, bullish if <0.7" if pc_ratio else None,
            "max_pain_strike": max_pain_strike,
        }
    except Exception:
        return None


def tool_get_options_data(ticker, expiry=None):
    """Implied vol, put/call OI ratio, max pain. Solo US tickers.
    expiry (YYYY-MM-DD) opzionale: tutti i provider devono usare la stessa scadenza.
    Strategia ibrida:
      - IBKR TWS (richiesta delayed-frozen) per spot + IV + Greeks
      - yfinance per Open Interest, put/call ratio, max pain (IBKR delayed non manda OI)
    Se IBKR non raggiungibile, fallback puro yfinance.
    """
    if expiry is not None:
        try:
            from datetime import date
            expiry = date.fromisoformat(expiry).isoformat()
        except (TypeError, ValueError):
            return {"error": "expiry non valida: usare una data YYYY-MM-DD"}
    # 0. POLYGON PRIMA (#163): fonte professionale, niente dipendenza da TWS.
    #    IBKR retrocesso a fallback d'emergenza (codice intatto sotto).
    try:
        from bellomberg.market_data.polygon_data import polygon_available, get_options_summary_polygon
        if polygon_available():
            poly = get_options_summary_polygon(ticker, expiry=expiry)
            if poly and not poly.get("error"):
                return poly
    except Exception:
        pass

    # 1. Tenta IBKR TWS (fallback se Polygon giu')
    ibkr_result = _get_ibkr_options(ticker, expiry=expiry) if expiry else _get_ibkr_options(ticker)
    if ibkr_result and "error_ibkr" not in ibkr_result:
        # IBKR ha risposto. Ma se OI=0 (no subscription), integra con yfinance
        if (ibkr_result.get("total_call_oi", 0) == 0 and ibkr_result.get("total_put_oi", 0) == 0
                and YFINANCE_AVAILABLE):
            try:
                yf_oi = _get_yfinance_oi_only(ticker, expiry=ibkr_result["nearest_expiry"])
                if yf_oi:
                    # Merge: tieni Greeks IBKR, sovrascrivi i campi OI con yfinance
                    ibkr_result["data_source"] += " + yfinance_OI"
                    ibkr_result["put_call_oi_ratio"] = yf_oi.get("put_call_oi_ratio")
                    ibkr_result["interpretation_pc"] = yf_oi.get("interpretation_pc")
                    ibkr_result["max_pain_strike"] = yf_oi.get("max_pain_strike")
                    ibkr_result["max_pain_vs_spot_pct"] = round(
                        (yf_oi["max_pain_strike"] - ibkr_result["spot"]) / ibkr_result["spot"] * 100, 2
                    ) if yf_oi.get("max_pain_strike") else None
                    ibkr_result["total_call_oi"] = yf_oi.get("total_call_oi")
                    ibkr_result["total_put_oi"] = yf_oi.get("total_put_oi")
                    ibkr_result["note"] = "Open Interest da yfinance, stessa scadenza. Greeks/IV da IBKR; tipo dati e fonte spot dichiarati nei metadata."
                else:
                    ibkr_result["note"] = "OI non integrato: yfinance non disponibile per la stessa scadenza."
            except Exception:
                pass
        return ibkr_result
    # 2. Fallback yfinance
    if not YFINANCE_AVAILABLE:
        return {"error": "yfinance non disponibile e IBKR TWS non raggiungibile"}
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return {"error": "Nessuna opzione disponibile per " + ticker}
        # Una scadenza diversa e' un contratto diverso: non sostituirla in silenzio.
        if expiry and expiry in expirations:
            nearest = expiry
        elif expiry:
            return {"error": "Scadenza richiesta non disponibile: " + expiry,
                    "available_expiries": list(expirations)}
        else:
            nearest = expirations[0]
        chain = tk.option_chain(nearest)
        calls = chain.calls
        puts = chain.puts

        if calls.empty or puts.empty:
            return {"error": "Chain vuota"}

        # ATM strike = piu' vicino al prezzo corrente
        try:
            spot = float(tk.history(period="1d")['Close'].iloc[-1])
        except Exception:
            spot = None

        if spot:
            atm_call_idx = (calls['strike'] - spot).abs().idxmin()
            atm_put_idx = (puts['strike'] - spot).abs().idxmin()
            atm_iv_call = float(calls.loc[atm_call_idx, 'impliedVolatility'])
            atm_iv_put = float(puts.loc[atm_put_idx, 'impliedVolatility'])
        else:
            atm_iv_call = atm_iv_put = None

        # Put/Call ratio basato su Open Interest
        total_call_oi = int(calls['openInterest'].fillna(0).sum())
        total_put_oi = int(puts['openInterest'].fillna(0).sum())
        pc_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi else None

        # Max pain: lo strike dove il valore intrinseco totale e' minimo
        all_strikes = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
        max_pain_loss = None
        max_pain_strike = None
        for s in all_strikes:
            call_pain = ((s - calls['strike']).clip(lower=0) * calls['openInterest'].fillna(0)).sum()
            put_pain = ((puts['strike'] - s).clip(lower=0) * puts['openInterest'].fillna(0)).sum()
            total_pain = float(call_pain + put_pain)
            if max_pain_loss is None or total_pain < max_pain_loss:
                max_pain_loss = total_pain
                max_pain_strike = float(s)

        return {
            "data_source": "yfinance options",
            "ticker": ticker,
            "spot": round(spot, 2) if spot else None,
            "nearest_expiry": nearest,
            "atm_iv_call_pct": round(atm_iv_call * 100, 1) if atm_iv_call else None,
            "atm_iv_put_pct": round(atm_iv_put * 100, 1) if atm_iv_put else None,
            "put_call_oi_ratio": pc_ratio,
            "interpretation_pc": "bearish if >1.0, bullish if <0.7" if pc_ratio else None,
            "max_pain_strike": max_pain_strike,
            "max_pain_vs_spot_pct": round((max_pain_strike - spot) / spot * 100, 2) if max_pain_strike and spot else None,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
        }
    except Exception as e:
        return {"error": "Errore options " + ticker + ": " + str(e)}


# I CIK dei gestori 13F vivono nel negozio privato delle istituzioni
# (negozi_privati.carica_istituzioni), lo stesso di sec_edgar: un solo posto che dice chi si segue.


def tool_get_13f_filing(institution):
    """Latest 13F-HR submission filings via SEC EDGAR."""
    from bellomberg.storage.negozi_privati import carica_istituzioni
    negozio = carica_istituzioni()
    if negozio["origine"] in ("assente", "illeggibile"):
        return {"error": "negozio delle istituzioni %s: %s" % (negozio["origine"], negozio["motivo"])}
    cik = negozio["istituzioni"]["cik"].get((institution or "").strip().lower())
    if not cik:
        return {
            "error": "Istituzione sconosciuta. Disponibili: "
                     + ", ".join(sorted(negozio["istituzioni"]["cik"])),
        }
    try:
        # SEC EDGAR richiede User-Agent custom
        headers = {"User-Agent": "Portfolio Monitor finance.research@example.com"}
        # 1. Cerca filing recenti
        url = "https://data.sec.gov/submissions/CIK" + cik + ".json"
        r = _req.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return {"error": "SEC EDGAR HTTP " + str(r.status_code)}
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession = recent.get("accessionNumber", [])

        # Trova ultimo 13F-HR
        latest_13f = None
        for i, form in enumerate(forms):
            if form == "13F-HR":
                latest_13f = {
                    "form": form,
                    "filingDate": dates[i],
                    "accessionNumber": accession[i],
                }
                break

        if not latest_13f:
            return {"institution": institution, "note": "Nessun 13F-HR trovato"}

        return {
            "institution": institution,
            "company_name": data.get("name", ""),
            "latest_13f": latest_13f,
            "filing_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=" + cik + "&type=13F-HR&dateb=&owner=include&count=40",
            "note": "Visita filing_url per il dettaglio holdings. Il 13F mostra le posizioni LONG US >$200M (no posizioni short, no asset esteri).",
        }
    except Exception as e:
        return {"error": "Errore SEC EDGAR: " + str(e)}



_POLY_CACHE = {}
_POLY_CACHE_TTL = 300  # 5 minuti
_POLY_BLOCKED = None  # errore TLS osservato, conservato per il solo TTL della cache
_POLY_BLOCKED_AT = 0.0


def _poly_fetch(url, params, retries=2):
    """GET Gamma con cache e retry; errori JSON/TLS dichiarati, mai liste finte.

    Un hostname mismatch non prova da solo la causa DNS/regolatoria. Si mantiene
    la verifica TLS e si evita il retry immediato; dopo il TTL si verifica di nuovo
    la rete, senza lasciare l'intero processo bloccato fino al riavvio.
    """
    import time as _time
    global _POLY_BLOCKED, _POLY_BLOCKED_AT
    if _POLY_BLOCKED and _time.time() - _POLY_BLOCKED_AT < _POLY_CACHE_TTL:
        _POLY_CACHE["__last_error"] = _POLY_BLOCKED
        return None
    _POLY_BLOCKED = None
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    hit = _POLY_CACHE.get(key)
    if hit and _time.time() - hit[0] < _POLY_CACHE_TTL:
        return hit[1]
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = _req.get(url, params=params, timeout=20)
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError as e:
                    raise ValueError("risposta JSON non valida") from e
                if url.endswith("/public-search"):
                    if not isinstance(data, dict) or "error" in data:
                        raise ValueError("JSON ricerca: atteso oggetto di risultati")
                    rows = data.get("events")
                    # Gamma documenta events nullable/omissibile.
                    rows = [] if rows is None else rows
                else:
                    rows = data
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("JSON eventi/mercati: attesa lista di oggetti")
                _POLY_CACHE[key] = (_time.time(), data)
                return data
            last_err = f"HTTP {r.status_code}"
        except _req.exceptions.SSLError as e:
            last_err = str(e)
            if "mismatch" in last_err.lower() or "certificate is not valid" in last_err.lower():
                _POLY_BLOCKED = ("Polymarket n.d.: verifica TLS fallita (hostname mismatch) "
                                 "su gamma-api.polymarket.com; certificato non valido per l'host. "
                                 "Nessun bypass TLS; causa di rete da verificare.")
                _POLY_BLOCKED_AT = _time.time()
                _POLY_CACHE["__last_error"] = _POLY_BLOCKED
                return None
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            _time.sleep(1.2 * (attempt + 1))
    _POLY_CACHE["__last_error"] = last_err
    return None


def _poly_kw_match(haystack, keywords):
    """Match keyword con word-boundary per le parole corte (#163:
    'ai' non deve matchare dentro 'ukrAIne')."""
    import re as _re
    for kw in keywords:
        if len(kw) <= 3:
            if _re.search(r"\b" + _re.escape(kw) + r"\b", haystack):
                return True
        elif kw in haystack:
            return True
    return False


def tool_get_polymarket_events(query, max_results=10):
    """Cerca su Polymarket prediction markets attivi.

    Strategia robusta:
    1. Pulla TOP-N per volume24h (non solo i primi 50 random)
    2. Anche /events endpoint per i mercati raggruppati per evento
    3. Match LOOSE: qualsiasi keyword della query matcha (OR, no AND)
    4. Include synonyms comuni (es. "war" -> "war|conflict|attack|strike")
    5. Sort by volume24h descending
    """
    import json as _json
    try:
        all_results = []

        # Expand query con sinonimi comuni per topic geopolitici/macro
        SYNONYMS = {
            "war": ["war", "conflict", "attack", "strike", "ceasefire", "invasion"],
            "iran": ["iran", "iranian", "israel-iran", "tehran"],
            "israel": ["israel", "israeli", "israel-iran", "gaza", "hamas"],
            "russia": ["russia", "russian", "ukraine", "putin"],
            "ukraine": ["ukraine", "ukrainian", "kyiv", "russia"],
            "china": ["china", "chinese", "xi", "taiwan", "tariff"],
            "fed": ["fed", "fomc", "powell", "rate", "interest"],
            "ecb": ["ecb", "lagarde", "eurozone", "rate"],
            "election": ["election", "vote", "ballot", "primary", "presidential"],
            "trump": ["trump", "potus", "white house", "president"],
            "recession": ["recession", "downturn", "gdp", "contract"],
            "bitcoin": ["bitcoin", "btc", "crypto", "etf"],
            "ai": ["ai", "openai", "agi", "claude", "gpt"],
        }
        # Build keyword list
        query_words = [w.lower() for w in query.split() if len(w) >= 2]
        all_keywords = set(query_words)
        for w in query_words:
            if w in SYNONYMS:
                all_keywords.update(SYNONYMS[w])

        fetch_warnings = []

        # Strategy 0 (#163 fix definitivo): RICERCA SERVER-SIDE di Polymarket —
        # lo stesso endpoint del sito. Trova i mercati specifici anche quando
        # hanno poco volume 24h e starebbero fuori dai top-N delle strategie 1-2.
        try:
            sr = _poly_fetch("https://gamma-api.polymarket.com/public-search",
                             {"q": query, "limit_per_type": 12})
            if isinstance(sr, dict):
                for ev in (sr.get("events") or []):
                    markets = ev.get("markets", []) or []
                    sub_markets = []
                    for m in markets[:5]:
                        outcomes_raw = m.get("outcomes", "[]")
                        prices_raw = m.get("outcomePrices", "[]")
                        try:
                            outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                            prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                        except (ValueError, TypeError) as e:
                            outcomes, prices = None, None
                            fetch_warnings.append(f"/public-search: outcomes/outcomePrices JSON non valido ({type(e).__name__}); probabilita' n.d.")
                        sub_markets.append({
                            "question": (m.get("question", "") or "")[:160],
                            "outcomes": outcomes,
                            "prices": prices,
                            "volume_24h": m.get("volume24hr"),
                            "end_date": m.get("endDate", ""),
                        })
                    all_results.append({
                        "type": "event_group",
                        "match": "server_search",
                        "title": (ev.get("title", "") or "")[:200],
                        "volume_24h": ev.get("volume24hr"),
                        "active_markets": len(markets),
                        "end_date": ev.get("endDate", ""),
                        "url": "https://polymarket.com/event/" + (ev.get("slug", "") or ""),
                        "markets": sub_markets,
                    })
            elif sr is None:
                fetch_warnings.append("/public-search: "
                                      + str(_POLY_CACHE.get("__last_error", "irraggiungibile")))
        except Exception as e:
            fetch_warnings.append(f"/public-search: {type(e).__name__}: {e}")

        seen_event_slugs = {(r.get("url", "").split("/")[-1] or "").lower()
                            for r in all_results}

        # Strategy 1: /events endpoint (grouped) — PAGINATO (#163: i top-500
        # per volume non bastano, i mercati di nicchia stanno oltre)
        try:
            url_events = "https://gamma-api.polymarket.com/events"
            events = []
            for offset in (0, 500, 1000):
                page = _poly_fetch(url_events, {
                    "active": "true", "closed": "false", "limit": 500,
                    "order": "volume24hr", "ascending": "false", "offset": offset,
                })
                if page is None:
                    fetch_warnings.append(
                        f"/events offset={offset}: {_POLY_CACHE.get('__last_error', 'irraggiungibile')}")
                    break
                events.extend(page)
                if len(page) < 500:
                    break
            if events:
                for ev in events:
                    if (ev.get("slug", "") or "").lower() in seen_event_slugs:
                        continue  # già trovato dalla server search
                    title = (ev.get("title", "") or "").lower()
                    slug = (ev.get("slug", "") or "").lower()
                    description = (ev.get("description", "") or "")[:300].lower()
                    # #31: i nomi propri (candidati, persone) spesso vivono SOLO nelle question
                    # dei sub-market (evento "Brazil Presidential Election", question "Will Jair
                    # Bolsonaro win..."): senza questo blocco una query "Lula Bolsonaro" non
                    # matchava nulla se la server-search era giu' -> falso "mercato inesistente".
                    mkt_txt = " ".join(
                        ((m.get("question") or "") + " " + (m.get("groupItemTitle") or ""))
                        for m in (ev.get("markets") or [])[:30]).lower()
                    haystack = title + " " + slug + " " + description + " " + mkt_txt
                    if _poly_kw_match(haystack, all_keywords):
                        markets = ev.get("markets", [])
                        sub_markets = []
                        for m in markets[:5]:
                            outcomes_raw = m.get("outcomes", "[]")
                            prices_raw = m.get("outcomePrices", "[]")
                            try:
                                outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                                prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                            except (ValueError, TypeError) as e:
                                outcomes, prices = None, None
                                fetch_warnings.append(f"/events: outcomes/outcomePrices JSON non valido ({type(e).__name__}); probabilita' n.d.")
                            sub_markets.append({
                                "question": (m.get("question", "") or "")[:160],
                                "outcomes": outcomes,
                                "prices": prices,
                                "volume_24h": m.get("volume24hr"),
                                "end_date": m.get("endDate", ""),
                            })
                        all_results.append({
                            "type": "event_group",
                            "title": ev.get("title", "")[:200],
                            "volume_24h": ev.get("volume24hr"),
                            "active_markets": len(markets),
                            "end_date": ev.get("endDate", ""),
                            "url": "https://polymarket.com/event/" + (ev.get("slug", "")),
                            "markets": sub_markets,
                        })
        except Exception as e:
            fetch_warnings.append(f"/events: {type(e).__name__}: {e}")

        # Strategy 2: /markets endpoint per match piu' singolare — PAGINATO
        try:
            url_markets = "https://gamma-api.polymarket.com/markets"
            markets = []
            for offset in (0, 500):
                page = _poly_fetch(url_markets, {
                    "active": "true", "closed": "false", "limit": 500,
                    "order": "volume24hr", "ascending": "false", "offset": offset,
                })
                if page is None:
                    fetch_warnings.append(
                        f"/markets offset={offset}: {_POLY_CACHE.get('__last_error', 'irraggiungibile')}")
                    break
                markets.extend(page)
                if len(page) < 500:
                    break
            if markets:
                seen_slugs = {ev.get("url", "").split("/")[-1] for ev in all_results}
                for m in markets:
                    question = (m.get("question", "") or "").lower()
                    slug = (m.get("slug", "") or "").lower()
                    description = (m.get("description", "") or "")[:200].lower()
                    haystack = question + " " + slug + " " + description
                    if not _poly_kw_match(haystack, all_keywords):
                        continue
                    if slug in seen_slugs:
                        continue  # Already in event group
                    outcomes_raw = m.get("outcomes", "[]")
                    prices_raw = m.get("outcomePrices", "[]")
                    try:
                        outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                        prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    except (ValueError, TypeError) as e:
                        outcomes, prices = None, None
                        fetch_warnings.append(f"/markets: outcomes/outcomePrices JSON non valido ({type(e).__name__}); probabilita' n.d.")
                    all_results.append({
                        "type": "single_market",
                        "question": (m.get("question", "") or "")[:200],
                        "outcomes": outcomes,
                        "prices": prices,
                        "volume_24h": m.get("volume24hr"),
                        "end_date": m.get("endDate", ""),
                        "url": "https://polymarket.com/event/" + (m.get("slug", "")),
                    })
        except Exception as e:
            fetch_warnings.append(f"/markets: {type(e).__name__}: {e}")

        # Sort: prima i match della server search (più pertinenti), poi per volume
        all_results.sort(key=lambda x: (0 if x.get("match") == "server_search" else 1,
                                        -(x.get("volume_24h", 0) or 0)))
        all_results = all_results[:max_results]

        result = {
            "query": query,
            "expanded_keywords": sorted(all_keywords),
            "count": len(all_results),
            "results": all_results,
            "note": "Prices are probabilities (0-1). Outcomes[0]=Yes, Outcomes[1]=No typically. "
                    "type=event_group raccoglie più mercati su stesso tema; type=single_market e' singolo.",
        }
        if fetch_warnings:
            result["fetch_warnings"] = fetch_warnings
            result["status"] = "partial" if all_results else "unavailable"
            if not all_results:
                result["error"] = "Polymarket n.d.: " + "; ".join(fetch_warnings)
                result["hint"] = ("Ricerca incompleta per errore del provider: NON dedurre "
                                  "probabilita' o assenza di mercati. Prosegui la risposta "
                                  "dichiarando il dato n.d.; evita retry ripetuti in questa risposta.")
        if _POLY_BLOCKED:
            # Buco TLS osservato: niente tentativi ripetuti nella stessa risposta.
            result["error"] = _POLY_BLOCKED
            result["status"] = "partial" if all_results else "unavailable"
            result["hint"] = ("NON riprovare in questa risposta: dato n.d. dichiarato. "
                              "Prosegui la chat dichiarando il limite. La rete sara' ricontrollata "
                              "nelle richieste successive alla scadenza della cache.")
            return result
        if not all_results and not fetch_warnings:
            result["hint"] = ("Nessun match. Se fetch_warnings e' presente, gamma-api era "
                              "irraggiungibile: riprova la stessa call. Altrimenti riprova "
                              "con termini inglesi piu' generici o sinonimi diversi — "
                              "NON concludere che il mercato non esiste.")
        return result
    except Exception as e:
        return {"error": f"Polymarket error {type(e).__name__}: {e}",
                "status": "unavailable",
                "hint": "Dato n.d. dichiarato; prosegui la chat senza inventare probabilita'."}


def tool_get_fundamentals(ticker):
    """Bilancio + valuation da yfinance: PE, margins, FCF, debt."""
    if not YFINANCE_AVAILABLE:
        return {"error": "yfinance non disponibile"}
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}

        result = {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            # Valuation
            "market_cap": info.get("marketCap"),
            "pe_trailing": info.get("trailingPE"),
            "pe_forward": info.get("forwardPE"),
            "peg": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "ev_to_ebitda": info.get("enterpriseToEbitda"),
            # Profitability
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "profit_margin": info.get("profitMargins"),
            "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"),
            # Growth
            "revenue_growth_yoy": info.get("revenueGrowth"),
            "earnings_growth_yoy": info.get("earningsGrowth"),
            # Balance Sheet Health
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "total_cash": info.get("totalCash"),
            "total_debt": info.get("totalDebt"),
            # Cash Flow
            "operating_cashflow": info.get("operatingCashflow"),
            "free_cashflow": info.get("freeCashflow"),
            # Dividends
            "dividend_yield": info.get("dividendYield"),
            "payout_ratio": info.get("payoutRatio"),
            # Analyst
            "target_mean_price": info.get("targetMeanPrice"),
            "target_high_price": info.get("targetHighPrice"),
            "target_low_price": info.get("targetLowPrice"),
            "recommendation": info.get("recommendationKey"),
            "num_analysts": info.get("numberOfAnalystOpinions"),
            # Short interest
            "short_ratio": info.get("shortRatio"),
            "short_pct_float": info.get("shortPercentOfFloat"),
        }
        # Rimuovi tutti i None per snellire
        result = {k: v for k, v in result.items() if v is not None}
        return result
    except Exception as e:
        return {"error": "Fundamentals error " + ticker + ": " + str(e)}



# EXCEL_PORTFOLIO_PATH rimosso (#194): il portafoglio vive SOLO nel DB SQLite, mai piu' Excel.


def _safe_float(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def tool_get_portfolio_live(filepath=None):
    """Portafoglio REAL-TIME dal database SQLite: UNICA fonte di verita' (prezzi live da
    position_prices, aggiornato automaticamente dai trade; stessa fonte di Dashboard e
    Performance). NESSUN fallback su Excel: il vecchio PORTFOLIO.xlsx non si aggiorna mai
    e causava il disallineamento del NAV, quindi non viene piu' letto in nessun caso."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        summ = MemoryDB().get_portfolio_summary()
    except Exception as e:
        return {"error": "Database portafoglio non disponibile: " + str(e),
                "hint": "Il portafoglio vive nel DB SQLite (data/consigliere.db), aggiornato dai trade."}
    if not summ or summ.get("n_positions", 0) == 0:
        return {"error": "Nessuna posizione attiva nel database.", "n_positions": 0,
                "hint": "Registra i trade dalla pagina TRADE (F7): aggiornano posizioni e cash nel DB."}
    # Voce 9 §9-quattuortrigies (01/08): la stringa fissa CANCELLAVA l'avviso
    # "FX non disponibile per X: valori in valuta NATIVA sommati nel NAV" che
    # memory_db scrive in `source` (regola 14/07). La dicitura del tool vale
    # SOLO nel caso pulito; con FX incompleto resta l'avviso vero.
    if not summ.get("fx_incomplete"):
        summ["source"] = "SQLite DB live (position_prices + trade history, fonte unica)"
    return summ


def tool_get_hyperliquid_intel(focus_asset=None, builder_dexs=True):
    """Dati profondi sul DEX via API ufficiale. `focus_asset` e' OPZIONALE e senza default:
    il default era il token del book (05/09, chat 8c, lotto 3): a chiamata vuota il tool
    dettagliava una posizione del PM. Senza asset: top perps e basta, DICHIARATO in
    `focus_note`; asset non quotato: `focus_asset_detail` n.d., dichiarato."""
    import requests as _r
    import math as _math
    def numero(value):
        try:
            n = float(value) if value is not None and not isinstance(value, bool) else None
            return n if n is not None and _math.isfinite(n) else None
        except (ValueError, TypeError, OverflowError):
            return None

    def misura(value, factor=1, digits=2):
        return round(value * factor, digits) if value is not None else None
    focus_asset = (focus_asset or "").strip() or None
    risultati = {"source": "Hyperliquid official API (api.hyperliquid.xyz)", "focus": focus_asset}
    if focus_asset is None:
        risultati["focus_note"] = ("nessun asset richiesto: solo top perps (nessun default: un "
                                   "ripiego zitto sceglierebbe un asset al posto tuo)")

    try:
        r = _r.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "metaAndAssetCtxs"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return {"error": "Risposta Hyperliquid invalida"}

        meta = data[0]
        ctxs = data[1]
        universe = meta.get("universe", [])

        intel_per_asset = []
        focus_data = None
        for i, asset in enumerate(universe):
            if i >= len(ctxs):
                continue
            name = asset.get("name", "")
            ctx = ctxs[i]
            try:
                mark, prev = numero(ctx.get("markPx")), numero(ctx.get("prevDayPx"))
                mark = mark if mark is not None and mark > 0 else None
                funding = numero(ctx.get("funding"))
                day_vol, oi = numero(ctx.get("dayNtlVlm")), numero(ctx.get("openInterest"))
                open_int_usd = oi * mark if oi is not None and oi >= 0 and mark is not None else None
                premium = numero(ctx.get("premium"))
                var_24h_pct = ((mark - prev) / prev * 100) if mark is not None and prev is not None and prev > 0 else None

                row = {
                    "asset": name,
                    "mark_px": mark,
                    "var_24h_pct": misura(var_24h_pct),
                    "funding_hourly_pct": misura(funding, 100, 4),
                    "funding_annualized_pct": misura(funding, 24 * 365 * 100),
                    "oi_usd_m": misura(open_int_usd, 1 / 1_000_000),
                    "vol_24h_usd_m": misura(day_vol, 1 / 1_000_000) if day_vol is not None and day_vol >= 0 else None,
                    "premium_basis_pts": misura(premium, 10000, 1),
                }
                row["missing_fields"] = [k for k, v in row.items() if v is None]
                # audit/07 + regola PM 14/07 (precisione, no numeri-segnale vuoti):
                # 0.0000125/h e' il TASSO BASE Hyperliquid — "+10.95% annualizzato" e'
                # stato citato per 4 memo come segnale quando significa premium ~0.
                if funding is not None and abs(funding - 0.0000125) < 1e-9:
                    row["funding_note"] = ("AL TASSO BASE (0.00125%/h): premium ~0, "
                                           "NESSUNA pressione direzionale — non e' un segnale")
                intel_per_asset.append(row)
                if focus_asset and name.upper() == focus_asset.upper():
                    focus_data = row
            except (ValueError, ZeroDivisionError, TypeError):
                continue

        intel_per_asset.sort(key=lambda x: x.get("oi_usd_m") or 0, reverse=True)

        risultati["focus_asset_detail"] = focus_data
        if focus_asset and focus_data is None:
            risultati["focus_note"] = ("asset %s non quotato sul dex principale: "
                                       "focus_asset_detail n.d." % focus_asset)
        risultati["top_10_perps_by_oi"] = [x for x in intel_per_asset if x["oi_usd_m"] is not None][:10]

        funding_sorted = [x for x in intel_per_asset if x.get("funding_hourly_pct") is not None]
        funding_sorted.sort(key=lambda x: x["funding_hourly_pct"], reverse=True)
        risultati["highest_funding_long_pressure"] = funding_sorted[:5]
        risultati["lowest_funding_short_pressure"] = funding_sorted[-5:]

        vol_sorted = sorted((x for x in intel_per_asset if x["vol_24h_usd_m"] is not None), key=lambda x: x["vol_24h_usd_m"], reverse=True)
        risultati["top_5_by_volume"] = vol_sorted[:5]

        risultati["interpretation_hints"] = {
            "high_positive_funding": "Long pressure (longs paying shorts). Crowded long, squeeze risk if reversal.",
            "high_negative_funding": "Short pressure (shorts paying longs). Contrarian long opportunity if technicals support.",
            "high_premium_bps": "Perp trading above spot - bullish bias",
            "rising_OI_with_price_up": "New longs entering - trend continuation likely",
            "rising_OI_with_price_down": "New shorts entering - bearish",
        }

        # --- HIP-3 builder dexs (#199-Crypto): equities/commodities 24/7 ('xyz') + pre-IPO Ventuals ('vntl') ---
        if builder_dexs:
            try:
                rd = _r.post("https://api.hyperliquid.xyz/info", json={"type": "perpDexs"}, timeout=10)
                rd.raise_for_status()
                dex_list = [d for d in (rd.json() or []) if isinstance(d, dict) and d.get("name")]
                builders = {}
                pre_ipo = []
                pre_ipo_hints = ("ANTHROPIC", "OPENAI", "SPACEX", "SPCX", "QNT", "XAI",
                                 "NEURALINK", "STRIPE", "ANDURIL", "DATABRICKS", "KRAKEN",
                                 "RIPPLE", "CIRCLE", "STARLINK", "PERPLEXITY", "FIGMA",
                                 "GROQ", "MISTRAL", "SCALE", "CURSOR", "EPIC")
                for d in dex_list[:8]:
                    dname = d.get("name")
                    try:
                        rb = _r.post("https://api.hyperliquid.xyz/info",
                                     json={"type": "metaAndAssetCtxs", "dex": dname}, timeout=8)
                        rb.raise_for_status()
                        db_ = rb.json()
                        uni_b = db_[0].get("universe", []) if isinstance(db_, list) and len(db_) >= 2 else []
                        ctxs_b = db_[1] if isinstance(db_, list) and len(db_) >= 2 else []
                        rows = []
                        for j, asset_b in enumerate(uni_b):
                            if j >= len(ctxs_b):
                                continue
                            cb = ctxs_b[j]
                            try:
                                mk, pv = numero(cb.get("markPx")), numero(cb.get("prevDayPx"))
                                mk = mk if mk is not None and mk > 0 else None
                                fn, oi = numero(cb.get("funding")), numero(cb.get("openInterest"))
                                oi_b = oi * mk if oi is not None and oi >= 0 and mk is not None else None
                                row_b = {
                                    "asset": asset_b.get("name", ""),
                                    "mark_px": mk,
                                    "var_24h_pct": misura((mk - pv) / pv, 100) if mk is not None and pv is not None and pv > 0 else None,
                                    "funding_annualized_pct": misura(fn, 24 * 365 * 100),
                                    "oi_usd_m": misura(oi_b, 1 / 1_000_000),
                                }
                                row_b["missing_fields"] = [k for k, v in row_b.items() if v is None]
                                rows.append(row_b)
                            except (ValueError, TypeError, ZeroDivisionError):
                                continue
                        rows.sort(key=lambda x: x.get("oi_usd_m") or 0, reverse=True)
                        top_n = 6 if dname == "xyz" else 3
                        builders[dname] = {"n_markets": len(uni_b), "top_by_oi": [x for x in rows if x["oi_usd_m"] is not None][:top_n],
                                           "n_missing_oi": sum(x["oi_usd_m"] is None for x in rows)}
                        for row_b in rows:  # pre-IPO per NOME, su qualunque dex (mix xyz/vntl)
                            base_b = str(row_b.get("asset", "")).split(":", 1)[-1].upper()
                            if any(h in base_b for h in pre_ipo_hints) and len(pre_ipo) < 15:
                                pre_ipo.append(row_b)
                    except Exception:
                        builders[dname] = {"error": "fetch fallito"}
                risultati["builder_dexs_hip3"] = builders
                if pre_ipo:
                    risultati["pre_ipo_markets"] = pre_ipo
                risultati["builder_note"] = ("Mercati HIP-3 24/7: 'xyz' = il dex TradFi principale "
                                             "(indici, big tech, commodities - tape weekend/overnight), pre-IPO in MIX "
                                             "su 'xyz'/'vntl' (ANTHROPIC, SPACEX, OPENAI...). Sentiment indicativo, "
                                             "liquidita' variabile: mai trattarli come prezzi eseguibili. ATTENZIONE: lo stesso nome "
                                             "puo' quotare su piu' dex con SCALA DIVERSA (es. SPACEX vntl vs SPCX xyz): "
                                             "confronta per OI/volume e preferisci il mercato piu' liquido, di solito xyz.")
            except Exception as e:
                risultati["builder_dexs_error"] = str(e)

        risultati["external_data_sources_hint"] = ("Per mNAV/holdings UFFICIALI di una DAT usa il tool get_dat_metrics (copre le DAT registrate nel motore; per le altre KO dichiarato), NON tavily_search.")

        return risultati
    except Exception as e:
        return {"error": "Errore Hyperliquid intel: " + str(e)}


# === DISPATCHER ===
TOOL_DISPATCHER = {
    "get_portfolio_live": tool_get_portfolio_live,
    "get_hyperliquid_intel": tool_get_hyperliquid_intel,
    "tavily_search": tool_tavily_search,
    "search_news": tool_search_news,
    "get_market_data": tool_get_market_data,
    "get_portfolio_state": tool_get_portfolio_state,
    "compare_assets": tool_compare_assets,
    "get_options_data": tool_get_options_data,
    "get_13f_filing": tool_get_13f_filing,
    "get_polymarket_events": tool_get_polymarket_events,
    "get_fundamentals": tool_get_fundamentals,
    "read_recent_briefings": tool_read_recent_briefings,
}


def execute_tool(name, arguments):
    """Dispatcher: chiama il tool richiesto da Opus."""
    if not isinstance(name, str):
        return {"error": "Nome tool non valido: attesa una stringa"}
    if name not in TOOL_DISPATCHER:
        return {"error": "Tool sconosciuto: " + name}
    try:
        return TOOL_DISPATCHER[name](**arguments)
    except TypeError as e:
        return {"error": "Argomenti non validi per " + name + ": " + str(e)}
    except Exception as e:
        return {"error": "Errore esecuzione " + name + ": " + str(e)}


# === FRED MACRO TOOLS ===
# Federal Reserve Economic Data, St. Louis Fed
# https://fred.stlouisfed.org/docs/api/api_key.html (gratis, illimitato)

_FRED_INDICATORS = {
    "us_cpi_yoy": ("CPIAUCSL", "US CPI all items (YoY computed)", "yoy"),
    "us_core_cpi_yoy": ("CPILFESL", "US Core CPI ex food/energy (YoY)", "yoy"),
    "us_unemployment": ("UNRATE", "US Unemployment Rate (%)", "level"),
    "fed_funds_rate": ("FEDFUNDS", "Fed Funds Effective Rate (%)", "level"),
    "10y_treasury": ("DGS10", "10Y Treasury Yield (%)", "level"),
    "2y_treasury": ("DGS2", "2Y Treasury Yield (%)", "level"),
    "yield_curve_10y_2y": ("T10Y2Y", "10Y-2Y Spread (bps if negative = inversion)", "level"),
    "vix_close": ("VIXCLS", "VIX close", "level"),
    "wti_oil": ("DCOILWTICO", "WTI Crude Oil ($/bbl)", "level"),
    "dollar_index_broad": ("DTWEXBGS", "Broad Dollar Index (FRED, scala ~120; NON e' il DXY ICE ~100)", "level"),
    "us_gdp_real_yoy": ("GDPC1", "US Real GDP (YoY computed)", "yoy"),
    "us_industrial_prod": ("INDPRO", "Industrial Production Index", "yoy"),
    "us_initial_claims": ("ICSA", "Initial Jobless Claims (weekly)", "level"),
    "us_retail_sales": ("RSAFS", "Retail Sales (YoY computed)", "yoy"),
    "us_housing_starts": ("HOUST", "Housing Starts (thousands SAAR)", "level"),
    "us_ism_manufacturing": ("MANEMP", "Manufacturing Employment (proxy)", "level"),
    "high_yield_spread": ("BAMLH0A0HYM2", "High Yield Bond Spread (%)", "level"),
    "ig_credit_spread": ("BAMLC0A0CM", "IG Corporate Bond Spread (%)", "level"),
    # === ECB (Eurozone) ===
    "ecb_deposit_rate": ("ECBDFR", "ECB Deposit Facility Rate (%)", "level"),
    "ecb_main_refi": ("ECBMRRFR", "ECB Main Refinancing Operations Rate (%)", "level"),
    # P1 14/07: ez_3m_euribor (IR3TIB01EZM156N) morto alla fonte (ultimo update 02/2026)
    # -> sostituito con ESTR overnight ECB, giornaliero, VERIFICATO con fetch reale (oss. 13/07/2026)
    "ez_estr": ("ECBESTRVOLWGTTRMDMNRT", "Euro Short-Term Rate ESTR overnight (%)", "level"),
    "ez_cpi_yoy": ("CP0000EZ19M086NEST", "Eurozone HICP YoY (%)", "level"),
    "ez_10y_bund": ("IRLTLT01DEM156N", "Germany 10Y Bund Yield (%)", "level"),
    # === BOE (UK) ===
    "boe_bank_rate": ("IUDSOIA", "BOE Sterling Overnight Index (%)", "level"),
    "uk_10y_gilt": ("IRLTLT01GBM156N", "UK 10Y Gilt Yield (%)", "level"),
    "uk_unemployment": ("LRHUTTTTGBM156S", "UK Unemployment Rate (%; fonte OCSE, ~5 mesi di lag dichiarato)", "level"),
    # === BOJ (Japan) ===
    # P1 14/07: boj_discount_rate (INTDSRJPM193N) morto alla fonte (ultima oss. 2017)
    # -> sostituito col tasso overnight call money (operativo BOJ), VERIFICATO (oss. 05/2026)
    "boj_call_rate": ("IRSTCI01JPM156N", "Japan Overnight Call Money Rate (%)", "level"),
    "japan_10y_jgb": ("IRLTLT01JPM156N", "Japan 10Y JGB Yield (%)", "level"),
    "japan_unemployment": ("LRHUTTTTJPM156S", "Japan Unemployment Rate (%)", "level"),
    "usd_jpy": ("DEXJPUS", "USD/JPY FX rate", "level"),
    # === China (PBoC) ===
    "china_3m_rate": ("IR3TIB01CNM156N", "China 3M Interbank Rate (%)", "level"),
    "usd_cny": ("DEXCHUS", "USD/CNY FX rate", "level"),
    "china_export_yoy": ("XTEXVA01CNM659S", "China Export Value YoY (%)", "level"),  # 659S: serie GIA' YoY (audit/11: prima si faceva lo YoY dello YoY)
    # === Brazil (BCB) ===
    "brazil_selic": ("INTDSRBRM193N", "Brazil SELIC Rate (%)", "level"),
    "usd_brl": ("DEXBZUS", "USD/BRL FX rate", "level"),
    # === Currencies cross ===
    "eur_usd": ("DEXUSEU", "USD/EUR FX rate (USD per EUR)", "level"),
    "gbp_usd": ("DEXUSUK", "USD/GBP FX rate (USD per GBP)", "level"),
    "real_10y_rate": ("DFII10", "10Y TIPS Real Yield (%)", "level"),
}

# P1 14/07 (serie FRED morte alla fonte, scoperta del freshness check): su FRED
# non esiste piu' nulla di vivo per queste serie (verificato con fetch reale,
# catalogo ordinato per last_updated). 5 su 6 sono state RIEMPITE con fonti
# NATIVE verificate (_NATIVE_INDICATORS qui sotto, ok PM 14/07); resta un solo
# buco DICHIARATO in indicators_not_available — regola PM "buco dichiarato >
# numero morto".
_FRED_REMOVED_NOTE = {
    "china_unemployment": "ID FRED LMUNRRTTCNM156S INESISTENTE (HTTP 400), registered unemployment ferma al 2011; nessuna fonte libera affidabile (NBS richiede sessione)",
}


# === NATIVE MACRO SOURCES (P1 14/07, ok PM: CPI/unemployment morte su FRED) ===
# Endpoint VERIFICATI con fetch reale il 14/07/2026 — mai indovinati.
# Ogni fetcher ritorna {"value", "date", ...} o {"error": ...}; la dashboard li
# integra con src dichiarato e il freshness li controlla come le serie FRED.
_NATIVE_CACHE = {}
_NATIVE_TTL_S = 900.0  # come _FRED_TTL_S: la dashboard gira piu' volte a run
_NATIVE_UA = {"User-Agent": "Bellomberg/1.0 (macro dashboard; personal use)"}


def _native_cached(key, fn):
    import time as _t
    hit = _NATIVE_CACHE.get(key)
    if hit and (_t.time() - hit[0]) < _NATIVE_TTL_S:
        return hit[1]
    try:
        out = fn()
    except Exception as e:
        # niente cache degli errori: al giro dopo si ritenta
        return {"error": key + ": " + str(e)[:160]}
    _NATIVE_CACHE[key] = (_t.time(), out)
    return out


def _fetch_bcb_ipca_yoy():
    """Brasile: IPCA accumulato 12 mesi (%) — API BCB/SGS serie 13522 (verificata 14/07: 4.64% a 06/2026)."""
    r = _req.get("https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados/ultimos/2",
                 params={"formato": "json"}, headers=_NATIVE_UA, timeout=15)
    r.raise_for_status()
    last = r.json()[-1]
    dd, mm, yy = last["data"].split("/")  # dd/MM/yyyy
    return {"value": float(last["valor"]), "date": yy + "-" + mm + "-" + dd}


def _fetch_ons_uk_cpi_yoy():
    """UK: CPI annual rate (%) — serie ONS d7g7/MM23 (verificata 14/07: 2.8% a 05/2026)."""
    r = _req.get("https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data",
                 headers=_NATIVE_UA, timeout=20)
    r.raise_for_status()
    months = r.json().get("months", [])
    if not months:
        return {"error": "ONS d7g7: nessun dato mensile nella risposta"}
    last = months[-1]
    from datetime import datetime as _dt
    d = _dt.strptime(last["date"].title(), "%Y %b")  # "2026 MAY" -> 2026-05-01
    return {"value": float(last["value"]), "date": d.strftime("%Y-%m-%d")}


def _fetch_eurostat_ez_unemployment():
    """Euro area: disoccupazione EA21 SA (%) — API Eurostat une_rt_m (verificata 14/07: 6.2% a 05/2026)."""
    r = _req.get("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/une_rt_m",
                 params={"geo": "EA21", "s_adj": "SA", "sex": "T", "age": "TOTAL",
                         "unit": "PC_ACT", "format": "JSON", "lastTimePeriod": "4"},
                 headers=_NATIVE_UA, timeout=20)
    r.raise_for_status()
    d = r.json()
    times = sorted(d["dimension"]["time"]["category"]["index"].items(), key=lambda kv: kv[1])
    vals = d.get("value", {})
    # ultimo periodo NON nullo (Eurostat espone anche i mesi non ancora pubblicati)
    for t, i in reversed(times):
        v = vals.get(str(i))
        if v is not None:
            return {"value": float(v), "date": t + "-01"}
    return {"error": "Eurostat une_rt_m EA21: solo valori nulli negli ultimi 4 periodi"}


def _fetch_imf_cpi_yoy(country):
    """Giappone/Cina: CPI YoY (%) CALCOLATO dall'indice IMF (api.imf.org SDMX 2.1,
    dataset IMF.STA,CPI, verificato 14/07: JPN indice a 05/2026). country='JPN'|'CHN'."""
    url = ("https://api.imf.org/external/sdmx/2.1/data/IMF.STA,CPI/"
           + country + ".CPI._T.IX.M")
    r = _req.get(url, params={"lastNObservations": 14},
                 headers={**_NATIVE_UA, "Accept": "application/json"}, timeout=25)
    r.raise_for_status()
    d = r.json()
    series = d["dataSets"][0]["series"]
    obs = series[list(series)[0]]["observations"]
    time_ids = [v["id"] for v in d["structure"]["dimensions"]["observation"][0]["values"]]
    rows = {time_ids[int(i)]: float(v[0]) for i, v in obs.items()}  # "2026-M05" -> 113.5
    if not rows:
        return {"error": "IMF CPI " + country + ": nessuna osservazione"}
    last_t = max(rows)
    yy, mm = last_t.split("-M")
    prev_t = str(int(yy) - 1) + "-M" + mm
    if prev_t not in rows:
        return {"error": "IMF CPI " + country + ": manca l'osservazione di 12 mesi prima (" + prev_t + "), YoY non calcolabile"}
    yoy = (rows[last_t] - rows[prev_t]) / abs(rows[prev_t]) * 100
    return {"value": round(yoy, 2), "date": yy + "-" + mm + "-01",
            "index_latest": rows[last_t], "index_year_ago": rows[prev_t]}


# chiave -> (fetcher, descrizione, src dichiarato). Per le serie CPI il "value"
# E' GIA' lo YoY in percento (niente livelli d'indice nel campo value: lezione
# audit/11 "CPI YoY 320%"); yoy_pct viene valorizzato uguale dalla dashboard.
_NATIVE_INDICATORS = {
    "uk_cpi_yoy": (_fetch_ons_uk_cpi_yoy, "UK CPI YoY (%)", "ONS"),
    "ez_unemployment": (_fetch_eurostat_ez_unemployment, "Euro Area (EA21) Unemployment Rate (%)", "Eurostat"),
    "japan_cpi_yoy": (lambda: _fetch_imf_cpi_yoy("JPN"), "Japan CPI YoY (%)", "IMF"),
    "china_cpi_yoy": (lambda: _fetch_imf_cpi_yoy("CHN"), "China CPI YoY (%)", "IMF"),
    "brazil_cpi_yoy": (_fetch_bcb_ipca_yoy, "Brazil IPCA CPI YoY 12m (%)", "BCB"),
}


_FRED_CACHE = {}
_FRED_TTL_S = 900.0  # 202-C: 15 min - il dashboard macro fa 44 serie e gira piu' volte a run


def _fred_fetch_series(series_id, last_n=24):
    """Helper: chiama FRED API per N osservazioni piu recenti. Cache 15 min (#202-C)."""
    import time as _t
    _k = (str(series_id), int(last_n))
    _hit = _FRED_CACHE.get(_k)
    if _hit and (_t.time() - _hit[0]) < _FRED_TTL_S:
        return _hit[1]
    from bellomberg.core.config import FRED_API_KEY
    if not FRED_API_KEY:
        return {"error": "FRED_API_KEY non configurata nel .env"}
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": last_n,
        }
        r = _req.get(url, params=params, timeout=15)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        # Reverse: piu vecchio -> piu recente, scarta valori "."
        clean = []
        for o in obs[::-1]:
            try:
                v = float(o["value"])
                clean.append({"date": o["date"], "value": v})
            except (ValueError, KeyError):
                continue
        _out = {"series_id": series_id, "observations": clean}
        _FRED_CACHE[_k] = (_t.time(), _out)
        return _out
    except Exception as e:
        return {"error": "FRED fetch " + series_id + ": " + str(e)}


def tool_get_macro_indicator(indicator, last_n=12):
    """Singolo indicatore macro da FRED o da fonte nativa (P1 14/07).
    'indicator' puo essere una chiave pre-mappata (es. 'us_cpi_yoy', 'vix_close',
    'uk_cpi_yoy') OPPURE un series_id FRED diretto (es. 'CPIAUCSL', 'DGS10')."""
    # Fonti native (ONS/Eurostat/IMF/BCB): serie morte su FRED, riempite 14/07
    if indicator in _NATIVE_INDICATORS:
        fn, desc, src = _NATIVE_INDICATORS[indicator]
        res = _native_cached(indicator, fn)
        if "error" in res:
            return {"indicator": indicator, "source": src, "error": res["error"]}
        out = {"indicator": indicator, "source": src, "description": desc,
               "latest_date": res["date"], "latest_value": res["value"]}
        if indicator.endswith("_yoy"):
            out["yoy_pct"] = res["value"]  # il value E' gia' lo YoY in %
        return out

    # Mapping chiave -> series_id, mode
    mode = "level"
    desc = indicator
    if indicator in _FRED_INDICATORS:
        series_id, desc, mode = _FRED_INDICATORS[indicator]
    else:
        series_id = indicator

    data = _fred_fetch_series(series_id, last_n=max(last_n, 24 if mode == "yoy" else last_n))
    if "error" in data:
        return data

    obs = data["observations"]
    if not obs:
        return {"error": "Nessun dato FRED per " + series_id}

    result = {
        "indicator": indicator,
        "series_id": series_id,
        "description": desc,
        "latest_date": obs[-1]["date"],
        "latest_value": obs[-1]["value"],
        "mode": mode,
    }

    if mode == "yoy" and len(obs) >= 2:
        # audit/11 §2: obs[-13] assumeva serie MENSILI — su GDPC1 (trimestrale) il "YoY"
        # era in realta' la crescita a 3 ANNI. Ora si cerca l'osservazione per DATA
        # (~365 giorni prima dell'ultima), robusto per qualunque frequenza.
        from datetime import datetime as _dt, timedelta as _td
        latest = obs[-1]["value"]
        year_ago_obs = None
        try:
            d_last = _dt.strptime(obs[-1]["date"], "%Y-%m-%d")
            target = d_last - _td(days=365)
            year_ago_obs = min(
                obs[:-1],
                key=lambda o: abs((_dt.strptime(o["date"], "%Y-%m-%d") - target).days))
            # se la piu' vicina dista >200gg dal target la serie non copre l'anno: niente YoY
            if abs((_dt.strptime(year_ago_obs["date"], "%Y-%m-%d") - target).days) > 200:
                year_ago_obs = None
        except Exception:
            year_ago_obs = None
        if year_ago_obs and year_ago_obs["value"]:
            year_ago = year_ago_obs["value"]
            yoy = (latest - year_ago) / abs(year_ago) * 100
            result["yoy_pct"] = round(yoy, 2)
            result["year_ago_date"] = year_ago_obs["date"]
            result["year_ago_value"] = year_ago

    if len(obs) >= 2:
        # Variazione vs precedente release
        prev = obs[-2]["value"]
        result["prev_value"] = prev
        result["prev_date"] = obs[-2]["date"]
        result["change_vs_prev"] = round(obs[-1]["value"] - prev, 4)

    # Storia ridotta per il LLM (ultimi 12)
    result["history_last_12"] = obs[-12:]
    return result


def tool_get_macro_dashboard():
    """Dashboard macro: tutti gli indicatori chiave in un colpo.
    Usa questo INVECE di chiamare get_macro_indicator 10 volte separatamente."""
    dashboard = {"source": "FRED St. Louis Fed + fonti native (ONS, Eurostat, IMF, BCB)",
                 "indicators": {}}
    for key, (series_id, desc, mode) in _FRED_INDICATORS.items():
        try:
            result = tool_get_macro_indicator(key, last_n=24)
            if "error" not in result:
                # Versione compatta per dashboard
                dashboard["indicators"][key] = {
                    "value": result["latest_value"],
                    "date": result["latest_date"],
                    "description": desc,
                    "yoy_pct": result.get("yoy_pct"),
                    "change_vs_prev": result.get("change_vs_prev"),
                }
            else:
                # P1 14/07 (no-fallback): l'errore si DICHIARA, non si salta in
                # silenzio (uk_cpi con ID inesistente e' sparito per mesi cosi')
                dashboard["indicators"][key] = {"error": result["error"], "description": desc}
        except Exception as e:
            dashboard["indicators"][key] = {"error": str(e), "description": desc}

    # Fonti native (P1 14/07, ok PM): serie morte su FRED riempite da ONS/
    # Eurostat/IMF/BCB, con src DICHIARATO. Errori dichiarati, mai saltati.
    for key, (fn, desc, src) in _NATIVE_INDICATORS.items():
        res = _native_cached(key, fn)
        if "error" in res:
            dashboard["indicators"][key] = {"error": res["error"],
                                            "description": desc, "src": src}
        else:
            entry = {"value": res["value"], "date": res["date"],
                     "description": desc + " [src: " + src + "]", "src": src}
            if key.endswith("_yoy"):
                entry["yoy_pct"] = res["value"]  # il value E' gia' lo YoY in %
            dashboard["indicators"][key] = entry

    # Buchi dichiarati: serie morte alla fonte, senza sostituto (P1 14/07)
    dashboard["indicators_not_available"] = dict(_FRED_REMOVED_NOTE)

    # Sintesi rapida
    ind = dashboard["indicators"]
    y10 = ind.get("10y_treasury", {}).get("value")
    y2 = ind.get("2y_treasury", {}).get("value")
    if y10 and y2:
        dashboard["yield_curve_10y_2y_bps"] = round((y10 - y2) * 100, 0)
        dashboard["yield_curve_status"] = "INVERTED (recession signal)" if y10 < y2 else "POSITIVE (normal)"

    cpi_yoy = ind.get("us_cpi_yoy", {}).get("yoy_pct")
    fed = ind.get("fed_funds_rate", {}).get("value")
    if cpi_yoy is not None and fed is not None:
        dashboard["real_fed_funds_pct"] = round(fed - cpi_yoy, 2)
        dashboard["real_rates_note"] = "Real rates positive (restrictive Fed)" if (fed - cpi_yoy) > 0 else "Real rates negative (still accommodative)"

    return dashboard


# Append a TOOLS_SCHEMA e TOOL_DISPATCHER (no edit in mezzo file per evitare troncamenti OneDrive)
TOOLS_SCHEMA.append({
    "name": "get_macro_indicator",
    "description": "Get a single macro economic indicator from FRED (Federal Reserve St. Louis). Authoritative source for US macro data. Use either a pre-mapped key (us_cpi_yoy, us_core_cpi_yoy, us_unemployment, fed_funds_rate, 10y_treasury, 2y_treasury, yield_curve_10y_2y, vix_close, wti_oil, dollar_index_broad, us_gdp_real_yoy, us_industrial_prod, us_initial_claims, us_retail_sales, us_housing_starts, high_yield_spread, ig_credit_spread) OR a raw FRED series_id (e.g. CPIAUCSL, DGS10, T10Y2Y). Returns latest value, YoY change if applicable, change vs prev release, and last 12 observations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "indicator": {"type": "string", "description": "Mapped key or FRED series_id"},
            "last_n": {"type": "integer", "description": "Numero osservazioni storiche (default 12, max 100)", "default": 12}
        },
        "required": ["indicator"]
    }
})
TOOLS_SCHEMA.append({
    "name": "get_macro_dashboard",
    "description": "ONE-SHOT MACRO DASHBOARD: returns all key macro indicators in a single call (CPI, core CPI, unemployment, Fed funds, yield curve, VIX, oil, Broad Dollar Index, GDP, industrial prod, claims, retail sales, housing, IG/HY credit spreads). NOTE: dollar_index_broad is the FRED Broad Dollar Index (~120 scale), NOT the ICE DXY (~100 scale) - never call it DXY. Use this INSTEAD of calling get_macro_indicator multiple times. Includes computed yield_curve_10y_2y status (inverted/positive) and real_fed_funds_pct.",
    "input_schema": {"type": "object", "properties": {}}
})
TOOL_DISPATCHER["get_macro_indicator"] = tool_get_macro_indicator
TOOL_DISPATCHER["get_macro_dashboard"] = tool_get_macro_dashboard


def tool_get_yield_curves():
    """Curve dei rendimenti REALI (US / Germania / Giappone) + credito HY europeo,
    dal tool macro_rates. Ogni serie porta as_of, variazione m/m in bps e pendenza
    2s10s. Serie senza fonte free (Italia BTP / spread BTP-Bund) sono DICHIARATE
    non disponibili, mai stimate (regola no-fallback-silenziosi)."""
    try:
        import bellomberg.market_data.macro_rates as _mr  # import lazy: evita import circolare
    except Exception as e:
        return {"error": "macro_rates non importabile: " + str(e)[:160]}
    out = {"source": "macro_rates (FRED Treasury / Deutsche Bundesbank / MOF Japan / ICE BofA)",
           "curves": {}, "credit": {}}
    for k, fn, label in (("US", _mr.get_us_curve, "US Treasury CMT"),
                         ("DE", _mr.get_bund_curve, "Germania Bund (Svensson)"),
                         ("JP", _mr.get_jgb_curve, "Giappone JGB")):
        try:
            c = fn()
        except Exception as e:
            out["curves"][k] = {"status": "error", "error": str(e)[:140]}
            continue
        entry = {"status": c.get("status"), "as_of": c.get("as_of"),
                 "label": label, "src": c.get("source")}
        pts = c.get("points") or []
        if pts:
            yld = {p["tenor"]: p["value"] for p in pts}
            entry["yields_pct"] = yld
            mm = {p["tenor"]: round((p["value"] - p["value_1m"]) * 100, 1)
                  for p in pts if p.get("value_1m") is not None}
            if mm:
                entry["change_bps_1m"] = mm
            yy = {p["tenor"]: round((p["value"] - p["value_1y"]) * 100, 1)
                  for p in pts if p.get("value_1y") is not None}
            if yy:
                entry["change_bps_1y"] = yy
            if "2Y" in yld and "10Y" in yld:
                slope = round((yld["10Y"] - yld["2Y"]) * 100, 1)
                entry["slope_2s10s_bps"] = slope
                entry["curve_shape"] = "invertita" if slope < 0 else "positiva"
        if c.get("gaps"):
            entry["gaps"] = c["gaps"]
        if c.get("label"):
            entry["caveat"] = c["label"]
        out["curves"][k] = entry
    # credito HY europeo (proxy iTraxx Crossover)
    try:
        hy = _mr.get_eu_hy_credit()
        pts = hy.get("points") or []
        if pts:
            last = pts[-1]
            cinfo = {"level_bps": round(last["value"] * 100, 0), "as_of": hy.get("as_of"),
                     "status": hy.get("status"), "caveat": hy.get("label"), "src": hy.get("source")}
            p1m = _mr._pick_prev(pts, last["date"], 30)
            p1y = _mr._pick_prev(pts, last["date"], 365)
            if p1m:
                cinfo["change_bps_1m"] = round((last["value"] - p1m["value"]) * 100, 0)
            if p1y:
                cinfo["change_bps_1y"] = round((last["value"] - p1y["value"]) * 100, 0)
            out["credit"]["eu_hy_oas"] = cinfo
        else:
            out["credit"]["eu_hy_oas"] = {"status": hy.get("status"), "error": hy.get("error")}
    except Exception as e:
        out["credit"]["eu_hy_oas"] = {"status": "error", "error": str(e)[:140]}
    # Review I-1 (26/07 sera-4): i due buchi avevano la STESSA motivazione e ne hanno due
    # diverse — e da oggi questo testo arriva al modello, che lo ripeterebbe al PM nel memo.
    out["unavailable"] = ("Italia BTP (curva e spread BTP-Bund): nessuna fonte gratuita "
                          "giornaliera, servirebbe un connettore a pagamento (scelta PM: no). "
                          "Francia OAT-Bund: una fonte gratuita ESISTE (Banque de France "
                          "Webstat TEC10, macro_rates.get_fr_oat_10y) ma la chiave non e' "
                          "registrata e il PM ha deciso 'Francia skip'. In entrambi i casi "
                          "NON stimarli: dichiarali n.d.")
    return out


# Descrizione del tool, tenuta in una costante NOMINATA perche' vive in due registri.
# Review I-1 (26/07 sera-4): la prima stesura prometteva al modello campi che il payload
# emette solo A CONDIZIONE — e il prompt macro ORDINA di citarli. Un campo promesso e
# assente e' un invito ad allucinare con tanto di [src] falso: ora la condizionalita' e'
# scritta, e c'e' la precedenza dichiarata contro get_macro_dashboard (che deriva un suo
# 2s10s da 2 serie: due misure zitte sulla stessa grandezza = regola di casa violata).
YIELD_CURVES_DESCRIPTION = (
    "REAL daily government yield curves for US, Germany, Japan (full term structure) + "
    "European HY credit spread (ICE BofA EUR HY OAS, proxy for iTraxx Crossover - a cash "
    "bond OAS, NOT a CDS: cite it as a labeled proxy). EVERY series carries an as_of date. "
    "CONDITIONAL fields - if a field is ABSENT the data is NOT available: declare it n.d., "
    "never derive or guess it. `change_bps_1m`/`change_bps_1y` appear only when the source "
    "has that history; `slope_2s10s_bps` and `curve_shape` ('positiva'/'invertita', Italian "
    "values) only when BOTH the 2Y and 10Y tenors exist; the credit leg never has a slope; "
    "`gaps` lists the missing tenors. Sources: FRED, Deutsche Bundesbank, MOF Japan. Series "
    "with no free source are DECLARED in the `unavailable` field, never faked - do NOT "
    "estimate them. PRECEDENCE over get_macro_dashboard: for term-structure LEVELS and the "
    "2s10s slope THIS tool wins (full daily curve, dated per source); the "
    "`yield_curve_10y_2y_bps` of get_macro_dashboard is a 2-series derivation - never cite "
    "both as if they were two independent measurements of the same thing."
)

TOOLS_SCHEMA.append({
    "name": "get_yield_curves",
    # ⚠️ Questa descrizione e' DUPLICATA in chat_tools.TOOL_DEFINITIONS (registro vivo):
    # le due copie devono restare IDENTICHE — c'e' un test che lo inchioda
    # (tests/test_i1_fili_rotti.py::test_le_due_copie_della_descrizione_non_divergono).
    "description": YIELD_CURVES_DESCRIPTION,
    "input_schema": {"type": "object", "properties": {}}
})
TOOL_DISPATCHER["get_yield_curves"] = tool_get_yield_curves


# === QUANT TOOLS ===

def tool_quant_compute(operation, tickers=None, period="6mo", benchmark="SPY", confidence=0.95):
    """
    Calcoli quantitativi sul portafoglio o su lista di ticker.

    operations supportate:
    - "var_cvar": Value-at-Risk e Conditional VaR (95% o 99% configurabile) a 1-week
    - "sharpe": Sharpe ratio annualizzato
    - "max_drawdown": Max drawdown storico % e durata in giorni
    - "beta": Beta vs benchmark (SPY o altro)
    - "correlation_matrix": Matrice correlazione tra tickers (pair-wise)
    - "factor_exposure": Beta a 4 fattori (SPY market, IWM size, IWD value, QQQ momentum)
    - "kelly_size": Kelly fraction per nuova posizione (richiede expected return + vol)
    - "rolling_corr_break": Identifica break correlazione 30d vs 90d
    - "monte_carlo": 1000 simulazioni a 4w del portafoglio (returns log-normal)
    """
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        return {"error": "numpy non installato"}
    if not YFINANCE_AVAILABLE:
        return {"error": "yfinance richiesto"}
    if not tickers:
        return {"error": "tickers richiesto"}

    if isinstance(tickers, str):
        tickers = [tickers]
    if not isinstance(tickers, list) or any(not isinstance(t, str) or not t.strip() for t in tickers):
        return {"error": "tickers deve essere una lista di simboli non vuoti"}
    tickers = list(dict.fromkeys(tickers))
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not np.isfinite(confidence) or not 0 < confidence < 1):
        return {"error": "confidence deve essere un numero finito strettamente fra 0 e 1"}

    # Scarica returns
    try:
        from bellomberg.cli.price_updater import data_ticker
        requested = tickers + ([benchmark] if benchmark not in tickers else [])
        aliases = {t: data_ticker(t) for t in requested}
        data = yf.download(list(dict.fromkeys(aliases.values())),
                           period=period, progress=False, auto_adjust=True)
    except Exception as e:
        return {"error": "Errore download yfinance: " + str(e)}

    if data is None or len(data) == 0:
        return {"error": "No data"}

    # Estrai Close
    if "Close" in data.columns.get_level_values(0):
        closes = data["Close"]
    else:
        closes = data

    if hasattr(closes, "to_frame"):
        closes = closes.to_frame()
    # Expand aliases from the same mapping used for this download; never label another
    # security's series as the requested symbol or reread a changing alias registry.
    closes = pd.DataFrame({t: closes[a] for t, a in aliases.items() if a in closes.columns})
    if not isinstance(closes.index, pd.DatetimeIndex):
        return {"error": "indice temporale dei prezzi non valido"}
    closes = closes.sort_index()
    closes = closes[~closes.index.duplicated(keep="last")]
    closes = closes.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    closes = closes.where(closes > 0)
    # audit/11 §4: niente ffill globale ne' dropna totale — il ffill iniettava rendimenti
    # ZERO nei weekend equity quando in panel c'era crypto (bar 7/7) e il dropna finale
    # troncava TUTTI i ticker alla storia del piu' corto. Ogni ticker tiene la SUA storia
    # (le op per-ticker fanno gia' rets[t].dropna(); beta fa gia' il join pairwise).
    closes = closes.dropna(how="all")
    if len(closes) < 20:
        return {"error": "Storia insufficiente: " + str(len(closes)) + " righe"}

    # Remove each asset's non-trading rows BEFORE computing its returns. Global
    # pct_change used to insert weekend zeros (pandas 2) or lose Monday (pandas 3).
    rets = pd.DataFrame({t: closes[t].dropna().pct_change(fill_method=None) for t in closes}).dropna(how="all")
    log_rets = pd.DataFrame({t: np.log(closes[t].dropna()).diff() for t in closes}).dropna(how="all")
    calendars = {t: (365 if any(closes[t].dropna().index.dayofweek >= 5) else 252) for t in closes}

    def pair_returns(names):
        # Both legs cover the SAME interval, including a crypto/equity weekend.
        return closes[names].dropna().pct_change(fill_method=None).dropna()

    def risk_free(t):
        try:
            from bellomberg.market_data.market_inputs import get_risk_free_ex
            currency = yf.Ticker(aliases[t]).fast_info.get("currency")
            if not isinstance(currency, str) or not currency.strip():
                raise ValueError("valuta di quotazione non consegnata da yfinance")
            currency = "GBP" if currency in ("GBp", "GBX") else currency.upper()
            record = dict(get_risk_free_ex(currency))
            value = record.get("value")
            record.update(currency=currency, currency_source="yfinance fast_info", proxy_note="10Y risk-free proxy from market_inputs; rate/source/date/staleness retained")
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not np.isfinite(value)):
                raise ValueError("risk-free assente o non finito")
            return value, record, None
        except Exception as error:
            return None, None, "Risk-free n.d.: " + str(error)

    result = {"operation": operation, "tickers": tickers, "period": period, "n_obs": len(rets),
              "source": "yfinance adjusted daily Close",
              "calendar_note": "Per-asset native returns; 365 annual days/7 weekly days if observed weekend bars, otherwise convention 252/5. Pair returns use common dates. Missing/nonpositive/nonfinite prices excluded.",
              "errors": {}}
    for t in tickers:
        if t not in rets or len(rets[t].dropna()) < 20:
            result[t] = {"error": "Meno di 20 rendimenti validi per " + t}
            result["errors"][t] = result[t]["error"]
    active_tickers = [t for t in tickers if t not in result["errors"]]

    if operation == "var_cvar":
        for t in active_tickers:
            if t not in rets.columns:
                continue
            r = rets[t].dropna()
            week_days = 7 if calendars[t] == 365 else 5
            daily_var = np.percentile(r, (1 - confidence) * 100)
            week_var = daily_var * np.sqrt(week_days)
            cvar = r[r <= daily_var].mean() * np.sqrt(week_days)
            result[t] = {
                "daily_var_pct": round(daily_var * 100, 3),
                "week_var_pct": round(week_var * 100, 3),
                "week_cvar_pct": round(cvar * 100, 3),
                "confidence": confidence,
                "weekly_observations": week_days,
                "interpretation": "Historical daily quantile scaled by sqrt(weekly observations), an approximation rather than a loss guarantee.",
            }

    elif operation == "sharpe":
        for t in active_tickers:
            if t not in rets.columns:
                continue
            r = rets[t].dropna()
            ann = calendars[t]
            rf, rf_record, rf_note = risk_free(t)
            sharpe = ((r.mean() - rf / ann) / r.std()) * np.sqrt(ann) if rf is not None and r.std() > 0 else None
            result[t] = {
                "sharpe_annualized": round(sharpe, 2) if sharpe is not None else None,
                "vol_annualized_pct": round(r.std() * np.sqrt(ann) * 100, 2),
                "return_annualized_pct": round(r.mean() * ann * 100, 2),
                "annualization_days": ann, "risk_free": rf_record, "risk_free_note": rf_note,
            }

    elif operation == "max_drawdown":
        for t in active_tickers:
            if t not in closes.columns:
                continue
            s = closes[t].dropna()
            cummax = s.cummax()
            dd = (s - cummax) / cummax
            mdd = dd.min()
            mdd_date = dd.idxmin()
            # Calcola durata: da peak a trough
            peak_date = s[:mdd_date].idxmax()
            duration_days = (mdd_date - peak_date).days if hasattr(mdd_date, "days") or mdd_date != peak_date else 0
            result[t] = {
                "max_drawdown_pct": round(mdd * 100, 2),
                "max_dd_date": str(mdd_date)[:10] if mdd_date is not None else None,
                "peak_date": str(peak_date)[:10] if peak_date is not None else None,
                "duration_days": int(duration_days),
                "current_dd_pct": round(dd.iloc[-1] * 100, 2),
            }

    elif operation == "beta":
        if benchmark not in rets.columns:
            return {"error": "benchmark " + benchmark + " non scaricato"}
        bench_ret = rets[benchmark]
        for t in active_tickers:
            if t == benchmark or t not in rets.columns:
                continue
            joined = pair_returns([t, benchmark]).rename(columns={t: "a", benchmark: "b"})
            if len(joined) < 20:
                result[t] = {"error": "Meno di 20 rendimenti su date comuni col benchmark"}
                continue
            # audit/11 §5: stessa ddof per cov e var (prima np.cov ddof=1 / np.var
            # ddof=0 gonfiava il beta di n/(n-1), +5% con n=20)
            from bellomberg.market_data.return_statistics import paired_beta_statistics
            statistic = paired_beta_statistics(joined[["a", "b"]])
            beta, corr = statistic['beta'], statistic['correlation']
            result[t] = {
                "beta_vs_" + benchmark: round(beta, 3) if beta is not None else None,
                "correlation": round(corr, 3) if corr is not None else None,
                "n_obs": len(joined),
            }

    elif operation == "correlation_matrix":
        corr = pd.DataFrame(index=rets.columns, columns=rets.columns, dtype=float)
        for a in corr.columns:
            for b in corr.columns:
                if a == b:
                    corr.loc[a, b] = 1.0 if len(rets[a].dropna()) >= 20 and rets[a].std() > 0 else np.nan
                else:
                    joined = pair_returns([a, b])
                    corr.loc[a, b] = joined[a].corr(joined[b]) if len(joined) >= 20 else np.nan
        corr = corr.round(3)
        # Solo coppie significative
        pairs = []
        cols = list(corr.columns)
        for i, a in enumerate(cols):
            for b in cols[i+1:]:
                if np.isfinite(corr.loc[a, b]):
                    pairs.append({"pair": a + "-" + b, "corr": float(corr.loc[a, b])})
        pairs.sort(key=lambda x: abs(x["corr"]), reverse=True)
        result["correlation_pairs"] = pairs[:20]
        result["matrix"] = {c: {k: float(v) if np.isfinite(v) else None for k, v in corr[c].items()} for c in corr.columns}

    elif operation == "factor_exposure":
        # Carica i 4 fattori: SPY (market), IWM (size), IWD (value), QQQ (momentum)
        factor_tickers = ["SPY", "IWM", "IWD", "QQQ"]
        try:
            fac_data = yf.download(factor_tickers, period=period, progress=False, auto_adjust=True)["Close"]
        except Exception as e:
            return {"error": "Factor download fail: " + str(e)}
        for t in active_tickers:
            if t not in rets.columns:
                continue
            joined = closes[t].rename("y").to_frame().join(fac_data).dropna().pct_change(fill_method=None).dropna()
            if len(joined) < 30:
                result[t] = {"error": "Meno di 30 rendimenti su date comuni coi fattori"}
                continue
            X = joined[factor_tickers].values
            y = joined["y"].values
            # OLS via numpy: coef = (X'X)^-1 X'y
            try:
                X_const = np.column_stack([np.ones(len(X)), X])
                coef = np.linalg.lstsq(X_const, y, rcond=None)[0]
                result[t] = {
                    "alpha_daily_pct": round(coef[0] * 100, 4),
                    "alpha_annualized_pct": round(coef[0] * 252 * 100, 2),
                    "beta_market_SPY": round(coef[1], 3),
                    "beta_smallcap_IWM": round(coef[2], 3),
                    "beta_value_IWD": round(coef[3], 3),
                    "beta_momentum_QQQ": round(coef[4], 3),
                }
            except Exception:
                continue

    elif operation == "kelly_size":
        # kelly = (b * p - q) / b dove b = win/loss ratio, p = prob win, q = 1-p
        # Per investing: kelly = (mu - rf) / sigma^2 (simplified)
        for t in active_tickers:
            if t not in rets.columns:
                continue
            r = rets[t].dropna()
            mu = r.mean()
            sig2 = r.var()
            rf, rf_record, rf_note = risk_free(t)
            kelly = (mu - rf / calendars[t]) / sig2 if sig2 > 0 and rf is not None else None
            # Quarter Kelly (sicurezza)
            quarter_kelly = kelly / 4 if kelly is not None else None
            result[t] = {
                "kelly_fraction": round(kelly, 3) if kelly is not None else None,
                "quarter_kelly_pct_of_portfolio": round(quarter_kelly * 100, 2) if quarter_kelly is not None else None,
                "annualization_days": calendars[t], "risk_free": rf_record, "risk_free_note": rf_note,
                "note": "Historical-return Kelly estimate, not an expected-return forecast. Quarter Kelly is a 1/4 scaling convention, not an approved portfolio weight.",
            }

    elif operation == "rolling_corr_break":
        if len(tickers) < 2:
            return {"error": "Servono almeno 2 tickers"}
        a, b = tickers[0], tickers[1]
        if a == b:
            return {"error": "Servono due ticker distinti"}
        if a not in rets.columns or b not in rets.columns:
            return {"error": "Tickers non in dati"}
        # allineamento pairwise sui soli giorni quotati da ENTRAMBI (audit/11: senza
        # ffill globale le serie possono avere buchi diversi, es. crypto vs equity)
        _ab = pair_returns([a, b])
        if len(_ab) < 90:
            return {"error": "Servono 90 rendimenti su date comuni per confrontare 30/90 osservazioni"}
        r_a, r_b = _ab[a], _ab[b]
        corr_30 = r_a.tail(30).corr(r_b.tail(30))
        corr_90 = r_a.tail(90).corr(r_b.tail(90))
        if not np.isfinite(corr_30) or not np.isfinite(corr_90):
            return {"error": "Correlazione non misurabile: serie costante o dati non validi"}
        delta = corr_30 - corr_90
        result["correlation_30d"] = round(corr_30, 3)
        result["correlation_90d"] = round(corr_90, 3)
        result["delta"] = round(delta, 3)
        result["regime_break"] = abs(delta) > 0.3
        result["interpretation"] = ("Significant correlation regime change" if abs(delta) > 0.3
                                    else "Stable correlation regime")

    elif operation == "monte_carlo":
        # Simula 1000 percorsi a 4 settimane (20 giorni) usando log-normal su tutti i ticker
        n_sims = 1000
        horizon = 20
        for t in active_tickers:
            if t not in log_rets.columns:
                continue
            r = log_rets[t].dropna()
            horizon = 28 if calendars[t] == 365 else 20
            mu = r.mean()
            sig = r.std()
            # Simula
            sims = np.random.normal(mu, sig, size=(n_sims, horizon))
            total_returns = np.exp(sims.sum(axis=1)) - 1
            result[t] = {
                "p5_pct": round(np.percentile(total_returns, 5) * 100, 2),
                "p25_pct": round(np.percentile(total_returns, 25) * 100, 2),
                "median_pct": round(np.percentile(total_returns, 50) * 100, 2),
                "p75_pct": round(np.percentile(total_returns, 75) * 100, 2),
                "p95_pct": round(np.percentile(total_returns, 95) * 100, 2),
                "prob_positive": round(float((total_returns > 0).mean()), 3),
                "prob_loss_gt_10pct": round(float((total_returns < -0.10).mean()), 3),
                "horizon_observations": horizon,
            }
    else:
        return {"error": "operation non riconosciuta: " + operation
                + ". Valide: var_cvar, sharpe, max_drawdown, beta, correlation_matrix, factor_exposure, kelly_size, rolling_corr_break, monte_carlo"}

    return result


TOOLS_SCHEMA.append({
    "name": "quant_compute",
    "description": "QUANT ANALYTICS: VaR, Sharpe, beta, factor exposure (SPY/IWM/IWD/QQQ), correlation matrix, Kelly sizing, regime detection, Monte Carlo. Operations: 'var_cvar' (Value-at-Risk 1w), 'sharpe' (Sharpe ratio + vol + return annualized), 'max_drawdown' (% + duration), 'beta' (vs benchmark, default SPY), 'correlation_matrix' (pair-wise top 20), 'factor_exposure' (4-factor model), 'kelly_size' (optimal sizing), 'rolling_corr_break' (regime detection 30d vs 90d), 'monte_carlo' (1000 sims a 4w, dist pct). Pass tickers as list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "description": "Vedi descrizione"},
            "tickers": {"type": "array", "items": {"type": "string"}, "description": "Lista ticker, es. ['AAPL', 'GOOG', 'SPY']"},
            "period": {"type": "string", "description": "Storia: '3mo', '6mo', '1y', '2y'. Default '6mo'", "default": "6mo"},
            "benchmark": {"type": "string", "description": "Per beta. Default 'SPY'", "default": "SPY"},
            "confidence": {"type": "number", "description": "Per VaR: 0.95 o 0.99. Default 0.95", "default": 0.95}
        },
        "required": ["operation", "tickers"]
    }
})
TOOL_DISPATCHER["quant_compute"] = tool_quant_compute


# === DCF MODEL TOOL ===

def tool_build_dcf_model(ticker, wacc=None, perpetual_growth=None, horizon_years=None, *,
                         prepared_bundle=None, sector_providers=None, as_of=None,
                         method_records=None, analysis_context=None):
    """Genera la valutazione col MOTORE A SUB-SETTORI (#165/#182): instrada al motore
    giusto (operativa buy-side a 9 fogli / banca DDM-residual income / ETF nota),
    con peer omogenei del sotto-settore, valuta convertita e WACC Damodaran.
    Solo per candidati GREEN del Quant. Gli errori non attivano motori alternativi.
    """
    if wacc is not None or horizon_years is not None:
        return {"ticker": ticker.upper(), "error": "wacc e horizon_years non sono "
                "override supportati dal motore a sub-settori; usare get_valuation "
                "per i driver e gli override supportati. Nessun modello prodotto."}
    # MOTORE NUOVO: dcf_engine.generate_valuation (sub-settori + 2 motori + Damodaran)
    try:
        from bellomberg.valuation.dcf_engine import generate_valuation
        kwargs = {"output_dir": str(MODELS_DIR)}
        if method_records is not None:
            kwargs["method_records"] = method_records
        if analysis_context is not None:
            kwargs["analysis_context"] = analysis_context
        if perpetual_growth is not None:
            kwargs["terminal_growth"] = perpetual_growth
        if prepared_bundle is not None:
            kwargs["prepared_bundle"] = prepared_bundle
        if sector_providers is not None:
            kwargs["providers"] = sector_providers
        if as_of is not None:
            kwargs["as_of"] = as_of
        r = generate_valuation(ticker, **kwargs)
        if isinstance(r, dict):
            from bellomberg.valuation.dcf_quality import normalize_valuation_payload
            cutoff = as_of or (prepared_bundle or {}).get("case", {}).get("as_of")
            r = normalize_valuation_payload(r, as_of=cutoff)
            eng = r.get("engine")
            if r.get("error") or r.get("ok") is False:
                return {**r, "path": r.get("path") if eng == "managed_care" else None,
                        "error": str(r.get("error") or "Motore DCF: esito KO"),
                        "ticker": ticker.upper(), "engine": eng,
                        "subsector": r.get("subsector"),
                        "engine_note": ("Snapshot managed care incompleto; fair value n.d."
                                        if eng == "managed_care" else "Modello non prodotto; nessun fallback legacy.")}
            if eng == "etf_passive":
                return {**r, "ticker": ticker.upper(), "engine": "etf_passive", "path": None,
                        "note": "ETF: nessun DCF: valutazione per NAV / esposizione fattoriale (sub-settore etf)",
                        "subsector": r.get("subsector")}
            if r.get("path"):
                dw = r.get("damodaran_wacc") or {}
                return {**r, "path": r.get("path"), "ticker": ticker.upper(),
                        "engine": eng, "subsector": r.get("subsector"),
                        "company": r.get("company"), "price": r.get("price"),
                        # P0 17/07 (review F5): l'esito del bake valori viaggia col
                        # risultato — un file senza valori cached NON passa in silenzio
                        "values_baked": r.get("values_baked"), "bake_note": r.get("bake_note"),
                        "bake_error": r.get("bake_error"),
                        "method": r.get("method"), "multiple_basis": r.get("multiple_basis"),
                        "peers_used": r.get("peers_used") or r.get("peers"),
                        # review 17/07 (peer banca): la nota di selezione/sanity dei
                        # peer viaggia anche su questo canale, non solo su get_valuation
                        "peer_note": r.get("peer_note"),
                        "wacc_pct": (dw["wacc_pct"] if dw.get("wacc_pct") is not None
                                     else dw["wacc"] * 100 if dw.get("wacc") is not None else None),
                        "beta_levered": dw.get("beta_levered"),
                        "sanity": r.get("sanity"), "fx_conversion": r.get("fx_conversion"),
                        "exclude_from_action_table": r.get("exclude_from_action_table"),
                        "n_sheets": r.get("n_sheets"), "sheets": r.get("sheets"),
                        "engine_note": ("Managed care: snapshot dei flussi equity distribuibili al cutoff dichiarato."
                                       if eng == "managed_care" else "Motore sub-settore #182 (peer omogenei, WACC Damodaran; "
                                       "fair value convertito nella valuta del prezzo solo se "
                                       "financialCurrency != quotazione, v. campo fx_conversion)")}
        return {"ticker": ticker.upper(), "error": "Risposta DCF incompleta o non valida: "
                "modello non prodotto; nessun fallback legacy."}
    except Exception as e:
        return {"ticker": ticker.upper(), "error": "DCF build error: " + str(e),
                "engine_note": "Modello non prodotto; nessun fallback legacy."}


TOOLS_SCHEMA.append({
    "name": "build_dcf_model",
    "description": "Generate an Excel valuation using the validated sub-sector engine and its supported market data. Use only after the Quant Specialist validates portfolio fit. The engine selects the operating-company or financial-sector model; ETFs return an explicit no-DCF result. Returns the file path, engine, data-quality and workbook-bake status. Missing data and engine failures are declared; no legacy fallback is used.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Exact exchange-qualified ticker supported by the data provider"},
            "perpetual_growth": {"type": "number", "description": "Terminal growth override as a decimal, validated by the selected engine. Omit to use its declared assumptions."},
            "method_records": method_records_schema(),
            "analysis_context": {"type": "object", "description": "Documentazione del metodo. Managed care: scenario_rationale bear/base/bull e revisions, prove nei method_records."}
        },
        "required": ["ticker"]
    }
})
TOOL_DISPATCHER["build_dcf_model"] = tool_build_dcf_model


# === BELLOMBERG MEMORY-AWARE PORTFOLIO TOOLS ===

def _try_memory_db():
    """Lazy import MemoryDB - non rompe se memory_db non esiste ancora."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        return MemoryDB()
    except Exception:
        return None


def tool_get_portfolio_live_v2(filepath=None):
    """Versione memory-aware: legge prima da SQLite (live), fallback su Excel se DB vuoto.
    Drop-in replacement per il vecchio tool_get_portfolio_live.
    """
    db = _try_memory_db()
    if db:
        try:
            snap = db.get_portfolio_summary()
            if snap.get("n_positions", 0) > 0:
                return snap
        except Exception as e:
            print("[!] DB read failed, falling back to Excel: " + str(e))
    # Fallback Excel
    return tool_get_portfolio_live(filepath)


def tool_get_pending_decisions():
    """Pending decisions del Capo non ancora eseguite dal PM."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        db = MemoryDB()
        # Fable 5 16/07: era status_filter="pending" minuscolo, ma il DB salva 'PENDING'
        # e il WHERE di memory_db e' case-sensitive -> il tool tornava SEMPRE 0 righe,
        # da quando esiste. Trovato dalla ricognizione (58 PENDING reali, 0 'pending').
        decs = db.get_recent_decisions(n=20, status_filter="PENDING")
        return {"count": len(decs), "decisions": decs}
    except Exception as e:
        return {"error": "pending decisions error: " + str(e)}


def tool_add_research_note(decision_id, note):
    """F10 v3 (mandato PM 16/07): la run risponde alle note del PM sul filo di una
    decisione RESEARCH. Scrive con autore='AI'; il DB valida che la decisione esista
    e sia RESEARCH (mai note su decisioni operative)."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        nid = MemoryDB().add_decision_note(decision_id, "AI", note)
        if not nid:
            return {"error": f"nota rifiutata: decisione {decision_id} inesistente, "
                             "non-RESEARCH o tabella decision_notes non migrata"}
        return {"ok": True, "note_id": nid, "decision_id": decision_id}
    except Exception as e:
        return {"error": "add_research_note error: " + str(e)}


def tool_search_past_memos(query, n_results=5):
    """Semantic search sui memo consigliere storici via ChromaDB."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        db = MemoryDB()
        results = db.search_memos_semantic(query, n_results=n_results)
        return {"query": query, "count": len(results), "memos": results}
    except Exception as e:
        return {"error": "semantic memo search error: " + str(e)}


def tool_check_decision_outcomes():
    """Verifica outcome decisioni passate: prezzo entry vs corrente, P/L %."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        db = MemoryDB()
        decs = db.get_recent_decisions(n=30)
        out = []
        for d in decs:
            row = {
                "id": d.get("id"),
                "ticker": d.get("ticker"),
                "action": d.get("action"),
                "timestamp": d.get("timestamp"),
                "status": d.get("status"),
                "eur_amount": d.get("eur_amount"),
                "outcome_pct": d.get("outcome_pct"),
                "pm_feedback": d.get("pm_feedback"),
            }
            # Compute current outcome se ticker valido
            ticker = d.get("ticker")
            if ticker and YFINANCE_AVAILABLE and (d.get("status") or "").upper() == "EXECUTED":
                try:
                    tk = yf.Ticker(ticker)
                    hist = tk.history(period="3mo")
                    if not hist.empty:
                        row["current_price"] = float(hist["Close"].iloc[-1])
                except Exception:
                    pass
            out.append(row)
        return {"count": len(out), "decisions": out}
    except Exception as e:
        return {"error": "decision outcomes error: " + str(e)}


# === #195: TOOL QUIVER (congress/lobbying/gov contracts) + INSIDER per gli agenti ===
def tool_get_congress_trades(ticker=None, limit=50):
    """Trade azionari del Congresso USA (Quiver). ticker opzionale (None = ultimi aggregati)."""
    try:
        from bellomberg.market_data.quiver_data import quiver_available, get_congress_trades
        if not quiver_available():
            return {"error": "QUIVER_API_KEY non configurata"}
        return get_congress_trades(ticker=ticker, limit=limit)
    except Exception as e:
        return {"error": "quiver congress: " + str(e)}


def tool_get_lobbying(ticker, limit=30):
    """Spesa di lobbying di una societa' (Quiver)."""
    try:
        from bellomberg.market_data.quiver_data import quiver_available, get_lobbying
        if not quiver_available():
            return {"error": "QUIVER_API_KEY non configurata"}
        return get_lobbying(ticker, limit=limit)
    except Exception as e:
        return {"error": "quiver lobbying: " + str(e)}


def tool_get_gov_contracts(ticker, limit=30):
    """Contratti governativi USA assegnati a una societa' (Quiver)."""
    try:
        from bellomberg.market_data.quiver_data import quiver_available, get_gov_contracts
        if not quiver_available():
            return {"error": "QUIVER_API_KEY non configurata"}
        return get_gov_contracts(ticker, limit=limit)
    except Exception as e:
        return {"error": "quiver gov contracts: " + str(e)}


def tool_get_insider_trades(ticker, days=90):
    """Insider trades / Form 4 (Finnhub se disponibile, fallback SEC EDGAR)."""
    try:
        try:
            from bellomberg.market_data.finnhub_news import fetch_insider_trades as _fn
            r = _fn(ticker, days=days)
            if r:
                return {"ticker": ticker, "source": "finnhub", "count": len(r), "trades": r[:30]}
        except Exception:
            pass
        from bellomberg.market_data.sec_edgar import get_insider_trades as _sec
        r = _sec(ticker, days=days)
        return {"ticker": ticker, "source": "sec_edgar", "count": len(r) if r else 0, "trades": (r or [])[:30]}
    except Exception as e:
        return {"error": "insider trades: " + str(e)}


TOOLS_SCHEMA.append({"name": "get_congress_trades", "description": "US CONGRESS stock trades (Quiver Quantitative, PAID). Which senators/representatives bought or sold a ticker and when - a strong political smart-money signal. Pass a ticker, or omit for the latest congressional trades across the market. Core tool for political/policy theses.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Ticker (optional; omit for latest market-wide)"}, "limit": {"type": "integer", "default": 50}}}})
TOOLS_SCHEMA.append({"name": "get_lobbying", "description": "Corporate LOBBYING spend disclosures (Quiver, PAID). How much a company spends lobbying and on which issues - signals regulatory exposure and political positioning. Use for policy/regulation theses.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "limit": {"type": "integer", "default": 30}}, "required": ["ticker"]}})
TOOLS_SCHEMA.append({"name": "get_gov_contracts", "description": "US GOVERNMENT CONTRACTS awarded to a company (Quiver, PAID). Federal awards are a hard revenue/visibility signal (defense, healthcare, infrastructure). Use for fundamentals + policy theses.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "limit": {"type": "integer", "default": 30}}, "required": ["ticker"]}})
TOOLS_SCHEMA.append({"name": "get_insider_trades", "description": "INSIDER TRADES / Form 4 - officers and directors buying or selling (Finnhub PAID, fallback SEC EDGAR). Insider buying is bullish, clustered selling a caution. Use for fundamentals conviction.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "days": {"type": "integer", "default": 90}}, "required": ["ticker"]}})
TOOL_DISPATCHER["get_congress_trades"] = tool_get_congress_trades
TOOL_DISPATCHER["get_lobbying"] = tool_get_lobbying
TOOL_DISPATCHER["get_gov_contracts"] = tool_get_gov_contracts
TOOL_DISPATCHER["get_insider_trades"] = tool_get_insider_trades


def tool_get_filing_changes(ticker):
    from bellomberg.agents.filing_context import get_filing_changes
    return get_filing_changes(ticker)


TOOLS_SCHEMA.append({"name": "get_filing_changes",
                     "description": "Sola lettura archivio locale dei filing: confronto, citazioni, copertura, freschezza e giudizio; nessun nuovo fetch o fair value.",
                     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}},
                                      "required": ["ticker"]}})
TOOL_DISPATCHER["get_filing_changes"] = tool_get_filing_changes
