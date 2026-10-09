"""
current_facts.py v2 (#199d) - FATTI CORRENTI iniettati nei prompt di TUTTI gli agenti.

AUTOMATICO (non invecchia da solo):
- CALENDARI UFFICIALI 2026 cablati (FOMC/BCE/CPI USA) -> prossimo evento calcolato a runtime,
  con etichette OGGI/DOMANI e allarme FINESTRA EVENTO se 2+ eventi entro 8 giorni.
- NUMERI LIVE da FRED a ogni run (fed funds, depo BCE, CPI yoy, 10Y, disoccupazione),
  cache 1h sull'intero blocco (current_facts_block e' chiamata a ogni iterazione di ogni agente).
- STALENESS GUARD: se la verifica MANUALE (cariche/contesto) e' piu' vecchia di 14 giorni,
  il blocco ordina agli agenti di ri-verificare con tavily e avvisa il PM.
MANUALE (cambia di rado): FACTS_STATIC e CONTEXT_SNAPSHOT. Quando li rivedi aggiorna LAST_VERIFIED.
Fonti calendari: federalreserve.gov, ecb.europa.eu, bls.gov (giugno 2026).
"""
import time
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LAST_VERIFIED = "2026-06-10"
STALE_AFTER_DAYS = 14

FACTS_STATIC = {
    "Presidente Federal Reserve (USA)":
        "Kevin Warsh, in carica dal 22/05/2026 (confermato dal Senato 54-45, succede a Jerome "
        "Powell). Orientamento atteso: dal bias espansivo verso il neutrale. NON citare Powell "
        "come presidente in carica.",
    "Presidente BCE": "Christine Lagarde (mandato fino a ottobre 2027).",
    "Presidente Stati Uniti": "Donald Trump (insediato gennaio 2025).",
    "Presidente del Consiglio (Italia)": "Giorgia Meloni.",
    "Conflitto Iran":
        "E' in corso una guerra in Iran che ha fatto impennare i prezzi dell'energia: e' la "
        "fonte del 'war premium' sull'energia e sui servizi petroliferi (E&P, oil services, "
        "E&C energetico, raffinazione). Lo stato del "
        "conflitto evolve: verificalo con tavily_search a ogni run prima di citarlo.",
}

# Aspettative di mercato fotografate alla data di verifica manuale (INVECCHIANO: sono datate).
CONTEXT_SNAPSHOT = [
    "FOMC 16-17 giugno = PRIMA riunione presieduta da Warsh, con dot plot e conferenza stampa; "
    "al 10/06 il mercato prezzava ~97% di hold (fed funds 3,50-3,75%). Quattro dissensi alla riunione precedente.",
    "BCE 11 giugno: al 10/06 il mercato prezzava un possibile RIALZO di 25pb (attesi due rialzi "
    "nel 2026, contesto euro forte + shock energetico). Lagarde: 'massima incertezza'.",
    "CPI USA di maggio (uscita 10/06 14:30 CET): attese +0,5% m/m, +4,2% a/a, core +2,9% a/a; "
    "l'energia sta accelerando l'inflazione.",
]

NOTES = [
    "La memoria di training non e' una fonte corrente. Questo blocco NON prevale automaticamente "
    "su altre fonti documentate: confronta definizione, periodo e vintage; dichiara il conflitto. "
    "Preferisci la fonte primaria pertinente e piu' recente solo dopo aver verificato la "
    "confrontabilita'; nessuna scelta silenziosa o media fra dati diversi.",
    "Per qualsiasi altro fatto che potrebbe essere cambiato dopo la tua data di addestramento, "
    "verifica con tavily_search prima di affermarlo, e non dare per scontate cariche o nomine.",
]

# ===== CALENDARI UFFICIALI 2026 (cablati una volta, validi tutto l'anno) =====
FOMC_2026 = [(date(2026, 1, 27), date(2026, 1, 28)), (date(2026, 3, 17), date(2026, 3, 18)),
             (date(2026, 4, 28), date(2026, 4, 29)), (date(2026, 6, 16), date(2026, 6, 17)),
             (date(2026, 7, 28), date(2026, 7, 29)), (date(2026, 9, 15), date(2026, 9, 16)),
             (date(2026, 10, 27), date(2026, 10, 28)), (date(2026, 12, 8), date(2026, 12, 9))]
ECB_2026 = [date(2026, 3, 19), date(2026, 4, 30), date(2026, 6, 11), date(2026, 7, 23),
            date(2026, 9, 10), date(2026, 10, 29), date(2026, 12, 17)]
# Verifica 07/10/2026: https://www.bls.gov/schedule/news_release/cpi.htm
# Ore 08:30 Eastern; mesi di riferimento da maggio a novembre (non mese di uscita).
CPI_US_2026 = [date(2026, 6, 10), date(2026, 7, 14), date(2026, 8, 12),
               date(2026, 9, 11), date(2026, 10, 14), date(2026, 11, 10), date(2026, 12, 10)]


def _rome_time(day, hour, minute=0):
    """Orario New York convertito alla data dell'evento, inclusi disallineamenti DST."""
    # Fed: monetary20130313a.htm (14:00 ET / 14:30 ET); BLS: cpi.htm (08:30 ET).
    try:
        event = datetime(day.year, day.month, day.day, hour, minute,
                         tzinfo=ZoneInfo("America/New_York"))
        return event.astimezone(ZoneInfo("Europe/Rome")).strftime("%H:%M %Z")
    except ZoneInfoNotFoundError:
        return "%02d:%02d America/New_York (ora Roma n.d.: dati zoneinfo assenti)" % (hour, minute)


def _cpi_year_pair(observations):
    """CPI mensile: nessuna distanza per posizione, nessun mese duplicato o invalido."""
    from bellomberg.core.macro_observations import annual_comparison
    result = annual_comparison(observations, 'monthly')
    # Compatibilita' rigorosa: questa tupla non puo' trasportare quality al prompt.
    if result['quality']['invalid_observations'] or result['quality']['duplicate_periods']:
        raise ValueError('osservazioni CPI non valide o mesi duplicati: vintage ambiguo')
    if result['yoy_pct'] is None:
        raise ValueError(result['comparison']['reason'])
    return result['yoy_pct'], result['latest_date'], result['year_ago_date']


def _days_label(d, today=None):
    n = (d - (today or date.today())).days
    if n == 0:
        return "OGGI"
    if n == 1:
        return "DOMANI"
    return "tra " + str(n) + " giorni"


def _next_fomc(today=None):
    t = today or date.today()
    for s, e in FOMC_2026:
        if e >= t:
            return s, e
    return None


def _next(dates, today=None):
    t = today or date.today()
    for d in dates:
        if d >= t:
            return d
    return None


def _live_numbers():
    """Numeri LIVE da FRED. Guarded: se FRED/import falliscono, degrada con una riga onesta."""
    lines = []
    try:
        from bellomberg.agents.agent_tools import _fred_fetch_series
    except Exception:
        return ["(numeri live FRED non disponibili: usa get_macro_dashboard per i livelli correnti)",
                "- CPI USA: n.d. (CPIAUCSL; import FRED non disponibile)."]

    def last(series, n=3):
        try:
            obs = (_fred_fetch_series(series, last_n=n) or {}).get("observations") or []
            return obs[-1] if obs else None
        except Exception:
            return None

    up, lo = last("DFEDTARU"), last("DFEDTARL")
    if up and lo:
        lines.append("- Fed funds target: %.2f%%-%.2f%% (al %s) [src: FRED]" % (lo["value"], up["value"], up["date"]))
    depo = last("ECBDFR")
    if depo:
        lines.append("- Tasso deposito BCE: %.2f%% (al %s) [src: FRED]" % (depo["value"], depo["date"]))
    try:
        from bellomberg.agents.agent_tools import _fred_fetch_series as _f
        data = _f("CPIAUCSL", last_n=14) or {}
        obs = data.get("observations") or []
        rejected = data.get("rejected_observations")
        vintage = ("realtime FRED %s / %s (intervallo della risposta, non data di pubblicazione)"
                   % (data["realtime_start"], data["realtime_end"])
                   if data.get("realtime_start") and data.get("realtime_end")
                   else "vintage n.d.: non fornito dal tool")
        if rejected is None:
            lines.append("- CPIAUCSL: metadati scarti n.d.; completezza del payload non verificabile. [src: FRED]")
        elif rejected:
            dates = ", ".join(o.get("date") or "data n.d." for o in rejected)
            latest_available = max((o["date"] for o in obs), default="n.d.")
            lines.append("- CPIAUCSL: osservazioni scartate: %s; ultima osservazione disponibile %s; %s. [src: FRED]"
                         % (dates, latest_available, vintage))
            if any(not o.get("date") or o["date"] >= latest_available for o in rejected) or not obs:
                raise ValueError("latest observation rejected")
        yoy, current_date, base_date = _cpi_year_pair(obs)
        lines.append("- CPI USA: %+.1f%% a/a (CPIAUCSL, CPI-U all items destagionalizzato; "
                     "indice %s / %s; %s, soggetto a revisione). "
                     "Non equiparare automaticamente al CPI non destagionalizzato del comunicato. "
                     "[src: FRED]" % (yoy, current_date, base_date, vintage))
    except ValueError as exc:
        # Solo diagnostica locale: gli errori provider possono contenere URL/credenziali.
        lines.append("- CPI USA: n.d. (CPIAUCSL; dati non validi o stesso mese di confronto "
                     "non disponibile; %s). [src: FRED]" % type(exc).__name__)
    except Exception as exc:
        lines.append("- CPI USA: n.d. (CPIAUCSL; acquisizione/calcolo KO: %s). [src: FRED]"
                     % type(exc).__name__)
    t10 = last("DGS10")
    if t10:
        lines.append("- Treasury 10Y: %.2f%% (al %s) [src: FRED]" % (t10["value"], t10["date"]))
    unr = last("UNRATE")
    if unr:
        lines.append("- Disoccupazione USA: %.1f%% (dato %s) [src: FRED]" % (unr["value"], unr["date"]))
    if not lines:
        lines.append("(FRED non ha risposto in questo run: usa get_macro_dashboard per i livelli correnti)")
    return lines


_BLOCK_CACHE = {"text": None, "ts": 0.0}
_BLOCK_TTL_S = 3600.0  # il blocco viene richiesto a OGNI iterazione di OGNI agente: 1 fetch FRED/ora


def current_facts_block() -> str:
    """Blocco di testo da concatenare ai system prompt / context degli agenti."""
    if _BLOCK_CACHE["text"] and (time.time() - _BLOCK_CACHE["ts"]) < _BLOCK_TTL_S:
        return _BLOCK_CACHE["text"]
    today = date.today()
    lines = ["=== FONTI E CONTESTO CORRENTE (" + datetime.now().strftime("%d/%m/%Y") + ") ===",
             "Distingui dati osservati, calendario e contesto manuale; applica le regole sulle fonti in fondo.",
             "CARICHE E CONTESTO MANUALE (verifica: " + LAST_VERIFIED + "):"]
    for k, v in FACTS_STATIC.items():
        lines.append("- " + k + ": " + v)

    lines.append("")
    lines.append("CALENDARIO EVENTI (da calendari ufficiali, calcolato a runtime):")
    near = []
    nf = _next_fomc(today)
    if nf:
        s, e = nf
        # Voce 13 §9-quattuortrigies: la riunione dura due giorni e la DECISIONE
        # esce alla FINE — etichetta e near-flag vanno su `e`, non su `s` (il
        # 28/07 la riga diceva "OGGI... statement ore 20:00" con statement il 29).
        lines.append("- Prossima riunione FOMC: " + s.strftime("%d/%m") + "-" + e.strftime("%d/%m/%Y")
                     + "; la decisione esce a fine riunione: statement " + _days_label(e, today)
                     + " (" + e.strftime("%d/%m") + ") ore " + _rome_time(e, 14)
                     + ", conferenza " + _rome_time(e, 14, 30) + ".")
        if 0 <= (e - today).days <= 8:
            near.append("FOMC " + e.strftime("%d/%m"))
    ne = _next(ECB_2026, today)
    if ne:
        lines.append("- Prossima decisione BCE: " + ne.strftime("%d/%m/%Y") + " (" + _days_label(ne, today)
                     + "); decisione 14:15 CET, conferenza 14:45.")
        if 0 <= (ne - today).days <= 8:
            near.append("BCE " + ne.strftime("%d/%m"))
    nc = _next(CPI_US_2026, today)
    if nc:
        lines.append("- Prossimo CPI USA: " + nc.strftime("%d/%m/%Y") + " (" + _days_label(nc, today)
                     + "), ore " + _rome_time(nc, 8, 30) + ". [src: BLS calendario, verifica 07/10/2026]")
        if 0 <= (nc - today).days <= 8:
            near.append("CPI USA " + nc.strftime("%d/%m"))
    elif CPI_US_2026 and max(CPI_US_2026) < today:
        lines.append("- CPI USA: calendario cablato esaurito, verifica bls.gov/schedule (di norma seconda settimana del mese). PM: estendi CPI_US_2026.")
    if len(near) >= 2:
        lines.append("! FINESTRA EVENTO: " + " + ".join(near) + " ravvicinati. Ogni proposta con timing/expiry DEVE tenerne conto.")

    lines.append("")
    lines.append("DATI OSSERVATI (FRED; cache blocco fino a 1h, data osservazione distinta dal vintage):")
    lines.extend(_live_numbers())

    lines.append("")
    lines.append("CONTESTO ALLA VERIFICA MANUALE DEL " + LAST_VERIFIED + " (aspettative di mercato: possono essere invecchiate):")
    for c in CONTEXT_SNAPSHOT:
        lines.append("- " + c)

    try:
        age = (today - datetime.strptime(LAST_VERIFIED, "%Y-%m-%d").date()).days
        if age > STALE_AFTER_DAYS:
            lines.append("")
            lines.append("! ATTENZIONE: la verifica manuale di cariche/contesto risale a " + str(age)
                         + " giorni fa. Prima di affermare cariche o stati di conflitto RIVERIFICA con "
                         "tavily_search. (PM: aggiorna FACTS_STATIC/CONTEXT_SNAPSHOT e LAST_VERIFIED in current_facts.py)")
    except Exception:
        pass

    lines.append("")
    for n in NOTES:
        lines.append("! " + n)
    text = "\n".join(lines)
    _BLOCK_CACHE["text"] = text
    _BLOCK_CACHE["ts"] = time.time()
    return text


_FAV_CACHE = {"text": None, "ts": 0.0}


def favorites_block() -> str:
    """T4-4: interessi del PM (favorite companies dal terminale) iniettati negli agenti.
    Elenco vuoto: "". Lettura fallita: causa dichiarata, senza cache del guasto."""
    import time as _t
    if _FAV_CACHE["text"] is not None and (_t.time() - _FAV_CACHE["ts"]) < 600:
        return _FAV_CACHE["text"]
    text = ""
    try:
        import os
        from bellomberg.storage.memory_db import connect_sqlite
        from bellomberg.storage.memory_db import SQLITE_PATH as path   # B4 (02/09): percorso unico
        cx = connect_sqlite(path)  # hardening #32: WAL + busy_timeout
        try:
            try:
                rows = cx.execute("SELECT ticker, name, sector, industry, note FROM favorite_companies "
                                  "ORDER BY added_at DESC LIMIT 15").fetchall()
            except Exception as e:
                if "no such column: note" not in str(e):
                    raise
                rows = [(r[0], r[1], r[2], r[3], None) for r in cx.execute(
                    "SELECT ticker, name, sector, industry FROM favorite_companies "
                    "ORDER BY added_at DESC LIMIT 15").fetchall()]
        finally:
            cx.close()
        if rows:
            nomi = ", ".join("%s (%s, %s/%s)" % (t, n or t, s or "?", i or "?") for t, n, s, i, _nt in rows)
            sett = {}
            for _, _, s2, i2, _nt in rows:
                k = (s2 or i2 or "").strip()
                if k:
                    sett[k] = sett.get(k, 0) + 1
            dedotti = ", ".join("%s (%d)" % (k, v) for k, v in sorted(sett.items(), key=lambda x: -x[1]))
            note_lines = []
            for t, _n, _s, _i, nt in rows:
                if nt and str(nt).strip():
                    # 21/08 (review avversariale, audit/25): qui c'era
                    # `'- %s: "%s"' % (t, str(nt).strip()[:500])` — taglio a un
                    # letterale con la virgoletta di CHIUSURA rimessa dopo, cioe'
                    # lo STESSO travestimento curato in memory_db, sullo stesso
                    # autore e nello stesso prompt degli specialisti. In piu' il
                    # canale di scrittura ne accetta 1.000 (bellomberg_api.py:870
                    # e :884): 500 caratteri scritti dal PM non arrivavano a
                    # nessuno. Ora passa dalla policy, che dichiara il taglio.
                    from bellomberg.storage.memory_db import pm_verbatim
                    note_lines.append('- %s: %s' % (
                        t, pm_verbatim(nt, ident=t, virgolette=True,
                                       fonte="favorite_companies.note")))
            text = ("\n\n=== INTERESSI DEL PM (favorite companies dal terminale) ===\n"
                    "Il PM segue attivamente: " + nomi + ".\n"
                    + ("Settori d'interesse dedotti: " + dedotti + ".\n" if dedotti else "")
                    + (("PENSIERO DEL PM sui preferiti (la SUA motivazione - leggila e TIENINE CONTO; "
                        "dove c'e' una nota, RISPONDI al suo pensiero confermando o smentendo con dati):\n"
                        + "\n".join(note_lines) + "\n") if note_lines else "")
                    + "ISTRUZIONI: nel tuo giro considera anche questi nomi (catalisti datati, valutazione, "
                    "rischi, correlazioni col book); se uno merita azione o attenzione, dillo esplicitamente "
                    "con numeri [src:]. NON sono posizioni: niente P&L inventato su di essi, il portafoglio "
                    "reale resta SOLO quello dal DB.")
    except Exception as e:
        return "\n\n[PREFERITI n.d.] Lettura fallita: " + type(e).__name__ + ": " + str(e)
    _FAV_CACHE["text"] = text
    _FAV_CACHE["ts"] = _t.time()
    return text


# Quanto di ogni tesi arriva agli agenti. POLICY, non un numero sepolto.
# 20/08: era 260 cablato, e MISURATO sul DB vero dopo gli addendum delle
# trimestrali tagliava il 37% di quello che il PM aveva scritto — l'addendum di
# una tesi spariva del tutto oltre il limite, quella della prima posizione arrivava per
# 26 caratteri su 635. Cioe' il canale nato il 16/07 per NON troncare le tesi le
# troncava, e proprio nella coda, dove sta la parte NUOVA.
# 2000 copriva con margine la piu' lunga di allora (894), e il blocco viaggia in
# un prefisso CACHATO (misura 20/08: 92,4% degli input serviti dalla cache).
# 06/10 (PM): 4000. Le tesi ora si scrivono anche dal Diario, dove una nota con
# ipotesi, prove, rischi e condizioni di smentita supera facilmente i 2000. Misura
# per un book da 20 titoli: 20 x (tesi + commento) pieni ~ 160 mila caratteri, uso
# normale molto meno; nessun tetto sul blocco intero (scelta PM). Il taglio resta
# dichiarato coi numeri. Vale anche per l'ultimo commento sul trade.
MAX_CHAR_TESI = 4000


def _tesi_tagliata(s: str) -> str:
    """Testo della tesi entro MAX_CHAR_TESI. Se taglia lo DICHIARA coi numeri:
    tre puntini non dicono quanto manca, e un agente che legge "..." non sa se
    dietro c'e' una virgola o l'intera lettura dell'ultima trimestrale."""
    s = (s or "").strip()
    if len(s) <= MAX_CHAR_TESI:
        return s
    return (s[:MAX_CHAR_TESI]
            + " […TRONCATA: mostrati %d dei %d caratteri scritti dal PM. La parte "
              "mancante e' in coda, dove stanno gli aggiornamenti piu' RECENTI "
              "(addendum datati): se ti serve, chiedila invece di assumere che non "
              "esista.]" % (MAX_CHAR_TESI, len(s)))


def _con_profilo(text: str) -> str:
    """05/09 (criterio 5, audit F02): «Il PM investe LONG-TERM e accetta concentrazione» era una
    frase cablata che attribuiva a chiunque il profilo del creatore. Ora le regole 2 e 3 del
    blocco vengono dal MANDATO (mandato_pm), letto dal disco a OGNI chiamata: il testo delle
    tesi porta il segnaposto, il profilo si compone qui.
    Mandato assente = dichiarato nel prompt stesso, mai il profilo di ieri (regola 14/07)."""
    if not text or "{MANDATO:profilo_tesi}" not in text:
        return text
    from bellomberg.core import mandato_pm
    try:
        riga = mandato_pm.sezioni(mandato_pm.carica())["profilo_tesi"]
    except mandato_pm.MandatoMancante:
        riga = "2. " + mandato_pm.riga_senza_mandato() + "\n"
    except Exception as e:
        # review 05/09: un guasto qui risaliva nel try/except di specialists/base.py e faceva sparire
        # fatti + preferiti + tesi dal round; meglio una riga dichiarata e le tesi intatte
        riga = "2. PROFILO DEL PM n.d. (%s: %s): nessuna preferenza va assunta.\n" % (type(e).__name__, str(e)[:80])
    return text.replace("{MANDATO:profilo_tesi}", riga)


def pm_theses_block() -> str:
    """Tesi del PM per posizione (regola PM 16/07) — canale DEDICATO e non troncato.
    Prima viveva solo dentro il dump JSON del portafoglio, che fino al 20/08 il Capo
    riceveva tagliato a [:4000] (~9 posizioni su 28) e il red team a [:2500]: la tesi
    sulla prima posizione sopravviveva solo perche' era in testa, le altre sparivano in silenzio.
    (Quei due tagli non esistono piu' — changelog (74), vista compatta 28/28 — ma il
    canale dedicato resta la ragione per cui le tesi non dipendono da quel dump.)

    Tre stati, MAI confondibili (review 16/07 — il ramo errore NON torna piu' ""):
      - tesi presenti  -> blocco con le tesi + elenco dichiarato dei nomi SENZA tesi;
      - DB leggibile ma zero tesi -> "" (vuoto LEGITTIMO: i chiamanti possono skippare);
      - errore di lettura -> blocco che DICHIARA il buco nel prompt stesso (n.d.),
        al giro dopo si riprova. "" da errore era un proxy zitto:
        il Capo avrebbe stampato 'nessuna tesi nel DB' su un DB lockato.
    Niente cache (06/10, PM): era di 600 s e una tesi salvata dall'app o dal Diario
    poteva non arrivare alla run lanciata subito dopo. Il DB si rilegge a ogni chiamata
    (una SELECT in sola lettura); dentro il round il blocco resta congelato nel primo
    messaggio user, quindi il prompt non cambia a meta' conversazione."""
    try:
        import os
        import sqlite3
        from bellomberg.storage.memory_db import SQLITE_PATH as path   # B4 (02/09): percorso unico
        # mode=ro: un lettore puro non deve MAI poter creare un DB vuoto se il path
        # e' sbagliato (lezione CLAUDE.md "0 posizioni = path sbagliato").
        cx = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
        try:
            attive = [r[0] for r in cx.execute(
                "SELECT ticker FROM positions WHERE is_active=1 "
                "ORDER BY quantita*prezzo_medio DESC").fetchall()]
            tesi_db = {r[0]: r[1] for r in cx.execute(
                "SELECT ticker, tesi FROM positions WHERE is_active=1 AND tesi IS NOT NULL "
                "AND TRIM(tesi) <> ''").fetchall()}
            # PM 16/07: "le tesi iniziali erano hardcodate; ora scrivo due righe nella
            # sezione trade quando compro o vendo" -> il pensiero VIVO del PM sta in
            # trade_history.pm_rationale: per ogni ticker si prende il commento del
            # trade piu' recente che ne ha uno (tie-break su id per determinismo).
            rationali = {}
            for tk, dt, act, rat in cx.execute(
                    "SELECT t.ticker, t.data, t.action, t.pm_rationale FROM trade_history t "
                    "JOIN (SELECT ticker, MAX(data) AS d FROM trade_history "
                    "      WHERE pm_rationale IS NOT NULL AND TRIM(pm_rationale) <> '' "
                    "      GROUP BY ticker) m ON t.ticker = m.ticker AND t.data = m.d "
                    "WHERE t.pm_rationale IS NOT NULL AND TRIM(t.pm_rationale) <> '' "
                    "ORDER BY t.id").fetchall():
                rationali[tk] = (str(dt)[:10], act, str(rat).strip())
        finally:
            cx.close()
        rows = [(tk, tesi_db.get(tk), rationali.get(tk)) for tk in attive
                if tk in tesi_db or tk in rationali]
        senza = [tk for tk in attive if tk not in tesi_db and tk not in rationali]
    except Exception as e:
        # BUCO DICHIARATO NEL PROMPT (non solo su stdout): chi legge deve sapere che
        # le tesi POSSONO esistere ma non sono arrivate. Al giro dopo si riprova.
        return ("\n\n=== TESI DEL PM PER POSIZIONE ===\n"
                "TESI PM: n.d. — errore di lettura dal DB (" + type(e).__name__ + ": "
                + str(e)[:80] + "). NON dedurre che il PM non abbia tesi: il dato "
                "non e' disponibile in questo giro.\n")
    text = ""
    if rows:
        righe = []
        for t, ts, rat in rows:
            pezzi = []
            if ts:
                s = str(ts).strip()
                pezzi.append('tesi registrata: "%s"' % _tesi_tagliata(s))
            if rat:
                dt, act, s = rat
                pezzi.append('ultimo commento del PM (%s %s): "%s"'
                             % (act, dt, _tesi_tagliata(str(s))))
            righe.append("- %s: %s" % (t, " | ".join(pezzi)))
        text = ("\n\n=== TESI DEL PM PER POSIZIONE (dal DB — leggile e INGAGGIALE) ===\n"
                "View del PM per posizione, da due fonti: la TESI REGISTRATA (puo' risalire "
                "all'apertura, alcune sono state scritte a inizio progetto) e l'ULTIMO "
                "COMMENTO scritto dal PM sul trade (il suo pensiero piu' RECENTE). Se "
                "divergono, pesa il commento recente e dichiara la divergenza. Regole (PM 16/07):\n"
                "1. Ogni proposta di TRIM/SELL su un nome con view deve INGAGGIARLA: o la "
                "sostieni coi numeri o la sfidi coi numeri [src:], MAI ignorarla.\n"
                # 05/09 (criterio 5, audit F02): le regole 2 e 3 (LONG-TERM e concentrazione,
                # «vuole essere sfidato») erano cablate: ora vengono dal MANDATO, e il segnaposto
                # resta nel testo — il profilo si compone in _con_profilo a ogni chiamata.
                "{MANDATO:profilo_tesi}"
                + "\n".join(righe)
                + (("\nLe altre %d posizioni attive NON hanno view dichiarata (%s): "
                    "su queste non c'e' una view del PM da ingaggiare."
                    % (len(senza), ", ".join(senza))) if senza else ""))
    return _con_profilo(text)


_RESEARCH_CACHE = {"text": None, "ts": 0.0}
_TICKER_OK = None  # regex compilata lazy


RESEARCH_NOTES_POLICY = "weekly-research-notes/1"
RESEARCH_NOTES_STAGE = "research_notes_context_v1"
RESEARCH_NOTES_BUDGET_CHARS = 8000  # Fixed note payload allowance; never grows the LLM budget.


def research_notes_enabled(contract):
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    if "research_notes_policy" not in contract:
        return False
    if contract["research_notes_policy"] != RESEARCH_NOTES_POLICY:
        raise WeeklyRunBlocked("Research notes policy non compatibile")
    return True


def _note_time(value):
    # SQLite decision/notes defaults are local wall time; accepted run is UTC.
    from datetime import datetime, timezone
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _research_note_line(row, text):
    import json
    return ("[RESEARCH_NOTE note_id=%d decision_id=%d ticker=%s author=%s decision_status=%s timestamp=%s role=%s archive_override=%s]\n%s"
            % (row['note_id'], row['decision_id'], row['ticker'], row['author'],
               row['decision_status'], row['timestamp'], row['context_role'], row['archive_override'],
               json.dumps(text, ensure_ascii=False)))


def capture_research_notes(db, as_of, *, budget_chars=RESEARCH_NOTES_BUDGET_CHARS):
    """One read transaction: current cards and the complete original note history.

    Raw text is independent of eligibility. Late/inactive notes and budget overflow
    remain individually accounted for. A newer AI reply cannot evict a PM question.
    """
    from hashlib import sha256
    import re
    cutoff = _note_time(as_of)
    if type(budget_chars) is not int or budget_chars < 0:
        raise ValueError("Research notes budget must be a nonnegative integer")
    result = {'policy': RESEARCH_NOTES_POLICY, 'as_of': as_of, 'cards': [], 'notes': [],
              'texts': {}, 'budget_chars': budget_chars, 'included_chars': 0,
              'destinations': ['fundamentals:1', 'fundamentals:2', 'capo'],
              'status': 'COMPLETO', 'error': None}
    try:
        with db._conn() as conn:
            conn.execute('BEGIN')
            decisions = [dict(row) for row in conn.execute(
                "SELECT id,ticker,timestamp,status,veto,veto_revoked_at,memo_id,timing,rationale,archive_override FROM decisions "
                "WHERE upper(action)='RESEARCH' ORDER BY timestamp DESC,id DESC")]
            notes = [dict(row) for row in conn.execute(
                "SELECT n.id,n.decision_id,n.autore,n.testo,n.timestamp FROM decision_notes n "
                "JOIN decisions d ON d.id=n.decision_id WHERE upper(d.action)='RESEARCH' "
                "ORDER BY n.timestamp DESC,n.id DESC")]
    except Exception as exc:
        result.update(status='INCOMPLETO', error='DB_READ_ERROR:' + type(exc).__name__)
        return result
    by_id = {row['id']: row for row in decisions}
    current = {}
    for row in sorted(decisions, key=lambda r: (_note_time(r['timestamp']), r['id']), reverse=True):
        ticker = str(row['ticker'] or '').strip().upper()
        if (_note_time(row['timestamp']) <= cutoff and ticker not in current
                and str(row['status']).upper() == 'PENDING' and not row['veto']
                and not row['veto_revoked_at'] and row['archive_override'] != 1):
            current[ticker] = row
    for ticker, row in sorted(current.items()):
        if re.fullmatch(r'[A-Z0-9][A-Z0-9.\-^]{0,11}', ticker):
            result['cards'].append({'decision_id': row['id'], 'ticker': ticker,
                                    'timestamp': row['timestamp'], 'decision_status': 'PENDING',
                                    'memo_id': row['memo_id'], 'timing': row['timing'],
                                    'rationale': row['rationale']})
    active = {row['ticker'] for row in result['cards']}
    # Timestamp ties have a stable event identity; identical texts are never deduplicated.
    def archived(row):
        return str(row['status']).upper() in ('ARCHIVED', 'EXPIRED') or row['archive_override'] == 1
    def priority(note):
        decision = by_id[note['decision_id']]
        rank = 0 if archived(decision) else 2 if note['autore'] == 'PM' else 1
        return rank, _note_time(note['timestamp']), note['id']
    notes.sort(key=priority, reverse=True)
    for row in notes:
        decision = by_id[row['decision_id']]
        text = row['testo']
        ticker = str(decision['ticker'] or '').strip().upper()
        status = str(decision['status']).upper()
        reason = ('after_cutoff' if _note_time(row['timestamp']) > cutoff else
                  'decision_after_cutoff' if _note_time(decision['timestamp']) > cutoff else
                  'veto' if decision['veto'] else
                  'veto_revoked' if decision['veto_revoked_at'] else
                  'decision_status:' + status if status != 'PENDING' and not (archived(decision) and ticker in active) else
                  'no_active_card' if ticker not in active else None)
        item = {'note_id': row['id'], 'decision_id': row['decision_id'], 'ticker': ticker,
                'author': row['autore'], 'timestamp': row['timestamp'],
                'text_sha256': sha256(text.encode('utf-8')).hexdigest(), 'decision_status': status,
                'archive_override': decision['archive_override'],
                'context_role': ('CONTESTO_STORICO' if archived(decision) else
                                 'RICHIESTA_PM' if row['autore'] == 'PM' else 'RISPOSTA_AI'),
                'status': 'excluded' if reason else 'included', 'reason': reason or ('historical_context' if archived(decision) else 'active_research'),
                'destinations': [] if reason else list(result['destinations'])}
        size = len(_research_note_line(item, text)) + 1
        if not reason and result['included_chars'] + size > budget_chars:
            item.update(status='pending', reason='budget_chars', destinations=[])
        elif not reason:
            result['included_chars'] += size
        result['notes'].append(item)
        result['texts'][str(row['id'])] = text
    result['notes'].sort(key=lambda row: row['note_id'])
    if any(row['status'] == 'pending' for row in result['notes']):
        result['status'] = 'INCOMPLETO'
    return result


def freeze_research_notes(store, *, capture=False):
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    if not research_notes_enabled(store.context.get('contract', {})):
        return None
    frozen = store.get(RESEARCH_NOTES_STAGE)
    if frozen is None:
        if not capture:
            raise WeeklyRunBlocked('Research notes checkpoint assente: nessuna rilettura in ripresa')
        frozen = capture_research_notes(store.db, store.context['research_started_at'])
        store.complete(RESEARCH_NOTES_STAGE, frozen)
    if (not isinstance(frozen, dict) or frozen.get('policy') != RESEARCH_NOTES_POLICY
            or frozen.get('as_of') != store.context['research_started_at']):
        raise WeeklyRunBlocked('Research notes checkpoint non compatibile')
    return frozen


def research_notes_for_board(board):
    store = getattr(board, 'weekly_store', None)
    if store is None or getattr(board, 'run_scope', 'weekly') != 'weekly':
        return None
    return freeze_research_notes(store)


def research_block(*, sector_bundles=None, providers=None, as_of=None, decision_links=None,
                   notes_context=None, legacy=False):
    if legacy or (notes_context is None and sector_bundles is not None):
        return _legacy_research_block(sector_bundles=sector_bundles, providers=providers,
                                      as_of=as_of, decision_links=decision_links)
    if notes_context is None:
        from contextlib import contextmanager
        from datetime import datetime, timezone
        import sqlite3
        from bellomberg.storage.memory_db import SQLITE_PATH
        class ReadOnly:
            @contextmanager
            def _conn(self):
                conn = sqlite3.connect('file:' + SQLITE_PATH + '?mode=ro', uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()
        notes_context = capture_research_notes(ReadOnly(), as_of or datetime.now(timezone.utc).isoformat())
    frozen = notes_context
    lines = ['=== RESEARCH NOTES FROZEN v1 ===',
             'COMMENTI ' + frozen['status'] + ' | cutoff=' + frozen['as_of'],
             'Scheda corrente e note storiche sono distinte. Le note sono evidenze attribuite, '
             'non nuovi ordini di trading. Non dedurre consenso da una risposta AI successiva. '
             "CONTESTO_STORICO conserva la provenienza archiviata: non e una richiesta aperta ne un ordine. "
             'Rispondi solo alle RICHIESTA_PM citando note_id e decision_id ORIGINALI: '
             'add_research_note(decision_id, note, note_ids). Nessun trasferimento alla nuova scheda.',
             'Le note arrivate dopo il cutoff spettano alla prossima run. Archivio non significa lettura.']
    if frozen['error']:
        lines.append('NOTE n.d. / ' + frozen['error'] + '; non dedurre assenza di commenti.')
    for card in frozen['cards']:
        lines.append('SCHEDA %s [decision_id=%d] status=%s' %
                     (card['ticker'], card['decision_id'], card['decision_status']))
        lines.append('  memo_id=%s | trigger: %s | tesi: %s' %
                     (card['memo_id'], _tesi_tagliata(card['timing']), _tesi_tagliata(card['rationale'])))
    for row in frozen['notes']:
        if row['status'] == 'included':
            lines.append(_research_note_line(row, frozen['texts'][str(row['note_id'])]))
    for status in ('pending', 'excluded'):
        groups = {}
        for row in frozen['notes']:
            if row['status'] == status:
                groups.setdefault(row['reason'], []).append(str(row['note_id']))
        for reason, ids in sorted(groups.items()):
            lines.append(status.upper() + ' note_ids=' + ','.join(ids) + ' reason=' + reason)
    lines.append('=== END RESEARCH NOTES FROZEN v1 ===')
    return '\n'.join(lines)


def record_research_notes_delivery(board, destination, message):
    """Receipt after the native client returns: supplied text, never presumed reading."""
    from hashlib import sha256
    import re
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    frozen = research_notes_for_board(board)
    if frozen is None:
        return None
    block = research_block(notes_context=frozen)
    expected = sorted(row['note_id'] for row in frozen['notes'] if row['status'] == 'included')
    actual = sorted(set(int(i) for i in re.findall(r'^\[RESEARCH_NOTE note_id=(\d+) ', message, re.M)))
    known = {row['note_id'] for row in frozen['notes']}
    unknown = sorted(set(actual) - known)
    if destination not in frozen['destinations'] or block not in message or actual != expected or unknown:
        raise WeeklyRunBlocked('Research notes delivery differs from frozen context: ' + destination)
    receipt = {'destination': destination, 'as_of': frozen['as_of'],
               'status': frozen['status'], 'delivered_note_ids': actual,
               'excluded_note_ids': sorted(row['note_id'] for row in frozen['notes'] if row['status'] == 'excluded'),
               'pending_note_ids': sorted(row['note_id'] for row in frozen['notes'] if row['status'] == 'pending'),
               'unknown_note_ids': unknown, 'note_block_sha256': sha256(block.encode('utf-8')).hexdigest(),
               'exclusions': [{key: row[key] for key in ('note_id','status','reason')}
                              for row in frozen['notes'] if row['status'] != 'included'],
               'attestation': 'native_client_returned; supplied context, comprehension not asserted'}
    board.weekly_store.complete('research_notes_delivery_v1:' + destination, receipt)
    return receipt


def recover_frozen_research_reply(store, checkpoint_key, tool_key, input_):
    """Read a proved committed result; never authorize redispatch of an uncertain write."""
    from hashlib import sha256
    from bellomberg.storage.weekly_run_store import digest
    import re
    if store is None or not research_notes_enabled(store.context['contract']):
        return None
    if (checkpoint_key not in ('fundamentals:R1', 'fundamentals:R2')
            or not isinstance(tool_key, str) or re.fullmatch('[0-9a-f]{64}', tool_key) is None
            or not isinstance(input_, dict)):
        return None
    decision_id, text, note_ids = (input_.get('decision_id'), input_.get('note'), input_.get('note_ids'))
    if (type(decision_id) is not int or not isinstance(text, str) or not text.strip()
            or not isinstance(note_ids, list) or not note_ids
            or any(type(i) is not int for i in note_ids) or len(note_ids) != len(set(note_ids))):
        return None
    frozen = freeze_research_notes(store)
    included = {row['note_id']: row for row in frozen['notes']
                if row['status'] == 'included' and row['author'] == 'PM' and row['context_role'] == 'RICHIESTA_PM'}
    if any(i not in included or included[i]['decision_id'] != decision_id for i in note_ids):
        return None
    destination = 'fundamentals:' + checkpoint_key[-1]
    delivery = store.get('research_notes_delivery_v1:' + destination)
    if delivery is None or not set(note_ids).issubset(delivery['delivered_note_ids']):
        return None
    event_id = checkpoint_key + ':' + tool_key
    request = {'event_id': event_id, 'destination': destination, 'decision_id': decision_id,
               'reply_to_note_ids': sorted(note_ids), 'text': text}
    saved = store.get('research_notes_reply_v1:' + digest(request))
    if not isinstance(saved, dict) or type(saved.get('note_id')) is not int:
        return None
    expected = {'ok': True, 'note_id': saved['note_id'], 'decision_id': decision_id,
                'reply_to_note_ids': sorted(note_ids), 'event_id': event_id, 'destination': destination,
                'text_sha256': sha256(text.encode('utf-8')).hexdigest()}
    if saved != expected:
        return None
    with store.db._conn() as conn:
        note = conn.execute('SELECT decision_id,autore,testo FROM decision_notes WHERE id=?',
                            (saved['note_id'],)).fetchone()
    return saved if note is not None and tuple(note) == (decision_id, 'AI', text) else None


def add_frozen_research_reply(board, decision_id, text, note_ids, *, event_id=None):
    """Write answer and original-question linkage atomically in the existing store."""
    from bellomberg.storage.weekly_run_store import _json, _now, digest
    from hashlib import sha256
    frozen = research_notes_for_board(board)
    if (frozen is None or type(decision_id) is not int or not isinstance(text, str) or not text.strip()
            or not isinstance(note_ids, list) or not note_ids
            or any(type(i) is not int for i in note_ids) or len(note_ids) != len(set(note_ids))
            or not isinstance(event_id, str) or not event_id):
        return {'ok': False, 'error': 'Original PM note_ids, decision_id and tool event identity are required'}
    included = {row['note_id']: row for row in frozen['notes']
                if row['status'] == 'included' and row['author'] == 'PM' and row['context_role'] == 'RICHIESTA_PM'}
    if any(i not in included or included[i]['decision_id'] != decision_id for i in note_ids):
        return {'ok': False, 'error': 'Reply must reference delivered PM notes on their original decision_id'}
    store = board.weekly_store
    destination = 'fundamentals:' + str(getattr(board, 'current_round', None))
    delivery = store.get('research_notes_delivery_v1:' + destination)
    if delivery is None or not set(note_ids).issubset(delivery['delivered_note_ids']):
        return {'ok': False, 'error': 'No delivery receipt for these PM questions in this round'}
    request = {'event_id': event_id, 'destination': destination, 'decision_id': decision_id,
               'reply_to_note_ids': sorted(note_ids), 'text': text}
    stage = 'research_notes_reply_v1:' + digest(request)
    with store._mutex, store.db._conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        saved = conn.execute('SELECT payload_json,payload_sha256 FROM weekly_checkpoints '
                             'WHERE memo_id=? AND stage=?', (store.memo_id,stage)).fetchone()
        if saved is not None:
            import json
            if sha256(saved[0].encode('utf-8')).hexdigest() != saved[1]:
                raise ValueError('Research notes reply receipt modified')
            return json.loads(saved[0])
        original = conn.execute('SELECT upper(action) FROM decisions WHERE id=?', (decision_id,)).fetchone()
        if original is None or original[0] != 'RESEARCH':
            return {'ok': False, 'error': 'Original RESEARCH decision unavailable'}
        cursor = conn.execute('INSERT INTO decision_notes(decision_id,autore,testo) VALUES(?,?,?)',
                              (decision_id,'AI',text))
        result = {'ok': True, 'note_id': cursor.lastrowid, 'decision_id': decision_id,
                  'reply_to_note_ids': sorted(note_ids), 'event_id': event_id, 'destination': destination,
                  'text_sha256': sha256(text.encode('utf-8')).hexdigest()}
        encoded = _json(result)
        conn.execute('INSERT INTO weekly_checkpoints VALUES(?,?,?,?,?)',
                     (store.memo_id,stage,encoded,sha256(encoded.encode('utf-8')).hexdigest(),_now()))
    return result


def _legacy_research_block(*, sector_bundles=None, providers=None, as_of=None, decision_links=None) -> str:
    """Titoli in pipeline RESEARCH (richiesta PM 16/07): le righe RESEARCH del
    Decisions tracker diventano un blocco per Fundamentals (R1/R2), cosi' la
    ricerca AVANZA tra le run invece di ripartire da zero. Dedup per ticker
    (riga piu' recente), filtro qualita' ticker (in decisions esistono righe
    sporche tipo 'Europe Defense basket': dichiarate, non iniettate).
    Stati come pm_theses_block: errore -> blocco n.d. dichiarato (non cachato);
    zero righe -> "" (vuoto legittimo). Cache 600s."""
    import time as _t
    global _TICKER_OK
    if sector_bundles is None and _RESEARCH_CACHE["text"] is not None and (_t.time() - _RESEARCH_CACHE["ts"]) < 600:
        return _RESEARCH_CACHE["text"]
    try:
        import os
        import re as _re
        import sqlite3
        if _TICKER_OK is None:
            _TICKER_OK = _re.compile(r"^[A-Z0-9][A-Z0-9.\-^]{0,11}$")
        from bellomberg.storage.memory_db import SQLITE_PATH as path   # B4 (02/09): percorso unico
        cx = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
        rows = cx.execute(
            "SELECT d.id, d.ticker, d.timestamp, d.memo_id, d.timing, d.rationale FROM decisions d "
            "JOIN (SELECT ticker, MAX(timestamp) AS ts FROM decisions "
            "      WHERE upper(action)='RESEARCH' AND upper(status)='PENDING' "
            "      AND timestamp >= date('now','-60 days') GROUP BY ticker) m "
            "ON d.ticker = m.ticker AND d.timestamp = m.ts "
            "WHERE upper(d.action)='RESEARCH' ORDER BY d.timestamp DESC").fetchall()
        # F10 v3: note del PM sul filo (le ultime 3 per decisione) — la run le legge
        # e risponde con add_research_note. Tabella assente (DB non migrato) = {}.
        note_map = {}
        try:
            for did, autore, testo, ts in cx.execute(
                    "SELECT decision_id, autore, testo, timestamp FROM decision_notes "
                    "ORDER BY timestamp DESC LIMIT 60"):
                note_map.setdefault(did, [])
                if len(note_map[did]) < 3:
                    note_map[did].append((autore, _tesi_tagliata(str(testo)), str(ts)[:10]))
        except Exception:
            note_map = {}
        cx.close()
    except Exception as e:
        return ("\n\n=== TITOLI IN RICERCA (pipeline RESEARCH) ===\n"
                "PIPELINE: n.d. — errore di lettura dal DB (" + type(e).__name__ + ": "
                + str(e)[:80] + "). NON dedurre che la pipeline sia vuota.\n")
    text = ""
    validi, scartati = [], []
    for did, tk, ts, memo_id, timing, rat in rows:
        tk_s = str(tk or "").strip().upper()
        if _TICKER_OK.match(tk_s):
            validi.append((did, tk_s, str(ts)[:10], memo_id, str(timing or "").strip()[:120],
                           _tesi_tagliata(str(rat or ""))))
        else:
            scartati.append(str(tk or "?")[:40])
    if validi:
        righe = []
        for did, tk_s, dt, memo_id, timing, rat in validi[:8]:
            righe.append("- %s [decision_id=%s] (RESEARCH dal memo #%s, %s)%s%s"
                         % (tk_s, did, memo_id or "?", dt,
                            (" | trigger: " + timing) if timing else "",
                            (' | tesi: "' + rat + '"') if rat else ""))
            if sector_bundles is not None:
                from datetime import date
                from bellomberg.valuation.sector_analysis import (prepare_sector_analysis,
                    default_sector_providers, sector_analysis_summary)
                cutoff = as_of or date.today().isoformat()
                try:
                    bundle = sector_bundles.get(tk_s)
                    if bundle is None or bundle["case"]["as_of"] != cutoff:
                        bundle = prepare_sector_analysis(tk_s, as_of=cutoff,
                            providers=providers if providers is not None else default_sector_providers())
                        sector_bundles[tk_s] = bundle
                    if decision_links is not None:
                        decision_links[tk_s] = did
                    righe.append("    " + sector_analysis_summary(bundle).replace("\n", "\n    "))
                except Exception as exc:
                    righe.append("    Acquisizione settoriale KO: " + type(exc).__name__ + ": " + str(exc))
            # F10 v3: il filo note PM<->AI viaggia dentro il blocco — le note del PM
            # sono DOMANDE DIRETTE a te: rispondere e' obbligatorio
            for autore, testo, nts in reversed(note_map.get(did, [])):
                righe.append('    NOTA %s (%s): "%s"' % (autore, nts, testo))
        text = ("\n\n=== TITOLI IN RICERCA (pipeline RESEARCH dal Decisions tracker) ===\n"
                "Il PM vuole che su questi nomi la ricerca AVANZI a ogni run, non che riparta "
                "da zero. Per ciascuno: aggiorna la tesi coi numeri [src:], controlla il "
                "trigger dichiarato, e concludi con un verdetto esplicito — PROMUOVI a "
                "BUY/ADD (con catalyst datato), RESTA in ricerca (dicendo cosa manca), o "
                "ARCHIVIA (con motivo). Un nome in ricerca senza progresso dichiarato e' "
                "un errore di processo.\n"
                "NOTE PM (F10 v3): se una riga ha 'NOTA PM' senza una tua risposta successiva, "
                "DEVI rispondere con add_research_note(decision_id, note) — breve, coi numeri "
                "[src:]. E' il canale di dialogo del PM sulla ricerca.\n" + "\n".join(righe)
                + (("\nRighe RESEARCH con ticker NON quotabile, escluse e da bonificare "
                    "nel tracker: " + ", ".join(scartati)) if scartati else ""))
    if sector_bundles is None:
        _RESEARCH_CACHE["text"] = text
        _RESEARCH_CACHE["ts"] = _t.time()
    return text


if __name__ == "__main__":
    print(current_facts_block())
