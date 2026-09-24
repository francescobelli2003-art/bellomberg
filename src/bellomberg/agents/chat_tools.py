"""
BELLOMBERG - Chat Tools registry per Anthropic native tool_use API.

DESIGN PRINCIPLES (anti-hallucination):
1. Ogni tool ritorna SOLO dati reali da fonti verificate (yfinance, FRED, IBKR,
   Polymarket, K. French, NewsAPI, calcoli numpy/statsmodels).
2. Output strutturato con campo `_source` esplicito e `_timestamp`.
3. Errori NON inventati: se la fonte e' down, ritorna {"error": "..."}.
4. L'agente DEVE chiamare un tool prima di citare qualunque numero.

CATEGORIE:
- quant     : VaR, GARCH, Fama-French factors, correlation (tutto Python deterministico)
- data      : prezzi live, fundamentals, options chain (yfinance/IBKR)
- search    : news (Tavily/NewsAPI), Polymarket, web (Tavily)
- macro     : FRED indicators, yield curve
- portfolio : holdings, decisioni pending, ultimi memo
- memory    : semantic search sui memo storici, feedback PM

API:
  TOOL_DEFINITIONS -> list[dict] schema Anthropic tools
  dispatch(tool_name, tool_input, caller=None) -> dict result (with _source,
      _timestamp); caller = attribuzione fine del chiamante (V6 Lotto 3, oggi
      usata da add_guidance per entered_by)
"""
import json
from bellomberg.valuation.sector_analysis import method_records_schema
from datetime import datetime
from typing import Any, Dict, List, Tuple

from bellomberg.core.paths import REPORT_DIR


# ============================================================
# TOOL DEFINITIONS (JSON schema per Anthropic)
# ============================================================

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {"name": "get_filing_changes",
     "description": "Legge SOLO l'archivio locale dei confronti filing verificati. Espone periodo, copertura, freschezza, confronto corrente/storico, citazioni e stato del giudizio qualitativo. Nessuna acquisizione o nuova valutazione; un cambiamento e' un tripwire, non un segnale di trading o aggiornamento del FV.",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    # ---- QUANT (deterministic Python) ----
    {
        "name": "get_portfolio_risk",
        "description": "Risk metrics quantitativi calcolati su 1y daily returns: VaR 95%/99% (% e EUR), Sharpe annualizzato, Beta vs SPY, Max Drawdown 1y, correlation matrix top 8 holdings, alerts automatici. Cached 5 min.",
        "input_schema": {"type": "object", "properties": {
            "force_refresh": {"type": "boolean", "description": "true per bypassare cache (slow)"}
        }, "required": []},
    },
    {
        "name": "get_sector_exposure",
        "description": "Esposizione SETTORIALE del book: pesi EUR per settore (cash escluso, dichiarato), HHI settoriale, N effettivo, fonti per ticker (yfinance con cache / override DICHIARATI per ETF e veicoli: una DAT conta come Crypto treasury, non Technology, e la fonte per ticker dice se il settore viene da yfinance o da un override), bucket 'n.d.' dichiarato per i senza-settore, divergenze vs la mappa policy del sizing. Usalo per giudicare la concentrazione TEMATICA (3 banche in paesi diversi = stesso rischio settoriale).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_attribution",
        "description": "CONTRIBUTION attribution del rendimento (fase 1b): da dove viene il rendimento di periodo — contributi per posizione, bucket economico (asse unico) e valuta, split prezzo-locale vs FX (cross residuo dichiarato), linking Carino (la somma chiude ESATTA sul rendimento composto del capitale INVESTITO, cash escluso), riconciliazione dichiarata vs TWR ufficiale. NON è una Brinson vs benchmark (nessun benchmark definito). Cached 10 min. Usalo per dire COSA sta facendo o perdendo i soldi; per la performance ufficiale usa il TWR.",
        "input_schema": {"type": "object", "properties": {
            "period": {"type": "string", "enum": ["MTD", "YTD", "30D", "INCEPTION"],
                       "description": "finestra di misura (default YTD)"}
        }, "required": []},
    },
    {
        "name": "get_tearsheet",
        "description": "TEARSHEET istituzionale sulla serie TWR UFFICIALE: rendimenti MENSILI e annuali (parzialità dichiarata), episodi di DRAWDOWN (top 5 con date/durate + drawdown corrente aperto), ROLLING vol/Sharpe 30-90gg (solo finestre piene, mai accorciate), metriche scalari RIUSATE da advanced_metrics (Sharpe/Sortino/Calmar/VaR/benchmark SPY-EUR — fonte unica). Cached 10 min. Usalo per il QUADRO performance complessivo; per capire DA DOVE viene il rendimento usa get_attribution.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_portfolio_garch",
        "description": "GARCH(1,1) e GJR-GARCH(1,1,1) volatility forecasting. Model selection automatica via AIC + significativita' gamma. Ritorna current conditional vol annualizzata, forecast 1d/5d/22d con CI 95%, persistence, diagnostics (Ljung-Box, ARCH-LM). Cached 30 min.",
        "input_schema": {"type": "object", "properties": {
            "force_refresh": {"type": "boolean"}
        }, "required": []},
    },
    {
        "name": "get_portfolio_factors",
        "description": "Fama-French 5-factor + Momentum decomposition. Per ogni holding: alpha annualizzato, beta_market, beta_smb (size), beta_hml (value), beta_rmw (profitability), beta_cma (investment), beta_mom (momentum), t-stat, R^2. OLS con Newey-West HAC SE. Cached 1h.",
        "input_schema": {"type": "object", "properties": {
            "period": {"type": "string", "description": "'1y' (default) or '6mo'"},
            "force_refresh": {"type": "boolean"}
        }, "required": []},
    },
    {
        "name": "quant_compute",
        "description": "Calcolo quantitativo generale per ticker singolo o lista: var_cvar (VaR+CVaR), sharpe, beta, max_drawdown, correlation_matrix, factor_exposure, kelly_size, rolling_corr_break, monte_carlo.",
        "input_schema": {"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["var_cvar", "sharpe", "beta", "max_drawdown", "correlation_matrix", "factor_exposure", "kelly_size", "rolling_corr_break", "monte_carlo"]},
            "tickers": {"type": "array", "items": {"type": "string"}, "description": "Lista ticker"},
            "params": {"type": "object", "description": "Parametri specifici (es. {'confidence': 0.95, 'period': '6mo', 'benchmark': 'SPY'})"}
        }, "required": ["operation", "tickers"]},
    },
    {
        "name": "get_portfolio_montecarlo",
        "description": "Monte Carlo simulation 10k sim (default FHS bank-grade: GARCH + bootstrap residui) sul portfolio. Ritorna percentili P5/25/50/75/95 di valore portfolio a horizon, expected return, Sharpe simulato, probabilita' loss/gain thresholds, max drawdown distribution. Supporta stress replay STORICO ('gfc_2008', 'covid_2020': finestra reale scaricata ad hoc, nomi giovani via proxy beta x SPY dichiarati in stress_meta.proxied) e 'shock_3sigma'. Il campo stress_scenario nel payload e' quello APPLICATO davvero: se stress_fallback=true il replay non era possibile (fallback dichiarato). stress_meta.window_loss_pct/eur = perdita deterministica del replay da citare DICHIARANDO la base (stress_meta.basis: rendimenti in valuta LOCALE per-asset scalati sul NAV EUR — per il replay GFC direzione conservativa). Supporta what-if: add_tickers o remove_tickers.",
        "input_schema": {"type": "object", "properties": {
            "horizon_days": {"type": "integer", "description": "5/22/63/126/252/504 trading days (default 252=1y)"},
            "n_sims": {"type": "integer", "description": "Numero simulazioni (default 10000, max 50000)"},
            "stress": {"type": "string", "enum": ["none", "gfc_2008", "covid_2020", "shock_3sigma"], "description": "Scenario stress: replay storico 2008/2020 o shock -3 sigma (default none)"},
            "add_tickers": {"type": "array", "items": {"type": "string"}, "description": "Ticker da aggiungere al portfolio per what-if (es. ['AAPL', 'GOOG'])"},
            "remove_tickers": {"type": "array", "items": {"type": "string"}, "description": "Ticker da rimuovere per what-if"}
        }, "required": []},
    },
    {
        "name": "get_var_backtest",
        "description": "Backtest del VaR storico ufficiale (historical 95/99 1d): Kupiec POF (copertura) + Christoffersen (indipendenza/clustering delle eccezioni) su VaR rolling 252 obs, rendimenti EUR. Verdetto PASS/FAIL per confidenza. DICHIARATO: gira sul book corrente proiettato all'indietro (valida il MODELLO, non la P&L storica); il test sul 99% ha bassa potenza. Usalo per qualificare l'affidabilita' del VaR citato nel memo.",
        "input_schema": {"type": "object", "properties": {
            "window": {"type": "integer", "description": "Finestra rolling del VaR in obs (default 252)"},
            "period": {"type": "string", "description": "Storia totale (default '3y')"}
        }, "required": []},
    },

    # ---- DATA (live prices/fundamentals/options) ----
    {
        "name": "get_price_live",
        "description": "Prezzo live e indicatori tecnici (RSI, SMA20, SMA50, drawdown vs 52w high) di un ticker. Fonte: yfinance.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "Es. 'AAPL', 'ENEL.MI', 'RIO.L'"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_fundamentals",
        "description": "Fundamentals di un ticker: PE, FCF yield, gross margin, ROE, target price analyst, debt/equity. Fonte yfinance.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_options_data",
        "description": "Options data: IV ATM, put/call ratio, max pain, open interest, gamma exposure se disponibile. Prima prova IBKR TWS (real-time), fallback yfinance. Solo per US tickers.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "expiry": {"type": "string", "description": "Optional expiry date YYYY-MM-DD, default nearest"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_option_expirations_polygon",
        "description": "Lista date di scadenza opzioni disponibili per un sottostante US (Polygon professional). Usala PRIMA di get_options_chain_polygon per scegliere l'expiry giusta.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_options_chain_polygon",
        "description": "Chain opzioni professionale via Polygon: IV, delta/gamma/theta/vega, open interest e volume per ogni strike, su QUALSIASI expiry (YYYY-MM-DD). Fonte premium, preferiscila a get_options_data quando disponibile.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "expiry": {"type": "string", "description": "YYYY-MM-DD opzionale; senza, ritorna tutte le scadenze (cap 250 contratti)"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_cef_nav",
        "description": "NAV e SCONTO/PREMIO di un FONDO CHIUSO quotato: copre i fondi chiusi CONFIGURATI nel motore (NAV dal sito ufficiale del gestore, as-of dichiarato nel payload). E' il modo GIUSTO di valutare un fondo chiuso (il DCF e' VIETATO e viene rifiutato): ritorna NAV/share, data as-of (STALE se >14gg), prezzo convertito nella valuta del NAV (conversione dichiarata), sconto/premio % e ritorni MTD/QTD/YTD. Il segnale operativo e' lo sconto vs la sua storia. Ticker non configurati = errore dichiarato.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "Ticker del fondo chiuso, col suffisso di borsa (obbligatorio: nessun default; non configurato = errore dichiarato)"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_cef_lookthrough",
        "description": "LOOK-THROUGH di un FONDO CHIUSO del book (copre i fondi con gestore 13F configurato nel motore; un ticker non configurato riceve un errore dichiarato): NAV e sconto/premio correnti + COSA CONTIENE il fondo via 13F SEC del gestore (pesi % normalizzati, filing date dichiarata). Il 13F e' un PROXY ETICHETTATO (portafoglio del gestore, solo long US >200M$, lag ~45gg): le posizioni del fondo che il 13F non riporta restano fuori e il payload le DICHIARA per nome. Dottrina bilaterale PM 16/07: un CEF si giudica per cio' che contiene e per lo sconto sul NAV vs la sua storia, NON per il suo Sharpe trailing. USALO prima di proporre trim/add su un CEF.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "Ticker del fondo chiuso, col suffisso di borsa (obbligatorio: nessun default)"}
        }, "required": ["ticker"]},
    },
    {
        "name": "add_guidance",
        "description": "REGISTRA una GUIDANCE SOCIETARIA nel registro persistente (V6, decisione PM D1): usalo quando LEGGI una trimestrale/press release/presentazione con guidance NUMERICA. FONTE OBBLIGATORIA: source_doc (documento+riferimento, es. 'press release Q2 FY2026, sito IR') E source_date — senza, il registro RIFIUTA. Valori pct come FRAZIONE (0.18 = 18%). Se la societa' da' un RANGE passa value_low/value_high (alimentera' bear/base/bull, D3). La scadenza si calcola da sola (prossima trimestrale dal calendar, fallback 120g — D4); supersede automatico della guidance attiva precedente per la stessa GRANDEZZA, cioe' stesso ticker+metric+period E STESSA `unit` (storico conservato) — se resta attiva una riga con unit diversa la risposta te la DICHIARA nel campo 'convivono', leggilo: se e' la stessa grandezza scritta con un'altra etichetta, riscrivila con la `unit` identica per sostituirla. MAI registrare numeri a memoria, stime tue o consensus: SOLO cifre LETTE dal documento citato (il consensus ha il suo tool, e' un'altra cosa).",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "metric": {"type": "string", "enum": ["revenue_growth", "revenue_abs", "eps", "ebitda_margin", "gross_margin", "capex_pct", "other"], "description": "revenue_growth = crescita ricavi (frazione); revenue_abs = ricavi assoluti in MILIONI valuta bilancio; margini come frazione; other = QUALSIASI altra grandezza (EBITA, FOCF, ordini, debito netto, dividendo...): la grandezza va NOMINATA dentro `unit`, non solo in note"},
            "period": {"type": "string", "description": "Periodo coperto, es. 'FY2026', 'H2-2026', 'Q3-2026'"},
            "value_mid": {"type": "number", "description": "Valore centrale (o unico) della guidance"},
            "value_low": {"type": "number", "description": "Estremo basso del range, se la societa' lo da'"},
            "value_high": {"type": "number", "description": "Estremo alto del range, se la societa' lo da'"},
            "unit": {"type": "string", "description": "Solo per metric=other (obbligatoria li'); altrimenti default dichiarato per metrica. Per metric=other NON basta la valuta: `unit` e' cio' che IDENTIFICA la grandezza e decide il supersede, quindi deve nominarla — 'meur (EBITA FY)', 'meur (FOCF FY)', 'meur (ordini FY)'. Scrivendo 'meur' nuda su piu' target dello stesso ticker/periodo li rendi la STESSA grandezza e si sostituiscono a vicenda (l'EBITA sparirebbe sotto il FOCF). Usa la stessa etichetta, identica, quando aggiorni quel target al trimestre dopo."},
            "source_doc": {"type": "string", "description": "OBBLIGATORIA: documento e riferimento (es. '8-K earnings release 22/07/2026' o 'slide 12 presentazione Q2, IR')"},
            "source_date": {"type": "string", "description": "OBBLIGATORIA: data del documento, YYYY-MM-DD"},
            "note": {"type": "string", "description": "Contesto breve (es. 'guidance ALZATA da 15-17%')"}
        }, "required": ["ticker", "metric", "period", "value_mid", "source_doc", "source_date"]},
    },
    {
        "name": "get_guidance",
        "description": "Legge il registro guidance di un ticker (V6): righe ATTIVE con staleness dichiarata (scadute = STALE, non guidano piu' i default) + storiche a richiesta. USALO prima di modellare o registrare: eviti doppioni e vedi se la guidance in registro va aggiornata dall'ultima trimestrale.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "include_history": {"type": "boolean", "description": "true = anche le righe superseded (storico)"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_consensus_estimates",
        "description": "CONSENSUS DEGLI ANALISTI per un ticker (Yahoo): stime EPS e ricavi (trim/anno corrente e prossimo) con n. analisti, REVISIONI delle stime a 30/90gg (il momentum delle attese: alzano o tagliano?), target price mean/median/high/low con upside implicito, mix raccomandazioni e trend 3 mesi. USALO PRIMA di modellare o proporre: la tua tesi vale rispetto a COSA SI ASPETTA GIA' il mercato — una view uguale al consensus non e' un edge. Blocchi mancanti dichiarati n.d.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_financial_history",
        "description": "STORICO FONDAMENTALE riga per riga dai filing XBRL: SEC (10-K/20-F, 10+ anni, copre anche gli ADR) con fallback AUTOMATICO e dichiarato ai filing ESEF europei via filings.xbrl.org per i nomi EU senza filing SEC (es. i quotati a Milano — copertura ~FY2021+, il payload dichiara fonte e limiti). ~30 voci di IS/BS/CF + derivate (margini, payout totale div+buyback, FCF, capex/ricavi). E' la base per giudicare trend, qualita' e ciclicita' PRIMA di modellare: usalo prima di get_valuation sui single-stock.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "years": {"type": "integer", "description": "Anni di storico (default 10)"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_dat_metrics",
        "description": "Metriche UFFICIALI delle Digital Asset Treasuries (DAT): copre le DAT REGISTRATE nel motore e per le altre risponde con KO dichiarato, mai un ripiego. Per ogni DAT coperta: holdings del cripto-asset di tesoreria, shares outstanding, yield/gain YTD-QTD, eventuali preferred e i derivati per azione (asset-per-share, delta acquisti) dalla fonte ufficiale o dal tracker dichiarato in _source, + derived_mnav CALCOLATO dal tool (mnav_equity_basic E mnav_ev con input dichiarati: per sconto/premio usare mnav_ev, MAI ricalcolare a mano). Il ticker di una DAT puo' coincidere col simbolo di un token su un DEX: qui risponde SOLO l'azione quotata. Per mNAV/holdings USA QUESTO tool, NON tavily_search: e' la fonte esatta e aggiornata.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "Ticker della DAT quotata (obbligatorio: nessun default; non registrata = KO dichiarato)"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_congress_trades",
        "description": "Trade azionari dichiarati dai membri del Congresso USA (Quiver Quantitative, STOCK Act). ticker opzionale per filtrare una società; senza ticker ritorna i più recenti di tutto il Congresso.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "limit": {"type": "integer", "default": 30}
        }},
    },
    {
        "name": "get_lobbying",
        "description": "Spesa di LOBBYING dichiarata da una societa' (Quiver Quantitative): quanto spende e su quali temi. Segnala esposizione regolatoria e posizionamento politico. Usa per tesi su regolamentazione/policy.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "limit": {"type": "integer", "default": 30}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_gov_contracts",
        "description": "CONTRATTI GOVERNATIVI USA assegnati a una societa' (Quiver Quantitative): gli appalti federali sono un segnale duro di ricavi/visibilita' (difesa, sanita', infrastrutture). Usa per tesi fondamentali + policy.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "limit": {"type": "integer", "default": 30}
        }, "required": ["ticker"]},
    },
    {
        "name": "compute_gex",
        "description": "Dealer Gamma Exposure (GEX, metodologia SqueezeMetrics) da chain Polygon: GEX netto in USD per 1% di movimento, profilo per strike, gamma flip point, regime (positivo=mean reversion, negativo=momentum). Usa SPY/QQQ per il mercato o un single name.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "es. SPY, QQQ, AAPL"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_cot_positioning",
        "description": "CFTC COT futures-only settimanale: TFF (Dealer/Asset Manager/Leveraged Funds) per futures finanziari; Disaggregated (Producer/Swap Dealer/Managed Money/Other) per GOLD/GC COMEX e WTI/CL NYMEX. Restituisce variazione settimanale e percentile 1 anno se disponibili. OIL/CRUDE OIL generici sono ambigui: specificare WTI o CL.",
        "input_schema": {"type": "object", "properties": {
            "market": {"type": "string", "description": "es. ES, NQ, EUR, GOLD/GC (COMEX), WTI/CL (NYMEX), 10Y, VIX, BTC. OIL generico ambiguo", "default": "ES"}
        }},
    },
    {
        "name": "get_vix_term_structure",
        "description": "Term structure della volatilità: VIX9D/VIX/VIX3M/VIX6M con ratio VIX/VIX3M — contango = regime calmo, backwardation = stress/risk-off. Termometro di regime immediato.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_valuation",
        "description": "Valutazione settoriale da uno snapshot acquisito: segui valuation_decision, acquisition_tasks e lo schema driver in method_records; non scegliere un motore alternativo. Per i metodi DOCUMENTATI correnti (operating FCFF, banche, managed care, assicurazioni, RAB, NAV e altri adapter a record) invia SOLO ticker, method_records e analysis_context.scenario_rationale. La variant view e le fonti stanno nel rationale dei record, con entita, periodo, unita, base contabile, URL, data e scadenza. I set di record APPROVATI dal PM arrivano da soli dall'archivio, con le motivazioni di scenario del set (method_inputs_origin nel riepilogo RESEARCH e nel blocco VALUTAZIONI): per un titolo con set approvato valido chiama get_valuation col SOLO ticker, senza method_records e senza scenario_rationale. Un set passato dal desk e' una PROPOSTA NON approvata: sostituisce l'archivio senza sommarsi (origine 'esplicito NON approvato' e un task che dichiara lo stato dell'archivio sostituito) e sostituisce anche il set esplicito precedente conservandone la storia; il bundle RESEARCH si revisiona senza riacquisire i provider. I parametri top-level diversi da questi tre sono LEGACY: NON inviarli ai metodi documentati (anche variant_view/growth_path/scenarios/peers/nav_target/rab); producono FV n.d. per input non consumati. Nessun CAGR/default economico colma record assenti. Prima studia filing, guidance, consensus, catalyst e management. ETF, fondi aperti, indici, crypto e panieri restano esposizioni, non DCF aziendali; NAV per veicoli solo nel perimetro registrato e dichiarato dal servizio, fuori copertura = FV n.d. con motivo, mai un veicolo sostitutivo. Cita FV/upside solo con valuation_usability.usable=true, insieme a metodo, snapshot e cutoff; DOCUMENTATA non certifica la correttezza economica o delle fonti e non supera sanity BLOCK.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "method_records": method_records_schema(),
            "analysis_context": {"type": "object", "description": "Contratto documentato: solo scenario_rationale e revisions. Date, periodi, driver e prove sono nei method_records secondo lo schema del metodo, NON in as_of/forecast_years/assumptions di questo oggetto. Riusa get_guidance(include_history=true) e consensus; non inventare fonti, vintage o percorsi. Non convertire EPS adjusted, soglie minime o MCR su premi direttamente in margini su ricavi totali.", "properties": {
                "scenario_rationale": {"type": "object", "properties": {k: {"type": "string", "minLength": 1} for k in ("bear", "base", "bull")}, "required": ["bear", "base", "bull"], "additionalProperties": False},
                "revisions": {"type": "array", "description": "Solo quando l'adapter riconcilia questo ponte (managed care). Per operating e gli altri adapter documentati comuni ometti o passa []; una revisione non riconciliata mantiene FV n.d. Revisiona invece method_records con le nuove fonti e rationale: la storia del set precedente resta nello snapshot.", "items": {"type": "object", "properties": {
                    "metric": {"type": "string"}, "period": {"type": "string"}, "basis": {"type": "string"}, "unit": {"type": "string"},
                    "previous_value": {"type": "number"}, "current_value": {"type": "number"},
                    "value_type": {"type": "string", "enum": ["point", "minimum", "maximum"]},
                    "previous_source": {"type": "string"}, "previous_date": {"type": "string"},
                    "source": {"type": "string"}, "source_date": {"type": "string"},
                    "drivers": {"type": "array", "items": {"type": "string"}, "description": "Driver di scenarios effettivamente modificati; vuoto se ponte prospettico n.d."},
                    "rationale": {"type": "string", "description": "Nuova evidenza -> giudizio -> driver, o buco dichiarato"}
                }, "required": ["metric", "period", "basis", "unit", "previous_value", "current_value", "value_type", "previous_source", "previous_date", "source", "source_date", "drivers", "rationale"]}}
            }, "required": ["scenario_rationale"], "additionalProperties": False},
            "growth_path": {"deprecated": True, "type": "array", "items": {"type": "number"}, "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. La TUA crescita ricavi anno per anno (5 decimali, es. [0.18,0.15,0.12,0.09,0.06]) dopo aver analizzato guidance/consensus/catalyst/management. Sovrascrive la stima meccanica: e' la tua variant view."},
            "variant_view": {"deprecated": True, "type": "string", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. Razionale della tua crescita vs la guidance (es. 'management guida 25%, modello 18% per execution risk; upside se contratto X si chiude')."},
            "ebitda_margin_target": {"deprecated": True, "type": "number", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPZIONALE: il margine EBITDA target a regime che TU prevedi (decimale, es. 0.35 per espansione da leva operativa). Il modello deriva l'opex per centrarlo."},
            "terminal_growth": {"deprecated": True, "type": "number", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPZIONALE: crescita perpetua terminale (decimale, es. 0.025). Cappata automaticamente al risk-free (regola Damodaran g<=rf)."},
            "roe_path": {"deprecated": True, "type": "array", "items": {"type": "number"}, "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. SOLO BANCHE/ASSICURAZIONI (#203): il TUO ROE anno per anno (decimali, es. [0.12,0.12,0.115]) - la variant view bancaria. Gli anni non forniti fanno fade verso il ROE terminale sostenibile."},
            "target_payout": {"deprecated": True, "type": "number", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. SOLO BANCHE (#203): payout totale (dividendi+buyback) che TU prevedi a regime (decimale, es. 0.55). Senza, il modello lo calcola dal cashflow REALE."},
            "fade_years": {"deprecated": True, "type": "integer", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. SOLO BANCHE (#203): in quanti anni l'excess return decade (V4: default dal PROFILO - 5 banche/assicurazioni, 8 credit services; 10 se il profilo non lo definisce). Meno anni = piu' conservativo."},
            "cost_of_equity": {"deprecated": True, "type": "number", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. SOLO BANCHE (#203): override del Ke se hai una view motivata (decimale, es. 0.085). Default: CAPM con beta disciplinato [0.9-1.6] e floor rf+3.5%."},
            "scenarios": {"deprecated": True, "type": "object", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPERATIVE (#204b): i TUOI 3 scenari completi. Formato: {bear:{revenue_growth:[5 decimali], gross_margin:[...], rnd_pct:[...], sga_pct:[...], capex_pct:[...], nwc_pct:[...], tax_rate:[...], commentary:{driver:'perche'' in 1 frase'}}, base:{...}, bull:{...}}. Driver opzionali extra (B11): da_tan_pct (D&A tangibile % rev, default 0.02), personnel_pct (quota COGS, default 0.60), services_pct (quota SG&A, default 0.30). Ogni driver mancante usa il default dai dati. La commentary finisce NEL foglio Excel accanto all'assumption: scrivila da analista."},
            "equity_adjustments": {"deprecated": True, "type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "value_m": {"type": "number"}, "commentary": {"type": "string"}}, "required": ["label", "value_m"]}, "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPERATIVE (parita' A7): bridge to equity OLTRE il net debt. value_m FIRMATO in MILIONI (valuta del bilancio): NEGATIVO se riduce l'equity (minorities, pension/TFR non fondato, earn-out, deferred consideration, preferred), POSITIVO se aggiunge (associates/partecipazioni non consolidate). Ogni voce con commentary e fonte [src: tool] — i numeri vengono dai filing/tool, mai inventati. Le voci finiscono come input blu nel blocco BRIDGE del foglio DCF e spostano il fair value: usale solo se motivate."},
            "stance": {"deprecated": True, "type": "string", "enum": ["buy", "sell", "neutral"], "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPZIONALE con equity_adjustments: dichiara il criterio di inclusione delle voci discrezionali del bridge (buy = includi i debt-like dubbi, prudente sul prezzo; sell = solo i certi; neutral = mid). Viene scritto nel foglio: la discrezionalita' si ordina, non si nasconde."},
            "diluted_shares_m": {"deprecated": True, "type": "number", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPZIONALE: azioni FULLY DILUTED in milioni (opzioni ITM + RSU/SBC) se materialmente diverse dalle basic. Sostituiscono le shares nel per-share di tutto il modello."},
            "precedents": {"deprecated": True, "type": "array", "items": {"type": "object", "properties": {"target": {"type": "string"}, "acquirer": {"type": "string"}, "year": {"type": "integer"}, "ev_m": {"type": "number"}, "ebitda_m": {"type": "number"}}, "required": ["target", "ev_m", "ebitda_m"]}, "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPERATIVE (parita' B9): transazioni M&A precedenti del settore, EV e EBITDA in MILIONI. Cifre SOLO da fonti verificate ([src: tool] nel tuo report): finiscono nel foglio Precedents con mediana viva. Deal con EV/EBITDA mancanti o <=0 vengono scartati e conteggiati."},
            "method_weights": {"deprecated": True, "type": "object", "properties": {"dcf": {"type": "number"}, "comps": {"type": "number"}}, "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPZIONALE (parita' B8): ponderazione dei metodi nel fair value finale, es. {dcf:0.7, comps:0.3} (devono sommare a 1). SENZA questo parametro il fair value resta 100% DCF (default storico invariato); il valore implicito dai comps e' comunque calcolato e mostrato come barra informativa nel football field."},
            "wacc_delta_bp": {"deprecated": True, "type": "object", "properties": {"bear": {"type": "number"}, "bull": {"type": "number"}}, "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. OPZIONALE (parita' B14): delta WACC in BASIS POINT per gli scenari bear/bull (es. {bear:+100, bull:-50} = bear scontato 1% sopra, bull 0.5% sotto). Cap +/-500bp; il BASE resta sempre sul WACC headline. Usalo quando il rischio dello scenario giustifica un tasso diverso, e motivalo."},
            "peers": {"deprecated": True, "type": "array", "items": {"type": "string"}, "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. I PEER CHE FITTANO scelti da TE (regola PM 16/07): ticker di societa' DAVVERO comparabili per business model, margini e size — non il settore anagrafico (una biotech non si confronta con un'assicurazione sanitaria, ne' due nomi della difesa con mix di prodotto opposti). Senza questo parametro il modello usa la lista auto del sub-settore (fallback dichiarato nel foglio): passala quando la lista auto non fitta."},
            "nav_target": {"deprecated": True, "type": "number", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. SOLO VEICOLI mNAV (V5, quelli registrati nel motore): il TUO target di premio/sconto sul NAV, motivato in variant_view. Semantica per TIPO di veicolo, che il motore dichiara nel campo `kind` del canonico: fondo chiuso = rapporto prezzo/NAV (es. 0.80 = sconto strutturale 20%); DAT senza debito ne' preferred davanti alle ordinarie = mNAV target su Adjusted NAV per azione FD; DAT con debt+preferred davanti alle ordinarie = target su mNAV EV. SENZA questo parametro il canonico esce come scheda informativa con FV n.d. DICHIARATO (D2: dire FV=NAV implicherebbe 'il premio chiude a 1,0'). Banda di buon senso 0.5-1.5 (fuori = WARN dichiarato, mai BLOCK su una view; oltre 0.1-3.0 = input rotto, FV n.d.). NB: il FV che ne esce e' l'upside alla CONVERGENZA del premio/sconto dichiarato, NON un target price."},
            "rab": {"deprecated": True, "type": "object", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. SOLO RETI REGOLATE (V7 - Terna/Snam/Italgas/National Grid...): il motore RAB. La revenue di una rete e' la FORMULA del regolatore, quindi SENZA rab_base il modello si RIFIUTA (la RAB non e' su Yahoo: prendila dalla Relazione finanziaria o dal piano industriale e cita la fonte in variant_view). Campi: rab_base (mln, OBBLIGATORIO), rab_base_year, service ('trasmissione elettrica'|'distribuzione elettrica'|'trasporto gas'|'distribuzione gas'|'stoccaggio'|'rigassificazione' - aggancia il WACC ammesso della delibera ARERA/Ofgem in codice), net_debt (mln), capex_plan (lista mln/anno) o capex_pct_rab, allowed_return (tasso REALE come in delibera — l'inflazione entra gia' via indicizzazione della RAB, NON passare il nominale; SOLO per sovrascrivere l'ancora regolatoria), rab_premium (EV/RAB, default profilo 1.15), payout, da_pct_rab, rab_growth_real_lt, cost_of_debt. Per i .L la RAB e il net debt vanno in GBP mln (la quotazione in pence la gestisce il motore, dichiarato).", "properties": {"rab_base": {"type": "number"}, "rab_base_year": {"type": "integer"}, "service": {"type": "string"}, "net_debt": {"type": "number"}, "capex_plan": {"type": "array", "items": {"type": "number"}}, "capex_pct_rab": {"type": "number"}, "allowed_return": {"type": "number"}, "rab_premium": {"type": "number"}, "payout": {"type": "number"}, "da_pct_rab": {"type": "number"}, "rab_growth_real_lt": {"type": "number"}, "cost_of_debt": {"type": "number"}}, "required": ["rab_base"]},
            "segments": {"deprecated": True, "type": "array", "description": "LEGACY: non inviare ai metodi documentati a record; usare il driver specifico nei method_records. SOTP OPZIONALE (V7 Lotto 3 — utilities INTEGRATE tipo Enel/SSE/Iberdrola, o conglomerate): lista segmenti DICHIARATA DA TE dalle segment notes di bilancio/piano (fonte in variant_view). Ogni segmento: name + engine 'rab' (rete regolata: serve rab_base in mln, opz. rab_premium EV/RAB — default profilo 1.15x, banda 0.7-1.6 dichiarata) oppure 'multiple'/'operating' (serve value mln + multiple, metric = etichetta es. 'EBITDA 2026E'; in V7 anche 'operating' e' valutato a multiplo, dichiarato). Opzionali: basis 'ev' (default) | 'equity' — USA 'equity' quando il multiplo produce un EQUITY value (P/E, book, valore della TUA quota di un'associata a equity method): viene sommato DOPO il net debt, mai dentro la somma EV; stake (quota economica 0-1) SOLO per quote NON consolidate il cui debito non e' nel net debt di gruppo — per una controllata consolidata usa stake=1 e lascia il NCI nel bridge (equity_adjustments); ebitda/revenue in mln per la guardia di copertura vs consolidato Yahoo (scarto >10% = warning dichiarato); src (fonte della riga). Valori in MILIONI della valuta di bilancio. Il CONSOLIDATO resta l'headline: il SOTP esce nel foglio 'SOTP (segmenti)' con la riga 'Delta vs consolidato' — RIPORTA delta e warnings nel report. Un segmento senza input validi = riga n.d. e SOTP INCOMPLETO dichiarato (mai somme parziali).", "items": {"type": "object", "properties": {"name": {"type": "string"}, "engine": {"type": "string", "enum": ["rab", "multiple", "operating"]}, "basis": {"type": "string", "enum": ["ev", "equity"]}, "rab_base": {"type": "number"}, "rab_premium": {"type": "number"}, "value": {"type": "number"}, "multiple": {"type": "number"}, "metric": {"type": "string"}, "stake": {"type": "number"}, "ebitda": {"type": "number"}, "revenue": {"type": "number"}, "src": {"type": "string"}}, "required": ["name", "engine"]}}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_advanced_metrics",
        "description": "Metriche di performance ISTITUZIONALI del portafoglio: Sharpe, Sortino, Calmar, Omega, Sterling, Ulcer Index, recovery factor, CVaR/Expected Shortfall, tail ratio, skew/kurtosi, win rate, payoff ratio, profit factor, Kelly, e metriche relative al benchmark (alpha, beta, information ratio, Treynor). Usalo per giudicare la qualita' risk-adjusted del portafoglio con numeri precisi.",
        "input_schema": {"type": "object", "properties": {
            "benchmark": {"type": "string", "default": "SPY"}
        }},
    },
    {
        "name": "get_edge_scan",
        "description": "EDGE SCANNER: scansiona TUTTE le posizioni del portafoglio e ritorna i SEGNALI QUANTITATIVI OGGETTIVI piu' forti (dislocazioni reali), gia' ranked per forza 0-100 e con lettura in italiano. Copre: vol risk premium (IV vs realized), dealer gamma, z-score di prezzo (mean-reversion), regime di volatilita', cluster del Congresso, esposizioni fattoriali estreme. ATTENZIONE, la copertura NON e' uniforme e il payload la dichiara nel campo `copertura`: sui ticker che contengono un PUNTO nel simbolo (il suffisso di borsa: .MI, .DE, .L, .FRA, .VI), salvo quelli elencati in signal_engine.US_OPTIONS, l'unico rilevatore per-ticker applicabile e' lo z-score di prezzo, perche' fuori dagli USA le catene OPRA e i trade del Congresso non esistono — quindi se uno di quei nomi non ha segnali di volatilita' NON e' una misura, e' una copertura che manca. Le esposizioni fattoriali invece li coprono, da una strada separata. Il campo `copertura` dice anche quali nomi hanno avuto un rilevatore MUTO per guasto (`scansione_degradata`) e quali nessuna misura: uno zero su quei nomi non va letto come assenza di dislocazione. La risposta puo' essere servita da una CACHE in-process (TTL 1 ora; 5 minuti se la scansione era degradata, e `cache.ttl_motivo` lo dice): in quel caso il campo `cache` e la nota di copertura dichiarano quanti secondi fa la scansione si e' conclusa — i rilevatori NON vengono re-interrogati a ogni chiamata, e non hai una leva per forzarli. USALO come PUNTO DI PARTENZA invece di inventare tesi: parti dai segnali oggettivi e interpretali.",
        "input_schema": {"type": "object", "properties": {
            "min_strength": {"type": "integer", "description": "soglia minima forza segnale (default 45)", "default": 45}
        }},
    },
    {
        "name": "get_position_doctor",
        "description": "POSITION DOCTOR: diagnosi quantitativa completa di UNA posizione con verdetto numerico (ADD/HOLD/TRIM) derivato dai segnali oggettivi: vol risk premium, gamma, z-score, congress. Usalo per decidere se una posizione esistente va tenuta, aumentata o alleggerita, con motivazione 100% numerica.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_vol_surface_summary",
        "description": "Volatility surface di QUALSIASI ticker US (Polygon multi-expiry): term structure ATM per scadenza, 25Δ risk reversal e butterfly, IV vs realized vol 30g (vol risk premium), P/C open interest, più un'interpretazione professionale già scritta. Usalo per giudicare se le opzioni sono care/a sconto e leggere skew ed eventi prezzati.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "es. SPY, QQQ, AAPL, TSLA"}
        }, "required": ["ticker"]},
    },
    {
        "name": "compare_assets",
        "description": "Performance relativa tra 2+ asset (return %, vol, Sharpe) su periodo specificato.",
        "input_schema": {"type": "object", "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}, "minItems": 2},
            "period": {"type": "string", "description": "'1mo','3mo','6mo','1y','2y'", "default": "1y"}
        }, "required": ["tickers"]},
    },

    # ---- SEARCH (web/news/predictions - interattivi) ----
    {
        "name": "search_news",
        "description": "Ricerca news multi-fonte (NewsAPI + Marketaux + TheNewsAPI + GNews + yfinance + RSS) per ticker o query libera. Copre il feed recente (~2-3 giorni), NON archivi storici. Ritorna lista articoli con title, source, url, published_at, snippet.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Ticker (es. 'AAPL') o query libera (es. 'BTC ETF outflows')"},
            "max_results": {"type": "integer", "default": 10}
        }, "required": ["query"]},
    },
    {
        "name": "tavily_search",
        "description": "Web search real-time tramite Tavily API. Per query general-purpose (eventi macro, ricerca tematica). Ritorna snippets curati.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5}
        }, "required": ["query"]},
    },
    {
        "name": "get_polymarket_events",
        "description": "Prediction markets Polymarket: implied probability su eventi (elezioni, Fed cuts, geopolitica). Ritorna mercati attivi con odds.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Es. 'Fed cut March 2026', 'US recession 2026'"},
            "max_results": {"type": "integer", "default": 10}
        }, "required": ["query"]},
    },
    {
        "name": "get_hyperliquid_intel",
        "description": "Hyperliquid DEX perp data: top perps, funding rates (annualizzati), OI, premium bps + BUILDER DEX HIP-3: tutta la TradFi 24/7 sul dex 'xyz' (indici, big tech, commodities) + pre-IPO in mix su 'xyz'/'vntl' (ANTHROPIC, SPACEX, OPENAI). E' il tape 24/7: usalo per il segnale weekend/overnight su asset TradFi. NB: un simbolo quotato sul DEX puo' coincidere col ticker di una DAT in borsa: qui rispondono i dati del TOKEN, per l'azione usa get_dat_metrics.",
        "input_schema": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "Optional symbol (es. 'BTC','SOL'), altrimenti top perps"},
            "builder_dexs": {"type": "boolean", "description": "Default true: include mercati HIP-3 (equities 24/7 + pre-IPO). False = solo dex principale."}
        }, "required": []},
    },

    # ---- MACRO ----
    {
        "name": "get_macro_dashboard",
        "description": "Snapshot macro indicators da FRED + fonti native: Fed Funds Rate, CPI, unemployment, 10y yield, yield curve 2-10, Broad Dollar Index (FRED ~120, NON il DXY ICE ~100: mai chiamarlo DXY), oil, real rates, ecc.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_macro_indicator",
        "description": "Singolo indicatore FRED per serie code esplicito.",
        "input_schema": {"type": "object", "properties": {
            "series_id": {"type": "string", "description": "FRED series code (es. 'DGS10', 'DFF', 'CPIAUCSL')"}
        }, "required": ["series_id"]},
    },
    {
        # I-1 (26/07): lo schema viveva SOLO in agent_tools (registro legacy) e non
        # arrivava mai agli specialisti, mentre specialists/macro.py:29 ne ORDINA l'uso.
        # Descrizione ripresa verbatim da agent_tools per non divergere.
        "name": "get_yield_curves",
        # ⚠️ Copia IDENTICA di agent_tools.YIELD_CURVES_DESCRIPTION (non si importa
        # agent_tools a livello modulo: qui l'import e' volutamente lazy dentro dispatch).
        # Le due copie sono inchiodate da tests/test_i1_fili_rotti.py.
        "description": (
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
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },

    # ---- PORTFOLIO + MEMORY ----
    {
        "name": "get_portfolio_live",
        "description": ("Snapshot LIVE del portfolio (SQLite + prezzi correnti): holdings, NAV EUR, "
                        "P/L unrealizzato totale ed individuale, pesi, valuta nativa. Ogni riga porta "
                        "anche `tipo`: la natura DICHIARATA nel negozio dei veicoli (fondo chiuso, ETF, "
                        "crypto treasury, societa' operativa, banca...), oppure «non dichiarato» se quel "
                        "simbolo non ha una voce — un buco dichiarato, mai una deduzione dal ticker."),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_pending_decisions",
        "description": "Lista decisioni pending del Capo (ACTION/TICKER/EUR_AMOUNT/TIMING/CONFIDENCE).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_research_note",
        "description": ("F10 v3: scrivi una NOTA (autore AI) sul filo di una decisione RESEARCH del "
                        "Decisions tracker — e' il canale di risposta alle note del PM (le ricevi nel "
                        "blocco TITOLI IN RICERCA con il loro decision_id). Solo decisioni RESEARCH. "
                        "Nota breve, coi numeri [src:], che risponda alla domanda del PM o aggiorni la tesi."),
        "input_schema": {"type": "object", "properties": {
            "decision_id": {"type": "integer", "description": "id della decisione RESEARCH (dal blocco TITOLI IN RICERCA)"},
            "note": {"type": "string", "description": "testo della nota (max ~2000 char)"}
        }, "required": ["decision_id", "note"]},
    },
    {
        "name": "search_past_memos",
        "description": "Semantic search ChromaDB sui memo settimanali storici del Capo.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "n_results": {"type": "integer", "default": 5}
        }, "required": ["query"]},
    },
    {
        "name": "check_decision_outcomes",
        "description": "Verifica outcome delle decisioni passate del Capo: prezzo decisione vs corrente, % return, status (executed/skipped/expired).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    # === NEWS TERMINAL TOOLS (Bloomberg-style) ===
    {
        "name": "get_news_briefing",
        "description": "Daily Market Briefing corrente (stile FT Briefing/Bloomberg Daybook): tone di mercato, macro highlights, portfolio impact, watch today. Generato da Haiku ogni 4h.",
        "input_schema": {"type": "object", "properties": {
            "force_refresh": {"type": "boolean", "default": False,
                              "description": "Se true, rigenera prima di restituire"}
        }, "required": []},
    },
    {
        "name": "get_macro_news_by_topic",
        "description": "News macro/geo/politica/EM/crypto/commodity aggregati per topic. Categorie disponibili: rates, inflation, geopolitics, politics, em, commodities, crypto, corporate.",
        "input_schema": {"type": "object", "properties": {
            "categories": {"type": "string",
                            "description": "CSV di categorie (es. 'rates,geopolitics'). Vuoto = tutte."},
            "min_importance": {"type": "integer", "default": 4,
                                "description": "Filtra topic con importance >= N (1-5, default 4)"},
            "days": {"type": "integer", "default": 2},
            "max_per_topic": {"type": "integer", "default": 3}
        }, "required": []},
    },
    {
        "name": "get_corporate_events_for_ticker",
        "description": "Eventi societari per un ticker: 8-K + filing strutturali (S-1, 424B, 10-Q/K, 13D/G) + insider Form 4 da SEC EDGAR, piu' company news da Finnhub (le press release Finnhub NON sono nel piano dati: non aspettartele da questo tool). Interroga ENTRAMBE le fonti sul ticker che chiedi. Leggi `fonti` e `avviso` PRIMA di `count`: ogni fonte dichiara se ha risposto e con quanti eventi, perche' uno zero da una fonte che risponde e' una MISURA mentre uno zero da una fonte muta non lo e'. I nomi non-US non depositano presso la SEC: per loro l'unica fonte e' Finnhub, e il payload lo dice.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "days": {"type": "integer", "default": 30}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_insider_trades",
        "description": "Form 4 SEC insider trades per un ticker: chi (CEO/CFO/director), cosa (BUY/SELL), quante azioni, prezzo, valore totale. Real-time via Finnhub.",
        "input_schema": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "days": {"type": "integer", "default": 30}
        }, "required": ["ticker"]},
    },
    {
        "name": "get_earnings_calendar",
        "description": "Earnings calendar prossimi N giorni. Se ticker fornito, filtra per quello. Altrimenti ritorna tutti gli earnings del periodo (max 100).",
        "input_schema": {"type": "object", "properties": {
            "days_ahead": {"type": "integer", "default": 14},
            "ticker": {"type": "string",
                        "description": "Opzionale: filtra per un ticker specifico"}
        }, "required": []},
    },
    {
        "name": "get_13f_holdings",
        "description": "13F filings di institutional investor noti, per slug (gestori USA value, attivisti, macro e quant): uno slug sconosciuto riceve un KO dichiarato che elenca quelli disponibili. Ritorna ultime holdings + valore USD.",
        "input_schema": {"type": "object", "properties": {
            "investor": {"type": "string",
                          "description": "Slug dell'investitore, minuscolo (sconosciuto = KO dichiarato che elenca i disponibili)"},
            "max_items": {"type": "integer", "default": 25}
        }, "required": ["investor"]},
    },
]


# ============================================================
# DISPATCHER - mappa tool_name -> python function reale
# ============================================================

def _stamp(payload: Any, source: str) -> Dict[str, Any]:
    """Decora il risultato con metadata anti-hallucination."""
    result = {
        "_source": source,
        "_timestamp": datetime.now().isoformat(timespec="seconds"),
        "data": payload,
    }
    if isinstance(payload, dict) and "error" in payload:
        result["error"] = payload["error"]
    return result


def _error(msg: str, source: str) -> Dict[str, Any]:
    return {
        "_source": source,
        "_timestamp": datetime.now().isoformat(timespec="seconds"),
        "error": msg,
    }


# =============================================================================
# VISTE COMPATTE PER GLI AGENTI (26/07 pre-V6, Opus 5)
# =============================================================================
# I tool_result dei desk sono TAGLIATI a TETTO_TOOL_RESULT char (6000 fino al
# 20/08, oggi 12000 — v. la costante sotto) e il
# taglio cade in CODA — cioe' esattamente dove questi payload scrivono le
# DICHIARAZIONI. Misurato sul payload vero del 26/07:
#   get_portfolio_live  17.252 char -> arrivavano 10 posizioni su 27 (ordine DB, non
#                       per valore) e cadevano nav_total_eur, cash_disponibile_eur,
#                       totale_valore_mercato_eur, totale_pl_eur. Quattro desk su sei
#                       (macro, fundamentals, crypto, eventdesk) non hanno
#                       get_portfolio_risk e non avevano NESSUN altro canale per il
#                       NAV: verificato che current_facts/favorites/pm_theses non li
#                       nominano. Condizione nota dal 16/07 (base.py:785, "~15/28").
#   get_tearsheet        7.993 char -> perdeva metrics (Sharpe/Sortino/Calmar/VaR),
#                       regime_summary, risk_free_used, notes, basis; e teneva 4.040
#                       char di serie rolling giorno-per-giorno.
#   get_attribution      6.616 char -> perdeva notes/basis e tagliava A META' la frase
#                       della riconciliazione ("Un delta ampio va capito, non nascosto").
#
# Queste funzioni sono PURE (dict -> dict) e vivono SOLO sul percorso LLM: gli
# endpoint HTTP /portfolio, /portfolio/tearsheet e /portfolio/attribution importano
# i moduli direttamente (bellomberg_api.py:668/2383/2394) e restano BYTE-IDENTICI
# per il frontend, che li consuma gia' in F2 Performance.
#
# Regola 14/07 applicata: la compattazione si DICHIARA sempre nel payload (chiave
# `_vista`), i campi tolti sono nominati, e se il risultato sfora comunque il tetto
# la degradazione e' dichiarata invece di essere subita zitta.
# =============================================================================

TETTO_TOOL_RESULT = 12000    # 6000 -> 12000 il 20/08 (ok PM: "aumentiamo i tetti").
# UNICA fonte di verita': `specialists/base.py` e `red_team.py` IMPORTANO questa
# costante invece di ri-cablare il numero (prima erano tre posti, e i due letterali
# erano quelli che tagliavano davvero — stessa classe delle fonti prezzi del 19/08).
# Il vincolo e' meccanico in tests/test_tetto_tool_result.py.
# Costo: il testo in piu' viaggia nei tool_result, che NON sono un prefisso stabile
# e quindi si cachano male — a differenza del blocco tesi. Misurato sulla prima run
# vera, non promesso qui.
# Busta `_stamp` (_source/_timestamp): ~103 char misurati + un filo di margine.
# Review Fable 5 del 01/08 (ALTA voce 59): il vecchio _MARGINE_SICUREZZA=200,
# confrontato col peso SENZA busta, degradava il book VERO a torto — via
# `prezzo_medio` da 27 righe quando vista+busta (5.957) stava sotto il tetto
# di allora (6000).
# Il criterio onesto e' UNO solo: peso serializzato + busta > tetto.
_BUSTA_STAMP = 110


def _arr(v: Any, n: int = 2) -> Any:
    """Arrotonda i float, lascia intatto tutto il resto (None compresi: un None
    e' un dato mancante DICHIARATO, non uno zero).

    REGOLA 14/07 applicata all'arrotondamento: un valore NON nullo non deve mai
    diventare 0.0. `cross_pct: 0.004` e' il residuo cross dichiarato apposta
    dall'attribution ("mai spalmato su altri"): arrotondarlo a 0.0 lo farebbe
    sparire in silenzio. Se il round azzera un non-zero si tengono cifre finche'
    il numero resta visibile."""
    if not isinstance(v, float):
        return v
    r = round(v, n)
    if r == 0.0 and v != 0.0:
        for extra in (4, 6, 8):
            r = round(v, extra)
            if r != 0.0:
                return r
        return v          # numero denormale: meglio intero che finto zero
    return r


def _peso_json(payload: Any) -> int:
    """Lunghezza del payload una volta serializzato come lo serializza base.py.

    NIENTE `except: return 0` qui: la prima stesura ce l'aveva e ha reso la
    guardia di degradazione MUTA — `json` non era importato in questo modulo, il
    NameError finiva nell'except e la funzione rispondeva "0 char" su un payload
    da 34.295. Uno zero finto che spegne un controllo e' esattamente il fallback
    silenzioso vietato il 14/07. Se la serializzazione fallisse davvero,
    l'eccezione sale al try/except del dispatcher, che risponde con un _error
    DICHIARATO."""
    return len(json.dumps(payload, default=str, ensure_ascii=False))


def _e_errore(payload: Any) -> bool:
    """Un payload d'errore o non-dict passa INTATTO: non si compatta un guasto."""
    return not isinstance(payload, dict) or "error" in payload


def _dichiara_se_sfora(out: Dict[str, Any], extra: int = 0) -> Dict[str, Any]:
    """Se la vista compatta sfora COMUNQUE il tetto, il payload lo DICE.

    Senza questa riga la compattazione avrebbe una scadenza silenziosa: il book
    cresce, i payload tornano sopra i 6000, il taglio riprende a mangiare la coda
    (`notes`, `basis`, `reconciliation`) e nessuno se ne accorge — la stessa cosa
    che questa modifica esiste per riparare. `extra` copre la busta di `_stamp`
    (chiavi `_source`/`_timestamp`, ~103 char misurati)."""
    if _peso_json(out) + extra > TETTO_TOOL_RESULT:
        out["_vista"] = (str(out.get("_vista", "")) + " ATTENZIONE: questa vista "
                         f"sfora COMUNQUE il tetto di {TETTO_TOOL_RESULT} char: la "
                         "coda del payload sara' TAGLIATA a valle. Quello che leggi "
                         "puo' essere incompleto.").strip()
    return out


def _compatta_edge_scan(r: Dict[str, Any]) -> Dict[str, Any]:
    """Vista dell'edge scan per gli agenti: degrada DICHIARANDO, mai zitta.

    22/08 (voce F). Togliendo il taglio a 20 posizioni il payload vero e' passato
    da 6.368 a 10.508 char su un tetto di 12.000 (dal 53% all'88%): il margine e'
    ~4 segnali, e il taglio a valle e' CIECO e cade in coda, cioe' sui segnali.
    Il vincolo del PM era esplicito — "non voglio che ci siano tagli o si rompa
    qualcosa di nuovo" — quindi la cura non puo' lasciare in eredita' un taglio
    muto che prima non poteva scattare.

    Si dimagrisce nell'ordine che perde meno: prima la LETTURA in prosa dei
    segnali piu' deboli (~260 char l'una, il pezzo grasso), che e' interpretazione
    ricostruibile dai campi numerici rimasti; solo se non basta si tolgono i
    segnali piu' deboli, e allora si dice quanti e come riaverli. Se il payload
    ci sta, torna INTATTO: una `_vista` inutile e' rumore che svaluta i caveat
    veri (lezione del 22/08 sul 403 di /press-releases).
    """
    if _e_errore(r) or _peso_json(r) + _BUSTA_STAMP <= TETTO_TOOL_RESULT:
        return r

    segnali = [dict(s) for s in (r.get("signals") or [])]
    # `_vista` IN TESTA: la dichiarazione deve sopravvivere a un taglio in coda.
    out: Dict[str, Any] = {"_vista": ""}
    for k, v in r.items():
        if k != "signals":
            out[k] = v
    out["signals"] = segnali

    letture_tolte = 0
    segnali_tolti = 0
    forza_minima = None
    tolti_elenco = []

    def _aggiorna_vista():
        """La dichiarazione PESA (~300 char) e cresce coi numeri: va scritta a
        ogni giro, altrimenti si misura un payload che non e' quello finale e la
        vista esce sopra il tetto che doveva rispettare (misurato: 12.044 su
        12.000 alla prima stesura). Una guardia deve pesare la cosa vera."""
        pezzi = ["COMPATTA per gli agenti (il payload pieno sfora il tetto di %d char)."
                 % TETTO_TOOL_RESULT]
        if letture_tolte:
            pezzi.append("Tolta la LETTURA in prosa di %d segnali fra i piu' deboli "
                         "(`reading: null`): i campi numerici restano tutti."
                         % letture_tolte)
        if segnali_tolti:
            # ⚠️ La prima stesura diceva «richiamali con get_edge_scan alzando
            # min_strength». E' FALSO per costruzione: i tolti sono i PIU'
            # DEBOLI, quindi alzare la soglia li esclude di piu' e abbassarla li
            # fa ritagliare — non esiste nessun valore che li faccia riapparire.
            # E senza i NOMI nemmeno position_doctor era azionabile.
            pezzi.append("Tolti anche %d segnali, i piu' deboli (forza <= %s): NON "
                         "sono assenti, sono fuori da QUESTA vista. Da get_edge_scan "
                         "non sono recuperabili in nessun modo (alzare min_strength "
                         "li esclude, abbassarlo li fa ritagliare); sono %s, e su un "
                         "singolo nome puoi chiedere get_position_doctor."
                         % (segnali_tolti, forza_minima,
                            ", ".join("%s %s (%s)" % (x.get("ticker"), x.get("name"),
                                                      x.get("strength"))
                                      for x in tolti_elenco[:12])
                            + ("" if len(tolti_elenco) <= 12
                               else " (+%d oltre questi)" % (len(tolti_elenco) - 12))))
        out["_vista"] = " ".join(pezzi)

    def _sta():
        return _peso_json(out) + _BUSTA_STAMP <= TETTO_TOOL_RESULT

    _aggiorna_vista()
    for s in reversed(segnali):          # dal piu' debole in su
        if _sta():
            break
        if s.get("reading"):
            s["reading"] = None
            letture_tolte += 1
            _aggiorna_vista()

    while not _sta() and len(out["signals"]) > 1:
        forza_minima = out["signals"][-1].get("strength")
        tolti_elenco.insert(0, out["signals"][-1])
        out["signals"] = out["signals"][:-1]
        segnali_tolti += 1
        _aggiorna_vista()

    return _dichiara_se_sfora(out, extra=_BUSTA_STAMP)


def _compatta_portfolio_live(p: Dict[str, Any], negozio=None) -> Dict[str, Any]:
    """Vista compatta del book per gli agenti: TOTALI IN TESTA (sopravvivono a
    qualunque taglio) + tutte le posizioni con i campi che un desk usa davvero.

    Tolti e perche':
      - `tesi`: duplicato PURO. Le tesi del PM viaggiano dal 16/07 su canale
        dedicato (current_facts.pm_theses_block) e la copertura e' stata
        verificata: 18 posizioni con tesi nel payload, 18 su 18 presenti nel
        blocco. Zero informazione persa, 3.909 char liberati.
      - `price_source`: aggregato in testa (`prezzi_fonte`) invece di 27 volte.
        Se le fonti sono miste il conteggio per fonte lo dice.
      - `price_stale`: tenuto SOLO dove e' vero (una posizione stantia si vede
        subito) + `stale_positions` resta in testa come lo produce il modulo.
      - `nome`, `prev_close`, `prev_close_ts`, `data_apertura`, `fx_to_eur`.

    ⚠️ IL CAMPO DEL CONTROVALORE E' `valore_mercato`, NON `valore_mercato_eur`.
    La prima stesura teneva il secondo "perche' e' quello in euro": FALSO, e la
    review pre-commit l'ha misurato. `memory_db.get_portfolio_summary` fa
    `continue` sulle posizioni in EUR (memory_db.py:947-949) e per loro
    `valore_mercato_eur` NON viene mai scritto; sulle altre lo scrive e poi
    SOVRASCRIVE `valore_mercato` con lo stesso numero (memory_db.py:964-965).
    Misurato sul book vero prima di scegliere — i conteggi stanno nel registro
    privato, non in questa docstring: un numero come «N posizioni su M» e' una
    misura DEL PORTAFOGLIO, e questo file esce nel perimetro pubblico (06/09).
    Le posizioni gia' in EUR restano senza `valore_mercato_eur`, tutte hanno
    `valore_mercato`, e dove ci sono entrambi coincidono — la stessa somma che il
    modulo usa per i pesi (memory_db.py:969). Tenere quello sbagliato lasciava
    senza controvalore ogni riga gia' in EUR: una vista nata per ridare i totali
    agli agenti che si portava via il valore per riga.
    Unico caso in cui `valore_mercato` resta in valuta NATIVA: FX non disponibile
    per quella valuta — e quei ticker sono elencati in `fx_incomplete`, che sta in
    testa alla vista.
    """
    if _e_errore(p):
        return p
    pos = p.get("positions")
    if not isinstance(pos, list):
        return p

    # pl_eur_fx/pl_pct_fx/fx_pl_eur per riga: decisione PM 31/08 («gli agenti
    # allocano soldi veri»), costo misurato sul book vivo alla voce (104)
    campi = ["ticker", "quantita", "prezzo_medio", "prezzo_live", "valuta",
             "valore_mercato", "pl_eur", "pl_pct", "pl_eur_fx", "pl_pct_fx",
             "fx_pl_eur", "peso_pct"]
    tolti = ["tesi (canale dedicato pm_theses_block)", "nome", "prev_close",
             "prev_close_ts", "prev_close_source", "price_source (aggregato in testa)", "data_apertura",
             "costo_eur_storico (derivabile: valore_mercato - pl_eur_fx)",
             "fx_to_eur", "valore_mercato_eur (duplicato di valore_mercato dove esiste)"]

    righe_dict = [r for r in pos if isinstance(r, dict)]
    fonti: Dict[str, int] = {}
    for r in righe_dict:
        k = str(r.get("price_source") or "n.d. (campo assente)")
        fonti[k] = fonti.get(k, 0) + 1
    fonti_txt = ", ".join(f"{k} ({v}/{len(righe_dict)})" for k, v in sorted(fonti.items()))
    # righe non-dict: non spariscono zitte (oggi memory_db non ne produce)
    scartate = len(pos) - len(righe_dict)

    # CAMPO TIPO (05/09, lotto 3): la natura del veicolo, dal negozio dei veicoli.
    # Senza, le righe dei desk che dicono «per le DAT del book…» non sono eseguibili:
    # il modello riceve dei ticker e nessuna classe, e l'unico modo di applicare la
    # regola e' che i simboli stiano scritti nel prompt. Il negozio si legge UNA volta
    # per chiamata (era una lettura di file per riga) e non a import-time.
    import bellomberg.storage.classificazione as _cl
    _neg = negozio if negozio is not None else _cl.carica_veicoli()
    _neg_rotto = _neg["origine"] in ("assente", "illeggibile")
    _senza_tipo = []

    def _tipo(t: str) -> str:
        """Tre esiti DISTINTI, mai confusi: la natura dichiarata; «non dichiarato»
        (il simbolo non ha una voce: buco, non deduzione); «n.d.» (il negozio non si
        legge — dichiarato una volta sola in `_vista`). `natura()` rende la stessa
        forma per le ultime due (valore None, fonte 'nessuna'): la differenza la fa
        qui chi sa PERCHE'."""
        if _neg_rotto:
            return "n.d."
        v = _cl.natura(t, _neg).valore
        if not v:
            _senza_tipo.append(t)
            return "non dichiarato"
        return v

    def riga(r: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: _arr(r.get(k)) for k in campi if k in r}
        out["tipo"] = _tipo(str(r.get("ticker") or ""))
        if r.get("price_stale"):
            out["price_stale"] = True      # dichiarato solo quando c'e' davvero
        if r.get("fx_pl_note"):
            out["fx_pl_note"] = r["fx_pl_note"]   # la nota viaggia dove il numero e' n.d./approssimato
        return out

    # le righe si costruiscono PRIMA della vista: `_vista` deve poter CONTARE i
    # simboli senza tipo, e in un dict literal sarebbero ancora zero.
    righe_out = [riga(r) for r in righe_dict]
    if _neg_rotto:
        _nota_tipo = (" Campo `tipo`: n.d. su tutte le righe perche' il negozio dei veicoli e' "
                      + _neg["origine"].upper() + " (" + str(_neg["motivo"]) + "): la natura dei "
                      "titoli non e' determinabile — non dedurla dai simboli.")
    else:
        _nota_tipo = (" Campo `tipo`: la natura dichiarata nel negozio dei veicoli del PM "
                      "(fondo chiuso, ETF, DAT, societa' operativa, banca...). "
                      "«non dichiarato» = quel simbolo non ha una voce nel negozio: e' un buco, "
                      "non una deduzione dal ticker — %d su %d in questa vista."
                      % (len(_senza_tipo), len(righe_out)))

    compatto: Dict[str, Any] = {
        # `source` = provenienza, si tiene. Dal 01/08 (voce 9 §9-quattuortrigies)
        # la sovrascrittura in agent_tools e' CONDIZIONALE: con FX incompleto
        # l'avviso "FX non disponibile per X..." di memory_db ARRIVA fin qui.
        "source": p.get("source"),
        "nav_total_eur": _arr(p.get("nav_total_eur")),
        "cash_disponibile_eur": _arr(p.get("cash_disponibile_eur")),
        # F43(1) 27/08: la dichiarazione della cassa viaggia coi totali — senza,
        # gli agenti leggevano «cassa 0» anche quando era un buco di lettura
        "cash_source": p.get("cash_source"),
        "cash_source_note": p.get("cash_source_note"),
        "totale_valore_mercato_eur": _arr(p.get("totale_valore_mercato_eur")),
        "totale_pl_eur": _arr(p.get("totale_pl_eur")),
        # pl_eur_fx (28/08) + totale «come il broker» (31/08): coi totali, PRIMA
        # delle posizioni — None resta None (n.d. dichiarato, mai uno zero)
        "totale_pl_eur_fx": _arr(p.get("totale_pl_eur_fx")),
        "totale_fx_pl_eur": _arr(p.get("totale_fx_pl_eur")),
        "pl_fx_nd": p.get("pl_fx_nd"),
        "fx_pl_basis": p.get("fx_pl_basis"),
        "realizzato_eur_vendite": _arr(p.get("realizzato_eur_vendite")),
        "realizzato_vendite_n": p.get("realizzato_vendite_n"),
        "dividendi_eur": _arr(p.get("dividendi_eur")),
        "totale_aperto_piu_realizzato_eur": _arr(p.get("totale_aperto_piu_realizzato_eur")),
        "totale_aperto_piu_realizzato_note": p.get("totale_aperto_piu_realizzato_note"),
        "n_positions": p.get("n_positions", len(pos)),
        "stale_positions": p.get("stale_positions"),
        "fx_incomplete": p.get("fx_incomplete"),
        "prezzi_fonte": fonti_txt,
        "timestamp": p.get("timestamp"),
        "_vista": ("COMPATTA per gli agenti (il payload pieno sfora il tetto di "
                   f"{TETTO_TOOL_RESULT} char dei tool_result e veniva TAGLIATO, "
                   "perdendo le posizioni in coda E i totali). `valore_mercato` e' "
                   "gia' normalizzato in EUR, TRANNE per i ticker elencati in "
                   "`fx_incomplete`, dove resta in valuta nativa: quelli non vanno "
                   "sommati ne' confrontati. Campi tolti: "
                   + "; ".join(tolti) + ". Il payload pieno resta su GET /portfolio."
                   + _nota_tipo
                   + (f" ATTENZIONE: {scartate} righe di posizione non erano leggibili "
                      "e sono state scartate." if scartate else "")),
        "positions": righe_out,
    }

    # DEGRADAZIONE DICHIARATA: se anche la vista compatta, UNA VOLTA IMBUSTATA
    # (_stamp), sfora il tetto, si toglie il campo meno strutturale e LO SI DICE
    # — mai un taglio zitto. Il verdetto ora conta la busta (review 01/08) e si
    # dichiara ANCHE nel log della run, non solo al modello.
    if _peso_json(compatto) + _BUSTA_STAMP > TETTO_TOOL_RESULT:
        for r in compatto["positions"]:
            r.pop("prezzo_medio", None)
        print("[chat_tools] VISTA PORTFOLIO DEGRADATA: tolto prezzo_medio da "
              + str(len(compatto["positions"])) + " righe (vista+busta sopra i "
              + str(TETTO_TOOL_RESULT) + " char)")
        compatto["_vista"] += (" ATTENZIONE: anche la vista compatta, contata la "
                               "busta _stamp, sforerebbe il tetto: tolto `prezzo_medio` "
                               "da tutte le righe.")
    # e se NON basta nemmeno cosi', si dichiara anche quello (la degradazione a
    # colpo singolo lasciava il payload tagliato con una nota che diceva altro)
    return _dichiara_se_sfora(compatto, extra=_BUSTA_STAMP)


def _riassumi_serie(valori: Any) -> Any:
    """Serie giornaliera -> {ultimo, min, max, medio, n}. Un desk usa il LIVELLO e
    l'ampiezza, non i 102 punti. I None non vengono contati ne' trasformati in zeri."""
    if not isinstance(valori, list) or not valori:
        return valori
    import math
    def valido(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    nums = [v for v in valori if valido(v)]
    if not nums:
        # `n` = quanti valori USABILI, sempre. Nella prima stesura qui valeva
        # len(valori), cioe' lo stesso nome diceva due cose opposte: un modello
        # leggeva {"n": 2, "nota": "nessun valore calcolabile"} come "2 valori".
        return {"n": 0, "n_nd": len(valori),
                "nota": "nessun valore calcolabile (tutti n.d.)"}
    # `ultimo` appartiene all'ultima data: non spostare un valore precedente
    # sull'ultima osservazione quando questa manca.
    return {"ultimo": _arr(float(valori[-1])) if valido(valori[-1]) else None,
            "min": _arr(float(min(nums))),
            "max": _arr(float(max(nums))), "medio": _arr(sum(nums) / len(nums)),
            "n": len(nums),
            **({"n_nd": len(valori) - len(nums)} if len(nums) != len(valori) else {})}


def _compatta_montecarlo(p: Dict[str, Any]) -> Dict[str, Any]:
    """Vista compatta del Monte Carlo per gli agenti (B.9 dossier 24, 03/08).

    Misura della review 01/08 (confermata dal confutatore): a horizon 252 il
    payload pieno pesa ~11,2K char, a 63 ~10,9K; il taglio a 6000 cade DENTRO
    `fan_bands` e TUTTE le metriche di rischio — che la descrizione del tool
    promette esplicitamente (chat_tools.py:87) — non arrivavano MAI al desk:
    nella run viva 2 chiamate su 29 (hz=63 e default 252) a metriche ZERO.

    Tolti i dati di SOLO plotting (un modello non disegna: legge i numeri):
      - fan_bands (~4,6-4,9K: le bande del fan chart del PDF)
      - terminal_hist (istogramma marginale a lato del fan)
      - sample_paths / sample_paths_days / sample_paths_n (traiettorie di
        esempio; `n_sims` resta e dice quante simulazioni sono state fatte)
    Tutto il resto — metriche, percentili, stress_meta, pesi, caveat — passa
    INTATTO e coi numeri del modulo. Il payload pieno resta su
    GET /portfolio/montecarlo (bellomberg_api.py:2248): percorso HTTP intatto,
    come per le altre viste."""
    if _e_errore(p):
        return p
    if not any(k in p for k in ("fan_bands", "terminal_hist", "sample_paths")):
        # forma inattesa (niente da togliere): meglio intatta che una `_vista`
        # che dichiara una compattazione mai avvenuta
        return p
    tolti = ["fan_bands", "terminal_hist", "sample_paths", "sample_paths_days",
             "sample_paths_n"]
    # `_vista` IN TESTA: la dichiarazione deve sopravvivere a un eventuale
    # taglio in coda, non esserne la prima vittima
    compatto: Dict[str, Any] = {
        "_vista": ("COMPATTA per gli agenti (il payload pieno sfora il tetto di "
                   f"{TETTO_TOOL_RESULT} char dei tool_result e il taglio in coda "
                   "si mangiava TUTTE le metriche di rischio a horizon 63/252). "
                   "Tolti i dati di solo plotting: " + ", ".join(tolti) + ". "
                   "Il payload pieno resta su GET /portfolio/montecarlo.")}
    compatto.update({k: v for k, v in p.items() if k not in tolti})
    if _peso_json(compatto) + _BUSTA_STAMP > TETTO_TOOL_RESULT:
        compatto.pop("weights", None)
        print("[chat_tools] VISTA MONTECARLO DEGRADATA: tolti i pesi per-ticker "
              f"(vista+busta sopra i {TETTO_TOOL_RESULT} char)")
        compatto["_vista"] += (" ATTENZIONE: anche la vista compatta, contata la "
                               "busta _stamp, sforerebbe il tetto: tolti i "
                               "`weights` per-ticker (il book con i pesi e' su "
                               "get_portfolio_live).")
    return _dichiara_se_sfora(compatto, extra=_BUSTA_STAMP)


def _compatta_tearsheet(p: Dict[str, Any]) -> Dict[str, Any]:
    """Riassume le serie `rolling` (4.040 char di punti giorno-per-giorno) in
    livello/min/max/medio per finestra. Tutto il resto — mensili, episodi di
    drawdown, metriche scalari, note, basis — resta INTATTO e coi numeri del
    modulo: erano proprio quelli che il taglio mangiava.

    ⚠️ NIENTE arrotondamento qui, e la review pre-commit ha misurato perche':
    valeva **32 char** (4.648 -> 4.616) e in cambio rovinava `risk_free_used`, che
    portfolio_tearsheet scrive come FRAZIONE e non come percentuale — 0,0324
    arrotondato a 2 decimali diventa 0,03, cioe' un risk-free del 3,00% al posto
    del 3,24% dichiarato al modello. Tutto il resto del tearsheet e' gia'
    arrotondato dai produttori (advanced_metrics), quindi non c'era niente da
    guadagnare e c'era un tasso da sbagliare."""
    if _e_errore(p):
        return p
    roll = p.get("rolling")
    if not isinstance(roll, dict) or not roll:
        # niente da riassumere (succede davvero: portfolio_tearsheet lascia
        # `rolling` vuoto quando nessuna finestra e' piena). Nessuna
        # trasformazione => nessuna dichiarazione da scrivere: il payload esce
        # identico a come lo produce il modulo.
        return p
    out: Dict[str, Any] = {"_vista": ""}     # la dichiarazione va in TESTA
    out.update(p)
    nuovo: Dict[str, Any] = {}
    for w, blk in roll.items():
        if not isinstance(blk, dict):
            nuovo[w] = blk
            continue
        date = blk.get("dates") if isinstance(blk.get("dates"), list) else []
        r: Dict[str, Any] = {"window_days": blk.get("window_days")}
        if date:
            r["da"], r["a"], r["n_punti"] = date[0], date[-1], len(date)
        for k, v in blk.items():
            if k in ("window_days", "dates"):
                continue
            r[k] = _riassumi_serie(v)
        nuovo[w] = r
    out["rolling"] = nuovo
    out["_vista"] = ("COMPATTA per gli agenti: le serie `rolling` sono RIASSUNTE "
                     "(ultimo/min/max/medio per finestra) perche' il payload pieno "
                     f"sfora il tetto di {TETTO_TOOL_RESULT} char dei tool_result e "
                     "il taglio mangiava le metriche scalari e i caveat. Le serie "
                     "punto-per-punto restano su GET /portfolio/tearsheet. "
                     "Nessun altro valore e' toccato: i numeri sono quelli del modulo.")
    return _dichiara_se_sfora(out, extra=_BUSTA_STAMP)


def _compatta_attribution(p: Dict[str, Any]) -> Dict[str, Any]:
    """L'attribution sfora di poco (6.513 grezzi, tetto 6.000) e il taglio cade
    su `notes`, `basis` e meta' della frase di riconciliazione.

    ⚠️ La prima stesura arrotondava i decimali: MISURATO che non serve a niente —
    `portfolio_attribution` arrotonda gia' i suoi numeri, quindi non c'era spazio da
    recuperare li' (con la sola nota aggiunta il payload PEGGIORAVA a 6.829).

    Quello che recupera davvero spazio e' una codifica SENZA PERDITA delle 27 righe
    di `by_position` (3.966 char su 6.513): 18 righe su 27 sono in EUR e hanno
    `fx_pct` e `cross_pct` ESATTAMENTE 0, e per loro `local_pct` coincide con
    `contribution_pct`. Quei campi vengono omessi e la regola e' dichiarata: chi
    legge ricostruisce il valore esatto, non stima.

    Un campo a `None` (buco DICHIARATO dal modulo, es. FX mancante) non viene MAI
    omesso: `None == 0` e' falso, quindi la riga se lo tiene e il buco resta visibile.
    """
    if _e_errore(p):
        return p
    righe = p.get("by_position")
    if not isinstance(righe, list):
        return p

    compatte = []
    for r in righe:
        if not isinstance(r, dict):
            compatte.append(r)
            continue
        q = dict(r)
        if q.get("fx_pct") == 0 and q.get("cross_pct") == 0:
            q.pop("fx_pct", None)
            q.pop("cross_pct", None)
            if q.get("local_pct") == q.get("contribution_pct"):
                q.pop("local_pct", None)
        if q.get("currency") == "EUR":
            q.pop("currency", None)
        compatte.append(q)

    # La DICHIARAZIONE va in TESTA: se un domani il payload sforasse comunque, la
    # prima cosa da non perdere e' la chiave per leggere le righe compatte.
    out: Dict[str, Any] = {
        "_vista": ("COMPATTA, codifica SENZA PERDITA (tetto tool_result "
                   f"{TETTO_TOOL_RESULT} char). In `by_position`: manca "
                   "`fx_pct`/`cross_pct` => valgono ESATTAMENTE 0 (nessun effetto "
                   "cambio); manca `local_pct` => uguale a `contribution_pct`; manca "
                   "`currency` => EUR. Assenza = valore noto, non dato mancante (un "
                   "buco dichiarato dal modulo resta scritto). Pieno: "
                   "GET /portfolio/attribution."),
    }
    for k, v in p.items():
        out[k] = compatte if k == "by_position" else v
    # provenienza duplicata: _stamp la riscrive identica nel guscio esterno
    out.pop("_source", None)
    # il book cresce: misurato che l'attribution risfora intorno alle 40 righe,
    # e la coda che tornerebbe a cadere e' proprio reconciliation+notes+basis
    return _dichiara_se_sfora(out, extra=_BUSTA_STAMP)


# =============================================================================
# EVENTI SOCIETARI DI UN TICKER — voce E del dossier audit/25 (22/08, Opus 5)
# =============================================================================
# Il tool prometteva "eventi societari per un ticker" e il ticker non arrivava a
# nessuna fonte: chiedeva a `news_aggregator.fetch_corporate_events` gli eventi
# del PORTAFOGLIO e poi filtrava in Python su `ticker_mentioned` — chiave che
# solo il ramo SEC valorizza, quindi meta' del payload cadeva sempre; SEC a sua
# volta interrogava i soli ticker US in book; Finnhub non era mai contattato; e
# la cache aveva per chiave `corp_events:{days}:{max_items}`, senza il ticker.
# La risposta restava firmata "SEC EDGAR + Finnhub corporate events" anche a
# `count: 0`: uno zero falso indistinguibile da una misura (regola 14/07).
# Sui 37 log veri, 23 chiamate su 37 potevano solo restituire zero.
#
# Ora ogni fonte viene interrogata SUL TICKER CHIESTO e DICHIARA il proprio
# esito: interrogata con quanti eventi, oppure muta e perche'. La firma nomina
# solo le fonti che hanno risposto davvero.
MAX_EVENTI_TICKER = 20      # era un `[:20]` muto: oggi il taglio si dichiara

# Importanza di default per cio' che non ne porta una. Il vecchio percorso
# ordinava per `(-importance, -data)` (news_aggregator) e la prima stesura di
# questa cura aveva buttato l'importanza tenendo la sola data: a parita' di
# giorno un'ISO con la 'T' batte sempre una data nuda, quindi 20 headline
# Finnhub sfrattavano l'8-K dello stesso giorno da un tool che si chiama
# «eventi societari». I filing SEC portano gia' 3/4/5 (S-1 e 13D valgono 5).
# (Voce C 25/08: via `_IMPORTANZA_PRESS_RELEASE = 3` insieme alla chiamata a
# /press-releases — 403 PER SEMPRE sul piano gratuito, v. changelog (78)/(86).)
_IMPORTANZA_NEWS = 2


def _quando(valore: Any) -> str:
    """Chiave d'ordinamento omogenea per date che arrivano in piu' formati:
    SEC 'YYYY-MM-DD', Finnhub news ISO con la 'T' — e, difensivamente, date
    con lo spazio (il formato delle press release, tolte il 25/08: la
    normalizzazione resta perche' non costa nulla e regge formati misti).
    Senza normalizzazione il confronto lessicografico mescola a parita' di
    giorno. Una data assente resta '' e viene DICHIARATA a parte: non e' una
    data vecchia, e' una data che non c'e'.
    """
    s = str(valore or "").strip().replace(" ", "T")
    if len(s) == 10:            # data nuda: mezzanotte, cosi' e' confrontabile
        s += "T00:00:00"
    return s[:19]


def _esito_fonte(n: int, ok: int, ko: int, motivi: List[str]) -> str:
    """La riga che l'agente legge per sapere se il silenzio e' quiete o cecita'.

    Il verdetto si decide sulle CHIAMATE che hanno risposto, non sugli eventi
    trovati. La prima stesura guardava `n == 0` e su una chiamata VERA del
    22/08 (caso banca italiana: /company-news 200 con zero notizie, /press-releases 403 —
    il piano Finnhub in uso non la comprende) dichiarava la fonte MUTA: cecita'
    affermata dove c'era una misura, cioe' il difetto E rovesciato. Una fonte
    che risponde «zero» sta MISURANDO, e va detto.

    ⚠️ Dalla voce C (25/08) ogni fonte fa UNA chiamata: il ramo «interrogata
    PARZIALMENTE» e' DIFENSIVO, oggi irraggiungibile in produzione (ok e ko
    sono esclusivi su entrambe le fonti). Resta perche' il conteggio e' il
    contratto, non il chiamante di oggi; lo esercita il banco (mutazione F6).
    """
    if ok == 0:
        return "MUTA: " + ("; ".join(motivi) or "nessuna chiamata ha risposto")
    if ko:
        return ("interrogata PARZIALMENTE: %d eventi da %d chiamata/e su %d; "
                "non ha risposto: %s" % (n, ok, ok + ko, "; ".join(motivi)))
    return "interrogata: %d eventi" % n


def _eventi_societari_per_ticker(ticker: str, days: int) -> Tuple[Dict[str, Any], str]:
    """Ritorna (payload, firma). Interroga Finnhub e SEC EDGAR SUL TICKER."""
    eventi: List[Dict[str, Any]] = []
    fonti: Dict[str, str] = {}

    # --- Finnhub: company news col parametro ticker (`_norm_ticker` traduce
    #     gia' i suffissi europei e ha la mappa ADR). Voce C 25/08: la SECONDA
    #     chiamata (/press-releases) e' stata TOLTA — 403 PER SEMPRE sul piano
    #     gratuito (confermato dal PM il 22/08, changelog (78)): una HTTP
    #     sprecata a ogni invocazione, e il rumore del 403 nel verdetto
    #     («interrogata PARZIALMENTE» su fonte sana) spingeva fuori vista il
    #     caveat del simbolo ricostruito. Misura PRE 25/08 con --vero: 3/3.
    motivi_fh: List[str] = []
    n_fh = ok_fh = ko_fh = 0
    try:
        from bellomberg.market_data.finnhub_news import fetch_company_news
        prima = len(motivi_fh)
        for n in fetch_company_news(ticker, days=days, max_items=MAX_EVENTI_TICKER,
                                     motivo=motivi_fh):
            eventi.append({"title": (n.get("title") or "")[:160], "type": "news",
                            "date": n.get("published_at", ""),
                            "url": n.get("url", ""), "provider": "Finnhub",
                            "_imp": _IMPORTANZA_NEWS})
            n_fh += 1
        # una chiamata che NON deposita un motivo ha risposto: zero item da lei
        # e' una misura, non un buco (v. `_esito_fonte`).
        ok_fh += len(motivi_fh) == prima
        ko_fh += len(motivi_fh) != prima
    except Exception as e:
        motivi_fh.append("%s: %s" % (type(e).__name__, e))
        ko_fh += 1
    fonti["finnhub"] = _esito_fonte(n_fh, ok_fh, ko_fh, motivi_fh)
    # Su un nome non-US il simbolo Finnhub e' RICOSTRUITO da `_norm_ticker` con
    # una mappa che ha due sole voci esplicite di settore. I modi di
    # sbagliare sono DUE, e la prima stesura ne dichiarava uno solo: il simbolo
    # puo' non esistere (200 con lista vuota, uno zero che non prova la quiete)
    # oppure esistere ed essere un OMONIMO americano — e allora tornano notizie
    # vere di un'altra societa'. L'audit 11 del repo l'aveva gia' misurato.
    # Distinguerli richiederebbe una chiamata in piu' (/stock/profile2): finche'
    # non c'e', si dichiara cio' che si sa — con quale simbolo si e' chiesto.
    from bellomberg.market_data.finnhub_news import _norm_ticker
    simbolo = _norm_ticker(ticker)
    if simbolo != ticker:
        fonti["finnhub"] += (
            " [ATTENZIONE simbolo RICOSTRUITO per Finnhub: %s -> %s. Se quel "
            "simbolo non esiste la risposta e' 200 con zero item (uno zero che "
            "NON prova la quiete); se esiste ma e' un omonimo USA, queste "
            "notizie sono di un'altra societa'. Verifica il nome nei titoli "
            "prima di usarle]" % (ticker, simbolo))

    # --- SEC EDGAR: 8-K + filing strutturali + Form 4.
    motivi_sec: List[str] = []
    n_sec = ok_sec = ko_sec = 0
    non_copre = non_interrogata = ""
    try:
        from bellomberg.market_data.sec_edgar import (get_corporate_events_for_portfolio, lookup_cik,
                                ticker_ambiguo_per_cik)
        ambiguo = ticker_ambiguo_per_cik(ticker)
        if ambiguo:
            # NON si interroga: il CIK verrebbe risolto sul ticker senza
            # suffisso e potrebbe essere un'altra societa' (BA.L -> Boeing).
            # NON e' una fonte muta: e' un'astensione DECISA, con un motivo.
            # Chiamarla «MUTA» sarebbe dichiarare cecita' dove c'e' una scelta.
            non_interrogata = ambiguo
        elif lookup_cik(ticker, motivo=motivi_sec):
            prima = len(motivi_sec)
            for e in get_corporate_events_for_portfolio([ticker], days=days,
                                                         motivo=motivi_sec):
                eventi.append({"title": (e.get("title") or "")[:160],
                                "type": e.get("type", ""),
                                "date": e.get("date", ""),
                                "url": e.get("url", ""),
                                "provider": "SEC EDGAR",
                                "_imp": e.get("importance", 4)})
                n_sec += 1
            # `ok_sec` NON puo' essere un letterale: il CIK viene da
            # www.sec.gov/files/company_tickers.json, i filing da
            # data.sec.gov/submissions — due endpoint diversi. Risolvere il
            # primo non prova nulla sul secondo (voce E, review 22/08).
            ok_sec = 1 if len(motivi_sec) == prima else 0
            ko_sec = 0 if ok_sec else 1
        else:
            # l'elenco SEC ha RISPOSTO e dice che l'emittente non e' un filer:
            # e' una misura, non un silenzio.
            non_copre = "; ".join(motivi_sec)
            if any("assente dall'elenco dei filer" in m for m in motivi_sec):
                ok_sec = 1
            else:
                ko_sec = 1
    except Exception as e:
        motivi_sec.append("%s: %s" % (type(e).__name__, e))
        ko_sec = 1
    if non_interrogata:
        fonti["sec_edgar"] = "NON INTERROGATA (apposta): " + non_interrogata
    elif non_copre and ok_sec:
        fonti["sec_edgar"] = "NON COPRE questo emittente: " + non_copre
    else:
        fonti["sec_edgar"] = _esito_fonte(n_sec, ok_sec, ko_sec, motivi_sec)

    # --- dedup: un 8-K e la company-news che lo racconta arrivano con lo
    #     stesso titolo e lo stesso giorno — e' QUESTA la coppia che il dedup
    #     previene oggi (fino al 25/08 c'era anche press-release+news, sparita
    #     con la voce C: il dedup NON e' peso morto, il banco lo sorveglia).
    #     Il vecchio percorso deduplicava (news_aggregator._dedupe); senza,
    #     `count` si gonfia — e la description ordina di leggere `count`.
    visti = set()
    unici = []
    for e in eventi:
        chiave = (" ".join((e.get("title") or "").lower().split())[:80],
                   _quando(e.get("date"))[:10])
        if chiave in visti:
            continue
        visti.add(chiave)
        unici.append(e)
    eventi = unici

    # ordine: prima l'importanza, poi la data. Vedi `_IMPORTANZA_*`.
    eventi.sort(key=lambda e: (e.get("_imp", 3), _quando(e.get("date"))), reverse=True)
    totale = len(eventi)
    tenuti = eventi[:MAX_EVENTI_TICKER]
    senza_data = sum(1 for e in tenuti if not _quando(e.get("date")))
    for e in eventi:
        e.pop("_imp", None)

    # LE DICHIARAZIONI VANNO PRIMA DI `events`. Il taglio a valle
    # (specialists/base.py, red_team.py) e' cieco e IN CODA sulla stringa JSON:
    # con `events` davanti, uno sforamento cancellerebbe per prime `fonti`,
    # `taglio` e `avviso`, cioe' esattamente cio' che questa cura consegna.
    out: Dict[str, Any] = {"ticker": ticker, "giorni": days, "fonti": fonti,
                            "count": totale, "mostrati": len(tenuti)}
    if totale > len(tenuti):
        out["taglio"] = ("eventi trovati %d, nel payload %d: i %d esclusi sono i "
                          "MENO IMPORTANTI e, a parita' di importanza, i piu' "
                          "vecchi (filing SEC 3-5, news 2)"
                          % (totale, len(tenuti), totale - len(tenuti)))
    if senza_data:
        out["senza_data"] = ("%d eventi su %d non portano una data: non sono "
                              "vecchi, sono senza data — non usarli come catalyst"
                              % (senza_data, len(tenuti)))

    # Quattro stati per fonte: «interrogata» e «NON COPRE» sono MISURE (la
    # fonte ha risposto, anche se la risposta e' zero o «non ho giurisdizione»);
    # «MUTA» e «NON INTERROGATA» non lo sono. L'avviso si costruisce su questa
    # distinzione, non sulla parola «muta».
    senza_misura = sorted(k for k, v in fonti.items()
                           if not (v.startswith("interrogata") or v.startswith("NON COPRE")))
    mute = senza_misura
    risposte = ok_fh + ok_sec          # chiamate che hanno DAVVERO risposto
    if totale == 0 and risposte == 0:
        out["avviso"] = ("ZERO NON MISURATO: nessuna fonte ha fornito una misura "
                          "(%s) — questo zero NON e' una misura, non dedurne che "
                          "non sia successo nulla su %s"
                          % (", ".join("%s: %s" % (k, fonti[k].split(":")[0])
                                        for k in senza_misura), ticker))
    elif totale == 0 and mute:
        # Finnhub risponde «nessuna notizia» (misura) e SEC e' cieca, o
        # viceversa. Dire «non misurato» sarebbe falso, dire «zero misurato»
        # sarebbe falso al contrario: si dichiara la meta' vera.
        out["avviso"] = ("zero PARZIALMENTE misurato su %s: %d chiamata/e ha "
                          "risposto senza trovare eventi, ma da %s non c'e' "
                          "misura — la quiete e' accertata solo per le fonti che "
                          "hanno risposto"
                          % (ticker, risposte,
                             ", ".join("%s (%s)" % (k, fonti[k].split(":")[0])
                                        for k in senza_misura)))
    elif mute:
        out["avviso"] = ("copertura PARZIALE: da %s non c'e' misura — dichiara "
                          "il buco, non dedurre quiete dal silenzio"
                          % ", ".join("%s (%s)" % (k, fonti[k].split(":")[0])
                                       for k in senza_misura))
    elif totale == 0:
        # il rovescio, e vale piu' dell'altro: se tutte le fonti hanno risposto,
        # lo zero E' una misura. Senza questa riga l'agente non puo' distinguere
        # "niente e' successo" da "non ho potuto guardare", e tratta ogni zero
        # come sospetto — che e' il difetto E rovesciato.
        out["avviso"] = ("zero MISURATO: tutte le fonti hanno risposto e nessun "
                          "evento societario risulta su %s negli ultimi %d giorni"
                          % (ticker, days))

    out["events"] = tenuti

    vive = sorted(k for k, v in fonti.items()
                   if v.startswith("interrogata") or v.startswith("NON COPRE"))
    firma = "eventi societari %s - interrogate: %s" % (
        ticker, ", ".join(vive) if vive else "nessuna")
    if senza_misura:
        firma += "; SENZA MISURA: " + ", ".join(senza_misura)
    return _dichiara_se_sfora(out, extra=_BUSTA_STAMP), firma


def _guidance_drift_nudge(r, variant_view):
    """V6.5 (Lotto 3, design audit/19): testo del nudge anti-deriva o None.
    Scatta SOLO se il growth_path dell'analista devia oltre 5pp dalla guidance
    ATTIVA (deviazione calcolata dal motore in guidance_deviation_pp) E la
    variant view non cita la guidance. Mai blocco: l'analista resta sovrano —
    ma una deviazione muta da un fatto datato con fonte va fatta notare.
    Estratto dal dispatch per testabilita' offline. Review L3 B2: la citazione
    consapevole richiede la parola "guidance" E almeno una cifra ("guidance +18%,
    io +14%...") — una NEGAZIONE senza numeri ("non c'e' guidance") non sopprime.
    Limite dichiarato: e' un check a parola chiave, non semantico (chi cita la
    guidance con altre parole prende un nudge inutile ma innocuo)."""
    import re as _re
    _gdev = r.get("guidance_deviation_pp") if isinstance(r, dict) else None
    if _gdev is None or abs(_gdev) <= 5.0:
        return None
    _vv = str(variant_view or "").lower()
    if "guidance" in _vv and _re.search(r"\d", _vv):
        return None
    return ("NUDGE GUIDANCE (V6.5): il tuo growth_path anno-1 devia %+.1fpp dalla "
            "guidance societaria ATTIVA nel registro (fonte in growth_source nel "
            "payload) e la variant_view non la cita: o e' una variant view "
            "consapevole (DICHIARALA: 'guidance X%%, io Y%% perche'...') o stai "
            "ignorando un fatto datato con fonte." % _gdev)


def dispatch(tool_name: str, tool_input: Dict[str, Any], caller: str = None, *,
             prepared_bundle=None, sector_providers=None, as_of=None,
             valuation_preparer=None) -> Dict[str, Any]:
    """Esegue il tool richiesto. Mai solleva eccezioni - cattura tutto in dict error.
    caller (V6 Lotto 3, review B4): chi sta chiamando ("chat:fundamentals",
    "specialista-run:fundamentals", "red-team") — oggi usato per l'attribuzione
    fine di entered_by nel registro guidance; opzionale e retrocompatibile."""
    try:
        if tool_name == "get_filing_changes":
            from bellomberg.agents.filing_context import get_filing_changes
            return _stamp(get_filing_changes(tool_input.get("ticker")), "filing_archive")
        if tool_name == "get_portfolio_risk":
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            r = compute_portfolio_risk(force=bool(tool_input.get("force_refresh", False)))
            return _stamp(r, "portfolio_risk.compute_portfolio_risk")

        if tool_name == "get_sector_exposure":
            from bellomberg.portfolio.portfolio_sectors import compute_sector_exposure
            return _stamp(compute_sector_exposure(),
                          "portfolio_sectors.compute_sector_exposure")

        if tool_name == "get_attribution":
            from bellomberg.portfolio.portfolio_attribution import compute_attribution
            per = (tool_input.get("period") or "YTD").upper()
            return _stamp(_compatta_attribution(compute_attribution(period=per)),
                          "portfolio_attribution.compute_attribution")

        if tool_name == "get_tearsheet":
            from bellomberg.portfolio.portfolio_tearsheet import compute_tearsheet
            return _stamp(_compatta_tearsheet(compute_tearsheet()),
                          "portfolio_tearsheet.compute_tearsheet")

        if tool_name == "get_portfolio_garch":
            from bellomberg.portfolio.portfolio_garch import compute_portfolio_garch
            r = compute_portfolio_garch(force=bool(tool_input.get("force_refresh", False)))
            return _stamp(r, "portfolio_garch.compute_portfolio_garch")

        if tool_name == "get_portfolio_factors":
            from bellomberg.portfolio.portfolio_factors import compute_portfolio_factors
            period = tool_input.get("period", "1y")
            r = compute_portfolio_factors(period=period,
                                            force=bool(tool_input.get("force_refresh", False)))
            return _stamp(r, "portfolio_factors.compute_portfolio_factors")

        if tool_name == "quant_compute":
            from bellomberg.agents.agent_tools import tool_quant_compute
            p = tool_input.get("params", {}) or {}
            r = tool_quant_compute(
                operation=tool_input.get("operation"),
                tickers=tool_input.get("tickers", []),
                period=p.get("period", "6mo"),
                benchmark=p.get("benchmark", "SPY"),
                confidence=p.get("confidence", 0.95),
            )
            return _stamp(r, "agent_tools.quant_compute")

        if tool_name == "get_portfolio_montecarlo":
            from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
            r = run_monte_carlo(
                horizon_days=int(tool_input.get("horizon_days", 252)),
                n_sims=int(tool_input.get("n_sims", 10000)),
                stress_scenario=str(tool_input.get("stress") or "none"),
                add_tickers=tool_input.get("add_tickers"),
                remove_tickers=tool_input.get("remove_tickers"),
            )
            return _stamp(_compatta_montecarlo(r),
                          "portfolio_montecarlo.run_monte_carlo (FHS bank-grade; stress replay storico)")

        if tool_name == "get_var_backtest":
            from bellomberg.portfolio.var_backtest import backtest_var
            r = backtest_var(window=int(tool_input.get("window", 252)),
                             period=str(tool_input.get("period") or "3y"))
            return _stamp(r, "var_backtest.backtest_var (Kupiec POF + Christoffersen)")

        if tool_name == "get_price_live":
            from bellomberg.agents.agent_tools import tool_get_market_data
            r = tool_get_market_data(ticker=tool_input["ticker"])
            return _stamp(r, f"yfinance/IBKR via agent_tools.get_market_data({tool_input['ticker']})")

        if tool_name == "get_fundamentals":
            from bellomberg.agents.agent_tools import tool_get_fundamentals
            r = tool_get_fundamentals(ticker=tool_input["ticker"])
            return _stamp(r, f"yfinance via agent_tools.get_fundamentals({tool_input['ticker']})")

        if tool_name == "get_financial_history":
            from bellomberg.market_data.sec_xbrl import get_financial_history
            r = get_financial_history(tool_input["ticker"], years=int(tool_input.get("years", 10)))
            # V3 §9-sexies n.3: fallback DICHIARATO all'ESEF per i nomi EU senza SEC
            if isinstance(r, dict) and r.get("error"):
                try:
                    from bellomberg.market_data.esef import get_esef_history
                    r2 = get_esef_history(tool_input["ticker"], years=int(tool_input.get("years", 10)))
                    if not r2.get("error"):
                        r2["sec_note"] = "SEC non copre il ticker (%s): fonte ESEF" % r["error"]
                        return _stamp(r2, "ESEF filings.xbrl.org (" + str(tool_input["ticker"]) + ")")
                    r["esef_note"] = "anche ESEF senza dati: " + str(r2.get("error"))
                except Exception as _e2:
                    r["esef_note"] = "fallback ESEF fallito (%s)" % type(_e2).__name__
            return _stamp(r, "SEC XBRL companyfacts (" + str(tool_input["ticker"]) + ")")

        if tool_name == "get_dat_metrics":
            # 04/09 (Opus 5): stesso difetto di get_cef_lookthrough, qui col DAT del book.
            # La guardia sta PRIMA dell'import: il KO non dipende dal modulo a valle.
            t = str(tool_input.get("ticker") or "").strip()
            if not t:
                return _error("serve il ticker della DAT: nessun default, un ripiego zitto "
                              "sceglierebbe un titolo al posto tuo", "dispatcher.get_dat_metrics")
            from bellomberg.valuation.dat_metrics import get_dat_metrics
            r = get_dat_metrics(ticker=t)
            # il timbro dice la fonte che HA risposto (payload.nav_fonte), non una cablata:
            # un KO «non e' una DAT dichiarata» non viene da nessun sito (review 05/09, lotto 2b)
            _fonte = (r.get("nav_fonte") if isinstance(r, dict) else None) or "nessuna fonte raggiunta (errore dichiarato)"
            return _stamp(r, "dat_metrics.get_dat_metrics(" + str(t) + ") <- " + _fonte)

        if tool_name == "get_options_data":
            from bellomberg.agents.agent_tools import tool_get_options_data
            # bugfix #162: expiry ora supportata (usata nel ramo yfinance)
            r = tool_get_options_data(
                ticker=tool_input["ticker"],
                expiry=tool_input.get("expiry"),
            )
            return _stamp(r, f"IBKR TWS + yfinance fallback({tool_input['ticker']})")

        if tool_name == "get_option_expirations_polygon":
            from bellomberg.market_data.polygon_data import get_option_expirations
            r = get_option_expirations(tool_input["ticker"])
            return _stamp(r, f"polygon expirations({tool_input['ticker']})")

        if tool_name == "get_options_chain_polygon":
            from bellomberg.market_data.polygon_data import get_options_chain
            r = get_options_chain(tool_input["ticker"], tool_input.get("expiry"))
            return _stamp(r, f"polygon options chain({tool_input['ticker']})")

        if tool_name == "get_congress_trades":
            from bellomberg.market_data.quiver_data import get_congress_trades
            r = get_congress_trades(tool_input.get("ticker"),
                                    int(tool_input.get("limit", 30)))
            return _stamp(r, "quiver congress trading")

        if tool_name == "get_lobbying":
            from bellomberg.market_data.quiver_data import get_lobbying
            r = get_lobbying(tool_input.get("ticker"), int(tool_input.get("limit", 30)))
            return _stamp(r, "quiver lobbying")

        if tool_name == "get_gov_contracts":
            from bellomberg.market_data.quiver_data import get_gov_contracts
            r = get_gov_contracts(tool_input.get("ticker"), int(tool_input.get("limit", 30)))
            return _stamp(r, "quiver government contracts")

        if tool_name == "compute_gex":
            from bellomberg.portfolio.positioning_tools import compute_gex
            r = compute_gex(tool_input["ticker"])
            return _stamp(r, f"GEX SqueezeMetrics via polygon({tool_input['ticker']})")

        if tool_name == "get_cot_positioning":
            from bellomberg.portfolio.positioning_tools import get_cot_positioning
            r = get_cot_positioning(tool_input.get("market", "ES"))
            return _stamp(r, r.get("_source", "CFTC Commitments of Traders"))

        if tool_name == "get_vix_term_structure":
            from bellomberg.portfolio.positioning_tools import get_vix_term_structure
            r = get_vix_term_structure()
            return _stamp(r, "VIX term structure (yfinance)")

        if tool_name == "get_cef_lookthrough":
            # 04/09 (Opus 5): il default era un titolo del book — chiamato a vuoto il tool
            # rispondeva col fondo del PM. Argomento mancante = KO dichiarato (regola 14/07),
            # e la guardia sta PRIMA dell'import: il KO non dipende dal modulo a valle.
            _t = str(tool_input.get("ticker") or "").strip()
            if not _t:
                return _error("serve il ticker del fondo chiuso: nessun default, "
                              "un ripiego zitto sceglierebbe un titolo al posto tuo",
                              "dispatcher.get_cef_lookthrough")
            from bellomberg.valuation.cef_lookthrough import get_lookthrough
            r = get_lookthrough(_t)
            return _stamp(r, "CEF look-through (NAV ufficiale + 13F SEC del gestore, proxy dichiarato)")

        if tool_name == "get_cef_nav":
            from bellomberg.valuation.cef_nav import get_cef_nav
            r = get_cef_nav(tool_input["ticker"])
            return _stamp(r, f"NAV fondo chiuso({tool_input['ticker']})")

        if tool_name == "get_consensus_estimates":
            from bellomberg.market_data.consensus_estimates import get_consensus
            r = get_consensus(tool_input["ticker"])
            return _stamp(r, f"consensus analisti yfinance({tool_input['ticker']})")

        # --- V6 guidance (audit/19, decisioni PM D1-D4 23/07) ---
        if tool_name == "add_guidance":
            from bellomberg.storage.memory_db import MemoryDB
            # D4: scadenza = prossima trimestrale STRETTAMENTE futura dal calendar
            # (review V6 A1: l'evento di OGGI renderebbe stale domani la guidance
            # appena letta); n.d. = fallback +120g DICHIARATO dentro memory_db.
            _vu, _vus = None, None
            try:
                from bellomberg.market_data.finnhub_news import next_earnings_date
                _vu = next_earnings_date(tool_input["ticker"])
                if _vu:
                    _vus = "prossima trimestrale dal calendar (finnhub)"
            except Exception:
                pass  # fallback dichiarato in memory_db
            r = MemoryDB().add_guidance(
                ticker=tool_input["ticker"], metric=tool_input["metric"],
                period=tool_input["period"], value_mid=tool_input["value_mid"],
                value_low=tool_input.get("value_low"),
                value_high=tool_input.get("value_high"),
                unit=tool_input.get("unit"),
                source_doc=tool_input.get("source_doc"),
                source_date=tool_input.get("source_date"),
                valid_until=_vu, valid_until_source=_vus,
                # V6 Lotto 3 (chiude review B4): entered_by = il caller VERO del
                # dispatch ("chat:fundamentals" | "specialista-run:fundamentals"
                # | "red-team"); senza caller resta il generico di v1
                entered_by=(caller or "agente/chat (D1)"), note=tool_input.get("note"))
            return _stamp(r, "registro guidance V6")

        if tool_name == "get_guidance":
            from bellomberg.storage.memory_db import MemoryDB
            r = MemoryDB().get_guidance(
                tool_input["ticker"],
                include_history=bool(tool_input.get("include_history")))
            return _stamp(r, "registro guidance V6")

        if tool_name == "get_valuation":
            from bellomberg.valuation.sector_analysis import (
                prepare_sector_analysis, default_sector_providers, validate_bundle, revise_sector_analysis)
            from bellomberg.valuation.dcf_quality import normalize_valuation_payload, assess_valuation_usability
            from bellomberg.valuation.method_registry import is_record_method
            from bellomberg.valuation.dcf_engine import generate_valuation
            from datetime import date
            import json
            import os
            assumptions = {k: v for k, v in tool_input.items()
                           if k not in ("ticker", "analysis_context", "method_records") and v not in (None, "", [], {})}
            if "growth_path" in assumptions:
                assumptions["growth_override"] = assumptions.pop("growth_path")
            acquired_now = prepared_bundle is None
            if acquired_now:
                prepared_bundle = prepare_sector_analysis(tool_input["ticker"],
                    as_of=as_of or date.today().isoformat(),
                    providers=sector_providers if sector_providers is not None else default_sector_providers(),
                    user_context={"assumptions": assumptions,
                                  "method_records": tool_input.get("method_records"),
                                  "analysis_context": tool_input.get("analysis_context") or {}})
            bundle = validate_bundle(prepared_bundle, tool_input["ticker"])
            if as_of is not None and as_of != bundle["case"]["as_of"]:
                raise ValueError("Cutoff diverso dal bundle: acquisire un nuovo snapshot")
            if assumptions or tool_input.get("analysis_context") or tool_input.get("method_records") is not None:
                bundle = revise_sector_analysis(bundle, assumptions=assumptions or None,
                                                method_records=tool_input.get("method_records") if not acquired_now else None,
                                                analysis_context=tool_input.get("analysis_context"))
            # Capture CAS before any preparation/calculation. Publishing after
            # registration must not overwrite a concurrent or user-held version.
            valuation_db, model_versions, publication_head = None, None, None
            publication_state = {"status": "not_applicable", "reason": "Metodo senza record documentati"}
            if is_record_method(bundle["decision"]):
                try:
                    from bellomberg.storage.memory_db import MemoryDB
                    from bellomberg.storage.valuation_versions import ValuationVersions
                    from bellomberg.core.paths import MODELS_DIR
                    valuation_db = MemoryDB()
                    versions = ValuationVersions(valuation_db, roots=[REPORT_DIR, MODELS_DIR])
                    publication_head = versions.current(tool_input["ticker"])
                    model_versions = versions  # Only after the explicit schema check succeeded.
                    publication_state = {"status": "pending", "reason": "In attesa di calcolo e registrazione"}
                except Exception as exc:
                    publication_state = {"status": "unavailable", "reason": type(exc).__name__ + ": " + str(exc)}
            cache_note = "Nuove assunzioni: nuova generazione richiesta"
            if not assumptions and not tool_input.get("analysis_context") and tool_input.get("method_records") is None:
                try:
                    from bellomberg.storage.memory_db import MemoryDB
                    if valuation_db is None:
                        valuation_db = MemoryDB()
                    history = valuation_db.get_valuation_history(tool_input["ticker"], n=1)
                    thesis = history[0] if history else {}
                    current_payload = (publication_head or {}).get("current")
                    if current_payload:
                        with valuation_db._conn() as conn:
                            link = conn.execute("SELECT thesis_id FROM valuation_snapshot_links WHERE snapshot_id=? AND generation_id=? AND thesis_id IS NOT NULL",
                                (current_payload["snapshot_id"], current_payload["generation_id"])).fetchone()
                        thesis = {"valuation_payload": current_payload,
                                  "generation_id": current_payload["generation_id"],
                                  "id": link[0] if link else None}
                    expected = {**bundle["decision"], "snapshot_id": bundle["snapshot_id"],
                                "generation_id": thesis.get("generation_id")}
                    cached = thesis.get("valuation_payload") or {}
                    check = assess_valuation_usability(cached, expected_decision=expected,
                                                       as_of=bundle["case"]["as_of"])
                    if check["usable"]:
                        canon = cached.get("path") or os.path.join(str(REPORT_DIR),
                            "VAL_" + tool_input["ticker"].upper().replace(".", "_") + ".xlsx")
                        with open(os.path.splitext(canon)[0] + ".payload.json", encoding="utf-8") as stream:
                            sidecar = json.load(stream)
                        side_check = assess_valuation_usability(sidecar, expected_decision=expected,
                                                               as_of=bundle["case"]["as_of"])
                        from hashlib import sha256
                        with open(canon, "rb") as workbook:
                            workbook_hash = sha256(workbook.read()).hexdigest()
                        if (side_check["usable"] and sidecar.get("workbook_sha256") == workbook_hash
                                and cached.get("workbook_sha256") == workbook_hash):
                            result = normalize_valuation_payload(cached, expected_decision=expected,
                                                                 as_of=bundle["case"]["as_of"])
                            result.update(reused=True, path=canon,
                                          cache_note="Snapshot, metodo, fonti e integrita della generazione verificati",
                                          _thesis_saved={"thesis_id": thesis.get("id"),
                                                         "snapshot_id": result.get("snapshot_id")})
                            result["model_publication"] = ({
                                "status": "current" if (publication_head or {}).get("current_generation") == result["generation_id"] else "not_current",
                                "reason": "Versione corrente verificata nel registro" if (publication_head or {}).get("current_generation") == result["generation_id"] else "Generazione storica riusata, non corrente nel registro"
                            } if model_versions is not None else publication_state)
                            return _stamp(result, f"valuation riusata({tool_input['ticker']})")
                        cache_note = ("Sidecar non riutilizzabile: " + "; ".join(side_check["reasons"])
                                      if not side_check["usable"] else
                                      "Workbook modificato o hash assente: cache non riutilizzabile")
                    else:
                        cache_note = "Cache non riutilizzabile: " + "; ".join(check["reasons"])
                except Exception as exc:
                    cache_note = "Verifica cache fallita: " + type(exc).__name__ + ": " + str(exc)
            # The application supplies a budget-authorized service, never an LLM
            # tool argument. Explicit desk records and approved inputs retain
            # their existing path; a ticker mention alone grants no paid work.
            if (valuation_preparer is not None and not assumptions
                    and tool_input.get("method_records") is None and not bundle["case"]["records"]):
                if is_record_method(bundle["decision"]) and model_versions is None:
                    r = generate_valuation(tool_input["ticker"], prepared_bundle=bundle)
                    r["preparation"] = {"status": "blocked", "reason": publication_state["reason"]}
                else:
                    r = valuation_preparer(bundle)
                    bundle = validate_bundle(r["acquisition_snapshot"], tool_input["ticker"])
                    if bundle["case"]["as_of"] != prepared_bundle["case"]["as_of"]:
                        raise ValueError("Il preparatore ha cambiato il cutoff acquisito")
            else:
                r = generate_valuation(tool_input["ticker"], prepared_bundle=bundle)
            r = normalize_valuation_payload(r, expected_decision=bundle["decision"],
                                           as_of=bundle["case"]["as_of"])
            r["cache_note"] = cache_note
            documented = is_record_method(bundle["decision"])
            if not documented and isinstance(r, dict) and r.get("engine") == "mnav" and r.get("ok"):
                # V5 (D2): la semantica del numero va detta all'agente, che la
                # riporta nel report — mai spacciare la convergenza per un target price
                if r.get("fair_value_nav") is not None:
                    r["_analyst_note"] = ("VEICOLO (motore mNAV V5): il fair value e' NAV x "
                                          "nav_target dichiarato — upside alla CONVERGENZA del "
                                          "premio/sconto, NON un target price: dillo cosi' nel "
                                          "report e riporta warnings, fonti e vintage del foglio.")
                else:
                    r["_analyst_note"] = ("VEICOLO (motore mNAV V5): scheda informativa SENZA "
                                          "fair value (nessun nav_target — D2). Se hai una view "
                                          "sul premio/sconto, rilancia con nav_target motivato in "
                                          "variant_view; altrimenti riporta mNAV/sconto correnti "
                                          "col vintage, senza inventare un FV.")
            if not documented and isinstance(r, dict) and r.get("engine") == "rab" and r.get("ok"):
                r["_analyst_note"] = ("RETE REGOLATA (motore RAB V7): il fair value nasce da "
                                      "RAB/rendimento ammesso/premio — RIPORTA i warnings del "
                                      "modello (ancora STALE, premio fuori banda, divergenza) "
                                      "e la fonte della RAB nel tuo report.")
            if not documented and isinstance(r, dict) and r.get("engine") in ("bank", "insurance") and not tool_input.get("roe_path"):
                r["_analyst_note"] = ("ATTENZIONE (banca): non hai fornito roe_path - il modello ha usato il fade "
                                      "standard. Da analista dovresti passare la TUA traiettoria di ROE + "
                                      "target_payout dopo aver letto guidance su redditivita', piano di buyback e capitale (CET1). "
                                      "RIPORTA SEMPRE i warnings del modello (divergenza metodi) nel tuo report.")
            if not documented and isinstance(r, dict) and not tool_input.get("growth_path") and str(r.get("engine") or "").startswith("operating"):
                # #198 p.5: nudge anti-pigrizia — V6 (D2): se il payload porta
                # guidance_used il default NON e' piu' il CAGR cieco ma la
                # guidance societaria etichettata: il messaggio dice quello vero
                if r.get("guidance_used"):
                    r["_analyst_note"] = ("Nota: non hai fornito growth_path — il modello ha usato la "
                                          "GUIDANCE societaria ATTIVA del registro come default del base "
                                          "(v. growth_source e guidance_used nel payload, con fonte e "
                                          "vintage). Da analista puoi sovrascriverla con la TUA growth_path "
                                          "+ variant_view se hai una view diversa.")
                else:
                    r["_analyst_note"] = ("ATTENZIONE: non hai fornito growth_path: il modello ha usato il CAGR "
                                          "meccanico. Da analista buy-side dovresti passare la TUA growth_path + "
                                          "variant_view dopo aver letto guidance, consensus, catalyst e management.")
            # V6.5 (Lotto 3): nudge ANTI-DERIVA — mai blocco, solo _analyst_note
            _gn = _guidance_drift_nudge(r, tool_input.get("variant_view")) if not documented else None
            if _gn:
                r["_analyst_note"] = (r["_analyst_note"] + " | " + _gn) if r.get("_analyst_note") else _gn
            # V7 Lotto 3: se c'e' un SOTP l'agente lo riporta; se c'e' solo un buco
            # dichiarato NON deve citare un delta che non esiste (review codice C3)
            if not documented and isinstance(r, dict) and r.get("ok") and r.get("fair_value_sotp") is not None:
                _sn = ("SOTP (V7): riporta nel report il DELTA vs consolidato e le ATTENZIONI del "
                       "foglio 'SOTP (segmenti)' — il fair value headline resta il CONSOLIDATO (PM 21/07).")
                r["_analyst_note"] = (r["_analyst_note"] + " | " + _sn) if r.get("_analyst_note") else _sn
            elif not documented and isinstance(r, dict) and r.get("ok") and r.get("sotp_note"):
                _sn = ("SOTP (V7): NON prodotto o non fruibile — dichiara il buco nel report "
                       "citando sotp_note; NON citare un delta o un foglio che non esistono.")
                r["_analyst_note"] = (r["_analyst_note"] + " | " + _sn) if r.get("_analyst_note") else _sn
            # C1 16/07: la tesi si persiste SEMPRE (prima solo con variant_view -> le
            # chiamate nude non lasciavano traccia in valuation_theses e la pagina/storico
            # per ticker restava monco). Senza variant_view il campo lo DICHIARA.
            try:
                from bellomberg.storage.memory_db import MemoryDB
                san = r.get("sanity") or {}
                fair_value = next((r[k] for k in ("fair_value_final", "fair_value_weighted",
                    "fair_value_blend", "fair_value_base", "fair_value_nav") if r.get(k) is not None), None)
                variant_view = tool_input.get("variant_view")
                if documented:
                    rationales = bundle["analysis_context"].get("scenario_rationale")
                    variant_view = ("\n".join(f"{scenario}: {rationales[scenario]}" for scenario in ("bear", "base", "bull")
                        if isinstance(rationales.get(scenario), str) and rationales[scenario].strip())
                        if isinstance(rationales, dict) else None)
                if valuation_db is None:
                    valuation_db = MemoryDB()
                thesis_id = valuation_db.save_valuation_thesis(
                    ticker=tool_input["ticker"],
                    variant_view=variant_view or ("(rationale documentate assenti: FV n.d.)" if documented
                                                 else "(senza variant view - chiamata nuda)"),
                    growth_path=tool_input.get("growth_path") or tool_input.get("roe_path"),
                    price=r.get("price"), fair_value=fair_value,
                    ebitda_margin_target=tool_input.get("ebitda_margin_target"),
                    terminal_growth=tool_input.get("terminal_growth"),
                    engine=r.get("engine"), subsector=r.get("subsector"),
                    sanity_severity=san.get("severity"), sanity_headline=san.get("headline"),
                    profile_key=r.get("profile_key"), valuation_payload=r)
                r["_thesis_saved"] = ({"thesis_id": thesis_id, "snapshot_id": r.get("snapshot_id")}
                    if thesis_id is not None else {"error": "Snapshot/tesi non salvati: verificare migrazione metadata e log storage"})
            except Exception as exc:
                r["_thesis_saved"] = {"error": type(exc).__name__ + ": " + str(exc)}
            if model_versions is not None:
                if (r.get("_thesis_saved") or {}).get("thesis_id") is None:
                    publication_state = {"status": "not_registered", "reason": "Registrazione snapshot non riuscita"}
                else:
                    try:
                        publication_state = model_versions.publish(r["snapshot_id"], r["generation_id"],
                            expected_current_generation=(publication_head or {}).get("current_generation"))
                    except Exception as exc:
                        publication_state = {"status": "failed", "reason": type(exc).__name__ + ": " + str(exc)}
            r["model_publication"] = publication_state
            if isinstance(r, dict) and r.get("valuation_flagged"):
                # 204b-FIX: la sanity ha marcato la VAL (divergenza estrema fair value/prezzo).
                # L'agente DEVE riportarlo e NON proporre il nome in ACTION TABLE su questo modello.
                _hl = r.get("sanity_headline") or "Fair value molto distante dal prezzo: verificare growth/margini/WACC/shares."
                r["_analyst_note"] = ("[VAL FLAGGED] " + _hl + " " + (r.get("_analyst_note") or "")).strip()
            return _stamp(r, f"dcf_engine valuation({tool_input['ticker']})")

        if tool_name == "get_advanced_metrics":
            from bellomberg.portfolio.advanced_metrics import portfolio_metrics
            r = portfolio_metrics(benchmark_ticker=tool_input.get("benchmark", "SPY"))
            return _stamp(r, "advanced_metrics institutional")

        if tool_name == "get_edge_scan":
            from bellomberg.portfolio.signal_engine import scan_portfolio
            r = scan_portfolio(min_strength=int(tool_input.get("min_strength", 45)))
            # 22/08: la vista compatta vive SOLO qui, sul percorso LLM —
            # l'endpoint HTTP /signals/edge_scan non ci passa. ⚠️ NON e'
            # "byte-identico": la voce F gli ha aggiunto `copertura` (additiva)
            # e 28 nomi invece di 20; il 22/08 sera anche `cache` (additiva) e,
            # sui hit, la frase d'eta' nella nota. `force` NON e' nello schema:
            # gli agenti prendono la cache, e la description lo dichiara.
            return _stamp(_compatta_edge_scan(r), "signal_engine edge scanner")

        if tool_name == "get_position_doctor":
            from bellomberg.portfolio.signal_engine import position_doctor
            r = position_doctor(tool_input["ticker"])
            return _stamp(r, f"signal_engine position doctor({tool_input['ticker']})")

        if tool_name == "get_vol_surface_summary":
            from bellomberg.portfolio.vol_surface import build_vol_surface
            r = build_vol_surface(tool_input["ticker"], max_expiries=4)
            # versione compatta per il contesto agente: via la griglia pesante
            if not r.get("error"):
                r = {k: v for k, v in r.items()
                     if k not in ("slices", "moneyness_grid")}
                # IV Rank dallo storico raccolto (25/07): n_obs/young dichiarati,
                # error dichiarato finche' la storia non matura — mai un rank finto
                try:
                    from bellomberg.market_data.iv_history import get_iv_context
                    r["iv_history_context"] = get_iv_context(tool_input["ticker"])
                except Exception as e:
                    r["iv_history_context"] = {"error": str(e)}
            return _stamp(r, f"polygon vol surface({tool_input['ticker']})")

        if tool_name == "compare_assets":
            from bellomberg.agents.agent_tools import tool_compare_assets
            r = tool_compare_assets(
                tickers=tool_input["tickers"],
                period=tool_input.get("period", "1y"),
            )
            return _stamp(r, "agent_tools.compare_assets")

        if tool_name == "search_news":
            from bellomberg.agents.agent_tools import tool_search_news
            # signature: tool_search_news(query, max_results=10) - no days_lookback
            r = tool_search_news(
                query=tool_input["query"],
                max_results=int(tool_input.get("max_results", 10)),
            )
            # Opus 4.8 15/07: NewsAPI tolta dall'etichetta — tool_search_news non la chiama
            # (vive solo nella catena news_aggregator): lo stamp prometteva 5 fonti mentre
            # il payload ne dichiara 4, e ora che le fonti sono nominate la bugia si vedeva.
            return _stamp(r, "multi-source news (Marketaux/TheNewsAPI/GNews/yfinance)")

        if tool_name == "tavily_search":
            from bellomberg.agents.agent_tools import tool_tavily_search
            r = tool_tavily_search(
                query=tool_input["query"],
                max_results=int(tool_input.get("max_results", 5)),
            )
            return _stamp(r, "tavily.com web search API")

        if tool_name == "get_polymarket_events":
            from bellomberg.agents.agent_tools import tool_get_polymarket_events
            r = tool_get_polymarket_events(
                query=tool_input["query"],
                max_results=int(tool_input.get("max_results", 10)),
            )
            return _stamp(r, "polymarket gamma API")

        if tool_name == "get_hyperliquid_intel":
            from bellomberg.agents.agent_tools import tool_get_hyperliquid_intel
            # signature: tool_get_hyperliquid_intel(focus_asset="HYPE", builder_dexs=True)
            r = tool_get_hyperliquid_intel(focus_asset=tool_input.get("symbol", "HYPE"),
                                           builder_dexs=bool(tool_input.get("builder_dexs", True)))
            return _stamp(r, "hyperliquid DEX public API (main + HIP-3 builder dexs)")

        if tool_name == "get_macro_dashboard":
            from bellomberg.agents.agent_tools import tool_get_macro_dashboard
            r = tool_get_macro_dashboard()
            return _stamp(r, "FRED St. Louis Fed + fonti native ONS/Eurostat/IMF/BCB via agent_tools.get_macro_dashboard")

        if tool_name == "get_macro_indicator":
            from bellomberg.agents.agent_tools import tool_get_macro_indicator
            # signature: tool_get_macro_indicator(indicator, last_n=12)
            r = tool_get_macro_indicator(indicator=tool_input["series_id"])
            return _stamp(r, f"FRED series {tool_input['series_id']}")

        if tool_name == "get_yield_curves":
            from bellomberg.agents.agent_tools import tool_get_yield_curves
            r = tool_get_yield_curves()
            # lo stamp NON elenca le 4 fonti: se una alza 503 la sua gamba esce
            # {"status":"error"} e una lista fissa mentirebbe (stesso fix del 15/07 su un
            # ramo vicino). La fonte vera e' per-serie nel payload.
            return _stamp(r, "macro_rates (fonte e as_of dichiarati per serie in curves[*].src)")

        if tool_name == "get_portfolio_live":
            from bellomberg.agents.agent_tools import tool_get_portfolio_live
            r = tool_get_portfolio_live()
            return _stamp(_compatta_portfolio_live(r),
                          "memory_db.get_portfolio_summary (live FX)")

        if tool_name == "add_research_note":
            from bellomberg.agents.agent_tools import tool_add_research_note
            r = tool_add_research_note(tool_input["decision_id"], tool_input["note"])
            return _stamp(r, f"nota research #{tool_input.get('decision_id')}")

        if tool_name == "get_pending_decisions":
            from bellomberg.agents.agent_tools import tool_get_pending_decisions
            r = tool_get_pending_decisions()
            return _stamp(r, "memory_db.decisions (status=pending)")

        if tool_name == "search_past_memos":
            from bellomberg.agents.agent_tools import tool_search_past_memos
            r = tool_search_past_memos(
                query=tool_input["query"],
                n_results=int(tool_input.get("n_results", 5)),
            )
            return _stamp(r, "chromadb consigliere memos vector search")

        if tool_name == "check_decision_outcomes":
            from bellomberg.agents.agent_tools import tool_check_decision_outcomes
            r = tool_check_decision_outcomes()
            return _stamp(r, "memory_db.decisions + live prices for outcome calc")

        # === NEWS TERMINAL TOOLS ===
        if tool_name == "get_news_briefing":
            from bellomberg.cli.briefing_engine import get_current_briefing, generate_briefing
            if tool_input.get("force_refresh"):
                r = generate_briefing()
            else:
                r = get_current_briefing()
            return _stamp(r, "briefing_engine (Haiku 4x/day)")

        if tool_name == "get_macro_news_by_topic":
            from bellomberg.market_data.news_aggregator import fetch_macro_news
            cats_raw = tool_input.get("categories", "")
            cats = [c.strip() for c in cats_raw.split(",")] if cats_raw else None
            items = fetch_macro_news(
                categories=cats,
                min_importance=int(tool_input.get("min_importance", 4)),
                days=int(tool_input.get("days", 2)),
                max_per_topic=int(tool_input.get("max_per_topic", 3)),
                include_reddit=True,
            )
            compact = [{
                "title": it.get("title", "")[:140],
                "topic_label": it.get("topic_label", ""),
                "category": it.get("topic_category", ""),
                "importance": it.get("topic_importance"),
                "tickers_affected": it.get("tickers_affected", []),
                "url": it.get("url", ""),
                "provider": it.get("provider", ""),
                "published_at": it.get("published_at", ""),
                "snippet": (it.get("snippet") or "")[:200],
            } for it in items[:30]]
            # residuo muto #2 (Lotto C verita' dei numeri, 23/07): il tool macro
            # dei 5 specialisti dichiara i provider fuori per budget/disable —
            # "poche news macro" e "fonti spente" non sono piu' indistinguibili.
            from bellomberg.market_data.news_aggregator import providers_blocked
            _muti = providers_blocked()
            _out = {"count": len(items), "items": compact}
            if _muti:
                _out["fonti_mute"] = _muti
                _out["avviso"] = ("copertura PARZIALE: provider bloccati "
                                  + ", ".join(sorted(_muti))
                                  + " — dichiara il buco, non dedurre calma dal silenzio")
            return _stamp(_out, "news_aggregator.fetch_macro_news (multi-source + Reddit)")

        if tool_name == "get_corporate_events_for_ticker":
            ticker = tool_input["ticker"].upper()
            days = int(tool_input.get("days", 30))
            payload, firma = _eventi_societari_per_ticker(ticker, days)
            return _stamp(payload, firma)

        if tool_name == "get_insider_trades":
            ticker = tool_input["ticker"].upper()
            days = int(tool_input.get("days", 30))
            try:
                from bellomberg.market_data.finnhub_news import fetch_insider_trades as fn_insider
                trades = fn_insider(ticker, days=days)
                if trades:
                    return _stamp({"ticker": ticker, "source": "finnhub", "count": len(trades),
                                    "trades": trades[:20]},
                                    "Finnhub insider transactions (Form 4 real-time)")
            except Exception:
                pass
            from bellomberg.market_data.sec_edgar import get_insider_trades as sec_insider
            trades = sec_insider(ticker, days=days)
            return _stamp({"ticker": ticker, "source": "sec_edgar", "count": len(trades),
                            "trades": trades[:20]},
                            "SEC EDGAR Form 4 (fallback)")

        if tool_name == "get_earnings_calendar":
            from bellomberg.market_data.finnhub_news import fetch_earnings_calendar, fetch_earnings_for_portfolio
            ticker = tool_input.get("ticker")
            days = int(tool_input.get("days_ahead", 14))
            if ticker:
                items = fetch_earnings_for_portfolio([ticker], days_ahead=days)
            else:
                items = fetch_earnings_calendar(days_ahead=days)
            return _stamp({"count": len(items), "items": items[:100]},
                            "Finnhub earnings calendar")

        if tool_name == "get_13f_holdings":
            from bellomberg.market_data.sec_edgar import get_13f_holdings
            investor = tool_input["investor"].lower()
            max_items = int(tool_input.get("max_items", 25))
            holdings = get_13f_holdings(investor, max_items=max_items)
            return _stamp({"investor": investor, "count": len(holdings), "holdings": holdings},
                            "SEC EDGAR 13F-HR filings")

        return _error(f"unknown tool '{tool_name}'", "dispatcher")

    except Exception as e:
        return _error(
            f"{type(e).__name__}: {e}",
            f"dispatcher.{tool_name}",
        )


def get_tools_for_agent(agent_id: str) -> List[Dict[str, Any]]:
    """Subset di tools per ogni agente.
    Capo ha accesso a TUTTO. Ogni specialist ha i SUOI tool primari + shared.
    """
    ALL = TOOL_DEFINITIONS
    if agent_id == "capo":
        return ALL  # full access

    SUBSETS = {
        "macro": ["get_macro_dashboard", "get_macro_indicator", "tavily_search",
                  "search_news", "get_portfolio_live", "get_polymarket_events",
                  "search_past_memos", "compare_assets",
                  "get_news_briefing", "get_macro_news_by_topic",
                  "get_cot_positioning", "get_vix_term_structure",
                  # I-1 (26/07): il prompt macro lo ORDINA al punto 1b; senza questo
                  # nome il desk riceveva un ordine ineseguibile. Test: test_i1_fili_rotti.
                  "get_yield_curves"],
        "options": ["get_options_data", "get_options_chain_polygon",
                    "get_option_expirations_polygon", "compute_gex",
                    "get_vol_surface_summary", "get_position_doctor", "get_price_live",
                    "get_portfolio_live", "get_portfolio_garch", "search_news",
                    "tavily_search", "get_portfolio_risk", "get_earnings_calendar",
                    "get_vix_term_structure"],
        "quant": ["get_financial_history",
                  "get_portfolio_risk", "get_portfolio_garch", "get_portfolio_factors",
                  "get_portfolio_montecarlo", "quant_compute", "compare_assets",
                  "get_portfolio_live", "get_price_live",
                  "compute_gex", "get_cot_positioning", "get_vix_term_structure",
                  "get_vol_surface_summary", "get_edge_scan", "get_position_doctor",
                  "get_advanced_metrics", "get_var_backtest", "get_cef_lookthrough",
                  "get_sector_exposure", "get_attribution", "get_tearsheet"],
        "fundamentals": ["get_filing_changes", "get_valuation", "get_fundamentals", "get_price_live", "search_news",
                         "get_sector_exposure",
                         "tavily_search", "get_portfolio_live", "compare_assets",
                         "search_past_memos", "get_position_doctor",
                         "get_corporate_events_for_ticker", "get_insider_trades",
                         "get_gov_contracts", "get_congress_trades",
                         "get_earnings_calendar", "get_13f_holdings",
                         "get_dat_metrics", "get_financial_history",
                         "get_consensus_estimates", "get_cef_nav", "get_cef_lookthrough",
                         # V6 guidance (D1): il desk che LEGGE le trimestrali registra
                         "add_guidance", "get_guidance",
                         # I-1 (26/07): current_facts.py:416 ordina "DEVI rispondere con
                         # add_research_note", ma il nome non c'era -> ordine ineseguibile.
                         # NB (review): le 0 righe di `decision_notes` NON provano il guasto —
                         # il trigger scatta solo se esiste una NOTA PM, e non ce n'e' nessuna:
                         # la tabella sarebbe vuota anche col filo attaccato (misura circolare).
                         # La prova del filo rotto e' il nome assente, non il conteggio.
                         "add_research_note"],
        "crypto": ["get_hyperliquid_intel", "get_price_live", "search_news",
                   "tavily_search", "get_macro_indicator", "get_portfolio_live",
                   "get_macro_news_by_topic", "get_dat_metrics"],
        "politics": ["get_polymarket_events", "get_congress_trades", "get_lobbying",
                     "get_gov_contracts", "tavily_search", "search_news",
                     "get_macro_dashboard", "get_portfolio_live",
                     "get_macro_news_by_topic"],
        "news": ["search_news", "tavily_search", "get_polymarket_events",
                 "get_portfolio_live", "get_pending_decisions", "search_past_memos",
                 "get_news_briefing", "get_macro_news_by_topic",
                 "get_corporate_events_for_ticker", "get_insider_trades",
                 "get_earnings_calendar", "get_13f_holdings"],
        # EVENT DESK (fusione news+politics 15/07): unione dei due subset, 16 tool.
        # I subset news/politics sopra restano per i thread chat legacy.
        "eventdesk": ["search_news", "tavily_search", "get_polymarket_events",
                      "get_portfolio_live", "get_pending_decisions", "search_past_memos",
                      "get_news_briefing", "get_macro_news_by_topic",
                      "get_corporate_events_for_ticker", "get_insider_trades",
                      "get_earnings_calendar", "get_13f_holdings",
                      "get_congress_trades", "get_lobbying", "get_gov_contracts",
                      "get_macro_dashboard",
                      # V6 (review B5): il desk eventi LEGGE il registro guidance
                      # (confronto numeri usciti vs guidance registrata); scrive
                      # solo Fundamentals (D1)
                      "get_guidance"],
    }
    allowed_names = set(SUBSETS.get(agent_id, []))
    allowed_names.update(["get_portfolio_live", "search_past_memos"])
    return [t for t in ALL if t["name"] in allowed_names]
