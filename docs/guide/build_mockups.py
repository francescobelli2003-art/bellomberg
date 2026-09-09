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
    ("09-vol-deck", "Vol Deck", "Scadenze, greche e laboratorio strategie"),
    ("10-edge-scanner", "Edge Scanner", "Segnali da verificare"),
    ("11-agent-chat", "Agent Chat", "Una domanda, la prospettiva dello specialista"),
    ("12-agents-live", "Agents Live", "Seguire il lavoro del comitato"),
    ("13-agent-progress", "Progressi agenti", "Score salvati, incertezza e lezioni"),
    ("14-memo-archive", "Memo Archive", "La storia della ricerca"),
    ("15-decisions", "Decisioni", "La tua risposta alle proposte del comitato"),
    ("16-trade-entry", "Trade Entry", "Registrare operazioni gia eseguite"),
    ("17-movements", "Movimenti", "Registro delle operazioni e della cassa"),
    ("18-mandate-journal", "Mandato e Diario", "Le tue regole e la storia delle tue tesi"),
    ("19-settings", "Impostazioni", "Stato del sistema e copie di sicurezza"),
]
NAV_SHORT = ["DASH", "PERF", "FAVS", "MKT", "NEWS", "FUND", "FCTR",
             "MTC", "VOLS", "EDGE", "CHAT", "LIVE", "SCORE", "MEMO",
             "DECN", "TRADE", "MOVES", "MNDT", "CONFIG"]


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
        self.text(248, 102, title, 30, TEXT, 650)
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
        c.tag(248, 160, "Ticker: SYN-A", 200)
        c.tag(458, 160, "CATALOGO", 155)
        c.tag(875, 160, "Chain–Strategie", 190)
        c.panel(248, 208, 1004, 140, "Scadenze e copertura")
        for k, (label, state, col) in enumerate([("0 DTE", "Fuori mesh", MUTED), ("14 DTE", "Caricata", CYAN), ("35 DTE", "Parziale", GOLD), ("63 DTE", "Errore fonte", RED)]):
            x = 268 + k * 242
            c.tag(x, 259, label, 215, col)
            c.text(x + 8, 316, state, 14, col)
        c.panel(248, 368, 412, 390, "Gambe • DEMO")
        c.table(268, 435, ["Lato", "Tipo", "Strike", "Premio"], [
            ["Buy", "Call", "100", "6"],
            ["Sell", "Call", "110", "2"],
        ], [84, 88, 86, 104], 47)
        c.lines(268, 604, ["Dati sintetici: moltiplicatore 100", "Premio per unita • stessa scadenza", "", "Greche: ispeziona ogni contratto", "Fonti e timestamp restano separati"], 14, gap=27)
        c.panel(680, 368, 572, 390, "Laboratorio strategie")
        for yy in (455, 505, 555, 605):
            c.line(713, yy, 1217, yy)
        c.line(713, 555, 1217, 555, MUTED)
        c.poly([(713,635),(895,635),(1060,441),(1217,441)], GOLD, 4)
        c.poly([(713,624),(780,619),(846,603),(913,561),(980,510),(1046,473),(1113,457),(1217,452)], CYAN, 3)
        c.poly([(713,616),(810,595),(907,557),(1004,510),(1101,480),(1217,466)], MUTED, 2, dash="7 5")
        c.text(713, 674, "Scadenza", 14, GOLD)
        c.text(813, 674, "Scenario", 14, CYAN)
        c.text(908, 674, "Oggi teorico", 14, MUTED)
        c.text(713, 716, "BSM europeo • costi iniziali • nessun ordine", 14, MUTED)
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
        c.tag(248, 162, "Agente: Quant", 230)
        c.tag(490, 162, "Run: DEMO C", 210, MUTED)
        c.panel(248, 214, 622, 344, "Hit rate e intervallo di incertezza • DEMO")
        for yy in (302, 366, 430, 494):
            c.line(278, yy, 840, yy)
        pts=[(335,418),(485,378),(785,346)]
        for x, y in pts:
            c.line(x, y-63, x, y+63, MUTED, 3)
            c.line(x-9, y-63, x+9, y-63, MUTED, 3)
            c.line(x-9, y+63, x+9, y+63, MUTED, 3)
            c.rect(x-5,y-5,10,10,CYAN,5)
        c.poly(pts[:2], CYAN, 3)
        c.text(646, 422, "Dato assente", 13, GOLD, anchor="middle")
        for x,label in [(335,"A"),(485,"B"),(635,"—"),(785,"C")]:
            c.text(x,527,"Run "+label,13,MUTED,anchor="middle")
        c.panel(890, 214, 362, 344, "Leggere lo score")
        c.lines(912, 291, ["Campione e orizzonte", "Intervallo Wilson 95%", "Delta: solo coorti comparabili", "Nessun punteggio inventato", "", "Lo storico puo avere buchi."], 15, gap=40)
        c.panel(248, 578, 1004, 180, "Lezione salvata • DEMO")
        c.lines(270, 646, ["Separare un'ipotesi di miglioramento da una modifica applicata e misurata.",
                           "Una riflessione salvata non e fine-tuning e non prova causalita."], 17, gap=35)
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
        c.tag(248,160,"Mandato",160,MUTED)
        c.tag(420,160,"Diario",145)
        c.panel(248,210,286,548,"Le tue note")
        c.tag(268,270,"+ Nuova nota",245)
        c.text(268,352,"Demo equity A",19,GOLD,600)
        c.lines(268,384,["Tesi titolo • versione 3","Origine: utente","", "Scenario macro", "Nota • versione 1", "", "Mostra archiviate"],15,gap=38)
        c.panel(554,210,698,548,"Tesi e revisioni • DEMO")
        c.text(578,279,"La condizione che cambia la tesi",24,TEXT,600)
        c.text(578,312,"SYN-A   •   Tesi titolo   •   Versione 3",14,GOLD)
        c.lines(578,365,["La societa e inventata. Il testo illustra un metodo:", "", "Tesi: spiegare quale ipotesi sto facendo.", "Evidenza: distinguere fatti e interpretazioni.", "Invalidazione: scrivere cosa mi farebbe cambiare idea.", "", "Le note non vengono inviate automaticamente agli agenti."],16,gap=33)
        c.line(578,610,1227,610)
        c.text(578,647,"Storico: v1 → v2 → v3",16,MUTED)
        c.tag(578,689,"Salva versione",185)
        c.tag(780,689,"Cronologia",150,MUTED)
        c.tag(947,689,"Archivia",140,MUTED)
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
