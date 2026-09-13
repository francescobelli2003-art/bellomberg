"""
Charts Agent: post-processor che genera UN PDF separato con TUTTI i grafici quantitativi.

NON e' un LLM agent - e' un modulo Python che legge il blackboard dei specialisti
+ portfolio data + macro data e produce ~15-18 grafici stile Citadel dashboard
componendoli in un PDF appendice 4-5 pagine A4.

Uso:
    from charts_agent import build_quant_appendix
    pdf_path = build_quant_appendix(blackboard, portfolio_data, macro_data, options_dict)
"""
import os
import re
from datetime import datetime
from bellomberg.reporting.i18n import label as _t, number as _n, localized, date_label
from bellomberg.storage.memory_db import REPORT_DIR   # B4 (02/09): report/ ancorato al repo
from bellomberg.reporting.style_research import (  # #178: stile research light (era style_citadel dark)
    COLORS, SEQ, apply_citadel_style, add_branding, add_footer,
    colorize_bar_by_sign, fmt_eur,
)

try:
    import matplotlib.pyplot as plt
    import numpy as np
    MPL_AVAILABLE = True
except ImportError:
    MPL_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image,
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


CHART_DIR = os.path.join(REPORT_DIR, "quant_charts")


def _ensure_dirs():
    os.makedirs(CHART_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)


def _save(fig, name):
    path = os.path.join(CHART_DIR, name + ".png")
    fig.savefig(path, dpi=130, bbox_inches="tight", facecolor=COLORS["bg"])
    plt.close(fig)
    return path


# ============================================================
# CHART GENERATORS
# ============================================================

@localized
def chart_portfolio_treemap(positions):
    """Allocation treemap stile Citadel."""
    if not MPL_AVAILABLE or not positions:
        return None
    apply_citadel_style()
    try:
        import squarify  # optional
        SQ = True
    except ImportError:
        SQ = False

    valid = [p for p in positions if p.get("peso_pct")]
    valid.sort(key=lambda x: x["peso_pct"] or 0, reverse=True)
    top = valid[:15]
    labels = [(p.get("ticker", "?")[:10] + "\n" + "{:.1f}%".format(p.get("peso_pct") or 0)) for p in top]
    sizes = [p.get("peso_pct") or 0 for p in top]
    colors = [SEQ[i % len(SEQ)] for i in range(len(top))]

    fig, ax = plt.subplots(figsize=(11, 6.5))
    if SQ:
        squarify.plot(sizes=sizes, label=labels, color=colors,
                      alpha=0.92, text_kwargs={"fontsize": 10, "color": COLORS["bg"], "fontweight": "bold"},
                      ax=ax, bar_kwargs={"edgecolor": COLORS["bg"], "linewidth": 2})
        ax.axis("off")
    else:
        # Fallback pie
        ax.pie(sizes, labels=labels, colors=colors, autopct="",
               wedgeprops={"edgecolor": COLORS["bg"], "linewidth": 2},
               textprops={"fontsize": 9, "color": COLORS["fg"]})
    add_branding(fig, _t("Portfolio Allocation - Treemap"), _t("Weight % per position"))
    add_footer(fig)
    return _save(fig, "01_portfolio_treemap")


@localized
def chart_pl_bar(positions):
    """P/L EUR per posizione."""
    if not MPL_AVAILABLE or not positions:
        return None
    apply_citadel_style()
    valid = [p for p in positions if p.get("pl_eur") is not None]
    if not valid:
        return None
    valid.sort(key=lambda x: x.get("pl_eur") or 0, reverse=True)
    labels = [p.get("ticker", "?")[:10] for p in valid]
    vals = [p.get("pl_eur") or 0 for p in valid]
    colors = colorize_bar_by_sign(vals)

    fig, ax = plt.subplots(figsize=(11, max(4, len(valid) * 0.35)))
    ax.barh(labels, vals, color=colors, alpha=0.88, edgecolor=COLORS["panel"], linewidth=0.5)
    for i, v in enumerate(vals):
        ax.text(v, i, " " + fmt_eur(v), va="center", fontsize=8, fontweight="bold",
                color=COLORS["green"] if v >= 0 else COLORS["red"])
    ax.axvline(0, color=COLORS["muted"], linewidth=0.6)
    ax.set_xlabel(_t("P/L (EUR)"))
    add_branding(fig, _t("P/L by Position"), _t("EUR - sorted descending"))
    add_footer(fig)
    return _save(fig, "02_pl_per_position")


@localized
def chart_correlation_heatmap(correlation_data, tickers):
    """Heatmap correlazione top 10 - dati estratti da Quant blackboard se presenti."""
    if not MPL_AVAILABLE or not correlation_data or not tickers:
        return None
    apply_citadel_style()
    n = len(tickers)
    matrix = np.zeros((n, n))
    for i, ta in enumerate(tickers):
        for j, tb in enumerate(tickers):
            if ta == tb:
                matrix[i, j] = 1.0
            else:
                v = correlation_data.get(ta, {}).get(tb)
                if v is None:
                    v = correlation_data.get(tb, {}).get(ta, 0)
                matrix[i, j] = v

    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(matrix, cmap="RdYlBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(n)); ax.set_yticks(range(n))
    ax.set_xticklabels(tickers, rotation=45, ha="right", color=COLORS["fg"])
    ax.set_yticklabels(tickers, color=COLORS["fg"])
    for i in range(n):
        for j in range(n):
            ax.text(j, i, "{:.2f}".format(matrix[i, j]),
                    ha="center", va="center", color="black" if abs(matrix[i,j]) > 0.5 else COLORS["fg"],
                    fontsize=8)
    plt.colorbar(im, ax=ax, label="Correlation")
    add_branding(fig, _t("Correlation Matrix"), _t("Pair-wise correlations - 6mo returns"))
    add_footer(fig)
    return _save(fig, "03_correlation_heatmap")


@localized
def chart_yield_curve(macro_data):
    """Yield curve Fed/2Y/10Y."""
    if not MPL_AVAILABLE or not macro_data:
        return None
    apply_citadel_style()
    ind = macro_data.get("indicators", {})
    points = []
    for label, key in [("Fed Funds", "fed_funds_rate"), ("2Y", "2y_treasury"), ("10Y", "10y_treasury")]:
        v = ind.get(key, {}).get("value")
        if v is not None:
            points.append((label, v))
    if len(points) < 2:
        return None
    labels = [p[0] for p in points]
    vals = [p[1] for p in points]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(labels, vals, marker="o", linewidth=3, markersize=14, color=COLORS["gold"])
    ax.fill_between(range(len(labels)), vals, alpha=0.18, color=COLORS["gold"])
    for i, (l, v) in enumerate(zip(labels, vals)):
        ax.annotate("{:.2f}%".format(v), xy=(i, v), xytext=(0, 14),
                    textcoords="offset points", ha="center", fontweight="bold",
                    color=COLORS["gold_light"], fontsize=11)
    ax.set_ylabel(_t("Yield (%)"))
    status = macro_data.get("yield_curve_status", "")
    add_branding(fig, _t("US Yield Curve"), status)
    add_footer(fig)
    return _save(fig, "04_yield_curve")


@localized
def chart_macro_dashboard_bars(macro_data):
    """Bar chart orizzontale indicatori macro key."""
    if not MPL_AVAILABLE or not macro_data:
        return None
    apply_citadel_style()
    ind = macro_data.get("indicators", {})
    keys = ["fed_funds_rate", "10y_treasury", "2y_treasury", "vix_close",
            "us_unemployment", "wti_oil", "high_yield_spread", "ig_credit_spread"]
    rows = []
    for k in keys:
        d = ind.get(k, {})
        v = d.get("value")
        if v is not None:
            label = d.get("description", k)[:38]
            rows.append((label, v))
    if not rows:
        return None
    rows.sort(key=lambda x: x[1])
    labels = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    fig, ax = plt.subplots(figsize=(10, max(4, len(rows) * 0.5)))
    ax.barh(labels, vals, color=COLORS["cyan"], alpha=0.82, edgecolor=COLORS["cyan_dark"], linewidth=1)
    for i, v in enumerate(vals):
        ax.text(v, i, "  {:.2f}".format(v), va="center", fontweight="bold", fontsize=10, color=COLORS["gold_light"])
    add_branding(fig, _t("Macro Indicators Dashboard"), _t("FRED St. Louis Fed - latest readings"))
    add_footer(fig)
    return _save(fig, "05_macro_indicators")


@localized
def chart_concentration_top_positions(positions):
    """Bar verticale: top 10 posizioni per peso."""
    if not MPL_AVAILABLE or not positions:
        return None
    apply_citadel_style()
    valid = [p for p in positions if p.get("peso_pct")]
    valid.sort(key=lambda x: x["peso_pct"] or 0, reverse=True)
    top = valid[:10]
    labels = [p.get("ticker", "?")[:10] for p in top]
    weights = [p.get("peso_pct") or 0 for p in top]
    fig, ax = plt.subplots(figsize=(11, 5))
    bars = ax.bar(labels, weights, color=COLORS["gold"], alpha=0.85, edgecolor=COLORS["panel"], linewidth=1)
    for b, w in zip(bars, weights):
        ax.text(b.get_x() + b.get_width()/2, b.get_height(), "{:.1f}%".format(w),
                ha="center", va="bottom", color=COLORS["gold_light"], fontweight="bold", fontsize=9)
    ax.set_ylabel(_t("Weight %"))
    ax.axhline(10, color=COLORS["red"], linewidth=0.8, linestyle="--", alpha=0.7, label=_t("10% concentration threshold"))
    ax.axhline(20, color=COLORS["red"], linewidth=1.2, linestyle="--", alpha=0.9, label=_t("20% high concentration"))
    ax.legend()
    add_branding(fig, _t("Top 10 Positions - Concentration Check"), _t("Single-position weights vs threshold"))
    add_footer(fig)
    return _save(fig, "06_concentration_top")


@localized
def chart_macro_evolution(macro_data, indicator_key, indicator_label):
    """Time series storia 12 mesi per UN indicatore (es. CPI, VIX)."""
    if not MPL_AVAILABLE or not macro_data:
        return None
    apply_citadel_style()
    # Macro dashboard non ha la storia inline - prendiamo cosa abbiamo
    # Per simulare uso solo il latest se la history non e' disponibile
    ind = macro_data.get("indicators", {}).get(indicator_key, {})
    if not ind:
        return None
    # Plot bar singolo se non c'e' storia
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar([0], [ind.get("value", 0)], color=COLORS["cyan"], width=0.5)
    ax.set_xticks([0])
    ax.set_xticklabels([ind.get("date", "")[:10]])
    ax.set_ylabel(indicator_label)
    add_branding(fig, indicator_label + _t(" - Latest"), ind.get("description", ""))
    add_footer(fig)
    return _save(fig, "07_macro_evol_" + indicator_key)


@localized
def chart_options_oi_summary(options_dict):
    """Sintesi OI calls vs puts per ogni ticker analizzato."""
    if not MPL_AVAILABLE or not options_dict:
        return None
    apply_citadel_style()
    rows = []
    for ticker, opt in options_dict.items():
        if not opt or "error" in opt:
            continue
        calls = opt.get("total_call_oi", 0)
        puts = opt.get("total_put_oi", 0)
        if calls or puts:
            rows.append((ticker, calls, puts))
    if not rows:
        return None
    rows.sort(key=lambda x: x[1] + x[2], reverse=True)
    tickers = [r[0] for r in rows]
    calls = [r[1] for r in rows]
    puts = [r[2] for r in rows]
    x = np.arange(len(tickers))
    w = 0.4
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(x - w/2, calls, width=w, color=COLORS["green"], label="Call OI", alpha=0.85)
    ax.bar(x + w/2, puts, width=w, color=COLORS["red"], label="Put OI", alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(tickers)
    ax.set_ylabel(_t("Open Interest"))
    ax.legend()
    add_branding(fig, _t("Options Open Interest"), _t("Call vs Put per ticker analyzed"))
    add_footer(fig)
    return _save(fig, "08_options_oi")


@localized
def chart_options_iv_pc(options_dict):
    """ATM IV + Put/Call ratio per ticker."""
    if not MPL_AVAILABLE or not options_dict:
        return None
    apply_citadel_style()
    rows = []
    for ticker, opt in options_dict.items():
        if not opt or "error" in opt:
            continue
        iv = opt.get("atm_iv_call_pct")
        pc = opt.get("put_call_oi_ratio")
        if iv is not None and pc is not None:
            rows.append((ticker, iv, pc))
    if not rows:
        return None
    tickers = [r[0] for r in rows]
    ivs = [r[1] for r in rows]
    pcs = [r[2] for r in rows]
    x = np.arange(len(tickers))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    ax1.bar(x, ivs, color=COLORS["gold"], alpha=0.85)
    ax1.set_ylabel("ATM IV (%)")
    ax1.set_title(_t("ATM Implied Volatility"), color=COLORS["gold"], fontweight="bold")
    for xi, vi in zip(x, ivs):
        ax1.text(xi, vi, "{:.1f}%".format(vi), ha="center", va="bottom", color=COLORS["gold_light"], fontsize=9)
    pc_colors = [COLORS["red"] if p > 1 else COLORS["green"] for p in pcs]
    ax2.bar(x, pcs, color=pc_colors, alpha=0.85)
    ax2.axhline(1.0, color=COLORS["white"], linestyle="--", linewidth=1, alpha=0.7, label=_t("Neutral 1.0"))
    ax2.set_ylabel(_t("Put/Call OI Ratio"))
    ax2.set_title(_t("Put/Call Ratio (>1 = bearish positioning)"), color=COLORS["gold"], fontweight="bold")
    ax2.set_xticks(x); ax2.set_xticklabels(tickers)
    ax2.legend()
    for xi, vi in zip(x, pcs):
        ax2.text(xi, vi, "{:.2f}".format(vi), ha="center", va="bottom", color=COLORS["fg"], fontsize=9)
    fig.suptitle(_t("Options Positioning Snapshot"), color=COLORS["gold"], fontweight="bold", fontsize=15)
    add_footer(fig)
    return _save(fig, "09_options_iv_pc")


@localized
def chart_options_max_pain_distance(options_dict):
    """Distance spot vs max pain per ticker."""
    if not MPL_AVAILABLE or not options_dict:
        return None
    apply_citadel_style()
    rows = []
    for ticker, opt in options_dict.items():
        if not opt or "error" in opt:
            continue
        d = opt.get("max_pain_vs_spot_pct")
        if d is not None:
            rows.append((ticker, d))
    if not rows:
        return None
    rows.sort(key=lambda x: x[1])
    tickers = [r[0] for r in rows]
    dists = [r[1] for r in rows]
    colors = colorize_bar_by_sign(dists)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.barh(tickers, dists, color=colors, alpha=0.85)
    for i, v in enumerate(dists):
        ax.text(v, i, "  {:+.1f}%".format(v), va="center", color=COLORS["gold_light"], fontweight="bold", fontsize=9)
    ax.axvline(0, color=COLORS["muted"], linewidth=0.8)
    ax.set_xlabel(_t("Distance Spot vs Max Pain (%)"))
    add_branding(fig, _t("Max Pain Gravitational Pull"), _t("Negative = spot above max pain (gravity down)"))
    add_footer(fig)
    return _save(fig, "10_max_pain")


@localized
def chart_polymarket_probs(politics_report_text):
    """Estrai probabilita Polymarket dal report Politics."""
    if not MPL_AVAILABLE or not politics_report_text:
        return None
    apply_citadel_style()
    # Regex per trovare "X%" pattern + nome evento
    # Cerco righe del tipo "Fed 0-cuts 2026 66%" o "Iran deal Jun 30 40%"
    pattern = re.compile(r"([A-Z][A-Za-z 0-9\-/]{5,50})\s+(\d{1,2})%")
    matches = pattern.findall(politics_report_text)
    events = []
    seen = set()
    for name, prob in matches[:8]:
        name = name.strip()
        if name in seen:
            continue
        seen.add(name)
        try:
            events.append((name[:35], int(prob)))
        except ValueError:
            continue
    if not events:
        return None
    events.sort(key=lambda x: x[1], reverse=True)
    labels = [e[0] for e in events]
    probs = [e[1] for e in events]
    fig, ax = plt.subplots(figsize=(11, max(4, len(events) * 0.5)))
    bars = ax.barh(labels, probs, color=COLORS["purple"], alpha=0.82, edgecolor=COLORS["gold"], linewidth=0.8)
    for b, p in zip(bars, probs):
        ax.text(p, b.get_y() + b.get_height()/2, "  {}%".format(p),
                va="center", color=COLORS["gold_light"], fontweight="bold", fontsize=10)
    ax.set_xlim(0, 100)
    ax.set_xlabel(_t("Implied Probability %"))
    add_branding(fig, _t("Polymarket Implied Probabilities"), _t("From Politics Specialist report"))
    add_footer(fig)
    return _save(fig, "11_polymarket")


def _bucket_settori(positions):
    """Aggrega il settore consegnato dai dati; l'assenza resta visibile."""
    buckets = {}
    for posizione in positions:
        settore = (posizione.get("sector") or posizione.get("settore") or "").strip()
        if not settore:
            settore = _t("n.d. (settore assente)")
        peso = float(posizione.get("peso_pct") or 0)
        buckets[settore] = buckets.get(settore, 0.0) + peso
    return buckets


@localized
def chart_sector_breakdown(positions):
    """Breakdown per il settore presente nei dati della posizione."""
    if not MPL_AVAILABLE or not positions:
        return None
    apply_citadel_style()
    buckets = _bucket_settori(positions)

    items = [(k, v) for k, v in buckets.items() if v > 0]
    items.sort(key=lambda x: x[1], reverse=True)
    labels = [i[0] for i in items]
    vals = [i[1] for i in items]
    colors_palette = [SEQ[i % len(SEQ)] for i in range(len(items))]
    fig, ax = plt.subplots(figsize=(10, 6))
    wedges, texts, autotexts = ax.pie(vals, labels=labels, colors=colors_palette,
                                      autopct="%1.1f%%", startangle=90,
                                      wedgeprops={"edgecolor": COLORS["bg"], "linewidth": 2.5},
                                      textprops={"color": COLORS["fg"], "fontsize": 10, "fontweight": "bold"})
    for at in autotexts:
        at.set_color(COLORS["bg"])
        at.set_fontweight("bold")
    add_branding(fig, _t("Sector Breakdown"), _t("Aggregate weight from position sector data"))
    add_footer(fig)
    return _save(fig, "12_geo_breakdown")


@localized
def chart_risk_summary_panel(quant_report_text, portfolio_data):
    """Panel sintetico delle metriche risk estratte dal Quant report."""
    if not MPL_AVAILABLE:
        return None
    apply_citadel_style()
    # Estrai Sharpe values da Quant report
    sharpe_pattern = re.compile(r"([A-Z]{2,8}(?:\.[A-Z]{2})?)\s*:?\s*Sharpe\s*[=:]?\s*(-?\d+\.\d+)", re.IGNORECASE)
    matches = sharpe_pattern.findall(quant_report_text or "")
    sharpes = []
    seen = set()
    for ticker, val in matches[:12]:
        if ticker.upper() in seen:
            continue
        seen.add(ticker.upper())
        try:
            sharpes.append((ticker.upper(), float(val)))
        except ValueError:
            continue
    if not sharpes:
        # Mostra solo NAV panel
        return None
    sharpes.sort(key=lambda x: x[1])
    tickers = [s[0] for s in sharpes]
    vals = [s[1] for s in sharpes]
    colors = [COLORS["green"] if v > 0 else COLORS["red"] for v in vals]
    fig, ax = plt.subplots(figsize=(10, max(4, len(sharpes) * 0.45)))
    ax.barh(tickers, vals, color=colors, alpha=0.85)
    for i, v in enumerate(vals):
        ax.text(v, i, "  {:+.2f}".format(v), va="center", color=COLORS["gold_light"], fontweight="bold", fontsize=10)
    ax.axvline(0, color=COLORS["muted"], linewidth=0.8)
    ax.axvline(1.0, color=COLORS["green"], linewidth=0.6, linestyle="--", alpha=0.7, label=_t("Sharpe 1.0 baseline"))
    ax.legend()
    ax.set_xlabel(_t("Sharpe Ratio (6mo annualized)"))
    add_branding(fig, _t("Sharpe Ratio per Position"), _t("From Quant Specialist analysis"))
    add_footer(fig)
    return _save(fig, "13_sharpe_positions")


@localized
def chart_news_count(news_report_text):
    """Sintesi conteggio news per ticker estratti dal News report."""
    if not MPL_AVAILABLE or not news_report_text:
        return None
    apply_citadel_style()
    # Cerca pattern "[ticker]" oppure menzioni multiple
    tickers_count = {}
    pattern = re.compile(r"\b([A-Z]{2,6})(?:\.MI|\.L|\.AS|\.DE)?\b")
    for tok in pattern.findall(news_report_text):
        if tok in ("CET", "USD", "EUR", "GBP", "JPY", "USA", "EU", "UK", "API", "JPM", "PDF", "PE", "BLUF", "ETF", "OI", "IV", "PC"):
            continue
        tickers_count[tok] = tickers_count.get(tok, 0) + 1
    items = sorted(tickers_count.items(), key=lambda x: x[1], reverse=True)[:10]
    if not items:
        return None
    labels = [i[0] for i in items]
    counts = [i[1] for i in items]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.barh(labels, counts, color=COLORS["cyan"], alpha=0.85)
    for i, v in enumerate(counts):
        ax.text(v, i, "  " + str(v), va="center", color=COLORS["gold_light"], fontweight="bold")
    ax.set_xlabel(_t("Mentions in News Report"))
    add_branding(fig, _t("News Coverage Density"), _t("Top tickers mentioned in News Specialist report"))
    add_footer(fig)
    return _save(fig, "14_news_density")


# ============================================================
# PDF COMPOSITION
# ============================================================

@localized
def build_quant_appendix(blackboard=None, portfolio_data=None, macro_data=None,
                        options_data_dict=None, correlation_data=None,
                        output_path=None):
    """
    Genera il PDF Appendice Quantitativo completo.
    Lo compone con i grafici disponibili (skippa quelli senza dati).
    """
    if not MPL_AVAILABLE or not REPORTLAB_AVAILABLE:
        return None

    _ensure_dirs()
    if not output_path:
        output_path = os.path.join(REPORT_DIR,
                                    "quant_appendix_" + datetime.now().strftime("%Y%m%d_%H%M") + ".pdf")

    # Estrai report specialisti per chart che leggono testo
    quant_text = ""
    news_text = ""
    politics_text = ""
    if blackboard:
        bb_data = blackboard.data if hasattr(blackboard, "data") else blackboard
        quant_text = "\n".join(str(v) for v in bb_data.get("quant", {}).values()) if isinstance(bb_data.get("quant"), dict) else ""
        news_text = "\n".join(str(v) for v in bb_data.get("news", {}).values()) if isinstance(bb_data.get("news"), dict) else ""
        politics_text = "\n".join(str(v) for v in bb_data.get("politics", {}).values()) if isinstance(bb_data.get("politics"), dict) else ""
        # fusione 15/07: il report EVENT DESK copre entrambi i domini dei chart
        ed_text = "\n".join(str(v) for v in bb_data.get("eventdesk", {}).values()) if isinstance(bb_data.get("eventdesk"), dict) else ""
        if ed_text:
            news_text = (news_text + "\n" + ed_text).strip()
            # review 15/07: al chart Polymarket (regex generica 'Nome NN%') passiamo
            # SOLO le righe di probabilita' — il report fuso contiene anche % di
            # earnings/pesi che finirebbero nel grafico spacciate per probabilita'.
            ed_prob_lines = "\n".join(l for l in ed_text.splitlines()
                                      if re.search(r"polymarket|probabilit|prob\b|odds", l, re.IGNORECASE))
            politics_text = (politics_text + "\n" + ed_prob_lines).strip()

    # Genera tutti i chart in sequenza
    print("[CHARTS] Generating quant appendix charts...")
    chart_paths = []
    positions = portfolio_data.get("positions", []) if portfolio_data else []

    def _try(label, func, *args, **kwargs):
        try:
            p = func(*args, **kwargs)
            if p:
                chart_paths.append((label, p))
                print("  [OK] " + label)
        except Exception as e:
            print("  [FAIL] " + label + ": " + str(e))

    _try(_t("Portfolio Treemap"), chart_portfolio_treemap, positions)
    _try(_t("Concentration Top"), chart_concentration_top_positions, positions)
    _try(_t("Sector Breakdown"), chart_sector_breakdown, positions)
    _try(_t("P/L per Position"), chart_pl_bar, positions)
    _try(_t("Sharpe Panel"), chart_risk_summary_panel, quant_text, portfolio_data)
    _try(_t("Correlation Heatmap"), chart_correlation_heatmap, correlation_data, [p.get("ticker") for p in positions[:8]])
    _try(_t("Yield Curve"), chart_yield_curve, macro_data)
    _try(_t("Macro Indicators Bars"), chart_macro_dashboard_bars, macro_data)
    _try(_t("Options IV + P/C"), chart_options_iv_pc, options_data_dict)
    _try(_t("Options OI Summary"), chart_options_oi_summary, options_data_dict)
    _try(_t("Max Pain Distance"), chart_options_max_pain_distance, options_data_dict)
    _try(_t("Polymarket Probs"), chart_polymarket_probs, politics_text)
    _try(_t("News Density"), chart_news_count, news_text)

    # Compose PDF
    print("[CHARTS] Composing PDF appendix...")
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            title=_t("Quantitative Appendix - Weekly Research"))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("t", parent=styles["Title"], fontSize=22, leading=26,
                                  textColor=rl_colors.HexColor(COLORS["gold"]),
                                  alignment=TA_CENTER, spaceAfter=8)
    subtitle_style = ParagraphStyle("st", parent=styles["Normal"], fontSize=11,
                                     textColor=rl_colors.HexColor(COLORS["muted"]),
                                     alignment=TA_CENTER, spaceAfter=24)
    section_style = ParagraphStyle("sec", parent=styles["Heading1"], fontSize=15,
                                    textColor=rl_colors.HexColor(COLORS["gold"]),
                                    spaceBefore=10, spaceAfter=8)
    caption_style = ParagraphStyle("cap", parent=styles["Normal"], fontSize=9,
                                    textColor=rl_colors.HexColor(COLORS["muted"]),
                                    alignment=TA_CENTER, spaceAfter=14)

    story = []
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph(_t("Quantitative Appendix"), title_style))
    story.append(Paragraph(date_label(weekday=True), subtitle_style))
    story.append(Paragraph(_t("Multi-Agent Consigliere - All-charts dashboard"), subtitle_style))
    story.append(PageBreak())

    # Gruppi per pagina
    sections = {
        _t("Portfolio Composition"): [_t("Portfolio Treemap"), _t("Concentration Top"), _t("Sector Breakdown"), _t("P/L per Position")],
        _t("Quantitative Risk"): [_t("Sharpe Panel"), _t("Correlation Heatmap")],
        _t("Macro Snapshot"): [_t("Yield Curve"), _t("Macro Indicators Bars")],
        _t("Options Positioning"): [_t("Options IV + P/C"), _t("Options OI Summary"), _t("Max Pain Distance")],
        _t("News & Politics"): [_t("Polymarket Probs"), _t("News Density")],
    }

    for section_name, chart_labels in sections.items():
        story.append(Paragraph(section_name, section_style))
        story.append(Spacer(1, 6))
        added = 0
        for label in chart_labels:
            path = next((p for lbl, p in chart_paths if lbl == label), None)
            if not path:
                continue
            try:
                img = Image(path, width=17*cm, height=10*cm)
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Paragraph(label, caption_style))
                added += 1
            except Exception as e:
                print("  [PDF EMBED FAIL] " + label + ": " + str(e))
        if added == 0:
            story.append(Paragraph(_t("[No data available for this section]"), caption_style))
        story.append(PageBreak())

    # Background dark per ogni pagina
    def add_dark_bg(canvas, doc_obj):
        canvas.saveState()
        canvas.setFillColor(rl_colors.HexColor(COLORS["bg"]))
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(rl_colors.HexColor(COLORS["muted"]))
        canvas.drawCentredString(A4[0]/2, 0.7*cm,
                                  _t("Quantitative Appendix - ") + datetime.now().strftime("%d/%m/%Y")
                                  + _t("  -  Page ") + str(doc_obj.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=add_dark_bg, onLaterPages=add_dark_bg)
    print("[CHARTS] PDF saved: " + output_path)
    return output_path
