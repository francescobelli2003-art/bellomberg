"""
cef_nav.py — NAV E SCONTO DEI FONDI CHIUSI (15/07, richiesta PM).

Un fondo chiuso quotato NON si valuta a DCF (guardia in dcf_engine) ma a NAV e
sconto/premio sul NAV: questo modulo da' al comitato lo strumento GIUSTO.

05/09 (classificazione lotto 2b): il registro dei fetcher `FETCH_NAV` e' per FONTE
(dominio del sito ufficiale -> funzione che legge NAV/share, data as-of, ritorni);
la copertura per SIMBOLO viene dal negozio dei veicoli (`data/veicoli.json`): una
voce `tipo=cef` con `nav_fonte` = chiave del registro, `nav_valuta` e `nome`.
`CEF_SOURCES` e' la VISTA derivata (stessa forma di ieri: fetch, nav_currency,
name), `CEF_SENZA_FONTE` i fondi dichiarati senza una fonte riconosciuta, col
motivo. Ieri la copertura era un dict cablato col fondo del PM e l'errore
elencava i fondi coperti: il book nel testo che il modello legge.

Lo sconto richiede prezzo e NAV nella STESSA valuta: una quotazione in GBp su
Yahoo -> pence/100 = GBP -> USD via GBPUSD=X. OGNI conversione e' dichiarata
nel payload. NAV piu' vecchio di 14 giorni = marcato STALE. Ticker senza fonte
riconosciuta = errore dichiarato (mai proxy zitti). Cache in-memory 1h.

LIMITE DICHIARATO v1: solo sconto CORRENTE; lo storico dello sconto (per dire
se -33% e' caro o a buon mercato vs la sua media) e' una voce futura — la
pagina performance/nav del sito e' renderizzata via JS, non parsabile flat.
"""
import re
import time
from datetime import datetime

import bellomberg.storage.classificazione as cl

_CACHE = {}
_TTL_S = 3600
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
NAV_STALE_DAYS = 14


def _fetch_psh_nav() -> dict:
    """NAV settimanale dalla homepage ufficiale pershingsquareholdings.com (blocco
    'current-nav-update': NAV/share USD, as-of, ritorni). Errori dichiarati, mai None zitti."""
    import requests
    r = requests.get("https://pershingsquareholdings.com/", headers=_UA, timeout=20)
    if not r.ok:
        return {"error": f"pershingsquareholdings.com HTTP {r.status_code}"}
    t = r.text
    m_nav = re.search(r"<strong>\$([\d.,]+)</strong>\s*NAV/Share", t)
    if not m_nav:
        return {"error": "pattern NAV/Share non trovato in homepage (layout cambiato?)"}
    out = {"nav_per_share_usd": float(m_nav.group(1).replace(",", ""))}
    m_date = re.search(r"as of\s+(\d{1,2}/\d{1,2}/\d{4})", t)
    if m_date:
        mm, dd, yy = m_date.group(1).split("/")
        out["nav_as_of"] = f"{yy}-{int(mm):02d}-{int(dd):02d}"
    else:
        out["nav_as_of"] = None
        out["as_of_note"] = "data as-of non trovata: trattare il NAV con cautela"
    for label, key in (("MTD", "mtd_return_pct"), ("QTD", "qtd_return_pct"),
                       ("YTD", "ytd_return_pct")):
        m = re.search(r"<strong>(-?[\d.]+)%</strong>\s*" + label, t)
        out[key] = float(m.group(1)) if m else None
    out["nav_source"] = "pershingsquareholdings.com (NAV settimanale ufficiale)"
    out["nav_currency"] = "USD"      # la fonte pubblica il NAV in USD: dichiarato nel payload
    return out


# REGISTRO DEI FETCHER, per FONTE (dominio del sito ufficiale), mai per simbolo: un fondo
# entra nella copertura con una voce nel negozio dei veicoli (nav_fonte = una chiave qui).
FETCH_NAV = {
    "pershingsquareholdings.com": _fetch_psh_nav,
}
# la valuta in cui OGNI fonte pubblica il NAV: `nav_valuta` del negozio deve coincidere
# (review 05/09: era testo libero e con EUR lo sconto mescolava le valute zitto)
VALUTA_FONTE = {
    "pershingsquareholdings.com": "USD",
}


def _cef_del_negozio(negozio=None):
    n = negozio if negozio is not None else cl.carica_veicoli()
    return n, {t: v for t, v in n["veicoli"].items() if v["tipo"] == "cef"}


def cef_sources_del_negozio(negozio=None) -> dict:
    """La VISTA che sostituisce il dict cablato di ieri: {TICKER: {fetch, nav_currency, name,
    nav_fonte}} per le voci `tipo=cef` con `nav_fonte` nel registro E `nav_valuta` dichiarata.
    Negozio assente/illeggibile = dict vuoto (il motivo sta in carica_veicoli()['motivo'])."""
    _, cef = _cef_del_negozio(negozio)
    out = {}
    for t, v in cef.items():
        if v["nav_fonte"] in FETCH_NAV and v["nav_valuta"] and v["nav_valuta"] == VALUTA_FONTE[v["nav_fonte"]]:
            out[t] = {"fetch": FETCH_NAV[v["nav_fonte"]], "nav_currency": v["nav_valuta"],
                      "name": v["nome"] or t, "nav_fonte": v["nav_fonte"]}
    return out


def cef_senza_fonte_del_negozio(negozio=None) -> dict:
    """{TICKER: motivo} dei fondi chiusi DICHIARATI nel negozio che il tool non puo' servire:
    nav_fonte assente, non nel registro, o nav_valuta assente. Dichiarati, non taciuti."""
    _, cef = _cef_del_negozio(negozio)
    out = {}
    for t, v in cef.items():
        if not v["nav_fonte"]:
            out[t] = "campo nav_fonte assente nella voce del negozio"
        elif v["nav_fonte"] not in FETCH_NAV:
            out[t] = ("nav_fonte %r non e' una chiave di cef_nav.FETCH_NAV (%s)"
                      % (v["nav_fonte"], ", ".join(sorted(FETCH_NAV))))
        elif not v["nav_valuta"]:
            out[t] = "campo nav_valuta assente nella voce del negozio (serve la valuta del NAV)"
        elif v["nav_valuta"] != VALUTA_FONTE[v["nav_fonte"]]:
            out[t] = ("nav_valuta %r nel negozio ma la fonte %s pubblica il NAV in %s: valute diverse, "
                      "lo sconto non si calcola" % (v["nav_valuta"], v["nav_fonte"], VALUTA_FONTE[v["nav_fonte"]]))
    return out


CEF_SOURCES = cef_sources_del_negozio()
CEF_SENZA_FONTE = cef_senza_fonte_del_negozio()


def _fx_rate(yf, pair: str):
    """Ultimo cambio da yfinance, robusto alle differenze di versione:
    fast_info (attributo) -> info -> chiusura 1d. None se tutto fallisce."""
    tk = yf.Ticker(pair)
    try:
        v = getattr(tk.fast_info, "last_price", None)
        if v:
            return float(v)
    except Exception:
        pass
    try:
        v = (tk.info or {}).get("regularMarketPrice")
        if v:
            return float(v)
    except Exception:
        pass
    try:
        h = tk.history(period="1d")
        if h is not None and not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


def get_cef_nav(ticker: str, negozio=None) -> dict:
    """NAV, prezzo convertito e sconto/premio per un fondo chiuso dichiarato nel negozio dei
    veicoli con una fonte riconosciuta. `negozio` iniettabile (prove); default: riletto."""
    tkr = (ticker or "").upper().strip()
    n = negozio if negozio is not None else cl.carica_veicoli()
    if n["origine"] in ("assente", "illeggibile"):
        return {"ticker": tkr, "error": (f"{tkr}: negozio dei veicoli {n['origine'].upper()} "
                                         f"({n['motivo']}): la copertura NAV dei fondi chiusi non "
                                         "e' determinabile finche' il file non c'e'/non e' corretto.")}
    fonti = cef_sources_del_negozio(n)
    src = fonti.get(tkr)
    if not src:
        senza = cef_senza_fonte_del_negozio(n)
        if tkr in senza:
            return {"ticker": tkr, "error": (
                f"{tkr}: fondo chiuso dichiarato nel negozio dei veicoli ma senza fonte NAV "
                f"riconosciuta ({senza[tkr]}). Serve una fonte NAV ufficiale: nav_fonte = una "
                f"chiave di cef_nav.FETCH_NAV ({', '.join(sorted(FETCH_NAV))}) e nav_valuta — "
                "non un proxy.")}
        return {"ticker": tkr, "error": (
            f"{tkr}: nessuna fonte NAV configurata (non e' un fondo chiuso con fonte nel negozio "
            f"dei veicoli: {len(fonti)} fond{'o' if len(fonti) == 1 else 'i'} chius{'o' if len(fonti) == 1 else 'i'} "
            f"copert{'o' if len(fonti) == 1 else 'i'} oggi). Per aggiungerne uno serve una voce tipo=cef con "
            f"nav_fonte = una chiave di cef_nav.FETCH_NAV ({', '.join(sorted(FETCH_NAV))}) e "
            "nav_valuta: una fonte NAV ufficiale, non un proxy.")}
    # la cache e' per VOCE (ticker, fonte, valuta, nome): un edit del negozio non viene servito
    # col payload vecchio per un'ora (review 05/09)
    chiave_cache = (tkr, src["nav_fonte"], src["nav_currency"], src["name"])
    hit = _CACHE.get(chiave_cache)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]

    try:
        nav = src["fetch"]()
    except Exception as e:      # la fonte che solleva e' un errore DICHIARATO, non un'eccezione al desk
        return {"ticker": tkr, "error": "fonte NAV %s: %s: %s" % (src["nav_fonte"], type(e).__name__, str(e)[:120])}
    if nav.get("error"):
        return {"ticker": tkr, "error": "fonte NAV: " + nav["error"]}
    if nav.get("nav_currency") and nav["nav_currency"] != src["nav_currency"]:
        return {"ticker": tkr, "error": ("fonte NAV %s: il payload dichiara il NAV in %s, il negozio dice nav_valuta %s: "
                                         "valute diverse, sconto non calcolato" % (src["nav_fonte"], nav["nav_currency"], src["nav_currency"]))}
    out = {"ticker": tkr, "name": src["name"], "nav_fonte": src["nav_fonte"], **nav}

    # staleness del NAV (dichiarata, regola no-fallback)
    if out.get("nav_as_of"):
        try:
            age_d = (datetime.now() - datetime.fromisoformat(out["nav_as_of"])).days
            out["nav_age_days"] = age_d
            if age_d > NAV_STALE_DAYS:
                out["nav_staleness"] = (f"STALE: NAV di {age_d} giorni fa "
                                        f"(soglia {NAV_STALE_DAYS}g, pubblicazione settimanale)")
        except Exception:
            pass

    # prezzo di mercato + conversione nella valuta del NAV (tutto dichiarato)
    try:
        import yfinance as yf
        info = yf.Ticker(tkr).info or {}
        px = info.get("regularMarketPrice") or info.get("currentPrice")
        ccy = info.get("currency") or "?"
        if px is None:
            out["discount"] = "n.d. (prezzo di mercato non disponibile da yfinance)"
        else:
            out["market_price"] = px
            out["market_price_currency"] = ccy
            px_conv, note = None, None
            navccy = src["nav_currency"]
            if ccy == navccy:
                px_conv = float(px)
            elif ccy == "GBp" and navccy == "USD":
                fx = _fx_rate(yf, "GBPUSD=X")
                if fx:
                    px_conv = float(px) / 100.0 * float(fx)
                    note = f"GBp/100 -> GBP -> USD a GBPUSD={float(fx):.4f} [src: yfinance]"
                else:
                    out["discount"] = "n.d. (FX GBPUSD non disponibile: conversione impossibile)"
            else:
                out["discount"] = (f"n.d. (prezzo in {ccy}, NAV in {navccy}: "
                                   "conversione non configurata)")
            if px_conv is not None:
                out["market_price_in_nav_ccy"] = round(px_conv, 2)
                if note:
                    out["fx_conversion"] = note
                disc = (px_conv / out["nav_per_share_usd"] - 1) * 100
                out["discount_to_nav_pct"] = round(disc, 1)
                out["discount_note"] = ("negativo = il mercato paga il fondo MENO dei suoi "
                                        "asset (sconto); il segnale operativo e' lo sconto "
                                        "vs la sua storia, non il livello assoluto "
                                        "(storico: voce futura, per ora contesto qualitativo)")
    except Exception as e:
        out["discount"] = f"n.d. (prezzo/FX: {type(e).__name__}: {str(e)[:80]})"

    _CACHE[chiave_cache] = (time.time(), out)
    return out


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        raise SystemExit("uso: python cef_nav.py <TICKER_CEF>  (nessun default: un ripiego "
                         "zitto sceglierebbe un titolo al posto tuo)")
    print(json.dumps(get_cef_nav(sys.argv[1]),
                     indent=2, ensure_ascii=False, default=str))
