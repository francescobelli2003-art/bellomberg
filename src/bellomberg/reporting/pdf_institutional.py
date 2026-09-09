"""
pdf_institutional.py - Memo PDF TOP istituzionale (#183), stile Aurum/Alphadyne.
Cover con banda laterale + grafici, KPI strip, tabella metriche, corpo memo formattato.
Font ROBUSTO (Arial su Windows, Liberation su Linux, Helvetica fallback reportlab).
"""
import os, re
from datetime import datetime

from bellomberg.core.paths import REPORT_DIR as _REPORT_DIR

REPORT_DIR = str(_REPORT_DIR)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors as C
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
                                    Spacer, Table, TableStyle, Image, PageBreak, NextPageTemplate)
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    RL = True
except ImportError:
    RL = False

NAVY=C.HexColor("#1F3864") if RL else None
BLUE=C.HexColor("#2E5496") if RL else None
# B-DC6 (contrasto WCAG AA, misurato). Il fondo peggiore NON e' il bianco ma il LGREY
# #F2F4F7 delle righe alterne delle tabelle: i ratio sotto sono su quello.
# Oro DECORATIVO (filetto sec(), rect cover): non veicola informazione -> esente dai
# minimi sul testo, e il PM l'ha appena approvato -> resta #BF9000, identico al pixel.
GOLD=C.HexColor("#BF9000") if RL else None
# Oro TESTO: era #BF9000 = 2.91:1 su bianco / 2.65 su LGREY (FAIL) -> 5.01 / 4.54
GOLD_TXT=C.HexColor("#8D6A00") if RL else None
GREY=C.HexColor("#6F6F6F") if RL else None     # era #808080 = 3.95/3.58 FAIL -> 5.02/4.56
LGREY=C.HexColor("#F2F4F7") if RL else None
RULE=C.HexColor("#D9DDE3") if RL else None
INK=C.HexColor("#262626") if RL else None
GREEN=C.HexColor("#507B32") if RL else None    # era #548235: 4.54 su bianco ma 4.12 su LGREY FAIL -> 4.98/4.52
ORANGE=C.HexColor("#B65310") if RL else None   # era #ED7D31 = 2.77/2.51, il PEGGIORE del memo -> 4.96/4.50
RED=C.HexColor("#C00000") if RL else None      # 6.48/5.88 gia' conforme
# Tema app "OBSIDIAN" (match UI Electron): fascia cover nera + wordmark ambra
OBSIDIAN=C.HexColor("#050608") if RL else None
AMBER=C.HexColor("#FFA51E") if RL else None
AMBER_DEEP=C.HexColor("#B97A00") if RL else None
TEXTLT=C.HexColor("#ECF1FA") if RL else None
TEXTDIM=C.HexColor("#95A1BA") if RL else None
MUTEDB=C.HexColor("#6A7793") if RL else None   # era #66738E = 4.25:1 su obsidian (FAIL): ora 4.51
W,Hh=(A4 if RL else (595,842))

_FONT_DONE=False
def _register_fonts():
    """Registra Arial-like robusto. Ritorna (reg,bold,italic) nomi font reportlab."""
    global _FONT_DONE
    if _FONT_DONE: return ("LS","LSB","LSI")
    cands=[("C:/Windows/Fonts/arial.ttf","C:/Windows/Fonts/arialbd.ttf","C:/Windows/Fonts/ariali.ttf"),
           ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf"),
           ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf")]
    for reg,bold,ital in cands:
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont("LS",reg))
                pdfmetrics.registerFont(TTFont("LSB",bold if os.path.exists(bold) else reg))
                pdfmetrics.registerFont(TTFont("LSI",ital if os.path.exists(ital) else reg))
                _FONT_DONE=True
                return ("LS","LSB","LSI")
            except Exception:
                continue
    # fallback reportlab built-in
    return ("Helvetica","Helvetica-Bold","Helvetica-Oblique")


# --- B-DC7: LOCKUP UNICO --------------------------------------------------------
# Prima il marchio era disegnato DUE volte a mano con costanti indipendenti, e i due
# gesti erano OPPOSTI: sulla cover il filetto SOVRASTA il wordmark (1.154x, sborda),
# sul masthead era piu' CORTO (0.764x, sottolineava tre quarti di parola); e il
# marchio piccolo aveva il filetto piu' spesso in assoluto (1.134pt contro 0.8pt).
# Radice: il filetto della cover era agganciato alla COLONNA (`band-0.8*cm`), non al
# wordmark -> non era riusabile, e il masthead si era reinventato un 2.3cm a occhio.
# Qui le proporzioni NON sono inventate: sono MISURATE sulla cover approvata dal PM e
# rese parametriche, con il filetto ancorato al wordmark. A size=21 la cover esce
# identica al disegno precedente (scarto < 0.001pt).
_LK_TEXT     = "BELLOMBERG"
_LK_TAG      = "PERSONAL AI HEDGE FUND TERMINAL"
_LK_TAG_SIZE = 0.338095   # corpo tagline / corpo wordmark  (7.1/21)
_LK_TAG_DX   = 0.040495   # rientro ottico tagline / corpo  (0.03cm)
_LK_TAG_DY   = 0.647919   # baseline tagline sotto wordmark / corpo (0.48cm)
_LK_RULE_DY  = 1.106862   # filetto sotto wordmark / corpo, CON tagline (0.82cm)
_LK_RULE_DY0 = 0.458943   # idem SENZA tagline (= _LK_RULE_DY - _LK_TAG_DY): conserva
                          # lo stacco fra l'ultima riga di testo e il filetto
_LK_RULE_W   = 1.154155   # larghezza filetto / larghezza wordmark (6.08/5.268 cm)
_LK_RULE_TH  = 0.038095   # spessore filetto / corpo (0.8pt a 21)
_LK_RULE_MIN = 0.8        # pavimento di spessore, in punti (vedi sotto)


def _draw_lockup(cnv,x,y,size,reg,bold,ink,dim,accent,tagline=True):
    """Marchio BELLOMBERG parametrico. (x,y) = baseline del wordmark.
    ink=wordmark, dim=tagline, accent=filetto.

    tagline=False sotto ~18pt: a corpo 12 il payoff uscirebbe a 4.1pt, illeggibile
    (prassi da manuale d'identita': lockup primario vs compatto). Il gesto sopravvive
    perche' il filetto risale conservando lo stesso stacco dall'ultima riga di testo.

    _LK_RULE_MIN: il solo scostamento voluto dalla proporzione pura. Il filetto e'
    AMBRA, che su nero rende (10.3:1) ma su bianco e' chiarissima (1.9:1): a corpo 12
    lo spessore proporzionale sarebbe 0.46pt e sparirebbe. Il pavimento a 0.8pt e' lo
    stesso dei filetti dei titoli di sezione (approvati a schermo). A corpo 21 il
    valore proporzionale VALE gia' 0.8 -> il pavimento non morde e la cover non cambia.
    """
    cnv.saveState()
    cnv.setFillColor(ink); cnv.setFont(bold,size)
    cnv.drawString(x,y,_LK_TEXT)
    w=pdfmetrics.stringWidth(_LK_TEXT,bold,size)
    if tagline:
        cnv.setFillColor(dim); cnv.setFont(reg,size*_LK_TAG_SIZE)
        cnv.drawString(x+size*_LK_TAG_DX,y-size*_LK_TAG_DY,_LK_TAG)
    ry=y-size*(_LK_RULE_DY if tagline else _LK_RULE_DY0)
    cnv.setStrokeColor(accent); cnv.setLineWidth(max(size*_LK_RULE_TH,_LK_RULE_MIN))
    cnv.line(x,ry,x+w*_LK_RULE_W,ry)
    cnv.restoreState()


def _gen_charts(portfolio_data, nav_history):
    """Genera i 3 grafici cover da dati reali. Ritorna (line,hbar,donut) path o None."""
    try:
        import bellomberg.reporting.charts_institutional as ci
    except Exception:
        return None,None,None
    line=hbar=donut=None
    positions=(portfolio_data or {}).get("positions",[])
    # donut allocazione
    try:
        items=[(p["ticker"],p.get("peso_pct") or 0) for p in positions if (p.get("peso_pct") or 0)>0]
        nav=(portfolio_data or {}).get("totale_valore_mercato_eur",0)
        # top_n=10: con ~28 nomi il vecchio top 7 lasciava un "Altri" al 53%, piu'
        # grande di ogni posizione vera -> il grafico non diceva piu' niente
        # top_n=8 = ampiezza della palette categorica validata (oltre, i colori si
        # ripeterebbero e la legenda diventa ambigua). Le posizioni per intero stanno
        # nel grafico "Rendimento per posizione" qui accanto.
        _top=8
        donut=ci.donut_chart(items,f"NAV\n{nav/1000:.0f}k€",
                             f"Allocazione per posizione",
                             sub=f"Peso di mercato · prime {min(_top,len(items))} su {len(items)} posizioni · resto aggregato in \"Altri\"",
                             top_n=_top)
    except Exception: pass
    # hbar rendimento per posizione: TUTTE le posizioni (richiesta PM 15/07).
    # Prima: sorted(desc)[:8] = solo gli 8 vincitori. Su un book di 28 nomi la cover
    # mostrava otto barre verdi e NESSUNA perdita (MSTR a -26% era discusso nel testo
    # del memo ma invisibile nel grafico): selezione silenziosamente lusinghiera.
    try:
        pl=[(p["ticker"],p.get("pl_pct") or 0) for p in positions if p.get("pl_pct") is not None]
        pl=sorted(pl,key=lambda x:-x[1])
        hbar=ci.hbar_chart(pl,"Rendimento per posizione",
                           f"Da inizio mandato · tutte le {len(pl)} posizioni",
                           color_by_sign=True)
    except Exception: pass
    # line NAV
    try:
        nh=nav_history or {}
        navs=nh.get("nav_eur") or []
        cb=nh.get("cost_basis_eur") or []
        dates=nh.get("dates") or []
        if len(navs)>10:
            base=navs[0] or 1
            series={"NAV portafoglio":[v/base*100 for v in navs]}
            if cb and cb[0]: series["Cost basis"]=[v/cb[0]*100 for v in cb]
            xl=[d[5:7] for d in dates] if dates else [str(i) for i in range(len(navs))]
            line=ci.line_chart(series,xl,"Performance: NAV vs cost basis","Indicizzato a 100")
    except Exception: pass
    return line,hbar,donut


def build_institutional_memo(memo_markdown, portfolio_data=None, risk_data=None,
                             nav_history=None, output_path=None, title_date=None, sizing_data=None, scoring_data=None):
    if not RL:
        return None
    REG,BOLD,ITAL=_register_fonts()
    os.makedirs(REPORT_DIR,exist_ok=True)
    if not output_path:
        output_path=os.path.join(REPORT_DIR,"weekly_"+datetime.now().strftime("%Y%m%d_%H%M")+".pdf")
    date_str=title_date or datetime.now().strftime("%d %B %Y")
    line,hbar,donut=_gen_charts(portfolio_data,nav_history)

    # estrai BLUF per la sintesi cover
    m=re.search(r"##+\s*BLUF.*?\n(.*?)(?=\n##|\Z)",memo_markdown,re.DOTALL)
    bluf=(m.group(1).strip() if m else memo_markdown[:600])
    def _frasi(t):
        """Spezza in frasi SENZA rompere le citazioni [src: ...] ne' i ticker (ALFA.L,
        BETA.MI). Taglia solo su punto FUORI da parentesi quadre e seguito da spazio +
        maiuscola: 'ALFA.L' non ha spazio dopo il punto, '[src: news/politics]' sta
        dentro le quadre. Impaginazione pura: nessuna parola tolta o cambiata."""
        out=[]; buf=""; depth=0
        for i,ch in enumerate(t):
            buf+=ch
            if ch=="[": depth+=1
            elif ch=="]": depth=max(0,depth-1)
            elif ch=="." and depth==0 and t[i+1:i+2]==" " and t[i+2:i+3].isupper():
                out.append(buf.strip()); buf=""
        if buf.strip(): out.append(buf.strip())
        return out

    _lines=[b.strip("-• ").strip() for b in re.split(r"\n",bluf) if b.strip()]
    # Il Capo scrive il BLUF in PROSA: un unico paragrafo (~1300 char) che diventava un
    # muro di 32 righe nella colonna nera della cover. Se e' un blocco solo, si spezza.
    if len(_lines)==1 and len(_lines[0])>240:
        bullets=_frasi(_lines[0])
    else:
        bullets=_lines[:5]
    if not bullets: bullets=[bluf[:200]]

    pd=portfolio_data or {}
    mkt_eur=pd.get("totale_valore_mercato_eur",0) or 0
    pl_eur=pd.get("totale_pl_eur",0) or 0
    cash=pd.get("cash_disponibile_eur",0) or 0
    nav_tot=pd.get("nav_total_eur") or (mkt_eur+cash)  # 203: NAV TOTALE esplicito = investito + cash
    # audit/11 §4: 'su investito' = cost basis (mkt - pl), non il market value che
    # include gia' il P/L (percentuale sistematicamente sottostimata)
    _cb=mkt_eur-pl_eur
    pl_pct=(pl_eur/_cb*100) if _cb>0 else 0

    def draw_cover(cnv,doc):
        cnv.saveState()
        band=W*0.38
        cnv.setFillColor(OBSIDIAN); cnv.rect(0,0,band,Hh,stroke=0,fill=1)
        cnv.setFillColor(AMBER); cnv.rect(band-0.11*cm,0,0.11*cm,Hh,stroke=0,fill=1)
        # lockup, vestito SCURO: ambra su obsidian (B-DC7). Reso identico al disegno
        # hardcoded precedente: e' da qui che sono state misurate le proporzioni.
        _draw_lockup(cnv,1.1*cm,Hh-2.3*cm,21,REG,BOLD,AMBER,AMBER_DEEP,AMBER,tagline=True)
        cnv.setFillColor(TEXTLT); cnv.setFont(BOLD,16); cnv.drawString(1.1*cm,Hh-4.4*cm,"Weekly Research Note")
        cnv.setFillColor(TEXTDIM); cnv.setFont(REG,10)
        cnv.drawString(1.1*cm,Hh-5.0*cm,"Comitato Multi-Agent"); cnv.drawString(1.1*cm,Hh-5.45*cm,date_str)
        # sintesi
        cnv.setFillColor(AMBER); cnv.setFont(BOLD,9.5)
        cnv.drawString(1.1*cm,Hh-6.7*cm,"IN SINTESI")
        from textwrap import wrap
        yy=Hh-7.4*cm; cnv.setFont(REG,8.1)
        _floor=1.45*cm   # sopra il disclaimer di cover
        _cut=False
        for b in bullets:
            _w=wrap(b,44)
            # respiro fra i blocchi: 0.30cm (era 0.12) — con un BLUF in prosa spezzato
            # per frasi e' cio' che distingue un elenco leggibile da un muro
            if yy-len(_w)*0.42*cm < _floor:
                _cut=True; break
            cnv.setFillColor(AMBER); cnv.setFont(BOLD,8.1); cnv.drawString(1.1*cm,yy,"—")
            cnv.setFillColor(TEXTDIM); cnv.setFont(REG,8.1)
            for ln in _w: cnv.drawString(1.5*cm,yy,ln); yy-=0.42*cm
            yy-=0.30*cm
        if _cut:
            # troncamento DICHIARATO, mai silenzioso: il testo integrale e' nel corpo
            cnv.setFillColor(AMBER); cnv.setFont(ITAL,7)
            cnv.drawString(1.1*cm,max(yy,_floor),"[...] segue nel BLUF a pagina 2")
        cnv.setFillColor(MUTEDB); cnv.setFont(ITAL,6)
        cnv.drawString(1.1*cm,1.0*cm,"Documento interno. Non costituisce consulenza finanziaria.")
        # grafici a destra
        rx=band+0.5*cm; rw=W-band-0.9*cm; yc=Hh-1.0*cm
        for ch in [line,hbar,donut]:
            if ch and os.path.exists(ch):
                ir=ImageReader(ch); iw,ih=ir.getSize(); h=rw*ih/iw
                cnv.drawImage(ch,rx,yc-h,width=rw,height=h,mask="auto"); yc-=h+0.25*cm
        cnv.setFillColor(GREY); cnv.setFont(ITAL,7); cnv.drawRightString(W-0.7*cm,0.8*cm,"Bellomberg Quant Engine   ·   Pagina 1")
        cnv.restoreState()

    def draw_body(cnv,doc):
        cnv.saveState()
        # stesso lockup della cover, corpo 12, vestito CHIARO: nero + filetto ambra
        # (era navy + rettangolo oro: il blu non regge accanto a una pagina 1 tutta
        # nera e ambra — decisione PM 15/07, la stessa dei titoli di sezione)
        _draw_lockup(cnv,2*cm,Hh-1.4*cm,12,REG,BOLD,OBSIDIAN,GREY,AMBER,tagline=False)
        cnv.setFillColor(GREY); cnv.setFont(REG,7.5)
        cnv.drawRightString(W-2*cm,Hh-1.38*cm,"WEEKLY RESEARCH NOTE   ·   "+date_str.upper())
        cnv.setStrokeColor(NAVY); cnv.setLineWidth(1); cnv.line(2*cm,Hh-1.75*cm,W-2*cm,Hh-1.75*cm)
        cnv.setStrokeColor(RULE); cnv.setLineWidth(0.5); cnv.line(2*cm,1.5*cm,W-2*cm,1.5*cm)
        cnv.setFillColor(GREY); cnv.setFont(ITAL,7)
        cnv.drawString(2*cm,1.15*cm,"Bellomberg Research · Documento interno")
        cnv.drawRightString(W-2*cm,1.15*cm,"Pagina "+str(doc.page))
        cnv.restoreState()

    doc=BaseDocTemplate(output_path,pagesize=A4,leftMargin=2*cm,rightMargin=2*cm,
                        topMargin=2.3*cm,bottomMargin=1.8*cm)
    cover_frame=Frame(0,0,W,Hh,id="cover",leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    body_frame=Frame(2*cm,1.8*cm,W-4*cm,Hh-4.1*cm,id="body")
    doc.addPageTemplates([
        PageTemplate(id="cover",frames=[cover_frame],onPage=draw_cover),
        PageTemplate(id="body",frames=[body_frame],onPage=draw_body),
    ])

    # stili corpo
    # Titoli di sezione: NERO obsidian + filetto AMBRA (decisione PM 15/07, in tre
    # passaggi sul rendering: le barre nere piene "non davano l'idea di report
    # istituzionale"; il navy che le aveva sostituite "non ci sta bene se la prima
    # pagina e' tutto sul nero e sull'ambra"). Cosi' il corpo richiama la cover
    # (nero+ambra) senza blocchi pieni e senza blu.
    h1=ParagraphStyle("h1",fontName=BOLD,fontSize=12.5,textColor=OBSIDIAN,
                      spaceBefore=13,spaceAfter=2,leading=15)
    h2=ParagraphStyle("h2",fontName=BOLD,fontSize=9.8,textColor=OBSIDIAN,
                      spaceBefore=10,spaceAfter=2,leading=13)

    def sec(text, style, thick=None):
        """Titolo di sezione + filetto ambra sotto, tenuti insieme (Paragraph non sa
        fare un bordo solo-sotto: si usa una tabella a una cella con LINEBELOW).
        Il filetto e' DECORATIVO (il titolo porta tutta l'informazione) -> l'ambra
        chiara su bianco e' ammessa qui, dove come TESTO non lo sarebbe."""
        if thick is None:
            thick = 1.2 if style is h1 else 0.8   # ambra e' chiara: serve un filo di corpo
        t=Table([[Paragraph(text,style)]],colWidths=[W-4*cm])
        t.setStyle(TableStyle([("LINEBELOW",(0,0),(-1,-1),thick,AMBER),
                               ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                               ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
        return t
    body=ParagraphStyle("b",fontName=REG,fontSize=9,textColor=INK,leading=13,spaceAfter=5,alignment=4)
    bullet=ParagraphStyle("bul",fontName=REG,fontSize=9,textColor=INK,leading=12.5,leftIndent=12,spaceAfter=3)
    small=ParagraphStyle("sm",fontName=REG,fontSize=7.2,textColor=GREY,leading=9.5,spaceBefore=3,
                         spaceAfter=4,alignment=4)   # note di lettura sotto le tabelle

    story=[NextPageTemplate("body"),PageBreak()]
    # header sezione + KPI strip
    story.append(sec("Sintesi e profilo del portafoglio",h1))
    kpi=[["NAV TOTALE","INVESTITO","CASH","P/L (su investito)","POSIZIONI"],
         [f"{nav_tot:,.0f} €",f"{mkt_eur:,.0f} €",f"{cash:,.0f} €",f"{pl_eur:+,.0f} €  ({pl_pct:+.1f}%)",
          str(pd.get('n_positions',len(pd.get('positions',[]))))]]
    kt=Table(kpi,colWidths=[(W-4*cm)/5]*5)
    kt.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),LGREY),("LINEABOVE",(0,0),(-1,0),2,NAVY),
        ("FONT",(0,0),(-1,0),REG,7),("TEXTCOLOR",(0,0),(-1,0),GREY),
        ("FONT",(0,1),(-1,1),BOLD,10),("TEXTCOLOR",(0,1),(0,1),NAVY),
        ("TEXTCOLOR",(3,1),(3,1),GREEN if pl_eur>=0 else RED),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("LEFTPADDING",(0,0),(-1,-1),8)]))
    story.append(kt); story.append(Spacer(1,0.4*cm))

    # 203: ACTION TABLE renderizzata come pannello (il vecchio renderer la SCARTAVA)
    try:
        # fix verificatore: stessa tolleranza di memory_db (DOTALL: righe interposte ammesse)
        at=re.search(r"##\s*ACTION TABLE.*?((?:\|[^\n]*\n)+)",memo_markdown,re.IGNORECASE|re.DOTALL)
        arows=[]
        if at:
            for ln_ in at.group(1).strip().split("\n"):
                cells=[c.strip() for c in ln_.strip().strip("|").split("|")]
                if not cells or "---" in cells[0] or cells[0].lower() in ("action",""):
                    continue
                if len(cells)>=5:
                    cells=cells[:5]
                    # "0" sulle righe senza movimento -> cella vuota (B-DC4). Lo "0" lo
                    # scrive il Capo perche' glielo impone capo.py:162; a video e' rumore
                    # e suggerisce "movimento nullo" invece di "nessun movimento".
                    # Si normalizza SOLO lo zero puro su HOLD/RESEARCH: un "0 (incasso
                    # ~750)" su una HEDGE resta, perche' li' lo zero ha un contenuto.
                    if (cells[0] or "").strip().upper() in ("HOLD","RESEARCH") and \
                       (cells[2] or "").strip().rstrip("€ ").strip() in ("0","0.0","0,0","-"):
                        cells[2]=""
                    arows.append(cells)
        if arows:
            story.append(sec("Action table - decisioni della settimana",h2))
            adata=[["Azione","Ticker","EUR","Timing","Confidence"]]+arows
            att=Table(adata,colWidths=[2.6*cm,3.2*cm,2.6*cm,(W-4*cm-11.0*cm),2.6*cm])
            asty=[("BACKGROUND",(0,0),(-1,0),OBSIDIAN),("TEXTCOLOR",(0,0),(-1,0),AMBER),
                  ("FONT",(0,0),(-1,0),BOLD,8),("FONT",(0,1),(-1,-1),REG,8.3),
                  ("FONT",(0,1),(0,-1),BOLD,8.3),("FONT",(1,1),(1,-1),BOLD,8.3),
                  ("ALIGN",(2,1),(2,-1),"RIGHT"),
                  ("ROWBACKGROUNDS",(0,1),(-1,-1),[C.white,LGREY]),
                  ("LINEBELOW",(0,0),(-1,-1),0.4,RULE),
                  ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
                  ("LEFTPADDING",(0,0),(-1,-1),6)]
            for i,r_ in enumerate(arows,start=1):
                act=(r_[0] or "").upper()
                col=GREEN if act in ("ADD","BUY") else (RED if act in ("TRIM","SELL") else (GOLD_TXT if act=="HEDGE" else GREY))
                asty.append(("TEXTCOLOR",(0,i),(0,i),col))
            att.setStyle(TableStyle(asty)); story.append(att); story.append(Spacer(1,0.4*cm))
    except Exception:
        arows=[]
    action_panel_rendered=bool(arows)

    # tabella metriche se risk_data
    if risk_data and isinstance(risk_data,dict):
        p=risk_data.get("portfolio",risk_data)
        mrows=[["Metrica","Valore","Lettura"]]
        def add(k,v,r):
            if v is not None: mrows.append([k,v,r])
        add("Volatilita' annualizzata",f"{p.get('vol_annual_pct','')}%","oscillazione tipica annua")
        add("Sharpe ratio",f"{p.get('sharpe','')}","rendimento per unita' di rischio")
        add("Beta vs S&P 500",f"{p.get('beta_vs_spy','')}","sensibilita' al mercato USA")
        add("VaR 95% (1 giorno)",f"{p.get('var_95_1d_pct','')}%","perdita 1 giorno su 20")
        add("Max Drawdown (1 anno)",f"{p.get('max_dd_1y_pct','')}%","massima caduta picco-minimo")
        if len(mrows)>1:
            mt=Table(mrows,colWidths=[7*cm,3*cm,(W-4*cm-10*cm)])
            st=[("BACKGROUND",(0,0),(-1,0),OBSIDIAN),("TEXTCOLOR",(0,0),(-1,0),AMBER),
                ("FONT",(0,0),(-1,0),BOLD,8),("FONT",(0,1),(-1,-1),REG,8.5),
                ("FONT",(1,1),(1,-1),BOLD,8.5),("ALIGN",(1,1),(1,-1),"RIGHT"),
                ("TEXTCOLOR",(2,1),(2,-1),GREY),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[C.white,LGREY]),
                ("LINEBELOW",(0,0),(-1,-1),0.4,RULE),
                ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
                ("LEFTPADDING",(0,0),(-1,-1),6)]
            mt.setStyle(TableStyle(st)); story.append(mt); story.append(Spacer(1,0.5*cm))

    # CRUSCOTTO SCORING (#186b): verdetti deterministici degli specialisti
    if scoring_data:
        try:
            from bellomberg.agents.specialist_scores import collect_scoreboard
            srows = collect_scoreboard(scoring_data)
            if srows:
                story.append(sec("Cruscotto di rischio (score deterministici degli specialisti)", h2))
                # Il grezzo NON e' confrontabile fra domini: il max cambia col numero di
                # metriche disponibili (9/18 e 8/21 sembrano simili ma valgono 50 e 38).
                # Si mostra l'indice NORMALIZZATO 0-100 — la grandezza su cui gli scorer
                # tarano davvero le bande (0-25 basso / 25-50 medio / 50-72 elevato /
                # 72+ critico) — e si tiene il grezzo accanto per tracciabilita'.
                data=[["Dominio","Verdetto","Rischio\n0-100","Score\ngrezzo"]]
                for r in srows:
                    mx=r.get("max_score") or 0
                    idx="{:.0f}".format(100.0*r["score"]/mx) if mx else "n.d."
                    # riga dichiarata n.d.: cella grezzo VUOTA, non "None/None"
                    grezzo="{}/{}".format(r["score"], r["max_score"]) if mx else ""
                    data.append([r["label"], r["verdict"], idx, grezzo])
                stt=Table(data, colWidths=[4.2*cm, 7.4*cm, 2.4*cm, (W-4*cm-14*cm)])
                sty=[("BACKGROUND",(0,0),(-1,0),OBSIDIAN),("TEXTCOLOR",(0,0),(-1,0),AMBER),
                     ("FONT",(0,0),(-1,0),BOLD,8),("FONT",(0,1),(-1,-1),REG,8.5),
                     ("FONT",(1,1),(1,-1),BOLD,8.5),("FONT",(2,1),(2,-1),BOLD,9),
                     ("ALIGN",(2,0),(-1,-1),"RIGHT"),
                     ("TEXTCOLOR",(3,1),(3,-1),GREY),("FONT",(3,1),(3,-1),REG,7.5),
                     ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                     ("ROWBACKGROUNDS",(0,1),(-1,-1),[C.white,LGREY]),
                     ("LINEBELOW",(0,0),(-1,-1),0.4,RULE),
                     ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
                     ("LEFTPADDING",(0,0),(-1,-1),6)]
                for i,r in enumerate(srows, start=1):
                    if not r.get("max_score"):
                        # score non calcolabile: GRIGIO, non verde. Colorare di verde
                        # (frac=0 -> "rischio basso") un dominio SENZA metriche lo fa
                        # leggere come rassicurante: e' un buco, e come tale si dichiara.
                        sty.append(("TEXTCOLOR",(1,i),(2,i),GREY))
                        continue
                    frac=r["score"]/r["max_score"]
                    col=GREEN if frac<0.25 else (GOLD_TXT if frac<0.5 else (ORANGE if frac<0.72 else RED))
                    sty.append(("TEXTCOLOR",(1,i),(1,i),col))
                    sty.append(("TEXTCOLOR",(2,i),(2,i),col))
                stt.setStyle(TableStyle(sty)); story.append(stt)
                story.append(Paragraph(
                    "Indice 0-100 = score grezzo rapportato al suo massimo (piu' alto = piu' "
                    "rischio). E' l'unica colonna confrontabile fra domini: il massimo grezzo "
                    "varia col numero di metriche disponibili per ciascuno specialista. "
                    "Bande: &lt;25 basso · 25-50 medio · 50-72 elevato · &ge;72 critico.", small))
                story.append(Spacer(1,0.4*cm))
        except Exception:
            pass

    # SIZING headroom chart (#184): esposizione vs limiti di rischio.
    # Il grafico OGGI c'e' (verificato sul memo vero): qui si chiude solo il buco
    # latente — `except: pass` faceva sparire la sezione dal PDF senza una riga da
    # nessuna parte il giorno che il chart si rompe (regola no-fallback-silenziosi).
    # NB: esc() e' definita PIU' SOTTO -> qui l'escape XML va fatto a mano.
    if sizing_data and sizing_data.get("positions"):
        ch=None; _sz_err=None
        try:
            import bellomberg.reporting.charts_institutional as ci
            ch=ci.sizing_headroom_chart(sizing_data["positions"])
            if not ch:
                _sz_err="motore grafici non disponibile o posizioni non plottabili"
            elif not os.path.exists(ch):
                _sz_err="file grafico non trovato: "+str(ch)
        except Exception as _e:
            ch=None; _sz_err=type(_e).__name__+": "+str(_e)
        story.append(sec("Esposizione vs limiti di rischio (sizing vol x correlazione)",h2))
        if _sz_err:
            _m=str(_sz_err).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            story.append(Paragraph("[n.d.] Grafico non disponibile - "+_m+
                                   ". Buco dichiarato: nessun dato sostitutivo.", small))
        else:
            ir=ImageReader(ch); iw,ih=ir.getSize(); w_img=W-4*cm
            story.append(Image(ch,width=w_img,height=w_img*ih/iw))
        story.append(Spacer(1,0.4*cm))

    # corpo memo (markdown -> flowables)
    def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    def inline(s):
        s=esc(s); s=re.sub(r"\*\*(.+?)\*\*",r"<b>\1</b>",s); return s
    def _flush_table(buf):
        # 203: le tabelle markdown del memo (es. Tabella Scenari) ora vengono RESE, non scartate
        rows=[]
        for ln_ in buf:
            cells=[c.strip() for c in ln_.strip().strip("|").split("|")]
            if cells and "---" in cells[0]:
                continue
            rows.append([Paragraph(inline(c),ParagraphStyle("tc",fontName=REG,fontSize=7.8,textColor=INK,leading=10)) for c in cells])
        if not rows: return
        ncol=max(len(r) for r in rows)
        rows=[r+[Paragraph("",body)]*(ncol-len(r)) for r in rows]
        tt=Table(rows,colWidths=[(W-4*cm)/ncol]*ncol,repeatRows=1)
        tt.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),NAVY),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[C.white,LGREY]),
            ("LINEBELOW",(0,0),(-1,-1),0.4,RULE),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
            ("LEFTPADDING",(0,0),(-1,-1),4)]))
        # header bianco in grassetto
        for j in range(ncol):
            try: rows[0][j]=Paragraph("<b><font color='#FFFFFF'>"+rows[0][j].text+"</font></b>",
                                      ParagraphStyle("th",fontName=BOLD,fontSize=7.8,leading=10))
            except Exception: pass
        story.append(tt); story.append(Spacer(1,0.3*cm))

    in_action=False
    tbuf=[]
    for ln in memo_markdown.split("\n"):
        ls=ln.strip()
        if ls.startswith("##") and not ls.startswith("###"):
            in_action=bool(re.match(r"##\s*ACTION TABLE", ls, re.IGNORECASE))
        if ls.startswith("|"):
            if not (in_action and action_panel_rendered):  # fallback: se il pannello non c'e', rendi nel corpo
                tbuf.append(ls)
            continue
        if tbuf:
            # audit/11 §5: una tabella che reportlab non parsa non deve SPARIRE dal PDF
            # (fallback stile _safe_para di pdf_report: testo plain escapato + log)
            try: _flush_table(tbuf)
            except Exception as _e:
                from xml.sax.saxutils import escape as _xesc
                print("[pdf_inst] tabella scartata (" + str(_e)[:80] + "): resa come testo")
                for _tl in tbuf:
                    try: story.append(Paragraph(_xesc(str(_tl)), body))
                    except Exception: pass
            tbuf=[]
        if not ls: continue
        if in_action and ls.upper().startswith("## ACTION TABLE"): continue
        if ls.startswith("### "): story.append(sec(inline(ls[4:]),h2))
        elif ls.startswith("## "): story.append(sec(inline(ls[3:]),h1))
        elif ls.startswith("# "): story.append(sec(inline(ls[2:]),h1))
        elif ls.startswith(("- ","* ")): story.append(Paragraph("• "+inline(ls[2:]),bullet))
        else:
            try: story.append(Paragraph(inline(ls),body))
            except Exception:
                # audit/11 §5: mai far sparire il testo del memo dal PDF in silenzio
                from xml.sax.saxutils import escape as _xesc
                try: story.append(Paragraph(_xesc(str(ls)), body))
                except Exception: print("[pdf_inst] riga scartata: " + str(ls)[:60])
    if tbuf:
        try: _flush_table(tbuf)
        except Exception: pass
    try:
        doc.build(story)
        return output_path
    except Exception as e:
        print("[pdf_institutional] build error:",e); return None


if __name__=="__main__":
    pos=[{"ticker":"ALFA.L","peso_pct":20.0,"pl_pct":12.0},{"ticker":"BETA.MI","peso_pct":15.0,"pl_pct":6.0},
         {"ticker":"GAMMA","peso_pct":12.5,"pl_pct":4.0},{"ticker":"DELTA","peso_pct":10.0,"pl_pct":3.0},
         {"ticker":"EPSILON.MI","peso_pct":8.0,"pl_pct":-2.0},{"ticker":"ZETA.MI","peso_pct":7.0,"pl_pct":-1.0},
         {"ticker":"ETA.MI","peso_pct":6.0,"pl_pct":2.0}]
    pdj={"positions":pos,"totale_valore_mercato_eur":146090,"totale_pl_eur":14208,
         "cash_disponibile_eur":125000,"n_positions":18}
    risk={"portfolio":{"vol_annual_pct":12.4,"sharpe":1.42,"beta_vs_spy":0.85,"var_95_1d_pct":-2.1,"max_dd_1y_pct":-8.8}}
    import numpy as np
    nav=list(146090*np.cumprod(1+np.random.default_rng(1).normal(0.0011,0.008,90)))
    nh={"nav_eur":nav,"cost_basis_eur":list(np.linspace(120000,131000,90)),"dates":["2026-03-%02d"%(i%28+1) for i in range(90)]}
    memo="""## BLUF
- NAV a 146k EUR con cash da impiegare
- Regime Burns 1976, recessione sottoprezzata
- Banca MPS tiratissima, preso profitto

## 1. Regime Macro
Il quadro macroeconomico resta dominato dall'inflazione vischiosa. **Il dollaro** e' tirato e i rendimenti reali elevati comprimono le valutazioni.

## 2. Portafoglio
La posizione ALFA.L resta la piu' grande del portafoglio sintetico al 20,0%."""
    print(build_institutional_memo(memo,pdj,risk,nh,"/tmp/MEMO_INST.pdf","8 giugno 2026"))
