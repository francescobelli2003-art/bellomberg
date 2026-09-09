"""charts_rates.py — grafici tassi/curve dai DATI VERI di macro_rates.

Stile terminale (fascetta obsidian + titolo ambra, area dati chiara). Ogni curva
mostra la struttura ODIERNA + ~1 mese fa + ~1 anno fa (per vedere come si e'
spostata). Fonte e as_of dichiarati; se un dato manca o e' stale il grafico lo
DICHIARA (niente numeri finti, regola no-fallback-silenziosi).
"""
import os
from datetime import datetime

from bellomberg.core.paths import REPORT_DIR

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MPL = True
except Exception:
    MPL = False

# identita' visiva condivisa (fascetta obsidian+ambra, palette, font): style_terminal
from bellomberg.reporting.style_terminal import (FONT, AMBER_D, INK, MUTED, GRID, BASE, DOWN, C_US, C_DE, C_JP,
                            apply_style, titlebar as _titlebar, gap_panel, save as _st_save)

OUT_DIR = str(REPORT_DIR / "rates_charts")

# tenor -> anni (posizione x proporzionale sulla curva)
_T2Y = {"1M": 1 / 12, "3M": 0.25, "6M": 0.5, "1Y": 1, "2Y": 2, "3Y": 3, "4Y": 4, "5Y": 5,
        "6Y": 6, "7Y": 7, "8Y": 8, "9Y": 9, "10Y": 10, "15Y": 15, "20Y": 20, "25Y": 25,
        "30Y": 30, "40Y": 40}


def _save(fig, name):
    return _st_save(fig, OUT_DIR, name)


def _panel_curve(ax, env, color, name):
    """Disegna un pannello curva: ultimo dato (piena) + 1m fa (tratteggio) + 1y fa (punti).

    Ogni pannello DICHIARA il proprio as_of e, se la fonte e' vecchia (status 'stale'),
    lo dice a chiare lettere: la curva resta disegnata (il dato e' vero, solo vecchio)
    ma NON viene spacciata per 'Oggi' (regola no-fallback-silenziosi).
    """
    pts = (env or {}).get("points") or []
    status = (env or {}).get("status")
    if not pts or status in ("error", "needs_connector", "declared_gap"):
        gap_panel(ax, "DATO NON DISPONIBILE\n(" + str(status or "n.d.") + ")", title=name)
        return
    stale = (status == "stale")
    as_of = (env or {}).get("as_of")
    rows = [(p["tenor"], _T2Y.get(p["tenor"]), p) for p in pts if p["tenor"] in _T2Y]
    rows = [r for r in rows if r[1] is not None]
    rows.sort(key=lambda r: r[1])
    xs = [r[1] for r in rows]
    y_now = [r[2].get("value") for r in rows]
    y_1m = [r[2].get("value_1m") for r in rows]
    y_1y = [r[2].get("value_1y") for r in rows]
    # 1 anno fa (punti, molto tenue) e 1 mese fa (tratteggio) prima, oggi sopra
    if all(v is not None for v in y_1y):
        ax.plot(xs, y_1y, color=color, lw=1.0, ls=":", alpha=0.42, zorder=2, label="1 anno fa")
    if all(v is not None for v in y_1m):
        ax.plot(xs, y_1m, color=color, lw=1.2, ls=(0, (4, 2)), alpha=0.62, zorder=3, label="1 mese fa")
    ax.plot(xs, y_now, color=color, lw=2.1, marker="o", ms=3.2, zorder=4,
            label="Ultimo dato (STALE)" if stale else "Oggi")
    # Etichetta 10Y e legenda vanno negli angoli LIBERI: su curva in salita restano
    # vuoti alto-sx e basso-dx, su curva invertita alto-dx e basso-sx. Senza questa
    # scelta il testo finisce sopra la linea (curve US/DE, 15/07).
    rising = y_now[-1] >= y_now[0]
    r10 = next((r for r in rows if r[0] == "10Y"), None)
    if r10:
        p = r10[2]
        dm = (p.get("value") - p["value_1m"]) * 100 if p.get("value_1m") is not None else None
        dy = (p.get("value") - p["value_1y"]) * 100 if p.get("value_1y") is not None else None
        txt = "10Y %.2f%%" % p["value"]
        if dm is not None:
            txt += "\n%+.0f bps m/m" % dm
        if dy is not None:
            txt += " · %+.0f y/y" % dy
        ax.text(0.03 if rising else 0.97, 0.97, txt, transform=ax.transAxes,
                fontsize=6.8, color=color, fontweight="bold", va="top",
                ha="left" if rising else "right", zorder=6,
                bbox=dict(boxstyle="round,pad=0.28", facecolor="white", alpha=0.82,
                          edgecolor="none"))
    # as_of PROPRIO del pannello: le tre curve hanno vintage diversi (FRED, Bundesbank
    # e MOF pubblicano con lag diversi), quindi una sola data in testata sarebbe falsa
    # per due pannelli su tre.
    ax.set_title(name, fontsize=8.5, color=INK, fontweight="bold", pad=12)
    stamp = ("DATO STALE · as of " + str(as_of or "n.d.")) if stale else ("as of " + str(as_of or "n.d."))
    ax.text(0.5, 1.012, stamp, transform=ax.transAxes, ha="center", va="bottom",
            fontsize=6.2, color=(DOWN if stale else MUTED),
            fontweight="bold" if stale else "normal")
    ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    ax.set_xscale("log")
    # Su scala log i tenor fitti (4Y-10Y del JGB) si accavallano: si etichettano solo
    # le scadenze cardine presenti, i punti restano tutti disegnati.
    keys = {"1M", "3M", "1Y", "2Y", "5Y", "10Y", "20Y", "30Y", "40Y"}
    ticks = [r[1] for r in rows if r[0] in keys] or xs
    labels = [r[0] for r in rows if r[0] in keys] or [r[0] for r in rows]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=6.6, rotation=0)
    ax.set_xticks([], minor=True)   # log scale: via i minor tick automatici
    ax.tick_params(length=2, labelsize=7)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(BASE); ax.spines["bottom"].set_color(BASE)
    ax.set_xlabel("Scadenza", fontsize=7.5)
    ax.legend(loc="lower right" if rising else "lower left",
              fontsize=6.4, frameon=False, handlelength=1.6)


def yield_curves_chart(us=None, de=None, jp=None):
    """3 pannelli US/Germania/Giappone: curva oggi + 1m + 1y. Dati da macro_rates
    (o iniettati per test)."""
    if not MPL:
        return None
    if us is None or de is None or jp is None:
        import bellomberg.market_data.macro_rates as mr
        us = us or mr.get_us_curve()
        de = de or mr.get_bund_curve()
        jp = jp or mr.get_jgb_curve()
    apply_style()
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.9))
    _panel_curve(axes[0], us, C_US, "US Treasury")
    _panel_curve(axes[1], de, C_DE, "Germania Bund")
    _panel_curve(axes[2], jp, C_JP, "Giappone JGB")
    axes[0].set_ylabel("Rendimento %", fontsize=8)
    # NIENTE as_of di figura: ogni pannello stampa il suo (vintage diversi per fonte).
    _titlebar(fig, "Curve dei rendimenti — US / Germania / Giappone",
              "Struttura a termine · linea piena ultimo dato, tratteggio 1 mese fa, punti 1 anno fa "
              "· as of dichiarato per curva",
              "macro_rates · FRED Treasury / Bundesbank / MOF Japan")
    fig.subplots_adjust(left=0.06, right=0.985, top=0.76, bottom=0.14, wspace=0.16)
    return _save(fig, "yield_curves")


def credit_chart(hy=None):
    """Serie storica del credito HY europeo (OAS proxy iTraxx Crossover)."""
    if not MPL:
        return None
    if hy is None:
        import bellomberg.market_data.macro_rates as mr
        hy = mr.get_eu_hy_credit()
    pts = (hy or {}).get("points") or []
    status = (hy or {}).get("status")
    # lo status NON si ignora: fonte KO/assente -> nessun grafico (meglio la figura
    # assente che un livello di credito spacciato per corrente); fonte vecchia ->
    # si disegna ma si DICHIARA stale nel sottotitolo e sull'ultimo punto.
    if not pts or status in ("error", "needs_connector", "declared_gap"):
        return None
    stale = (status == "stale")
    vals = [p["value"] * 100 for p in pts]   # % -> bps
    x = list(range(len(vals)))
    apply_style()
    fig = plt.figure(figsize=(9.2, 3.6))
    ax = fig.add_axes([0.075, 0.15, 0.85, 0.60])
    ax.plot(x, vals, color="#2E5496", lw=1.8, zorder=3)
    ax.fill_between(x, vals, min(vals) - 15, color="#2E5496", alpha=0.06, zorder=1)
    ax.plot([x[-1]], [vals[-1]], "o", ms=6, color=(DOWN if stale else AMBER_D), zorder=5)
    ax.annotate(("%.0f bps (STALE)" if stale else "%.0f bps") % vals[-1],
                xy=(x[-1], vals[-1]), xytext=(7, 0), textcoords="offset points",
                fontsize=8.4, color=(DOWN if stale else INK), va="center", fontweight="bold")
    ax.set_ylabel("OAS (bps)", fontsize=8); ax.set_xlabel("Giorni (storico disponibile)", fontsize=8)
    ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True); ax.set_xlim(0, len(x) + 12)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(BASE); ax.spines["bottom"].set_color(BASE); ax.tick_params(length=2)
    sub = hy.get("label", "")
    if stale:
        sub = "DATO STALE (as of " + str(hy.get("as_of") or "n.d.") + ") · " + sub
    _titlebar(fig, "Credito HY europeo — OAS (proxy iTraxx Crossover)",
              sub, "macro_rates · " + str(hy.get("source", "FRED")), hy.get("as_of"))
    return _save(fig, "eu_hy_credit")


if __name__ == "__main__":
    print("Genero i grafici tassi dai dati VERI (macro_rates)...")
    print("curve:", yield_curves_chart())
    print("credito:", credit_chart())
