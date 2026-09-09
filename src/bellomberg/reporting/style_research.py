"""
Stile grafici "Research Note" — print-grade, light (#178).
Sostituisce style_citadel (dark dashboard) nei PDF: palette navy+gold sobria,
sfondo bianco, gridline leggere, titoli left-aligned, source line.
API IDENTICA a style_citadel: drop-in replacement per charts_agent & co.
"""
import os as _os
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as _fm
    MPL_AVAILABLE = True
    # Font Arial-like ROBUSTO: Arial (Windows), Liberation (Linux), DejaVu fallback
    _FONT_CANDS = [
        ("Arial", "C:/Windows/Fonts/arial.ttf"),
        ("Liberation Sans", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ("Liberation Sans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ("DejaVu Sans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    FONT = "DejaVu Sans"
    for _nm, _p in _FONT_CANDS:
        try:
            if _p and _os.path.exists(_p):
                _fm.fontManager.addfont(_p); FONT = _nm; break
        except Exception:
            continue
except ImportError:
    MPL_AVAILABLE = False
    FONT = "sans-serif"


# Palette research (print) — chiavi compatibili con style_citadel
COLORS = {
    "bg": "#FFFFFF",
    "panel": "#FFFFFF",
    "fg": "#262626",
    "muted": "#6F6F6F",     # B-DC6: era #808080 = 3.95:1 su bianco (FAIL AA) -> 5.02:1.
                            # Usato SOLO come testo (label assi, tick, sottotitolo, footer).
    "grid": "#EEF0F3",
    "gold": "#8D6A00",      # B-DC6: era #BF9000 = 2.91:1 -> FAIL sia come testo (4.5:1)
                            # sia come oggetto grafico portatore di info (3:1). Ora 5.01:1.
    "gold_light": "#C9A227",
    "cyan": "#4472C4",       # Office blue
    "cyan_dark": "#2E5496",  # navy2
    "green": "#548235",
    "red": "#C00000",
    "orange": "#ED7D31",
    "purple": "#7A2E2E",     # burgundy per heatmap negativa
    "white": "#FFFFFF",
    "navy": "#1F3864",       # Office dark navy
    "steel": "#4472C4",
    "rule": "#BFBFBF",
}

# Serie multi-asset ORDINATA e sobria (niente arcobaleno)
SEQ = [
    COLORS["navy"], COLORS["steel"], COLORS["orange"], "#A6A6A6",
    "#8FAADC", COLORS["green"], COLORS["gold"], "#264478",
    "#6B7A8F", "#8C6D2F", "#4A6B8A", "#7E8CA0",
]


def apply_citadel_style():
    """Nome storico mantenuto per compatibilita' — applica lo stile RESEARCH light."""
    if not MPL_AVAILABLE:
        return
    plt.rcParams.update({
        "figure.facecolor": COLORS["bg"],
        "axes.facecolor": COLORS["panel"],
        "axes.edgecolor": COLORS["rule"],
        "axes.labelcolor": COLORS["muted"],
        "axes.titlecolor": COLORS["navy"],
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
        "axes.titlelocation": "left",
        "axes.titlepad": 12,
        "axes.labelsize": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": COLORS["grid"],
        "grid.linestyle": "-",
        "grid.linewidth": 0.6,
        "grid.alpha": 1.0,
        "xtick.color": COLORS["muted"],
        "ytick.color": COLORS["muted"],
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "text.color": COLORS["fg"],
        "font.family": FONT,
        "font.size": 9,
        "legend.facecolor": COLORS["bg"],
        "legend.edgecolor": COLORS["rule"],
        "legend.fontsize": 8,
        "legend.framealpha": 0.0,
        "figure.titlesize": 13,
        "figure.titleweight": "bold",
        "savefig.facecolor": COLORS["bg"],
        "savefig.edgecolor": "none",
    })


apply_research_style = apply_citadel_style  # alias esplicito


def add_branding(fig, title, subtitle=None):
    """Titolo + subtitle stile research note (left-aligned, navy + grigio).

    FIX scritte sovrapposte: RISERVA spazio in alto (subplots_adjust) cosi' i
    titoli non cadono mai sopra gli assi del grafico. Applicato solo a figure
    a singolo subplot standard (le multi-subplot gestiscono il proprio layout).
    """
    if not MPL_AVAILABLE:
        return
    # va="top" su entrambi + gap ampio: niente sovrapposizione su figure basse
    fig.suptitle(title, fontsize=12, color=COLORS["navy"], fontweight="bold",
                 x=0.02, y=0.978, ha="left", va="top")
    if subtitle:
        fig.text(0.02, 0.905, subtitle, fontsize=8, color=COLORS["muted"],
                 ha="left", va="top")
    try:
        if len(fig.axes) == 1:
            # riserva spazio in alto per titolo+subtitle, in basso per la source line
            fig.subplots_adjust(top=0.83, bottom=0.14, left=0.09, right=0.97)
    except Exception:
        pass


def add_footer(fig, text="Source: Bellomberg Quant Engine"):
    """Source line in basso a sinistra (standard research)."""
    if not MPL_AVAILABLE:
        return
    fig.text(0.02, 0.015, text, fontsize=6.5, color=COLORS["muted"], ha="left")


def colorize_bar_by_sign(values):
    """Verde sobrio per positivi, rosso mattone per negativi."""
    return [COLORS["green"] if v >= 0 else COLORS["red"] for v in values]


def fmt_eur(v):
    if v is None:
        return "N/A"
    if abs(v) >= 1_000_000:
        return "EUR {:+,.2f}M".format(v / 1_000_000)
    if abs(v) >= 1_000:
        return "EUR {:+,.1f}k".format(v / 1_000)
    return "EUR {:+,.0f}".format(v)


def fmt_pct(v):
    if v is None:
        return "N/A"
    return "{:+.2f}%".format(v)
