"""guardia_prezzi.py — plausibilita' dei prezzi sul percorso della cassa.

Proposta 27/07, specifica decisa dal PM il 01/08 (§9-quattuortrigies, coda §3).
Funzione PURA: il chiamante (POST /trade in bellomberg_api) le passa i fatti
— valuta della posizione, ultimo prezzo noto — e decide col verdetto.

Le tre classi di errore GIA' VISTE che questa guardia chiude in avanti:
  - virgola mangiata dall'input number (158,50 -> 15850, x100) — LEZIONE in CLAUDE.md;
  - GBX/GBP adiacenti nella tendina di F7 (42 GBP invece di 4.200 GBX, x100);
  - valute mischiate nel prezzo medio (trade #46/#47 MSTR in EUR su book USD,
    misurato dal ponte F22: gia' successo in produzione).

DIVIDEND e' ESENTE dai controlli valuta/scala (dichiarato): il suo "prezzo" e'
il dividendo per azione (un ratio col prezzo di mercato non ha senso) e una quota in GBX
paga dividendi USD su quotazione GBX. Resta la positivita'.
"""

import math

# Soglie DECISE dal PM (01/08): cambiarle e' una decisione, non un refactor.
# x3 perche' gli errori misurati sono x100 (virgola, GBX/GBP): lontanissimi
# dalla soglia, mentre un crollo o un rally veri restano sotto.
RIFIUTO_RATIO = 3.0
AVVISO_PCT = 0.30


def _rifiuto(motivo):
    return {"esito": "rifiuto", "motivo": motivo}


def controlla_trade(prezzo, quantita, valuta_trade,
                    valuta_posizione=None, ultimo_prezzo=None, azione="",
                    ultimo_prezzo_data=None):
    """Verdetto sulla plausibilita' di un trade PRIMA della scrittura su DB.

    Ritorna {"esito": "ok"|"avviso"|"rifiuto", "motivo": None|str}.
    "avviso" NON blocca: il chiamante scrive e riporta il motivo in risposta.
    Il chiamante passa `ultimo_prezzo` SOLO se il riferimento e' valido (nel
    backend: posizione ATTIVA — su una riapertura lo snapshot e' orfano e
    vecchio per costruzione: confrontarlo sarebbe un rifiuto senza rimedio).
    """
    azione = (azione or "").upper()

    # (3) positivita' server-side: finora l'unico guardiano era la UI.
    # isfinite: NaN supera sia `<= 0` sia i confronti di ratio (review 01/08) —
    # da F7 non arriva (leggiNumero lo ferma) ma da curl col token si'.
    try:
        _p, _q = float(prezzo), float(quantita)
    except (TypeError, ValueError):
        return _rifiuto(f"prezzo/quantita' non numerici: p={prezzo!r}, q={quantita!r}")
    if not math.isfinite(_p) or not math.isfinite(_q):
        return _rifiuto(f"prezzo/quantita' non finiti: p={_p!r}, q={_q!r}")
    if _p <= 0 or _q <= 0:
        return _rifiuto(f"quantita' e prezzo devono essere positivi: "
                        f"ricevuti q={_q:g}, p={_p:g}")

    # DIVIDEND: esente da valuta e scala (v. docstring), positivita' gia' fatta
    if azione == "DIVIDEND":
        return {"esito": "ok", "motivo": None}

    # (1) valuta discorde dalla posizione: nessuna conversione zitta
    if valuta_posizione and valuta_trade and \
            str(valuta_trade).strip().upper() != str(valuta_posizione).strip().upper():
        return _rifiuto(
            f"valuta del trade ({str(valuta_trade).upper()}) diversa da quella "
            f"della posizione ({str(valuta_posizione).upper()}): rifiutato senza "
            f"conversione automatica — e' la classe del carico MSTR contaminato "
            f"(#46/#47) e del GBX/GBP x100 su una quota londinese. Reinserisci nella valuta "
            f"di quotazione della posizione.")

    # (2) scarto dall'ultimo prezzo noto (stessa valuta di quotazione)
    if ultimo_prezzo:
        try:
            _u = float(ultimo_prezzo)
        except (TypeError, ValueError):
            _u = 0.0
        if _u > 0:
            _rif = (f" (riferimento del {ultimo_prezzo_data})"
                    if ultimo_prezzo_data else "")
            ratio = _p / _u
            if ratio > RIFIUTO_RATIO or ratio < 1.0 / RIFIUTO_RATIO:
                return _rifiuto(
                    f"prezzo {_p:g} fuori scala rispetto all'ultimo noto {_u:g}{_rif} "
                    f"(x{ratio:.2f}, soglia x{RIFIUTO_RATIO:g}): classi note "
                    f"virgola-mangiata e GBX/GBP. Se il prezzo e' VERO, aggiorna "
                    f"prima lo snapshot prezzi (l'updater gira ~15min sulle "
                    f"posizioni attive) e reinserisci.")
            if abs(ratio - 1.0) > AVVISO_PCT:
                return {"esito": "avviso",
                        "motivo": (f"prezzo {_p:g} scostato del {(ratio - 1) * 100:+.1f}% "
                                   f"dall'ultimo noto {_u:g}{_rif}: scrittura eseguita, "
                                   f"verifica che sia voluto")}

    return {"esito": "ok", "motivo": None}
