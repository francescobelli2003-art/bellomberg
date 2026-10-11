# -*- coding: utf-8 -*-
"""calibra_soglie.py — calibrazione RIPRODUCIBILE delle soglie degli score del memo
(10/10/2026, Opus 5.5, delega PM «decidi tu come un quant»).

Scarica SOLO fonti pubbliche gratuite, calcola i percentili e stampa le costanti da
congelare in `src/bellomberg/core/soglie_score.py`. Il codice NON legge questo script a
run-time: le costanti si congelano a mano con data, fonte, finestra e percentile a commento.
Rilanciare lo script e confrontare e' la verifica (le serie si allungano: i numeri si
muovono di poco; una deriva grande e' un segnale da portare al PM, non da assorbire zitti).

Fonti (nessuna chiave, nessun dato del portafoglio):
  - CBOE  VIX_History.csv, VIX3M_History.csv (chiusure giornaliere; VIX3M dal 2009-09-18
          nel file pubblico CBOE: la serie VXV 2007-2009 non c'e')
  - FRED  VIXCLS (dal 1990), SP500 (FRED ne espone 10 anni), BAMLH0A0HYM2 e BAMLC0A0CM
          (ICE: FRED ne espone SOLO gli ultimi 3 anni per licenza -> calibrazione NON
          eseguibile, lo script lo DICHIARA invece di calibrare su una finestra corta)
  - Hyperliquid API pubblica: fundingHistory (BTC, ETH, SOL) + metaAndAssetCtxs (OI)

I file scaricati restano in una cache GITIGNORED (default <repo>/.cache/calibra_soglie):
mai committati. --refresh li riscarica.

USO (dalla radice del repo):
  python tools/ops/calibra_soglie.py                 <- scarica (o usa la cache) e stampa
  python tools/ops/calibra_soglie.py --refresh       <- riscarica tutto
  python tools/ops/calibra_soglie.py --json out.json <- anche su file
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DEFAULT = os.path.join(ROOT, ".cache", "calibra_soglie")
# User-Agent di default di urllib («Python-urllib/3.x»): FRED chiude o lascia scadere la
# connessione con un «Mozilla/5.0» e con un UA inventato (misurato 10/10: RemoteDisconnected
# e timeout a 60 s), risponde 200 in <0,1 s al client Python dichiarato
UA = {}

CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{}_History.csv"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
HL = "https://api.hyperliquid.xyz/info"

# Tasso base Hyperliquid annualizzato (0,125 bp/h x 24 x 365): lo zero dello scarto,
# identico a specialist_scores._HL_TASSO_BASE_ANN (regola della piattaforma).
HL_BASE_ANN_PCT = 10.95
RV_FINESTRA = 21          # sedute: la finestra di vol_surface.RV_WINDOW_SESSIONS
ANNUALIZZA = 252 ** 0.5   # come vol_surface (pct_change, std campionaria, x sqrt 252)
MAJOR = ("BTC", "ETH", "SOL")
OBBLIGATORI = ("BTC", "ETH")


# ------------------------------------------------------------------ funzioni PURE (testate)
def percentile(valori, q):
    """Percentile q (0-100) con interpolazione lineare fra i ranghi (metodo «linear» di
    numpy, tipo 7 di Hyndman-Fan). Lista vuota o q fuori [0,100] -> ValueError."""
    xs = sorted(float(v) for v in valori)
    if not xs:
        raise ValueError("serie vuota")
    if not 0 <= q <= 100:
        raise ValueError("q fuori da [0, 100]")
    h = (len(xs) - 1) * q / 100.0
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (h - lo)


def leggi_cboe(testo):
    """{date: close} dal CSV CBOE (DATE MM/DD/YYYY, CLOSE)."""
    out = {}
    for r in csv.DictReader(io.StringIO(testo)):
        try:
            d = datetime.strptime(r["DATE"].strip(), "%m/%d/%Y").date()
            v = float(r["CLOSE"])
        except (KeyError, ValueError, AttributeError):
            continue
        if math.isfinite(v) and v > 0:
            out[d] = v
    return out


def leggi_fred(testo):
    """{date: valore} dal fredgraph.csv; '.'/vuoti (festivi) saltati."""
    out = {}
    rd = csv.reader(io.StringIO(testo))
    next(rd, None)
    for r in rd:
        if len(r) < 2:
            continue
        try:
            d = date.fromisoformat(r[0].strip())
            v = float(r[1])
        except ValueError:
            continue
        if math.isfinite(v):
            out[d] = v
    return out


def rapporti_allineati(num, den):
    """[(data, num/den)] sulle sole date presenti in entrambe le serie (mai un rapporto misto)."""
    return [(d, num[d] / den[d]) for d in sorted(set(num) & set(den)) if den[d] > 0]


def realizzata_21(chiusure):
    """{data: vol realizzata annualizzata in %} come vol_surface: rendimenti semplici,
    deviazione standard CAMPIONARIA (ddof=1) su 21 sedute, x sqrt(252)."""
    ds = sorted(chiusure)
    rend = [(ds[i], chiusure[ds[i]] / chiusure[ds[i - 1]] - 1.0) for i in range(1, len(ds))]
    out = {}
    for i in range(RV_FINESTRA - 1, len(rend)):
        fin = [r for _, r in rend[i - RV_FINESTRA + 1:i + 1]]
        m = sum(fin) / len(fin)
        var = sum((x - m) ** 2 for x in fin) / (len(fin) - 1)
        out[rend[i][0]] = math.sqrt(var) * ANNUALIZZA * 100.0
    return out


def funding_pesato(per_coin, pesi, base=HL_BASE_ANN_PCT):
    """Scarti |funding pesato - base| (punti %/anno) per ora, con le stesse regole dello
    score: BTC ed ETH obbligatori, SOL se presente; pesi fissi (l'OI storico non e'
    esposto dall'API). per_coin = {coin: {ora_ms: tasso_orario}}."""
    ore = set.intersection(*(set(per_coin.get(c, {})) for c in OBBLIGATORI))
    out = []
    for h in sorted(ore):
        num = den = 0.0
        for c in MAJOR:
            r = per_coin.get(c, {}).get(h)
            w = pesi.get(c)
            if r is None or not w:
                continue
            num += r * 24 * 365 * 100 * w
            den += w
        if den > 0:
            out.append(abs(num / den - base))
    return out


# ------------------------------------------------------------------ rete + cache
def _scarica(url, percorso, refresh, corpo=None):
    if os.path.exists(percorso) and not refresh:
        with open(percorso, "rb") as fh:
            return fh.read()
    req = urllib.request.Request(url, data=corpo, headers=dict(UA, **(
        {"Content-Type": "application/json"} if corpo is not None else {})))
    for tentativo in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:   # segue i redirect CBOE (307)
                dati = r.read()
            break
        except (TimeoutError, OSError):
            # FRED a volte non risponde al primo colpo: 3 tentativi, poi l'errore SALE
            if tentativo == 2:
                raise
            time.sleep(5 * (tentativo + 1))
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    tmp = percorso + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(dati)
    os.replace(tmp, percorso)
    return dati


def _hl(cache, refresh, corpo, nome):
    return json.loads(_scarica(HL, os.path.join(cache, nome), refresh,
                               json.dumps(corpo).encode("utf-8")).decode("utf-8"))


def storia_funding(cache, refresh, coin, inizio_ms):
    """{ora_ms: tasso orario} paginando fundingHistory (500 righe per risposta)."""
    out = {}
    t = inizio_ms
    pagina = 0
    while True:
        nome = "hl_funding_%s_%d.json" % (coin, pagina)
        if refresh or not os.path.exists(os.path.join(cache, nome)):
            time.sleep(0.25)   # cortesia verso l'API pubblica (solo quando si scarica)
        righe = _hl(cache, refresh, {"type": "fundingHistory", "coin": coin, "startTime": t},
                    nome)
        if not righe:
            break
        for r in righe:
            out[int(r["time"]) // 3_600_000 * 3_600_000] = float(r["fundingRate"])
        ultimo = max(int(r["time"]) for r in righe)
        if len(righe) < 500 or ultimo <= t:
            break
        t = ultimo + 1
        pagina += 1
    return out


# ------------------------------------------------------------------ calibrazioni
def _riassunto(serie_date, valori, qs):
    return {"n": len(valori), "dal": str(min(serie_date)), "al": str(max(serie_date)),
            "percentili": {str(q): round(percentile(valori, q), 4) for q in qs}}


def calibra(cache, refresh):
    out = {"eseguita_il": date.today().isoformat(), "cache": cache}
    txt = lambda b: b.decode("utf-8-sig")

    # 1) VIX/VIX3M (CBOE): ancore 0/2/4/6 = percentili 50/75/90/97
    vix_c = leggi_cboe(txt(_scarica(CBOE.format("VIX"), os.path.join(cache, "VIX_History.csv"), refresh)))
    v3m_c = leggi_cboe(txt(_scarica(CBOE.format("VIX3M"), os.path.join(cache, "VIX3M_History.csv"), refresh)))
    rr = rapporti_allineati(vix_c, v3m_c)
    out["vix_vix3m"] = _riassunto([d for d, _ in rr], [v for _, v in rr], (50, 75, 90, 97))
    out["vix_vix3m"]["quota_sopra_1"] = round(sum(1 for _, v in rr if v >= 1.0) / len(rr), 4)

    # 2) Macro STRESS (FRED dal 1997): ancore 1/2/3 punti = percentili 60/80/95
    inizio = date(1997, 1, 1)
    macro = {}
    for sid in ("VIXCLS", "BAMLH0A0HYM2", "BAMLC0A0CM"):
        s = {d: v for d, v in leggi_fred(txt(_scarica(FRED.format(sid), os.path.join(cache, sid + ".csv"), refresh))).items()
             if d >= inizio}
        r = _riassunto(list(s), list(s.values()), (50, 60, 80, 95))
        # una finestra che parte anni dopo il 1997 non e' la storia chiesta: si DICHIARA
        r["calibrabile"] = min(s) <= date(1997, 12, 31)
        if not r["calibrabile"]:
            r["motivo"] = "FRED espone la serie solo dal %s (licenza ICE): finestra non rappresentativa" % min(s)
        macro[sid] = r
    out["macro_stress"] = macro

    # 3) Prezzo della protezione: VIX / realizzata 21 sedute dell'S&P 500 (FRED SP500, 10 anni)
    spx = leggi_fred(txt(_scarica(FRED.format("SP500"), os.path.join(cache, "SP500.csv"), refresh)))
    vix_f = leggi_fred(txt(_scarica(FRED.format("VIXCLS"), os.path.join(cache, "VIXCLS.csv"), refresh)))
    rv = realizzata_21(spx)
    vr = rapporti_allineati(vix_f, rv)
    out["vrp_ratio"] = _riassunto([d for d, _ in vr], [v for _, v in vr], (20, 50, 80))

    # 4) Crypto: |funding major pesato - base| sugli ultimi 2 anni (pesi = OI corrente)
    ctx = _hl(cache, refresh, {"type": "metaAndAssetCtxs"}, "hl_meta.json")
    pesi = {}
    for a, c in zip(ctx[0]["universe"], ctx[1]):
        if a.get("name") in MAJOR:
            pesi[a["name"]] = float(c["openInterest"]) * float(c["markPx"])
    inizio_ms = int((datetime.now(timezone.utc) - timedelta(days=730)).timestamp() * 1000)
    # cache per finestra: la paginazione dipende dall'inizio, che si sposta ogni giorno
    sub = os.path.join(cache, "hl_" + date.today().isoformat())
    per_coin = {c: storia_funding(sub, refresh, c, inizio_ms) for c in MAJOR}
    scarti = funding_pesato(per_coin, pesi)
    ore = sorted(set.intersection(*(set(per_coin[c]) for c in OBBLIGATORI)))
    out["crypto_funding"] = {
        "n_ore": len(scarti),
        "dal": datetime.fromtimestamp(ore[0] / 1000, timezone.utc).isoformat()[:16] if ore else None,
        "al": datetime.fromtimestamp(ore[-1] / 1000, timezone.utc).isoformat()[:16] if ore else None,
        "pesi_oi_usd_m": {c: round(w / 1e6, 1) for c, w in pesi.items()},
        "quota_al_base": round(sum(1 for s in scarti if s < 1e-6) / len(scarti), 4) if scarti else None,
        "percentili_abs_scarto": {str(q): round(percentile(scarti, q), 3) for q in (50, 80, 95, 99)} if scarti else None,
        # sensibilita' ai pesi: pesi uguali
        "percentili_pesi_uguali": ({str(q): round(percentile(funding_pesato(per_coin, {c: 1.0 for c in MAJOR}), q), 3)
                                    for q in (50, 80, 95, 99)} if scarti else None),
    }
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=CACHE_DEFAULT)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = calibra(a.cache, a.refresh)
    testo = json.dumps(res, indent=1, ensure_ascii=False)
    print(testo)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(testo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
