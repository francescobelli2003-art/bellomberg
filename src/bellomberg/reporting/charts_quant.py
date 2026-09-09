"""
charts_quant.py — Quant Appendix v2 (#178): grafici desk-grade dai moduli reali.

Sostituisce charts_agent.build_quant_appendix come builder dell'appendice:
  - Fan chart Monte Carlo (fan_bands da portfolio_montecarlo)
  - Cono volatilita' GARCH (forecast+CI da portfolio_garch, realizzata da nav history)
  - Underwater chart (drawdown su cumulative P/L ratio da portfolio_analytics)
  - Distribuzione rendimenti + VaR/ES (nav history + portfolio_risk)
  - Correlation heatmap annotata
  - Treemap, concentration, yield curve riusati da charts_agent (ora research style)

Ogni grafico e' guarded: se un modulo/dato manca, il grafico viene saltato
senza far fallire l'appendice. API compatibile col vecchio builder.
"""
import os
from datetime import datetime

from bellomberg.core.paths import REPORT_DIR

try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MPL_OK = True
except ImportError:
    MPL_OK = False

from bellomberg.reporting.style_research import COLORS, apply_research_style, add_branding, add_footer
import bellomberg.reporting.style_terminal as st   # identita' "terminale" (fascetta obsidian+ambra)

CHART_DIR = str(REPORT_DIR / "quant_charts")
NAVY = COLORS["navy"]; NAVY2 = COLORS["cyan_dark"]; GOLD = COLORS["gold"]
STEEL = COLORS["steel"]; GREY = COLORS["muted"]; RED = COLORS["red"]
GREEN = COLORS["green"]; INK = COLORS["fg"]


def _save(fig, name):
    os.makedirs(CHART_DIR, exist_ok=True)
    path = os.path.join(CHART_DIR, name + "_" + datetime.now().strftime("%H%M%S") + ".png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def _find_key(d, key):
    """Cerca una chiave ricorsivamente in dict annidati (robusto a shape diverse)."""
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def _horizon_label(mc):
    """Etichetta orizzonte leggibile ('1Y', '6M', '120 gg') dai campi del motore."""
    y = (mc or {}).get("horizon_years")
    d = (mc or {}).get("horizon_days")
    try:
        if y and abs(float(y) - round(float(y))) < 0.02:
            return "{:.0f}Y".format(float(y))
        if y and float(y) < 1:
            return "{:.0f}M".format(float(y) * 12)
    except (TypeError, ValueError):
        pass
    return "{} gg".format(d) if d else "n/d"


def _returns_from_nav(nav_hist):
    """Serie rendimenti giornalieri % dal P/L INCREMENTALE sul cost basis del giorno
    prima. audit/11 §4: il diff del ratio pnl/cb generava rendimenti FANTASMA a ogni
    variazione di capitale (ADD/BUY/TRIM cambiano il denominatore, non la performance)."""
    pnl = np.array(nav_hist.get("pnl_eur") or [], dtype=float)
    cb = np.array(nav_hist.get("cost_basis_eur") or [], dtype=float)
    if len(pnl) < 10 or len(cb) != len(pnl):
        return None
    dpnl = np.diff(pnl)
    cb_prev = cb[:-1]
    r = np.where(cb_prev > 0, dpnl / cb_prev, 0.0) * 100.0
    return r[np.isfinite(r)]


# ============================================================
# CHARTS
# ============================================================

def chart_mc_fan(mc):
    """Fan chart NAV: bande annidate + path campione + marginale dei valori terminali.

    Tutto da portfolio_montecarlo (percentili per-giorno, sample_paths, terminal_hist):
    nessun path re-simulato qui e nessuna banda interpolata — si disegna solo cio' che
    il motore ha davvero prodotto (bande mancanti = non disegnate, non inventate).
    """
    fb = (mc or {}).get("fan_bands")
    if not (MPL_OK and fb):
        return None
    try:
        st.apply_style()
        d = fb["days"]
        fig = plt.figure(figsize=(9.2, 4.2))
        ax = fig.add_axes([0.075, 0.135, 0.70, 0.645])
        base = mc.get("base_nav_eur") or fb["p50"][0]

        # path campione in trasparenza: danno il senso della dispersione dei singoli
        # scenari, che le sole bande (percentili) non mostrano
        paths = mc.get("sample_paths") or []
        pdays = mc.get("sample_paths_days")
        # Tetto a 10 tracce SUL POSTO (26/07): il payload ora puo' portarne fino a
        # 500 per il fan interattivo di F5, ma su una figura da 9x4 pollici oltre la
        # decina le tracce smettono di "dare il senso della dispersione" e coprono
        # le bande, che sono il contenuto vero. Il PDF non deve dipendere da quante
        # ne chiede la UI: se ne arrivano di piu' si campiona, non si disegna tutto.
        MAX_TRACCE = 10
        if len(paths) > MAX_TRACCE:
            _step = len(paths) / MAX_TRACCE
            paths = [paths[int(k * _step)] for k in range(MAX_TRACCE)]
        if paths and pdays:
            for pth in paths:
                n = min(len(pth), len(pdays))
                ax.plot(pdays[:n], [v * base for v in pth[:n]], color=st.STEEL_L,
                        lw=0.45, alpha=0.5, zorder=1)

        # bande annidate: stessa tinta, opacita' crescente verso la mediana
        for lo, hi, alpha, lab in (("p5", "p95", 0.10, "5–95 pct"),
                                   ("p10", "p90", 0.14, "10–90 pct"),
                                   ("p25", "p75", 0.22, "25–75 pct")):
            if fb.get(lo) and fb.get(hi):
                ax.fill_between(d, fb[lo], fb[hi], color=st.STEEL, alpha=alpha, lw=0,
                                zorder=2, label=lab)
        ax.plot(d, fb["p50"], color=st.NAVY, lw=2.1, zorder=4, label="Mediana",
                solid_capstyle="round")
        ax.axhline(base, color=st.MUTED, lw=0.8, ls=(0, (3, 3)), zorder=3)
        ax.annotate("NAV oggi  {:,.0f}k€".format(base / 1000), xy=(0, base),
                    xytext=(4, -12), textcoords="offset points", fontsize=7, color=st.MUTED)
        for key, col in (("p95", st.UP), ("p50", st.NAVY), ("p5", st.DOWN)):
            ax.plot([d[-1]], [fb[key][-1]], "o", ms=5, color=col, zorder=6)
        ax.set_xlim(0, max(d))
        ax.grid(axis="y")
        ax.set_xlabel("Giorni di trading")
        ax.set_ylabel("NAV (EUR)")
        ax.yaxis.set_major_formatter(lambda x, _: "{:,.0f}k".format(x / 1000))
        ax.tick_params(length=2)
        ax.legend(loc="upper left", fontsize=7.5, handlelength=1.4, labelspacing=0.35)

        # marginale: distribuzione dei NAV a fine orizzonte (istogramma vero dal motore)
        th = (mc or {}).get("terminal_hist") or {}
        counts, edges = th.get("counts"), th.get("edges_eur")
        if counts and edges and len(edges) == len(counts) + 1:
            # NIENTE sharey: l'istogramma copre l'INTERO supporto terminale (code
            # comprese), il fan solo p5..p95. Con l'asse condiviso l'autoscale delle
            # barre allargava la ylim comune e schiacciava il fan a ~40% dell'altezza
            # (review 15/07). Si allinea a mano alla ylim del fan, gia' fissata sopra.
            ylim = ax.get_ylim()
            mx = fig.add_axes([0.79, 0.135, 0.135, 0.645])
            centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
            heights = [edges[i + 1] - edges[i] for i in range(len(counts))]
            mx.barh(centers, counts, height=heights, color=st.STEEL, alpha=0.45,
                    align="center")
            mx.set_ylim(ylim)   # stessa scala del fan: le code fuori scala restano tagliate
            mx.axhline(fb["p50"][-1], color=st.NAVY, lw=1.4)
            for key, col in (("p95", st.UP), ("p5", st.DOWN)):
                mx.axhline(fb[key][-1], color=col, lw=1.0, ls=(0, (2, 2)))
            mx.set_xticks([])
            for s in mx.spines.values():
                s.set_visible(False)
            mx.tick_params(labelleft=False, length=0)
            mx.grid(False)
            mx.set_title("Orizzonte {}".format(_horizon_label(mc)), fontsize=6.8,
                         color=st.MUTED, pad=3)
            for key, lab, col in (("p95", "P95", st.UP), ("p50", "P50", st.NAVY),
                                  ("p5", "P5", st.DOWN)):
                v = fb[key][-1]
                mx.annotate("{} {:,.0f}k€".format(lab, v / 1000),
                            xy=(mx.get_xlim()[1], v), xytext=(2, 0),
                            textcoords="offset points", fontsize=7.2, color=col,
                            va="center", fontweight="bold", annotation_clip=False)

        sub = "{} · {:,} scenari · orizzonte {} gg".format(
            (mc.get("method_description") or mc.get("method", ""))[:58],
            mc.get("n_sims") or 0, mc.get("horizon_days", "?"))
        pneg = mc.get("prob_negative_pct")
        if pneg is not None:
            sub += " · P(anno in perdita) {:.0f}%".format(pneg)
        st.titlebar(fig, "Proiezione NAV — Monte Carlo", sub,
                    "Bellomberg Quant Engine · portfolio_montecarlo ({})".format(
                        mc.get("method", "?")))
        return _save(fig, "mc_fan")
    except Exception:
        plt.close("all")
        return None


def chart_garch_cone(garch, nav_hist):
    """Vol realizzata rolling 22d + punti forecast GARCH con CI 95%."""
    if not (MPL_OK and garch) or garch.get("error"):
        return None
    fc = garch.get("forecast_vol") or {}
    pts = []
    for k, h in (("1d", 1), ("5d", 5), ("22d", 22)):
        v = fc.get(k) or {}
        if v.get("vol_annualized_pct") is not None:
            pts.append((h, v["vol_annualized_pct"],
                        v.get("ci95_low_pct"), v.get("ci95_high_pct")))
    if not pts:
        return None
    try:
        st.apply_style()
        fig, ax = plt.subplots(figsize=(9.2, 3.6))
        n_hist = 0
        rets = _returns_from_nav(nav_hist or {})
        if rets is not None and len(rets) > 30:
            roll = np.array([rets[max(0, i - 22):i].std() * np.sqrt(252)
                             for i in range(22, len(rets))])
            n_hist = len(roll)
            ax.plot(range(n_hist), roll, color=st.NAVY, lw=1.3,
                    label="Vol realizzata (22d ann.)")
        xs = [n_hist + h for h, _, _, _ in pts]
        ys = [v for _, v, _, _ in pts]
        lo = [l if l is not None else v for _, v, l, _ in pts]
        hi = [u if u is not None else v for _, v, _, u in pts]
        ax.plot(xs, ys, color=st.GOLD, lw=1.8, marker="o", ms=3.5,
                label="Forecast {}".format(garch.get("model_chosen", "GARCH")))
        ax.fill_between(xs, lo, hi, color=st.GOLD, alpha=0.18, label="CI 95%")
        if n_hist:
            ax.axvline(n_hist, color=st.MUTED, lw=0.8, ls=(0, (3, 3)))
        ax.set_ylabel("Volatilita' annualizzata %")
        ax.set_xlabel("Giorni")
        ax.grid(axis="y"); ax.tick_params(length=2)
        ax.legend(loc="upper left", fontsize=7.5, handlelength=1.4)
        st.titlebar(fig, "Volatilita' di portafoglio — realizzata e forecast GARCH",
                    "Persistenza {} · vol prevista al giorno t+h (1/5/22g) · CI 95% simulati dal modello".format(
                        garch.get("persistence", "?")),
                    "Bellomberg Quant Engine · portfolio_garch")
        fig.subplots_adjust(left=0.075, right=0.985, top=0.78, bottom=0.15)
        return _save(fig, "garch_cone")
    except Exception:
        plt.close("all")
        return None


def chart_underwater(nav_hist):
    """Underwater chart: drawdown sul cumulative P/L ratio."""
    if not MPL_OK:
        return None
    try:
        pnl = np.array((nav_hist or {}).get("pnl_eur") or [], dtype=float)
        cb = np.array((nav_hist or {}).get("cost_basis_eur") or [], dtype=float)
        if len(pnl) < 10 or len(cb) != len(pnl):
            return None
        # audit/11 §4: drawdown sul COMPOUND dei rendimenti incrementali (dpnl/cb_prev),
        # non sul ratio grezzo pnl/cb che crolla artificialmente a ogni versamento
        dpnl = np.diff(pnl)
        cb_prev = cb[:-1]
        r = np.where(cb_prev > 0, dpnl / cb_prev, 0.0)
        ratio = np.concatenate(([1.0], np.cumprod(1.0 + r)))
        peak = np.maximum.accumulate(ratio)
        dd = (ratio / peak - 1) * 100
        st.apply_style()
        fig, ax = plt.subplots(figsize=(9.2, 3.1))
        x = range(len(dd))
        ax.fill_between(x, dd, 0, color=st.DOWN, alpha=0.28)
        ax.plot(x, dd, color=st.DOWN, lw=1.0)
        imin = int(dd.argmin())
        ax.annotate("Max DD {:.1f}%".format(dd[imin]), xy=(imin, dd[imin]),
                    xytext=(min(imin + 8, len(dd) - 1), dd[imin] * 0.82),
                    fontsize=7.5, color=st.DOWN, fontweight="bold")
        ax.set_ylabel("Drawdown %")
        ax.set_xlabel("Giorni (dal primo trade)")
        ax.grid(axis="y"); ax.tick_params(length=2)
        st.titlebar(fig, "Underwater chart — drawdown ITD",
                    "Su cumulative P/L ratio: esclude l'effetto degli apporti di capitale",
                    "Bellomberg Quant Engine · portfolio_analytics")
        fig.subplots_adjust(left=0.075, right=0.985, top=0.78, bottom=0.17)
        return _save(fig, "underwater")
    except Exception:
        plt.close("all")
        return None


def chart_var_distribution(nav_hist, risk):
    """Istogramma rendimenti giornalieri con VaR95/99 e coda evidenziata."""
    if not MPL_OK:
        return None
    try:
        r = _returns_from_nav(nav_hist or {})
        if r is None or len(r) < 40:
            return None
        st.apply_style()
        fig, ax = plt.subplots(figsize=(9.2, 3.5))
        cnt, bins, patches = ax.hist(r, bins=60, color=st.STEEL, alpha=0.55, density=True)
        var95 = _find_key(risk or {}, "var_95_1d_pct")
        var99 = _find_key(risk or {}, "var_99_1d_pct")
        v95 = -abs(var95) if var95 is not None else float(np.percentile(r, 5))
        v99 = -abs(var99) if var99 is not None else float(np.percentile(r, 1))
        for b, p in zip(bins[:-1], patches):
            if b <= v95:
                p.set_facecolor(st.DOWN)
                p.set_alpha(0.55)
        mu, sd = float(r.mean()), float(r.std())
        if sd > 0:
            xs = np.linspace(r.min(), r.max(), 240)
            ax.plot(xs, np.exp(-(xs - mu) ** 2 / (2 * sd ** 2)) / (sd * np.sqrt(2 * np.pi)),
                    color=st.MUTED, lw=1.0, ls=(0, (4, 3)), label="Normale equivalente")
            kurt = float(((r - mu) ** 4).mean() / sd ** 4)
        else:
            kurt = float("nan")
        ax.axvline(v95, color=st.DOWN, lw=1.3)
        ax.axvline(v99, color="#6E1414", lw=1.3)
        ax.text(v95, ax.get_ylim()[1] * 0.92, " VaR95 {:.2f}%".format(v95),
                fontsize=7.5, color=st.DOWN, fontweight="bold")
        ax.text(v99, ax.get_ylim()[1] * 0.74, " VaR99 {:.2f}%".format(v99),
                fontsize=7.5, color="#6E1414", fontweight="bold")
        ax.set_xlabel("Rendimento giornaliero %")
        ax.set_ylabel("Densita'")
        ax.grid(axis="y"); ax.tick_params(length=2)
        ax.legend(loc="upper right", fontsize=7.5, handlelength=1.4)
        st.titlebar(fig, "Distribuzione rendimenti giornalieri — VaR",
                    "Kurtosis {:.1f} (code grasse vs 3.0 normale) · coda oltre VaR95 in rosso".format(kurt),
                    "Bellomberg Quant Engine · portfolio_risk + nav history")
        fig.subplots_adjust(left=0.075, right=0.985, top=0.78, bottom=0.15)
        return _save(fig, "var_dist")
    except Exception:
        plt.close("all")
        return None


def chart_corr_heatmap(corr_obj, risk_obj=None, n_total=None):
    """Heatmap correlazioni annotata (max 14 ticker per leggibilita').

    `risk_obj`/`n_total` servono a DICHIARARE il vero: base valutaria e finestra si
    leggono dal payload di portfolio_risk (non si affermano a mano), e il numero di
    nomi mostrati va confrontato col totale delle posizioni — la matrice arriva gia'
    troncata a monte dal motore di rischio.
    """
    if not (MPL_OK and corr_obj):
        return None
    try:
        m = _find_key(corr_obj, "matrix") if isinstance(corr_obj, dict) else None
        tks = _find_key(corr_obj, "tickers") if isinstance(corr_obj, dict) else None
        if m is None and isinstance(corr_obj, (list, tuple)):
            m = corr_obj
        if m is None:
            return None
        M = np.array(m, dtype=float)
        if M.ndim != 2 or M.shape[0] != M.shape[1]:
            return None
        k = M.shape[0]
        if not tks or len(tks) != k:
            tks = ["#{}".format(i + 1) for i in range(k)]
        n_tot = k
        if k > 14:
            M = M[:14, :14]; tks = list(tks)[:14]; k = 14
        st.apply_style()
        from matplotlib.colors import LinearSegmentedColormap
        # diverging rosso-bianco-navy: il bianco cade sullo zero (vmin/vmax simmetrici),
        # cosi' il colore legge il SEGNO della correlazione senza ambiguita'
        cmap = LinearSegmentedColormap.from_list("bb", [st.DOWN, "#F4F6F9", st.NAVY])
        fig, ax = plt.subplots(figsize=(8.0, 4.8))
        # aspect="auto": le celle riempiono il riquadro. Con l'aspect quadrato di
        # default imshow lasciava mezza figura bianca (ogni cella porta il suo numero,
        # quindi la forma della cella non veicola informazione).
        im = ax.imshow(M, cmap=cmap, vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(k)); ax.set_yticks(range(k))
        ax.set_xticklabels(tks, rotation=45, ha="right", fontsize=7)
        ax.set_yticklabels(tks, fontsize=7)
        for i in range(k):
            for j in range(k):
                ax.text(j, i, "{:.2f}".format(M[i, j]), ha="center", va="center",
                        fontsize=6, color="white" if abs(M[i, j]) > 0.55 else st.INK)
        ax.grid(False); ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
        cb = fig.colorbar(im, ax=ax, fraction=0.045, pad=0.03)
        cb.ax.tick_params(labelsize=6.5, color=st.MUTED, labelcolor=st.MUTED)
        cb.outline.set_visible(False)
        # Tutto cio' che il sottotitolo AFFERMA si legge dal dato: l'estimatore (Ledoit-Wolf
        # shrinkato, non la "Pearson su rendimenti log" che la didascalia vantava fino al
        # 15/07) e la base valutaria, che portfolio_risk dichiara in `returns_basis` e che
        # NON e' sempre EUR (se la conversione FX fallisce restano in valuta locale).
        meta = _find_key(corr_obj, "meta") if isinstance(corr_obj, dict) else None
        est = (meta or {}).get("estimator") or ""
        est_lbl = ("Ledoit-Wolf (shrinkage a correlazione costante)"
                   if est.startswith("ledoit_wolf") else
                   "correlazione campionaria (Ledoit-Wolf fallito)" if est.startswith("sample") else
                   "estimatore n.d.")
        basis = _find_key(risk_obj or {}, "returns_basis") if risk_obj else None
        sub = "{} · rendimenti giornalieri in {}".format(est_lbl, basis or "base valutaria n.d.")
        obs = (meta or {}).get("obs")
        if obs:
            sub += " · {} oss.".format(obs)
        # il troncamento vero avviene A MONTE (portfolio_risk manda gia' solo 8 nomi):
        # si dichiara il confronto col totale delle posizioni, non un taglio locale
        # che su dati veri non scatta mai.
        shown = k
        if n_total and n_total > shown:
            sub += " · {} nomi su {} posizioni".format(shown, n_total)
        elif n_tot > k:
            sub += " · primi {} nomi su {} (leggibilita')".format(k, n_tot)
        st.titlebar(fig, "Matrice di correlazione", sub,
                    "Bellomberg Quant Engine · portfolio_risk")
        fig.subplots_adjust(left=0.12, right=0.98, top=0.78, bottom=0.14)
        return _save(fig, "corr_heatmap")
    except Exception:
        plt.close("all")
        return None


# ============================================================
# BUILDER (API compatibile con charts_agent.build_quant_appendix)
# ============================================================

def _fmt(v, suffix="", dec=2):
    if v is None:
        return "n/d"
    try:
        return f"{float(v):,.{dec}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def _numeric_tables(risk, mc, ff):
    """Costruisce le tabelle numeriche dell'appendice (reportlab flowables):
    metriche di rischio + decomposizione fattoriale + interpretazione.
    E' la parte che trasforma 'grafici' in 'analisi quantitativa'."""
    from reportlab.lib.units import cm
    from reportlab.lib import colors as C
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, HRFlowable

    # Stessa identita' del memo (PM 15/07): intestazioni tabella obsidian+ambra,
    # titoli di sezione NERI con filetto ambra. Prima erano navy: nell'appendice le
    # tabelle restavano "come prima" mentre i grafici sotto erano gia' passati al terminale.
    OBSIDIAN = C.HexColor("#050608"); AMBER = C.HexColor("#FFA51E")
    NAVY = C.HexColor("#0B2545"); GOLD = C.HexColor("#B08D2E")
    GREY = C.HexColor("#5A6472"); RULE = C.HexColor("#D7DCE3")
    INK = C.HexColor("#1A1A1A"); GREEN = C.HexColor("#1B7A4A"); RED = C.HexColor("#A12B2B")
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=12.5, textColor=OBSIDIAN,
                        spaceBefore=2, spaceAfter=8, leading=15)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=9.5, textColor=OBSIDIAN,
                        spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=8.5, textColor=INK,
                          leading=12, spaceAfter=5, alignment=4)

    def styled(data, widths, right_cols=()):
        t = Table(data, colWidths=widths, repeatRows=1)
        st = [("BACKGROUND", (0, 0), (-1, 0), OBSIDIAN),
              ("TEXTCOLOR", (0, 0), (-1, 0), AMBER),
              ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
              ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
              ("TEXTCOLOR", (0, 1), (-1, -1), INK),
              ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C.white, C.HexColor("#F4F6F9")]),
              ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
              ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
              ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]
        for rc in right_cols:
            st.append(("ALIGN", (rc, 1), (rc, -1), "RIGHT"))
        t.setStyle(TableStyle(st))
        return t

    def _sec(text, style, thick=1.2):
        """Titolo + filetto ambra: stessa identita' dei titoli del memo (sec())."""
        return [Paragraph(text, style),
                HRFlowable(width="100%", thickness=thick, color=AMBER,
                           spaceBefore=1, spaceAfter=6)]

    flow = _sec("PROFILO DI RISCHIO QUANTITATIVO", h1)

    # --- Tabella metriche di rischio ---
    p = (risk or {}).get("portfolio", {})
    mcd = mc or {}
    rows = [["Metrica", "Valore", "Lettura"]]
    def add(metric, val, reading):
        rows.append([metric, val, reading])
    if p.get("vol_annual_pct") is not None:
        add("Volatilita' annualizzata", _fmt(p["vol_annual_pct"], "%"),
            "ampiezza tipica delle oscillazioni annue")
    if p.get("sharpe") is not None:
        add("Sharpe ratio", _fmt(p["sharpe"]),
            "rendimento per unita' di rischio; <0 distrugge valore")
    if p.get("beta_vs_spy") is not None:
        add("Beta vs S&P 500", _fmt(p["beta_vs_spy"]),
            "sensibilita' al mercato USA; >1 amplifica")
    if p.get("var_95_1d_pct") is not None:
        add("VaR 95% (1 giorno)", f"{_fmt(p['var_95_1d_pct'],'%')}  ({_fmt(p.get('var_95_1d_eur'),' EUR',0)})",
            "perdita giornaliera superata 1 giorno su 20")
    if p.get("var_99_1d_pct") is not None:
        add("VaR 99% (1 giorno)", f"{_fmt(p['var_99_1d_pct'],'%')}  ({_fmt(p.get('var_99_1d_eur'),' EUR',0)})",
            "perdita giornaliera superata 1 giorno su 100")
    if mcd.get("es_95_pct") is not None:
        add("Expected Shortfall 95% (1Y)", _fmt(mcd["es_95_pct"], "%"),
            "perdita media nei peggiori scenari (oltre il VaR)")
    if p.get("max_dd_1y_pct") is not None:
        add("Max Drawdown (1 anno)", _fmt(p["max_dd_1y_pct"], "%"),
            "massima caduta picco-minimo nell'ultimo anno")
    if mcd.get("prob_negative_pct") is not None:
        add("Prob. anno in perdita", _fmt(mcd["prob_negative_pct"], "%"),
            "da Monte Carlo, probabilita' di chiudere l'anno sotto zero")
    if len(rows) > 1:
        flow.append(styled(rows, [4.6 * cm, 4.8 * cm, 7.6 * cm], right_cols=(1,)))

    # interpretazione rischio
    notes = []
    if p.get("sharpe") is not None:
        notes.append("Lo Sharpe a {} indica che il portafoglio {}."
                     .format(_fmt(p["sharpe"]),
                             "remunera bene il rischio assunto" if p["sharpe"] > 0.5 else
                             "rende poco rispetto al rischio" if p["sharpe"] >= 0 else
                             "sta distruggendo valore corretto per il rischio"))
    if p.get("beta_vs_spy") is not None:
        notes.append("Con beta {} verso l'S&P 500, il book {} i movimenti del mercato USA."
                     .format(_fmt(p["beta_vs_spy"]),
                             "amplifica" if p["beta_vs_spy"] > 1.1 else
                             "attenua" if p["beta_vs_spy"] < 0.9 else "segue circa 1:1"))
    for a in (risk or {}).get("alerts", [])[:3]:
        notes.append("ALERT: " + a.get("message", ""))
    if notes:
        flow.append(Spacer(1, 0.2 * cm))
        flow.append(Paragraph(" ".join(notes), body))

    # --- Tabella metriche di performance ISTITUZIONALI (advanced_metrics) ---
    try:
        from bellomberg.portfolio.advanced_metrics import portfolio_metrics
        am = portfolio_metrics()
    except Exception:
        am = {}
    if am and not am.get("error"):
        flow.extend(_sec("Metriche di Performance Istituzionali (vs benchmark)", h2, 0.8))
        def gv(k):
            v = am.get(k)
            return "n/d" if v is None else v
        bm = am.get("benchmark", {}) or {}
        perf_rows = [
            ["Rendimento/Rischio", "Valore", "Drawdown & Code", "Valore"],
            [f"CAGR", f"{gv('cagr_pct')}%", "Max Drawdown", f"{gv('max_drawdown_pct')}%"],
            [f"Volatilita' annua", f"{gv('vol_annual_pct')}%", "Durata max DD (gg)", f"{gv('max_dd_duration_days')}"],
            [f"Sharpe", f"{gv('sharpe')}", "Ulcer Index", f"{gv('ulcer_index')}"],
            [f"Sortino", f"{gv('sortino')}", "Recovery Factor", f"{gv('recovery_factor')}"],
            [f"Calmar", f"{gv('calmar')}", "CVaR 95% (ES)", f"{gv('cvar_95_1d_pct')}%"],
            [f"Omega", f"{gv('omega')}", "CVaR 99%", f"{gv('cvar_99_1d_pct')}%"],
            [f"Win rate", f"{gv('win_rate_pct')}%", "Skewness", f"{gv('skewness')}"],
            [f"Payoff ratio", f"{gv('payoff_ratio')}", "Kurtosi in eccesso", f"{gv('excess_kurtosis')}"],
            [f"Profit factor", f"{gv('profit_factor')}", "Tail ratio", f"{gv('tail_ratio')}"],
            [f"Beta vs {am.get('benchmark_ticker','SPY')}", f"{bm.get('beta','n/d')}",
             "Alpha annuo", f"{bm.get('alpha_annual_pct','n/d')}%"],
            [f"Information Ratio", f"{bm.get('information_ratio','n/d')}",
             "Kelly fraction", f"{gv('kelly_fraction_pct')}%"],
        ]
        pt = Table(perf_rows, colWidths=[4.6*cm, 3.6*cm, 4.6*cm, 4.2*cm], repeatRows=1)
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), OBSIDIAN),
            ("TEXTCOLOR", (0, 0), (-1, 0), AMBER),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("FONT", (0, 1), (0, -1), "Helvetica", 8),
            ("FONT", (2, 1), (2, -1), "Helvetica", 8),
            ("TEXTCOLOR", (0, 1), (0, -1), GREY), ("TEXTCOLOR", (2, 1), (2, -1), GREY),
            ("FONT", (1, 1), (1, -1), "Helvetica-Bold", 8),
            ("FONT", (3, 1), (3, -1), "Helvetica-Bold", 8),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"), ("ALIGN", (3, 1), (3, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C.white, C.HexColor("#F4F6F9")]),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
            ("LINEAFTER", (1, 0), (1, -1), 0.5, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        flow.append(pt)
        flow.append(Spacer(1, 0.15 * cm))
        # lettura sintetica
        sh, so = am.get("sharpe"), am.get("sortino")
        if sh is not None:
            quality = ("eccellente" if sh > 1.5 else "buono" if sh > 0.8 else
                       "modesto" if sh > 0 else "negativo")
            flow.append(Paragraph(
                f"Il profilo risk-adjusted e' {quality} (Sharpe {sh}, Sortino {so}). "
                f"Il Sortino sopra lo Sharpe indica che la volatilita' e' prevalentemente al "
                f"rialzo; l'Ulcer Index e il Recovery Factor misurano profondita' e velocita' di "
                f"recupero dei drawdown. Le metriche relative al benchmark isolano alpha e beta.", body))

    # --- Tabella decomposizione fattoriale ---
    ph = (ff or {}).get("per_holding", {})
    if ph:
        flow.extend(_sec("Decomposizione Fattoriale (Fama-French 5 + Momentum, regionale)", h2, 0.8))
        frows = [["Titolo", "Regione", "β Mkt", "β SMB", "β HML", "Alpha a.", "t-stat", "R²"]]
        items = sorted(ph.items(), key=lambda kv: -(kv[1].get("weight_pct") or 0))[:12]
        for tk, h in items:
            frows.append([tk,
                          (h.get("factor_region") or "")[:8],
                          _fmt(h.get("beta_market"), "", 2),
                          _fmt(h.get("beta_smb"), "", 2),
                          _fmt(h.get("beta_hml"), "", 2),
                          _fmt(h.get("alpha_annualized_pct"), "%", 1),
                          _fmt(h.get("alpha_tstat"), "", 1),
                          _fmt(h.get("r_squared"), "", 2)])
        agg = (ff or {}).get("portfolio_aggregate", {})
        if agg:
            frows.append(["AGGREGATO", f"{_fmt((ff or {}).get('coverage_weight_pct'),'%',0)} cov",
                          _fmt(agg.get("beta_market"), "", 2),
                          _fmt(agg.get("beta_smb"), "", 2),
                          _fmt(agg.get("beta_hml"), "", 2),
                          _fmt(agg.get("alpha_annualized_pct"), "%", 1), "", ""])
        ft = styled(frows, [2.6*cm, 1.9*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.7*cm, 1.5*cm, 1.3*cm],
                    right_cols=(2, 3, 4, 5, 6, 7))
        # evidenzia la riga aggregato
        if agg:
            ft.setStyle(TableStyle([("FONT", (0, len(frows)-1), (-1, len(frows)-1), "Helvetica-Bold", 8),
                                    ("LINEABOVE", (0, len(frows)-1), (-1, len(frows)-1), 0.8, NAVY)]))
        flow.append(ft)
        nsig = (ff or {}).get("n_alpha_significant_5pct", 0)
        nan = (ff or {}).get("n_holdings_analyzed", 0)
        flow.append(Spacer(1, 0.15 * cm))
        flow.append(Paragraph(
            f"Copertura {_fmt((ff or {}).get('coverage_weight_pct'),'%',0)} del NAV su fattori regionali. "
            f"Solo {nsig} alpha su {nan} sono statisticamente significativi (t>1,96): gli altri sono "
            f"rumore, non vera capacita' di sovraperformare. L'alpha aggregato va quindi letto con "
            f"prudenza e il portafoglio risulta in larga parte spiegato dai fattori di mercato.", body))

    return flow


def build_quant_appendix_v2(blackboard=None, portfolio_data=None, macro_data=None,
                            options_data_dict=None, correlation_data=None,
                            output_path=None):
    """Appendice quant v2 (#178/#180). Tabelle numeriche + grafici desk-grade."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors as C
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Image, PageBreak, HRFlowable)
    except ImportError:
        return None

    # ---- raccolta dati (ogni modulo guarded) ----
    try:
        from bellomberg.portfolio.portfolio_analytics import compute_nav_history
        nav = compute_nav_history()
    except Exception:
        nav = {}
    try:
        from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
        risk = compute_portfolio_risk()
    except Exception:
        risk = {}
    try:
        from bellomberg.portfolio.portfolio_garch import compute_portfolio_garch
        garch = compute_portfolio_garch()
    except Exception:
        garch = {}
    try:
        from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
        mc = run_monte_carlo()
    except Exception:
        mc = {}

    corr_src = correlation_data or (risk.get("correlation") if isinstance(risk, dict) else None)

    # ---- grafici riusati da charts_agent (research style dopo lo swap) ----
    treemap = conc = None
    try:
        from bellomberg.reporting import charts_agent
        positions = (portfolio_data or {}).get("positions") or []
        if positions:
            try:
                treemap = charts_agent.chart_portfolio_treemap(positions)
            except Exception:
                pass
            try:
                conc = charts_agent.chart_concentration_top_positions(positions)
            except Exception:
                pass
    except Exception:
        pass

    # ---- tassi: curve VERE da macro_rates (#143). Sostituiscono la vecchia
    # charts_agent.chart_yield_curve, che disegnava una "curva" di 3 soli punti
    # (Fed funds/2Y/10Y) presi da macro_data. Qui: struttura a termine intera
    # US/Germania/Giappone + credito HY europeo, con i buchi dichiarati.
    yieldc = credit = None
    try:
        from bellomberg.reporting import charts_rates
        try:
            yieldc = charts_rates.yield_curves_chart()
        except Exception as e:
            print("[appendix] yield curves skip: {}".format(e))
        try:
            credit = charts_rates.credit_chart()
        except Exception as e:
            print("[appendix] credit chart skip: {}".format(e))
    except Exception:
        pass

    sections = [
        ("PORTAFOGLIO — COMPOSIZIONE E CONCENTRAZIONE", [
            (treemap, "Figura 1. Allocazione per peso — scala navy, top holding in oro."),
            (conc, "Figura 2. Top-10 posizioni vs soglia di concentrazione."),
        ]),
        ("RISCHIO PROSPETTICO — MONTE CARLO E VOLATILITA'", [
            (chart_mc_fan(mc), "Figura 3. Proiezione NAV con bande percentili (FHS)."),
            (chart_garch_cone(garch, nav), "Figura 4. Vol realizzata e forecast GARCH con CI 95%."),
        ]),
        ("RISCHIO REALIZZATO — DRAWDOWN E CODE", [
            (chart_underwater(nav), "Figura 5. Drawdown storici su cumulative P/L."),
            (chart_var_distribution(nav, risk), "Figura 6. Distribuzione rendimenti con VaR/coda."),
        ]),
        ("STRUTTURA — CORRELAZIONI E MACRO", [
            (chart_corr_heatmap(corr_src, risk_obj=risk,
                                n_total=len((portfolio_data or {}).get("positions") or []) or None),
             "Figura 7. Correlazioni annotate (cluster visibili)."),
            (yieldc, "Figura 8. Curve dei rendimenti US/Germania/Giappone: struttura a "
                     "termine intera, oggi vs 1 mese e 1 anno fa [src: macro_rates]."),
            (credit, "Figura 9. Credito high yield europeo (OAS ICE BofA, proxy dichiarato "
                     "dell'iTraxx Crossover) [src: macro_rates]."),
        ]),
    ]

    # ---- PDF ----
    os.makedirs(REPORT_DIR, exist_ok=True)
    if not output_path:
        output_path = os.path.join(
            str(REPORT_DIR), "quant_appendix_" + datetime.now().strftime("%Y%m%d_%H%M") + ".pdf")

    NAVY_C = C.HexColor("#0B2545"); GOLD_C = C.HexColor("#B08D2E")
    OBS_C = C.HexColor("#050608"); AMBER_C = C.HexColor("#FFA51E")
    GREY_C = C.HexColor("#5A6472"); RULE_C = C.HexColor("#D7DCE3")

    def furniture(canvas, doc_):
        canvas.saveState()
        w, h = A4
        # B-DC7: UNICO lockup, quello del memo (nero + filetto ambra ancorato al
        # wordmark). Prima anche qui c'era un marchio disegnato a mano, con un terzo
        # rettangolo oro di larghezza fissa scelta a occhio.
        from bellomberg.reporting.pdf_institutional import _draw_lockup
        _draw_lockup(canvas, 2 * cm, h - 1.45 * cm, 13, "Helvetica", "Helvetica-Bold",
                     OBS_C, GREY_C, AMBER_C, tagline=False)
        canvas.setFillColor(GREY_C); canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(w - 2 * cm, h - 1.42 * cm,
                               "QUANTITATIVE APPENDIX  ·  "
                               + datetime.now().strftime("%d %B %Y").upper())
        canvas.setStrokeColor(NAVY_C); canvas.setLineWidth(0.8)
        canvas.line(2 * cm, h - 1.78 * cm, w - 2 * cm, h - 1.78 * cm)
        canvas.setStrokeColor(RULE_C); canvas.setLineWidth(0.5)
        canvas.line(2 * cm, 1.55 * cm, w - 2 * cm, 1.55 * cm)
        canvas.setFont("Helvetica", 7); canvas.setFillColor(GREY_C)
        canvas.drawString(2 * cm, 1.2 * cm,
                          "Bellomberg Research · Documento interno — non costituisce consulenza finanziaria")
        canvas.drawRightString(w - 2 * cm, 1.2 * cm, "Pagina " + str(doc_.page))
        canvas.restoreState()

    # titoli sezione: neri + filetto ambra, come nel memo (decisione PM 15/07)
    S_h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=12.5,
                          textColor=OBS_C, spaceBefore=2, spaceAfter=2, leading=15)
    S_cap = ParagraphStyle("cap", fontName="Helvetica", fontSize=8,
                           textColor=GREY_C, spaceAfter=12, leading=10)

    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2.4 * cm, bottomMargin=2.0 * cm,
                            title="Bellomberg Quantitative Appendix",
                            author="Bellomberg Quant Engine")
    story = []
    # PRIMA le tabelle numeriche (le metriche contano piu' dei grafici), poi le figure
    try:
        from bellomberg.portfolio.portfolio_factors import compute_portfolio_factors
        ff = compute_portfolio_factors()
    except Exception:
        ff = {}
    try:
        num_flow = _numeric_tables(risk, mc, ff)
        if num_flow:
            story.extend(num_flow)
            story.append(PageBreak())
    except Exception as e:
        print(f"[appendix] numeric tables skip: {e}")

    first = True
    for title, charts in sections:
        present = [(p, cap) for p, cap in charts if p and os.path.exists(p)]
        if not present:
            continue
        if not first:
            story.append(PageBreak())
        first = False
        story.append(Paragraph(title, S_h1))
        story.append(HRFlowable(width="100%", thickness=1.2, color=AMBER_C,
                                spaceBefore=1, spaceAfter=7))
        for p, cap in present:
            try:
                # dimensioni dalle PROPORZIONI vere del PNG (prima erano fisse a
                # 17x7.2cm per tutti: schiacciavano fan chart e heatmap, che hanno
                # forme diverse). Il tetto d'altezza scala ANCHE la larghezza, se no
                # rideforma; l'immagine resta centrata in pagina.
                w_cm, h_cm = 17.0, 7.2
                try:
                    from reportlab.lib.utils import ImageReader
                    iw, ih = ImageReader(p).getSize()
                    if iw and ih:
                        h_cm = w_cm * ih / float(iw)
                        if h_cm > 10.5:
                            w_cm *= 10.5 / h_cm
                            h_cm = 10.5
                except Exception:
                    pass
                img = Image(p, width=w_cm * cm, height=h_cm * cm)
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Paragraph(cap, S_cap))
                story.append(Spacer(1, 0.25 * cm))
            except Exception:
                continue
    if not story:
        return None
    doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return output_path
