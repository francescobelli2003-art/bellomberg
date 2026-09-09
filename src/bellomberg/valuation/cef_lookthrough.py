# -*- coding: utf-8 -*-
"""cef_lookthrough.py — LOOK-THROUGH DEI FONDI CHIUSI (16/07, finding #1a run #45).

Il sistema trattava il fondo chiuso in book come un "blob di beta USA": nessuno vedeva COSA contiene.
Questo modulo unisce, per i CEF configurati:
  1. NAV e sconto/premio correnti (cef_nav, fonte ufficiale, staleness dichiarata);
  2. le holdings dal 13F SEC del GESTORE (sec_edgar.get_13f_holdings) con pesi
     normalizzati sul totale del filing.

IL 13F E' UN PROXY E VIENE ETICHETTATO COME TALE (regola no-fallback PM 14/07):
- e' il portafoglio del GESTORE (tutti i suoi fondi), NON i pesi del singolo CEF;
- copre SOLO posizioni long US >200M$: cio' che resta fuori per ogni fondo e' scritto nel
  negozio (campo not_in_13f) e dichiarato nel payload;
- e' trimestrale con ~45 giorni di lag (la filing date e' dichiarata nel payload).
Cache in-memory 6h (una run non deve martellare la SEC). Errori dichiarati, mai [].
"""
import time

_TITOLO = "=== LOOK-THROUGH CEF (cosa contengono i fondi chiusi del book) ==="
_CACHE = {}
_TTL_S = 6 * 3600

# ticker CEF in book -> gestore (slug 13F di sec_edgar + nome + cosa non e' nel 13F): nel negozio
# privato delle istituzioni (negozi_privati.carica_istituzioni, sezione cef), riletto a ogni chiamata.
# La NATURA del simbolo (che sia un fondo chiuso) vive nel negozio dei veicoli, non qui.
def _gestori() -> dict:
    from bellomberg.storage.negozi_privati import carica_istituzioni
    return carica_istituzioni()


def get_lookthrough(ticker: str, max_holdings: int = 12) -> dict:   # 04/09 (Opus 5): il default era un titolo del book
    """NAV+sconto e holdings 13F (proxy dichiarato) per un CEF configurato."""
    tkr = (ticker or "").upper().strip()
    negozio = _gestori()
    if negozio["origine"] in ("assente", "illeggibile"):
        return {"error": (f"{tkr}: negozio delle istituzioni {negozio['origine']} "
                          f"({negozio['motivo']}): nessun gestore 13F configurato")}
    gestori = negozio["istituzioni"]["cef"]
    cfg = gestori.get(tkr)
    if not cfg:
        # il messaggio CONTA, non elenca (lotto 2b, 05/09): questo errore esce sia nel
        # payload del tool sia nel prompt del Capo, e l'elenco era il book del PM.
        n = len(gestori)
        return {"error": (f"{tkr}: nessun gestore 13F configurato per questo simbolo "
                          f"({n} fond{'o' if n == 1 else 'i'} chius{'o' if n == 1 else 'i'} "
                          f"copert{'o' if n == 1 else 'i'} oggi). Estensione = voce nel negozio "
                          "data/istituzioni.json (sezione cef): investor = uno slug della "
                          "sezione cik, manager = nome del gestore.")}
    # review 16/07: la chiave include max_holdings (capo_block e tool usano tagli diversi)
    _key = (tkr, int(max_holdings))
    hit = _CACHE.get(_key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]

    out = {"ticker": tkr, "manager": cfg["manager"]}

    # 1) NAV e sconto (fonte ufficiale via cef_nav; i suoi errori restano dichiarati)
    try:
        from bellomberg.valuation.cef_nav import get_cef_nav
        out["nav"] = get_cef_nav(tkr)
    except Exception as e:
        out["nav"] = {"error": f"cef_nav non disponibile: {type(e).__name__}: {str(e)[:80]}"}

    # 2) Holdings 13F del gestore. review 16/07: si scarica il filing INTERO (max_items
    # alto) e si normalizza sul totale VERO, poi si mostra il taglio — prima i pesi %
    # erano calcolati sul subset troncato ma etichettati "sul totale del filing".
    try:
        from bellomberg.market_data.sec_edgar import get_13f_holdings
        h_full = get_13f_holdings(cfg["investor"], max_items=200)
        if not h_full:
            out["holdings_13f"] = {"error": "13F non disponibile (SEC muta o parse fallito): "
                                            "buco dichiarato, NON dedurre che il fondo sia vuoto"}
        else:
            tot = sum(x.get("value_usd", 0) for x in h_full) or 1
            top = h_full[:max_holdings]
            coperto = round(100.0 * sum(x.get("value_usd", 0) for x in top) / tot, 1)
            out["holdings_13f"] = {
                "filing_date": h_full[0].get("filing_date"),
                "proxy_warning": (
                    "PROXY DICHIARATO: portafoglio 13F del GESTORE (" + cfg["manager"] +
                    ", tutti i suoi fondi), NON i pesi del singolo fondo " + tkr +
                    "; solo long US >200M$; lag ~45 giorni. Fuori dal 13F: "
                    + (cfg.get("not_in_13f") or "n.d. (campo not_in_13f assente nel negozio)") + ". I pesi % sono sul totale del filing ("
                    + str(len(h_full)) + " posizioni); qui le prime " + str(len(top))
                    + " = " + str(coperto) + "% del filing."),
                "top_holdings": [
                    {"issuer": x["issuer"], "pct_of_13f": round(100.0 * x["value_usd"] / tot, 1),
                     "value_usd_m": round(x["value_usd"] / 1e6)} for x in top],
            }
    except Exception as e:
        out["holdings_13f"] = {"error": f"{type(e).__name__}: {str(e)[:100]} — buco dichiarato"}

    out["uso"] = ("Per la dottrina bilaterale (PM 16/07): un CEF si giudica per COSA contiene "
                  "e per lo sconto sul NAV vs la sua storia, non per il suo Sharpe trailing.")
    # review 16/07: MAI cachare esiti con errori (rete/SEC giu' per 6h avvelenerebbe
    # anche le chat successive) — coerente con cef_nav che non cacha gli errori.
    _err = (out.get("nav") or {}).get("error") or (out.get("holdings_13f") or {}).get("error")
    if not _err:
        _CACHE[_key] = (time.time(), out)
    return out


def capo_block(max_holdings: int = 10) -> str:
    """Blocco compatto per il prompt del Capo (e red team): tutti i CEF configurati
    NEL NEGOZIO che risultano nel book. Ritorna "" se nessun dato — il chiamante
    DICHIARA lo skip.

    05/09 (chat e3): i gestori si leggono dal negozio, come in `get_lookthrough`. Fino
    a qui la funzione usava la costante `CEF_MANAGERS`, rimossa dal lotto 5a (bc905de)
    quando i gestori sono passati nel negozio privato: due usi, zero definizioni ->
    `NameError` a ogni chiamata, inghiottito dall'`except` di capo.py:545-554. Il memo
    del PM ha perso il blocco senza che nulla lo dicesse fuori dal log.
    Negozio non leggibile: il blocco lo DICHIARA al modello invece di uscire vuoto —
    "" non distinguerebbe «nessun fondo chiuso in portafoglio» da «non ho potuto
    guardare» (regola no-fallback PM 14/07)."""
    negozio = _gestori()
    if negozio["origine"] in ("assente", "illeggibile"):
        return (_TITOLO + "\nNON DISPONIBILE: negozio delle istituzioni "
                + negozio["origine"].upper() + " (" + str(negozio["motivo"]) + "): "
                "nessun gestore 13F configurato, il look-through non e' calcolabile. "
                "Non e' «nessun fondo chiuso in portafoglio»: e' un dato che manca.")
    gestori = negozio["istituzioni"]["cef"]
    _book_nota = ""
    try:
        import os
        import sqlite3
        # review 16/07: mode=ro — connect_sqlite (rw+WAL) su path sbagliato CREA un DB
        # fantasma: la lezione "0 posizioni = path sbagliato" del CLAUDE.md.
        from bellomberg.storage.memory_db import SQLITE_PATH as path   # B4 (02/09): percorso unico
        cx = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
        book = {r[0] for r in cx.execute("SELECT ticker FROM positions WHERE is_active=1")}
        cx.close()
    except Exception as e:
        book = set(gestori)  # DB non leggibile: si prova comunque sui CEF del negozio
        _book_nota = ("NB: book non leggibile dal DB (%s) — mostro i CEF configurati "
                      "senza conferma che siano in portafoglio." % type(e).__name__)
    righe = []
    for tkr in sorted(gestori):
        if tkr not in book:
            continue
        lt = get_lookthrough(tkr, max_holdings=max_holdings)
        if lt.get("error"):
            righe.append(f"{tkr}: {lt['error']}")
            continue
        nav = lt.get("nav") or {}
        disc = nav.get("discount_to_nav_pct")
        nav_line = (f"sconto sul NAV {disc:+.1f}% (NAV {nav.get('nav_per_share_usd')}$ "
                    f"as-of {nav.get('nav_as_of')})" if disc is not None
                    else "sconto NAV n.d. (" + str(nav.get("error") or nav.get("discount") or "?") + ")")
        h13 = lt.get("holdings_13f") or {}
        if h13.get("top_holdings"):
            # review 16/07: niente .title() (storpiava "MCDONALD'S" in "Mcdonald'S"):
            # gli issuer restano come nel filing SEC.
            hh = ", ".join("%s %.1f%%" % (x["issuer"][:26], x["pct_of_13f"])
                           for x in h13["top_holdings"][:max_holdings])
            righe.append(f"{tkr} ({lt['manager']}): {nav_line}. Dentro (13F gestore "
                         f"{h13.get('filing_date')}): {hh}. {h13.get('proxy_warning')}")
        else:
            righe.append(f"{tkr}: {nav_line}. Holdings 13F: "
                         + str((h13 or {}).get("error", "n.d.")))
    if not righe:
        return ""
    return ("=== LOOK-THROUGH CEF (cosa contengono i fondi chiusi del book) ===\n"
            "Un CEF si giudica per cio' che CONTIENE e per lo sconto sul NAV, non per il "
            "suo Sharpe trailing (dottrina bilaterale PM 16/07).\n"
            + (_book_nota + "\n" if _book_nota else "") + "\n".join(righe))


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        raise SystemExit("uso: python cef_lookthrough.py <TICKER_CEF>  (nessun default: "
                         "un ripiego zitto sceglierebbe un titolo al posto tuo)")
    print(json.dumps(get_lookthrough(sys.argv[1]),
                     indent=2, ensure_ascii=False, default=str))
    print("\n" + capo_block())
