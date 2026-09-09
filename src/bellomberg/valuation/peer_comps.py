# -*- coding: utf-8 -*-
"""peer_comps.py — COMPS AUTOMATICI PER I MODELLI (16/07, richiesta PM:
"vanno sempre trovati i comp giusti nei DCF").

Le liste peer CURATE per sub-settore esistono da sempre in sector_taxonomy
(SUBSECTORS[..]["peers"], inclusi ticker EU), ma dcf_calibration lasciava
spec["comps"] = [] con un TODO: il foglio Comps usciva vuoto su ogni nome.
Il motore BANCA i suoi peer li scarica gia' (bank_peer_comps): questo modulo
e' l'equivalente per i motori OPERATING (v3/fallback).

Per ogni peer: EV, ricavi, EBITDA, P/E da yfinance -> multipli calcolati per
societa' (ratio, quindi neutri rispetto alla valuta del singolo peer).
Peer senza dati = DICHIARATI nella nota, mai scartati in silenzio.
Cache in-memory 1h (una run valuta piu' nomi dello stesso settore)."""
import time
from typing import Dict, List, Optional, Tuple

_CACHE: Dict[str, tuple] = {}
_TTL_S = 3600


def _norm_issuer(name) -> str:
    """audit/12 V0.6: nome emittente normalizzato per il dedup dei cross-listing
    (il listing USA diventava peer del listing europeo: stesso emittente, listing diverso)."""
    import re
    stop = {"nv", "n", "v", "spa", "s", "p", "a", "plc", "inc", "sa", "ag", "se", "ltd",
            "corp", "corporation", "incorporated", "co", "group", "holding", "holdings",
            "adr", "the", "class",
            # review V0: naming Yahoo dei listini tedeschi ("BAYER AG NA O.N.")
            "aktiengesellschaft", "na", "o"}
    toks = [t for t in re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).split() if t not in stop]
    return " ".join(toks)


def _one_peer(p: str) -> Optional[dict]:
    import yfinance as yf
    info = yf.Ticker(p).info or {}
    ev = info.get("enterpriseValue")
    sales = info.get("totalRevenue")
    ebitda = info.get("ebitda")
    pe = info.get("trailingPE") or info.get("forwardPE")
    if not ev or not sales:
        return None
    out = {"name": "%s (%s)" % (info.get("shortName") or p, p),
           "ev": float(ev), "sales": float(sales),
           "ev_sales": round(float(ev) / float(sales), 2),
           "_shortname": info.get("shortName") or info.get("longName"),
           # review V0: il mcap e' GIA' in questa .info — il filtro size lo usa da qui,
           # senza pagare le 1-2 richieste HTTP extra di fast_info per candidato
           "_mcap": info.get("marketCap"),
           "_src": "yfinance"}
    if ebitda and float(ebitda) > 0:
        out["ebitda"] = float(ebitda)
        out["ev_ebitda"] = round(float(ev) / float(ebitda), 1)
    if pe:
        try:
            out["pe"] = round(float(pe), 1)
        except Exception:
            pass
    return out


def fetch_peer_comps(ticker: str, peers: List[str], max_peers: int = 6) -> Tuple[List[dict], str]:
    """Ritorna (comps, nota). comps nel formato atteso da dcf_buyside_v3._comps_multiples
    (name/ev/ebitda/ev_ebitda/ev_sales/pe). La nota dichiara fonte, riusciti e falliti."""
    tk_u = (ticker or "").upper()
    key = tk_u + "|" + ",".join(peers)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    comps, falliti = [], []
    for p in peers:
        if p.upper() == tk_u:
            continue  # il target non e' un peer di se stesso
        if len(comps) >= max_peers:
            break
        try:
            c = _one_peer(p)
            if c:
                comps.append(c)
            else:
                falliti.append(p)
        except Exception as e:
            falliti.append("%s (%s)" % (p, type(e).__name__))
    nota = ("peer AUTO dal sub-settore (sector_taxonomy), multipli via yfinance: "
            "%d su %d con dati" % (len(comps), len(comps) + len(falliti)))
    if falliti:
        nota += "; senza dati (dichiarati): " + ", ".join(falliti)
    if not comps:
        nota += ". NESSUN peer con dati: il metodo comps resta n.d. (dichiarato nel foglio)."
    result = (comps, nota)
    # niente cache sugli esiti vuoti: la rete puo' essere stata giu'
    if comps:
        _CACHE[key] = (time.time(), result)
    return result


def select_peer_comps(ticker: str, info: dict, seed_peers: Optional[List[str]] = None,
                      max_peers: int = 6, max_probe: int = 14) -> Tuple[List[dict], str]:
    """Selezione DETERMINISTICA dei peer (16/07 sera, richiesta PM: "come Bloomberg,
    meno prompt e piu' codice" — il fit non si chiede a un LLM, si CALCOLA).

    Universo: l'INDUSTRY Yahoo del target (classificazione gia' granulare:
    Biotechnology != Healthcare Plans != Drug Manufacturers) via yf.Industry
    .top_companies + la lista seed del sub-settore (copre gli EU). FIT in codice:
      - market cap tra 0.1x e 10x quella del target (niente nano-cap vs mega-cap);
      - margine EBITDA entro +/-15 punti dal target, quando noto per entrambi
        (business model proxy: separa chi fa la stessa cosa da chi no);
      - ranking per vicinanza: |log10(rapporto mcap)| + 3*|delta margine|.
    OGNI scarto e' DICHIARATO col motivo nella nota. Ritorna (comps, nota)."""
    import math

    tk_u = (ticker or "").upper()
    info = info or {}
    ind_key = info.get("industryKey")
    key = "FIT|" + tk_u + "|" + str(ind_key)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]

    t_mcap = info.get("marketCap")
    t_rev, t_ebitda = info.get("totalRevenue"), info.get("ebitda")
    t_margin = (float(t_ebitda) / float(t_rev)) if (t_ebitda and t_rev) else None

    candidati: List[str] = []
    fonte_universo = []
    if ind_key:
        try:
            import yfinance as yf
            tc = yf.Industry(ind_key).top_companies
            if tc is not None and len(tc):
                candidati += [str(s) for s in list(tc.index[:25])]
                fonte_universo.append("industry Yahoo '%s' (%d società)" % (ind_key, min(len(tc), 25)))
        except Exception as e:
            fonte_universo.append("industry Yahoo n.d. (%s)" % type(e).__name__)
    if seed_peers:
        candidati += [p for p in seed_peers if p not in candidati]
        fonte_universo.append("seed sub-settore (%d)" % len(seed_peers))

    comps, scartati = [], []
    probati = 0
    t_name = _norm_issuer(info.get("shortName") or info.get("longName"))
    for p in candidati:
        if p.upper() == tk_u:
            continue
        if len(comps) >= max_peers or probati >= max_probe:
            break
        probati += 1
        try:
            c = _one_peer(p)
        except Exception as e:
            scartati.append("%s (errore %s)" % (p, type(e).__name__))
            continue
        if not c:
            scartati.append("%s (senza dati)" % p)
            continue
        # FIT 0 (audit/12 V0.6): stesso emittente su un altro listino non e' un peer
        # (listing USA selezionato come comp del listing europeo: il titolo confrontato con se stesso)
        if t_name and _norm_issuer(c.get("_shortname")) == t_name:
            scartati.append("%s (stesso emittente: cross-listing del target)" % p)
            continue
        # FIT 1: size — un peer 30x piu' grande/piccolo non e' un comparabile.
        # audit/12 V0.6: la chiave era 'market_cap' che fast_info.get() NON risolve
        # (misurato: None) -> filtro size MORTO da sempre. Review V0: il mcap arriva
        # dalla .info gia' scaricata da _one_peer (zero HTTP extra); fast_info resta
        # solo come fallback quando la .info non lo espone.
        p_mcap = c.get("_mcap")
        if not p_mcap:
            import yfinance as yf
            try:
                fi = yf.Ticker(p).fast_info
                for _k in ("marketCap", "market_cap"):
                    try:
                        p_mcap = fi[_k]
                    except (KeyError, TypeError, AttributeError):
                        p_mcap = None
                    if p_mcap:
                        break
            except Exception:
                p_mcap = None
        if t_mcap and p_mcap:
            ratio = float(p_mcap) / float(t_mcap)
            if ratio < 0.1 or ratio > 10:
                scartati.append("%s (size %.1fx fuori banda 0.1-10x)" % (p, ratio))
                continue
        else:
            ratio = None
        # FIT 2: margine EBITDA — business model proxy
        p_margin = (c["ebitda"] / c["sales"]) if (c.get("ebitda") and c.get("sales")) else None
        if t_margin is not None and p_margin is not None and abs(p_margin - t_margin) > 0.15:
            scartati.append("%s (margine EBITDA %+.0fpp dal target)" % (p, (p_margin - t_margin) * 100))
            continue
        c["_fit_score"] = round((abs(math.log10(ratio)) if ratio else 0.5)
                                + 3 * (abs(p_margin - t_margin) if (t_margin is not None and p_margin is not None) else 0.1), 3)
        comps.append(c)
    comps.sort(key=lambda x: x.get("_fit_score", 9))
    comps = comps[:max_peers]

    criteri = "size 0.1-10x" + (" + margine EBITDA +/-15pp" if t_margin is not None
                                else " (margine target n.d.: filtro margini NON applicato, dichiarato)")
    nota = ("peer FIT deterministico [%s] — criteri: %s; tenuti %d"
            % (" + ".join(fonte_universo) or "nessun universo", criteri, len(comps)))
    if scartati:
        nota += "; scartati: " + ", ".join(scartati[:8]) + ("..." if len(scartati) > 8 else "")
    if not comps:
        nota += ". NESSUN peer sopravvive al fit: metodo comps n.d. (dichiarato nel foglio)."
    result = (comps, nota)
    if comps:
        _CACHE[key] = (time.time(), result)
    return result


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import yfinance as yf
    from bellomberg.market_data.sector_taxonomy import SUBSECTORS, classify
    tk = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    info = yf.Ticker(tk).info or {}
    prof = classify(info.get("industry") or "", info.get("sector") or "", tk)
    comps, nota = select_peer_comps(tk, info, prof.get("peers"))
    print(json.dumps(comps, indent=2, ensure_ascii=False))
    print(nota)
