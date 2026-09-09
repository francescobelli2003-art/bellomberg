"""
DAT METRICS (#199-Crypto): metriche UFFICIALI delle Digital Asset Treasuries (DAT) in book.

05/09 (classificazione lotto 2b): il registro dei fetcher `FETCH_DAT` e' per FONTE (dominio
del sito ufficiale della tesoreria), la copertura per SIMBOLO viene dal negozio dei veicoli
(`data/veicoli.json`): una voce `tipo=dat` con `nav_fonte` = chiave del registro. Ieri il
dispatcher instradava per alias cablati del book e l'errore elencava le DAT supportate.
- fonte con `__NEXT_DATA__` (btcTrackerData: holdings BTC, azioni, debt/pref/cash) -> mNAV
  equity/EV calcolati IN CODICE con prezzi live yfinance;
- fonte con `/api/dashboard-inputs` (JSON del sito IR, migrazione 23/07: il sito ha
  abbandonato window.DashboardConfig; input POSITIVI, segni nella formula, warrant a
  treasury method) -> mirror ESATTO della computeDashboard del sito. Prezzi live: azione da
  yfinance, HYPE da Hyperliquid allMids (fallback yfinance, dichiarato). ATTENZIONE: e'
  l'AZIONE della tesoreria, NON il token omonimo sul DEX.
Convenzione anti-allucinazione (context B.4): payload; _source/_timestamp li aggiunge il
dispatcher; su errore {"error": "..."} e MAI eccezioni verso il chiamante. Cache 30 min per
ticker, nel dispatcher (un esito con errore non si cachea).
"""
import json
import re
import time

import requests

import bellomberg.storage.classificazione as cl

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 20
_CACHE = {}
_CACHE_TTL_S = 1800


def _cache_get(key):
    v = _CACHE.get(key)
    if v and (time.time() - v[0]) < _CACHE_TTL_S:
        return v[1]
    return None


def _cache_put(key, val):
    _CACHE[key] = (time.time(), val)


try:  # 27/08: strategy.com (Akamai) risponde 403 a `requests` coi 2 UA provati
    # (nessuno, Chrome/126 — quello che questo modulo mandava) e 200 a un client
    # che manda il SET DI HEADER di un browser. Misurato (n=1 per cella, 27/08):
    # `curl_cffi` impersonate chrome/chrome124/safari/firefox con gli header di
    # default → 200 con __NEXT_DATA__ e btcTrackerData; impronta TLS Chrome ma
    # header disattivati → 403; `requests` liscio coi 13 header letti dal
    # verbose → 200 identico (review). Quindi il cancello oggi e' il set di
    # header (sec-ch-ua, sec-fetch-*, accept, accept-language, priority…), NON
    # l'impronta TLS. `curl_cffi` lo manda e lo MANTIENE con la libreria (gia'
    # dipendenza di yfinance in questo ambiente); l'alternativa e' un dict di
    # header a mano su `requests`: zero dipendenze, ma da tenere aggiornato a
    # mano. Scelta reversibile in una riga. E' una corsa agli armamenti: un
    # aggiornamento di libreria o di Akamai puo' rompere o riparare in silenzio
    # — il 403 esce SEMPRE dichiarato al desk (v. `_fetch_next_data`).
    from curl_cffi import requests as _curl_requests
    _curl_get = _curl_requests.get
    _CURL_OK = True
except Exception:  # modulo assente: si DICHIARA, niente ripiego zitto (v. sotto)
    _curl_get = None
    _CURL_OK = False

# Alias mobile: in curl_cffi 0.15.0 «chrome» = chrome146 (misura 27/08) e si
# sposta con `pip install -U curl_cffi`/yfinance — con lui si sposta il set di
# header (sec-ch-ua v=146, UA Chrome/146). Il nome finisce nel testo d'errore.
IMPERSONATE = "chrome"


def _fetch_next_data(url):
    """HTML del sito ufficiale → JSON di `__NEXT_DATA__`, col client che manda
    il set di header di un browser. Senza `curl_cffi` si solleva un errore CHE
    LO DICE: un ripiego su `requests` tornerebbe 403 e il desk cercherebbe la
    causa in strategy.com invece che nella dipendenza mancante (regola 14/07).
    Un non-200 solleva con URL, codice e impersonazione (la forma della libreria,
    «HTTP Error 403: Forbidden», perdeva la fonte — review 27/08)."""
    if not _CURL_OK:
        raise RuntimeError("curl_cffi non installato: strategy.com risponde 403 al client "
                           "standard (Akamai); `pip install curl_cffi` (requirements.txt)")
    r = _curl_get(url, impersonate=IMPERSONATE, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError("%s HTTP %s %s (impersonate=%s)"
                           % (url, r.status_code, getattr(r, "reason", "") or "", IMPERSONATE))
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise ValueError("__NEXT_DATA__ non trovato in " + url)
    return json.loads(m.group(1))


def _flatten_scalars(d, prefix=""):
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[prefix + str(k)] = v
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, (str, int, float, bool)) or v2 is None:
                    out[prefix + str(k) + "." + str(k2)] = v2
    return out


def _derivati_mnav_btc(latest, ticker):
    """mNAV equity/EV della DAT bitcoin calcolati IN CODICE (fix 21/07, memo #46: il desk li
    ricalcolava a mano e ha presentato l'equity 0,66x come 'sconto 34%' ignorando
    15,5 mld $ di preferred + debito davanti alle ordinarie; l'EV era ~1,0x).
    Regola: il numero sta nel codice, l'LLM narra. Dati mancanti = mnav_error
    dichiarato, mai un proxy zitto."""
    try:
        btc = float(latest.get("btc_holdings") or 0)
        sh = float(latest.get("basic_shares_outstanding") or 0)
        debt = float(latest.get("debt") or 0)
        pref = float(latest.get("pref") or 0)
        cash = float(latest.get("cash") or 0)
        if btc <= 0 or sh <= 0:
            return {"mnav_error": "btc_holdings/basic_shares_outstanding mancanti nel record strategy.com"}
        import yfinance as yf
        px_mstr = float(yf.Ticker(ticker).fast_info["last_price"] or 0)
        px_btc = float(yf.Ticker("BTC-USD").fast_info["last_price"] or 0)
        if px_mstr <= 0 or px_btc <= 0:
            return {"mnav_error": "prezzo azione/BTC non disponibile da yfinance: niente mNAV (no proxy)"}
        btc_nav = btc * px_btc
        mktcap = px_mstr * sh
        # review 21/07 finding: se il sito toglie/rinomina debt/pref/cash, il vecchio
        # "or 0" faceva degenerare mnav_ev nell'equity SENZA dirlo (stessa classe di
        # errore del memo #46, ma col timbro del tool). Campo assente != zero vero.
        missing = [k for k in ("debt", "pref", "cash") if latest.get(k) is None]
        dm = {
            "mnav_equity_basic": round(mktcap / btc_nav, 3),
            "inputs": {
                "price_mstr": round(px_mstr, 2), "price_btc": round(px_btc),
                "price_src": "yfinance fast_info",
                "computed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "cache_ttl_min": int(_CACHE_TTL_S / 60),
                "btc_nav_usd": round(btc_nav), "mktcap_basic_usd": round(mktcap),
                "debt_usd": round(debt), "pref_usd": round(pref), "cash_usd": round(cash),
                "record_as_of": latest.get("as_of_date"),
            },
            "leggimi": ("Per giudizi di sconto/premio sul NAV usare mnav_ev: davanti alle azioni "
                        "ordinarie ci sono debt+preferred. mnav_equity_basic ignora debito e "
                        "preferred e usa le sole azioni BASIC (convertibili/RSU esclusi: limite "
                        "dichiarato). Prezzi al computed_at_utc (payload in cache fino a "
                        "cache_ttl_min). Citare SEMPRE entrambi con gli input."),
        }
        if missing:
            dm["mnav_ev"] = None
            dm["mnav_warning"] = ("mnav_ev NON calcolato: campi %s assenti nel record "
                                  "strategy.com (assente != zero; layout cambiato?)" % missing)
        else:
            dm["mnav_ev"] = round((mktcap + debt + pref - cash) / btc_nav, 3)
        return {"derived_mnav": dm}
    except Exception as e:
        return {"mnav_error": "mNAV non calcolato: " + type(e).__name__ + ": " + str(e)}


def _fetch_dat_next_data(ticker):
    """Record DAT piu' recente dalla fonte ufficiale con __NEXT_DATA__ + derivati prudenti.
    Fetcher del registro: riceve il ticker (per il prezzo live), non lo conosce."""
    nd = _fetch_next_data("https://www.strategy.com/")
    rows = (nd.get("props", {}).get("pageProps", {}) or {}).get("btcTrackerData") or []
    if not rows:
        return {"error": "btcTrackerData vuoto/assente su strategy.com (layout cambiato?)"}
    latest = _flatten_scalars(rows[0])
    out = {
        "dat_di": "Bitcoin",
        "fonte": "strategy.com (sito ufficiale della tesoreria) - record btcTrackerData[0]",
        "nota": "mNAV equity e EV sono CALCOLATI dal tool nel campo derived_mnav (prezzi live yfinance, debt/pref/cash dal record): NON ricalcolarli a mano. Per giudizi di sconto/premio usare mnav_ev.",
        "latest": latest,
    }
    mnav = {k: v for k, v in latest.items() if "mnav" in k.lower()}
    if mnav:
        out["mnav_fields"] = mnav
    try:
        btc = float(latest.get("btc_holdings") or 0)
        sh = float(latest.get("basic_shares_outstanding") or 0)
        if btc > 0 and sh > 0:
            out["derived"] = {"btc_per_1000_shares": round(btc / sh * 1000.0, 4)}
    except (TypeError, ValueError):
        pass
    out.update(_derivati_mnav_btc(latest, ticker))
    if len(rows) > 1:
        prev = _flatten_scalars(rows[1])
        delta = {}
        for f in ("btc_holdings", "basic_shares_outstanding"):
            try:
                a, b = float(latest.get(f) or 0), float(prev.get(f) or 0)
                if a and b:
                    delta[f + "_delta"] = round(a - b, 2)
            except (TypeError, ValueError):
                continue
        if delta:
            out["delta_vs_record_precedente"] = delta
    # review 21/07: un mnav_error transitorio (es. hiccup yfinance) NON va congelato
    # 30 min in cache — lo decide il dispatcher (gli errori della fonte escono prima).
    return out


_NOTA_HYPE = ("Formula UFFICIALE (hypestrat.xyz, computeDashboard/Excel: input POSITIVI, segni nella formula): "
              "Adjusted NAV = bookNAV - cashFromOps + cashFromFin - treasuryDeploy - reportedDigital "
              "+ (HYPE live x hypeHeld) + dtlChange, con dtlChange = reportedDTL - taxRate x (HYPE value - taxBasis); "
              "FD shares = basicShares + warrant ITM a TREASURY METHOD ((px-strike)x amount/px); "
              "mNAV = prezzo dell'azione / (Adjusted NAV / FD shares); mnav_dtl_addback riaggiunge la DTL (riga C24 del sito). "
              "NB: e' l'AZIONE della tesoreria quotata al Nasdaq, NON il token omonimo sul DEX.")


def _prezzi_live_hype(ticker):
    """(purr_px, purr_fonte, hype_px, hype_fonte) — fonti DICHIARATE, mai zitte.
    Azione: yfinance sul ticker chiesto. HYPE: Hyperliquid allMids (venue nativa, stessa fonte
    live del sito), fallback yfinance HYPE32196-USD dichiarato."""
    purr_px, purr_src = None, None
    try:
        import yfinance as yf
        purr_px = float(yf.Ticker(ticker).fast_info.last_price)
        purr_src = "yfinance %s (Nasdaq)" % ticker
    except Exception as e:
        purr_src = "yfinance ko: " + str(e)[:80]
    hype_px, hype_src = None, None
    try:
        r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "allMids"}, timeout=10)
        hype_px = float(r.json().get("HYPE"))
        hype_src = "hyperliquid allMids"
    except Exception:
        try:
            import yfinance as yf
            hype_px = float(yf.Ticker("HYPE32196-USD").fast_info.last_price)
            hype_src = "yfinance HYPE32196-USD (fallback: hyperliquid allMids ko, dichiarato)"
        except Exception as e:
            hype_src = "hyperliquid E yfinance ko: " + str(e)[:80]
    return purr_px, purr_src, hype_px, hype_src


def _fetch_dat_dashboard_inputs(ticker):
    """Input DAT dal JSON /api/dashboard-inputs del sito IR + mirror ESATTO della
    computeDashboard del sito (migrazione 23/07: DashboardConfig non esiste piu').
    Fetcher del registro: riceve il ticker (per il prezzo live), non lo conosce."""
    r = requests.get("https://www.hypestrat.xyz/api/dashboard-inputs", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    inp = payload.get("inputs") or {}
    if not inp.get("basicShares") or not inp.get("hypeHeld"):
        return {"error": "inputs incompleti da /api/dashboard-inputs (basicShares/hypeHeld mancanti)",
                "nota_mnav": _NOTA_HYPE, "raw_keys": sorted(inp.keys())}

    out = {
        "dat_di": "HYPE",
        "fonte": "hypestrat.xyz/api/dashboard-inputs (sito IR ufficiale, JSON)",
        "nota_mnav": _NOTA_HYPE,
        "unita": "valori in milioni ($M / M azioni / M token); quote in $ per unita'",
        "dat_fundamentals_musd": {k: v for k, v in inp.items()
                                  if isinstance(v, (int, float))},
        "warrants": inp.get("warrants") or [],
        "vintage": {"effective_date": inp.get("effectiveDate"),
                    "next_update": inp.get("nextUpdate"),
                    "lag_note": (inp.get("noteText") or "")[:300] or None},
    }

    purr_px, purr_src, hype_px, hype_src = _prezzi_live_hype(ticker)
    out["purr_quote"] = {"last": purr_px, "fonte": purr_src}
    out["hype_quote"] = {"last": hype_px, "fonte": hype_src}

    # Mirror ESATTO di computeDashboard(inp, liveHype, livePurr) del sito
    # (commenti C7..C39 = celle dell'Excel di riferimento citate nel JS).
    try:
        if purr_px and hype_px and purr_px > 0 and hype_px > 0:
            cur_hype_val = inp["hypeHeld"] * hype_px                                  # C7
            dilutive = 0.0                                                            # treasury method
            warrants_itm = []
            for w in (inp.get("warrants") or []):
                strike, amount = float(w.get("strike") or 0), float(w.get("amount") or 0)
                if strike and amount and strike < purr_px:
                    dilutive += (purr_px - strike) * amount / purr_px
                    warrants_itm.append(f"{amount}M @{strike}")
            fd_shares = inp["basicShares"] + dilutive                                 # C11
            tax = (inp.get("taxRate") or 0) / 100.0
            dtl = -tax * (cur_hype_val - (inp.get("taxBasis") or 0))                  # C38 (con segno, neg se plus)
            dtl_change = (inp.get("reportedDTL") or 0) - tax * (cur_hype_val - (inp.get("taxBasis") or 0))  # C21
            adj_nav = ((inp.get("bookNAV") or 0) - (inp.get("cashFromOps") or 0)
                       + (inp.get("cashFromFin") or 0) - (inp.get("treasuryDeploy") or 0)
                       - (inp.get("reportedDigital") or 0) + cur_hype_val + dtl_change)  # C22
            if fd_shares > 0:
                anav_ps = adj_nav / fd_shares                                         # C23
                anav_ps_dtl = (adj_nav - dtl) / fd_shares                             # C24 (dtl<0 -> riaggiunge)
                out["derived"] = {
                    "current_hype_value_musd": round(cur_hype_val, 1),
                    "dtl_musd_signed": round(dtl, 1),
                    "dtl_change_musd": round(dtl_change, 1),
                    "adjusted_nav_musd": round(adj_nav, 1),
                    "fully_diluted_shares_m": round(fd_shares, 3),
                    "warrants_itm_inclusi": warrants_itm,
                    "warrants_metodo": "treasury method (come computeDashboard del sito)",
                    "adjusted_nav_per_fd_share": round(anav_ps, 4),
                    "adjusted_nav_per_fd_share_dtl_addback": round(anav_ps_dtl, 4),
                    "mnav": round(purr_px / anav_ps, 3) if anav_ps > 0 else None,
                    "mnav_dtl_addback": round(purr_px / anav_ps_dtl, 3) if anav_ps_dtl > 0 else None,
                    "nota": "Mirror della computeDashboard del sito sugli input /api/dashboard-inputs; "
                            "prezzi live dichiarati in purr_quote/hype_quote.",
                }
        else:
            out["derived_warning"] = ("derivati non calcolati: prezzo mancante "
                                      f"(azione: {purr_src} | HYPE: {hype_src})")
    except Exception as e:
        out["derived_warning"] = "derivati non calcolati: " + type(e).__name__ + ": " + str(e)

    return out


# REGISTRO DEI FETCHER, per FONTE (dominio del sito ufficiale), mai per simbolo: una DAT
# entra nella copertura con una voce nel negozio dei veicoli (nav_fonte = una chiave qui).
FETCH_DAT = {
    "strategy.com": _fetch_dat_next_data,
    "hypestrat.xyz": _fetch_dat_dashboard_inputs,
}


def fonte_dat_del_negozio(ticker, negozio=None):
    """(chiave del registro, None) se la voce del simbolo e' una DAT con `nav_fonte`
    riconosciuta; altrimenti (None, motivo dichiarato). Mai un alias, mai un elenco."""
    t = (ticker or "").upper().strip()
    n = negozio if negozio is not None else cl.carica_veicoli()
    if n["origine"] in ("assente", "illeggibile"):
        return None, ("%s: negozio dei veicoli %s (%s): la copertura delle DAT non e' determinabile "
                      "finche' il file non c'e'/non e' corretto." % (t, n["origine"].upper(), n["motivo"]))
    dat = {k: v for k, v in n["veicoli"].items() if v["tipo"] == "dat"}
    coperte = sum(1 for v in dat.values() if v["nav_fonte"] in FETCH_DAT)
    v = dat.get(t)
    chiavi = ", ".join(sorted(FETCH_DAT))
    if v is None:
        return None, ("%s: non e' una DAT dichiarata nel negozio dei veicoli (%d DAT con fonte "
                      "copert%s oggi). Per coprirla serve una voce tipo=dat con nav_fonte = una "
                      "chiave di dat_metrics.FETCH_DAT (%s): una fonte ufficiale, non un proxy."
                      % (t, coperte, "a" if coperte == 1 else "e", chiavi))
    if not v["nav_fonte"]:
        return None, ("%s: DAT dichiarata nel negozio dei veicoli ma senza fonte (campo nav_fonte "
                      "assente). Serve nav_fonte = una chiave di dat_metrics.FETCH_DAT (%s)." % (t, chiavi))
    if v["nav_fonte"] not in FETCH_DAT:
        return None, ("%s: DAT dichiarata nel negozio dei veicoli con nav_fonte %r che non e' una "
                      "chiave di dat_metrics.FETCH_DAT (%s): fonte non riconosciuta, dichiarato."
                      % (t, v["nav_fonte"], chiavi))
    return v["nav_fonte"], None


def get_dat_metrics(ticker, negozio=None):
    """Entry point unico. `ticker` OBBLIGATORIO: il default era un titolo del book, e a
    chiamata vuota il tool rispondeva col DAT del PM (04/09, Opus 5). L'instradamento viene
    dal negozio dei veicoli (nav_fonte -> fetcher del registro), non da alias cablati.
    Cache 30 min per ticker: mai un esito con errore/mnav_error (si ritenta al giro dopo)."""
    t = (ticker or "").upper().strip()
    if not t:
        return {"error": "get_dat_metrics: serve il ticker della DAT (nessun default)."}
    chiave, motivo = fonte_dat_del_negozio(t, negozio)
    if chiave is None:
        return {"error": motivo}
    hit = _cache_get((t, chiave))       # per (ticker, fonte): un cambio di nav_fonte non serve il vecchio
    if hit:
        return hit
    try:
        out = FETCH_DAT[chiave](t)
    except Exception as e:
        return {"ticker": t, "nav_fonte": chiave,
                "error": "dat_metrics " + t + " (" + chiave + "): " + type(e).__name__ + ": " + str(e)}
    if not isinstance(out, dict):
        return {"ticker": t, "nav_fonte": chiave, "error": "dat_metrics " + t + ": payload non valido dalla fonte " + chiave}
    out = {"ticker": t, "nav_fonte": chiave, **out}    # anche l'errore dice QUALE fonte ha fallito
    if "error" not in out and "mnav_error" not in out:
        _cache_put((t, chiave), out)
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        raise SystemExit("uso: python dat_metrics.py <TICKER_DAT>  (nessun default: un ripiego "
                         "zitto sceglierebbe un titolo al posto tuo)")
    print(json.dumps(get_dat_metrics(sys.argv[1]), indent=2, ensure_ascii=False)[:4000])
