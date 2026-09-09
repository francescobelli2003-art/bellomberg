# -*- coding: utf-8 -*-
"""VOCE 8, PRIMO PEZZO — il grilletto della trimestrale automatica (03/08, Fable 5).

Legge il REGISTRO (`company_guidance.valid_until`, che per costruzione D4 e' la
data attesa della prossima trimestrale) e dice QUALI ticker hanno la guidance
scaduta o in scadenza: quelli sono i comunicati da andare a leggere. MAI
l'earnings calendar finnhub: verificato inaffidabile il 03/08 (cap 1500 non
gestito che mangia la settimana imminente + copertura 1/27 nomi del book,
misure in changelog (63)).

SOLA LETTURA per costruzione (mode=ro): questo modulo non puo' ne' scrivere ne'
applicare migrazioni — niente MemoryDB (il ⚠️ di §9-sextrigies sui task
schedulati che istanziano MemoryDB qui non puo' accadere).

L'anello pieno della voce 8 (recupero comunicato -> lettura INTEGRALE ->
proposta guidance+addendum-tesi al GATE del PM) resta manuale finche' il PM
non decide il gate (default conservativo: propone, il PM approva). Banco di
prova: emittente farmaceutico con data sintetica.
"""
import os
import sqlite3
from datetime import date

from bellomberg.market_data.fonti_guidance import carica_fonti, classifica_book, radar_fonti

from bellomberg.storage.memory_db import SQLITE_PATH as DB_PATH   # B4 (02/09): percorso unico

FONTE = ("registro company_guidance (valid_until, D4 = data attesa della "
         "prossima trimestrale) — MAI finnhub (inaffidabile, misura 03/08)")


def scadenze_guidance(entro_giorni: int = 0, today=None, db_path: str = None) -> dict:
    """Grilletto: righe ATTIVE con valid_until <= oggi (SCADUTE: la trimestrale
    dovrebbe essere uscita) e, a richiesta, quelle dentro la finestra
    (oggi, oggi+entro_giorni] (IN SCADENZA: trimestrale imminente).
    `today` ISO esplicitabile per collaudi deterministici."""
    oggi = date.fromisoformat(str(today)[:10]) if today else date.today()
    path = db_path or DB_PATH
    # mode=ro: la connessione NON PUO' scrivere (ne' file -wal): garanzia di
    # sola lettura per costruzione, non per promessa.
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        # `unit` fa parte dell'IDENTITA' della riga dal 20/08 (supersede per
        # ticker+metric+period+unit): senza, un ticker con piu' target dello stesso
        # periodo esce come N voci identiche distinte solo da un id opaco — Bayer ne
        # produce 9, misurate. Chi legge il radar deve sapere QUALE grandezza scade.
        righe = con.execute(
            "SELECT id, ticker, metric, period, unit, valid_until, valid_until_source, "
            "source_date, note FROM company_guidance WHERE status='active' "
            "ORDER BY valid_until, ticker").fetchall()
    finally:
        con.close()

    scadute, in_scadenza, fuori = [], [], 0
    for r in righe:
        vu = date.fromisoformat(str(r["valid_until"])[:10])
        ritardo = (oggi - vu).days
        voce = {
            "id": r["id"], "ticker": r["ticker"], "metric": r["metric"],
            "unit": r["unit"],
            "period": r["period"], "valid_until": r["valid_until"],
            "valid_until_source": r["valid_until_source"],
            "source_date": r["source_date"],
            "giorni_di_ritardo": ritardo,
        }
        if ritardo >= 0:
            scadute.append(voce)
        elif -ritardo <= int(entro_giorni):
            voce["fra_giorni"] = -ritardo
            del voce["giorni_di_ritardo"]
            in_scadenza.append(voce)
        else:
            fuori += 1

    return {
        "oggi": oggi.isoformat(),
        "scadute": scadute,
        "in_scadenza": in_scadenza,
        "attive_non_in_finestra": fuori,
        "fonte": FONTE,
        "prossimo_passo": ("per ogni SCADUTA: recuperare il comunicato UFFICIALE, "
                           "leggerlo INTEGRALE, proporre al gate PM guidance "
                           "(supersede) + addendum datato alla tesi"),
    }


def radar_completo(entro_giorni: int = 0, today=None, db_path: str = None,
                   fonti: dict = None) -> dict:
    """VOCE 8, SECONDO PEZZO (ok PM 13/08): il radar su TUTTO l'UNIVERSO.

    L'universo e' `positions` UNIONE `favorite_companies` (Task 6, 04/09):
    il difetto descritto dal PM era «in base ai trade che aggiunge o leva O
    ALLE POSIZIONI TRA I PREFERITI»; i preferiti non li guardava nessuno
    (0 occorrenze in questo modulo e in fonti_guidance prima di oggi).
    Chi mette un titolo fra i preferiti vuole sapere quando riporta tanto
    quanto chi lo ha in portafoglio.

    Il registro resta il grilletto primario (valid_until = data attesa);
    per i ticker dell'universo SENZA righe attive parlano le fonti
    per-ticker (fonti_guidance). Ogni ticker dell'universo finisce in
    ESATTAMENTE una casella di `copertura` — niente buchi zitti (regola PM
    14/07) — e `provenienza` dice per ognuno DA DOVE arriva. Un ticker a
    registro NON viene doppiato dal radar fonti: una scadenza, un grilletto
    solo. Sempre mode=ro.

    `fonti=None` -> snapshot PER CHIAMATA del negozio, non piu' lo snapshot
    dell'import: la coerenza serve DENTRO la chiamata, e legarla all'import
    rendeva invisibile fino al riavvio una modifica a mano di
    `data/fonti_guidance.json` (Task 6, decisione). Si chiama `carica_fonti()`
    e non `fonti_correnti()` perche' serve anche il suo ESITO: `fonti_stato`
    (fix round 1)."""
    path = db_path or DB_PATH
    registro = scadenze_guidance(entro_giorni=entro_giorni, today=today,
                                 db_path=path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        # ASIMMETRIA VOLUTA fra i due filtri sul ticker (rilievo review round 1:
        # era giusta ma non scritta). `positions.ticker` e' NOT NULL nello
        # schema canonico, quindi un NULL li' vorrebbe dire schema rotto e il
        # posto giusto per accorgersene e' un guasto rumoroso, non un filtro che
        # lo nasconde: `sorted()` piu' sotto solleverebbe TypeError. In
        # `favorite_companies` il ticker e' PRIMARY KEY, che in SQLite AMMETTE
        # NULL: li' un vuoto e' raggiungibile dalla CRUD dell'API, quindi si
        # filtra — e si CONTA, perche' una riga scartata in silenzio sarebbe il
        # ripiego zitto della regola PM 14/07.
        book = [r[0] for r in con.execute(
            "SELECT DISTINCT ticker FROM positions WHERE quantita > 0 "
            "ORDER BY ticker")]
        # `favorite_companies` sta nello schema canonico (memory_db) dal
        # riallineamento 23/07, ma un DB piu' vecchio puo' non averla: il buco
        # si DICHIARA in `preferiti_stato`, non si trasforma in uno zero muto
        # (regola PM 14/07). Il radar non muore per una tabella che manca.
        try:
            preferiti = [r[0] for r in con.execute(
                "SELECT DISTINCT ticker FROM favorite_companies "
                "WHERE ticker IS NOT NULL AND ticker != '' ORDER BY ticker")]
            senza_ticker = con.execute(
                "SELECT COUNT(*) FROM favorite_companies "
                "WHERE ticker IS NULL OR ticker = ''").fetchone()[0]
            preferiti_stato = "letti da favorite_companies"
            if senza_ticker:
                preferiti_stato += (" (%d righe SCARTATE perche' senza ticker: "
                                    "non sono radarabili)" % senza_ticker)
        except sqlite3.OperationalError as e:
            preferiti = []
            preferiti_stato = "NON LETTI (%s): i preferiti sono FUORI dal radar" % e
        registro_attivi = {r[0] for r in con.execute(
            "SELECT DISTINCT ticker FROM company_guidance "
            "WHERE status='active'")}
    finally:
        con.close()

    # Lo STATO DEL NEGOZIO viaggia nell'uscita, come `preferiti_stato` (rilievo
    # review round 1). Senza, tre mondi diversi davano una `copertura`
    # IDENTICA: negozio buono senza quella voce, negozio ROTTO e negozio
    # ASSENTE finivano tutti in `scoperti`, e «questo ticker non ha una fonte
    # censita» collassava con «non lo so, il negozio non si legge». Il motivo
    # `carica_fonti()` ce l'ha; era `fonti_correnti()` a buttarlo via.
    if fonti is not None:
        fonti_tutte = fonti
        fonti_stato = ("mappa passata dal chiamante (%d voci): il negozio non "
                       "e' stato letto" % len(fonti))
    else:
        _negozio = carica_fonti()
        fonti_tutte = _negozio["fonti"]
        if _negozio["motivo"] is None:
            fonti_stato = ("negozio letto da %s (%d voci)"
                           % (_negozio["origine"], len(fonti_tutte)))
        else:
            fonti_stato = (
                "NEGOZIO NON LETTO (%s: %s): 'scoperti' qui sotto NON "
                "distingue fra «nessuna fonte censita» e «non lo so»"
                % (_negozio["origine"], _negozio["motivo"]))
    universo = sorted(set(book) | set(preferiti))
    a_registro = [t for t in universo if t in registro_attivi]
    resto = [t for t in universo if t not in registro_attivi]
    cls = classifica_book(resto, fonti=fonti_tutte)
    fuori = radar_fonti(today=today, entro_giorni=entro_giorni,
                        fonti={t: fonti_tutte[t] for t in resto
                               if t in fonti_tutte})
    fuori["scoperti"] = cls["scoperti"]

    in_book, in_pref = set(book), set(preferiti)
    provenienza = {t: ("posizione e preferito" if t in in_book and t in in_pref
                       else "posizione" if t in in_book else "preferito")
                   for t in universo}

    return {
        "oggi": registro["oggi"],
        "registro": registro,
        "fuori_registro": fuori,
        "copertura": {
            "book": len(book),
            "preferiti": len(preferiti),
            "preferiti_stato": preferiti_stato,
            "fonti_stato": fonti_stato,
            "universo": len(universo),
            "provenienza": provenienza,
            "a_registro": a_registro,
            "con_fonte": cls["con_fonte"],
            "con_fonte_senza_data": cls["con_fonte_senza_data"],
            "non_applicabili": cls["non_applicabili"],
            "scoperti": cls["scoperti"],
        },
        "prossimo_passo": registro["prossimo_passo"],
    }


if __name__ == "__main__":
    import json
    import sys
    finestra = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(json.dumps(radar_completo(entro_giorni=finestra),
                     indent=2, ensure_ascii=False))
