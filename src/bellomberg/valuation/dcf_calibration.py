"""
dcf_calibration.py - Calibrazione DINAMICA del modello DCF (#165).

build_spec_from_ticker(ticker) costruisce lo 'spec' per il motore DCF (oggi dcf_buyside_v3;
il v2 dcf_buyside.py sta in attic/morti_20260902 dal 02/09: 0 importer misurati)
popolandolo con DATI REALI e ricalibrando in base a SETTORE e GEOGRAFIA:

  - FONDAMENTALI (yfinance): revenue history 4y, margini reali, shares, net debt,
    settore, paese, valuta. Valuta dichiarata (i valori restano nella valuta di
    reporting della societa', coerente con se stessa - niente mix).
  - SETTORE -> motore (operativa vs banca) + assumptions tipiche (software cresce
    13% gm70%, industriale cresce 4% gm25%, ...) usate come PRIOR, poi blendate
    con i margini storici reali.
  - GEOGRAFIA/VALUTA -> WACC (audit/13 V1): risk-free per VALUTA DEI FLUSSI
    (fonti daily con staleness dichiarata), CRP del paese dal dataset Damodaran
    versionato, ERP implied versionato, tax marginale del paese, D/E target
    dall'industry Damodaran del profilo. Ogni input porta fonte+data in
    wacc_inputs['sources'] fino al foglio WACC.

Robusto: ogni campo guarded; se un dato manca usa il prior di settore DICHIARANDOLO.
Mai eccezioni.
"""
from typing import Dict, Any, Optional, List

# ---- Prior di SETTORE (growth/margini tipici + beta unlevered Damodaran-style) ----
SECTOR_PRIORS = {
    "technology":   {"growth": 0.13, "gm": 0.68, "ebitda_m": 0.30, "beta_u": 0.95, "engine": "operating"},
    "software":     {"growth": 0.14, "gm": 0.72, "ebitda_m": 0.32, "beta_u": 0.95, "engine": "operating"},
    "communication":{"growth": 0.07, "gm": 0.55, "ebitda_m": 0.28, "beta_u": 0.90, "engine": "operating"},
    "healthcare":   {"growth": 0.08, "gm": 0.60, "ebitda_m": 0.25, "beta_u": 0.85, "engine": "operating"},
    "consumer cyclical":{"growth": 0.05, "gm": 0.38, "ebitda_m": 0.15, "beta_u": 1.05, "engine": "operating"},
    "consumer defensive":{"growth": 0.04, "gm": 0.35, "ebitda_m": 0.16, "beta_u": 0.70, "engine": "operating"},
    "industrials":  {"growth": 0.05, "gm": 0.28, "ebitda_m": 0.15, "beta_u": 1.00, "engine": "operating"},
    "energy":       {"growth": 0.03, "gm": 0.30, "ebitda_m": 0.25, "beta_u": 1.10, "engine": "operating"},
    "basic materials":{"growth": 0.04, "gm": 0.25, "ebitda_m": 0.18, "beta_u": 1.05, "engine": "operating"},
    "utilities":    {"growth": 0.03, "gm": 0.40, "ebitda_m": 0.35, "beta_u": 0.55, "engine": "operating"},
    "real estate":  {"growth": 0.04, "gm": 0.55, "ebitda_m": 0.45, "beta_u": 0.80, "engine": "operating"},
    "financial services":{"growth": 0.05, "gm": 0.0, "ebitda_m": 0.0, "beta_u": 1.10, "engine": "bank"},
    "financial":    {"growth": 0.05, "gm": 0.0, "ebitda_m": 0.0, "beta_u": 1.10, "engine": "bank"},
}
DEFAULT_PRIOR = {"growth": 0.06, "gm": 0.40, "ebitda_m": 0.18, "beta_u": 1.00, "engine": "operating"}

# audit/13 V1.3 (ok PM 16/07): la vecchia tabella COUNTRY_WACC (11 paesi statici,
# DEFAULT_COUNTRY silenzioso su Danimarca/Lussemburgo/...) e' stata SOSTITUITA dal
# dataset Damodaran versionato (damodaran_snapshot.json, ~157 paesi). Paese non
# mappato = CRP 0 con WARN DICHIARATO che arriva fino al foglio, mai 1% zitto.


def _prior_for_sector(sector: str) -> Dict[str, Any]:
    s = (sector or "").lower().strip()
    for k, v in SECTOR_PRIORS.items():
        if k in s:
            return v
    return DEFAULT_PRIOR


def _country_wacc(country: str, flow_currency: str = None) -> Dict[str, Any]:
    """Input geografici del WACC, con FONTE E DATA di ognuno (audit/13 V1.2/V1.3).

    - rf: per VALUTA DEI FLUSSI SCONTATI (flow_currency), fonti daily con staleness
      dichiarata (market_inputs.get_risk_free_ex) — non piu' per paese sede;
    - crp: dal dataset Damodaran completo; paese non mappato -> 0 con WARN dichiarato;
    - erp: implied Damodaran versionato (get_erp_ex).
    Ritorna anche rf_source/crp_source/erp_source/rf_stale: il chiamante li propaga.
    """
    cur = (flow_currency or "USD").upper()
    base = {"currency": cur}
    try:
        from bellomberg.market_data.market_inputs import get_risk_free_ex, get_erp_ex
        rfx = get_risk_free_ex(cur)
        base["rf"] = rfx["value"]
        base["rf_source"] = "%s | oss. %s%s" % (rfx["source"], rfx.get("obs_date") or "n.d.",
                                                " | STALE" if rfx.get("stale") else "")
        base["rf_stale"] = bool(rfx.get("stale"))
        erx = get_erp_ex()
        base["erp"] = erx["value"]
        base["erp_source"] = erx["source"] + (" | STALE" if erx.get("stale") else "")
    except Exception as e:
        # ultima rete (import rotto): statici DICHIARATI come tali
        base["rf"] = {"USD": 0.045, "EUR": 0.031, "GBP": 0.042, "JPY": 0.011}.get(cur, 0.04)
        base["rf_source"] = "static hardcoded (canale market_inputs KO: %s)" % e
        base["rf_stale"] = True
        base["erp"] = 0.05
        base["erp_source"] = "default hardcoded 5% (canale market_inputs KO)"
    try:
        from bellomberg.market_data.damodaran_data import get_crp
        c = get_crp(country)
    except Exception:
        c = None
    if c is not None:
        base["crp"] = c["value"]
        base["crp_source"] = c["source"] + (" | STALE" if c.get("stale") else "")
    else:
        base["crp"] = 0.0
        base["crp_source"] = ("WARN: paese '%s' non mappato nel dataset CRP Damodaran: "
                              "CRP n.d. -> usato 0 DICHIARATO" % (country or "n.d."))
    return base


def _wacc_inputs_v1(cw: Dict[str, Any], prior: Dict[str, Any], profile,
                    country: str) -> Dict[str, Any]:
    """Assemblaggio wacc_inputs (audit/13 V1.5d/V1.7): niente piu' 0.05/0.30/0.25
    fissi — ERP implied versionato, tax marginale del PAESE, D/E target dall'industry
    Damodaran del profilo. Ogni valore ha la sua riga in 'sources' (arriva al foglio).
    """
    sources = {"rf": cw.get("rf_source", "n.d."), "erp": cw.get("erp_source", "n.d."),
               "crp": cw.get("crp_source", "n.d.")}
    # tax: marginale del paese (re-lever e scudo fiscale); l'EFFETTIVA dei bilanci
    # entra nel P&L con V2.3. Fallback 25% SOLO etichettato.
    tax = 0.25
    try:
        from bellomberg.market_data.damodaran_data import get_tax_marginal
        t = get_tax_marginal(country)
    except Exception:
        t = None
    if t is not None:
        tax = t["value"]
        sources["tax"] = t["source"]
    else:
        sources["tax"] = ("tax 25%% DEFAULT (paese '%s' non nel dataset marginali): "
                          "dichiarato" % (country or "n.d."))
    # D/E target: market D/E lease-adjusted dell'industry Damodaran del profilo,
    # regione della societa'. Senza profilo/ancora -> 0.30 etichettato proxy.
    de, de_src = 0.30, "D/E 0.30 PROXY (nessun profilo/ancora Damodaran): dichiarato"
    dam_ind = (profile or {}).get("dam_industry")
    if dam_ind:
        try:
            from bellomberg.market_data.damodaran_data import get_industry_field, region_for_country
            reg = region_for_country(country)
            d = get_industry_field(dam_ind, "de", reg)
        except Exception:
            d = None
        if d is not None:
            de = d["value"]
            de_src = "D/E target %s" % d["source"] + \
                     ((" | " + d["note"]) if d.get("note") else "")
        else:
            de_src = ("D/E 0.30 PROXY (industry '%s' senza D/E nel dataset): "
                      "dichiarato" % dam_ind)
    sources["de"] = de_src
    sources["kd"] = "kd pre-rating rf+200bp: a valle lo sostituisce il synthetic rating"
    sources["beta_u"] = "prior macro-settore (a valle: prior Damodaran + shrinkage, V1.4)"
    return {"rf": cw["rf"], "erp": cw["erp"], "crp": cw["crp"],
            "beta_u": prior["beta_u"], "de": round(de, 4), "tax": round(tax, 4),
            "kd": cw["rf"] + 0.02, "sources": sources}


def _safe(v, default=None):
    try:
        f = float(v)
        import math
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _revenue_history(tk) -> Optional[List[float]]:
    """Estrae 4 anni di Revenue dal income statement yfinance (in milioni)."""
    try:
        fin = tk.income_stmt  # DataFrame, colonne = anni (recente -> vecchio)
        if fin is None or fin.empty:
            return None
        for key in ("Total Revenue", "TotalRevenue", "Operating Revenue"):
            if key in fin.index:
                row = fin.loc[key].dropna()
                vals = [float(x) / 1e6 for x in row.values][:4]  # in milioni
                vals.reverse()  # vecchio -> recente
                return vals if len(vals) >= 2 else None
    except Exception:
        return None
    return None


def _real_driver_ratios(tk) -> Dict[str, Any]:
    """audit/13 V2.3: rapporti REALI dai bilanci yfinance (fino a 4 anni) —
    D&A/ricavi, capex/ricavi, tax rate effettiva. Mediane clampate a bound di
    plausibilita'; voce assente = None (buco dichiarato dal chiamante)."""
    out = {"da_med": None, "capex_med": None, "tax_med": None, "n_years": 0}
    try:
        fin = tk.income_stmt
        cf = tk.cashflow
        if fin is None or fin.empty or "Total Revenue" not in fin.index:
            return out
        rev = fin.loc["Total Revenue"].dropna()

        def _ratios(df, keys, lo, hi, absval=True):
            if df is None or getattr(df, "empty", True):
                return None
            for k in keys:
                if k in df.index:
                    row = df.loc[k].dropna()
                    vals = []
                    for col in row.index:
                        if col in rev.index and rev[col]:
                            r = float(row[col]) / float(rev[col])
                            if absval:
                                r = abs(r)
                            if lo <= r <= hi:
                                vals.append(r)
                    if vals:
                        vals.sort()
                        m = len(vals)
                        med = vals[m // 2] if m % 2 else (vals[m // 2 - 1] + vals[m // 2]) / 2
                        return round(med, 4), len(vals)
            return None

        da = _ratios(cf, ["Depreciation And Amortization", "Depreciation Amortization Depletion",
                          "Depreciation"], 0.002, 0.30)
        cap = _ratios(cf, ["Capital Expenditure", "Purchase Of PPE"], 0.002, 0.35)
        if da:
            out["da_med"], out["n_years"] = da[0], max(out["n_years"], da[1])
        if cap:
            out["capex_med"], out["n_years"] = cap[0], max(out["n_years"], cap[1])
        # tax effettiva: Tax Provision / Pretax Income per anno, clamp [10%, 35%]
        if "Tax Provision" in fin.index and "Pretax Income" in fin.index:
            tp, pi = fin.loc["Tax Provision"].dropna(), fin.loc["Pretax Income"].dropna()
            vals = []
            for col in tp.index:
                if col in pi.index and pi[col] and float(pi[col]) > 0:
                    t = float(tp[col]) / float(pi[col])
                    if 0.0 <= t <= 0.55:
                        vals.append(min(0.35, max(0.10, t)))
            if vals:
                vals.sort()
                m = len(vals)
                out["tax_med"] = round(vals[m // 2] if m % 2
                                       else (vals[m // 2 - 1] + vals[m // 2]) / 2, 4)
    except Exception:
        pass
    return out


def _margin(tk, num_key, rev_key="Total Revenue") -> Optional[float]:
    try:
        fin = tk.income_stmt
        if num_key in fin.index and rev_key in fin.index:
            num = float(fin.loc[num_key].dropna().iloc[0])
            rev = float(fin.loc[rev_key].dropna().iloc[0])
            return num / rev if rev else None
    except Exception:
        return None
    return None


# ---- V6 Lotto 2 (audit/19 V6.2-V6.3, decisioni PM D1-D4 23/07): layer GUIDANCE ----
# La guidance vive nel registro company_guidance (memory_db, Lotto 1); QUI arriva
# gia' letta (il chiamante dcf_engine inietta il payload di get_guidance): la
# calibrazione non apre il DB — helper PURI, testabili offline con date esplicite.

def _guidance_rows(guidance, metric):
    """Righe ATTIVE e NON-stale di una metrica dal payload di get_guidance.
    La staleness e' gia' calcolata in lettura dal registro (D4): qui si FILTRA
    e basta — una guidance scaduta non guida mai i default (regola 14/07)."""
    if not isinstance(guidance, dict):
        return []
    return [r for r in (guidance.get("active") or [])
            if isinstance(r, dict) and r.get("metric") == metric and not r.get("stale")]


def _guidance_src(row):
    """Riferimento fonte compatto '[doc, data]' — la fonte e' OBBLIGATORIA nel
    registro (D1), quindi qui c'e' sempre."""
    return "[%s, %s]" % (row.get("source_doc"), row.get("source_date"))


def _guidance_expiry_warn(row, today):
    """V6.4: WARN dichiarato se la guidance consumata scade entro 7 giorni."""
    vu = row.get("valid_until")
    if not vu:
        return None
    try:
        from datetime import date
        days = (date.fromisoformat(str(vu)[:10]) - today).days
    except (TypeError, ValueError):
        return None
    if 0 <= days <= 7:
        return ("ATTENZIONE: guidance %s %s scade il %s (entro 7 giorni) — "
                "aggiornarla dalla prossima trimestrale" % (row.get("metric"), row.get("period"), vu))
    return None


def _guidance_slim(row, driver, note=None):
    """Riga consumata come esce nel payload guidance_used: fonte e vintage SEMPRE."""
    out = {k: row.get(k) for k in ("id", "ticker", "metric", "period", "value_low",
                                   "value_mid", "value_high", "unit", "source_doc",
                                   "source_date", "valid_until")}
    out["driver"] = driver
    if note:
        out["note"] = note
    return out


def _consume_guidance_growth(guidance, last_rev, rep_cur, pg, terminal_g, gf, gc, today=None):
    """Layer 2 della gerarchia growth (D2: la guidance e' il DEFAULT del base,
    etichettato e sovrascrivibile dall'analista): anno 1 (e anno 2 se un FY
    consecutivo lo copre) = mid della guidance, poi la STESSA coda convergente
    del blend (fade verso il prior, Y5 a meta' strada verso il terminale).
    Solo period FY (H/Q restano nel registro, non consumati nel growth annuale:
    dichiarato). revenue_abs (unit fissa musd) consumata SOLO con flussi in USD
    e storico ricavi VERO: mismatch = skip DICHIARATO, mai una conversione FX
    zitta. Ritorna None se non c'e' nulla di consumabile."""
    import re as _re
    from datetime import date
    today = today or date.today()
    _fy = _re.compile(r"^FY(\d{4})$")
    by_year, skips = {}, []
    for r in _guidance_rows(guidance, "revenue_growth") + _guidance_rows(guidance, "revenue_abs"):
        m = _fy.match(str(r.get("period") or ""))
        if not m:
            skips.append("%s %s: period non-FY, non consumato nel growth annuale"
                         % (r.get("metric"), r.get("period")))
            continue
        yr = int(m.group(1))
        if yr < today.year:
            skips.append("%s FY%d: esercizio passato, non guida il forward"
                         % (r.get("metric"), yr))
            continue
        if r.get("metric") == "revenue_abs":
            if str(rep_cur or "").upper() != "USD" or not last_rev:
                skips.append("revenue_abs FY%d in musd ma flussi in %s%s: NON convertita "
                             "(niente cambio zitto) — registrare revenue_growth"
                             % (yr, rep_cur, "" if last_rev else " e storico ricavi proxy"))
                continue
            conv = {}
            for k in ("value_low", "value_mid", "value_high"):
                v = r.get(k)
                conv[k] = round(v / last_rev - 1.0, 4) if v is not None else None
            if conv["value_mid"] is None:
                continue
            note = ("revenue_abs %s musd -> growth %+.1f%% sui ricavi reali %s musd (conversione dichiarata)"
                    % (r.get("value_mid"), conv["value_mid"] * 100, round(last_rev, 1)))
            cand = dict(r, _g_low=conv["value_low"], _g_mid=conv["value_mid"],
                        _g_high=conv["value_high"], _conv_note=note)
        else:
            cand = dict(r, _g_low=r.get("value_low"), _g_mid=r.get("value_mid"),
                        _g_high=r.get("value_high"), _conv_note=None)
        # a parita' di anno vince revenue_growth (dato diretto, niente conversione)
        if yr in by_year and by_year[yr].get("metric") == "revenue_growth":
            # review L2 (BASSA-2): la perdente si dichiara, costo zero
            skips.append("revenue_abs FY%d: presente anche revenue_growth (dato "
                         "diretto), usata quella" % yr)
            continue
        by_year[yr] = cand
    if not by_year:
        # regola 14/07: guidance revenue PRESENTE ma non consumabile (period
        # non-FY, valuta, esercizio passato) = buco DICHIARATO al chiamante,
        # che lo appende a _growth_source — mai sparita in silenzio
        return {"growth_fwd": None, "skips": skips} if skips else None
    years = sorted(by_year)
    y1 = by_year[years[0]]
    y2 = by_year[years[1]] if (len(years) > 1 and years[1] == years[0] + 1) else None
    # review L2 (MEDIA-3): righe FY valide ma oltre l'orizzonte anno-2 o non
    # consecutive (es. piano industriale con target FY2028) NON spariscono zitte
    _consumed = {years[0]} | ({years[1]} if y2 is not None else set())
    for _yr in years:
        if _yr not in _consumed:
            skips.append("%s FY%d: oltre l'orizzonte anno-2 o non consecutiva, "
                         "NON consumata" % (by_year[_yr].get("metric"), _yr))
    clamp_notes = []

    def _cl(v, tag):
        # la guidance NON si clampa al floor/cap del profilo (collaudo live su un farmaceutico:
        # -8% guidato sarebbe diventato -5% zitto il floor, contro audit/19 "anno
        # 1 = mid della guidance"): la banda di plausibilita' l'ha gia' validata
        # il registro; fuori banda profilo = DICHIARATO, come l'analista che non
        # viene clampato (gerarchia: la societa' conosce il proprio anno 1)
        v = float(v)
        if not (gf <= v <= gc):
            clamp_notes.append("%s %+.1f%% FUORI dal floor/cap del profilo "
                               "[%.0f%%, %.0f%%]: si usa comunque la GUIDANCE "
                               "(banda registro gia' validata), dichiarato"
                               % (tag, v * 100, gf * 100, gc * 100))
        return v
    g1 = _cl(y1["_g_mid"], "anno 1")
    if y2 is not None:
        if y2.get("metric") == "revenue_abs":
            # review L2 (ALTA-1): la conversione v/last_rev-1 e' growth CUMULATO
            # sulla base storica — l'anno 2 vuole il growth ANNUALE sopra l'anno 1,
            # altrimenti i ricavi modellati sfondano la guidance etichettata
            for _k in ("_g_low", "_g_mid", "_g_high"):
                if y2.get(_k) is not None:
                    y2[_k] = round((1.0 + y2[_k]) / (1.0 + g1) - 1.0, 4)
            y2["_conv_note"] = ((y2.get("_conv_note") or "") +
                                " | anno 2: growth ANNUALE ricalcolato sopra l'anno 1 "
                                "(%+.1f%%)" % (y2["_g_mid"] * 100))
        g2 = _cl(y2["_g_mid"], "anno 2")
        path = [round(g1, 3), round(g2, 3), round(g2 * 0.5 + pg * 0.5, 3),
                round(pg, 3), round((pg + terminal_g) / 2.0, 3)]
        fade_from = "anni 3+"
    else:
        path = [round(g1, 3), round(g1 * 0.75 + pg * 0.25, 3), round(g1 * 0.5 + pg * 0.5, 3),
                round(pg, 3), round((pg + terminal_g) / 2.0, 3)]
        fade_from = "anni 2+"
    rng = ""
    if y1.get("_g_low") is not None or y1.get("_g_high") is not None:
        rng = " (range %s-%s)" % tuple("%.1f%%" % (v * 100) if v is not None else "n.d."
                                       for v in (y1.get("_g_low"), y1.get("_g_high")))
    label = "GUIDANCE societaria %s revenue %+.1f%%%s %s" % (
        y1.get("period"), y1["_g_mid"] * 100, rng, _guidance_src(y1))
    if y2 is not None:
        label += " / %s %+.1f%% %s" % (y2.get("period"), y2["_g_mid"] * 100, _guidance_src(y2))
    label += " — %s fade verso prior %.1f%%" % (fade_from, pg * 100)
    for n in clamp_notes:
        label += " | " + n
    used = [_guidance_slim(y1, "revenue_growth anno 1", note=y1.get("_conv_note"))]
    warns = [w for w in (_guidance_expiry_warn(y1, today),) if w]
    if y2 is not None:
        used.append(_guidance_slim(y2, "revenue_growth anno 2", note=y2.get("_conv_note")))
        w2 = _guidance_expiry_warn(y2, today)
        if w2:
            warns.append(w2)
    for w in warns:
        label += " | " + w
    if skips:
        # review L2 (MEDIA-3): le righe perse si dichiarano NELL'etichetta, qui
        # (un solo punto di verita'), non nel chiamante
        label += " | altre righe NON consumate (dichiarato): " + "; ".join(skips)
    range_y1 = None
    if y1.get("_g_low") is not None or y1.get("_g_high") is not None:
        # V6.3 (D3): il range anno-1 alimenta bear/bull in _default_scenarios
        # (dcf_buyside_v3) col floor B14 — passa SOLO quando la guidance ha
        # guidato il base (questo ramo), mai sopra un growth_path dell'analista
        range_y1 = {"low": y1.get("_g_low"), "high": y1.get("_g_high"),
                    "mid": round(g1, 4), "period": y1.get("period"),
                    "src": "%s, %s" % (y1.get("source_doc"), y1.get("source_date"))}
    return {"growth_fwd": path, "source": label, "used": used,
            "range_y1": range_y1, "skips": skips}


def _compare_override_vs_guidance(guidance, override_y1, last_rev, rep_cur, pg,
                                  terminal_g, gf, gc, today=None):
    """V6.5 (Lotto 3): confronto DICHIARATO analista-vs-guidance quando il
    growth_override vince la gerarchia. Ritorna (suffisso_etichetta | None,
    deviazione_pp | None). Estratto da build_spec_from_ticker per testabilita'
    offline (review L3 B3). Niente buchi zitti neanche qui (review L3 MEDIA-1):
    guidance presente ma NON confrontabile = dichiarata in etichetta; guidance
    in scadenza entro 7g = WARN visibile anche a chi la sta scavalcando (V6.4)."""
    from datetime import date
    _gg = _consume_guidance_growth(guidance, last_rev=last_rev, rep_cur=rep_cur,
                                   pg=pg, terminal_g=terminal_g, gf=gf, gc=gc,
                                   today=today)
    if not _gg:
        return None, None
    if not _gg.get("growth_fwd"):
        return (" | GUIDANCE nel registro NON confrontabile col path analista "
                "(dichiarato): " + "; ".join(_gg.get("skips") or []), None)
    g1 = _gg["growth_fwd"][0]
    u0 = _gg["used"][0]
    dev = round((float(override_y1) - g1) * 100, 1)
    suff = (" | GUIDANCE attiva %s revenue %+.1f%% %s: il path dell'analista "
            "anno-1 %+.1f%% devia %+.1fpp (V6.5, solo confronto: vince l'analista)"
            % (u0.get("period"), g1 * 100, _guidance_src(u0),
               float(override_y1) * 100, dev))
    w = _guidance_expiry_warn(u0, today or date.today())
    if w:
        suff += " | " + w
    return suff, dev


def _consume_guidance_driver(guidance, metric, today=None):
    """Guidance di margine/capex sul driver corrispondente (V6.2): mid della riga
    FY piu' vicina (>= anno corrente), etichettata con fonte. Il range dei margini
    NON alimenta gli scenari in v1 (dichiarato in audit/19). Ritorna None se non
    c'e' NULLA per la metrica; righe presenti ma non consumabili (period non-FY,
    esercizio passato) = {"mid": None, "skip_note": ...} — il chiamante DICHIARA
    (review L2 MEDIA-2, regola 14/07)."""
    import re as _re
    from datetime import date
    today = today or date.today()
    _fy = _re.compile(r"^FY(\d{4})$")
    best, best_yr, skips = None, None, []
    for r in _guidance_rows(guidance, metric):
        m = _fy.match(str(r.get("period") or ""))
        if not m:
            skips.append("%s %s: period non-FY, non consumato (v1 annuale)"
                         % (metric, r.get("period")))
            continue
        yr = int(m.group(1))
        if yr < today.year:
            skips.append("%s FY%d: esercizio passato" % (metric, yr))
            continue
        if best is None or yr < best_yr:
            best, best_yr = r, yr
    if best is None or best.get("value_mid") is None:
        return {"mid": None, "skip_note": "; ".join(skips)} if skips else None
    label = "GUIDANCE societaria %s %s = %.1f%% %s" % (
        best.get("period"), metric, float(best["value_mid"]) * 100, _guidance_src(best))
    w = _guidance_expiry_warn(best, today)
    if w:
        label += " | " + w
    return {"mid": float(best["value_mid"]), "label": label,
            "used": _guidance_slim(best, "driver %s" % metric)}


def build_spec_from_ticker(ticker: str, growth_override=None, variant_view=None,
                          ebitda_margin_target=None, terminal_growth=None,
                          profile=None, guidance=None) -> Dict[str, Any]:
    """Costruisce lo spec calibrato. Ritorna anche '_calibration' con le scelte fatte.
    profile (audit/13 V1): il profilo sub-settore di sector_taxonomy.classify —
    porta dam_industry (ancora Damodaran) per D/E target e, in V2, i driver."""
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance non disponibile"}
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as e:
        return {"error": f"yfinance ticker: {e}"}

    sector = info.get("sector") or info.get("industry") or ""
    country = info.get("country") or ""
    name = info.get("longName") or info.get("shortName") or ticker
    # audit/13 V1.2: la valuta del modello = valuta dei FLUSSI (financialCurrency).
    # Se manca, si usa la valuta di QUOTAZIONE con nota dichiarata (i per-share sono
    # coerenti con essa); se mancano ENTRAMBE -> rifiuto (mai un default zitto).
    rep_cur = info.get("financialCurrency")
    _cur_note = None
    if not rep_cur:
        rep_cur = info.get("currency")
        if rep_cur:
            _cur_note = ("financialCurrency n.d. su Yahoo: usata la valuta di "
                         "quotazione %s (DICHIARATO)" % rep_cur)
        else:
            return {"error": f"{ticker}: yfinance senza financialCurrency NE' currency: "
                             "valuta dei flussi indeterminabile, DCF rifiutato "
                             "(audit/13 V1.2 — mai valuta di default zitta)"}

    prior = _prior_for_sector(sector)
    # audit/13 V2.1: i prior granulari del PROFILO sub-settore (finora dati morti in
    # tassonomia) sostituiscono il macro-settore quando presenti; il macro resta come
    # fallback dichiarato per il profilo default.
    _prior_src = "macro-settore"
    if profile:
        _over = {k: profile[k] for k in ("growth", "gm", "ebitda_m", "beta_u", "engine")
                 if profile.get(k) is not None}
        if _over:
            prior = {**prior, **_over}
            _prior_src = "profilo sub-settore '%s'" % (profile.get("_profile_key") or "?")
    cw = _country_wacc(country, flow_currency=rep_cur)
    engine = prior["engine"]

    # fondamentali reali — MAI inventare cifre: senza storia ne' totalRevenue il DCF si rifiuta
    rev_hist = _revenue_history(tk)
    _rev_proxy = False
    if not rev_hist:
        mc = _safe(info.get("totalRevenue"))
        if not mc:
            return {"error": f"revenue history non disponibile per {ticker} "
                             "(yfinance senza income_stmt ne' totalRevenue): DCF rifiutato"}
        rev_hist = [mc/1e6*0.85, mc/1e6*0.92, mc/1e6]
        _rev_proxy = True
    # audit/13 V2.2.1: gli anni VERI si fotografano PRIMA del pad — il CAGR si calcola
    # solo su questi; il pad 0.9x resta SOLO per riempire le 4 colonne del foglio
    # (dichiarato in _growth_source), non entra piu' nella growth.
    rev_hist_real = list(rev_hist)
    _n_real_years = 0 if _rev_proxy else len(rev_hist_real)
    while len(rev_hist) < 4:
        rev_hist.insert(0, rev_hist[0] * 0.9)
    rev_hist = [round(x, 1) for x in rev_hist[-4:]]

    gm_real = _margin(tk, "Gross Profit") or prior["gm"]
    ebitda_real = _safe(info.get("ebitdaMargins")) or prior["ebitda_m"]
    shares = _safe(info.get("sharesOutstanding"))
    shares_m = round(shares/1e6, 1) if shares else None
    total_debt = _safe(info.get("totalDebt"), 0) / 1e6
    total_cash = _safe(info.get("totalCash"), 0) / 1e6
    net_debt = round(total_debt - total_cash, 1)
    price = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))
    beta_mkt = _safe(info.get("beta")) or prior["beta_u"] * 1.3

    # GROWTH (audit/13 V2.2, ok PM): 1) variant view dell'agente; 2) blend CAGR-reale
    # /prior pesato sulla QUALITA' dello storico (anni veri, mai il pad), con floor/cap
    # del PROFILO e coda del fade convergente al terminale; 3) prior puro DICHIARATO.
    # Il terminale si calcola PRIMA della growth: la coda del fade converge verso di lui.
    _terminal_g = 0.02 if cw["currency"] != "JPY" else 0.01
    _terminal_source = "default (~inflazione; regola JPY chiavata sulla valuta dei flussi)"
    if terminal_growth is not None:
        try:
            _terminal_g = max(0.0, min(float(terminal_growth), cw.get("rf", 0.04)))
            _terminal_source = "agent (capped at risk-free)"
        except Exception:
            pass
    _growth_source = None
    growth_fwd = None
    pg = prior["growth"]
    gf = _safe((profile or {}).get("growth_floor"), -0.15)
    gc = _safe((profile or {}).get("growth_cap"), 0.45)
    if growth_override:
        try:
            gp = [float(x) for x in growth_override if x is not None][:5]
            if gp:
                while len(gp) < 5:
                    gp.append(gp[-1])
                growth_fwd = [round(x, 4) for x in gp]
                g = growth_fwd[0]
                _growth_source = "agent variant view (buy-side)"
        except Exception:
            growth_fwd = None
    # V6 Lotto 2 (audit/19 V6.2, D2): layer GUIDANCE — sotto l'analista, sopra il
    # blend: un nome con guidance pubblica fresca non esce mai piu' col CAGR cieco.
    _guidance_used = []
    _guidance_growth_range = None
    _guidance_deviation_pp = None
    _guid_skips = None
    # V6.5 (Lotto 3): l'analista ha vinto la gerarchia — la guidance ATTIVA resta
    # come CONFRONTO dichiarato in etichetta + deviazione anno-1 nel payload, per
    # il nudge anti-deriva in chat_tools (mai blocco: sovranita' intatta)
    if growth_fwd is not None and guidance:
        _suff, _guidance_deviation_pp = _compare_override_vs_guidance(
            guidance, growth_fwd[0],
            last_rev=(rev_hist_real[-1] if (rev_hist_real and not _rev_proxy) else None),
            rep_cur=rep_cur, pg=pg, terminal_g=_terminal_g, gf=gf, gc=gc)
        if _suff:
            _growth_source += _suff
    if growth_fwd is None and guidance:
        _gg = _consume_guidance_growth(
            guidance,
            last_rev=(rev_hist_real[-1] if (rev_hist_real and not _rev_proxy) else None),
            rep_cur=rep_cur, pg=pg, terminal_g=_terminal_g, gf=gf, gc=gc)
        if _gg and _gg.get("growth_fwd"):
            growth_fwd = _gg["growth_fwd"]
            g = growth_fwd[0]
            _growth_source = _gg["source"]   # skips gia' dichiarati nell'etichetta
            _guidance_used.extend(_gg["used"])
            _guidance_growth_range = _gg["range_y1"]
        elif _gg and _gg.get("skips"):
            _guid_skips = _gg["skips"]
    if growth_fwd is None:
        real_cagr = None
        try:
            if _n_real_years >= 2 and rev_hist_real[0] > 0:
                real_cagr = (rev_hist_real[-1] / rev_hist_real[0]) ** (1.0 / (_n_real_years - 1)) - 1.0
        except Exception:
            real_cagr = None
        # peso della storia per QUALITA' (V2.2.2): >=4 anni veri w=0.7, 3 anni 0.5,
        # 2 anni 0.3, storia proxy w=0 (solo prior, dichiarato)
        w = {0: 0.0, 1: 0.0, 2: 0.3, 3: 0.5}.get(_n_real_years, 0.7)
        if real_cagr is not None and w > 0:
            g0 = max(gf, min(gc, w * real_cagr + (1 - w) * pg))
            _growth_source = ("blend qualita'-storia: CAGR reale %.1f%% su %d anni VERI "
                              "(pad escluso) x w=%.1f + prior %s %.1f%% x %.1f -> %.1f%% "
                              "(clamp profilo [%.0f%%, %.0f%%])"
                              % (real_cagr * 100, _n_real_years, w, _prior_src, pg * 100,
                                 1 - w, g0 * 100, gf * 100, gc * 100))
        else:
            g0 = max(gf, min(gc, pg))
            _growth_source = ("prior %s %.1f%% PURO — %s: il numero senza variant view "
                              "non e' actionable"
                              % (_prior_src, pg * 100,
                                 "storia PROXY fabbricata (w=0)" if _rev_proxy
                                 else "storico insufficiente per un CAGR"))
        g = g0
        # fade verso il prior con CODA CONVERGENTE al terminale (audit/13 B3): Y4 =
        # prior, Y5 = meta' strada prior->g_term — il cliff si chiude nel PATH
        _y5 = (pg + _terminal_g) / 2.0
        growth_fwd = [round(g0, 3), round(g0*0.75 + pg*0.25, 3), round(g0*0.50 + pg*0.50, 3),
                      round(pg, 3), round(_y5, 3)]
    if _guid_skips:
        # V6 Lotto 2 (regola 14/07): guidance revenue nel registro ma NON consumata
        # — il modello e' tornato al blend/prior e lo dice, mai un buco zitto
        _growth_source = ((_growth_source or "") + " | GUIDANCE nel registro NON "
                          "consumata nel growth (dichiarato): " + "; ".join(_guid_skips))
    # ---- DRIVER REALI (audit/13 V2.3): D&A/capex/tax dai bilanci, profilo come
    # fallback, tutto etichettato in _driver_sources ----
    _rr = _real_driver_ratios(tk)
    da_pct, capex_med, tax_med = _rr.get("da_med"), _rr.get("capex_med"), _rr.get("tax_med")
    _driver_sources = {}
    _prof_capex = _safe((profile or {}).get("capex"))
    # V6 Lotto 2: capex di GUIDANCE sopra la mediana storica — il piano capex
    # dichiarato dalla societa' e' forward, la mediana e' il passato (etichettato)
    _g_cx = _consume_guidance_driver(guidance, "capex_pct") if guidance else None
    if _g_cx is not None and _g_cx.get("mid") is not None:
        capex_p = _g_cx["mid"]
        _driver_sources["capex"] = _g_cx["label"]
        _guidance_used.append(_g_cx["used"])
    elif capex_med is not None:
        capex_p = capex_med
        _driver_sources["capex"] = "mediana REALE cashflow su %d anni: %.1f%%" % (_rr["n_years"], capex_p * 100)
    elif _prof_capex is not None:
        capex_p = _prof_capex
        _driver_sources["capex"] = "PROFILO sub-settore %.1f%% (capex reale n.d.)" % (capex_p * 100)
    else:
        capex_p = 0.03
        _driver_sources["capex"] = "DEFAULT 3% (ne' reale ne' profilo): dichiarato"
    if _g_cx is not None and _g_cx.get("mid") is None:
        # review L2 (MEDIA-2): guidance capex nel registro ma non consumabile
        _driver_sources["capex"] += (" | GUIDANCE capex NON consumata (dichiarato): "
                                     + _g_cx["skip_note"])
    if tax_med is not None:
        tax_p = tax_med
        _driver_sources["tax"] = "effettiva REALE dai bilanci (mediana, clamp 10-35%%): %.1f%%" % (tax_p * 100)
    else:
        try:
            from bellomberg.market_data.damodaran_data import get_tax_marginal as _gtm
            _tm = _gtm(country)
        except Exception:
            _tm = None
        tax_p = _tm["value"] if _tm else 0.25
        _driver_sources["tax"] = (_tm["source"] + " (effettiva n.d.)") if _tm else \
            "DEFAULT 25% (ne' effettiva ne' marginale paese): dichiarato"
    _driver_sources["nwc"] = "convenzione 3% dei ricavi (dichiarata, nessuna fonte reale)"
    _driver_sources["caprd"] = "convenzione 4% (dichiarata)"

        # ---- MARGINI + fix double-count D&A IFRS (audit/13 V2.3, caso industriale IFRS) ----
    # Per gli industriali IFRS (regione Europe/Japan) il Gross Profit yfinance
    # INCLUDE il D&A nel COGS: l'EBIT lo sottrarrebbe due volte. Se il D&A reale
    # e' noto, si riaggiunge al GM del modello e diventa il driver da_tan.
    try:
        from bellomberg.market_data.damodaran_data import region_for_country as _rfc
        # review pre-commit (MEDIA M2): l'euristica IFRS copre TUTTO il mondo non
        # US/Canada (anche Emerging/Global: Taiwan, Corea, Brasile... sono IFRS o
        # equivalenti) — con da_tan ora REALE, escluderli avrebbe raddoppiato il
        # D&A nell'EBIT dei nomi ex-US. Euristica DICHIARATA (il flag fine per
        # profilo resta un raffinamento possibile, v. audit/13 V2.3).
        _ifrs = _rfc(country) in ("Europe", "Japan", "Emerging", "Global")
    except Exception:
        _ifrs = False
    _gm_is_real = _margin(tk, "Gross Profit") is not None
    _margin_source = "real margins (derived opex)" if _gm_is_real else \
        "PRIOR %s (Gross Profit yfinance n.d.): dichiarato" % _prior_src
    # V6 Lotto 2: gross margin di GUIDANCE = base del margine del modello (la
    # societa' guida sul margine RIPORTATO, quindi il re-add D&A IFRS sotto si
    # applica uguale). La sanity M7 continua a misurare sul gm REALE
    # (_calibration.gross_margin_real, invariato).
    _g_gm = _consume_guidance_driver(guidance, "gross_margin") if guidance else None
    if _g_gm is not None and _g_gm.get("mid") is None:
        # review L2 (MEDIA-2): guidance gm nel registro ma non consumabile
        _margin_source += (" | GUIDANCE gross_margin NON consumata (dichiarato): "
                           + _g_gm["skip_note"])
        _g_gm = None
    _gm_base = gm_real
    if _g_gm is not None:
        _gm_base = _g_gm["mid"]
        _margin_source = _g_gm["label"]
        _guidance_used.append(_g_gm["used"])
    gm_model = _gm_base
    _da_note = None
    if _ifrs and (_gm_is_real or _g_gm is not None) and da_pct:
        gm_model = min(0.95, round(_gm_base + da_pct, 4))
        _da_note = ("IFRS (%s): D&A reale %.1f%% dei ricavi RIAGGIUNTO al gross margin "
                    "(%.1f%% -> %.1f%%) e usato come driver da_tan: niente double-count "
                    "nell'EBIT" % (country, da_pct * 100, _gm_base * 100, gm_model * 100))
    elif _ifrs and _gm_is_real:
        _da_note = ("ATTENZIONE IFRS: D&A reale n.d. dal cashflow — il gross margin "
                    "Yahoo puo' includere D&A nel COGS: possibile sottostima dell'EBIT "
                    "(dichiarato, non corretto)")
    da_tan_p = da_pct if da_pct else 0.02
    _driver_sources["da_tan"] = ("mediana REALE D&A/ricavi: %.1f%%" % (da_tan_p * 100)
                                 if da_pct else "DEFAULT 2% (D&A reale n.d.): dichiarato")
    _ebitda_used = ebitda_real
    if ebitda_margin_target is not None:
        try:
            _ebitda_used = max(0.02, min(0.60, float(ebitda_margin_target)))
            _margin_source = "agent EBITDA-margin target"
        except Exception:
            _ebitda_used = ebitda_real
    else:
        # V6 Lotto 2: ebitda_margin di GUIDANCE ancora l'opex derivato (l'analista
        # col suo ebitda_margin_target resta sovrano, ramo sopra)
        _g_em = _consume_guidance_driver(guidance, "ebitda_margin") if guidance else None
        if _g_em is not None and _g_em.get("mid") is None:
            # review L2 (MEDIA-2): registro con ebitda_margin non consumabile
            _margin_source += (" | GUIDANCE ebitda_margin NON consumata (dichiarato): "
                               + _g_em["skip_note"])
            _g_em = None
        if _g_em is not None:
            _ebitda_used = max(0.02, min(0.60, _g_em["mid"]))
            _margin_source = (_margin_source + " | opex ancorato a " + _g_em["label"])
            _guidance_used.append(_g_em["used"])
    opex_total = max(0.05, round(gm_model - _ebitda_used, 3))
    if guidance and _guidance_used and (gm_model - _ebitda_used) < 0.05 and \
            any("ebitda_margin" in str(u.get("driver") or "") for u in _guidance_used):
        # review L2 (BASSA-3): floor opex 5% sopra un EBITDA di guidance = target
        # effettivo diverso da quello etichettato — si dichiara
        _margin_source += (" | ATTENZIONE: gm-ebitda < 5pp, opex al floor 5%: "
                           "l'EBITDA effettivo del modello diverge dal target di guidance")
    sm_p = round(opex_total * 0.45, 3)
    rd_p = round(opex_total * 0.30, 3)
    ga_p = round(opex_total * 0.25, 3)
    gm_fwd = [round(min(0.85, gm_model + 0.002*i), 3) for i in range(5)]
    em_fwd = [round(min(0.45, ebitda_real + 0.004*i), 3) for i in range(5)]

    # scenari additivi (audit/13 V2.4): spread per profilo, sign-aware — i vecchi
    # moltiplicatori (x0.7/x1.25) si INVERTIVANO con growth negativa
    _cyclical = bool((profile or {}).get("cyclical"))
    _spread = 0.06 if _cyclical else 0.03

    # ancora RONIC per il terminale disciplinato (audit/13 V2.2.5): ROC industry
    # Damodaran del profilo, dichiarata; assente = None (il TV degrada a RONIC=WACC
    # DICHIARANDOLO)
    _ronic = None
    _dam_ind = (profile or {}).get("dam_industry")
    if _dam_ind:
        try:
            from bellomberg.market_data.damodaran_data import get_industry_field as _gif, region_for_country as _rfc2
            _roc = _gif(_dam_ind, "roc", _rfc2(country))
        except Exception:
            _roc = None
        if _roc is not None:
            _ronic = {"value": _roc["value"], "source": _roc["source"]
                      + ((" | " + _roc["note"]) if _roc.get("note") else "")}

    yA = [f"{y}A" for y in range(2022, 2026)]
    yF = [f"{y}B" for y in range(2026, 2031)]

    spec = {
        "ticker": ticker.upper(), "company_name": name, "sector": sector or "n/d",
        "currency": rep_cur, "country": country or "n/d",
        "shares": shares_m, "net_debt": net_debt, "price": price,
        "base_scenario": "Base",
        "_variant_view": variant_view, "_growth_source": _growth_source,
        "_margin_source": _margin_source, "_terminal_source": _terminal_source,
        # V6 Lotto 2: righe di guidance consumate (con fonte+vintage, D1) e range
        # anno-1 per gli scenari (D3) — None quando la guidance non ha guidato nulla
        "_guidance_used": _guidance_used or None,
        "_guidance_growth_range": _guidance_growth_range,
        # V6.5 (Lotto 3): deviazione anno-1 analista vs guidance attiva (in pp),
        # None quando non c'e' guidance o non c'e' override — alimenta il nudge
        "_guidance_deviation_pp": _guidance_deviation_pp,
        "years_actual": yA, "years_forecast": yF,
        "revenue_build": {
            "products": [{"name": "Core business", "actuals": rev_hist,
                          "growth": growth_fwd}],
            "geo": {},
        },
        "actuals": {
            # audit/13 V2.3 (review B5): niente piu' 3%/3%/25% spacciati per consuntivi
            # — dove c'e' il dato reale (mediana) si usa quello, e _driver_sources
            # dichiara riga per riga cosa e' reale e cosa e' convenzione
            "revenue": rev_hist,
            "growth": [None] + [round(rev_hist[i]/rev_hist[i-1]-1, 3) for i in range(1, 4)],
            "gm": [round(gm_model, 3)]*4,
            "sm": [sm_p]*4, "rd": [rd_p]*4, "ga": [ga_p]*4,
            "caprd": [0.04]*4, "nwc": [0.03]*4,
            "capex": [round(capex_p, 3)]*4, "tax": [round(tax_p, 3)]*4,
        },
        "assumptions": {
            "growth": growth_fwd, "gm": gm_fwd, "sm": [sm_p]*5, "rd": [rd_p]*5,
            "ga": [ga_p]*5, "caprd": [0.04]*5, "nwc": [0.03]*5,
            "capex": [round(capex_p, 3)]*5, "tax": [round(tax_p, 3)]*5,
            "da_tan": [round(da_tan_p, 4)]*5,
        },
        "scenarios": {
            # audit/13 V2.4: spread ADDITIVI (sign-aware) al posto dei moltiplicatori
            "Bear": {"growth": [round(x - _spread, 3) for x in growth_fwd]},
            "Base": {},
            "Bull": {"growth": [round(x + _spread, 3) for x in growth_fwd],
                     "gm": [round(min(0.85, x+0.01), 3) for x in gm_fwd]},
        },
        "comps": [],            # da popolare con peer di settore (TODO peer list)
        "precedents": [],
        "wacc_inputs": _wacc_inputs_v1(cw, prior, profile, country),
        "terminal": {"g": _terminal_g, "exit_multiple": 12},
        "scenario_spread": _spread, "_cyclical": _cyclical,
        "ronic_anchor": _ronic,
        "net_debt_items": {"senior_debt": -round(total_debt, 1), "cash": round(total_cash, 1),
                           "earnouts": 0, "deferred": 0, "nwc_norm": 0, "tfr": 0},
        "_calibration": {
            "sector_matched": sector, "engine": engine, "country": country,
            "risk_free": cw["rf"], "rf_source": cw.get("rf_source"),
            "rf_stale": cw.get("rf_stale"),
            "country_risk_premium": cw["crp"], "crp_source": cw.get("crp_source"),
            "erp_source": cw.get("erp_source"),
            "reporting_currency": rep_cur, "currency_note": _cur_note,
            "gross_margin_real": round(gm_real, 3),
            "gross_margin_model": round(gm_model, 3), "da_note": _da_note,
            "driver_sources": _driver_sources, "prior_source": _prior_src,
            "cyclical": _cyclical, "scenario_spread": _spread,
            "beta_unlevered_sector": prior["beta_u"],
            "note": "audit/13 V1: rf per valuta dei FLUSSI (%s) con fonte/staleness "
                    "dichiarate; ERP implied Damodaran versionato; CRP dataset completo; "
                    "tax marginale paese; D/E target industry. La regola 'terminal g "
                    "0.01 se JPY' segue ora la valuta dei flussi." % rep_cur,
        },
    }
    return spec



if __name__ == "__main__":
    import json, sys
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    spec = build_spec_from_ticker(t)
    if spec.get("error"):
        print("ERR:", spec["error"])
    else:
        print("=== " + str(spec["company_name"]) + " (" + str(spec["ticker"]) + ") ===")
        print("settore:", spec["sector"], "| paese:", spec["country"], "| valuta:", spec["currency"])
        print("revenue 4y:", spec["actuals"]["revenue"])
        print("opex S&M/R&D/G&A:", spec["assumptions"]["sm"][0], spec["assumptions"]["rd"][0], spec["assumptions"]["ga"][0])
        print("net debt:", spec["net_debt"], "| shares(m):", spec["shares"], "| price:", spec["price"])
        print("calibrazione:", json.dumps(spec["_calibration"], indent=1, ensure_ascii=False))
