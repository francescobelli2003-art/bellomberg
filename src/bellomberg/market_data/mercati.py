# -*- coding: utf-8 -*-
"""Orari di borsa per i ticker del book (20/08/2026, Opus 5, ok PM).

PERCHE' ESISTE. Dal 19/08 `price_stale` guarda l'ETA' del prezzo e non piu' la sua
sola esistenza (changelog (73)). Ma una soglia a tempo fisso non distingue
"mercato chiuso" da "updater morto": il lunedi' mattina, prima del primo giro,
TUTTO il book risulta vecchio — e un allarme sempre acceso e' peggio di nessun
allarme (e' la lezione che quel fix si era gia' portato dietro una volta).

Qui si risponde a una domanda sola: **il mercato di questo ticker e' aperto
adesso?** Se e' chiuso, un prezzo fermo e' il prezzo GIUSTO e non va segnalato.

LIMITI DICHIARATI (regola 14/07 — meglio scritti qui che scoperti in produzione):
  · I FESTIVI NON SONO GESTITI. Il 25 dicembre questo modulo dice "aperto" e un
    prezzo fermo verrebbe marcato vecchio. E' un falso allarme raro e visibile,
    scelto contro l'alternativa peggiore (una tabella di festivi che invecchia in
    silenzio e fa dichiarare CHIUSO un mercato aperto, nascondendo un guasto vero).
    `stato()` lo dichiara nel campo `limiti`.
  · Nessuna asta di apertura/chiusura, nessun after-hours: si usa la seduta
    continua. Un prezzo delle 17:35 su Milano risulta "a mercato chiuso".
  · La pausa pranzo di Tokyo non e' modellata (seduta unica 09:00-15:00).
  · Il fuso viene da `zoneinfo` (dati IANA del sistema): l'ora legale e' corretta
    automaticamente, ma se il database dei fusi manca la funzione lo DICHIARA
    invece di indovinare.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# suffisso ticker -> (nome mercato, fuso IANA, apertura, chiusura)
# Gli orari sono quelli della SEDUTA CONTINUA, ora LOCALE del mercato.
MERCATI = {
    ".MI":  ("Borsa Italiana",   "Europe/Rome",      time(9, 0),  time(17, 30)),
    ".DE":  ("Xetra",            "Europe/Berlin",    time(9, 0),  time(17, 30)),
    ".F":   ("Francoforte",      "Europe/Berlin",    time(9, 0),  time(17, 30)),
    ".FRA": ("Francoforte",      "Europe/Berlin",    time(9, 0),  time(17, 30)),
    ".VI":  ("Vienna",           "Europe/Vienna",    time(9, 0),  time(17, 30)),
    ".L":   ("London SE",        "Europe/London",    time(8, 0),  time(16, 30)),
    ".AS":  ("Euronext Amsterdam", "Europe/Amsterdam", time(9, 0), time(17, 30)),
    ".PA":  ("Euronext Paris",   "Europe/Paris",     time(9, 0),  time(17, 30)),
    ".BR":  ("Euronext Bruxelles", "Europe/Brussels", time(9, 0), time(17, 30)),
    ".HE":  ("Nasdaq Helsinki",  "Europe/Helsinki",  time(10, 0), time(18, 30)),
    ".AT":  ("Atene",            "Europe/Athens",    time(10, 15), time(17, 20)),
    ".SW":  ("SIX Swiss",        "Europe/Zurich",    time(9, 0),  time(17, 30)),
    ".HK":  ("HKEX",             "Asia/Hong_Kong",   time(9, 30), time(16, 0)),
    ".T":   ("Tokyo SE",         "Asia/Tokyo",       time(9, 0),  time(15, 0)),
    ".TO":  ("Toronto SE",       "America/Toronto",  time(9, 30), time(16, 0)),
}

# senza suffisso = listino USA (stessa inferenza di memory_db._infer_currency)
USA = ("NYSE/Nasdaq", "America/New_York", time(9, 30), time(16, 0))

# 24/7: nessuna chiusura, nemmeno il weekend. PURR NON e' qui — e' Hyperliquid
# Strategies Inc, DAT equity USA (conferma PM 22/07, v. price_updater:168).
CRYPTO_24_7 = {"BTC", "ETH", "SOL", "HYPE", "BTC-USD", "ETH-USD", "SOL-USD"}


def mercato_di(ticker):
    """(nome, fuso, apertura, chiusura) per il ticker, o None se e' 24/7."""
    t = str(ticker or "").upper().strip()
    if t in CRYPTO_24_7:
        return None
    for suff, dati in MERCATI.items():
        if t.endswith(suff):
            return dati
    return USA


def stato(ticker, adesso=None):
    """Stato del mercato di `ticker`. Ritorna un dict, mai un'eccezione.

    `aperto` e' None quando NON si puo' sapere (fuso non risolvibile): il
    chiamante deve trattarlo come "non lo so", non come "chiuso" — dichiarare
    aperto un mercato chiuso produce falsi allarmi, ma dichiarare chiuso un
    mercato aperto NASCONDE un updater morto, che e' il guasto vero.
    """
    t = str(ticker or "").upper().strip()
    m = mercato_di(t)
    limiti = "festivi non gestiti; nessun after-hours/asta"
    if m is None:
        return {"ticker": t, "mercato": "crypto 24/7", "aperto": True,
                "motivo": "mercato sempre aperto", "limiti": "nessuno"}
    nome, fuso, apre, chiude = m
    try:
        tz = ZoneInfo(fuso)
    except (ZoneInfoNotFoundError, Exception) as e:      # noqa: B014 - vogliamo TUTTO
        return {"ticker": t, "mercato": nome, "aperto": None,
                "motivo": "fuso %s non risolvibile (%s): stato NON determinabile"
                          % (fuso, type(e).__name__), "limiti": limiti}
    ora = (adesso or datetime.now(tz))
    if ora.tzinfo is None:
        ora = ora.replace(tzinfo=tz)
    locale = ora.astimezone(tz)
    if locale.weekday() >= 5:
        return {"ticker": t, "mercato": nome, "aperto": False,
                "motivo": "weekend (%s locale)" % locale.strftime("%a %H:%M"),
                "limiti": limiti}
    dentro = apre <= locale.time() <= chiude
    return {"ticker": t, "mercato": nome, "aperto": dentro,
            "motivo": "%s locale, seduta %s-%s" % (locale.strftime("%a %H:%M"),
                                                   apre.strftime("%H:%M"),
                                                   chiude.strftime("%H:%M")),
            "limiti": limiti}


def aperto(ticker, adesso=None):
    """Scorciatoia: True/False/None (None = non determinabile, v. stato())."""
    return stato(ticker, adesso)["aperto"]
