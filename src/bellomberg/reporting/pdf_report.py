"""
Genera PDF multi-pagina del Weekly Research Note del consigliere.

Pipeline:
1. Riceve markdown del memo (output Opus)
2. Genera grafici matplotlib (via chart_helpers)
3. Compone PDF con reportlab: cover + memo formatted + charts appendix

Uso:
    from pdf_report import build_pdf_report
    path = build_pdf_report(memo_markdown, portfolio_data, macro_data, options_data_dict)
"""
import os
import re
from datetime import datetime
from bellomberg.reporting.i18n import label as _t, number as _n, localized, date_label

from bellomberg.core.paths import REPORT_DIR as _REPORT_DIR

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image,
        Table, TableStyle, KeepTogether,
    )
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    from bellomberg.reporting import charts_agent  # #178: grafici research-style (era chart_helpers)
    CHARTS_AVAILABLE = True
except ImportError:
    CHARTS_AVAILABLE = False


REPORT_DIR = str(_REPORT_DIR)

# Palette research (#178)
if REPORTLAB_AVAILABLE:
    NAVY = colors.HexColor("#0B2545")
    NAVY2 = colors.HexColor("#16345E")
    GOLD = colors.HexColor("#B08D2E")
    INK = colors.HexColor("#1A1A1A")
    GREY = colors.HexColor("#5A6472")
    RULE = colors.HexColor("#D7DCE3")
    BGBOX = colors.HexColor("#F4F6F9")
    GREEN = colors.HexColor("#1B7A4A")
    RED = colors.HexColor("#A12B2B")


def _styles():
    ss = getSampleStyleSheet()
    s = {
        "kicker": ParagraphStyle(
            "kicker", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=8,
            textColor=GOLD, spaceAfter=4, leading=10,
        ),
        "title": ParagraphStyle(
            "title", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=24,
            leading=28, textColor=NAVY, spaceAfter=6, alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=ss["Normal"], fontSize=9.5, leading=13,
            textColor=GREY, alignment=TA_LEFT, spaceAfter=14,
        ),
        "h1": ParagraphStyle(
            "h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=12.5,
            leading=15, textColor=NAVY, spaceBefore=14, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11,
            leading=14, textColor=NAVY2, spaceBefore=11, spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "h3", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=10,
            leading=13, textColor=colors.HexColor("#3E5C76"), spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=ss["Normal"], fontSize=9, leading=13.2,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=ss["Normal"], fontSize=9, leading=13,
            textColor=INK, leftIndent=14, bulletIndent=5, spaceAfter=3,
        ),
        "caption": ParagraphStyle(
            "caption", parent=ss["Normal"], fontSize=8, leading=10,
            textColor=GREY, alignment=TA_LEFT, spaceAfter=12,
        ),
        "footer": ParagraphStyle(
            "footer", parent=ss["Normal"], fontSize=7, leading=9,
            textColor=GREY, alignment=TA_CENTER,
        ),
    }
    return s


def _md_inline_to_rl(text):
    """Converte markdown inline -> tag reportlab paragraph.

    BUGFIX (run 08/06): le celle con '<', '>' o '&' (es. 'MSTR 4.2% -> 2.0%',
    'USD/CNY > 6.85') rompevano il parser XML di reportlab -> Memo PDF NON generato.
    Ora si escapa SEMPRE prima di iniettare i tag <b>/<i>/<font> nostri.
    """
    if text is None:
        return ""
    # 1. Escape XML PRIMA di tutto (i nostri tag li aggiungiamo dopo, restano validi)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 2. Markdown inline -> tag bilanciati per costruzione
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" color="#a83232">\1</font>', text)
    return text


def _safe_para(text, style):
    """Paragraph a prova di markdown sporco: se reportlab non riesce a parsare
    i tag, ripiega su testo plain escapato (mai far fallire l'intero PDF)."""
    from xml.sax.saxutils import escape as _xml_escape
    try:
        return Paragraph(_md_inline_to_rl(text), style)
    except Exception:
        try:
            return Paragraph(_xml_escape(str(text or "")), style)
        except Exception:
            return Spacer(1, 1)


def _parse_markdown_table(lines, start_idx):
    """Trova una tabella markdown a partire da lines[start_idx]. Ritorna (rows, end_idx)."""
    rows = []
    i = start_idx
    while i < len(lines) and lines[i].strip().startswith("|"):
        row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        # Skip alignment row (---|---)
        if not all(re.match(r"^:?-+:?$", c) for c in row):
            rows.append(row)
        i += 1
    return rows, i


def _build_table(rows, styles_):
    """Build una reportlab Table da rows (list of list)."""
    if not rows:
        return None
    # Converti ogni cella in Paragraph per word wrap
    body = ParagraphStyle("td", parent=styles_["body"], fontSize=9, leading=11, alignment=TA_LEFT)
    header = ParagraphStyle("th", parent=body, textColor=colors.white, fontName="Helvetica-Bold")
    data = []
    for r_idx, row in enumerate(rows):
        cells = [_safe_para(c, header if r_idx == 0 else body) for c in row]
        data.append(cells)
    t = Table(data, repeatRows=1)
    # #178: stile sell-side — solo righe orizzontali, niente gabbia
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FB")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("LINEBELOW", (0, 1), (-1, -1), 0.35, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _markdown_to_flowables(md_text, styles_):
    """Convert memo markdown into reportlab flowables."""
    flowables = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            flowables.append(Spacer(1, 4))
            i += 1
            continue

        # Tabella markdown
        if line.startswith("|"):
            rows, i = _parse_markdown_table(lines, i)
            t = _build_table(rows, styles_)
            if t:
                flowables.append(Spacer(1, 6))
                flowables.append(t)
                flowables.append(Spacer(1, 6))
            continue

        # Headers
        if line.startswith("### "):
            flowables.append(Paragraph(_md_inline_to_rl(line[4:]), styles_["h3"]))
        elif line.startswith("## "):
            flowables.append(Paragraph(_md_inline_to_rl(line[3:]), styles_["h2"]))
        elif line.startswith("# "):
            flowables.append(Paragraph(_md_inline_to_rl(line[2:]), styles_["h1"]))
        # Bullets
        elif line.lstrip().startswith(("- ", "* ")):
            txt = line.lstrip()[2:]
            flowables.append(Paragraph("&bull; " + _md_inline_to_rl(txt), styles_["bullet"]))
        elif re.match(r"^\d+\.\s", line):
            txt = re.sub(r"^\d+\.\s", "", line)
            flowables.append(Paragraph(_md_inline_to_rl(txt), styles_["bullet"]))
        else:
            flowables.append(Paragraph(_md_inline_to_rl(line), styles_["body"]))
        i += 1
    return flowables


def _add_chart(flowables, path, caption, styles_, max_width=16 * cm):
    """Helper: append un chart con caption."""
    if not path or not os.path.exists(path):
        return
    try:
        img = Image(path, width=max_width, height=max_width * 0.6)
        img.hAlign = "CENTER"
        flowables.append(KeepTogether([img, Paragraph(caption, styles_["caption"])]))
        flowables.append(Spacer(1, 8))
    except Exception:
        pass


@localized
def build_pdf_report(
    memo_markdown,
    portfolio_data=None,
    macro_data=None,
    options_data_dict=None,
    output_path=None,
):
    """
    Compone il PDF completo del weekly research note.

    Args:
        memo_markdown: testo markdown del memo Opus
        portfolio_data: output di get_portfolio_live (con 'positions')
        macro_data: output di get_macro_dashboard
        options_data_dict: dict {ticker: get_options_data_output}
        output_path: dove salvare (default report/weekly_TIMESTAMP.pdf)
    """
    if not REPORTLAB_AVAILABLE:
        return None

    os.makedirs(REPORT_DIR, exist_ok=True)
    if not output_path:
        output_path = os.path.join(REPORT_DIR, "weekly_" + datetime.now().strftime("%Y%m%d_%H%M") + ".pdf")

    styles_ = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2.4 * cm, bottomMargin=2.0 * cm,  # spazio per masthead/footer (#178)
        title=_t("Weekly Research Note - ") + date_label(),
        author="AI Portfolio Consigliere",
    )

    story = []

    # === COVER PAGE (#178: research style) ===
    story.append(Spacer(1, 0.9 * cm))
    story.append(Paragraph(_t("PORTFOLIO STRATEGY · COMITATO MULTI-AGENT · SINTESI DEL CAPO"),
                           styles_["kicker"]))
    story.append(Paragraph(_t("Weekly Research Note"), styles_["title"]))
    story.append(Paragraph(
        date_label(weekday=True) + datetime.now().strftime(" · run %H:%M CET")
        + _t(" · 6 specialist + Capo · memoria attiva"), styles_["subtitle"]))

    # At a Glance
    if portfolio_data and portfolio_data.get("positions"):
        total = portfolio_data.get("totale_valore_mercato_eur", 0)
        pl = portfolio_data.get("totale_pl_eur", 0)
        n = portfolio_data.get("n_positions", 0)
        cash = (portfolio_data.get("cash_disponibile_eur")
                or portfolio_data.get("available_capital_eur") or 0)
        glance = [
            [_t("AT A GLANCE"), ""],
            [_t("Net Asset Value"), "EUR " + _n(total)],
            [_t("P/L totale (su investito)"), "EUR " + _n(pl, '+,.0f') + "   (" + _n(
                pl / (total - pl) * 100 if (total - pl) > 0 else 0, '+.2f') + "%)"],  # audit/11: % su cost basis, non su NAV che include il P/L
            [_t("Posizioni attive"), str(n)],
            [_t("Cash disponibile"), "EUR " + _n(cash)],
            [_t("Generato"), datetime.now().strftime("%Y-%m-%d %H:%M")],
        ]
        gt = Table(glance, colWidths=[8.2 * cm, 8.8 * cm])
        gt.setStyle(TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (1, 0), colors.white),
            ("FONT", (0, 0), (1, 0), "Helvetica-Bold", 8),
            ("FONT", (0, 1), (0, -1), "Helvetica", 9),
            ("FONT", (1, 1), (1, -1), "Helvetica-Bold", 9),
            ("TEXTCOLOR", (0, 1), (0, -1), GREY),
            ("TEXTCOLOR", (1, 1), (1, -1), INK),
            ("TEXTCOLOR", (1, 2), (1, 2), GREEN if pl >= 0 else RED),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (1, 0), 0.8, NAVY),
            ("LINEBELOW", (0, 1), (1, -2), 0.4, RULE),
            ("LINEBELOW", (0, -1), (1, -1), 0.8, NAVY),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(gt)
        story.append(Spacer(1, 0.5 * cm))

    # Executive Summary (BLUF dal memo) in box con barra oro
    _m = re.search(r"##+\s*BLUF.*?\n(.*?)(?=\n##|\n---|\Z)", memo_markdown, re.DOTALL)
    if _m:
        _bluf = _md_inline_to_rl(_m.group(1).strip()[:1200])
        _bluf = _bluf.replace("\n\n", "<br/><br/>").replace("\n", " ")
        _box = Table([
            [Paragraph(_t("EXECUTIVE SUMMARY"), styles_["kicker"])],
            [Paragraph(_bluf, styles_["body"])],
        ], colWidths=[17 * cm])
        _box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), BGBOX),
            ("LINEBEFORE", (0, 0), (0, -1), 2.2, GOLD),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(_box)

    story.append(PageBreak())

    # === MEMO BODY (markdown -> flowables) ===
    story.extend(_markdown_to_flowables(memo_markdown, styles_))
    story.append(PageBreak())

    # === APPENDIX: CHARTS ===
    story.append(Paragraph(_t("Appendix: Visual Data"), styles_["h1"]))
    story.append(Spacer(1, 8))

    if CHARTS_AVAILABLE:
        # #178: charts_agent con style_research (light, sobrio)
        if portfolio_data and portfolio_data.get("positions"):
            try:
                p = charts_agent.chart_portfolio_treemap(portfolio_data["positions"])
                _add_chart(story, p, _t("Figura 1. Allocazione di portafoglio per peso (treemap)."), styles_)
            except Exception as e:
                story.append(Paragraph("[Chart treemap skipped: " + str(e) + "]", styles_["caption"]))

            try:
                p = charts_agent.chart_pl_bar(portfolio_data["positions"])
                _add_chart(story, p, _t("Figura 2. P/L per posizione (EUR)."), styles_)
            except Exception as e:
                story.append(Paragraph("[Chart P/L skipped: " + str(e) + "]", styles_["caption"]))

        # Macro charts
        if macro_data:
            try:
                p = charts_agent.chart_yield_curve(macro_data)
                _add_chart(story, p, _t("Figura 3. Curva dei rendimenti USA (Fed Funds, 2Y, 10Y)."), styles_)
            except Exception as e:
                story.append(Paragraph("[Chart yield curve skipped: " + str(e) + "]", styles_["caption"]))

            try:
                p = charts_agent.chart_macro_dashboard_bars(macro_data)
                _add_chart(story, p, _t("Figura 4. Indicatori macro chiave (ultimi valori)."), styles_)
            except Exception as e:
                story.append(Paragraph("[Chart macro skipped: " + str(e) + "]", styles_["caption"]))

        # Options OI charts (uno per ticker)
        if options_data_dict:
            for ticker, opt in options_data_dict.items():
                if not opt or "error" in opt:
                    continue
                # Per il chart OI ci servono strikes + calls_oi + puts_oi, non li abbiamo
                # nel formato attuale (solo totali). Skip per ora oppure dopo arricchiamo.
                pass

    # Masthead + footer su ogni pagina (#178)
    def add_furniture(canvas, doc_):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(2 * cm, h - 1.45 * cm, "BELLOMBERG")
        canvas.setFillColor(GOLD)
        canvas.rect(2 * cm, h - 1.58 * cm, 2.45 * cm, 0.05 * cm, stroke=0, fill=1)
        canvas.setFillColor(GREY)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(w - 2 * cm, h - 1.42 * cm,
                               _t("WEEKLY RESEARCH NOTE  ·  ")
                               + date_label().upper()
                               + _t("  ·  MULTI-AGENT COMMITTEE"))
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(0.8)
        canvas.line(2 * cm, h - 1.78 * cm, w - 2 * cm, h - 1.78 * cm)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(2 * cm, 1.55 * cm, w - 2 * cm, 1.55 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        canvas.drawString(2 * cm, 1.2 * cm,
                          _t("Bellomberg Research · Documento interno — non costituisce consulenza finanziaria"))
        canvas.drawRightString(w - 2 * cm, 1.2 * cm, _t("Pagina ") + str(doc_.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=add_furniture, onLaterPages=add_furniture)
    return output_path
