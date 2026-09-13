"""
charts_institutional.py - Grafici research istituzionale TOP (#183).
Font Liberation Sans (Arial-like), palette Office, valori finali, gridline impercettibili.
"""
import os
import sys
from datetime import datetime

from bellomberg.core.paths import REPORT_DIR
try:
    import numpy as np, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    MPL = True
    # Font Arial-like ROBUSTO: Arial su Windows, Liberation su Linux, DejaVu fallback
    _FONT_CANDIDATES = [
        ("Arial", "C:/Windows/Fonts/arial.ttf"),
        ("Arial", "C:/Windows/Fonts/Arial.ttf"),
        ("Arial", "/System/Library/Fonts/Supplemental/Arial.ttf"),
        ("Arial", "/Library/Fonts/Arial.ttf"),
        ("Liberation Sans", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ("Liberation Sans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ("DejaVu Sans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    FONT = "DejaVu Sans"
    for _name, _p in _FONT_CANDIDATES:
        try:
            if _p and os.path.exists(_p):
                font_manager.fontManager.addfont(_p); FONT = _name; break
        except Exception:
            continue
    if FONT == "DejaVu Sans":
        print("[charts_institutional] Font Arial/Liberation non disponibile: ripiego DejaVu Sans.", file=sys.stderr)
except ImportError:
    MPL = False; FONT = "sans-serif"

NAVY="#1F3864"; NAVY2="#2E5496"; BLUE="#4472C4"; BLUE_LT="#8FAADC"
ORANGE="#ED7D31"; GREEN="#548235"; RED="#C00000"; GREY="#A6A6A6"; INK="#262626"
# Serie categoriche: palette CAT validata colorblind-safe (style_terminal, 15/07).
# SEQ resta per le serie ORDINATE (line/grouped), dove la gerarchia navy->blu conta.
SEQ=[NAVY, BLUE, ORANGE, GREY, BLUE_LT, GREEN, "#BF9000", "#264478"]
GRID="#EEF0F3"; RULE="#BFBFBF"; STEEL=BLUE; AMBER=ORANGE
DIR=str(REPORT_DIR / "inst_charts")

# identita' "terminale" del memo (fascetta obsidian + titolo ambra): stesso modulo
# usato da charts_rates e charts_quant, cosi' memo e appendix parlano una lingua sola
import bellomberg.reporting.style_terminal as _st
from bellomberg.reporting.i18n import label as _t, number as _n, localized

def _setup():
    plt.rcParams.update({"figure.facecolor":"white","axes.facecolor":"white",
        "font.family":FONT,"axes.edgecolor":RULE,"text.color":INK,
        "axes.labelcolor":"#595959","xtick.color":"#808080","ytick.color":"#808080",
        "xtick.labelsize":7.5,"ytick.labelsize":7.5,"axes.linewidth":0.6,
        "xtick.major.width":0.5,"ytick.major.width":0.5})

def _save(fig,name):
    os.makedirs(DIR,exist_ok=True)
    p=os.path.join(DIR,name+"_"+datetime.now().strftime("%H%M%S%f")[:-3]+".png")
    fig.savefig(p,dpi=220); plt.close(fig); return p

def _title(fig,t,sub=None,source=None):
    """Fascetta obsidian+ambra (tema terminale del memo, deciso col PM il 15/07).
    I chiamanti lasciano l'area dati sotto ~0.80 di figura."""
    _st.titlebar(fig,t,sub,source,compact=True)

@localized
def line_chart(series,x_labels,title,sub=None,source=None,fname="line",ylabel=None):
    source = _t("Source: Bellomberg Quant Engine") if source is None else source
    if not MPL: return None
    try:
        _setup(); fig,ax=plt.subplots(figsize=(5.2,2.7))
        names=list(series.keys()); n=len(series[names[0]])
        for i,(name,vals) in enumerate(series.items()):
            col=SEQ[i%len(SEQ)]; ls=(0,(5,2)) if i>=2 else "-"
            lw=1.8 if i==0 else 1.3
            ax.plot(range(n),vals,color=col,lw=lw,ls=ls,label=name,zorder=3,solid_capstyle="round")
            if i==0:  # area fill leggera sotto la serie principale
                ax.fill_between(range(n),vals,min(vals),color=col,alpha=0.05,zorder=1)
            # etichetta valore finale
            ax.annotate(f"{vals[-1]:.0f}",xy=(n-1,vals[-1]),xytext=(4,0),
                        textcoords="offset points",va="center",fontsize=6.8,color=col,fontweight="bold")
        ticks=list(range(0,n,max(1,n//5)))
        ax.set_xticks(ticks); ax.set_xticklabels([x_labels[t] for t in ticks if t<len(x_labels)],fontsize=7)
        if ylabel: ax.set_ylabel(ylabel,fontsize=7.5,color="#595959")
        for s in ["top","right"]: ax.spines[s].set_visible(False)
        ax.spines["left"].set_color(RULE); ax.spines["bottom"].set_color(RULE)
        ax.grid(axis="y",color=GRID,lw=0.7,zorder=0); ax.set_axisbelow(True)
        ax.tick_params(length=2)
        ax.legend(loc="upper center",bbox_to_anchor=(0.5,-0.14),ncol=min(4,len(series)),
                  frameon=False,fontsize=7,handlelength=1.6,columnspacing=1.5)
        ax.set_xlim(-1,n+3)
        _title(fig,title,sub,source)
        fig.subplots_adjust(left=0.08,right=0.95,top=0.78,bottom=0.21)
        return _save(fig,fname)
    except Exception as e:
        plt.close("all"); print("line err",e); return None

@localized
def hbar_chart(items,title,sub=None,source=None,unit="%",fname="hbar",color_by_sign=False):
    source = _t("Source: Bellomberg Quant Engine") if source is None else source
    if not MPL: return None
    try:
        items=list(items); _setup()
        n=len(items)
        # passo per riga adattivo: con un book lungo (28 nomi) il passo fisso da 0.44"
        # generava una figura da 13" che sulla cover non ci sta. Sotto le 10 voci
        # resta il passo pieno di prima.
        per=0.44 if n<=10 else max(0.17, 4.9/n)
        fs=7.5 if n<=10 else (6.6 if n<=18 else 5.8)
        fig,ax=plt.subplots(figsize=(5.2,per*n+1.0))
        labels=[k for k,_ in items]; vals=[v for _,v in items]
        colors=[(GREEN if v>=0 else RED) for v in vals] if color_by_sign else [SEQ[i%len(SEQ)] for i in range(n)]
        y=list(range(n))[::-1]
        ax.barh(y,vals,color=colors,height=0.58 if n<=10 else 0.72,zorder=3)
        rng=max(abs(min(vals)),abs(max(vals))) or 1
        name_x=min(0,min(vals))-rng*0.58
        for yi,(lab,v) in zip(y,items):
            ax.text(v+(rng*0.035 if v>=0 else -rng*0.035),yi,f"{v:+.1f}{unit}",
                    va="center",ha="left" if v>=0 else "right",fontsize=fs,fontweight="bold",color=INK)
            ax.text(name_x,yi,lab,va="center",ha="left",fontsize=fs,color="#404040")
        ax.set_yticks([]); ax.set_xticks([]); ax.axvline(0,color="#808080",lw=0.7,zorder=2)
        for s in ax.spines.values(): s.set_visible(False)
        ax.set_xlim(name_x-rng*0.05,max(vals)+rng*0.38)
        ax.set_ylim(-0.7,n-0.3)   # via i margini automatici: su book lunghi erano aria sprecata
        _title(fig,title,sub,source)
        fig.subplots_adjust(left=0.04,right=0.96,top=0.78,bottom=0.12)
        return _save(fig,fname)
    except Exception as e:
        plt.close("all"); print("hbar err",e); return None

@localized
def donut_chart(items,center_label,title,sub=None,source=None,fname="donut",top_n=7):
    source = _t("Source: Bellomberg Quant Engine") if source is None else source
    if not MPL: return None
    try:
        # mai piu' spicchi che colori: la palette CAT validata ne ha 8, e con i%len(CAT)
        # il 9o e 10o nome riprendevano il colore del 1o e 2o. La legenda e' l'UNICA
        # mappatura colore->nome (gli spicchi non hanno etichetta): due spicchi dello
        # stesso colore la rendono ambigua (review 15/07).
        top_n=min(top_n,len(_st.CAT))
        items=sorted(items,key=lambda x:-x[1]); top=items[:top_n]; other=sum(v for _,v in items[top_n:])
        labels=[k for k,_ in top]+([_t("Altri")] if other>0 else [])
        vals=[v for _,v in top]+([other] if other>0 else [])
        _setup(); fig,ax=plt.subplots(figsize=(5.2,2.7))
        # categorie SENZA ordine intrinseco -> palette CAT validata colorblind-safe,
        # non la SEQ navy->blu (che suggerirebbe una gerarchia inesistente e rende
        # gli spicchi adiacenti indistinguibili)
        cols=[_st.CAT[i%len(_st.CAT)] for i in range(len(top))]
        # "Altri" NON e' una posizione: grigio neutro. Con un book lungo l'aggregato
        # e' lo spicchio piu' grande e, in tinta piena, gridava piu' dei nomi veri.
        if other>0: cols.append(GREY)
        w,_=ax.pie(vals,colors=cols,startangle=90,counterclock=False,
                   wedgeprops=dict(width=0.34,edgecolor="white",linewidth=2.2))
        ax.text(0,0,center_label,ha="center",va="center",fontsize=10,color=NAVY,fontweight="bold")
        leg=[f"{l}    {v:.1f}%" for l,v in zip(labels,vals)]
        ax.legend(w,leg,loc="center left",bbox_to_anchor=(1.05,0.5),frameon=False,fontsize=7.5,handlelength=1.0,handleheight=1.0)
        _title(fig,title,sub,source)
        fig.subplots_adjust(left=0.0,right=0.55,top=0.78,bottom=0.05)
        return _save(fig,fname)
    except Exception as e:
        plt.close("all"); print("donut err",e); return None

@localized
def grouped_bars(categories,series,title,sub=None,source=None,fname="grouped",unit=""):
    source = _t("Source: Bellomberg Quant Engine") if source is None else source
    if not MPL: return None
    try:
        _setup(); fig,ax=plt.subplots(figsize=(5.2,2.7))
        n=len(categories); k=len(series); w=0.74/k
        for i,(name,vals) in enumerate(series.items()):
            x=[j+i*w for j in range(n)]
            bars=ax.bar(x,vals,width=w,color=SEQ[i%len(SEQ)],label=name,zorder=3)
            for xx,vv in zip(x,vals):
                ax.text(xx,vv+max(max(series.values()))[0]*0 if False else vv*1.02,f"{vv:.0f}",ha="center",fontsize=6.3,color="#595959")
        ax.set_xticks([j+w*(k-1)/2 for j in range(n)]); ax.set_xticklabels(categories,fontsize=7.5)
        for s in ["top","right"]: ax.spines[s].set_visible(False)
        ax.spines["left"].set_color(RULE); ax.spines["bottom"].set_color(RULE)
        ax.grid(axis="y",color=GRID,lw=0.7); ax.set_axisbelow(True); ax.tick_params(length=2)
        ax.legend(loc="upper center",bbox_to_anchor=(0.5,-0.13),ncol=min(4,k),frameon=False,fontsize=7,handlelength=1.4)
        _title(fig,title,sub,source)
        fig.subplots_adjust(left=0.07,right=0.96,top=0.78,bottom=0.19)
        return _save(fig,fname)
    except Exception as e:
        plt.close("all"); print("grp err",e); return None

@localized
def sizing_headroom_chart(positions, title=None, sub=None, source=None, fname="sizing"):
    """Grafico esposizione vs limite per nome. positions: lista da sizing['positions']
    (ticker, current_pct, max_position_pct, trim_eur)."""
    title = _t("Esposizione attuale vs limite di rischio") if title is None else title
    sub = _t("Barra navy = peso attuale · marker arancio = size massima ammessa · fascia = capacita' residua") if sub is None else sub
    source = _t("Source: Bellomberg Sizing Engine (limiti vol x correlazione)") if source is None else source
    if not MPL or not positions:
        return None
    try:
        items=positions[:12]
        names=[p["ticker"] for p in items]
        cur=[p.get("current_pct",0) for p in items]
        mx=[p.get("max_position_pct",0) for p in items]
        over=[p.get("trim_eur",0)>0 for p in items]
        n=len(items); ys=list(range(n))[::-1]
        fig,ax=plt.subplots(figsize=(7.4,max(2.4,0.42*n+1.0)))
        xmax=max(max(cur+mx)*1.18,5)
        for yi,(c,m,ov) in zip(ys,zip(cur,mx,over)):
            ax.barh(yi,m,color=GRID,height=0.6,zorder=1)
            ax.barh(yi,c,color=(RED if ov else NAVY),height=0.6,zorder=2)
            ax.plot([m,m],[yi-0.3,yi+0.3],color=ORANGE,lw=2.2,zorder=3)
            ax.annotate(f"{c:.1f}%",xy=(c,yi),xytext=(4,0),textcoords="offset points",
                        va="center",fontsize=7.3,fontweight="bold",color=(RED if ov else NAVY))
            ax.annotate(f"max {m:.0f}%",xy=(m,yi),xytext=(5,0),textcoords="offset points",
                        va="center",fontsize=6.8,color=ORANGE,fontweight="bold")
        ax.set_yticks(list(ys)); ax.set_yticklabels(names,fontsize=8)
        ax.set_xlabel(_t("% del capitale investito (cash escluso)"),fontsize=7.5)
        ax.set_xlim(0,xmax)
        for s in ["top","right","left"]: ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color(RULE); ax.tick_params(length=2)
        ax.grid(axis="x",color=GRID,lw=0.7); ax.set_axisbelow(True)
        _title(fig,title,sub)
        fig.text(0.015,0.02,source,fontsize=6,color=GREY,style="italic",family=FONT)
        fig.subplots_adjust(top=0.78,bottom=0.12,left=0.13,right=0.96)
        return _save(fig,fname)
    except Exception as e:
        # era l'UNICA delle 5 funzioni-grafico che perdeva la causa dell'errore
        # (le sorelle stampano tutte): senza, il grafico spariva dal memo E dal log
        plt.close("all"); print("sizing err",e); return None

if __name__=="__main__":
    import numpy as np; np.random.seed(2)
    x=[f"{m}" for m in ["Feb","","","Mar","","","Apr","","","Mag","",""]*10][:120]
    s={"NAV portafoglio":list(100*np.cumprod(1+np.random.normal(0.0013,0.008,120))),
       "Benchmark (SPY)":list(100*np.cumprod(1+np.random.normal(0.0007,0.0075,120))),
       "Cost basis":list(np.linspace(100,116,120))}
    print(line_chart(s,x,"Performance: NAV vs Benchmark","Indicizzato a 100, da inizio mandato"))
    print(hbar_chart([("ALFA",6.0),("BETA",4.0),("GAMMA",3.0),("DELTA",-2.0),("EPSILON",-1.0)],
                     "Rendimento per posizione","Da inizio mandato",color_by_sign=True))
    print(donut_chart([("ALFA",20.0),("BETA",15.0),("GAMMA",12.5),("DELTA",10.0),("EPSILON",8.0),
                       ("ZETA",7.0),("ETA",6.0),("THETA",5.0)],"NAV\n100k€","Allocazione per posizione"))
    print(grouped_bars(["1 giorno","5 giorni","22 giorni"],{"Forecast GARCH":[16,17,18],"Realizzata":[15,16,17]},
                       "Volatilita': forecast vs realizzata","% annualizzata"))
    print("OK")
