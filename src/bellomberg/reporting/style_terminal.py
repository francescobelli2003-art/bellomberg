"""style_terminal.py — identita' visiva condivisa dei grafici Bellomberg.

Tema OBSIDIAN v0.9 dell'app portato sulla carta: fascetta obsidian con titolo AMBRA
in testa al grafico ("echo terminale"), area dati CHIARA e leggibile (ambra su bianco
fallisce il contrasto).

Perimetro del nero (decisione PM 15/07, dopo review delle preview): cover del memo,
TESTATE DEI GRAFICI e intestazioni delle TABELLE. NON i titoli/sottotitoli di sezione
del memo, dove le barre nere "non davano l'idea di report istituzionale" e spezzavano
la pagina in blocchi: quelli sono navy + filetto oro (vedi pdf_institutional.sec()).

Modulo di sole COSTANTI + helper di disegno: non conosce dati ne' business logic.
Usato da charts_rates (tassi), charts_quant (appendice) e charts_institutional (memo)
per non triplicare palette/font/testata.

Palette categorica CAT: validata colorblind-safe (metodo skill dataviz, 15/07).
"""
import os
from datetime import datetime

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib import font_manager
    MPL = True
except Exception:
    MPL = False

# --- font Arial-like ROBUSTO (Windows -> Linux -> fallback) ---
FONT = "DejaVu Sans"
if MPL:
    for _n, _p in [("Arial", "C:/Windows/Fonts/arial.ttf"),
                   ("Arial", "C:/Windows/Fonts/Arial.ttf"),
                   ("Liberation Sans", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
                   ("Liberation Sans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
                   ("DejaVu Sans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]:
        try:
            if _p and os.path.exists(_p):
                font_manager.fontManager.addfont(_p)
                FONT = _n
                break
        except Exception:
            continue

# --- identita' (coerente col tema OBSIDIAN dell'app e del memo) ---
OBS = "#050608"        # nero canvas del terminale
AMBER = "#FFA51E"      # ambra firma (SOLO su fondo obsidian)
AMBER_D = "#B97A00"    # ambra scura (leggibile su bianco)
SUBTLE = "#AEB8CC"     # sottotitolo dentro la fascetta

NAVY = "#1F3864"; STEEL = "#2E5496"; STEEL_L = "#7FA8D0"; GOLD = "#BF9000"
INK = "#1A1D24"; SECOND = "#52514E"; MUTED = "#8A8F98"; GRID = "#E8EAED"; BASE = "#C7CBD2"
UP = "#1B7A4A"; DOWN = "#C0392B"

# categorica validata colorblind-safe (allocazione, serie multiple)
CAT = ["#2E5496", "#1baf7a", "#BF9000", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]

# colori paese per le curve dei rendimenti
C_US = "#2E5496"; C_DE = "#4E8A3C"; C_JP = "#9E2B2B"

# geometria della fascetta (frazioni di figura): serve ai chiamanti per il subplots_adjust
BAR_BOTTOM = 0.855
TOP_MAX = 0.80          # tetto consigliato per l'area dati sotto la fascetta


def apply_style():
    """rcParams comuni: fondo bianco, spine sobrie, griglia impercettibile."""
    if not MPL:
        return
    plt.rcParams.update({
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
        "font.family": FONT, "text.color": INK,
        "axes.edgecolor": BASE, "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.labelcolor": SECOND, "axes.labelsize": 8.5,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True, "legend.frameon": False,
    })


def titlebar(fig, title, subtitle=None, source=None, asof=None, compact=False):
    """Fascetta obsidian + titolo ambra in testa alla figura ("echo terminale").

    compact=True per figure basse (memo, ~2.7"): testo piu' piccolo, stessa fascia.
    L'`asof` va passato SOLO se noto (mai una data inventata: se manca, non si stampa).
    """
    if not MPL:
        return
    fig.patches.append(Rectangle((0, BAR_BOTTOM), 1.0, 1.0 - BAR_BOTTOM, transform=fig.transFigure,
                                 facecolor=OBS, edgecolor="none", zorder=0))
    fig.patches.append(Rectangle((0, BAR_BOTTOM - 0.002), 1.0, 0.004, transform=fig.transFigure,
                                 facecolor=AMBER, edgecolor="none", zorder=1))
    fig.text(0.017, 0.952, str(title).upper(), fontsize=9.5 if compact else 11.5, color=AMBER,
             fontweight="bold", va="center", ha="left", family=FONT)
    if subtitle:
        fig.text(0.017, 0.892, str(subtitle), fontsize=6.8 if compact else 7.9, color=SUBTLE,
                 va="center", ha="left")
    if asof:
        fig.text(0.983, 0.952, "as of " + str(asof), fontsize=6.6 if compact else 7.2,
                 color=AMBER_D, va="center", ha="right", family=FONT)
    if source:
        fig.text(0.017, 0.02, source if str(source).lower().startswith("source")
                 else "Source: " + str(source),
                 fontsize=6.4, color=MUTED, style="italic", va="bottom")


def gap_panel(ax, message, title=None):
    """Pannello "DATO NON DISPONIBILE": il buco si DICHIARA, non si riempie
    (regola PM 14/07, no fallback silenziosi)."""
    if not MPL:
        return
    ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes,
            fontsize=8.5, color=MUTED, style="italic")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(False)
    if title:
        ax.set_title(title, fontsize=8.5, color=INK, fontweight="bold", pad=4)


def save(fig, out_dir, name, dpi=220, stamp="%Y%m%d_%H%M"):
    """Salva in out_dir con timestamp nel nome e chiude la figura."""
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, name + "_" + datetime.now().strftime(stamp) + ".png")
    fig.savefig(p, dpi=dpi)
    plt.close(fig)
    return p
