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
from datetime import date, datetime

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
    "Se un fatto qui sopra confligge con la tua memoria di training, PREVALE questo elenco.",
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
CPI_US_2026 = [date(2026, 6, 10), date(2026, 7, 14), date(2026, 8, 12)]  # confermate BLS; estendere quando pubblicano le successive


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
        return ["(numeri live FRED non disponibili: usa get_macro_dashboard per i livelli correnti)"]

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
        obs = (_f("CPIAUCSL", last_n=14) or {}).get("observations") or []
        if len(obs) >= 13:
            yoy = (obs[-1]["value"] / obs[-13]["value"] - 1.0) * 100.0
            lines.append("- CPI USA: %+.1f%% a/a (indice al %s) [src: FRED]" % (yoy, obs[-1]["date"]))
    except Exception:
        pass
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
    lines = ["=== FATTI CORRENTI VERIFICATI (ground truth, " + datetime.now().strftime("%d/%m/%Y") + ") ===",
             "Usa SEMPRE questi fatti; ignora la tua memoria di training dove confligge.", ""]
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
                     + " (" + e.strftime("%d/%m") + ") ore 20:00 CET, conferenza 20:30.")
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
        lines.append("- Prossimo CPI USA: " + nc.strftime("%d/%m/%Y") + " (" + _days_label(nc, today) + "), ore 14:30 CET.")
        if 0 <= (nc - today).days <= 8:
            near.append("CPI USA " + nc.strftime("%d/%m"))
    elif CPI_US_2026 and max(CPI_US_2026) < today:
        lines.append("- CPI USA: calendario cablato esaurito, verifica bls.gov/schedule (di norma seconda settimana del mese). PM: estendi CPI_US_2026.")
    if len(near) >= 2:
        lines.append("! FINESTRA EVENTO: " + " + ".join(near) + " ravvicinati. Ogni proposta con timing/expiry DEVE tenerne conto.")

    lines.append("")
    lines.append("NUMERI LIVE (FRED, al momento della run):")
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


_TESI_CACHE = {"text": None, "ts": 0.0}

# Quanto di ogni tesi arriva agli agenti. POLICY, non un numero sepolto.
# 20/08: era 260 cablato, e MISURATO sul DB vero dopo gli addendum delle
# trimestrali tagliava il 37% di quello che il PM aveva scritto — l'addendum di
# una tesi spariva del tutto oltre il limite, quella della prima posizione arrivava per
# 26 caratteri su 635. Cioe' il canale nato il 16/07 per NON troncare le tesi le
# troncava, e proprio nella coda, dove sta la parte NUOVA.
# 2000 copre con margine la piu' lunga di oggi (894). Il costo e' irrisorio: il
# blocco intero passa da ~8.200 a ~11.000 caratteri e viaggia in un prefisso
# CACHATO (misura 20/08: 92,4% degli input serviti dalla cache, +50.000 caratteri
# su 11 agenti = 0,13 EUR su una run da 11,23 EUR).
MAX_CHAR_TESI = 2000


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
    blocco vengono dal MANDATO (mandato_pm), letto dal disco a OGNI chiamata e FUORI dalla
    cache delle tesi (600 s): il testo cachato porta il segnaposto, il profilo si compone qui.
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
        NON cachato (al giro dopo si riprova). "" da errore era un proxy zitto:
        il Capo avrebbe stampato 'nessuna tesi nel DB' su un DB lockato.
    Cache 600s (solo esiti sani): dentro il round il blocco e' comunque congelato
    nel primo messaggio user; tra round successivi un edit del PM viene raccolto."""
    import time as _t
    if _TESI_CACHE["text"] is not None and (_t.time() - _TESI_CACHE["ts"]) < 600:
        return _con_profilo(_TESI_CACHE["text"])
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
        # le tesi POSSONO esistere ma non sono arrivate. Non cachato: si riprova.
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
                # resta nel testo CACHATO — il profilo si compone in _con_profilo a ogni chiamata.
                "{MANDATO:profilo_tesi}"
                + "\n".join(righe)
                + (("\nLe altre %d posizioni attive NON hanno view dichiarata (%s): "
                    "su queste non c'e' una view del PM da ingaggiare."
                    % (len(senza), ", ".join(senza))) if senza else ""))
    _TESI_CACHE["text"] = text
    _TESI_CACHE["ts"] = _t.time()
    return _con_profilo(text)


_RESEARCH_CACHE = {"text": None, "ts": 0.0}
_TICKER_OK = None  # regex compilata lazy


def research_block() -> str:
    """Titoli in pipeline RESEARCH (richiesta PM 16/07): le righe RESEARCH del
    Decisions tracker diventano un blocco per Fundamentals (R1/R2), cosi' la
    ricerca AVANZA tra le run invece di ripartire da zero. Dedup per ticker
    (riga piu' recente), filtro qualita' ticker (in decisions esistono righe
    sporche tipo 'Europe Defense basket': dichiarate, non iniettate).
    Stati come pm_theses_block: errore -> blocco n.d. dichiarato (non cachato);
    zero righe -> "" (vuoto legittimo). Cache 600s."""
    import time as _t
    global _TICKER_OK
    if _RESEARCH_CACHE["text"] is not None and (_t.time() - _RESEARCH_CACHE["ts"]) < 600:
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
    _RESEARCH_CACHE["text"] = text
    _RESEARCH_CACHE["ts"] = _t.time()
    return text


if __name__ == "__main__":
    print(current_facts_block())
