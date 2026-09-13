"""
specialist_scores.py (#186) — Scorer DETERMINISTICI per gli specialisti.

Pattern ispirato a virattt/ai-hedge-fund: il NUMERO sta nel codice (rubric a punti
con soglie esplicite, riproducibile e auditabile), l'LLM NARRA partendo dallo score.
Meno prompt, piu' codice. Ogni scorer ritorna {score, max_score, verdict, lines, metrics}
ed e' guarded: se i dati mancano ritorna None e lo specialista lavora come prima.

Convenzione 'risk score': PIU' ALTO = PIU' RISCHIO.
"""
from __future__ import annotations
from bellomberg.core.language import scoped_language
from bellomberg.reporting.i18n import label as _t

import bellomberg.storage.classificazione as cl
from bellomberg.core.paths import REPORT_DIR
import math
from numbers import Real


def _finite_number(value):
    """Un dato invalido e' assente, non una fascia di rischio (NaN confronta falso)."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return float(value) if math.isfinite(value) else None


def _band(value, thresholds, points, reverse=False):
    """thresholds crescenti; ritorna i punti del primo bucket che contiene value.
    reverse=True per metriche dove ALTO=buono (es. Sharpe): si confronta al contrario."""
    value = _finite_number(value)
    if value is None:
        return None
    if not reverse:
        for th, pt in zip(thresholds, points):
            if value <= th:
                return pt
        return points[-1]
    else:
        for th, pt in zip(thresholds, points):
            if value >= th:
                return pt
        return points[-1]


@scoped_language
def quant_score(portfolio_data=None, risk_data=None):
    """Rubric di rischio del portafoglio. Ritorna dict o None se dati insufficienti."""
    if risk_data is None:
        try:
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            risk_data = compute_portfolio_risk()
        except Exception:
            return None
    if not isinstance(risk_data, dict) or risk_data.get("error"):
        return None
    p = risk_data.get("portfolio") or {}
    vol = _finite_number(p.get("vol_annual_pct"))
    sharpe = _finite_number(p.get("sharpe"))
    beta = _finite_number(p.get("beta_vs_spy"))
    var95 = _finite_number(p.get("var_95_1d_pct"))
    maxdd = _finite_number(p.get("max_dd_1y_pct"))

    # concentrazione: top-name % e HHI dai pesi
    top_pct = None
    hhi = None
    weights = []
    try:
        positions = (portfolio_data or {}).get("positions") or []
        if positions:
            values = [_finite_number(x.get("valore_mercato_eur", x.get("valore_mercato"))) for x in positions]
            if all(v is not None for v in values) and sum(values) > 0:
                weights = [v / sum(values) for v in values]
        if not weights:
            pa = risk_data.get("per_asset") or {}
            ws = [_finite_number(v.get("weight_pct")) for v in pa.values()]
            if ws and all(w is not None for w in ws):
                weights = [w / 100.0 for w in ws]
        if weights:
            top_pct = max(weights) * 100.0
            hhi = sum(w * w for w in weights) * 10000  # 0..10000
    except Exception:
        pass

    lines = []
    pts = []

    def add(label, value, fmt, p_):
        if p_ is not None:
            lines.append((label, fmt, p_)); pts.append(p_)

    # ogni metrica: soglie -> punti (0=ok .. 3=rischio alto)
    add(_t("Volatilita' annualizzata"), vol, ("{:.1f}%".format(vol) if vol is not None else "n/d"),
        _band(vol, [12, 20, 30], [0, 1, 2, 3]))
    # 16/07: etichetta "trailing 1a" esplicita — senza, la banda si leggeva come giudizio
    # sul titolo invece che come fotografia storica (dottrina bilaterale PM 16/07)
    add(_t("Sharpe ratio (trailing 1a)"), sharpe, ("{:.2f}".format(sharpe) if sharpe is not None else "n/d"),
        _band(sharpe, [1.5, 1.0, 0.5], [0, 1, 2, 3], reverse=True))
    add(_t("Beta vs S&P 500"), beta, ("{:.2f}".format(beta) if beta is not None else "n/d"),
        _band(beta, [0.8, 1.1, 1.4], [0, 1, 2, 3]))
    add(_t("VaR 95% 1g"), var95, ("{:.2f}%".format(var95) if var95 is not None else "n/d"),
        _band(abs(var95) if var95 is not None else None, [2.0, 3.5, 5.0], [0, 1, 2, 3]))
    add(_t("Max Drawdown 1a"), maxdd, ("{:.1f}%".format(maxdd) if maxdd is not None else "n/d"),
        _band(abs(maxdd) if maxdd is not None else None, [8, 15, 25], [0, 1, 2, 3]))
    add(_t("Top position"), top_pct, ("{:.1f}%".format(top_pct) if top_pct is not None else "n/d"),
        _band(top_pct, [15, 25, 35], [0, 1, 2, 3]))
    add(_t("Concentrazione (HHI)"), hhi, ("{:.0f}".format(hhi) if hhi is not None else "n/d"),
        _band(hhi, [1200, 2000, 3000], [0, 1, 2, 3]))

    if not pts:
        return None
    score = sum(pts)
    max_score = len(pts) * 3
    frac = score / max_score if max_score else 0
    if frac < 0.25:
        verdict = _t("RISCHIO BASSO")
    elif frac < 0.5:
        verdict = _t("RISCHIO MEDIO")
    elif frac < 0.72:
        verdict = _t("RISCHIO ELEVATO")
    else:
        verdict = _t("RISCHIO CRITICO")

    return {
        "domain": "quant",
        "score": score,
        "max_score": max_score,
        "verdict": verdict,
        "lines": lines,  # (label, value_str, points)
        "metrics": {"vol_annual_pct": vol, "sharpe": sharpe, "beta_vs_spy": beta,
                    "var_95_1d_pct": var95, "max_dd_1y_pct": maxdd,
                    "top_position_pct": top_pct, "hhi": hhi},
    }


@scoped_language
def macro_score(macro_data=None):
    """Rubric REGIME di mercato. PIU' ALTO = piu' restrittivo/risk-off/late-cycle.
    Ritorna dict o None se dati insufficienti."""
    if macro_data is None:
        try:
            from bellomberg.agents.agent_tools import tool_get_macro_dashboard
            macro_data = tool_get_macro_dashboard()
        except Exception:
            return None
    if not isinstance(macro_data, dict):
        return None
    ind = macro_data.get("indicators") or {}
    def _v(k, field="value"):
        try:
            return _finite_number(ind.get(k, {}).get(field))
        except Exception:
            return None
    vix = _v("vix_close")
    # audit/11 §5: NIENTE fallback al campo 'value' (e' il LIVELLO dell'indice CPIAUCSL
    # ~320, non una percentuale: finiva '3 punti rischio' e 'CPI YoY 320%' nel blocco)
    cpi = _v("us_cpi_yoy", "yoy_pct")
    hy = _v("high_yield_spread")
    unemp_chg = _v("us_unemployment", "change_vs_prev")
    curve_bps = _finite_number(macro_data.get("yield_curve_10y_2y_bps"))
    real_ff = _finite_number(macro_data.get("real_fed_funds_pct"))

    lines = []
    pts = []

    def add(label, valstr, p_):
        if p_ is not None:
            lines.append((label, valstr, p_)); pts.append(p_)

    # VIX: calmo->teso
    if vix is not None:
        p = 0 if vix < 16 else 1 if vix < 22 else 2 if vix < 28 else 3
        add("VIX", "{:.1f}".format(vix), p)
    # Yield curve 10y-2y: invertita = late-cycle/recessione
    if curve_bps is not None:
        p = 3 if curve_bps < 0 else 2 if curve_bps < 25 else 1 if curve_bps < 60 else 0
        add(_t("Curva 10y-2y"), "{:.0f} bps".format(curve_bps), p)
    # Real Fed funds: piu' alto = piu' restrittivo
    if real_ff is not None:
        p = 0 if real_ff < 0.5 else 1 if real_ff < 1.5 else 2 if real_ff < 2.5 else 3
        add("Real Fed funds", "{:.2f}%".format(real_ff), p)
    # CPI YoY: inflazione alta = rischio
    if cpi is not None:
        p = 0 if cpi < 2.3 else 1 if cpi < 3 else 2 if cpi < 4 else 3
        add("CPI YoY", "{:.1f}%".format(cpi), p)
    # HY credit spread: largo = stress
    if hy is not None:
        p = 0 if hy < 3 else 1 if hy < 4 else 2 if hy < 5 else 3
        add("Spread HY", "{:.2f}".format(hy), p)
    # Disoccupazione in salita = deterioramento
    if unemp_chg is not None:
        p = 0 if unemp_chg <= 0 else 1 if unemp_chg < 0.2 else 2
        add(_t("Disoccup. (var)"), "{:+.2f}".format(unemp_chg), p)

    if not pts:
        return None
    score = sum(pts)
    max_score = len(pts) * 3
    frac = score / max_score if max_score else 0
    if frac < 0.25:
        verdict = _t("REGIME ESPANSIVO (risk-on)")
    elif frac < 0.5:
        verdict = _t("REGIME NEUTRALE")
    elif frac < 0.72:
        verdict = _t("REGIME RESTRITTIVO (late-cycle)")
    else:
        verdict = _t("REGIME RISK-OFF / RECESSIVO")

    return {"domain": "macro", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines,
            "metrics": {"vix": vix, "cpi_yoy": cpi, "hy_spread": hy, "curve_10y2y_bps": curve_bps,
                        "real_fed_funds_pct": real_ff, "unemp_change": unemp_chg}}


def _verdict_bands(score, max_score, labels):
    frac = score / max_score if max_score else 0
    if frac < 0.25: return labels[0]
    if frac < 0.5:  return labels[1]
    if frac < 0.72: return labels[2]
    return labels[3]


@scoped_language
def fundamentals_score(portfolio_data=None, valuations=None, max_names=4):
    """Valutazione del book dal DCF (margine di sicurezza). PIU' ALTO = piu' CARO/sopravvalutato."""
    positions = (portfolio_data or {}).get("positions") or []
    if portfolio_data is None or (not positions and valuations is None):
        try:
            from bellomberg.agents import agent_tools
            portfolio_data = agent_tools.tool_get_portfolio_live()
            positions = (portfolio_data or {}).get("positions") or []
        except Exception:
            positions = []
    ranked = sorted(positions, key=lambda x: -(x.get("peso_pct") or 0))
    # C1 16/07: i VEICOLI (fondi chiusi, ETF, ...) si valutano a NAV, non a DCF — dcf_engine li
    # rifiuta comunque, ma tenerli nei top-4 bruciava 3 slot su 4 e lo scorer valutava
    # UN SOLO nome a ogni run. Ora i top-4 sono i top-4 VALUTABILI (copertura reale).
    _negozio_buco = None
    try:
        # Una sola fotografia per tutto lo score: veicoli e DAT non possono
        # provenire da due versioni diverse del negozio durante la stessa run.
        _negozio = cl.carica_veicoli()
        if _negozio["origine"] in ("assente", "illeggibile"):
            _negozio_buco = ("negozio dei veicoli %s (%s): nessun veicolo/DAT escluso"
                             % (_negozio["origine"], _negozio["motivo"]))
        _veh = {t for t, v in _negozio["veicoli"].items()
                if v.get("classe_size") == "veicolo"}
        _dat = set(cl.veicoli_per_tipo("dat", _negozio)["tickers"])
    except Exception as e:
        _veh, _dat = set(), set()
        _negozio_buco = ("negozio dei veicoli illeggibile (%s: %s): "
                         "nessun veicolo/DAT escluso" % (type(e).__name__, e))
    _fuori = _veh | _dat
    names = [p.get("ticker") for p in ranked
             if p.get("ticker") and p.get("ticker").upper() not in _fuori][:max_names]
    mos_list = []
    detail = []
    flagged_skipped = []
    no_model = []
    invalid_valuation = []
    valuation_dates = {}
    for tk in names:
        upside = None
        try:
            if valuations is not None and tk in valuations:
                v = valuations.get(tk) or {}
                from bellomberg.valuation.dcf_quality import normalize_valuation_payload
                v = normalize_valuation_payload(v)
                if str(v.get("ticker") or "").upper() != tk.upper():
                    invalid_valuation.append(tk)
                    continue
                fv = next((v[key] for key in ("fair_value", "fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base")
                           if v.get(key) is not None), None)
                fv, pr = _finite_number(fv), _finite_number(v.get("price"))
                if v.get("valuation_flagged"):
                    flagged_skipped.append(tk)
                elif fv is not None and pr is not None and pr > 0:
                    upside = _finite_number((fv / pr - 1) * 100)
                    if v.get("valuation_date"):
                        valuation_dates[tk] = v["valuation_date"]
            else:
                # 21/07 (fix F17, causa vera dei "FV n.d."): PRIMA qui c'era
                # generate_valuation(tk), che a OGNI run riscriveva il modello SENZA
                # variant view e quindi AZZERAVA la tesi (guardia tesi-vs-file 17/07)
                # — nel run #46 ha invalidato tre tesi del book alle 13:56-13:58 senza
                # nessuna chiamata tool loggata. Lo scorer ora LEGGE il sidecar
                # dell'ultimo modello fatto dall'analista; senza modello (o piu'
                # vecchio di 30gg) il nome resta FUORI dal margine, dichiarato:
                # MAI rigenerare senza tesi (dottrina modello unico, PM 16/07).
                import os as _os
                import json as _json
                from datetime import date as _date
                import re as _re
                _safe = tk.replace(".", "_").replace("-", "_")
                _versioned_safe = _re.sub(r'[^A-Za-z0-9_-]', '_', tk)
                r = None
                _names = ["VAL_" + _safe + ".payload.json", "VAL_" + _safe + "_FLAGGED.payload.json"]
                _versioned = []
                if _os.path.isdir(REPORT_DIR):
                    _versioned = [name for name in _os.listdir(REPORT_DIR) if _re.fullmatch(
                        r"VAL_" + _re.escape(_versioned_safe) + r"_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.payload\.json", name)]
                if _versioned:
                    _names = sorted([name for name in _names + _versioned
                                     if _os.path.isfile(_os.path.join(str(REPORT_DIR), name))],
                                    key=lambda name: _os.path.getmtime(_os.path.join(str(REPORT_DIR), name)), reverse=True)
                for _name in _names:
                    _p = _os.path.join(str(REPORT_DIR), _name)
                    if _os.path.exists(_p):
                        try:
                            with open(_p, encoding="utf-8") as _f:
                                r = _json.load(_f)
                            from bellomberg.valuation.method_registry import is_record_method
                            if _name in _versioned and (not is_record_method(r.get('valuation_decision')) or
                                    _name != 'VAL_' + _versioned_safe + '_' + str(r.get('generation_id')) + '.payload.json'):
                                r = None
                            if "_FLAGGED" in _name:
                                r["valuation_flagged"] = True
                        except Exception:
                            r = None
                        break
                _age_ok = False
                if r is not None:
                    try:
                        _age_ok = (_date.today() - _date.fromisoformat(
                            str(r.get("_timestamp", ""))[:10])).days <= 30
                    except Exception:
                        _age_ok = False
                if r is None or not _age_ok:
                    no_model.append(tk)
                    continue
                from hashlib import sha256
                workbook_path = _p[:-len(".payload.json")] + ".xlsx"
                try:
                    with open(workbook_path, "rb") as workbook:
                        workbook_hash = sha256(workbook.read()).hexdigest()
                    if r.get("workbook_sha256") != workbook_hash:
                        invalid_valuation.append(tk)
                        continue
                except OSError:
                    invalid_valuation.append(tk)
                    continue
                if (r.get("sanity") or {}).get("severity") == "BLOCK":
                    r["valuation_flagged"] = True
                from bellomberg.valuation.dcf_quality import normalize_valuation_payload
                r = normalize_valuation_payload(r)
                if str(r.get("ticker") or "").upper() != tk.upper():
                    invalid_valuation.append(tk)
                    continue
                # CATENA CANONICA del fair value (review 15/07): nessun engine emette
                # una chiave piatta "fair_value" (operating_v3 -> _weighted, bank -> _blend).
                # Zero e' un valore presente; un FV prioritario invalido non va
                # sostituito in silenzio da un campo subordinato.
                fv = next((r[key] for key in (
                    "fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base"
                ) if r.get(key) is not None), None)
                fv, pr = _finite_number(fv), _finite_number(r.get("price"))
                # audit/12 V0.5: un fair value FLAGGED dalla sanity non entra nel margine
                # di sicurezza del memo — prima un FV bocciato (0.08 su un ADR del book) contava come "-99.6%, sopravvalutato"
                if r.get("valuation_flagged"):
                    flagged_skipped.append(tk)
                elif fv is not None and pr is not None and pr > 0:
                    upside = _finite_number((fv / pr - 1) * 100)
                    if r.get("valuation_date"):
                        valuation_dates[tk] = r["valuation_date"]
        except Exception:
            upside = None
        if upside is not None:
            mos_list.append(upside); detail.append((tk, upside))
        elif tk not in flagged_skipped:
            invalid_valuation.append(tk)
    if not mos_list:
        return None
    avg_mos = sum(mos / len(mos_list) for mos in mos_list)
    n_cheap = sum(1 for x in mos_list if x >= 15)
    n_rich = sum(1 for x in mos_list if x <= -15)
    # punteggio: piu' caro = piu' rischio
    p_mos = 0 if avg_mos >= 20 else 1 if avg_mos >= 5 else 2 if avg_mos >= -5 else 3
    p_rich = 0 if n_rich == 0 else 1 if n_rich == 1 else 2 if n_rich == 2 else 3
    lines = [(_t("Margine di sicurezza medio"), "{:+.1f}%".format(avg_mos), p_mos),
             (_t("Nomi sopravvalutati (>15%)"), "{}/{}".format(n_rich, len(mos_list)), p_rich)]
    for tk, u in detail:
        lines.append(("  " + tk, "{:+.1f}%".format(u), 0 if u >= 0 else 2))
        if tk in valuation_dates:
            lines.append(("  Cutoff flussi/prezzo " + tk, valuation_dates[tk] + _t(" (non upside corrente)"), 0))
    pts = [p_mos, p_rich]
    score = sum(pts); max_score = len(pts) * 3
    verdict = _verdict_bands(score, max_score, [_t("BOOK A SCONTO"), _t("VALUTAZIONE EQUA"), _t("BOOK CARO"), _t("BOOK MOLTO CARO")])
    # COPERTURA DICHIARATA: il verdetto vale sui nomi effettivamente valutabili, non sul
    # book intero (ETF, veicoli e nomi senza DCF restano fuori). Dirlo evita che un
    # giudizio su 1 nome su 4 si legga come giudizio su tutto il portafoglio.
    verdict = _t("{} ({}/{} nomi valutati)").format(verdict, len(mos_list), len(names))
    if flagged_skipped:
        # audit/12 V0.5: i modelli bocciati dalla sanity sono FUORI dal giudizio, dichiarati
        verdict += _t(" [FLAGGED esclusi: {}]").format(", ".join(flagged_skipped))
        lines.append((_t("Modelli FLAGGED esclusi"), ", ".join(flagged_skipped), 0))
    if no_model:
        # 21/07: buco DICHIARATO, non rigenerato alla cieca (il modello lo fa
        # l'analista con la sua variant view, non lo scorer)
        verdict += _t(" [senza modello recente: {}]").format(", ".join(no_model))
        lines.append((_t("Senza modello recente (tocca all'analista)"), ", ".join(no_model), 0))
    if invalid_valuation:
        verdict += _t(" [FV/prezzo assenti o non validi: {}]").format(", ".join(invalid_valuation))
        lines.append((_t("FV/prezzo assenti o non validi"), ", ".join(invalid_valuation), 0))
    if _negozio_buco:
        verdict += _t(" [veicoli/DAT non esclusi: negozio non disponibile]")
        lines.append((_t("Negozio veicoli non disponibile"), _negozio_buco, 0))
    return {"domain": "fundamentals", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "metrics": {"avg_mos_pct": round(avg_mos, 1), "n_valued": len(mos_list),
                                        "n_cheap": n_cheap, "n_rich": n_rich}}


@scoped_language
def options_score(proxy_ticker=None, portfolio_data=None, options_data=None):
    """Regime di volatilita'/protezione da opzioni. PIU' ALTO = piu' paura/protezione cara."""
    if options_data is None:
        if not proxy_ticker:
            # audit/11 §4: il vecchio override "primo ticker USA in ordine DB" applicava
            # soglie IV da INDICE (15/22/30) a single name (una DAT del book con IV 60-100 = sempre
            # 'VOL PANICO'; un ETF sull'oro = sempre 'COMPLACENTE'). SPY resta il proxy di regime.
            proxy_ticker = "SPY"
        try:
            from bellomberg.agents import agent_tools
            options_data = agent_tools.tool_get_options_data(proxy_ticker)
        except Exception:
            return None
    if not isinstance(options_data, dict) or options_data.get("error"):
        return None
    civ = _finite_number(options_data.get("atm_iv_call_pct"))
    piv = _finite_number(options_data.get("atm_iv_put_pct"))
    pcr = _finite_number(options_data.get("put_call_oi_ratio"))
    skew = (piv - civ) if (piv is not None and civ is not None) else None
    atm = piv if piv is not None else civ
    lines = []; pts = []
    if pcr is not None:
        p = 0 if pcr < 0.8 else 1 if pcr < 1.1 else 2 if pcr < 1.5 else 3
        lines.append(("Put/Call OI ratio", "{:.2f}".format(pcr), p)); pts.append(p)
    if skew is not None:
        p = 0 if skew < 0 else 1 if skew < 3 else 2 if skew < 6 else 3
        lines.append(("Skew (IV put-call)", "{:+.1f} pt".format(skew), p)); pts.append(p)
    if atm is not None:
        p = 0 if atm < 15 else 1 if atm < 22 else 2 if atm < 30 else 3
        lines.append(("ATM IV", "{:.1f}%".format(atm), p)); pts.append(p)
    if not pts:
        return None
    score = sum(pts); max_score = len(pts) * 3
    verdict = _verdict_bands(score, max_score, [_t("VOL COMPLACENTE"), _t("VOL NORMALE"), _t("VOL TESA"), _t("VOL PANICO")])
    return {"domain": "options ({})".format(proxy_ticker or "?"), "score": score, "max_score": max_score,
            "verdict": verdict, "lines": lines, "metrics": {"atm_iv": atm, "skew": skew, "put_call_oi": pcr}}


@scoped_language
def crypto_score(intel=None, has_crypto=True):
    """Froth crypto da funding/premio perp (Hyperliquid). PIU' ALTO = piu' euforico/affollato."""
    if intel is None:
        try:
            from bellomberg.agents import agent_tools
            intel = agent_tools.tool_get_hyperliquid_intel()
        except Exception:
            return None
    if not isinstance(intel, dict) or intel.get("error"):
        return None
    # funding annualizzato massimo tra i major + premio perp
    fund_ann = None; premium_bps = None
    try:
        rows = intel.get("highest_funding_long_pressure") or []
        if rows:
            fund_ann = max((v for r in rows
                            if (v := _finite_number(r.get("funding_annualized_pct"))) is not None), default=None)
            premium_bps = max((v for r in rows
                               if (v := _finite_number(r.get("premium_basis_pts"))) is not None), default=None)
    except Exception:
        pass
    lines = []; pts = []
    if fund_ann is not None:
        p = 0 if fund_ann < 5 else 1 if fund_ann < 15 else 2 if fund_ann < 30 else 3
        lines.append((_t("Funding annualizzato max"), "{:.1f}%".format(fund_ann), p)); pts.append(p)
    if premium_bps is not None:
        ap = abs(premium_bps)
        p = 0 if ap < 5 else 1 if ap < 15 else 2 if ap < 40 else 3
        lines.append((_t("Premio perp max"), "{:+.0f} bps".format(premium_bps), p)); pts.append(p)
    if not pts:
        return None
    score = sum(pts); max_score = len(pts) * 3
    verdict = _verdict_bands(score, max_score, [_t("CRYPTO CALMO"), _t("CRYPTO NORMALE"), _t("CRYPTO SURRISCALDATO"), _t("CRYPTO EUFORICO")])
    return {"domain": "crypto", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "metrics": {"funding_ann_pct": fund_ann, "premium_bps": premium_bps}}


# parole ad alto impatto negativo per il news score
# Opus 4.8 15/07: NON CALIBRATA — vedi news_score(), la riga e' dichiarata n.d. e non
# assegna punti. Il contatore era dead code (text_blob sempre vuoto, fix a :_blob) e
# accendendolo sono emersi difetti misurati che NON si chiudono con una lista di parole:
#   - termini nudi -> falsi positivi: 'sec' 13/13 FP (boilerplate 13F "filing with the
#     SEC"), 'probe' 4/5 FP (inchiesta FIFA su Infantino attribuita a una banca del book), 'miss'
#     "Barrett to miss Ireland clash" (rugby), 'warning' "A-50U Early Warning Aircraft";
#   - bigrammi rigidi -> falsi negativi: \b...\b non tollera plurale ne' inversione, cosi'
#     "Price Target Cuts" e "Analyst cuts <ticker> price target" non matchano (misurato: 12/12
#     titoli reali persi, tra cui 4 tagli di target sulla stessa DAT del book a relevance 7-8);
#   - i 5 termini IT fanno 0 hit su 16.817 righe: il feed non ha testo italiano (7 righe).
# La taratura richiede pattern tolleranti + un flusso news ONESTO, che oggi non c'e': il
# volume dipende dalla quota provider (limiter -> [] muto), non dal mercato. Prima quello.
_NEWS_RISK_KW = ["lawsuit", "downgrade", "investigation", "fraud", "bankrupt",
                 "plunge", "selloff", "profit warning", "guidance cut", "dividend cut",
                 "job cuts", "price target cut", "earnings miss", "revenue miss",
                 "profit miss", "trading halt", "product recall", "declassa"]


@scoped_language
def news_score(portfolio_data=None, news_items=None, max_names=6):
    """Turbolenza dal flusso notizie sui nomi del book. PIU' ALTO = piu' eventi negativi/alto impatto."""
    items = news_items
    # Opus 4.8 16/07: copertura delle fonti, raccolta mentre si raccolgono gli item.
    # tool_search_news dichiara quali fonti erano mute (P0 15/07) e qui la dichiarazione
    # veniva BUTTATA VIA: il numero e il verdetto uscivano lo stesso, costruiti su un
    # flusso dimezzato. Con la quota esaurita ogni mattina alle ~08:36 il caso NON e'
    # teorico: e' lo stato della run settimanale. Un book cieco che stampa "FLUSSO CALMO"
    # e' il fallback silenzioso peggiore, perche' e' il Capo a narrarlo al PM.
    _mute = set()
    _fonti_candidate = set()   # fonti INTERROGATE (unione sui nomi): il denominatore vero
    _coperture = []
    if items is None:
        names = []
        try:
            positions = (portfolio_data or {}).get("positions") or []
            if not positions:
                from bellomberg.agents import agent_tools
                positions = (agent_tools.tool_get_portfolio_live() or {}).get("positions") or []
            names = [p.get("ticker") for p in sorted(positions, key=lambda x: -(x.get("peso_pct") or 0))][:max_names]
            from bellomberg.agents import agent_tools
            items = []
            for tk in names:
                r = agent_tools.tool_search_news(tk) if hasattr(agent_tools, "tool_search_news") else None
                if isinstance(r, dict):
                    items += (r.get("news") or [])
                    if r.get("error"):
                        # nome MAI cercato: non e' "coperto", e non va contato come tale
                        _coperture.append("NESSUNA")
                        continue
                    # chiave assente = nessuna fonte muta: contratto di tool_search_news
                    # (scrive 'copertura' solo dentro `if _mute:`). Vale oggi; se cambia,
                    # qui c'e' un lettore.
                    _coperture.append(r.get("copertura") or "PIENA")
                    _fm = r.get("fonti_mute") or []
                    # set.update() su una STRINGA itera i caratteri: "gnews" diventerebbe
                    # {'g','n','e','w','s'} e il memo stamperebbe "mute e, g, n, s, w".
                    _mute.update([_fm] if isinstance(_fm, str) else _fm)
                    _fonti_candidate.update(
                        f for f, s in (r.get("fonti") or {}).items() if s != "non_interrogata")
        except Exception:
            return None
    if not items:
        return None
    # Guardia ESPLICITA e ridondante: se ogni nome e' scoperto, items e' gia' vuoto e il
    # ramo 'if not items' qui sopra ha gia' ritornato None. Tenuta per dichiarare l'intento
    # (e per il caso in cui una fonte viva ritorni item senza coprire alcun nome).
    # NB: si ritorna None e NON un dict con score=None, perche' eventdesk_score fa
    # `score += n["score"]` e un None li' fa esplodere la run.
    if _coperture and all(c == "NESSUNA" for c in _coperture):
        return None
    # L'asse che conta per il PM non e' "quante fonti sono mute" ma "su quanti dei MIEI
    # nomi non ho guardato": 5 nomi ciechi su 6 e 1 su 6 danno lo stesso identico elenco
    # di fonti mute. Senza questo numero l'avviso rassicura invece di informare — ed e'
    # la parte che fundamentals_score dichiara ("N/M nomi valutati") e che qui mancava.
    _n_nomi = len(_coperture)
    _n_scoperti = sum(1 for c in _coperture if c != "PIENA")
    total = len(items)
    import re as _re
    # audit/11 §5: confini di parola — 'cut'/'miss' come substring matchavano anche
    # 'execute', 'haircut', 'commission', 'dismiss', 'missile' gonfiando l'allerta
    _pats = [_re.compile(r"\b" + _re.escape(kw) + r"\b", _re.IGNORECASE) for kw in _NEWS_RISK_KW]

    def _blob(it):
        # Opus 4.8 15/07: nel repo convivono 3 forme di item news e qui ne arrivava una
        # sola: tool_search_news (:363) emette titolo/descrizione (agent_tools.py:239-245),
        # il feed/briefing title/snippet. Leggere solo title/summary lasciava il testo
        # SEMPRE vuoto -> n_risk sempre 0 -> riga 'Eventi ad alto impatto neg.' morta.
        return (str(it.get("title") or it.get("titolo") or "") + " " +
                str(it.get("summary") or it.get("snippet") or it.get("descrizione") or ""))

    # Opus 4.8 15/07: si contano gli ITEM colpiti, non le occorrenze (un solo articolo
    # valeva fino a 8 "eventi", e 'profit warning' contava doppio matchando anche
    # 'warning'). Resta esposto in metrics come diagnostica, NON come punteggio.
    n_risk = sum(1 for it in items
                 if isinstance(it, dict) and any(p.search(_blob(it)) for p in _pats))
    lines = []; pts = []
    p = 0 if total < 8 else 1 if total < 20 else 2 if total < 40 else 3
    lines.append((_t("Volume notizie (book)"), str(total), p)); pts.append(p)
    # Opus 4.8 15/07 — riga DICHIARATA n.d. (regola PM 14/07: dichiarare, non stimare).
    # Il contatore keyword non e' calibrato (difetti misurati in _NEWS_RISK_KW) e il
    # flusso su cui girerebbe non e' onesto: quando il limiter esaurisce la quota
    # provider torna [] MUTO, quindi 'poche news' e 'sono cieco' oggi coincidono. Con
    # 0 punti i numeri del memo restano quelli di sempre, ma smettono di dichiarare
    # "nessun evento negativo" quando il dato non c'e'. Prima il limiter, poi la
    # taratura: MASTER_TODO "News igiene" (skip dichiarato) e "contatore eventi news".
    lines.append((_t("Eventi ad alto impatto neg."), _t("n.d. (contatore non calibrato)"), 0))
    pts.append(0)
    score = sum(pts); max_score = len(pts) * 3
    verdict = _verdict_bands(score, max_score, [_t("FLUSSO CALMO"), _t("FLUSSO NORMALE"), _t("FLUSSO INTENSO"), _t("ALLERTA NOTIZIE")])
    # COPERTURA DICHIARATA (Opus 4.8 16/07) — stesso rimedio di fundamentals_score:259-262:
    # il verdetto vale su cio' che abbiamo potuto guardare. Senza, "FLUSSO CALMO" su un book
    # con 3 fonti su 4 spente si legge come "mercato tranquillo" invece che "non lo so".
    # La riga entra in lines perche' e' cio' che finisce nel blocco iniettato allo
    # specialista; il verdetto lo rilegge eventdesk_score, che e' l'unica via verso il PDF.
    # Stringhe ASCII come nel resto del file (_log e' un print nudo: su console cp1252 un
    # em dash puo' sollevare UnicodeEncodeError).
    # 'PIENA' solo se le fonti le abbiamo interrogate NOI e hanno risposto: con news_items
    # passati dall'esterno la copertura e' IGNOTA, non piena.
    _cop = ("PARZIALE" if (_mute or any(c != "PIENA" for c in _coperture))
            else ("PIENA" if _coperture else "IGNOTA"))
    _n_fc = len(_fonti_candidate)
    if _cop == "PARZIALE":
        # Audit run 10/09 (Fable 5.1): "fonti mute su 6/6 nomi" e' stato letto da Event Desk
        # e Capo come "sei fonti su sei mute" (memo #53 e #54): le fonti mute erano DUE su
        # quattro, i 6/6 erano i NOMI. Prima le fonti, col loro rapporto; poi i nomi.
        # "scoperti" direbbe "ciechi del tutto": qui i nomi hanno fonti RIDOTTE, non zero.
        _det = _t("mute: ") + (", ".join(sorted(_mute)) if _mute else "n.d.")
        if _n_fc:
            _det += _t(" (%d/%d fonti)") % (len(_mute), _n_fc)
        _det += _t(" su %d/%d nomi") % (_n_scoperti, _n_nomi)
        lines.append((_t("Copertura fonti"), _t("PARZIALE - ") + _det, 0))
        verdict = _t("%s (copertura PARZIALE: %s - il volume NON misura il flusso reale)") % (
            verdict, _det)
    return {"domain": "news", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "metrics": {"n_news": total, "n_high_impact": n_risk,
                                        "copertura": _cop, "fonti_mute": sorted(_mute),
                                        "n_fonti_mute": len(_mute), "n_fonti_candidate": _n_fc,
                                        "n_nomi": _n_nomi, "n_nomi_scoperti": _n_scoperti}}


# topic di rischio geopolitico monitorati su Polymarket.
# Query rimisurate dal vivo l'11/09 col filtro qui sotto: "war conflict 2026", "iran israel
# strike" e "china taiwan tariff" non restituivano NESSUN mercato aperto pertinente fra i
# primi 8 risultati (solo mercati risolti e partite); queste trovano mercati veri e aperti
# ("Will the US officially declare war on Iran by December 31, 2026?", "Will China invade
# Taiwan by end of 2026?").
_POLI_TOPICS = {"recessione USA": "us recession 2026", "conflitto/guerra": "war 2026",
                "Iran-Israele": "iran war 2026", "Cina-Taiwan/dazi": "china taiwan 2026"}

# Audit run 10/09 (Fable 5.1, memo #54): il tool espande la query coi sinonimi e aggiunge i
# mercati piu' scambiati del giorno; qui si prendeva il massimo "Yes" fra TUTTI i risultati.
# Misurato dal vivo l'11/09: "recessione USA" -> 0,995 da una partita di calcio (Sevilla-
# Valencia), gli altri tre temi -> 1,0 da mercati gia' RISOLTI nel 2025/inizio 2026 (parole
# di un podcast, "Israel strikes Iran by February 28", dazi di aprile 2025), mentre il
# mercato vero "US recession by end of 2026?" stava a 0,07. Il cruscotto del memo #54 e'
# uscito "EVENTI CRITICI 78/100" su quattro 100% costruiti su nulla.
# Un mercato conta per un tema solo se: la sua DOMANDA parla del tema (ogni alternativa =
# tutti i termini presenti, a confine di parola), la data di chiusura e' futura, il prezzo
# non e' gia' 0/1 (risolto). I mercati di "menzione" (Will X say ...) sono scommesse sulle
# parole di un discorso, non rischio di coda. Fra i validi vince il PIU' SCAMBIATO, non il
# piu' alto: il numero del cruscotto e' quello che il mercato prezza davvero.
_POLI_TERMINI = {
    "recessione USA": (("recession",),),
    # niente "ceasefire": la probabilita' di una TREGUA misura la pace, non la coda
    "conflitto/guerra": (("war",), ("conflict",), ("invasion",), ("invade",), ("attack",)),
    # niente coppia nuda (iran, israel): "Will Israel reopen its embassy in Iran" non e' rischio
    "Iran-Israele": (("iran", "war"), ("iran", "strike"), ("iran", "attack"),
                     ("israel", "strike"), ("israel", "attack")),
    "Cina-Taiwan/dazi": (("china", "taiwan"), ("china", "tariff"), ("taiwan", "invade"),
                         ("taiwan", "invasion")),
}
_POLI_MENZIONE = ("say", "said", "mention", "mentions")


def _poli_termini_ok(question, alternative):
    """True se la domanda parla del tema. Un tema SENZA termini dichiarati non filtra
    (accetta ogni domanda che non sia di menzione): il vincolo lo mette chi scrive il tema."""
    import re as _re
    q = str(question or "").lower()
    if any(_re.search(r"\b" + w + r"\b", q) for w in _POLI_MENZIONE):
        return False
    if not alternative:
        return True
    # "s?" = plurale/terza persona: "strikes", "attacks", "invades", "conflicts"
    return any(all(_re.search(r"\b" + _re.escape(t) + r"s?\b", q) for t in alt) for alt in alternative)


def _poli_data_futura(end_date, adesso):
    from datetime import datetime as _dt, timezone as _tz
    s = str(end_date or "").strip()
    if not s:
        return False
    try:
        d = _dt.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=_tz.utc)
    return d > adesso


def _poli_scegli_mercato(risultati, alternative, adesso, esclusi=()):
    """(prob_yes, {question, end_date, volume_24h}, n_scartati) fra i mercati VALIDI del tema:
    vince il piu' scambiato (volume 24h), a parita' il Yes piu' alto. Nessun mercato valido
    -> (None, None, n_scartati): il tema esce n.d., non con un numero preso a caso.
    `esclusi` = domande gia' usate da un altro tema: un mercato conta per UN tema solo."""
    import json as _json
    scelto = None
    n_scartati = 0
    esclusi = {str(x).strip().lower() for x in (esclusi or ())}
    for ev in risultati or []:
        if not isinstance(ev, dict):
            continue
        mkts = ev.get("markets") if ev.get("markets") else [ev]
        for m in (mkts or []):
            if not isinstance(m, dict):
                continue
            outs = m.get("outcomes")
            prs = m.get("prices") or m.get("outcomePrices")
            try:
                if isinstance(outs, str):
                    outs = _json.loads(outs)
                if isinstance(prs, str):
                    prs = _json.loads(prs)
            except (ValueError, TypeError):
                n_scartati += 1
                continue
            if not outs or not prs:
                continue
            yes = None
            for o, pr in zip(outs, prs):
                if str(o).lower() in ("yes", "si", "sì"):
                    try:
                        yes = _finite_number(float(pr))
                    except (TypeError, ValueError):
                        yes = None
            if yes is None:
                n_scartati += 1
                continue
            question = m.get("question") or ev.get("title")
            end_date = m.get("end_date") or ev.get("end_date")
            if (not (0.0 < yes < 1.0) or not _poli_data_futura(end_date, adesso)
                    or not _poli_termini_ok(question, alternative)
                    or str(question or "").strip().lower() in esclusi):
                n_scartati += 1
                continue
            vol = _finite_number(m.get("volume_24h"))
            if vol is None:
                vol = _finite_number(ev.get("volume_24h"))
            chiave = (vol if vol is not None else -1.0, yes)
            if scelto is None or chiave > scelto[0]:
                scelto = (chiave, yes, {"question": str(question)[:160], "end_date": end_date,
                                        "volume_24h": vol})
    if scelto is None:
        return None, None, n_scartati
    return scelto[1], scelto[2], n_scartati


@scoped_language
def politics_score(events_by_topic=None):
    """Rischio geopolitico dai prezzi dei prediction market. PIU' ALTO = piu' probabilita' di coda.
    metrics.mercati dice QUALE domanda ha dato il numero; metrics.non_calcolabili i temi senza
    un mercato aperto pertinente (dichiarati anche in lines, a zero punti e fuori dal massimo)."""
    probs = events_by_topic
    mercati = {}
    non_calc = {}
    scartati = {}
    if probs is None:
        probs = {}
        try:
            from bellomberg.agents import agent_tools
            from datetime import datetime as _dt, timezone as _tz
            adesso = _dt.now(_tz.utc)
            usati = set()
            for label, q in _POLI_TOPICS.items():
                try:
                    r = agent_tools.tool_get_polymarket_events(q, max_results=8)
                    r = r if isinstance(r, dict) else {}
                    risultati = r.get("results") or r.get("events") or r.get("markets") or []
                    prob, info, n_sc = _poli_scegli_mercato(risultati, _POLI_TERMINI.get(label, ()),
                                                            adesso, esclusi=usati)
                except Exception as e:
                    non_calc[label] = "tool non disponibile (%s)" % type(e).__name__
                    continue
                scartati[label] = n_sc
                if prob is None:
                    if risultati or n_sc:
                        non_calc[label] = "nessun mercato aperto pertinente (%d scartati)" % n_sc
                    else:
                        non_calc[label] = str(r.get("error") or "nessun risultato dal tool")[:120]
                    continue
                probs[label] = prob
                mercati[label] = info
                usati.add(str(info.get("question") or "").strip().lower())
        except Exception:
            return None
    probs = {k: v for k, raw in (probs or {}).items()
             if (v := _finite_number(raw)) is not None and 0 <= v <= 100}
    if not probs:
        return None
    lines = []; pts = []
    for label, pr in probs.items():
        pct = pr * 100 if pr <= 1 else pr
        p = 0 if pct < 10 else 1 if pct < 25 else 2 if pct < 45 else 3
        lines.append((label, "{:.0f}%".format(pct), p)); pts.append(p)
    if not pts:
        return None
    # temi senza mercato: riga DICHIARATA a zero punti, fuori dal massimo (regola 14/07)
    for label, motivo in non_calc.items():
        lines.append((label + " (n.d.)", motivo[:60], 0))
    score = sum(pts); max_score = len(pts) * 3
    verdict = _verdict_bands(score, max_score, [_t("RISCHIO GEOPOL. BASSO"), _t("RISCHIO MODERATO"),
                                                _t("RISCHIO ELEVATO"), _t("RISCHIO ACUTO")])
    return {"domain": "politics", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "metrics": {"topics": {k: round(v, 3) for k, v in probs.items()},
                                        "mercati": mercati, "non_calcolabili": non_calc,
                                        "scartati": scartati}}


@scoped_language
def eventdesk_score(portfolio_data=None):
    """Score EVENT DESK (fusione News+Politics 15/07): turbolenza dal flusso
    notizie + rischio di coda dai prediction market in UNA ancora.
    Meta' non calcolabile = riga DICHIARATA (mai buchi silenziosi)."""
    try:
        n = news_score(portfolio_data)
    except Exception:
        n = None
    try:
        p = politics_score()
    except Exception:
        p = None
    if not n and not p:
        return None
    lines = []; score = 0; max_score = 0
    if n:
        lines += [("NEWS | " + l, v, pt) for (l, v, pt) in n["lines"]]
        score += n["score"]; max_score += n["max_score"]
    else:
        lines.append((_t("NEWS | score non calcolabile (dichiarato)"), "n.d.", 0))
    if p:
        lines += [("GEO | " + l, v, pt) for (l, v, pt) in p["lines"]]
        score += p["score"]; max_score += p["max_score"]
    else:
        lines.append((_t("GEO | score non calcolabile (dichiarato)"), "n.d.", 0))
    if max_score <= 0:
        return None
    verdict = _verdict_bands(score, max_score, [_t("EVENTI CALMI"), _t("EVENTI IN FERMENTO"),
                                                _t("EVENTI CALDI"), _t("EVENTI CRITICI")])
    # Opus 4.8 16/07: la copertura news arriva al PM SOLO da qui. news_score la dichiara nel
    # suo verdict, ma quel verdict non lo legge nessuno: qui sotto si ricalcola il proprio, e
    # collect_scoreboard -> PDF/Capo prende solo verdict/score/max_score di 'eventdesk'.
    # Senza queste righe la dichiarazione moriva nel prompt del solo specialista.
    # AGGRAVANTE che rende la riga necessaria: la cecita' ABBASSA lo score (meno fonti ->
    # meno volume -> meno punti), quindi un book scoperto si colora di VERDE nel cruscotto.
    # Forma CORTA di proposito: la colonna Verdetto del PDF e' 7.4cm (~203pt utili), il font
    # e' Helvetica-Bold 8.5 e le celle stringa NON vanno a capo -> un testo lungo sborda
    # sulla colonna accanto. Misurato col font vero, caso peggiore "EVENTI IN FERMENTO":
    # " (news: fonti mute su 6/6 nomi)" = 216pt SBORDA; " (news: 2/4 fonti mute)" = 181pt
    # (misurato 11/09), 22pt di margine. Il dettaglio (quali fonti) resta in lines e metrics.
    # Audit run 10/09 (Fable 5.1): la forma precedente " (news: fonti mute 6/6)" contava i
    # NOMI e il PM l'ha letta come "sei fonti mute su sei". Ora si contano le FONTI.
    _mn = (n or {}).get("metrics") or {}
    if _mn.get("copertura") == "PARZIALE":
        _nf, _nc = _mn.get("n_fonti_mute"), _mn.get("n_fonti_candidate")
        if _nf and _nc:
            verdict += _t(" (news: %d/%d fonti mute)") % (_nf, _nc)
        else:
            verdict += _t(" (news: fonti mute, dettaglio in righe)")
    elif _mn.get("copertura") == "IGNOTA":
        verdict += _t(" (news: copertura ignota)")
    return {"domain": "eventdesk", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines,
            "metrics": {"news": (n or {}).get("metrics"), "politics": (p or {}).get("metrics")}}


@scoped_language
def format_score_block(score: dict) -> str:
    """Blocco testo compatto da iniettare nel contesto dello specialista."""
    if not score:
        return ""
    L = []
    L.append(_t("=== SCORE DETERMINISTICO ({}) — calcolato in codice, parti da QUESTO ===").format(score["domain"].upper()))
    L.append(_t("Verdetto: {} ({}/{} punti rischio; piu' alto = piu' rischio).").format(
        score["verdict"], score["score"], score["max_score"]))
    for label, val, pt in score["lines"]:
        flag = ["ok", _t("attenzione"), _t("alto"), _t("critico")][min(pt, 3)]
        L.append(_t("  - {:<26} {:>8}  -> {} punti ({})").format(label, val, pt, flag))
    L.append(_t("Usa questi numeri come base fattuale: spiega COSA implicano e DOVE intervenire. "
             "Le metriche di performance (Sharpe, maxDD) sono TRAILING: fotografano il passato, "
             "non lo predicono — su titoli molto scesi dichiarane il limite (dottrina bilaterale "
             "PM 16/07) invece di trattarle come verdetto. "
             "Se chiami i tool e trovi numeri diversi, dichiara la discrepanza."))
    return "\n".join(L)


if __name__ == "__main__":
    # mock realistico su simboli INVENTATI (05/09, lotto 5b): stessi pesi di prima
    pdj = {"positions": [
        {"ticker": "THETA.L", "valore_mercato_eur": 30400}, {"ticker": "ZETA.MI", "valore_mercato_eur": 13300},
        {"ticker": "ALFA", "valore_mercato_eur": 11100}, {"ticker": "KRYPTO", "valore_mercato_eur": 5800},
        {"ticker": "OMEGA", "valore_mercato_eur": 18000}, {"ticker": "KAPPA.MI", "valore_mercato_eur": 9800}]}
    risk = {"portfolio": {"vol_annual_pct": 22.5, "sharpe": 0.78, "beta_vs_spy": 1.18,
                          "var_95_1d_pct": -3.2, "max_dd_1y_pct": -17.4}}
    s = quant_score(pdj, risk)
    import json
    print(json.dumps({k: v for k, v in s.items() if k != "lines"}, indent=2))
    print()
    print(format_score_block(s))


# ============================================================
# CRUSCOTTO: sintesi di tutti gli score per Capo + memo (#186b)
# ============================================================
_SCORE_LABELS = {"macro": "Macro / Regime", "quant": "Rischio book", "fundamentals": "Valutazione",
                 "options": "Volatilita'", "crypto": "Crypto", "eventdesk": "Eventi & Geopolitica",
                 "news": "Notizie", "politics": "Geopolitica"}  # news/politics: chiavi legacy pre-fusione (memo vecchi)
_SCORE_ORDER = ["macro", "quant", "fundamentals", "options", "crypto", "eventdesk", "news", "politics"]
# Domini VIVI del comitato (= R1_STAGES). news/politics restano in _SCORE_ORDER solo per
# rendere i memo vecchi, ma non vanno dichiarati n.d. se mancano: sono fusi in eventdesk.
_SCORE_LIVE = ["macro", "quant", "fundamentals", "options", "crypto", "eventdesk"]


@scoped_language
def collect_scoreboard(cache):
    """Da blackboard.data['_score_cache'] -> lista ordinata di righe (label, verdict, score, max).

    Un dominio VIVO che non produce score NON sparisce: esce DICHIARATO n.d.
    (regola no-fallback-silenziosi). Prima veniva filtrato via e il cruscotto
    mostrava 4 domini su 6 senza dire che gli altri due mancavano.
    """
    rows = []
    if not isinstance(cache, dict):
        return rows
    for k in _SCORE_ORDER:
        sc = cache.get(k)
        if not (sc and isinstance(sc, dict) and sc.get("verdict")) and k in _SCORE_LIVE:
            # copre sia la chiave assente sia la chiave presente con valore None
            # (base.py cachea anche i None, regenerate_memo no)
            rows.append({"key": k, "label": _t(_SCORE_LABELS[k]),
                         "verdict": _t("n.d. - score non calcolabile (dichiarato)"),
                         "score": None, "max_score": None})
            continue
        if sc and isinstance(sc, dict) and sc.get("verdict"):
            rows.append({"key": k, "label": _t(_SCORE_LABELS[k]), "verdict": sc.get("verdict"),
                         "score": sc.get("score"), "max_score": sc.get("max_score")})
    return rows


@scoped_language
def format_scoreboard(cache):
    """Blocco testo per il contesto del Capo."""
    rows = collect_scoreboard(cache)
    if not rows:
        return ""
    L = [_t("=== CRUSCOTTO SCORING DETERMINISTICO (calcolato in codice dagli specialisti) ==="),
         _t("Indice 0-100 = score/massimo (piu' alto = piu' rischio). CONFRONTA i domini SOLO "
         "sull'indice: il massimo grezzo cambia col numero di metriche disponibili per "
         "specialista (9/18 e 8/21 valgono 50 e 38, non 'quasi uguale'). "
         "Bande: <25 basso, 25-50 medio, 50-72 elevato, >=72 critico.")]
    for r in rows:
        mx = r.get("max_score") or 0
        idx = "{:.0f}/100".format(100.0 * r["score"] / mx) if mx else "n.d."
        # senza guardia le righe dichiarate n.d. stamperebbero "(grezzo None/None)":
        # il Capo leggerebbe un artefatto invece di un buco
        grezzo = _t("  (grezzo {}/{})").format(r["score"], r["max_score"]) if mx else ""
        L.append("  {:<16} {:<34} {:>7}{}".format(
            r["label"], r["verdict"] or "", idx, grezzo))
    L.append(_t("DEVI sintetizzare questo cruscotto in una sezione dedicata del memo ('Cruscotto di rischio'): "
             "cosa dicono gli score nel loro insieme (regime macro, rischio del book, valutazione, volatilita', "
             "geopolitica) e come orientano concretamente le decisioni della settimana."))
    return "\n".join(L)
