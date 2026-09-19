"""Build original, deliberately synthetic SVG illustrations for the public handbook.

No application modules, providers, environment files or user data are read.
Only the generated *.svg files in docs/assets/product are written.
"""
from __future__ import annotations

from html import escape
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets" / "product"
BG, PANEL, LINE = "#10151d", "#18212c", "#344250"
TEXT, MUTED, GOLD, CYAN, GREEN, RED = (
    "#eef2f7", "#adbac9", "#e2bf72", "#63cddb", "#8ecfa8", "#e89999"
)
PAGES = [
    ("01-command-center", "Command Center", "Portafoglio e contesto"),
    ("02-performance", "Performance", "Rendimento, flussi e attribuzione"),
    ("03-watchlist", "Watchlist", "Una lista di ricerca, separata dal portafoglio"),
    ("04-global-markets", "Global Markets", "Identita, prezzo e fondamentali"),
    ("05-news-desk", "News Desk", "Notizie, fonti ed eventi"),
    ("06-fundamentals", "Fundamentals", "Leggere le ipotesi dietro un valore"),
    ("07-factor-lab", "Factor Lab", "Esposizioni comuni e qualita del campione"),
    ("08-monte-carlo", "Monte Carlo", "Confrontare scenari, senza eseguire ordini"),
    ("09-vol-deck", "Vol Deck", "Una mappa coordinata; dettaglio su richiesta."),
    ("10-edge-scanner", "Edge Scanner", "Segnali da verificare"),
    ("11-agent-chat", "Agent Chat", "Una domanda, la prospettiva dello specialista"),
    ("12-agents-live", "Agents Live", "Seguire il lavoro del comitato"),
    ("13-agent-progress", "Progressi agenti", "Misure, perimetro e lezioni separati."),
    ("14-memo-archive", "Memo Archive", "La storia della ricerca"),
    ("15-decisions", "Decisioni", "La tua risposta alle proposte del comitato"),
    ("16-trade-entry", "Trade Entry", "Registrare operazioni gia eseguite"),
    ("17-movements", "Movimenti", "Registro delle operazioni e della cassa"),
    ("18-mandate-journal", "Mandato e Diario", "Regole di investimento, con effetto e verifica in vista."),
    ("19-settings", "Impostazioni", "Stato del sistema e copie di sicurezza"),
]
NAV_SHORT = ["DASH", "PERF", "FAVS", "MKT", "NEWS", "FUND", "FCTR",
             "MTC", "VOLS", "EDGE", "CHAT", "LIVE", "SCORE", "MEMO",
             "DECN", "TRADE", "MOVES", "MNDT", "CONFIG"]
# Pages redrawn on the 12/09 layouts show the heading the app renders (h1 and intro of the Italian
# catalogue, subtitle in PAGES); the destination name stays in <title>.
APP_TITLES = {9: "Atlante della volatilità", 13: "Risultati con evidenza"}
# Button styles of the pages redrawn on the 12/09 layouts: (fill, stroke, text colour, weight).
BUTTONS = {
    "primary": (GOLD, GOLD, "#15120d", 600),
    "secondary": ("#15243a", LINE, TEXT, 500),
    "pressed": ("#242016", "#7e6742", GOLD, 600),
    "outline": ("#151b24", "#637088", TEXT, 400),
    "journal": ("#17202e", "#3a4860", "#d4deef", 500),
    "banner": ("#10242b", "#46707b", "#8eeaff", 500),
}


class Canvas:
    def __init__(self, index: int):
        self.index = index
        self.main = False
        self.items: list[str] = []
        slug, title, subtitle = PAGES[index - 1]
        self.slug = slug
        self.items += [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="900" viewBox="0 0 1280 900" role="img" aria-labelledby="title desc">',
            f'<title id="title">F{index} {escape(title)} — synthetic DEMO illustration</title>',
            '<desc id="desc">Original explanatory mockup, not a screenshot. All values, names, instruments and research text are invented. No user portfolio data.</desc>',
            '<style>text{font-family:Segoe UI,Arial,sans-serif} .mono{font-family:Consolas,monospace}</style>',
        ]
        self.rect(0, 0, 1280, 900, BG)
        self.rect(0, 0, 1280, 64, "#141c26")
        self.text(28, 41, "Bellomberg", 24, TEXT, 650)
        self.text(207, 39, "Research terminal", 13, MUTED)
        self.rect(490, 18, 390, 31, BG, 4, LINE)
        self.text(504, 39, "Cerca pagina o ticker", 13, MUTED)
        self.text(866, 39, "Ctrl + K", 13, GOLD, 600, "end")
        self.text(1250, 39, "DEMO • Dati interamente sintetici", 13, GOLD, 650, "end")
        self.rect(0, 64, 1280, 67, PANEL)
        self.items.append('<g id="navigation" aria-label="Navigation F1 to F19">')
        for i, short in enumerate(NAV_SHORT, 1):
            x = 12 + (i - 1) * 66
            if i == index:
                self.rect(x, 68, 64, 59, "#2c3541", 3)
                self.rect(x, 125, 64, 3, GOLD)
            self.text(x + 32, 87, f"F{i}", 12, GOLD if i == index else MUTED, anchor="middle")
            self.text(x + 32, 111, short, 12, TEXT if i == index else MUTED, 600, "middle")
        self.items.append('</g>')
        self.line(0, 132, 1280, 132)
        self.main = True
        self.text(248, 102, APP_TITLES.get(index, title), 30, TEXT, 650)
        self.text(248, 132, subtitle, 16, MUTED)
        self.text(248, 794, "DEMO  /  Illustration only. Not a screenshot, recommendation or measured result.", 12, MUTED)

    def position(self, x, y):
        """Reflow content into the full-width main area without scaling type."""
        return (28 + (x - 248) * 1224 / 1004, y + 70) if self.main else (x, y)

    def rect(self, x, y, w, h, fill=PANEL, radius=0, stroke=None):
        x, y = self.position(x, y)
        if self.main:
            w *= 1224 / 1004
        self.items.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}"' + (f' stroke="{stroke}"' if stroke else "") + "/>")

    def text(self, x, y, s, size=16, color=TEXT, weight=400, anchor="start"):
        x, y = self.position(x, y)
        self.items.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(s))}</text>')

    def line(self, x1, y1, x2, y2, color=LINE, width=1, dash=""):
        x1, y1 = self.position(x1, y1)
        x2, y2 = self.position(x2, y2)
        self.items.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"' + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")

    def panel(self, x, y, w, h, title):
        self.rect(x, y, w, h, PANEL, 5, LINE)
        self.text(x + 18, y + 30, title, 17, TEXT, 600)

    def tag(self, x, y, label, width=140, color=GOLD):
        self.rect(x, y, width, 30, "#24303c", 4)
        self.text(x + 12, y + 20, label, 13, color, 600)

    def button(self, x, y, label, width, style="secondary", height=26, size=12, disabled=None):
        """A command drawn as the app styles it; `disabled` is the opacity the page's CSS gives it."""
        fill, stroke, color, weight = BUTTONS[style]
        if disabled:
            self.items.append(f'<g opacity="{disabled}">')
        self.rect(x, y, width, height, fill, 4, stroke)
        self.text(x + width / 2, y + height / 2 + size * 0.36, label, size, color, weight, "middle")
        if disabled:
            self.items.append("</g>")

    def field(self, x, y, label, value, width, unit="", height=22, size=13):
        """Labelled input: label above, value inside the box, unit at the right edge."""
        self.text(x, y, label, 11, MUTED)
        self.rect(x, y + 4, width, height, BG, 3, LINE)
        baseline = y + 4 + height / 2 + size * 0.36
        self.text(x + 6, baseline, value, size, TEXT)
        if unit:
            self.text(x + width - 6, baseline, unit, 11, MUTED, anchor="end")

    def dot(self, x, y, r, fill, stroke=None, width=2):
        x, y = self.position(x, y)
        self.items.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}"' + (f' stroke="{stroke}" stroke-width="{width}"' if stroke else "") + "/>")

    def lines(self, x, y, lines, size=15, color=MUTED, gap=26):
        for i, item in enumerate(lines):
            self.text(x, y + i * gap, item, size, color)

    def table(self, x, y, cols, rows, widths, rowheight=44):
        pos = [x + sum(widths[:i]) for i in range(len(cols))]
        for px, label in zip(pos, cols):
            self.text(px, y, label, 13, MUTED, 600)
        self.line(x, y + 14, x + sum(widths), y + 14)
        for i, row in enumerate(rows):
            ry = y + 46 + i * rowheight
            for j, (px, value) in enumerate(zip(pos, row)):
                self.text(px, ry, value, 15, TEXT if j == 0 else MUTED)
            self.line(x, ry + 15, x + sum(widths), ry + 15)

    def poly(self, points, color=CYAN, width=3, fill="none", dash=""):
        points = [self.position(x, y) for x, y in points]
        self.items.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in points) + f'" fill="{fill}" stroke="{color}" stroke-width="{width}"' + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")

    def chart(self, x, y, w, h, variants=1):
        for k in range(5):
            self.line(x, y + k * h / 4, x + w, y + k * h / 4)
        self.line(x, y, x, y + h)
        for j in range(variants):
            values = [(x + i * w / 25, y + h * (.76 - .46 * i / 25 + .07 * math.sin(i * .8 + j) + j * .08)) for i in range(26)]
            self.poly(values, CYAN if j == 0 else GOLD, 3, dash="" if j == 0 else "7 5")
        self.text(x, y + h + 23, "Inizio", 12, MUTED)
        self.text(x + w, y + h + 23, "Fine periodo DEMO", 12, MUTED, anchor="end")

    def finish(self):
        self.items.append("</svg>")
        return "\n".join(self.items) + "\n"


def draw(index: int) -> Canvas:
    c = Canvas(index)
    if index == 1:
        for x, title, value, foot in [
            (248, "Patrimonio DEMO", "25 000 EUR", "Capitale e mercato"),
            (588, "Posizioni DEMO", "3 strumenti", "Nomi inventati"),
            (928, "Dati del book", "Parziale", "Una quotazione non disponibile"),
        ]:
            c.panel(x, 164, 324, 108, title)
            c.text(x + 18, 229, value, 27, GOLD if x == 248 else TEXT, 600)
            c.text(x + 18, 253, foot, 13, MUTED)
        c.panel(248, 292, 624, 314, "Portafoglio")
        c.table(268, 356, ["Titolo", "Peso DEMO", "Prezzo"], [
            ["Demo equity A", "40%", "100,00 EUR"],
            ["Demo equity B", "32%", "80,00 EUR"],
            ["Demo fund C", "18%", "n.d."],
            ["Cassa", "10%", "2 500 EUR"],
        ], [240, 160, 180], 44)
        c.panel(892, 292, 360, 314, "Curva della quota • DEMO")
        c.chart(914, 365, 306, 150)
        c.tag(912, 558, "Quota", 90)
        c.tag(1012, 558, "Patrimonio", 125, MUTED)
        c.panel(248, 626, 1004, 132, "Prima di avviare il Consigliere")
        c.lines(268, 688, ["Controlla cassa, fonti e mandato. La ricerca utilizza i servizi configurati.",
                           "Il memo e una proposta: la decisione rimane tua."])
        c.tag(1030, 650, "Run Consigliere", 200)
    elif index == 2:
        c.tag(248, 160, "Tearsheet", 140)
        c.tag(400, 160, "Book & Risk", 150, MUTED)
        c.panel(248, 210, 650, 338, "Rendimento nel periodo • DEMO")
        c.chart(280, 277, 584, 207, 2)
        c.text(280, 531, "Quota", 14, CYAN)
        c.text(360, 531, "Benchmark", 14, GOLD)
        c.panel(918, 210, 334, 338, "Come leggere i dati")
        c.lines(938, 281, ["Flussi ≠ rendimento", "Copertura prima del confronto", "Stessa finestra temporale", "Serie ricostruita: dichiarata"], 16, gap=52)
        c.panel(248, 568, 1004, 190, "Attribuzione: prezzo locale, cambio, interazione")
        for k, (name, w, col) in enumerate([("Prezzo locale", 410, CYAN), ("Cambio", 130, GOLD), ("Interazione", 46, MUTED)]):
            y = 626 + k * 43
            c.text(268, y + 3, name, 15)
            c.rect(450, y - 13, w, 18, col, 2)
        c.text(930, 665, "Valori illustrativi", 16, MUTED)
    elif index == 3:
        c.panel(248, 164, 1004, 368, "Preferiti")
        c.table(268, 234, ["Strumento", "Prezzo DEMO", "Stato fonte", "Azione"], [
            ["Demo equity A", "100,00 EUR", "Disponibile", "Apri mercato"],
            ["Demo equity B", "80,00 EUR", "Ritardato", "Apri mercato"],
            ["Demo fund C", "n.d.", "Fonte non disponibile", "Riprova"],
        ], [290, 205, 270, 175], 67)
        c.panel(248, 554, 1004, 204, "Nota di osservazione • DEMO")
        c.lines(270, 618, ["Seguire il prossimo aggiornamento operativo della societa fittizia.",
                           "Separare fatti, ipotesi e condizioni di invalidazione."])
        c.tag(1050, 697, "Salva nota", 170)
        c.text(270, 727, "Aggiungere un preferito non crea una posizione.", 15, GOLD)
    elif index == 4:
        c.rect(248, 164, 1004, 46, PANEL, 5, LINE)
        c.text(266, 194, "Ricerca: Demo equity A / SYN-A", 18)
        c.tag(1085, 173, "Preferito", 145)
        c.panel(248, 230, 672, 332, "Demo equity A • 100,00 EUR • DEMO")
        c.chart(278, 305, 604, 192)
        c.panel(940, 230, 312, 332, "Identita dello strumento")
        c.lines(960, 299, ["Simbolo: SYN-A", "Nome: societa inventata", "Valuta: EUR", "Fonte: illustrazione"], 15, gap=49)
        c.panel(248, 582, 1004, 176, "Conto economico / Bilancio / Cassa / Azionisti / News")
        c.table(268, 649, ["Voce DEMO", "Periodo A", "Periodo B"], [
            ["Ricavi illustrativi", "100", "110"],
        ], [490, 220, 220])
        c.text(268, 738, "Verifica periodo, unita e valuta prima di confrontare.", 14, GOLD)
    elif index == 5:
        c.tag(248, 162, "Tutte le fonti", 170)
        c.tag(430, 162, "Rilevanza", 145, MUTED)
        c.tag(1090, 162, "Aggiorna", 162)
        c.panel(248, 214, 646, 544, "Feed DEMO")
        stories = [
            ("Demo equity A", "La societa fittizia aggiorna le sue ipotesi", "Articolo inventato • apri la fonte"),
            ("Scenario macro", "Una sorpresa nell'attivita cambia il dibattito", "Testo sintetico • nessun evento reale"),
            ("Demo equity B", "Nuove domande sui margini futuri", "Distinguere fatti da interpretazioni"),
        ]
        for k, (tag, title, note) in enumerate(stories):
            y = 288 + k * 142
            c.text(270, y, tag, 14, GOLD, 600)
            c.text(270, y + 33, title, 18, TEXT, 600)
            c.text(270, y + 64, note, 14, MUTED)
            c.line(270, y + 93, 870, y + 93)
        c.panel(914, 214, 338, 544, "Calendario e briefing")
        c.lines(936, 290, ["Controlla la provenienza", "delle date del calendario.", "", "Una data euristica va", "confermata alla fonte ufficiale.", "", "Il briefing e una sintesi,", "non una nuova fonte primaria."], 16, gap=40)
    elif index == 6:
        c.panel(248, 164, 304, 594, "Modelli salvati")
        c.tag(269, 228, "SYN-A • rev. 2", 260)
        c.lines(270, 320, ["Demo equity A", "Ipotesi riviste", "", "SYN-A • rev. 1", "Versione precedente", "", "Mostra modelli vecchi"], 16, gap=42)
        c.panel(572, 164, 680, 594, "Demo equity A • Valutazione DEMO")
        c.text(598, 240, "Le ipotesi vengono prima del risultato.", 24, GOLD, 600)
        c.table(598, 300, ["Assunzione sintetica", "Scenario A", "Scenario B"], [
            ["Crescita normalizzata", "4%", "2%"],
            ["Margine operativo", "12%", "9%"],
            ["Costo del capitale", "8%", "10%"],
            ["Valore modellato", "110", "85"],
        ], [322, 145, 145], 63)
        c.lines(598, 652, ["Il modello non e una quotazione.", "Leggi motore, data, fonti e controlli di plausibilita."], 16)
    elif index == 7:
        c.panel(248, 164, 660, 400, "Esposizioni stimate • DEMO")
        for k, (name, val) in enumerate([("Mercato", .82), ("Value", .35), ("Qualita", .55), ("Momentum", -.26), ("Cambio", -.43)]):
            y = 247 + k * 59
            c.text(269, y, name, 15)
            c.line(530, y - 19, 530, y + 10, MUTED)
            c.rect(530 if val >= 0 else 530 + val * 330, y - 16, abs(val) * 330, 22, CYAN if val >= 0 else GOLD, 2)
            c.text(864, y, f"{val:+.2f}", 14, MUTED, anchor="end")
        c.panel(928, 164, 324, 400, "Qualita della stima")
        c.lines(950, 241, ["Finestra: DEMO", "Copertura: parziale", "Metodo: da leggere", "", "Una relazione statistica", "non prova causalita."], 16, gap=42)
        c.panel(248, 584, 1004, 174, "Riconciliazione beta")
        c.lines(270, 650, ["Confrontare la stessa finestra, gli stessi strumenti e lo stesso metodo.",
                           "Campioni brevi e fattori correlati rendono instabili le stime."], 17, gap=40)
    elif index == 8:
        c.panel(248, 164, 322, 594, "Scenario ipotetico")
        c.lines(270, 238, ["Portafoglio attuale", "", "+ Demo equity D", "Riduci Demo equity A", "", "Orizzonte: illustrativo"], 16, gap=39)
        c.tag(270, 523, "Simula confronto", 276)
        c.lines(270, 616, ["Nessuna scrittura del book.", "Nessun ordine al broker.", "", "Le bande dipendono", "dalle ipotesi del modello."], 15, gap=27)
        c.panel(590, 164, 662, 594, "Distribuzione dei percorsi • DEMO")
        x, y, w, h = 620, 258, 586, 345
        for k in range(5):
            c.line(x, y + k * h / 4, x + w, y + k * h / 4)
        upper=[(x+i*w/30, y+h*.6-i*5) for i in range(31)]
        lower=[(x+i*w/30, y+h*.6+i*2.1) for i in range(31)]
        c.poly(upper + list(reversed(lower)) + [upper[0]], "#285663", 1, "#21424e")
        c.poly([(x+i*w/30,y+h*.6-i*1.5) for i in range(31)], CYAN, 3)
        c.poly([(x+i*w/30,y+h*.6-i*.65) for i in range(31)], GOLD, 3, dash="7 5")
        c.text(620, 653, "Scenario", 16, CYAN)
        c.text(730, 653, "Book attuale", 16, GOLD)
        c.text(620, 705, "Percentili sintetici, non una promessa sul futuro.", 15, MUTED)
    elif index == 9:
        # Layout of 12/09 (VolSurfacePage.tsx, VolWorkbench.tsx): Sottostante and Catalogo in the header,
        # four exclusive tabs, provenance and figures above every tab once a surface exists, the
        # coverage notice outside Acquisizione. One state: a complete download whose 0-1 DTE expiry
        # stays out of the mesh, then the Laboratorio tab with two manual legs already simulated.
        c.text(1000, 90, "Sottostante", 13, MUTED)
        c.rect(1000, 98, 128, 30, "#0b1017", 0, "#637088")
        c.text(1010, 118, "SYN-A", 14)
        c.button(1136, 98, "Catalogo", 116, "secondary", 30, 13)
        for x, label, width in [(248, "Acquisizione", 92), (350, "Strumenti", 76), (436, "Chain", 54), (500, "Laboratorio", 88)]:
            c.button(x, 158, label, width, "pressed" if label == "Laboratorio" else "secondary", 30, 13)
        c.line(248, 206, 1252, 206)
        c.text(248, 232, "SYN-A", 18, TEXT, 600)
        for x, item in [(318, "Spot 100.0"), (386, "fonte spot DEMO"), (486, "istante DEMO"), (576, "smoothing DEMO")]:
            c.text(x, 231, item, 12, MUTED)
        c.line(248, 244, 1252, 244)
        c.line(248, 322, 1252, 322)
        for k, (label, value, sub) in enumerate([
            ("IV ATM · prima scadenza", "21.7%", "Scadenza B"),
            ("Struttura", "Contango", "2.1 pt · front → 60 giorni"),
            ("Celle mancanti nel campione", "3", "I buchi restano vuoti"),
            ("Contesto", "Non richiesto", "Azione provider esplicita"),
        ]):
            x = 248 + k * 251
            if k:
                c.line(x, 244, x, 322)
            c.text(x + 16, 265, label, 13, MUTED)
            c.text(x + 16, 294, value, 22, TEXT, 500)
            c.text(x + 16, 313, sub, 12, MUTED)
        # Coverage notice (VolWorkbench.tsx:273-284): the download is complete, the surface is not.
        c.rect(248, 334, 1004, 48, "#211e18")
        c.rect(248, 334, 2.5, 48, GOLD)
        c.text(264, 353, "Copertura dell’ultima superficie: 3/4 curve · 1 escluse · 0 errori · mesh incompleto", 13, TEXT)
        c.text(264, 372, "Scadenza A · esclusa — 0–1 DTE o scadenza passata: consulta la chain, non il mesh interpolato", 13, GOLD)
        c.button(1102, 343, "Copertura e fonti", 136, "secondary", 30, 13)
        c.rect(248, 394, 1004, 386, PANEL, 5, LINE)
        c.text(266, 420, "Disegna la strategia", 17, TEXT, 600)
        c.text(266, 439, "SYN-A · laboratorio teorico locale. Nessun ordine viene inviato.", 12, MUTED)
        c.rect(1060, 404, 178, 24, "#172b3b", 12, "#426078")
        c.text(1149, 420, "Black–Scholes–Merton · europeo", 11, "#c3dce9", 400, "middle")
        c.line(248, 450, 1252, 450)
        c.rect(249, 451, 311, 328, "#0d1727")
        c.line(560, 450, 560, 780)
        c.text(262, 474, "Gambe", 14, TEXT, 600)
        c.text(312, 474, "2/12", 12, MUTED)
        c.button(470, 458, "+ Manuale", 78, "secondary", 24, 12)
        names = ["Strike", "Premio / unità", "Contratti", "Moltiplicatore", "Giorni a scadenza", "IV %"]
        for k, (head, side, head_fill, head_color, values) in enumerate([
            ("↗ Gamba 1", "Compra", "#133334", "#94e0d0", ["100", "6", "1", "100", "90", "30"]),
            ("↙ Gamba 2", "Vendi", "#33273b", "#e3b2d0", ["110", "2", "1", "100", "90", "27"]),
        ]):
            top = 490 + k * 144
            c.rect(262, top, 284, 136, "#132237", 6, "#2f4a60")
            c.rect(262, top, 284, 19, head_fill, 6)
            c.text(272, top + 14, head, 12, head_color, 600)
            for j, choice in enumerate((side, "Call")):
                c.rect(272 + j * 136, top + 24, 128, 19, BG, 3, LINE)
                c.text(280 + j * 136, top + 38, choice, 12)
                c.text(394 + j * 136, top + 38, "▾", 11, MUTED, anchor="end")
            for j, (label, value) in enumerate(zip(names, values)):
                c.field(272 + (j % 3) * 92, top + 57 + (j // 3) * 37, label, value, 84, height=19, size=12)
            c.line(262, top + 121, 546, top + 121, "#2b3e59")
            c.text(272, top + 131, "Ipotesi modificata nel laboratorio (non quota corrente).", 10, MUTED)
        for k, (label, value, unit) in enumerate([("Spot iniziale", "100", "USD"), ("Prezzo scenario", "105", "USD"),
                                                  ("Tempo trascorso", "30", "giorni"), ("Shock IV", "0", "punti %")]):
            c.field(578 + k * 166, 470, label, value, 154, unit)
        c.text(578, 522, "La forma del rendimento", 16, TEXT, 600)
        c.text(578, 538, "Payoff a 90 giorni e valore teorico intermedio", 11, MUTED)
        c.text(1236, 522, "USD", 12, MUTED, anchor="end")
        # P&L in USD of the two legs above (price, at expiry, today, scenario after 30 days, no IV shock),
        # read once from the app's local laboratory and written here: this script calculates nothing.
        curve = [(60, -400, -399.9, -400.0), (64.67, -400, -399.4, -400.0), (70, -400, -396.7, -399.5),
                 (74.67, -400, -388.6, -397.2), (80, -400, -364.5, -386.3), (84.67, -400, -321.7, -359.0),
                 (90, -400, -240.2, -291.2), (94.67, -400, -140.3, -191.2), (100, -400, -3.2, -36.6),
                 (104, 0, 105.3, 93.1), (104.67, 66.7, 123.2, 114.8), (110, 600, 258.6, 277.5),
                 (114.67, 600, 358.9, 393.0), (120, 600, 447.6, 486.7), (124.67, 600, 502.9, 538.2),
                 (130, 600, 545.0, 571.5), (134.67, 600, 568.0, 586.5), (140, 600, 583.5, 594.6)]
        left, right, top, bottom, low, high = 622, 1232, 552, 650, -520, 720
        def at(price, pnl):
            return (left + (price - 60) / 80 * (right - left), top + (high - pnl) / (high - low) * (bottom - top))
        for tick in (720, 410, 100, -210, -520):
            c.line(left, at(60, tick)[1], right, at(60, tick)[1], "#26364f", 1, "2 5")
            c.text(left - 8, at(60, tick)[1] + 4, str(tick), 11, MUTED, anchor="end")
        for k, label in enumerate(["60,0", "73,3", "86,7", "100,0", "113,3", "126,7", "140,0"]):
            c.text(left + (right - left) * k / 6, 666, label, 11, MUTED, anchor="middle")
        c.line(left, at(60, 0)[1], right, at(60, 0)[1], "#7689a6")
        c.line(at(104, 0)[0], top, at(104, 0)[0], bottom, "#9b804f", 1, "3 5")
        c.poly([at(p, today) for p, _, today, _ in curve], "#91a2ba", 1.6, dash="5 5")
        c.poly([at(p, expiry) for p, expiry, _, _ in curve], GOLD, 3)
        c.poly([at(p, scenario) for p, _, _, scenario in curve], CYAN, 2.5)
        c.dot(*at(104, 0), 4, GOLD)
        for x, label, color in [(578, "A scadenza", GOLD), (670, "Scenario teorico", CYAN), (792, "Oggi teorico", "#91a2ba")]:
            c.line(x, 682, x + 15, 682, color, 2)
            c.text(x + 20, 686, label, 11, MUTED)
        c.text(1236, 686, "Prezzo sottostante (USD)", 11, MUTED, anchor="end")
        c.line(578, 698, 1236, 698)
        c.line(578, 742, 1236, 742)
        for k, (label, value, unit, color) in enumerate([("Esborso iniziale", "400,00", "USD", TEXT),
                                                         ("P&L scenario", "125,54", "USD a 105,00", "#8cdcc5"),
                                                         ("Profitto max a scadenza", "600,00", "USD", TEXT),
                                                         ("Perdita max a scadenza", "400,00", "USD", TEXT)]):
            x = 578 + k * 165
            if k:
                c.line(x - 10, 704, x - 10, 736)
            c.text(x, 715, label, 11, MUTED)
            c.text(x, 735, value, 15, color, 600)
            c.text(x + 50, 735, unit, 11, MUTED)
        c.button(578, 752, "Simula strategia", 118, "primary", 24, 12)
        c.text(710, 768, "Solo calcolo locale · nessun provider o modello AI", 12, MUTED)
    elif index == 10:
        c.tag(248, 164, "Tutte le categorie", 230)
        c.tag(490, 164, "Forza minima", 200, MUTED)
        c.panel(248, 216, 1004, 374, "Segnali sintetici")
        c.table(270, 288, ["Strumento", "Categoria", "Forza DEMO", "Copertura"], [
            ["Demo equity A", "Valutazione", "Alta", "Parziale"],
            ["Demo equity B", "Evento", "Media", "Disponibile"],
            ["Demo fund C", "Opzioni", "n.d.", "Fonte KO"],
        ], [284, 247, 214, 180], 72)
        c.panel(248, 610, 1004, 148, "Dalla graduatoria alla ricerca")
        c.lines(270, 678, ["Apri le fonti. Verifica liquidita, costi e compatibilita con il mandato.",
                           "La forza del segnale non e una probabilita di profitto."], 17, gap=32)
    elif index == 11:
        c.panel(248, 164, 266, 594, "Scegli un agente")
        for k, name in enumerate(["Capo", "Macro", "Events", "Crypto", "Fundamentals", "Quant", "Options"]):
            c.tag(268, 231 + k * 52, name, 225, GOLD if k == 1 else MUTED)
        c.panel(534, 164, 718, 594, "Macro • Conversazione DEMO")
        c.tag(558, 230, "Quali scenari cambierebbero la lettura del book?", 666)
        c.text(558, 303, "Titoli del tuo portafoglio", 14, MUTED)
        c.tag(558, 321, "SYN-A", 126)
        c.tag(696, 321, "SYN-B", 126, MUTED)
        c.tag(834, 321, "SYN-C", 126, MUTED)
        c.rect(558, 381, 666, 89, "#26303c", 5)
        c.lines(578, 413, ["Quali sensibilita macro osservi in Demo equity A?", "Distingui dati, ipotesi e fattori di invalidazione."], 16, TEXT)
        c.lines(558, 516, ["Risposta illustrativa dello specialista:", "• Descrivere l'esposizione, citando le fonti.", "• Esplicitare i dati mancanti e l'orizzonte.", "• Verificare uno scenario contrario alla tesi."], 16, gap=32)
        c.rect(558, 684, 666, 49, BG, 4, LINE)
        c.text(577, 715, "Scrivi una domanda…", 16, MUTED)
        c.text(1202, 715, "Invia", 16, GOLD, 600, "end")
    elif index == 12:
        c.panel(248, 164, 1004, 114, "Run DEMO • fase di ricerca")
        for k, name in enumerate(["Preparazione", "R1", "Confronto", "R2", "Sintesi"]):
            x=275+k*191
            c.tag(x, 221, name, 171, CYAN if k < 2 else MUTED)
        c.panel(248, 298, 528, 460, "Stato dei desk")
        c.table(270, 367, ["Agente", "Stato", "Tool"], [
            ["Macro", "Completato", "4 chiamate"],
            ["Fundamentals", "In corso", "6 chiamate"],
            ["Options", "Fonte KO", "1 errore"],
            ["Quant", "In attesa", "—"],
        ], [165, 163, 150], 67)
        c.panel(796, 298, 456, 460, "Registro recente • DEMO")
        c.lines(818, 378, ["10:01  Avvio ricerca", "10:02  Macro: fonti lette", "10:03  Options: fonte non disponibile", "10:04  Fundamentals: analisi in corso", "", "La presenza di un memo non prova", "che ogni chiamata sia riuscita."], 15, gap=43)
    elif index == 13:
        # Layout of 12/09 (AgentProgressPage.tsx, agent-progress.css): roster in the order of
        # score_history.AGENTS, Rilevazione selector, figures, evidence tabs, the opened chart with the
        # exact reading of the pointed run (13/09, drawn where the stylesheet places it) and the run
        # register. Invented values; qualities as score_history._point assigns them (below
        # SMALL_SAMPLE_N = 10 a sample is small; a desk absent from the scorecard is a missing measure).
        c.button(1128, 96, "Aggiorna dati", 124, "secondary", 30, 13)
        c.line(248, 146, 1252, 146)
        c.line(248, 176, 1252, 176)
        c.text(248, 166, "✓", 13, GOLD, 600)
        c.text(262, 166, "4 run registrate nella finestra", 12, MUTED)
        c.text(452, 166, "Prima registrazione nella finestra: data DEMO", 12, MUTED)
        c.text(740, 166, "Fonte: SQLite agent_score_history + scorekeeper", 12, MUTED)
        c.rect(248, 184, 1004, 28, "#17222b")
        c.rect(248, 184, 2.5, 28, GOLD)
        c.text(262, 203, "Misura corrente disponibile · separata dallo storico · data DEMO", 13, "#b4dae0")
        c.text(248, 236, "Agenti e ruoli", 14, TEXT, 600)
        c.text(430, 236, "Ultima misura", 11, MUTED, anchor="end")
        c.line(248, 246, 430, 246)
        for k, (name, score, role, sample) in enumerate([
            ("Capo / Comitato", "65,0%", "Sintesi e decisioni collettive", "n=40 · Misura disponibile"),
            ("Macro", "n.d.", "Regime economico e liquidità", "n=n.d. · Misura assente"),
            ("Fundamentals", "58,3%", "Valutazioni e tesi societarie", "n=24 · Misura disponibile"),
            ("Quant", "62,5%", "Rischio, fattori e sizing", "n=8 · Campione piccolo"),
            ("Options", "50,0%", "Volatilità e flussi opzioni", "n=12 · Misura disponibile"),
            ("Crypto", "n.d.", "Mercati digitali e derivati", "n=n.d. · Misura assente"),
            ("Event Desk", "61,1%", "Catalyst, notizie e geopolitica", "n=18 · Misura disponibile"),
            ("Red Team", "—", "Revisione critica del comitato", "Ruolo senza score direzionale"),
            ("Reflection", "—", "Lezioni per la run successiva", "Ruolo senza score direzionale"),
            ("Action extractor", "—", "Estrazione delle decisioni", "Ruolo senza score direzionale"),
        ]):
            y = 266 + k * 51
            if k == 0:
                c.rect(248, y - 18, 182, 51, "#2b251b")
                c.rect(248, y - 18, 2.5, 51, GOLD)
            c.text(256, y, name, 13, TEXT, 600)
            c.text(426, y, score, 13, TEXT, anchor="end")
            c.text(256, y + 15, role, 11, MUTED)
            c.text(256, y + 29, sample, 11, MUTED)
            c.line(248, y + 33, 430, y + 33)
        c.text(450, 246, "Capo / Comitato", 20, TEXT, 600)
        c.text(450, 266, "Sintesi e decisioni collettive", 13, MUTED)
        c.text(1064, 232, "Rilevazione", 11, MUTED)
        c.rect(1064, 238, 188, 26, "#101827", 4, LINE)
        c.text(1074, 256, "Ultima disponibile", 12)
        c.text(1244, 256, "▾", 11, MUTED, anchor="end")
        c.text(450, 292, "Il Capo mostra il risultato collettivo delle decisioni del comitato; non una performance individuale isolata.", 12, MUTED)
        c.rect(450, 304, 514, 146, "#11161e", 4, LINE)
        c.text(468, 324, "Hit rate direzionale", 12, MUTED)
        c.text(468, 362, "65,0%", 36, GOLD, 500)
        c.text(570, 362, "26 esiti corretti / 40 call", 12, MUTED)
        c.text(468, 388, "Intervallo al 95%", 12, MUTED)
        c.text(468, 410, "49,5–77,9%", 17, TEXT, 500)
        c.text(570, 410, "Wilson · ampiezza = incertezza", 12, MUTED)
        bar = lambda value: 790 + 156 * value / 100
        c.line(bar(0), 402, bar(100), 402, "#50617b", 1, "5 5")
        c.line(bar(49.5), 402, bar(77.9), 402, GOLD, 2)
        c.line(bar(65.0), 397, bar(65.0), 407, GOLD, 1)
        c.text(bar(0), 418, "0", 10, MUTED)
        c.text(bar(50), 418, "50", 10, MUTED, anchor="middle")
        c.text(bar(100), 418, "100%", 10, MUTED, anchor="end")
        c.text(468, 440, "Edge medio", 12, MUTED)
        c.text(570, 440, "1,2%", 17, TEXT, 500)
        c.text(620, 440, "Rendimento nella direzione della call", 12, MUTED)
        c.rect(984, 304, 268, 146, "#11161e", 4, LINE)
        c.text(1002, 324, "Si può confrontare?", 12, MUTED)
        c.text(1002, 360, "Non confrontabile", 19, GOLD, 600)
        c.lines(1002, 388, ["Campione, orizzonte, metodo o qualità", "diversi: delta non confrontabile"], 12, MUTED, 16)
        c.text(450, 470, "Misura disponibile", 12, MUTED)
        c.text(560, 470, "Misurato: data DEMO", 12, MUTED)
        c.text(680, 470, "Fonte: scorekeeper / agent_score_history", 12, MUTED)
        c.line(450, 509, 1252, 509)
        for x, label in [(450, "Rilevazioni"), (534, "Per azione"), (612, "Per fiducia"), (692, "Singole call"), (778, "Attività")]:
            c.text(x, 500, label, 14, GOLD if x == 450 else TEXT, 600 if x == 450 else 400)
        c.line(450, 509, 516, 509, GOLD, 2)
        c.text(450, 532, "Hit rate nel tempo", 14, TEXT, 600)
        c.text(1252, 532, "Asse orizzontale: sequenza delle run", 12, MUTED, anchor="end")
        left, right, top, bottom = 489, 1227, 556, 644
        level = lambda value: top + (100 - value) / 100 * (bottom - top)
        runs = [left + (right - left) * k / 3 for k in range(4)]
        for value in (0, 25, 50, 75, 100):
            c.line(left, level(value), right, level(value), "#50617b" if value == 50 else "#223045", 1, "5 5" if value == 50 else "")
            c.text(left - 8, level(value) + 4, f"{value}%", 12, MUTED, anchor="end")
        # Reading line on run #4, the selected run's 95% interval, then the points: #1-#2 share cohort
        # and method (joined), #3 has no measurement (below the axis), #4 is not comparable with #3.
        c.line(runs[3], top, runs[3], bottom, "#50617b", 1, "5 5")
        c.line(runs[0], level(62.5), runs[1], level(62.5), GOLD, 2)
        c.line(runs[3], level(49.5), runs[3], level(77.9), "#b7d9ee", 2)
        for value in (49.5, 77.9):
            c.line(runs[3] - 6, level(value), runs[3] + 6, level(value), "#b7d9ee", 2)
        c.dot(runs[0], level(62.5), 4, GOLD, "#102337")
        c.dot(runs[1], level(62.5), 4, GOLD, "#102337")
        c.dot(runs[2], bottom + 8, 4, "none", "#c69670")
        c.dot(runs[3], level(65.0), 6, "#e8f7ff", GOLD)
        for x, label in zip(runs, ["#1", "#2", "#3", "#4"]):
            c.text(x, 670, label, 12, MUTED, anchor="middle")
        c.rect(495, 549, 468, 24, "#11161e")
        c.text(502, 565, "Memo 4 • 65,0% • n=40 • data DEMO · Intervallo al 95% 49,5–77,9% · Misura disponibile", 12)
        c.dot(452, 690, 3.5, GOLD)
        c.text(460, 694, "Misura salvata", 12, MUTED)
        c.line(560, 690, 575, 690, GOLD, 2)
        c.text(580, 694, "Stessa coorte e metodo", 12, MUTED)
        c.text(740, 694, "Barra verticale: intervallo al 95% della selezione", 12, MUTED)
        c.text(450, 722, "Registro delle run", 14, TEXT, 600)
        c.text(1252, 722, "Ultime 4 registrate", 12, MUTED, anchor="end")
        for x, head in [(458, "Memo"), (560, "Completata"), (760, "Hit rate Capo / Comitato"), (1010, "Call")]:
            c.text(x, 742, head, 12, MUTED)
        c.line(450, 750, 1252, 750)
        c.rect(450, 751, 802, 24, "#25291f")
        for x, value, color in [(458, "#4 ↗", GOLD), (560, "data DEMO", TEXT), (760, "65,0%", TEXT), (1010, "40", TEXT)]:
            c.text(x, 768, value, 13, color)
    elif index == 14:
        c.panel(248, 164, 290, 594, "Archivio memo")
        for k, (title, desc) in enumerate([("Run DEMO C","Sintesi disponibile"),("Run DEMO B","Testo + PDF"),("Run DEMO A","Solo testo")]):
            y=236+k*126
            c.text(270,y,title,19,GOLD if k==0 else TEXT,600)
            c.text(270,y+30,desc,14,MUTED)
            c.line(270,y+59,516,y+59)
        c.text(270,718,"Cerca: Ctrl + Shift + F",14,MUTED)
        c.panel(558, 164, 694, 594, "Memo del comitato • DEMO")
        c.text(584,238,"Sintesi e domande aperte",26,GOLD,600)
        c.lines(584,287,["Questo testo e interamente inventato.", "Illustra la struttura di un memo, non una raccomandazione."],16)
        for y, title, body in [(380,"Scenario centrale","Ipotesi, orizzonte e fonti da verificare."),(477,"Rischi e tesi contraria","Condizioni che invaliderebbero il ragionamento."),(574,"Decisioni collegate","La risposta del PM rimane separata dal trade.")]:
            c.text(584,y,title,19,TEXT,600)
            c.text(584,y+31,body,16,MUTED)
            c.line(584,y+60,1224,y+60)
        c.tag(584,697,"Apri decisioni",185)
        c.tag(784,697,"PDF se disponibile",210,MUTED)
    elif index == 15:
        c.tag(248,164,"Stato: tutte",190)
        c.tag(450,164,"Run: tutte",170,MUTED)
        c.panel(248,216,1004,298,"Proposte del comitato • DEMO")
        c.table(270,286,["Titolo","Proposta","Stato","Risposta"],[
            ["Demo equity A","Approfondire","Da valutare","Apri"],
            ["Demo equity B","Rivedere la tesi","Rinviata","Apri"],
        ],[254,300,220,150],73)
        c.panel(248,534,1004,224,"Feedback del PM • DEMO")
        c.lines(270,601,["La tesi resta aperta: manca evidenza sul presupposto principale.",
                          "Rivalutare dopo nuove fonti. Questo testo e un esempio inventato."],17)
        c.tag(270,689,"Salva feedback",185)
        c.tag(470,689,"Veto",100,MUTED)
        c.tag(585,689,"Archivia",140,MUTED)
        c.text(1230,738,"Lo stato non esegue un ordine.",14,GOLD,anchor="end")
    elif index == 16:
        c.panel(248,164,566,594,"Registra un trade gia eseguito • DEMO")
        fields=[("Titolo","SYN-A"),("Azione","BUY"),("Quantita","10"),("Prezzo per unita","100,00"),("Valuta","EUR")]
        for k,(name,value) in enumerate(fields):
            y=235+k*78
            c.text(270,y,name,14,MUTED)
            c.rect(468,y-23,324,43,BG,4,LINE)
            c.text(484,y+5,value,19)
        c.tag(270,688,"Anteprima e conferma",310)
        c.panel(834,164,418,278,"Cassa")
        c.lines(856,236,["Versamento o prelievo", "Data, importo e causale", "Controlla il riepilogo prima del salvataggio"],15,gap=44)
        c.tag(856,375,"Registra movimento",372)
        c.panel(834,462,418,296,"Controllo prima del salvataggio")
        c.lines(856,537,["Quantita e prezzo non sono un totale.", "Verifica simbolo e valuta.", "", "Dopo un timeout controlla il registro", "prima di ripetere l'invio.", "", "Nessuna connessione di esecuzione ordini."],14,gap=29)
    elif index == 17:
        c.panel(248,164,1004,273,"Cronologia • DEMO")
        for k,name in enumerate(["Cassa","Demo equity A","Demo equity B"]):
            y=255+k*63
            c.text(270,y,name,15)
            c.line(466,y-5,1220,y-5)
            for j in range(2):
                x=550+k*177+j*175
                c.rect(x,y-15,20,20,GOLD if k==0 else CYAN,10)
        c.panel(248,457,1004,301,"Registro delle operazioni")
        c.table(270,527,["Sequenza","Tipo","Strumento","Importo DEMO"],[
            ["Evento A","Versamento","Cassa","5 000 EUR"],
            ["Evento B","BUY","Demo equity A","1 000 EUR"],
            ["Evento C","SELL","Demo equity B","400 EUR"],
        ],[218,206,296,200],60)
    elif index == 18:
        # The two tabs are exclusive in the app (MandatoPage.tsx): two separate views, each in one
        # possible state. Mandato: a saved personalised mandate, a recoverable draft of undeclared number
        # format not yet restored, section Rischio open. Values of the public example profile
        # (mandato_pm.py ESEMPIO), badges of lib/mandato.ts, field order of the schema.
        c.text(248, 164, "Mandato", 15, GOLD, 600)
        c.text(320, 164, "Diario", 15, MUTED)
        c.line(248, 173, 1252, 173)
        c.line(248, 173, 296, 173, GOLD, 2)
        c.text(248, 193, "Formato dei numeri della bozza: italiano (1.234,56).", 12, MUTED)
        c.button(1044, 179, "Compila con profilo di esempio", 208, "outline", 22, 12)
        c.rect(248, 207, 1004, 40, "#10242b", 0, "#295d6a")
        c.text(262, 223, "BOZZA RECUPERABILE", 12, "#8eeaff", 700)
        c.text(370, 223, "· data DEMO · Il formato dei numeri di questa bozza non è dichiarato o non è riconosciuto:", 12, "#8eeaff")
        c.text(262, 240, "il ripristino la legge in formato italiano (1.234,56). Verifica i numeri prima di salvare.", 12, "#8eeaff")
        c.button(1004, 215, "RIPRISTINA ESPLICITAMENTE", 172, "banner", 24, 11)
        c.button(1182, 215, "SCARTA", 62, "banner", 24, 11)
        c.rect(248, 256, 172, 178, "#11161e", 4, "#354052")
        c.text(260, 277, "Sezioni del mandato", 13, TEXT, 600)
        for k, name in enumerate(["Profilo", "Rischio", "Dimensionamento", "Cassa", "Disciplina", "Opzioni", "Note"]):
            y = 300 + k * 19
            if k == 1:
                c.rect(250, y - 13, 168, 18, "#2b251b")
                c.rect(250, y - 13, 1.6, 18, "#efbb66")
            c.text(260, y, f"{k + 1:02d}", 12, "#aeb9ca")
            c.text(282, y, name, 12, "#efbb66" if k == 1 else TEXT)
            c.text(410, y, "✓", 12, "#83cfd2", anchor="end")
        c.text(436, 277, "02", 17, "#ffa51e", 600)
        c.text(462, 277, "Rischio", 17, TEXT, 600)
        for k, (label, width, badge, badge_width, color, border, description, bounds, value, unit) in enumerate([
            ("Volatilità obiettivo", 88, "Validazione", 62, "#75dff3", "#2a5f69", "Volatilita' annua del portafoglio che cerca.",
             "Obbligatorio · Intervallo ammesso: 5–60", "15", "% annua"),
            ("VaR al 99% · un giorno", 109, "Motore", 40, "#5de4b7", "#286653", "Perdita massima a un giorno che accetta (VaR 99%), in % del patrimonio.",
             "Obbligatorio · Intervallo ammesso: 0.5–15", "2", "% del patrimonio"),
            ("Perdita massima dal picco", 124, "Validazione", 62, "#75dff3", "#2a5f69", "Drawdown massimo dal picco che accetta sul portafoglio.",
             "Obbligatorio · Intervallo ammesso: 5–70", "20", "%"),
        ]):
            y = 302 + k * 45
            c.text(436, y, label, 13, TEXT, 600)
            c.text(438 + width, y - 3, "*", 11, "#ffa51e")
            c.rect(450 + width, y - 12, badge_width, 16, "none", 0, border)
            c.text(454 + width, y, badge, 11, color)
            c.text(436, y + 15, description, 11, MUTED)
            c.text(436, y + 28, bounds, 10, MUTED)
            c.rect(790, y - 11, 64, 24, "#0b1017", 0, "#637088")
            c.text(798, y + 5, value, 13)
            c.text(860, y + 5, unit, 11, MUTED)
        c.rect(956, 256, 296, 178, "#11161e", 4, "#354052")
        c.text(972, 278, "Testo per il comitato", 13, TEXT, 600)
        c.text(1238, 278, "DA VALIDARE", 12, "#ffa51e", 600, "end")
        c.lines(972, 304, ["Compila i sette blocchi e chiedi l’anteprima.", "Il testo apparirà qui solo dopo la",
                           "validazione del server."], 12, "#cad3de", 18)
        c.line(972, 372, 1238, 372)
        c.text(972, 392, "IMPRONTA", 11, MUTED)
        c.text(1032, 392, "DEMO", 13, "#83cfd2", 600)
        c.text(972, 412, "data DEMO · Personalizzato", 11, MUTED)
        c.rect(248, 442, 1004, 32, "#151b24")
        c.line(248, 442, 1252, 442, "#637088")
        c.text(264, 463, "Nessuna modifica da salvare", 13, MUTED)
        c.button(848, 446, "Scarta modifiche", 116, "outline", 24, 12)
        c.button(972, 446, "Verifica e apri anteprima", 168, "outline", 24, 12)
        c.button(1148, 446, "Salva mandato", 96, "primary", 24, 12, disabled=0.5)
        # Diario (JournalPage.tsx): note 12 edited and not saved. The app disables what would lose or
        # bypass the draft: Archivia nota and Usa questa versione come bozza. Three versions, all loaded.
        c.line(248, 488, 1252, 488, MUTED, 1, "4 6")
        c.text(248, 512, "Mandato", 15, MUTED)
        c.text(320, 512, "Diario", 15, GOLD, 600)
        c.line(248, 521, 1252, 521)
        c.line(320, 521, 360, 521, GOLD, 2)
        title = "La condizione che cambia la tesi"
        body = ["Società inventata: il testo illustra un metodo, non una view.",
                "Separa i fatti osservati dall'ipotesi e da ciò che la smentirebbe."]
        c.text(248, 542, "Le tue note", 14, TEXT, 600)
        c.button(248, 550, "Nuova nota", 84, "primary", 24, 12)
        c.text(248, 592, "Cerca nel Diario", 11, MUTED)
        c.rect(248, 597, 198, 22, BG, 3, LINE)
        c.text(256, 612, "Titolo, ticker o testo", 12, "#6d7886")
        for x, label, value in [(248, "Mostra", "Note attive"), (351, "Tipo", "Tutti i tipi")]:
            c.text(x, 636, label, 11, MUTED)
            c.rect(x, 641, 95, 22, BG, 3, LINE)
            c.text(x + 7, 656, value, 12)
            c.text(x + 89, 656, "▾", 11, MUTED, anchor="end")
        c.line(248, 674, 446, 674)
        c.text(248, 692, "SYN-A", 12, "#f3bf78")
        c.text(446, 692, "v3", 11, MUTED, anchor="end")
        c.text(248, 709, title, 13, "#efbb66", 600)
        c.text(248, 724, "data DEMO", 12, "#9aa9c2")
        c.line(248, 732, 446, 732)
        c.text(248, 750, "Nota macro", 12, "#f3bf78")
        c.text(446, 750, "v1", 11, MUTED, anchor="end")
        c.text(248, 767, "Uno scenario da osservare", 13, TEXT, 600)
        c.rect(466, 530, 524, 246, "#0e121a", 6, "#354052")
        c.text(480, 548, "Bozza conservata nella sessione ·", 12, "#83cfd2")
        c.text(627, 548, "Nota 12 · Versione 3", 12, MUTED)
        c.text(976, 548, "Modifiche da salvare", 12, "#ffd294", anchor="end")
        c.text(480, 566, "Tipo di nota", 11, MUTED)
        c.rect(480, 570, 240, 22, BG, 3, LINE)
        c.text(488, 585, "Tesi su un titolo", 12)
        c.text(714, 585, "▾", 11, MUTED, anchor="end")
        c.text(732, 566, "Simbolo", 11, MUTED)
        c.text(772, 566, "facoltativo", 10, MUTED)
        c.rect(732, 570, 244, 22, BG, 3, LINE)
        c.text(740, 585, "SYN-A", 12)
        c.text(480, 608, "Titolo", 11, MUTED)
        c.text(976, 608, f"{len(title)} / 160 caratteri", 11, MUTED, anchor="end")
        c.rect(480, 612, 496, 24, BG, 3, LINE)
        c.text(488, 629, title, 14, TEXT, 600)
        c.text(480, 652, "Ipotesi → Evidenze → Rischi → Cosa mi farebbe cambiare idea", 12, MUTED)
        c.text(480, 670, "La tua nota", 11, MUTED)
        c.rect(480, 674, 496, 40, BG, 3, LINE)
        c.lines(488, 690, body, 12, TEXT, 16)
        # The body character counter is omitted from this illustration; the title counter stays.
        c.text(976, 728, "Origine: inserita dall’utente", 11, MUTED, anchor="end")
        c.rect(467, 736, 522, 39, "#151b24")
        c.line(467, 736, 989, 736, "#637088")
        c.text(480, 750, "La versione precedente sarà conservata", 12, MUTED)
        c.button(480, 754, "Salva nuova versione", 128, "primary", 20, 11)
        c.button(614, 754, "Scarta modifiche", 104, "journal", 20, 11)
        c.button(724, 754, "Archivia nota", 88, "journal", 20, 11, disabled=0.45)
        c.text(1006, 542, "Come evolve la tua idea", 14, TEXT, 600)
        c.lines(1006, 560, ["Rileggi le versioni precedenti. Puoi usarle", "come bozza di una nuova revisione."], 12, MUTED, 15)
        for y, version, action, marker in [(601, "v3", "Testo aggiornato", "▶"), (641, "v2", "Testo aggiornato", "▼"), (774, "v1", "Nota creata", "▶")]:
            c.line(1006, y - 17, 1252, y - 17)
            c.text(1006, y, version, 12, "#f2c488")
            c.text(1030, y, action, 13)
            c.text(1252, y, marker, 10, MUTED, anchor="end")
            if y < 774:
                c.text(1030, y + 14, "data DEMO", 12, MUTED)
        c.text(1006, 675, title, 12, TEXT, 600)
        c.text(1006, 690, "Tesi su un titolo · SYN-A", 11, MUTED)
        c.rect(1006, 695, 246, 18, "#090e16")
        c.text(1012, 708, "Società inventata: tesi riletta dopo un dato.", 12, "#cad3de")
        c.text(1006, 726, "Origine utente · Attiva in questa versione", 10, MUTED)
        c.button(1006, 732, "Usa questa versione come bozza", 246, "journal", 20, 11, disabled=0.45)
        c.text(1252, 794, "Two separate views: the app shows one tab at a time.", 12, MUTED, anchor="end")
    elif index == 19:
        c.rect(248,164,1004,594,"#131a23",5,LINE)
        c.panel(354,192,792,539,"Impostazioni")
        c.text(1118,225,"×",25,MUTED,anchor="end")
        c.lines(380,286,["Backend: disponibile (DEMO)", "Versione e modelli: configurazione del backend"],17,gap=38)
        c.line(380,354,1118,354)
        c.text(380,393,"Backup del database",21,TEXT,600)
        c.text(380,427,"Controlla data e risultato della copia.",16,MUTED)
        c.tag(380,451,"Crea backup",205)
        c.line(380,504,1118,504)
        c.text(380,542,"Attivita pianificate",21,TEXT,600)
        c.table(380,585,["Attivita DEMO","Stato","Dettaglio"],[
            ["Aggiornamento prezzi","Disabilitata","Nessuna esecuzione"],
        ],[275,175,270],45)
        c.text(380,707,"Le chiavi si configurano nel file .env privato.",15,GOLD)
    return c


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (slug, _, _) in enumerate(PAGES, 1):
        (OUT / f"{slug}.svg").write_text(draw(i).finish(), encoding="utf-8")
    print(f"Generated {len(PAGES)} original DEMO SVG files. No runtime data read.")


if __name__ == "__main__":
    main()
