"""macro_rates.py — dati REALI di curve dei rendimenti / spread / credito per
il memo e il quant appendix. Fonti verificate col workflow multi-agente del
15/07/2026 (Opus 4.8 — da rivedere con Fable 5).

REGOLA INVALICABILE (PM 14/07, no-fallback-silenziosi): ogni numero viene da
una fonte REALE ed e' etichettato con {value, date, src}. Se una fonte manca,
e' irraggiungibile o il dato e' stale, il modulo lo DICHIARA (campo `status`)
e NON emette un numero di ripiego. I proxy vanno in chiave separata con `label`.

Fonti (verificate):
- US Treasury CMT   -> FRED DGSx (riusa _fred_fetch_series, key gia in .env)  [SOLIDA]
- JGB Giappone      -> MOF Japan CSV jgbcme (free, no key)                    [SOLIDA]
- Bund Germania     -> Deutsche Bundesbank SDMX REST (BBSIS/BBSSY, free)      [SOLIDA]
- EU HY credit      -> FRED BAMLHE00EHYIOAS (ICE BofA EUR HY OAS)  [PROXY di iTraxx XO]
- OAT Francia 10Y   -> Banque de France Webstat TEC10 (free, serve client_id) [MEDIA]
- BTP Italia curva  -> nessuna fonte free daily -> S&P Global connector       [GAP]
- Spread BTP/OAT-Bund -> calcolati dai 10Y (OAT-Bund free; BTP-Bund gap)
"""
import io
import csv
import datetime
import requests

from bellomberg.agents.agent_tools import _fred_fetch_series, _native_cached, _NATIVE_UA

# ============================================================
# CONFIG — identificatori VERIFICATI (configurazione, NON dati)
# ============================================================
FRED_UST_CURVE = {
    "1M": "DGS1MO", "3M": "DGS3MO", "6M": "DGS6MO", "1Y": "DGS1", "2Y": "DGS2",
    "3Y": "DGS3", "5Y": "DGS5", "7Y": "DGS7", "10Y": "DGS10", "20Y": "DGS20", "30Y": "DGS30",
}

BUND_BASE = "https://api.statistiken.bundesbank.de/rest/data"
BUND_FLOW = "BBSIS"
# curva Svensson su titoli federali quotati; pattern maturita' R{nn}XX = nn anni
# (2Y/10Y/30Y verificati diretti; gli altri per pattern -> se un id e' errato,
#  il tenor viene DICHIARATO come gap, mai riempito)
_BUND_KEY = "D.I.ZST.ZI.EUR.S1311.B.A604.R{code}XX.R.A.A._Z._Z.A"
BUND_CURVE = {t: _BUND_KEY.format(code="%02d" % y)
              for t, y in [("1Y", 1), ("2Y", 2), ("3Y", 3), ("5Y", 5), ("7Y", 7),
                           ("10Y", 10), ("15Y", 15), ("20Y", 20), ("30Y", 30)]}
BUND_FLOW_10Y = "BBSSY"                       # 10Y on-the-run (gamba spread)
BUND_KEY_10Y = "D.REN.EUR.A630.000000WT1010.A"

MOF_JGB_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
MOF_JGB_HIST_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
MOF_JGB_TENORS = ["1Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y", "9Y",
                  "10Y", "15Y", "20Y", "25Y", "30Y", "40Y"]

WEBSTAT_BASE = "https://api.webstat.banque-france.fr/webstat-fr/v1/data"
WEBSTAT_FLOW = "FM"
WEBSTAT_TEC10 = "FM.D.FR.EUR.FR2.BB.FRMOYTEC10.HSTA"   # TEC10 constant-maturity, daily

FRED_10Y_MONTHLY = {"IT": "IRLTLT01ITM156N", "FR": "IRLTLT01FRM156N", "DE": "IRLTLT01DEM156N"}
FRED_EU_HY_OAS = "BAMLHE00EHYIOAS"            # ICE BofA Euro HY OAS (proxy iTraxx XO)

MAX_STALE_DAILY = 5      # giorni di calendario (weekend+festivo+lag T+1)
MAX_STALE_MONTHLY = 45


# ============================================================
# ENVELOPE + helper (gap policy)
# ============================================================
def _envelope(series_key, source, status, as_of=None, points=None, gaps=None,
              label=None, error=None, extra=None):
    """Struttura di ritorno comune. status in
    {solid, proxy_labeled, needs_connector, declared_gap, stale, error}."""
    out = {"series_key": series_key, "source": source, "status": status,
           "as_of": as_of, "points": points or [], "gaps": gaps or []}
    if label:
        out["label"] = label
    if error:
        out["error"] = error
    if extra:
        out.update(extra)
    return out


def _today():
    return datetime.date.today()


def _norm_date(s):
    """Normalizza 'YYYY/M/D' o 'YYYY-MM-DD' -> date. None se non parsabile."""
    s = (s or "").strip()
    for sep, fmt in (("/", "%Y/%m/%d"), ("-", "%Y-%m-%d")):
        if sep in s:
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                # gestisci componenti non zero-paddate (2026/7/1)
                try:
                    y, m, d = [int(x) for x in s.replace("-", "/").split("/")]
                    return datetime.date(y, m, d)
                except Exception:
                    return None
    return None


def _iso(d):
    return d.isoformat() if isinstance(d, datetime.date) else str(d)


def _is_stale(date_str, max_days):
    d = _norm_date(date_str)
    if d is None:
        return True
    return (_today() - d).days > max_days


# ============================================================
# PARSER PURI (testabili su fixture, separati dall'HTTP)
# ============================================================
def _parse_bundesbank_csv(text):
    """SDMX-CSV Bundesbank -> lista [{date(ISO), value}] ordinata. Salta i
    non-trading day ('No value available'). Robusto al layout: per ogni riga
    prende la prima cella-data e l'ULTIMA cella numerica."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        d = None
        for cell in row:
            d = _norm_date(cell)
            if d is not None:
                break
        if d is None:
            continue  # header/metadati
        val = None
        for cell in reversed(row):
            c = (cell or "").strip()
            if not c or "no value" in c.lower():
                continue
            try:
                val = float(c.replace(",", "."))
                break
            except ValueError:
                continue
        if val is not None:
            out.append({"date": _iso(d), "value": val})
    out.sort(key=lambda x: x["date"])
    return out


def _parse_mof_jgb_csv(text, tenors=MOF_JGB_TENORS):
    """CSV MOF jgbcme -> lista di righe [{date(ISO), yields:{tenor:value}}].
    Salta righe titolo/label; mappa colonne per POSIZIONE dopo la data.
    Celle vuote/non numeriche = tenor OMESSO (gap dichiarato a valle)."""
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        d = _norm_date(row[0])
        if d is None:
            continue  # titolo o riga label
        yields = {}
        for tenor, cell in zip(tenors, row[1:]):
            c = (cell or "").strip()
            try:
                yields[tenor] = float(c)
            except ValueError:
                continue  # cella vuota / n.d. -> non riempita
        if yields:
            rows.append({"date": _iso(d), "yields": yields})
    rows.sort(key=lambda x: x["date"])
    return rows


def _parse_webstat_json(obj):
    """SDMX-JSON Banque de France Webstat -> lista [{date, value}] best-effort.
    La struttura esatta va confermata alla prima chiamata LIVE con la key."""
    out = []
    try:
        # struttura SDMX-JSON: dataSets[0].series[key].observations {idx: [val]}
        ds = (obj.get("dataSets") or [{}])[0]
        series = ds.get("series") or {}
        struct = obj.get("structure") or {}
        dims = (((struct.get("dimensions") or {}).get("observation")) or [{}])[0]
        times = [v.get("id") for v in (dims.get("values") or [])]
        for _skey, sval in series.items():
            for idx, arr in (sval.get("observations") or {}).items():
                try:
                    i = int(idx)
                    dt = times[i] if i < len(times) else None
                    v = float(arr[0])
                    if dt:
                        out.append({"date": dt, "value": v})
                except (ValueError, IndexError, TypeError):
                    continue
            break
    except Exception:
        return []
    out.sort(key=lambda x: x["date"])
    return out


# ============================================================
# FETCHER (HTTP -> parser), tutti cachati e senza cache degli errori
# ============================================================
def _bundesbank_series(flow, key, start=None):
    def _fn():
        url = BUND_BASE + "/" + flow + "/" + key
        params = {"format": "csv", "lang": "en"}
        if start:
            params["startPeriod"] = start
        r = requests.get(url, params=params, headers=_NATIVE_UA, timeout=20)
        r.raise_for_status()
        return {"points": _parse_bundesbank_csv(r.text)}
    return _native_cached("bbk:%s:%s:%s" % (flow, key, start or ""), _fn)


def _mof_jgb(historical=False):
    url = MOF_JGB_HIST_URL if historical else MOF_JGB_URL

    def _fn():
        r = requests.get(url, headers=_NATIVE_UA, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return {"rows": _parse_mof_jgb_csv(r.text)}
    return _native_cached("mof_jgb:%s" % ("hist" if historical else "cur"), _fn)


def _webstat_series(key, client_id, start=None):
    if not client_id:
        return {"error": "Webstat client_id assente (registrare key gratuita su developer.webstat.banque-france.fr)"}

    def _fn():
        url = WEBSTAT_BASE + "/" + WEBSTAT_FLOW + "/" + key
        params = {"format": "json", "client_id": client_id}
        if start:
            params["startPeriod"] = start
        r = requests.get(url, params=params, headers=_NATIVE_UA, timeout=20)
        r.raise_for_status()
        return {"points": _parse_webstat_json(r.json())}
    return _native_cached("webstat:%s:%s" % (key, start or ""), _fn)


# ============================================================
# API PUBBLICA
# ============================================================
def _pick_prev(points, latest_date, days=30):
    """Punto piu' vicino a (latest_date - days). points: [{date, value|...}]."""
    ld = _norm_date(latest_date)
    if ld is None:
        return None
    target = ld - datetime.timedelta(days=days)
    best, bestdiff = None, None
    for o in points:
        d = _norm_date(o.get("date"))
        if d is None or d >= ld:
            continue
        diff = abs((d - target).days)
        if bestdiff is None or diff < bestdiff:
            best, bestdiff = o, diff
    return best


def _add_history(point, series, latest_date):
    """Aggiunge al punto i valori ~1 mese fa e ~1 anno fa (per il confronto m/m e y/y).
    series: lista [{date, value}] o [{date, yields}]; per le curve multi-tenor il
    chiamante passa gia' la serie del singolo tenor."""
    p1m = _pick_prev(series, latest_date, 30)
    p1y = _pick_prev(series, latest_date, 365)
    if p1m and p1m.get("value") is not None:
        point["value_1m"], point["date_1m"] = p1m["value"], p1m["date"]
    if p1y and p1y.get("value") is not None:
        point["value_1y"], point["date_1y"] = p1y["value"], p1y["date"]
    return point


def _fred_curve(tenor_map, last_n=400):
    """Curva FRED per tenor: valore odierno + ~1 mese fa + ~1 anno fa.
    last_n=400 osservazioni giornaliere (~1,5 anni) per coprire il confronto y/y."""
    pts, gaps, as_of = [], [], None
    for tenor, sid in tenor_map.items():
        r = _fred_fetch_series(sid, last_n)
        if not isinstance(r, dict) or r.get("error") or not r.get("observations"):
            reason = r.get("error") if isinstance(r, dict) else "no data"
            gaps.append({"tenor": tenor, "reason": reason or "nessuna osservazione"})
            continue
        obs = r["observations"]
        o = obs[-1]
        p = {"tenor": tenor, "value": o["value"], "date": o["date"], "src": "FRED:" + sid}
        _add_history(p, obs, o["date"])
        pts.append(p)
        if as_of is None or o["date"] > as_of:
            as_of = o["date"]
    return pts, gaps, as_of


def get_us_curve(last_n=400):
    src = "FRED Treasury CMT (Fed H.15)"
    pts, gaps, as_of = _fred_curve(FRED_UST_CURVE, last_n)
    if not pts:
        return _envelope("us_curve", src, "error", gaps=gaps,
                         error="nessun tenor US disponibile da FRED")
    status = "stale" if _is_stale(as_of, MAX_STALE_DAILY) else "solid"
    return _envelope("us_curve", src, status, as_of=as_of, points=pts, gaps=gaps)


def get_bund_curve(start=None):
    src = "Deutsche Bundesbank BBSIS (Svensson term structure)"
    if start is None:  # ~14 mesi per coprire il confronto y/y
        start = (datetime.date.today() - datetime.timedelta(days=430)).isoformat()
    pts, gaps, as_of = [], [], None
    for tenor, key in BUND_CURVE.items():
        r = _bundesbank_series(BUND_FLOW, key, start)
        series = (r or {}).get("points") if isinstance(r, dict) else None
        if not series:
            gaps.append({"tenor": tenor, "reason": (r or {}).get("error", "nessun dato")})
            continue
        o = series[-1]
        p = {"tenor": tenor, "value": o["value"], "date": o["date"], "src": "Bundesbank:" + key}
        _add_history(p, series, o["date"])
        pts.append(p)
        if as_of is None or o["date"] > as_of:
            as_of = o["date"]
    if not pts:
        return _envelope("de_bund_curve", src, "error", gaps=gaps, error="Bundesbank irraggiungibile")
    status = "stale" if _is_stale(as_of, MAX_STALE_DAILY) else "solid"
    return _envelope("de_bund_curve", src, status, as_of=as_of, points=pts, gaps=gaps,
                     label="Svensson term-structure (yield modellati, non on-the-run)")


def get_jgb_curve(historical=False):
    src = "MOF Japan JGB reference rates (JSDA OTC)"
    # merge: file anno-corrente (FRESCO, aggiornato T+1) + storico (per il y/y).
    # il current vince sugli overlap -> today sempre dal file fresco.
    cur = _mof_jgb(False)
    hist = _mof_jgb(True)
    rows_cur = (cur or {}).get("rows") if isinstance(cur, dict) else None
    rows_hist = (hist or {}).get("rows") if isinstance(hist, dict) else None
    merged = {}
    for r in (rows_hist or []):
        merged[r["date"]] = r["yields"]
    for r in (rows_cur or []):
        merged[r["date"]] = r["yields"]
    if not merged:
        err = (cur or {}).get("error") or (hist or {}).get("error") or "MOF CSV vuoto/irraggiungibile"
        return _envelope("jp_jgb_curve", src, "error", error=err)
    rows = [{"date": d, "yields": y} for d, y in sorted(merged.items())]
    last = rows[-1]
    as_of = last["date"]
    # trim a ~15 mesi: basta per m/m + y/y, evita di ciclare 50 anni di storico
    ld = _norm_date(as_of)
    if ld:
        cutoff = (ld - datetime.timedelta(days=460)).isoformat()
        rows = [r for r in rows if r["date"] >= cutoff]
    pts = []
    for t, v in last["yields"].items():
        p = {"tenor": t, "value": v, "date": as_of, "src": "MOF:jgbcme"}
        series_t = [{"date": r["date"], "value": r["yields"][t]} for r in rows if t in r["yields"]]
        _add_history(p, series_t, as_of)
        pts.append(p)
    gaps = [{"tenor": t, "reason": "cella vuota"} for t in MOF_JGB_TENORS if t not in last["yields"]]
    status = "stale" if _is_stale(as_of, MAX_STALE_DAILY) else "solid"
    return _envelope("jp_jgb_curve", src, status, as_of=as_of, points=pts, gaps=gaps)


def get_fr_oat_10y(client_id=None, start=None):
    src = "Banque de France Webstat TEC10"
    r = _webstat_series(WEBSTAT_TEC10, client_id, start)
    pts = (r or {}).get("points") if isinstance(r, dict) else None
    if not pts:
        status = "declared_gap" if not client_id else "error"
        return _envelope("fr_oat_10y", src, status,
                         error=(r or {}).get("error", "nessun dato Webstat"),
                         label="TEC10 constant-maturity")
    o = pts[-1]
    status = "stale" if _is_stale(o["date"], MAX_STALE_DAILY) else "solid"
    return _envelope("fr_oat_10y", src, status, as_of=o["date"],
                     points=[{"tenor": "10Y", "value": o["value"], "date": o["date"], "src": "Webstat:TEC10"}],
                     label="TEC10 constant-maturity (non OAT on-the-run)")


def get_it_btp_10y():
    """Solo ancora 10Y MENSILE FRED, etichettata proxy (NON daily, NON curva)."""
    sid = FRED_10Y_MONTHLY["IT"]
    r = _fred_fetch_series(sid, 4)
    if not isinstance(r, dict) or r.get("error") or not r.get("observations"):
        return _envelope("it_btp_10y", "FRED " + sid, "error",
                         error=(r.get("error") if isinstance(r, dict) else "no data"))
    o = r["observations"][-1]
    status = "stale" if _is_stale(o["date"], MAX_STALE_MONTHLY) else "proxy_labeled"
    return _envelope("it_btp_10y", "FRED " + sid + " (OECD, mensile)", status, as_of=o["date"],
                     points=[{"tenor": "10Y", "value": o["value"], "date": o["date"], "src": "FRED:" + sid}],
                     label="PROXY: Italia 10Y OECD MENSILE (non BTP benchmark daily)")


def get_it_btp_curve():
    """Nessuna fonte free autorevole per la curva BTP 1-30Y giornaliera."""
    return _envelope(
        "it_btp_curve", "S&P Global connector (non autorizzato) / nessuna fonte free daily",
        "needs_connector",
        error="Curva BTP 1-30Y daily senza fonte free autorevole: autorizzare il "
              "connettore S&P Global su claude.ai, oppure usare get_it_btp_10y() "
              "(proxy 10Y mensile FRED, etichettato).")


def get_eu_hy_credit(last_n=260):
    src = "FRED " + FRED_EU_HY_OAS + " (ICE BofA Euro HY OAS)"
    r = _fred_fetch_series(FRED_EU_HY_OAS, last_n)
    if not isinstance(r, dict) or r.get("error") or not r.get("observations"):
        return _envelope("eu_hy_credit", src, "error",
                         error=(r.get("error") if isinstance(r, dict) else "no data"))
    obs = r["observations"]
    as_of = obs[-1]["date"]
    pts = [{"tenor": None, "value": o["value"], "date": o["date"], "src": "FRED:" + FRED_EU_HY_OAS} for o in obs]
    status = "stale" if _is_stale(as_of, MAX_STALE_DAILY) else "proxy_labeled"
    return _envelope("eu_hy_credit", src, status, as_of=as_of, points=pts,
                     label="PROXY: ICE BofA EUR HY OAS ~ iTraxx Crossover (strumento diverso: OAS bond cash, non CDS)")


def get_sov_spreads(client_id=None):
    """BTP-Bund e OAT-Bund come differenza dei 10Y benchmark. MAI uno spread
    se una gamba manca o e' stale: si dichiara quale gamba e' mancata."""
    out = {"series_key": "sov_spreads", "spreads": {}, "legs": {}}
    # gamba Bund 10Y on-the-run (BBSSY)
    bund = _bundesbank_series(BUND_FLOW_10Y, BUND_KEY_10Y)
    bpts = (bund or {}).get("points") if isinstance(bund, dict) else None
    bund_val = bund_date = None
    if bpts and not _is_stale(bpts[-1]["date"], MAX_STALE_DAILY):
        bund_val, bund_date = bpts[-1]["value"], bpts[-1]["date"]
    out["legs"]["bund_10y"] = {"value": bund_val, "date": bund_date, "src": "Bundesbank:BBSSY"}
    # OAT-Bund: gamba FR (Webstat TEC10) free
    fr = get_fr_oat_10y(client_id)
    if bund_val is not None and fr["status"] in ("solid",) and fr["points"]:
        oat = fr["points"][-1]["value"]
        out["spreads"]["OAT-Bund"] = {"value_bps": round((oat - bund_val) * 100, 1),
                                      "date": min(bund_date, fr["as_of"]),
                                      "src": "TEC10 - Bundesbank 10Y", "status": "solid"}
    else:
        out["spreads"]["OAT-Bund"] = {"status": "declared_gap",
                                      "reason": "gamba Bund o FR mancante/stale (FR richiede client_id Webstat)"}
    # BTP-Bund: gamba IT 10Y daily benchmark ASSENTE (free)
    out["spreads"]["BTP-Bund"] = {"status": "needs_connector",
                                  "reason": "gamba BTP 10Y daily benchmark senza fonte free: "
                                            "autorizzare S&P Global (o proxy Investing.com etichettato)"}
    return out


def get_all_rates(client_id=None):
    """Aggrega tutte le serie, ognuna col proprio status dichiarato."""
    return {
        "us_curve": get_us_curve(),
        "de_bund_curve": get_bund_curve(),
        "jp_jgb_curve": get_jgb_curve(),
        "fr_oat_10y": get_fr_oat_10y(client_id),
        "it_btp_10y": get_it_btp_10y(),
        "it_btp_curve": get_it_btp_curve(),
        "eu_hy_credit": get_eu_hy_credit(),
        "sov_spreads": get_sov_spreads(client_id),
    }


if __name__ == "__main__":
    import json
    # NB: richiede rete + FRED_API_KEY nel .env. client_id Webstat via argv opzionale.
    import sys
    cid = sys.argv[1] if len(sys.argv) > 1 else None
    data = get_all_rates(cid)
    for k, v in data.items():
        if k == "sov_spreads":
            print("[%-14s] spreads=%s" % (k, v["spreads"]))
            continue
        st = v.get("status")
        n = len(v.get("points", []))
        asof = v.get("as_of")
        lab = (" | " + v["label"]) if v.get("label") else ""
        err = (" | ERR: " + v["error"]) if v.get("error") else ""
        print("[%-14s] status=%-14s punti=%-2d as_of=%s%s%s" % (k, st, n, asof, lab, err))
