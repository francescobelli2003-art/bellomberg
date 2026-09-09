"""
dcf_bank.py - Motore di valutazione BANCHE / FINANZIARI v2 (#203, buy-side).

Le banche NON si valutano con DCF unlevered. Metodi:
  1. RESIDUAL INCOME (Edwards-Bell-Ohlson), 10 anni, ROE path DECISO DALL'ANALISTA
     (roe_path agente) o fade verso un ROE terminale sostenibile (default Ke+1.5%,
     NON verso Ke secco in 5 anni: quella ipotesi azzerava il valore di ogni banca).
  2. P/TBV GIUSTIFICATO (Damodaran) = (ROE_term - g)/(Ke - g), su ROE TERMINALE.
  3. DDM (Gordon) su EPS x payout REALE.

FIX v2 rispetto al v1 (caso banca cross-listed):
  - Ke: beta yfinance grezzo (0.32 vs indice locale) -> floor/cap beta [0.9, 1.6]
    e floor Ke = rf + 3.5%. Override agente cost_of_equity ammesso.
  - Payout: non piu' info.payoutRatio cieco -> calcolato da cashflow REALE
    (dividendi pagati + buyback) / net income, con fonte tracciata. Override agente.
  - Input agente (#198 esteso alle banche): roe_path, target_payout, fade_years,
    terminal_growth, cost_of_equity -> il modello e' la TESI dell'analista.
  - Fogli: Thesis & Assumptions / Inputs & Sources / Residual Income (10y) /
    Peer Comps P-TBV / Cross-Check & Sensitivity / Summary.
  - FAIR VALUE NUMERICI nel risultato (ri/ptbv/ddm/blend) + divergenza tra metodi:
    alimentano sanity_check e il testo del memo (il numero non vive solo nell'Excel).
V4 (§9-novies n.1, 21/07):
  - DDM multi-stage COERENTE col RI (stessi book/ROE path/payout, clean surplus):
    la forte divergenza del caso banca nel #46 era un artefatto del Gordon su EPS trailing.
  - Storico banca nel workbook (foglio Historical) + costo del rischio
    through-the-cycle da impairment IFRS9 / crediti (ancora DICHIARATA, mai
    aggiustamento automatico del ROE).
  - Fade dell'excess ROE PER PROFILO (fade_years_default in sector_taxonomy):
    analista > profilo > 10 anni.
Interfaccia verso dcf_engine: build_bank_spec(ticker, info, wacc_inputs, ...)
+ build_bank_model(spec, output_path, ..., history=None). Robusto e guarded.
"""
from datetime import datetime
from typing import Dict, Any, List

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    OPX_OK = True
except ImportError:
    OPX_OK = False

NAVY = "0B2545"; GOLD = "B08D2E"; WHITE = "FFFFFF"; GREYTX = "5A6472"; INK = "1A1A1A"
BANK_BETA_FLOOR = 0.90   # una megabanca non ha beta 0.32: artefatto dell'indice locale
BANK_BETA_CAP = 1.60
KE_FLOOR_SPREAD = 0.035  # Ke mai sotto rf + 3.5%
# audit/13 V2.5e: TERM_ROE_PREMIUM rimossa — era una costante MORTA (il roe_term
# reale usa il franchise cap di profilo, vedi build_bank_spec).
N_YEARS = 10


def _safe(v, d=None):
    try:
        import math
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def _detect_pershare_currency(info):
    """audit/12 V0.1 (caso ADR con valore per azione quasi nullo): per gli ADR/cross-listed yfinance fornisce i
    campi PER-SHARE (bookValue, trailingEps) nella valuta di QUOTAZIONE, non in
    financialCurrency — convertirli di nuovo distrugge il fair value (13,08 USD
    trattato come JPY -> 0,08). Check DETERMINISTICO: P/B e P/E impliciti
    (prezzo / per-share) confrontati coi rapporti Yahoo; se coincidono, i per-share
    condividono la valuta del prezzo (ipotesi financialCurrency darebbe ~P/B 340).
    Ritorna (valuta | None, evidenza). None = NON verificabile o check discordi:
    il chiamante NON deve convertire in silenzio (regola no-fallback 14/07)."""
    quote = info.get("currency")
    fin = info.get("financialCurrency")
    if not fin or not quote or fin == quote:
        return (fin or quote), "financialCurrency == currency: nessuna ambiguita'"
    price = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))
    checks = []
    bvps, ptb = _safe(info.get("bookValue")), _safe(info.get("priceToBook"))
    if price and bvps and bvps > 0 and ptb and ptb > 0:
        checks.append(("P/B", (price / bvps) / ptb))
    eps, pe = _safe(info.get("trailingEps")), _safe(info.get("trailingPE"))
    if price and eps and eps > 0 and pe and pe > 0:
        checks.append(("P/E", (price / eps) / pe))
    if not checks:
        return None, ("valuta dei per-share NON verificabile (priceToBook/trailingPE "
                      "Yahoo assenti) con financialCurrency %s != quotazione %s" % (fin, quote))
    dett = ", ".join("%s implicito/Yahoo=%.3f" % (k, x) for k, x in checks)
    vals = [x for _, x in checks]
    if all(abs(x - 1.0) < 0.10 for x in vals):
        return quote, ("per-share GIA' in valuta di quotazione %s (%s)" % (quote, dett))
    # review V0 (caso HSBA.L): quotazione in pence con per-share in STERLINE — la firma
    # e' un ratio ~100 esatto (fattore GBP->GBp), NON financialCurrency: concluderla
    # sarebbe una conversione sbagliata certificata come verificata.
    if quote in ("GBp", "GBX") and all(abs(x - 100.0) < 10.0 for x in vals):
        return "GBP", ("per-share in GBP (sterline) con quotazione in pence %s: "
                       "ratio impliciti ~100 (%s)" % (quote, dett))
    # financialCurrency SOLO con prova positiva (review V0): ratio coerenti tra loro
    # e chiaramente lontani da 1 — un dato Yahoo stantio (gap 10-50%) non deve
    # ribaltare la detection verso una conversione sbagliata: meglio None dichiarato.
    _far = all(x > 1.5 or x < 0.67 for x in vals)
    _coerenti = (max(vals) / min(vals) < 1.25) if min(vals) > 0 else False
    if _far and _coerenti:
        return fin, ("per-share coerenti con financialCurrency %s: ratio impliciti "
                     "lontani da Yahoo in modo coerente (%s)" % (fin, dett))
    return None, ("ratio impliciti AMBIGUI (%s): valuta per-share non conclusa — "
                  "conversione rifiutata, serve verifica" % dett)


def _real_payout_from_cashflow(ticker_obj, net_income_hint=None):
    """Payout REALE = (dividendi pagati + buyback) / net income, dall'ultimo FY.
    Ritorna (payout_div, payout_total, source) o (None, None, motivo)."""
    try:
        cf = ticker_obj.cashflow
        fin = ticker_obj.financials
        if cf is None or cf.empty:
            return None, None, "cashflow non disponibile"
        col = cf.columns[0]

        def row(df, names):
            for n in names:
                if df is not None and not df.empty and n in df.index:
                    v = _safe(df.loc[n, col] if col in df.columns else df.loc[n].iloc[0])
                    if v is not None:
                        return v
            return None
        div_paid = row(cf, ["Cash Dividends Paid", "Common Stock Dividend Paid",
                            "Dividends Paid", "Payments Of Dividends"])
        buyback = row(cf, ["Repurchase Of Capital Stock", "Common Stock Payments"])
        ni = row(fin, ["Net Income", "Net Income Common Stockholders"]) or net_income_hint
        if not ni or ni <= 0:
            return None, None, "net income non disponibile/negativo"
        pd_ = abs(div_paid) / ni if div_paid else None
        pt = ((abs(div_paid) if div_paid else 0) + (abs(buyback) if buyback else 0)) / ni
        src = f"cashflow FY {getattr(col, 'year', col)}: div {abs(div_paid or 0)/1e9:.1f}bn"
        if buyback:
            src += f" + buyback {abs(buyback)/1e9:.1f}bn"
        return (round(pd_, 3) if pd_ else None,
                round(min(pt, 1.5), 3) if pt else None, src)
    except Exception as e:
        return None, None, f"errore payout: {type(e).__name__}"


def _build_roe_vec(roe_start, agent_path, fade_years, roe_term):
    """Vettore ROE 10y: anni dell'analista, poi fade lineare verso il terminale.
    Helper CONDIVISO con la sensitivity (fix verificatore: centro griglia = headline)."""
    n_agent = len(agent_path or [])
    vec = []
    for t in range(N_YEARS):
        if agent_path and t < n_agent:
            vec.append(float(agent_path[t]))
        else:
            start = vec[n_agent - 1] if n_agent else roe_start
            steps_left = max(fade_years - n_agent, 1)
            k = min(t + 1 - n_agent, steps_left)
            vec.append(start + (roe_term - start) * (k / steps_left))
    return vec


def build_bank_spec(ticker, info, wacc_inputs, roe_path=None, target_payout=None,
                    fade_years=None, terminal_growth=None, cost_of_equity=None,
                    variant_view=None, ticker_obj=None, profile=None):
    """Spec banca: dati reali + OVERRIDE DELL'ANALISTA (#203). Ogni input ha la fonte.
    profile (audit/13 V1.6): clamp/default del beta per profilo bancario
    (banks-regional/diversified/credit services/insurance) + ancore Damodaran."""
    src = {}
    # audit/12 V0.1: PRIMA di tutto la valuta VERA dei per-share yfinance (caso ADR:
    # sono in valuta di quotazione, non in financialCurrency come assumeva il payload)
    ps_cur, ps_evidence = _detect_pershare_currency(info)
    src["valuta_per_share"] = ps_evidence
    shares = _safe(info.get("sharesOutstanding"), 0) / 1e6
    bvps = _safe(info.get("bookValue"))
    book_total = (bvps * shares) if (bvps and shares) else None
    if not book_total and ticker_obj is not None:
        # audit/11 §4: 'totalStockholderEquity' NON esiste in yf .info (fallback sempre 0
        # = Residual Income scartato in silenzio): equity dal balance sheet REALE.
        try:
            bs = ticker_obj.balance_sheet
            if bs is not None and len(getattr(bs, "index", [])):
                for rowname in ("Stockholders Equity", "Total Stockholder Equity",
                                "Common Stock Equity", "Total Equity Gross Minority Interest"):
                    if rowname in bs.index:
                        v = _safe(bs.loc[rowname].dropna().iloc[0])
                        if v:
                            book_total = v / 1e6
                            src["book_value"] = f"balance_sheet '{rowname}'"
                            break
        except Exception:
            pass
        # audit/12 V0.1: il balance sheet e' in financialCurrency, ma se i per-share
        # (e il prezzo) sono in valuta di quotazione il RI mischierebbe unita' nello
        # stesso spec -> si allinea col cambio, o si SALTA dichiarando (mai unita' miste)
        _fin = info.get("financialCurrency")
        if book_total and _fin and ps_cur and ps_cur != _fin:
            from bellomberg.valuation.dcf_engine import _fx_pair_rate  # import runtime: nessun ciclo a import-time
            _rate = _fx_pair_rate(_fin, ps_cur)
            if _rate:
                book_total = book_total * _rate
                src["book_value"] += f" convertito {_fin}->{ps_cur} @{_rate:.6f} (allineato ai per-share)"
            else:
                book_total = None
                src["book_value_warn"] = (f"book dal balance sheet in {_fin} NON convertibile in "
                                          f"{ps_cur} (cambio n.d.): Residual Income SALTATO")
    if not book_total:
        src["book_value_warn"] = ("book value NON disponibile (bookValue*shares e balance "
                                  "sheet ko): Residual Income SALTATO dal blend")
    # --- ROE di partenza (audit/13 V2.5a, ok PM "floor di profilo") ---
    # trailing yfinance clampato a [roe_floor, roe_cap] DEL PROFILO; l'ancora
    # mid-cycle dell'industry Damodaran (REGIONE della banca) e' DICHIARATA come
    # termine di confronto nel foglio, non blenda il dato: la view sopra/sotto
    # il trailing e' dell'analista (roe_path + variant view), non del default.
    _p = profile or {}
    _roe_floor = _safe(_p.get("roe_floor"), 0.0)
    _roe_cap = _safe(_p.get("roe_cap"), 0.60)
    _anchor = None
    try:
        from bellomberg.market_data.damodaran_data import get_industry_field, region_for_country
        if _p.get("dam_industry"):
            _anchor = get_industry_field(_p["dam_industry"], "roe",
                                         region_for_country(info.get("country")))
    except Exception:
        _anchor = None
    roe_raw = _safe(info.get("returnOnEquity"))
    if roe_raw is not None:
        roe = roe_raw
        src["roe"] = "yfinance returnOnEquity %.1f%%" % (roe_raw * 100)
    elif _anchor is not None:
        roe = _anchor["value"]
        src["roe"] = ("ROE yfinance N.D.: usato il mid-cycle dell'industry %.1f%% [%s] "
                      "— DICHIARATO" % (roe * 100, _anchor["source"]))
    else:
        roe = 0.10
        src["roe"] = "ROE yfinance N.D. e nessuna ancora industry: DEFAULT 10% DICHIARATO"
    _roe_pre = roe
    roe = min(max(roe, _roe_floor), _roe_cap)
    if roe != _roe_pre:
        src["roe"] += "; clamp profilo [%.0f%%, %.0f%%] (V2.5)" % (_roe_floor * 100, _roe_cap * 100)
    if _anchor is not None and roe_raw is not None:
        src["roe_anchor"] = ("confronto mid-cycle industry: %.1f%% [%s] vs trailing %.1f%%"
                             % (_anchor["value"] * 100, _anchor["source"], roe_raw * 100))
    eps = _safe(info.get("trailingEps"))
    price = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))

    # --- PAYOUT: cashflow reale > info.payoutRatio > default settore ---
    payout = None
    if target_payout is not None and 0 < _safe(target_payout, 0) <= 1.2:
        payout = float(target_payout); src["payout"] = "OVERRIDE ANALISTA (target_payout)"
    elif target_payout is not None:
        src["payout_note"] = f"target_payout {target_payout} IGNORATO (fuori bound 0-1.2)"
    elif ticker_obj is not None:
        pdiv, ptot, psrc = _real_payout_from_cashflow(ticker_obj)
        if ptot and 0.05 <= ptot <= 1.2:
            payout = ptot; src["payout"] = "REALE (div+buyback): " + psrc
        elif pdiv and 0.05 <= pdiv <= 1.0:
            payout = pdiv; src["payout"] = "REALE (solo dividendi): " + psrc
    if payout is None:
        p_info = _safe(info.get("payoutRatio"))
        if p_info and 0.05 <= p_info <= 1.0:
            payout = p_info; src["payout"] = "yfinance payoutRatio (verificare)"
        else:
            payout = 0.40; src["payout"] = "DEFAULT 40% (nessun dato affidabile)"
    # audit/13 V2.5c: bound del payout DAL PROFILO (una fintech in crescita non
    # distribuisce come una banca matura) — clamp dichiarato, vale anche sull'override
    _pay_floor = _safe(_p.get("payout_floor"), 0.05)
    _pay_cap = _safe(_p.get("payout_cap"), 1.0)
    _pay_pre = payout
    payout = min(max(payout, _pay_floor), _pay_cap)
    if payout != _pay_pre:
        src["payout"] += ("; clamp profilo [%.0f%%, %.0f%%] (V2.5): %.0f%% -> %.0f%%"
                          % (_pay_floor * 100, _pay_cap * 100, _pay_pre * 100, payout * 100))

    # --- Ke: CAPM con beta disciplinato + floor; override analista ---
    # audit/13 V1.6: clamp e default del beta PER PROFILO bancario (una fintech in
    # crescita non ha i bound di una banca universale); fallback = costanti modulo.
    beta_raw = _safe(info.get("beta"))
    _bf = _safe((profile or {}).get("bank_beta_floor"), BANK_BETA_FLOOR)
    _bc = _safe((profile or {}).get("bank_beta_cap"), BANK_BETA_CAP)
    _bd = _safe((profile or {}).get("bank_beta_default"), 1.1)
    beta = min(max(beta_raw if beta_raw else _bd, _bf), _bc)
    src["beta"] = (f"yfinance {beta_raw} -> clamp profilo [{_bf},{_bc}]"
                   if beta_raw else f"default profilo {_bd} (beta yfinance assente)")
    rf = wacc_inputs["rf"]; erp = wacc_inputs["erp"]; crp = wacc_inputs.get("crp", 0)
    _wi_src = wacc_inputs.get("sources") or {}
    # audit/13 V1.2a (ramo banca): il modello sconta i PER-SHARE in ps_cur -> il rf
    # deve essere della stessa valuta. Per gli ADR (per-share USD, bilanci JPY)
    # il rf della calibrazione (valuta dei flussi = financialCurrency) va ri-chiesto
    # per ps_cur — usare rf JPY su flussi USD ricreerebbe l'errore di unita' V0.1.
    _fin_cur = (info.get("financialCurrency") or "").upper()
    if ps_cur and _fin_cur and ps_cur != _fin_cur:
        try:
            from bellomberg.market_data.market_inputs import get_risk_free_ex
            _rfx = get_risk_free_ex(ps_cur)
            rf = _rfx["value"]
            src["rf"] = ("rf %s %.2f%% [%s | oss. %s%s] — valuta dei per-share scontati, "
                         "non financialCurrency %s"
                         % (ps_cur, rf * 100, _rfx["source"], _rfx.get("obs_date") or "n.d.",
                            " | STALE" if _rfx.get("stale") else "", _fin_cur))
        except Exception as _e:
            src["rf"] = ("ATTENZIONE: rf per ps_cur %s NON ottenibile (%s): usato il rf "
                         "della calibrazione in %s — unita' potenzialmente miste, verificare"
                         % (ps_cur, _e, _fin_cur))
    else:
        src["rf"] = _wi_src.get("rf") or "rf della calibrazione"
    src["erp"] = _wi_src.get("erp") or "ERP della calibrazione"
    src["crp"] = _wi_src.get("crp") or "CRP della calibrazione"
    if cost_of_equity is not None and 0.04 <= _safe(cost_of_equity, 0) <= 0.25:
        ke = float(cost_of_equity); src["ke"] = "OVERRIDE ANALISTA (cost_of_equity)"
    else:
        ke = max(rf + beta * erp + crp, rf + KE_FLOOR_SPREAD)
        src["ke"] = f"CAPM rf {rf:.2%} + beta {beta:.2f} x ERP {erp:.2%} + CRP {crp:.2%}, floor rf+{KE_FLOOR_SPREAD:.1%}"

    # --- crescita terminale: agente > default geografia, cap a rf ---
    if terminal_growth is not None and 0 <= _safe(terminal_growth, -1) <= rf:
        g = float(terminal_growth); src["g"] = "OVERRIDE ANALISTA (terminal_growth)"
    else:
        g = min(0.03 if (info.get("country") not in ("Japan",)) else 0.015, rf)
        src["g"] = f"default geografia, cap a rf {rf:.2%}"

    # --- ROE path: agente > fade verso ROE terminale sostenibile ---
    # V4 (§9-novies n.1): fade PER PROFILO — gerarchia analista > profilo > 10 anni.
    # Banche mature/assicurazioni comprimono l'excess ROE in fretta (5y default),
    # i compounder fintech lo trattengono piu' a lungo (8y): fade_years_default
    # in sector_taxonomy, fonte dichiarata. Valore agente fuori bound = IGNORATO
    # dichiarato (regola 14/07), non silenziosamente rimpiazzato.
    _fy_agent = _safe(fade_years)
    _fy_prof = _safe(_p.get("fade_years_default"))
    if fade_years is not None and _fy_agent is None:
        # review V4 (B1): anche il non-numerico si dichiara (il vecchio int() crashava)
        src["fade_years_note"] = f"fade_years agente {fade_years!r} non numerico: IGNORATO (dichiarato)"
    if _fy_agent is not None and 3 <= int(_fy_agent) <= N_YEARS:
        fy = int(_fy_agent)
    elif _fy_prof is not None and 3 <= int(_fy_prof) <= N_YEARS:
        fy = int(_fy_prof)
        if _fy_agent is not None:
            src["fade_years_note"] = f"fade_years agente {fade_years} IGNORATO (fuori bound 3-{N_YEARS})"
    else:
        fy = N_YEARS
        if _fy_agent is not None:
            src["fade_years_note"] = f"fade_years agente {fade_years} IGNORATO (fuori bound 3-{N_YEARS})"
    # ROE terminale (audit/13 V2.5b, policy esplicita ok PM §6 n.6-B): il franchise trattiene
    # meta' dell'excess return corrente, cappato dal FRANCHISE CAP DI PROFILO
    # (banche tradizionali +4pp, credit services/fintech +8pp — non piu' +6pp unico);
    # SOPRA il cap si va solo con roe_path + variant view dell'analista (cap esteso
    # a 2x, dichiarato): il numero non si inventa, si argomenta.
    excess = max(roe - ke, 0.0)
    _fcap = _safe(_p.get("franchise_cap"), 0.06)
    roe_term = min(roe, ke + min(0.5 * excess, _fcap))
    roe_term = max(roe_term, ke + 0.005)  # almeno un filo sopra Ke se la banca e' sana
    src["roe_term"] = ("franchise: Ke + min(0.5 x excess, cap profilo %.0fpp) = %.1f%%"
                       % (_fcap * 100, roe_term * 100))
    # sanificazione path agente (fix verificatore: None in mezzo -> TypeError)
    clean_path = [float(x) for x in (roe_path or []) if _safe(x) is not None and -0.5 <= float(x) <= 0.6]
    if roe_path and len(clean_path) != len(roe_path):
        src["roe_path_note"] = f"path agente sanificato: {len(roe_path)} -> {len(clean_path)} valori validi"
    if clean_path and variant_view:
        _rt_ext = min(max(roe_term, clean_path[-1]), ke + 2 * _fcap)
        if _rt_ext > roe_term:
            src["roe_term"] += ("; OVERRIDE ANALISTA con variant view: terminale %.1f%% "
                                "(cap esteso a Ke+%.0fpp)" % (_rt_ext * 100, 2 * _fcap * 100))
            roe_term = _rt_ext
    elif clean_path and not variant_view:
        src["roe_term"] += "; path agente SENZA variant view: il terminale resta cappato dal profilo"
    vec = _build_roe_vec(roe, clean_path, fy, roe_term)
    # audit/13 V2.5d: vincolo di coerenza g <= ROE_term x (1 - payout) — la crescita
    # perpetua del book non puo' superare la retention terminale (primo morso alla
    # forte divergenza DDM/RI del caso banca; il DDM pienamente coerente resta V4).
    _g_sust = max(roe_term, 0.0) * max(1.0 - payout, 0.0)
    if g > _g_sust:
        src["g"] = (src.get("g", "") +
                    "; COERENZA V2.5: g %.2f%% > ROE_term x retention = %.2f%%: clampata"
                    % (g * 100, _g_sust * 100))
        g = round(max(0.0, _g_sust), 4)
    # V4: fonte del fade a tre vie (analista > profilo > default modulo)
    if _fy_agent is not None and 3 <= int(_fy_agent) <= N_YEARS:
        src["fade_years"] = "OVERRIDE ANALISTA"
    elif _fy_prof is not None and fy == int(_fy_prof):
        src["fade_years"] = f"default PROFILO {fy} anni (V4, sector_taxonomy)"
    else:
        src["fade_years"] = f"default {N_YEARS} anni (profilo senza fade_years_default)"
    src["roe_path"] = ("OVERRIDE ANALISTA anni 1-" + str(len(roe_path)) +
                       f", poi fade a ROE terminale {roe_term:.1%} entro anno {fy}"
                       if roe_path else f"fade lineare {roe:.1%} -> {roe_term:.1%} in {fy} anni")

    return {
        "ticker": ticker.upper(), "company_name": info.get("longName") or ticker,
        # audit/12 V0.1: la valuta del payload e' quella RILEVATA dei per-share (per gli
        # ADR = valuta di quotazione), non piu' financialCurrency assunta alla cieca
        "currency": ps_cur or info.get("financialCurrency") or info.get("currency") or "USD",
        "fx_contract": {"per_share_currency": ps_cur, "evidence": ps_evidence,
                        "verified": ps_cur is not None,
                        # review G4 17/07: la valuta di QUOTAZIONE viaggia nello spec —
                        # serve al contratto entry/per-share dell'IRR (caso pence HSBA.L)
                        "quote_currency": info.get("currency")},
        "country": info.get("country") or "n/d", "sector": info.get("sector") or "Financial",
        "shares": round(shares, 1), "book_value": round(book_total, 1) if book_total else None,
        "bvps": round(bvps, 2) if bvps else None, "roe": round(roe, 4),
        "roe_vec": [round(x, 4) for x in vec], "roe_terminal": round(roe_term, 4),
        "agent_roe_path": clean_path, "fade_years": fy, "payout": round(payout, 3), "eps": round(eps, 3) if eps else None,
        "price": price, "beta": round(beta, 3), "beta_raw": beta_raw, "ke": round(ke, 4),
        "rf": rf, "erp": erp, "crp": crp, "growth_lt": round(g, 4),
        "variant_view": variant_view, "_sources": src,
    }


# ---------- fair value NUMERICI (mirror python delle formule Excel) ----------
def _fv_residual_income(spec):
    bv = spec.get("book_value"); sh = spec.get("shares")
    if not bv or not sh:
        return None
    ke = spec["ke"]; pay = spec["payout"]; g = spec["growth_lt"]
    pv = 0.0; b = bv; ri_last = 0.0
    for t, roe_t in enumerate(spec["roe_vec"], start=1):
        ni = b * roe_t
        ri = ni - b * ke
        df = 1.0 / (1.0 + ke) ** t
        pv += ri * df
        ri_last, df_last = ri, df
        b = b + ni * (1.0 - pay)
    term = ri_last * (1.0 + g) / (ke - g) if ke > g else 0.0
    eq = bv + pv + term * df_last
    return round(eq / sh, 2)


def _fv_ptbv(spec):
    bvps = spec.get("bvps")
    if not bvps:
        return None
    ke = spec["ke"]; g = spec["growth_lt"]; rt = spec["roe_terminal"]
    if ke <= g:
        return None
    return round(bvps * (rt - g) / (ke - g), 2)


def _fv_ddm(spec):
    """V4 (§9-novies n.1): DDM multi-stage COERENTE col Residual Income — stessi
    stati (book per azione, ROE path, payout, retention) e stesso orizzonte 10y.
    DIV_t = BVPS_{t-1} x ROE_t x payout; book evolve con la retention (clean
    surplus, identico a _fv_residual_income e compute_holding_irr). TERMINALE
    (misura probe 21/07: il Gordon su DIV_10 lasciava una forte divergenza,
    perche' payout 40% + g 3% sono internamente CONTRADDITTORI — retention 60%
    a ROE 11% implica crescita 6,9%, non 3%): payout terminale ENDOGENO
    1 - g/ROE (identita' di crescita sostenibile) => TV = Book_10 x P/TBV
    giustificato (ROE_10 - g)/(Ke - g) — la STESSA exit dell'IRR di holding (G4)
    e la stessa formula del metodo P/TBV. Cosi' DDM e RI convergono con clean
    surplus e la divergenza residua e' un segnale, non un artefatto del caso #46.
    Il vecchio Gordon a stadio unico su EPS trailing ignorava path/fade/book.
    Nota: in un anno di perdita il 'dividendo' e' negativo (= aumento di capitale
    implicito) — stessa convenzione del RI e dell'IRR, nessun floor zitto.
    Book n.d. -> None (dichiarato a monte, mai ripiego EPS)."""
    bvps = spec.get("bvps")
    if not bvps and spec.get("book_value") and spec.get("shares"):
        bvps = spec["book_value"] / spec["shares"]
    if not bvps or bvps <= 0:
        return None
    ke = spec["ke"]; g = spec["growth_lt"]
    pay = spec["payout"]
    roe_vec = spec.get("roe_vec") or []
    if ke <= g or not roe_vec:
        return None
    b = bvps; pv = 0.0; df_last = 1.0
    for t, roe_t in enumerate(roe_vec, start=1):
        ni = b * roe_t
        df_last = 1.0 / (1.0 + ke) ** t
        pv += ni * pay * df_last
        b = b + ni * (1.0 - pay)
    term = b * (roe_vec[-1] - g) / (ke - g)  # Book_10 x P/TBV giustificato
    return round(pv + term * df_last, 2)


def compute_fair_values(spec) -> Dict[str, Any]:
    import statistics
    ri = _fv_residual_income(spec); pt = _fv_ptbv(spec); dd = _fv_ddm(spec)
    named = [(k, v) for k, v in (("RI", ri), ("P/TBV", pt), ("DDM", dd)) if v and v > 0]
    warn = []
    used = list(named)
    if len(named) >= 3:
        med = statistics.median(v for _, v in named)
        outl = [(k, v) for k, v in named if abs(v / med - 1.0) > 0.6]
        _keep = [(k, v) for k, v in named if (k, v) not in outl]
        # review V4 (M3): RI e DDM sono CORRELATI per costruzione (stessi stati) —
        # se l'outlier e' proprio il P/TBV, l'unico cross-check indipendente, i due
        # gemelli non possono votarlo fuori: resta nel blend, divergenza da spiegare.
        if outl and {k for k, _ in _keep} == {"RI", "DDM"}:
            warn.append("Metodo P/TBV oltre il 60% dalla mediana ma NON escluso dal blend: "
                        "RI e DDM sono correlati per costruzione (V4) e voterebbero fuori "
                        "l'unico cross-check indipendente — divergenza da spiegare, non da espellere.")
        elif outl and len(_keep) >= 2:
            used = _keep
            for k, v in outl:
                warn.append(f"Metodo {k} ({v}) ESCLUSO dal blend: outlier >60% dalla mediana - "
                            "input sospetto: verificare la fonte nel foglio Thesis.")
    # review V4 (M2): con un roe_path COMPLETO di 10 anni il terminale (RI e DDM)
    # gira sull'anno 10 del path, non sul ROE terminale cappato dal profilo —
    # legittimo con view argomentata, ma va DETTO: il franchise cap non morde li'.
    _rv = spec.get("roe_vec") or []
    _rt = spec.get("roe_terminal")
    if _rv and _rt is not None and abs(_rv[-1] - _rt) > 1e-9:
        warn.append("TERMINALE su ROE anno-10 del path analista (%.1f%%), non sul ROE terminale "
                    "di profilo (%.1f%%): il franchise cap NON morde sulla perpetuita' — "
                    "view da motivare." % (_rv[-1] * 100, _rt * 100))
    vals = [v for _, v in used]
    blend = round(statistics.median(vals), 2) if vals else None
    div = round((max(vals) / min(vals) - 1.0), 2) if len(vals) >= 2 and min(vals) > 0 else None
    if div is not None and div > 0.5:
        warn.append(f"DIVERGENZA TRA METODI USATI {div:+.0%}: rivedere Ke/ROE terminale/payout PRIMA di usare il numero.")
    return {"fair_value_ri": ri, "fair_value_ptbv": pt, "fair_value_ddm": dd,
            "fair_value_blend": blend, "blend_methods": [k for k, _ in used],
            "methods_divergence": div, "warnings": warn}


# ---------- V4 (§9-novies n.1): costo del rischio through-the-cycle dallo storico ----------
def _cost_of_risk_ttc(history):
    """Serie impairment IFRS9 / crediti a clientela (bps) dallo storico SEC/ESEF.
    Ritorna dict (anni, bps per anno, media di ciclo, ultimo FY) o None se le due
    serie mancano o si sovrappongono per <3 anni (2 punti non fanno un 'ciclo':
    n.d. dichiarato dal chiamante). SEGNO: nei filing l'impairment e' quasi sempre
    NEGATIVO (perdita) — convenzione qui: costo POSITIVO in bps, normalizzato col
    segno di MAGGIORANZA della serie (un anno di ripresa resta negativo = rilascio,
    dichiarato); mai abs() cieco che trasformerebbe un rilascio in costo."""
    items = (history or {}).get("items") or {}
    imp = items.get("impairment_ifrs9") or {}
    loans = items.get("loans_to_customers") or {}
    years = sorted(y for y in (set(imp) & set(loans))
                   if _safe(imp.get(y)) is not None and (_safe(loans.get(y)) or 0) > 0)
    if len(years) < 3:
        return None
    vals = [_safe(imp[y]) for y in years]
    n_neg = sum(1 for v in vals if v < 0)
    n_pos = sum(1 for v in vals if v > 0)
    sign = -1.0 if n_neg * 2 >= len(vals) else 1.0
    # review V4 (M1 finanza): con serie a segni MISTI (es. rilasci overlay post-COVID
    # dominanti nella finestra ESEF corta) il voto di maggioranza puo' invertire la
    # lettura e far parlare i warning al contrario -> convenzione DICHIARATA come non
    # determinabile e warning sotto/sopra-media soppressi dal chiamante (regola 14/07).
    mixed = min(n_neg, n_pos) >= len(vals) / 3.0
    bps = {y: round(sign * _safe(imp[y]) / _safe(loans[y]) * 1e4, 1) for y in years}
    avg = round(sum(bps.values()) / len(bps), 1)
    last_y = max(years)
    return {"years": years, "bps": bps, "avg_bps": avg,
            "last_bps": bps[last_y], "last_year": last_y, "sign": sign, "mixed": mixed,
            "sign_note": (("serie a segni MISTI (%d/%d anni negativi): convenzione del filing "
                           "NON determinabile dal solo storico — verificare il filing, "
                           "giudizi sotto/sopra ciclo soppressi" % (n_neg, len(vals))) if mixed
                          else ("impairment NEGATIVO nei filing: costo mostrato positivo"
                                if sign < 0 else "impairment positivo nei filing: usato tal quale")),
            "source": str((history or {}).get("_source") or "storico XBRL")}


def _sheet_historical_bank(wb, spec, history, cor=None):
    """V4: storico banca riga-per-riga nel workbook BANCA — prima il ramo banca di
    dcf_engine usciva PRIMA del fetch storico e questo foglio non esisteva. Righe
    bancarie (margine interesse, commissioni, impairment IFRS9, crediti/depositi)
    + net income/equity se taggate; derivate a FORMULE VIVE (costo del rischio in
    bps, loans/deposits). Storico assente o STALE = foglio col motivo dichiarato.
    Book value bancario NON taggato ESEF (Circ.262) e CET1/RWA = Pillar 3: fonti
    dichiarate nel Thesis, non inventate qui (nota misurata §9-sexies n.3)."""
    ws = wb.create_sheet("Historical (banca)")
    _src = str((history or {}).get("_source") or "SEC/ESEF XBRL")
    T(ws, "A1", f"Storico banca riga-per-riga ({_src})")
    if not history or history.get("error"):
        L(ws, "A3", "Storico non disponibile per questo nome: "
          + str((history or {}).get("error", "n.d.")), italic=True, color=GREYTX)
        L(ws, "A4", "(book value da yfinance dichiarato in Thesis; CET1/RWA = Pillar 3, fuori ESEF)",
          italic=True, color=GREYTX)
        ws.column_dimensions["A"].width = 40
        return
    years = history.get("years") or []
    items = history.get("items") or {}
    if not years:
        # cintura (review V4): col contratto sec_xbrl/esef years non e' mai vuoto
        # senza error, ma un foglio rotto non deve far saltare il workbook
        L(ws, "A3", "Storico senza anni (contratto inatteso): foglio vuoto dichiarato",
          italic=True, color=GREYTX)
        ws.column_dimensions["A"].width = 40
        return
    cols = [get_column_letter(2 + i) for i in range(len(years))]
    _all_vals = [abs(_safe(v, 0) or 0) for s in items.values() for v in s.values()]
    scale = 1e6 if (_all_vals and max(_all_vals) > 1e7) else 1.0
    unit = ",".join(set((history.get("units") or {}).values()))
    _notes = []
    for k in ("coverage_note", "gaps", "index_note", "accounting_basis"):
        v = history.get(k)
        if v:
            _notes.append(("BUCHI: " + "; ".join(str(x) for x in v[:3])) if k == "gaps" else str(v))
    if _notes:
        L(ws, "A2", " | ".join(_notes), italic=True, color=GREYTX)
    H(ws, "A3", f"VOCE ({unit}{'m' if scale > 1 else ''})")
    for i, y in enumerate(years):
        H(ws, f"{cols[i]}3", str(y))
    ROWS = [("interest_revenue", "Interest revenue (lordo)"),
            ("interest_expense_bank", "Interest expense"),
            ("fee_commission_net", "Net fee & commission"),
            ("fee_commission_income", "Fee & commission income"),
            ("trading_income", "Trading income"),
            ("impairment_ifrs9", "Impairment IFRS9 (costo del rischio)"),
            ("net_income", "Net Income"),
            ("equity", "Equity (se taggata nel filing)"),
            ("loans_to_customers", "Loans to customers"),
            ("loans_to_banks", "Loans to banks"),
            ("deposits_from_customers", "Deposits from customers"),
            ("deposits_from_banks", "Deposits from banks")]
    r = 4
    rowmap = {}
    for key, label in ROWS:
        if key not in items:
            continue
        L(ws, f"A{r}", label)
        for i, y in enumerate(years):
            v = _safe(items[key].get(y))
            if v is not None:
                N(ws, f"{cols[i]}{r}", v / scale)
        rowmap[key] = r
        r += 1
    _extra = sorted(set(items) - {k for k, _ in ROWS})
    if _extra:
        L(ws, f"A{r}", "(altre %d voci nel payload, leggibili via tool get_financial_history: %s)"
          % (len(_extra), ", ".join(_extra[:6]) + ("..." if len(_extra) > 6 else "")),
          italic=True, color=GREYTX)
        r += 1
    r += 1
    H(ws, f"A{r}", "DERIVATE (formule vive sulle righe sopra)")
    r += 1

    def _has(key, i):
        return key in rowmap and _safe(items[key].get(years[i])) is not None

    if "impairment_ifrs9" in rowmap and "loans_to_customers" in rowmap:
        # review V4 (A1, convergente 2 agenti): con cor=None (overlap <3 anni) il
        # segno NON si normalizza (as-filed) e l'etichetta lo dice — il vecchio
        # default -1 negava la formula sotto un'etichetta "come nel filing" (falso).
        _sgn = "-" if (cor and (cor.get("sign") or 1.0) < 0) else ""
        L(ws, f"A{r}", "Costo del rischio (bps su crediti clientela; %s)"
          % (cor.get("sign_note") if cor else
             "overlap <3 anni: segno NON normalizzato, come nel filing"))
        for i in range(len(years)):
            # review V4 (B4): stesso filtro del mirror Python (loans > 0, non solo != 0)
            if _has("impairment_ifrs9", i) and _has("loans_to_customers", i) \
                    and (_safe(items["loans_to_customers"][years[i]], 0) or 0) > 0:
                ws[f"{cols[i]}{r}"] = (f"={_sgn}{cols[i]}{rowmap['impairment_ifrs9']}"
                                       f"/{cols[i]}{rowmap['loans_to_customers']}*10000")
                ws[f"{cols[i]}{r}"].number_format = "0.0"
        _cor_row = r
        r += 1
        if cor:
            L(ws, f"A{r}", "Media di ciclo (bps) — ancora THROUGH-THE-CYCLE, V4", bold=True)
            ws[f"B{r}"] = f"=AVERAGE(B{_cor_row}:{cols[-1]}{_cor_row})"
            ws[f"B{r}"].number_format = "0.0"
            ws[f"B{r}"].font = Font(bold=True, color=GOLD, size=9)
        else:
            # finestra rifiutata da _cost_of_risk_ttc: media semplice, MAI venduta come ciclo
            L(ws, f"A{r}", "Media semplice della finestra (<3 anni: NON through-the-cycle, dichiarato)")
            ws[f"B{r}"] = f"=AVERAGE(B{_cor_row}:{cols[-1]}{_cor_row})"
            ws[f"B{r}"].number_format = "0.0"
        r += 1
    else:
        L(ws, f"A{r}", "Costo del rischio n.d.: impairment IFRS9 o crediti clientela non taggati "
          "in questo storico (buco dichiarato)", italic=True, color=GREYTX)
        r += 1
    if "loans_to_customers" in rowmap and "deposits_from_customers" in rowmap:
        L(ws, f"A{r}", "Loans / Deposits (clientela)")
        for i in range(len(years)):
            if _has("loans_to_customers", i) and _has("deposits_from_customers", i) \
                    and (_safe(items["deposits_from_customers"][years[i]], 0) or 0) > 0:
                ws[f"{cols[i]}{r}"] = (f"={cols[i]}{rowmap['loans_to_customers']}"
                                       f"/{cols[i]}{rowmap['deposits_from_customers']}")
                ws[f"{cols[i]}{r}"].number_format = "0.00"
        r += 1
    ws.column_dimensions["A"].width = 44
    for c in cols:
        ws.column_dimensions[c].width = 12


# ---------- G4 audit/14: IRR di holding period (metodo Kairos, principio n.4) ----------
HOLDING_YEARS = 3


def _irr(flows):
    """IRR per bisezione (flows[0] = entry negativa, t in anni). None se il segno
    non cambia nel range [-95%, +1000%]: mai un numero inventato."""
    if any(cf != cf for cf in flows):  # review 17/07: NaN collasserebbe la bisezione su hi
        return None
    def npv(rate):
        return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(flows))
    lo, hi = -0.95, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_lo * f_hi > 0:
        return None
    mid = (lo + hi) / 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        v = npv(mid)
        if abs(v) < 1e-9:
            break
        if (v > 0) == (f_lo > 0):
            lo, f_lo = mid, v
        else:
            hi = mid
    return round(mid, 4)


def compute_holding_irr(spec, peer_pb_median=None):
    """G4 audit/14: la domanda finale e' l'IRR di holding period, non 'FV vs prezzo'.
    Entry al prezzo di oggi -> dividendi dal ROE path DEL MODELLO (payout reale) ->
    exit a 3 anni a multiplo NORMALIZZATO: P/TBV giustificato (roe_term-g)/(ke-g)
    sul book proiettato. Seconda strada (G5): exit al P/B MEDIANO dei peer — il
    delta tra le due e' la scommessa sul re-rating, dichiarata. Tutto per-share
    nella valuta del modello (spec['currency'], gia' allineata al prezzo)."""
    price = _safe(spec.get("price"))
    bvps = _safe(spec.get("bvps"))
    ke = _safe(spec.get("ke"))
    g = _safe(spec.get("growth_lt"))
    rt = _safe(spec.get("roe_terminal"))
    roe_vec = spec.get("roe_vec") or []
    pay = _safe(spec.get("payout"), 0) or 0
    if not price or price <= 0 or not bvps or bvps <= 0:
        return {"irr": None, "note": "IRR n.d.: prezzo o book per azione mancante (dichiarato)"}
    if ke is None or g is None or ke <= g or rt is None:
        return {"irr": None, "note": "IRR n.d.: Ke <= g o ROE terminale mancante — P/TBV giustificato non definito (dichiarato)"}
    if len(roe_vec) < HOLDING_YEARS:
        return {"irr": None, "note": "IRR n.d.: ROE path piu' corto dell'holding period (dichiarato)"}
    # review G4 17/07 (ALTA, F1 finanza): CONTRATTO VALUTA entry/per-share — il prezzo
    # e' in valuta di QUOTAZIONE, dividendi/exit in valuta dei per-share (spec currency).
    # Su una banca .L sarebbe pence contro sterline: IRR falso di 100x. Conversione
    # dichiarata via _fx_pair_rate (gestisce il fattore pence); cambio n.d. = IRR n.d.
    entry = price
    fx_note = ""
    _q = ((spec.get("fx_contract") or {}).get("quote_currency"))
    _ps = spec.get("currency")
    if _q and _ps and _q != _ps:
        try:
            from bellomberg.valuation.dcf_engine import _fx_pair_rate
            _rate = _fx_pair_rate(_q, _ps)
        except Exception:
            _rate = None
        if not _rate:
            return {"irr": None, "note": ("IRR n.d.: prezzo in %s, per-share in %s e cambio "
                                          "n.d. — unita' miste rifiutate (dichiarato)" % (_q, _ps))}
        entry = price * _rate
        fx_note = " (entry %s %.2f convertito @%.6f)" % (_q, price, _rate)
    b = bvps
    divs = []
    for t in range(HOLDING_YEARS):
        roe_t = _safe(roe_vec[t], 0) or 0
        divs.append(b * roe_t * pay)
        b = b * (1.0 + roe_t * (1.0 - pay))
    ptbv_just = (rt - g) / (ke - g)
    exit_just = b * ptbv_just
    irr = _irr([-entry] + divs[:-1] + [divs[-1] + exit_just])
    note = ("entry %.2f%s -> dividendi da ROE path x payout %.0f%% -> exit Y%d a P/TBV "
            "giustificato %.2fx su book %.2f (normalizzato sul ROE TERMINALE %.1f%%: la "
            "coda del fade resta fuori dall'exit, scelta conservativa)"
            % (entry, fx_note, pay * 100, HOLDING_YEARS, ptbv_just, b, rt * 100))
    # review F5 finanza: il payout REALE include i buyback trattati qui come cassa
    _pay_src = str((spec.get("_sources") or {}).get("payout") or "")
    if _pay_src.startswith("REALE (div+buyback)"):
        note += (" | payout include BUYBACK trattati come cassa (semplificazione dichiarata; "
                 "con prezzo sopra il giustificato la stima e' ottimistica)")
    if irr is None:
        note = "IRR fuori range [-95%,+1000%] o flussi senza cambio di segno (dichiarato) | " + note
    out = {"irr": irr, "years": HOLDING_YEARS,
           "dividends_ps": [round(d, 4) for d in divs],
           "entry_price": round(entry, 4), "entry_pb": round(entry / bvps, 2),
           "exit_bvps": round(b, 2), "exit_ptbv_just": round(ptbv_just, 2),
           "exit_price_just": round(exit_just, 2),
           "note": note}
    if peer_pb_median and peer_pb_median > 0:
        out["exit_ptbv_peer"] = round(peer_pb_median, 2)
        out["irr_exit_peer"] = _irr([-entry] + divs[:-1] + [divs[-1] + b * peer_pb_median])
    else:
        out["peer_exit_note"] = "exit a P/B peer n.d. (nessuna mediana peer valida): solo strada giustificata"
    return out


# ---------- helpers Excel ----------
def H(ws, c, t, fill=NAVY):
    ws[c] = t; ws[c].font = Font(bold=True, color=WHITE, size=9); ws[c].fill = PatternFill("solid", fgColor=fill)
def L(ws, c, t, bold=False, italic=False, color=INK):
    ws[c] = t; ws[c].font = Font(bold=bold, italic=italic, color=color, size=9)
def N(ws, c, v, fmt="#,##0", bold=False, color=INK):
    ws[c] = v; ws[c].number_format = fmt; ws[c].font = Font(bold=bold, color=color, size=9)
def T(ws, c, t, size=12, color=NAVY):
    ws[c] = t; ws[c].font = Font(bold=True, color=color, size=size)


def _sheet_thesis(wb, spec, fv):
    ws = wb.create_sheet("Thesis & Assumptions", 0)
    T(ws, "A1", f"{spec['company_name']} ({spec['ticker']}) - Tesi dell'analista")
    L(ws, "A2", f"Motore BANCA v2+V4 (#203, §9-novies n.1) - {datetime.now():%d/%m/%Y %H:%M} - valuta {spec['currency']}", italic=True, color=GREYTX)
    # 16/07 (feedback PM sul file banca "non c'e' DCF, non c'e' niente"): il perche'
    # va DETTO DENTRO il file, non dedotto dal lettore.
    L(ws, "A3", "LEGGIMI: questo e' un modello BANCA - il DCF NON si applica alle banche (policy del "
                "sistema): la valutazione usa Residual Income, P/TBV GIUSTIFICATO da formula "
                "(i peer nel foglio dedicato sono il CONFRONTO di mercato) e DDM multi-stage "
                "COERENTE col RI (V4: stessi book/ROE path/payout). I fogli hanno "
                "FORMULE VIVE con i valori gia' calcolati (ricalcolo Excel post-generazione, P0 "
                "17/07): restano modificabili in Excel.",
      italic=True, color=GREYTX)
    H(ws, "A4", "VARIANT VIEW")
    L(ws, "A5", spec.get("variant_view") or "(non fornita: il modello usa fade standard - l'analista DEVE motivare il ROE path)")
    H(ws, "A7", "ASSUMPTION"); H(ws, "B7", "VALORE"); H(ws, "C7", "FONTE")
    rows = [("ROE corrente", f"{spec['roe']:.1%}", spec["_sources"].get("roe", "")),
            ("ROE terminale", f"{spec['roe_terminal']:.1%}", spec["_sources"].get("roe_path", "")),
            ("Fade (anni)", spec["fade_years"], ""),
            ("Payout", f"{spec['payout']:.1%}", spec["_sources"].get("payout", "")),
            ("Cost of Equity Ke", f"{spec['ke']:.2%}", spec["_sources"].get("ke", "")),
            ("Beta (clamped)", spec["beta"], spec["_sources"].get("beta", "")),
            ("Crescita terminale g", f"{spec['growth_lt']:.2%}", spec["_sources"].get("g", "")),
            ("Risk-free", f"{spec['rf']:.2%}", "market_inputs LIVE (FRED)")]
    r = 8
    for a, b, c in rows:
        L(ws, f"A{r}", a); L(ws, f"B{r}", str(b), bold=True); L(ws, f"C{r}", c, color=GREYTX); r += 1
    H(ws, f"A{r+1}", "FAIR VALUE (calcolo numerico, mirror delle formule)")
    r += 2
    for lab, key in [("Residual Income", "fair_value_ri"), ("P/TBV giustificato", "fair_value_ptbv"),
                     ("DDM multi-stage (coerente RI, V4)", "fair_value_ddm"), ("BLEND (mediana metodi)", "fair_value_blend")]:
        L(ws, f"A{r}", lab); N(ws, f"B{r}", fv.get(key), "#,##0.00", bold=(key == "fair_value_blend"),
                              color=GOLD if key == "fair_value_blend" else INK); r += 1
    if spec.get("price"):
        # residuo (i) §9-quinquies n.5 (23/07, opzione A PM): con valuta per-share
        # rilevata != quotazione il blend/prezzo mischierebbe le valute (caso latente,
        # oggi nessuno in book) — blend mostrato in ENTRAMBE le valute (tasso
        # dichiarato, stesso del payload) e upside sul convertito; contratto non
        # verificabile o cambio n.d. = upside n.d. DICHIARATO (regola 14/07).
        fxq = spec.get("fx_quote") or {}
        L(ws, f"A{r}", "Prezzo corrente" + (f" ({fxq['to']})" if fxq.get("to") else ""))
        N(ws, f"B{r}", spec["price"], "#,##0.00"); r += 1
        if fv.get("fair_value_blend"):
            _blend_q = fv["fair_value_blend"]
            if fxq.get("rate"):
                _blend_q = _blend_q * fxq["rate"]
                L(ws, f"A{r}", f"Blend in {fxq['to']} (convertito @{fxq['rate']:.6g}, "
                               f"src {fxq.get('src', 'yfinance')})")
                N(ws, f"B{r}", _blend_q, "#,##0.00", bold=True); r += 1
            L(ws, f"A{r}", "Upside/Downside (blend)")
            if fxq.get("error"):
                L(ws, f"B{r}", "n.d. — " + fxq["error"], color=GREYTX); r += 1
            else:
                ups = _blend_q / spec["price"] - 1
                N(ws, f"B{r}", ups, "+0.0%;-0.0%", bold=True, color=GOLD); r += 1
    # G5 audit/14: il delta tra metodi sta IN RIGA sempre, non solo nel warning >50%
    if fv.get("methods_divergence") is not None:
        L(ws, f"A{r}", "Divergenza metodi usati (max/min - 1)")
        N(ws, f"B{r}", fv["methods_divergence"], "+0.0%;-0.0%")
        L(ws, f"C{r}", "G5: dove esistono due strade, il delta si dichiara", color=GREYTX); r += 1
    # G4 audit/14 (Kairos, principio n.4): la domanda finale e' l'IRR di holding
    # period — entry oggi, dividendi del modello, exit a multipli normalizzati
    hi = fv.get("holding_irr") or {}
    r += 1
    H(ws, f"A{r}", f"IRR DI HOLDING PERIOD ({HOLDING_YEARS} ANNI) — G4 audit/14"); r += 1
    if hi.get("irr") is not None:
        # review F6 finanza: il derating sta NEL label (entry P/B vs exit P/TBV)
        L(ws, f"A{r}", f"IRR annuo (entry P/B {hi.get('entry_pb')}x -> exit P/TBV giustificato {hi.get('exit_ptbv_just')}x)")
        N(ws, f"B{r}", hi["irr"], "+0.0%;-0.0%", bold=True, color=GOLD); r += 1
        if hi.get("irr_exit_peer") is not None:
            L(ws, f"A{r}", f"IRR annuo (exit P/B mediana peer {hi.get('exit_ptbv_peer')}x)")
            N(ws, f"B{r}", hi["irr_exit_peer"], "+0.0%;-0.0%"); r += 1
            L(ws, f"A{r}", "  il delta tra le due exit e' la scommessa sul re-rating (G5)",
              italic=True, color=GREYTX); r += 1
        elif hi.get("peer_exit_note"):
            L(ws, f"A{r}", "  " + hi["peer_exit_note"], italic=True, color=GREYTX); r += 1
        L(ws, f"A{r}", "  " + str(hi.get("note") or ""), italic=True, color=GREYTX); r += 1
    else:
        _nt = str(hi.get("note") or "input mancanti (dichiarato)")
        L(ws, f"A{r}", _nt if _nt.startswith("IRR") else "IRR n.d.: " + _nt,
          italic=True, color=GREYTX); r += 1
    # V4 (§9-novies n.1): costo del rischio through-the-cycle = ANCORA DICHIARATA
    # (impairment IFRS9 / crediti, storico SEC/ESEF) — informa la view sul ROE,
    # NON aggiusta nulla in automatico (regola 14/07: il numero si argomenta).
    cor = fv.get("cost_of_risk_ttc")
    r += 1
    H(ws, f"A{r}", "COSTO DEL RISCHIO THROUGH-THE-CYCLE (V4 — ancora dichiarata, nessun aggiustamento automatico)"); r += 1
    if cor:
        L(ws, f"A{r}", "Media di ciclo %d-%d (bps su crediti clientela)" % (min(cor["years"]), max(cor["years"])))
        N(ws, f"B{r}", cor["avg_bps"], "0.0", bold=True, color=GOLD)
        L(ws, f"C{r}", "[src: %s] | %s" % (cor["source"], cor["sign_note"]), color=GREYTX); r += 1
        L(ws, f"A{r}", "Ultimo FY %d (bps)" % cor["last_year"])
        N(ws, f"B{r}", cor["last_bps"], "0.0")
        L(ws, f"C{r}", "serie per anno nel foglio Historical (banca)", color=GREYTX); r += 1
    else:
        # review V4 (A1): "assenti" era falso quando le serie esistono ma si
        # sovrappongono per <3 anni — il testo copre entrambi i casi, il dettaglio
        # vero sta nel foglio Historical
        L(ws, f"A{r}", "n.d.: serie impairment IFRS9 / crediti clientela assenti o con "
          "sovrapposizione <3 anni nello storico (buco dichiarato; v. foglio Historical)",
          italic=True, color=GREYTX); r += 1
    for w in fv.get("warnings", []):
        r += 1; L(ws, f"A{r}", "! " + w, bold=True, color="C00000")
    ws.column_dimensions["A"].width = 26; ws.column_dimensions["B"].width = 16; ws.column_dimensions["C"].width = 64


def _sheet_ri(wb, spec):
    ws = wb.create_sheet("Residual Income")
    cur = spec["currency"]
    T(ws, "A1", f"Residual Income Model - {N_YEARS} anni ({cur}m)")
    y0 = datetime.now().year
    cols = [get_column_letter(2 + i) for i in range(N_YEARS)]
    # inputs ancorati
    H(ws, "A3", "INPUT"); L(ws, "A4", "Book Value (equity)"); N(ws, "B4", spec.get("book_value"))
    L(ws, "A5", "Shares (m)"); N(ws, "B5", spec.get("shares"), "#,##0.0")
    L(ws, "A6", "Ke"); N(ws, "B6", spec["ke"], "0.00%")
    L(ws, "A7", "Payout"); N(ws, "B7", spec["payout"], "0.0%")
    L(ws, "A8", "g terminale"); N(ws, "B8", spec["growth_lt"], "0.00%")
    H(ws, "A10", "MODELLO")
    for i in range(N_YEARS):
        H(ws, f"{cols[i]}10", f"{y0 + i}E")
    labels = ["Opening Book Value", "ROE (path analista/fade)", "Net Income", "Equity Charge (Ke*BV)",
              "Residual Income", "Discount Factor", "PV Residual Income", "Retained -> Book",
              "Dividends (NI x payout)", "PV Dividends"]  # V4: righe DDM coerente
    for j, lab in enumerate(labels):
        L(ws, f"A{11 + j}", lab)
    for i in range(N_YEARS):
        c = cols[i]; p = cols[i - 1] if i > 0 else None
        ws[f"{c}11"] = "=B4" if i == 0 else f"={p}11+{p}18"
        ws[f"{c}11"].number_format = "#,##0"
        N(ws, f"{c}12", spec["roe_vec"][i], "0.0%")  # valore: path dell'analista
        ws[f"{c}13"] = f"={c}11*{c}12"; ws[f"{c}13"].number_format = "#,##0"
        ws[f"{c}14"] = f"={c}11*$B$6"; ws[f"{c}14"].number_format = "#,##0"
        ws[f"{c}15"] = f"={c}13-{c}14"; ws[f"{c}15"].number_format = "#,##0"
        ws[f"{c}16"] = f"=1/(1+$B$6)^{i + 1}"; ws[f"{c}16"].number_format = "0.000"
        ws[f"{c}17"] = f"={c}15*{c}16"; ws[f"{c}17"].number_format = "#,##0"
        ws[f"{c}18"] = f"={c}13*(1-$B$7)"; ws[f"{c}18"].number_format = "#,##0"
        # V4: DDM sugli STESSI stati del RI (book, ROE path, payout — clean surplus)
        ws[f"{c}19"] = f"={c}13*$B$7"; ws[f"{c}19"].number_format = "#,##0"
        ws[f"{c}20"] = f"={c}19*{c}16"; ws[f"{c}20"].number_format = "#,##0"
    last = cols[-1]
    L(ws, "A22", "Sum PV RI"); ws["B22"] = f"=SUM(B17:{last}17)"; ws["B22"].number_format = "#,##0"
    L(ws, "A23", "Terminal RI (Gordon)"); ws["B23"] = f"=IF($B$6>$B$8,{last}15*(1+$B$8)/($B$6-$B$8),0)"; ws["B23"].number_format = "#,##0"
    L(ws, "A24", "PV Terminal"); ws["B24"] = f"=B23*{last}16"; ws["B24"].number_format = "#,##0"
    L(ws, "A25", "EQUITY VALUE (RI)", bold=True); ws["B25"] = "=B4+B22+B24"; ws["B25"].number_format = "#,##0"
    L(ws, "A26", "FAIR VALUE / SHARE (RI)", bold=True); ws["B26"] = "=B25/B5"; ws["B26"].number_format = "#,##0.00"
    ws["B26"].font = Font(bold=True, color=GOLD, size=11)
    # V4 (§9-novies n.1): blocco DDM COERENTE — stessi stati del RI, quindi con clean
    # surplus i due metodi convergono; un delta residuo = input incoerenti (payout/g),
    # non un artefatto di metodo (la forte divergenza del caso #46 era questo).
    H(ws, "A28", "DDM MULTI-STAGE COERENTE (V4: stessi book/ROE path/payout del RI)")
    L(ws, "A29", "Sum PV Dividends"); ws["B29"] = f"=SUM(B20:{last}20)"; ws["B29"].number_format = "#,##0"
    # terminale: payout ENDOGENO 1-g/ROE (probe 21/07: Gordon su DIV_10 con payout
    # esplicito era contraddittorio con g -> forte divergenza) = Book fine Y10 x P/TBV
    # giustificato, la stessa exit dell'IRR di holding (G4)
    L(ws, "A30", "Terminal DDM = Book Y10 x (ROE-g)/(Ke-g) (payout term. endogeno 1-g/ROE)")
    ws["B30"] = f"=IF($B$6>$B$8,({last}11+{last}18)*({last}12-$B$8)/($B$6-$B$8),0)"; ws["B30"].number_format = "#,##0"
    L(ws, "A31", "PV Terminal DDM"); ws["B31"] = f"=B30*{last}16"; ws["B31"].number_format = "#,##0"
    L(ws, "A32", "EQUITY VALUE (DDM)", bold=True); ws["B32"] = "=B29+B31"; ws["B32"].number_format = "#,##0"
    L(ws, "A33", "FAIR VALUE / SHARE (DDM)", bold=True); ws["B33"] = "=B32/B5"; ws["B33"].number_format = "#,##0.00"
    ws["B33"].font = Font(bold=True, color=GOLD, size=11)
    L(ws, "A34", "Con clean surplus RI e DDM convergono: un delta residuo segnala input incoerenti, non il metodo.",
      italic=True, color=GREYTX)
    ws.column_dimensions["A"].width = 26
    for c in cols + ["B"]:
        ws.column_dimensions[c].width = 11


def _sheet_peers(wb, spec, peers_data, peers_note=None):
    ws = wb.create_sheet("Peer Comps P-TBV")
    T(ws, "A1", "Peer banche: P/TBV vs ROE (la retta del settore)")
    # 17/07 §9-sexies n.1: la LISTA e' dichiarata (geografia/analista/fallback) e ogni
    # riga porta la sua fonte; un P/B fuori banda resta VISIBILE ma senza numero in B
    # (fuori dalla MEDIAN, che ignora le celle vuote).
    if peers_note:
        L(ws, "A2", "Lista: " + str(peers_note), italic=True, color=GREYTX)
    H(ws, "A3", "Peer"); H(ws, "B3", "P/B"); H(ws, "C3", "ROE"); H(ws, "D3", "Div yield"); H(ws, "E3", "Fonte")
    r = 4
    for pd_ in peers_data or []:
        L(ws, f"A{r}", pd_.get("name", "")); N(ws, f"B{r}", pd_.get("pb"), "0.00")
        N(ws, f"C{r}", pd_.get("roe"), "0.0%"); N(ws, f"D{r}", pd_.get("dy"), "0.0%")
        L(ws, f"E{r}", pd_.get("src") or "yfinance", color=GREYTX); r += 1
    _n_pb = sum(1 for x in (peers_data or []) if x.get("pb") is not None)
    _n_roe = sum(1 for x in (peers_data or []) if x.get("roe") is not None)
    if peers_data and _n_pb:
        L(ws, f"A{r}", "MEDIANA", bold=True)
        ws[f"B{r}"] = f"=MEDIAN(B4:B{r-1})"; ws[f"B{r}"].number_format = "0.00"
        # review 17/07: MEDIAN su colonna ROE tutta vuota uscirebbe #NUM! nudo
        if _n_roe:
            ws[f"C{r}"] = f"=MEDIAN(C4:C{r-1})"; ws[f"C{r}"].number_format = "0.0%"
        else:
            L(ws, f"C{r}", "n.d.", color=GREYTX)
        r += 2
    elif peers_data:
        L(ws, f"A{r}", "MEDIANA n.d.: nessun P/B valido tra i peer (v. colonna Fonte)",
          bold=True, color="C00000")
        r += 2
    else:
        L(ws, "A4", "(peer non disponibili: v. nota Lista in alto / risultato del tool)", italic=True, color=GREYTX)
        r = 6
    if peers_data:
        L(ws, f"A{r}", f"{spec['ticker']} P/B implicito al prezzo", bold=True)
        if spec.get("price") and spec.get("bvps"):
            N(ws, f"B{r}", round(spec["price"] / spec["bvps"], 2), "0.00", bold=True, color=GOLD)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["E"].width = 72


def _sheet_sensitivity(wb, spec):
    ws = wb.create_sheet("Sensitivity")
    T(ws, "A1", "Fair value RI per Ke x ROE terminale (calcolo numerico)")
    kes = [round(spec["ke"] + d, 4) for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    rts = [round(spec["roe_terminal"] + d, 4) for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    H(ws, "A3", "Ke \\ ROE term")
    for j, rt in enumerate(rts):
        H(ws, f"{get_column_letter(2 + j)}3", f"{rt:.1%}")
    import copy
    for i, ke in enumerate(kes):
        L(ws, f"A{4 + i}", f"{ke:.2%}", bold=(ke == spec["ke"]))
        for j, rt in enumerate(rts):
            s2 = copy.deepcopy(spec)
            s2["ke"] = ke
            s2["roe_terminal"] = rt
            # fix verificatore: stesso costruttore del modello -> centro griglia = fair value headline
            s2["roe_vec"] = _build_roe_vec(spec["roe"], spec.get("agent_roe_path") or [], spec["fade_years"], rt)
            v = _fv_residual_income(s2)
            base = (i == 2 and j == 2)
            N(ws, f"{get_column_letter(2 + j)}{4 + i}", v, "#,##0.00", bold=base, color=GOLD if base else INK)
    L(ws, "A11", "Nota: griglia ricalcolata dal motore (mirror numerico delle formule), fade lineare.", italic=True, color=GREYTX)
    ws.column_dimensions["A"].width = 14


def build_bank_model(spec, output_path, peers_data=None, peers_note=None, history=None):
    """Workbook banca multi-foglio (#203). Ritorna anche i fair value numerici.
    V4 (§9-novies n.1): history = storico SEC/ESEF (contratto get_financial_history)
    -> foglio Historical (banca) + costo del rischio through-the-cycle dichiarato."""
    if not OPX_OK:
        return {"ok": False, "error": "openpyxl non disponibile"}
    fv = compute_fair_values(spec)
    # G4 audit/14: IRR di holding period nel fv (fluisce in Thesis e nel payload);
    # la mediana P/B dei peer VALIDI e' la seconda exit (G5, delta di re-rating)
    try:
        import statistics as _st
        _pbs = [_safe(x.get("pb")) for x in (peers_data or [])]
        _pbs = [x for x in _pbs if x and x > 0]
        _pb_med = _st.median(_pbs) if _pbs else None
    except Exception:
        _pb_med = None
    fv["holding_irr"] = compute_holding_irr(spec, peer_pb_median=_pb_med)
    # V4: ancora costo del rischio through-the-cycle (impairment IFRS9 / crediti).
    # SOLO dichiarata: se l'ultimo FY e' ben sotto/sopra la media di ciclo il warning
    # lo dice (il ROE trailing ne beneficia/e' depresso), ma il ROE NON viene
    # aggiustato in automatico — la view resta dell'analista (roe_path/variant view).
    cor = _cost_of_risk_ttc(history)
    fv["cost_of_risk_ttc"] = cor  # None = serie n.d., buco dichiarato nel foglio
    # review V4 (M1): serie a segni misti = convenzione non determinabile ->
    # giudizi sotto/sopra ciclo SOPPRESSI (parlerebbero al contrario), resta l'ancora
    if cor and not cor.get("mixed") and cor["avg_bps"] > 0:
        if cor["last_bps"] < 0.7 * cor["avg_bps"]:
            fv.setdefault("warnings", []).append(
                "COSTO DEL RISCHIO FY%d %.0f bps SOTTO la media di ciclo %.0f bps (%d-%d) [src: %s]: "
                "il ROE trailing ne beneficia — fade/roe_path da argomentare, nessun aggiustamento automatico (V4)."
                % (cor["last_year"], cor["last_bps"], cor["avg_bps"],
                   min(cor["years"]), max(cor["years"]), cor["source"]))
        elif cor["last_bps"] > 1.3 * cor["avg_bps"]:
            fv.setdefault("warnings", []).append(
                "COSTO DEL RISCHIO FY%d %.0f bps SOPRA la media di ciclo %.0f bps (%d-%d) [src: %s]: "
                "ROE trailing depresso rispetto al ciclo — un fade puramente meccanico puo' sottostimare (V4)."
                % (cor["last_year"], cor["last_bps"], cor["avg_bps"],
                   min(cor["years"]), max(cor["years"]), cor["source"]))
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _sheet_thesis(wb, spec, fv)
    _sheet_ri(wb, spec)
    _sheet_peers(wb, spec, peers_data, peers_note=peers_note)
    _sheet_historical_bank(wb, spec, history, cor=cor)  # V4
    _sheet_sensitivity(wb, spec)
    # P0 17/07: cintura — se il bake COM di dcf_engine fallisse, Excel ricalcola
    # comunque all'apertura (openpyxl non scrive i valori cached delle formule)
    wb.calculation.fullCalcOnLoad = True
    try:
        wb.save(output_path)
    except Exception as e:
        return {"ok": False, "error": f"save: {e}", **fv}
    out = {"ok": True, "path": output_path, "engine": "bank",
           # audit/12 V0.1: la valuta VERA del payload viaggia col risultato, cosi'
           # _convert_fx_to_quote non riconverte fair value gia' in valuta di quotazione
           "payload_currency": spec.get("currency"),
           "fx_contract": spec.get("fx_contract"),
           "method": "residual income 10y (ROE path analista) + P/TBV + DDM multi-stage coerente (V4)",
           # §9-sexies n.1: peer effettivi e nota di selezione/sanity nel payload
           "peers_used": [x.get("name") for x in (peers_data or [])],
           "peer_note": peers_note,
           "assumptions": {"roe_path": spec["roe_vec"][:5], "roe_terminal": spec["roe_terminal"],
                           "fade_years": spec["fade_years"], "payout": spec["payout"],
                           "ke": spec["ke"], "g": spec["growth_lt"],
                           "sources": spec["_sources"]},
           "_timestamp": datetime.now().isoformat()}
    out.update(fv)
    return out


def bank_peer_comps(peers: List[str], exclude: str, pb_band=None, exclude_name=None):
    """P/B, ROE, div yield dei peer bancari via yfinance (best effort, online).
    Ritorna (righe, nota). Fix 17/07 §9-sexies n.1 (HSBC P/B 7,9 nel foglio della banca cross-listed):
      - P/B = campo priceToBook Yahoo (il ricalcolo prezzo/bookValue si romperebbe
        sui .L in pence, ratio 100x). Sugli ADR pero' il campo stesso puo' inglobare
        il rapporto ADS/ordinarie (HSBC 7,9 = 5 x 1,58) e NESSUN incrocio di campi
        lo smaschera — anche marketCap/(bookValue x shares) da' 7,9 (misurato 17/07)
        -> guardia = banda di plausibilita' del PROFILO (pb_band): fuori banda il
        P/B esce dalla mediana ma la RIGA resta nel foglio col motivo in Fonte
        (niente scarti silenziosi, regola 14/07).
      - dividendYield yfinance e' in unita' PERCENTO (misurato 17/07 su 25/25
        ticker: JPM 1,73 = 1,73%) -> riscalato /100 e dichiarato in nota; oltre il
        25% post-riscala resta implausibile -> n.d. dichiarato sulla riga."""
    rows: List[Dict[str, Any]] = []
    try:
        import yfinance as yf
    except ImportError:
        return rows, "yfinance non disponibile: peer comps banca SALTATI (dichiarato)"
    # review 17/07 (2 agenti, finding convergente): dedup EMITTENTE sui cross-listing
    # — l'exclude a ticker esatto non basta quando il soggetto e' un ADR e la lista
    # geo usa i listing locali (HSBC vs HSBA.L, SAN vs SAN.MC, DB vs DBK.DE...).
    # Stessa _norm_issuer del canale operating (audit/12 V0.6).
    # exclude_name: stringa o lista di nomi candidati del soggetto. Si confrontano
    # TUTTI i nomi normalizzati di entrambi i lati: lo shortName dei .L e' sporcato
    # dalla convenzione LSE ("HSBC HOLDINGS PLC ORD $0.50 (UK"), il longName no.
    _norm = None
    _ex_names = set()
    if exclude_name:
        try:
            from bellomberg.valuation.peer_comps import _norm_issuer as _norm
            _cand = exclude_name if isinstance(exclude_name, (list, tuple, set)) else [exclude_name]
            _ex_names = {_norm(x) for x in _cand if x} - {""}
        except Exception:
            _norm, _ex_names = None, set()
    # review 17/07 F2: il cap a 6 non deve essere uno scarto silenzioso (regola 14/07)
    _lst = [p for p in (peers or []) if p.upper() != exclude.upper()]
    _cut = len(_lst) - 6 if len(_lst) > 6 else 0
    for p in _lst[:6]:
        try:
            inf = yf.Ticker(p).info or {}
        except Exception as e:
            rows.append({"name": p.upper(), "pb": None, "roe": None, "dy": None,
                         "src": "yfinance KO (%s): dati n.d." % type(e).__name__})
            continue
        if _norm and _ex_names:
            _peer_names = {_norm(inf.get("longName")), _norm(inf.get("shortName"))} - {""}
            if _ex_names & _peer_names:
                rows.append({"name": p.upper(), "pb": None, "roe": None, "dy": None,
                             "src": "STESSO EMITTENTE del soggetto (cross-listing): escluso dai peer"})
                continue
        pb = _safe(inf.get("priceToBook"))
        src = "yfinance"
        if pb is not None and pb_band and not (pb_band[0] <= pb <= pb_band[1]):
            src = ("P/B %.2f FUORI banda profilo [%.2f-%.2f]: ESCLUSO dalla mediana — "
                   "dato sospetto (es. rapporto ADR) O multiplo fuori range del profilo"
                   % (pb, pb_band[0], pb_band[1]))
            pb = None
        dy = _safe(inf.get("dividendYield"))
        if dy is not None:
            dy = dy / 100.0  # unita' percento Yahoo (misura 17/07), dichiarato in nota
            if dy > 0.25:
                src += " | div yield %.0f%% implausibile: n.d." % (dy * 100)
                dy = None
        rows.append({"name": p.upper(), "pb": pb, "roe": _safe(inf.get("returnOnEquity")),
                     "dy": dy, "src": src})
    note = ("%d peer, %d con P/B valido | banda P/B profilo %s | div yield riscalato "
            "da unita' percento Yahoo (misura 17/07)"
            % (len(rows), sum(1 for x in rows if x.get("pb") is not None),
               ("[%.2f-%.2f]" % tuple(pb_band)) if pb_band else "ASSENTE (profilo senza banda)"))
    if _cut:
        note += " | lista TRONCATA alle prime 6 (%d richiesti)" % len(_lst)
    return rows, note


if __name__ == "__main__":
    import json
    # mock di banca cross-listed (il caso che ha rotto il v1)
    info = {"longName": "Banca Sintetica", "financialCurrency": "USD", "country": "Japan",
            "sector": "Financial Services", "sharesOutstanding": 10_000_000_000, "bookValue": 12.0,
            "returnOnEquity": 0.12, "payoutRatio": 0.20, "trailingEps": 1.30,
            "currentPrice": 20.0, "beta": 0.90}
    wi = {"rf": 0.02515, "erp": 0.05, "crp": 0}
    spec = build_bank_spec("BANK.TEST", info, wi, variant_view="test: banca centrale normalizza, ROE 12% durevole")
    print(json.dumps({k: v for k, v in spec.items() if k != "roe_vec"}, indent=1, ensure_ascii=False))
    print("roe_vec:", spec["roe_vec"])
    r = build_bank_model(spec, "/tmp/BANK_TEST_v2.xlsx")
    print(json.dumps({k: v for k, v in r.items() if k not in ("assumptions",)}, indent=1, ensure_ascii=False))
