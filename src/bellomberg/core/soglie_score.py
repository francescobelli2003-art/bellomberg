"""soglie_score.py — UNA sola fonte di verita' per le soglie degli score del memo e degli
strumenti che leggono le stesse misure (10/10/2026, Opus 5.5, delega PM «decidi tu come un
quant»; censimento delle soglie su main a3f7488).

Regole del modulo:
1. Ogni soglia condivisa da piu' moduli vive QUI e solo qui: specialist_scores,
   positioning_tools, signal_engine, vol_surface e pdf_institutional la importano. Prima le
   bande 25/50/72 stavano in quattro punti e il rapporto VIX/VIX3M aveva due tarature
   (score 0,88-1,06, strumento 0,97/1,03).
2. Dove esiste una storia pubblica gratuita la soglia e' CALIBRATA su percentili con
   `tools/ops/calibra_soglie.py` (riproducibile; i dati scaricati restano in una cache
   gitignored) e CONGELATA qui con fonte, finestra, data e percentile a commento. Il codice
   NON ricalcola a run-time: rilanciare lo script e confrontare e' la verifica.
3. Dove la calibrazione non si puo' fare la soglia resta quella di prima, DICHIARATA
   «giudizio» o «convenzione» col motivo (niente fallback silenziosi).
4. Funzioni pure, nessun I/O, nessuna dipendenza da agents/portfolio (il modulo sta in core
   perche' lo importano anche portfolio e reporting).
"""
from __future__ import annotations

import math
from numbers import Real


def _num(value):
    """Un dato invalido e' assente (None), mai una fascia: NaN confronta falso."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return float(value) if math.isfinite(value) else None


# ===================================================================== BANDE DEL CRUSCOTTO
# Indice 0-100 -> banda 0..3 (basso, medio, elevato, critico). Quartili simmetrici della
# scala: 25/50/75. GIUDIZIO (scala, non storia): il vecchio 72 non aveva motivazione
# scritta e rompeva la simmetria. Stesse soglie per verdetti, pavimenti e colori del PDF.
BANDE_INDICE = (0.25, 0.50, 0.75)


def banda(frac):
    """Indice di banda 0..3 della frazione score/massimo sulle BANDE_INDICE."""
    b1, b2, b3 = BANDE_INDICE
    return 0 if frac < b1 else 1 if frac < b2 else 2 if frac < b3 else 3


# ===================================================================== FRESCHEZZA
# Eta' massima (giorni di calendario) di una chiusura giornaliera: VIX, VIX3M, spread,
# Treasury, TIPS. 6 = weekend + un festivo + 1-2 giorni di ritardo FRED (stesso limite di
# core/freshness OBS_DEFAULT_DAYS). FATTO TECNICO. Prima la volatilita' usava 5 per lo
# stesso VIX che la macro accettava fino a 6.
ETA_MAX_GIORNALIERA_GG = 6


# ===================================================================== VIX / VIX3M
# CALIBRATE (calibra_soglie.py, eseguito il 10/10/2026). Fonte: CBOE VIX_History.csv e
# VIX3M_History.csv, chiusure giornaliere 2009-09-18 -> 2026-10-09 (n = 4291 giorni comuni;
# il file pubblico CBOE del VIX3M parte dal 18/09/2009, la serie VXV 2007-2009 non c'e').
# Ancore del punteggio 0 -> 2 -> 4 -> 6 (peso doppio, interpolate) = percentili
# 50 / 75 / 90 / 97 del rapporto: 0,8842 / 0,9344 / 0,9850 / 1,0460. Il rapporto e' >= 1,00
# nel 7,6% dei giorni. Vincolo economico rispettato: l'ancora del 4 (p90) sta sotto 1,00,
# il punto d'inversione (protezione a 30 giorni cara quanto quella a 3 mesi).
VIX3M_ANCORE = (0.884, 0.934, 0.985, 1.046)
VIX3M_PUNTI = (0.0, 2.0, 4.0, 6.0)
# Punto d'inversione della curva: FATTO (definizione di backwardation).
VIX3M_INVERSIONE = 1.00
# Pavimento IN STRESS = l'ancora piena (p97): oltre, il verdetto volatilita' e' almeno
# «IN STRESS» qualunque sia lo skew.
VIX3M_PAVIMENTO_STRESS = VIX3M_ANCORE[-1]


def struttura_vix(ratio):
    """Codice STABILE della curva VIX/VIX3M dalle STESSE ancore dello score:
    CONTANGO sotto l'ancora del 2 (p75 storico), FLAT fra il p75 e 1,00 (contango sotto la
    norma), BACKWARDATION da 1,00. None se il rapporto non e' valido. Decisione 10/10 (delega
    PM): con CONTANGO sotto la mediana il 42,5% dei giorni 2009-2026 usciva FLAT; dal p75
    FLAT e' il 17,6%, CONTANGO il 74,8%, BACKWARDATION il 7,6% (CBOE, 4291 giorni)."""
    r = _num(ratio)
    if r is None or r <= 0:
        return None
    if r >= VIX3M_INVERSIONE:
        return "BACKWARDATION"
    return "CONTANGO" if r < VIX3M_ANCORE[1] else "FLAT"


# ===================================================================== PREZZO DELLA PROTEZIONE
# Misura INVARIANTE DI SCALA: IV ATM ~30 giorni / realizzata 21 sedute (rapporto, non
# differenza in punti: 4 punti di premio con IV 12 e con IV 60 non sono la stessa cosa).
# CALIBRATE (calibra_soglie.py, 10/10/2026). Fonte: FRED VIXCLS e SP500 (FRED espone 10
# anni dell'S&P 500), finestra 2016-11-08 -> 2026-10-08, n = 2492 sedute; realizzata come
# vol_surface (rendimenti semplici, std campionaria 21 sedute x sqrt 252). Percentili del
# rapporto VIX / realizzata: p20 = 1,0802, p50 = 1,3391, p80 = 1,7715.
# «a sconto» sotto p20, «cara» da p80, «nella norma» in mezzo.
# LIMITE DICHIARATO: la storia gratuita e' il VIX (indice di varianza, include lo skew),
# non l'IV ATM: sull'ATM il rapporto corre qualche punto percentuale piu' basso, quindi
# queste soglie leggono «cara» piu' tardi e «a sconto» piu' spesso del vero. ENTRAMBI I LATI,
# misurati sulla stessa finestra se l'IV ATM fosse il VIX diviso per k (k = VIX / IV ATM):
#   k = 1,00 -> a sconto 19,9% dei giorni, cara 20,0%
#   k = 1,05 -> a sconto 26,1%, cara 15,2%
#   k = 1,10 -> a sconto 32,7%, cara 11,8%
#   k = 1,15 -> a sconto 40,0%, cara 9,3%
# Le soglie NON sono riscalate per k: il rapporto VIX/ATM non ha una storia gratuita e cambia
# col regime di skew, quindi un k fisso sarebbe un proxy non misurato (revisione 10/10).
# Applicate ai singoli titoli (Edge Scan, sintesi della superficie) sono una taratura
# d'INDICE: il premio al rischio di volatilita' dei singoli nomi e' strutturalmente piu' basso.
VRP_SCONTO = 1.08
VRP_CARA = 1.77


def prezzo_protezione(iv, rv):
    """(codice, rapporto): DISCOUNT / NORMAL / EXPENSIVE dal rapporto IV/RV con le soglie
    VRP_*. (None, None) se IV o realizzata mancano o non sono positive."""
    iv, rv = _num(iv), _num(rv)
    if iv is None or rv is None or iv <= 0 or rv <= 0:
        return None, None
    r = iv / rv
    return ("DISCOUNT" if r < VRP_SCONTO else "EXPENSIVE" if r >= VRP_CARA else "NORMAL"), r


# ===================================================================== SKEW
# PUNTEGGIO (options_score): -RR25 a 30-45 giorni in PUNTI VOL ASSOLUTI, ancore 3 -> 0,
# 5 -> 1 (skew equity tipico di SPY a 1 mese, -4/-6), 7 -> 2, 9 -> 3. CONVENZIONE SPX 1 mese,
# NON calibrate (nessuna storia RR25 gratuita). v3 10/10 (revisione avversariale, decisione
# del coordinatore): il punteggio era passato allo skew normalizzato RR25/IV ATM, che SCENDE
# proprio nei crash (l'IV ATM esplode piu' dello skew: calmo -4,5/14 = 0,32, crash -8/42 =
# 0,19). Con VIX/VIX3M = 1,00, IV 42 e RR25 -8 l'indice cadeva da 0,777 a 0,543 e perdeva la
# banda critica: come misura di stress andava al contrario. Torna in punti assoluti.
SKEW_RR25_ANCORE_PT = (3.0, 5.0, 7.0, 9.0)

# LETTURA (sintesi della superficie, informativa): skew NORMALIZZATO = RR25 / IV ATM della
# STESSA scadenza, che descrive la DOMANDA RELATIVA di copertura. CONVENZIONE, NON calibrate:
# skew normalizzato tipico SPX 1 mese 0,20-0,35. Riferimenti 0,15 (piatto) / 0,25 (tipico) /
# 0,35 (bordo alto del tipico) / 0,45.
SKEW_NORM_ANCORE = (0.15, 0.25, 0.35, 0.45)
# «MARCATO» nella sintesi della superficie = dal bordo alto del tipico (0,35).
SKEW_NORM_MARCATO = SKEW_NORM_ANCORE[2]
# Zona neutra (smile simmetrico) del valore normalizzato: 0,03 ~ 0,5 punti vol su IV ~16,
# la vecchia zona +-0,5 punti assoluti. CONVENZIONE.
SKEW_NORM_NEUTRO = 0.03


def skew_normalizzato(rr25, atm_iv):
    """RR25 / IV ATM (segno conservato: negativo = put piu' care). None se manca un dato o
    l'ATM non e' positiva: mai il valore assoluto in punti come ripiego."""
    rr, atm = _num(rr25), _num(atm_iv)
    if rr is None or atm is None or atm <= 0:
        return None
    return rr / atm


def lettura_skew(norm):
    """Codice STABILE: MARCATO / NORMA / NEUTRO / INVERTITO dallo skew normalizzato."""
    n = _num(norm)
    if n is None:
        return None
    if -n >= SKEW_NORM_MARCATO:
        return "MARCATO"
    if -n > SKEW_NORM_NEUTRO:
        return "NORMA"
    if n > SKEW_NORM_NEUTRO:
        return "INVERTITO"
    return "NEUTRO"


# ===================================================================== VOL REALIZZATA
# Percentile della realizzata a 21 sedute sull'ultimo anno: estremi a 20/80 ovunque
# (segnale e sintesi della superficie; prima la sintesi usava 25/75). GIUDIZIO: quintili.
RV_PCT_BASSO = 20
RV_PCT_ALTO = 80


# ===================================================================== MACRO STRESS
# Soglie a gradino per 1 / 2 / 3 punti (sotto la prima = 0).
# VIX: CALIBRATE (calibra_soglie.py, 10/10/2026). Fonte FRED VIXCLS, 1997-01-02 ->
# 2026-10-08, n = 7521: percentili 60 / 80 / 95 = 20,48 / 25,02 / 34,13.
MACRO_VIX_SOGLIE = (20.5, 25.0, 34.1)
# Spread HY (BAMLH0A0HYM2) e IG (BAMLC0A0CM): CALIBRAZIONE NON ESEGUIBILE. FRED espone le
# serie ICE solo dal 2023-10-10 (licenza ICE, misurato il 10/10/2026): tre anni calmi non
# sono la storia dal 1997 che la calibrazione chiede. Restano le soglie di GIUDIZIO di prima
# (mediana HY dal 1997 ~4,5-5%, IG ~1,3-1,5%, livelli di stress 2011/2016/2022 e acuti
# 2001-02/2008-09/2020), dichiarate tali.
MACRO_HY_SOGLIE = (4.0, 5.5, 7.0)
MACRO_IG_SOGLIE = (1.3, 1.7, 2.5)


def punti_gradino(valore, soglie):
    """0..3: quante soglie (crescenti) il valore raggiunge o supera."""
    v = _num(valore)
    if v is None:
        return None
    return sum(1 for s in soglie if v >= s)


# ===================================================================== CRYPTO FUNDING
# |funding major pesato per OI - tasso base 10,95%| in punti %/anno. CALIBRATE
# (calibra_soglie.py, 10/10/2026). Fonte: API pubblica Hyperliquid fundingHistory di BTC,
# ETH e SOL, ultimi 2 anni orari (2024-10-10 16:00 -> 2026-10-10 15:00 UTC, n = 17520 ore),
# stesse regole dello score (BTC ed ETH obbligatori). Percentili 50 / 80 / 95 / 99 del valore
# assoluto dello scarto: 3,35 / 11,70 / 26,99 / 53,45 (il 24% delle ore sta esattamente al
# tasso base). I percentili 50/80/95 sono i CONFINI DELLE ETICHETTE (NORMALE, SURRISCALDATO,
# EUFORICO): con punti continui e bande comuni 25/50/75 su un massimo di 3 i confini cadono a
# 0,75 / 1,5 / 2,25 punti, quindi le ancore portano i punti 0,75 / 1,5 / 2,25 e il p99 satura
# a 3. Con le ancore «p50 -> 1, p80 -> 2, p95 -> 3» EUFORICO sarebbe scattato gia' verso il p86
# (13% delle ore negli ultimi 2 anni, misurato) invece che al p95. PROXY DICHIARATO: l'API non
# espone l'OI storico; i pesi sono l'OI del giorno della calibrazione. Sensibilita': a pesi
# uguali i percentili sono 4,46 / 13,41 / 29,25 / 56,60 (stesso ordine di grandezza).
CRYPTO_FUNDING_ANCORE = (0.0, 3.35, 11.7, 27.0, 53.45)
CRYPTO_FUNDING_PUNTI = (0.0, 0.75, 1.5, 2.25, 3.0)


# ===================================================================== VALUTAZIONE
# MOS del book da +45% (0 punti) a -75% (3 punti), lineare e continuo: niente scogli. Le
# ancore sono scelte perche' i confini delle BANDE COMUNI (0,75 / 1,5 / 2,25 punti su 3)
# cadano a MOS +15 / -15 / -45: A SCONTO > +15%, EQUA fra -15% e +15%, CARO fra -15% e
# -45%, MOLTO CARO sotto -45%. GIUDIZIO: la zona neutra +-15% e' l'errore tipico di un DCF
# (+-1 punto di WACC o +-0,5 di crescita terminale spostano il fair value del 15-25%): dentro
# la zona il segno del MOS non e' informazione. Decisione 10/10 (delega PM): con le ancore
# +15/-45 «a sconto» partiva gia' da MOS > 0, cioe' dentro l'errore del modello. Col
# troncamento per nome a +-50% il MOS del book non scende sotto -50% (massimo 2,375 punti).
MOS_ZERO_PUNTI = 45.0
MOS_PIENO = -75.0


def punti_mos(mos_pct):
    """0..3 continuo: 3 x (MOS_ZERO_PUNTI - mos) / (MOS_ZERO_PUNTI - MOS_PIENO), saturato.
    None se invalido."""
    m = _num(mos_pct)
    if m is None:
        return None
    p = 3.0 * (MOS_ZERO_PUNTI - m) / (MOS_ZERO_PUNTI - MOS_PIENO)
    # 4 decimali: a 2 decimali MOS +15,1% (0,7475 punti) diventava 0,75 e saltava nella banda EQUA
    return round(min(3.0, max(0.0, p)), 4)


# ===================================================================== RISCHIO BOOK: PAVIMENTI
# Pavimenti «almeno ELEVATO» sulla concentrazione LEGATI AL MANDATO: un nome singolo che
# con uno shock idiosincratico del -60% consuma da solo il budget di stress; un cluster che
# con uno shock settoriale del -50% lo consuma. Shock: GIUDIZIO su ordini di grandezza
# storici (singoli nomi -60/-90% in un evento, settori -50/-80%: tech 2000-02, banche 2008).
# Soglia = budget / shock: con budget 30% -> nome 50%, cluster 60%.
PAVIMENTO_SHOCK_NOME = 0.60
PAVIMENTO_SHOCK_CLUSTER = 0.50
# Senza budget nel mandato: gli stessi valori del budget 30%, DICHIARATI nella riga.
PAVIMENTO_NOME_DEFAULT_PCT = 50.0
PAVIMENTO_CLUSTER_DEFAULT_PCT = 60.0


def pavimenti_concentrazione(stress_budget_pct):
    """(nome_pct, cluster_pct, dal_mandato): soglie dei pavimenti in % del NAV."""
    b = _num(stress_budget_pct)
    if b is None or b <= 0:
        return PAVIMENTO_NOME_DEFAULT_PCT, PAVIMENTO_CLUSTER_DEFAULT_PCT, False
    return round(b / PAVIMENTO_SHOCK_NOME, 1), round(b / PAVIMENTO_SHOCK_CLUSTER, 1), True


# ===================================================================== POSITION DOCTOR
# Netto dei segnali: |netto| < 0,5 HOLD; fra 0,5 e 0,7 «HOLD (al limite)» con la tendenza
# dichiarata (fascia al limite, non un'isteresi: non c'e' memoria dello stato precedente);
# oltre 0,7 la raccomandazione piena. GIUDIZIO: prima un decimo di z-score (2,0 contro 2,1)
# spostava da HOLD a TRIM/HEDGE sul confine 0,6. Le soglie escono nel payload del Doctor
# (`thresholds`): il frontend le legge da li', non ne tiene una copia.
DOCTOR_LIMITE = (0.5, 0.7)
