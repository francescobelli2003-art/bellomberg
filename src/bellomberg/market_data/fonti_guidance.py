# -*- coding: utf-8 -*-
"""VOCE 8, SECONDO PEZZO — le FONTI per-ticker della trimestrale automatica
(17/08, Fable 5, ok PM 13/08 "ok procedi tu").

Il grilletto del registro (guidance_watch) copre solo i nomi con righe attive
in `company_guidance`; il resto del book era fuori radar (§9-octotrigies).
Questo modulo e' la mappa CURATA delle fonti UFFICIALI per-ticker: pagina IR,
calendario finanziario, prossima data di report attesa. Estende il radar a
tutto il book. MAI finnhub (verificato inaffidabile il 03/08, changelog (63));
MAI una tabella nuova (migrazione = lettera A/B del PM, ancora aperta).

DAL 04/09/2026 (Task 3) IL REGISTRO NON E' PIU' IL DICT COMMITTATO: le voci
vivono nel negozio privato `data/fonti_guidance.json`, che questo sorgente non
contiene e non deve contenere. La revisione non passa piu' da git — il negozio
non e' tracciato — e il facsimile pubblico, con simboli inventati, e'
`fonti_guidance.example.json`.

DISCIPLINA DEI DATI (regola PM 14/07 + "una data nuda e' un segnaposto"):
- `next_report_date` senza `next_report_source` + `verificato_il` e' VIETATA:
  il test d'igiene in tests/test_fonti_guidance.py la boccia sull'esempio
  tracciato (che c'e' in ogni clone, negozio o no) e IN PIU' sul negozio dove
  esiste, e cade se non ha nessuna data da controllare — mai un verde a vuoto.
- ticker ignoto -> stato 'assente' dichiarato, mai None silenzioso.
- ETF/ETC/ETN/cripto: niente trimestrali -> 'non applicabile' DICHIARATO,
  mai semplicemente omesso.
- I nomi degli strumenti vengono da `positions.nome` del DB dove esiste
  [src: sqlite ro 17/08]; dove il DB e' NULL il nome e' dedotto dal ticker e
  la nota lo dice.

Tipi: equity | adr | fondo_chiuso (applicabili: pubblicano risultati) ·
etf | etc | etn | cripto (non applicabili).
"""
import json
import os
from datetime import date

from bellomberg.core.paths import DATA_DIR

TIPI_APPLICABILI = ("equity", "adr", "fondo_chiuso")
TIPI_NON_APPLICABILI = ("etf", "etc", "etn", "cripto")

FONTE = ("mappa fonti per-ticker curata (pagine IR ufficiali + calendari "
         "finanziari, verificate a mano con data di verifica) — MAI finnhub "
         "(inaffidabile, misura 03/08)")

# Le date si aggiornano SOLO con verifica dal vivo (calendario IR ufficiale):
# next_report_date + next_report_source + verificato_il viaggiano insieme.
# La regola governa ora le voci del NEGOZIO (sotto), non piu' un letterale.


PERCORSO_FONTI = str(DATA_DIR / "fonti_guidance.json")


def carica_fonti(path: str = None) -> dict:
    """Legge il negozio privato. MAI un ripiego muto (regola PM 14/07): se il file
    manca o non si legge, torna mappa VUOTA con `origine` e `motivo` scritti.
    Le chiavi che iniziano con '_' sono commenti del file, non voci."""
    p = path or PERCORSO_FONTI
    if not os.path.exists(p):
        return {"fonti": {}, "origine": "assente",
                "motivo": "negozio non trovato: %s (copia fonti_guidance.example.json)" % p}
    try:
        with open(p, encoding="utf-8") as fh:
            grezzo = json.load(fh)
    except Exception as e:
        return {"fonti": {}, "origine": "illeggibile",
                "motivo": "%s: %s" % (type(e).__name__, e)}
    if not isinstance(grezzo, dict):
        return {"fonti": {}, "origine": "illeggibile",
                "motivo": "il negozio non e' un oggetto JSON ma %s"
                          % type(grezzo).__name__}
    return {"fonti": {k: v for k, v in grezzo.items() if not k.startswith("_")},
            "origine": p, "motivo": None}


def fonti_correnti() -> dict:
    """Le voci del negozio, rilette a ogni chiamata.

    DAL TASK 6 (04/09) E' L'UNICA SORGENTE DI VERITA': `fonte_per` e
    `classifica_book` la chiamano quando il chiamante non passa `fonti`, e
    ne prendono UNO snapshot per chiamata. Lo snapshot dell'import (`FONTI`)
    non c'e' piu': dava la stessa coerenza dentro la chiamata, ma rendeva
    invisibile fino al riavvio del processo una modifica a mano del negozio —
    e un valore fermo che si presenta come «le fonti» e' la forma silenziosa
    del dato vecchio che la regola PM 14/07 vieta."""
    return carica_fonti()["fonti"]


# Caricato QUI, all'import: `ORIGINE_FONTI` deve dire lo stato VERO del negozio.
# Un "non ancora caricato" che nessuno aggiorna sarebbe una frase falsa in un
# valore che si presenta come l'esito dell'ultimo caricamento (regola PM 14/07
# + "la frase in pagina e' un'affermazione"): dove il negozio manca dice
# 'assente' col suo motivo, col percorso in cui ha cercato.
#
# E' l'esito dell'ULTIMO CARICAMENTO ALL'IMPORT, e solo quello: le voci non
# vivono qui. Chi vuole le fonti chiama `fonti_correnti()`.
_CARICATO = carica_fonti()
ORIGINE_FONTI = {"origine": _CARICATO["origine"], "motivo": _CARICATO["motivo"]}


def fonte_per(ticker: str, fonti: dict = None) -> dict:
    """Fonte di un ticker. MAI None silenzioso: ignoto -> stato 'assente'.

    Senza `fonti`, snapshot PER CHIAMATA del negozio: UNA lettura del file per
    ogni chiamata. Irrilevante col chiamante di oggi (`lettore_trimestrali`,
    un ticker a invocazione); un ciclo per-ticker ne farebbe N, e in quel caso
    si carica una volta e si passa `fonti=` (rilievo review round 1)."""
    f = (fonti if fonti is not None else fonti_correnti()).get(ticker)
    if f is None:
        return {"stato": "assente", "ticker": ticker,
                "motivo": f"nessuna fonte censita per {ticker}: da costruire"}
    return {"stato": "ok", "ticker": ticker, **f}


def classifica_book(tickers, fonti: dict = None) -> dict:
    """Ogni ticker del book in ESATTAMENTE una lista: con_fonte /
    non_applicabili / scoperti. Il buco 'fonte senza data verificata' e'
    dichiarato a parte in con_fonte_senza_data. L'uscita e' lunga quanto
    `tickers`, non quanto il negozio: senza `fonti` puo' quindi prendersi
    lo snapshot per chiamata senza esporre nulla che il chiamante non
    abbia gia' nominato."""
    fonti = fonti if fonti is not None else fonti_correnti()
    r = {"con_fonte": [], "con_fonte_senza_data": [],
         "non_applicabili": [], "scoperti": []}
    for tk in tickers:
        f = fonti.get(tk)
        if f is None:
            r["scoperti"].append(tk)
        elif f["tipo"] in TIPI_NON_APPLICABILI:
            r["non_applicabili"].append(tk)
        else:
            r["con_fonte"].append(tk)
            if not f.get("next_report_date"):
                r["con_fonte_senza_data"].append(tk)
    return r


def radar_fonti(today=None, entro_giorni: int = 0, *, fonti: dict) -> dict:
    """Radar sulle fonti per-ticker: date attese passate (usciti), imminenti
    nella finestra, e — dichiarate, mai sparite — le fonti senza data
    verificata. `today` ISO esplicitabile per collaudi deterministici.

    `fonti` E' OBBLIGATORIO, e solo per parola chiave (Task 6, 04/09). Fra le
    tre funzioni che accettano `fonti`, e' l'unica la cui uscita cresce con la
    MAPPA invece che con cio' che il chiamante ha nominato (`fonte_per`
    risponde su un ticker, `classifica_book` sulla lista che riceve): col
    default un chiamante che si dimenticava `fonti=` ciclava il NEGOZIO
    INTERO — cioe' la fuga che il Task 5 ha tolto dal `__main__`, riaperta da
    un'omissione. Un parametro senza default non si dimentica: si sbaglia
    rumorosamente (TypeError). Chi vuole davvero tutto il negozio lo dichiara
    scrivendo `fonti=fonti_correnti()`. (`carica_fonti`/`fonti_correnti` il
    negozio intero lo tornano per mestiere, ma chi le chiama l'ha chiesto:
    rilievo review round 1 su una frase che diceva «unica del modulo».)"""
    oggi = date.fromisoformat(str(today)[:10]) if today else date.today()
    usciti, imminenti, non_verificati, non_applicabili = [], [], [], 0
    for tk in sorted(fonti):
        f = fonti[tk]
        if f["tipo"] in TIPI_NON_APPLICABILI:
            non_applicabili += 1
            continue
        nrd = f.get("next_report_date")
        if not nrd:
            non_verificati.append({
                "ticker": tk, "nome": f["nome"], "ir_url": f.get("ir_url"),
                "motivo": "data del prossimo report mai verificata sul calendario IR",
            })
            continue
        attesa = date.fromisoformat(nrd)
        voce = {"ticker": tk, "nome": f["nome"],
                "next_report_date": nrd,
                "next_report_source": f.get("next_report_source"),
                "verificato_il": f.get("verificato_il"),
                "ir_url": f.get("ir_url")}
        ritardo = (oggi - attesa).days
        if ritardo >= 0:
            voce["giorni_dalla_data_attesa"] = ritardo
            usciti.append(voce)
        elif -ritardo <= int(entro_giorni):
            voce["fra_giorni"] = -ritardo
            imminenti.append(voce)
    return {
        "oggi": oggi.isoformat(),
        "usciti": usciti,
        "imminenti": imminenti,
        "non_verificati": non_verificati,
        "non_applicabili": non_applicabili,
        "fonte": FONTE,
    }


if __name__ == "__main__":
    # NON stampa piu' il radar. `radar_fonti()` senza argomenti ciclava il
    # NEGOZIO: lanciato cosi', il modulo mostrava le posizioni di chi il
    # negozio ce l'ha a chiunque desse il comando. Il radar sull'universo di
    # chi esegue e' `guidance_watch`, che posizioni e preferiti li legge dal
    # DB. (Dal Task 6 quella forma non e' nemmeno piu' scrivibile per
    # distrazione: `radar_fonti` vuole `fonti=` e senza solleva TypeError.)
    # Qui resta lo STATO del negozio, dichiarato e mai muto (regola PM 14/07):
    # da dove ha letto, perche' no, quante voci — e dov'e' il radar vero.
    _c = carica_fonti()
    print(json.dumps({
        "origine": _c["origine"],
        "motivo": _c["motivo"],
        "voci_nel_negozio": len(_c["fonti"]),
        "come_si_usa": ("il radar sul TUO universo e' "
                       "`python -m bellomberg.market_data.guidance_watch [giorni]`: "
                       "cicla le posizioni e i preferiti del DB, non le voci di "
                       "questo negozio"),
    }, indent=2, ensure_ascii=False))
