# -*- coding: utf-8 -*-
"""
market_inputs.py (#197, rifatto in audit/13 V1.2) — UNICA FONTE LIVE per gli input
di mercato: risk-free 10Y PER VALUTA e ERP implied Damodaran.

Fonti rf (tutte gratuite, esercitate il 16/07/2026 — audit/13 §5):
  USD  FRED DGS10 (daily, plumbing esistente) -> fallback Treasury.gov CSV (daily)
  EUR  Bundesbank API, Bund 10Y Svensson (daily, lag 0 — convenzione desk, ok PM)
  GBP  Bank of England IADB IUDMNZC (zero-coupon 10y, daily)
  JPY  MoF Giappone jgbcme.csv (constant maturity, daily)
  DKK  ECB Data Portal, serie IRS mensile (NON esiste un daily ufficiale gratuito:
       etichettata SEMPRE 'mensile' — ok PM 16/07)
  BRL/CHF/altre: nessuna fonte daily verificata -> statico DICHIARATO (per un titolo brasiliano i
       flussi sono USD: il rf BRL serve solo da riferimento).

Gerarchia e staleness (regola no-fallback-silenziosi, 14/07 — soglie ok PM 16/07):
  live (<=3 gg lavorativi OK; >3 STALE dichiarato) -> last-known-good su disco con
  LA SUA data (>10 gg lavorativi il live e' KO) -> statico versionato dallo snapshot
  Damodaran (rinfrescato da tools/ops/aggiorna_damodaran.py) -> statico hardcoded
  (ultima rete, etichettato 'static hardcoded'). La FONTE VERA esce sempre in
  get_risk_free_ex(): mai piu' 'FRED live' su un valore di tabella.

get_risk_free(cur) resta SCALARE (consumatori esterni: advanced_metrics, twr_engine
— invariati per contratto, audit/13 review MEDIA-3). Il motore usa get_risk_free_ex().
"""
import io
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from bellomberg.core.paths import DATA_DIR

_LKG_PATH = str(DATA_DIR / "rf_cache.json")   # last-known-good su disco

# ultima rete hardcoded (etichettata 'static hardcoded (tabella modulo)')
_RF_FALLBACK = {"USD": 0.045, "EUR": 0.031, "GBP": 0.042, "JPY": 0.011,
                "CHF": 0.010, "CNY": 0.025, "BRL": 0.105, "INR": 0.070}
_ERP_DEFAULT = 0.05      # ultima rete ERP se lo snapshot Damodaran e' illeggibile
_TTL = 12 * 3600
_CACHE = {}
_UA = {"User-Agent": "Mozilla/5.0 (Bellomberg; uso personale)"}
_STALE_BDAYS = 3         # oltre -> STALE dichiarato
_KO_BDAYS = 10           # oltre -> il live non vale piu' come 'live'
_STALE_MONTHLY_D = 45    # serie mensili (DKK): giorni di calendario
_KO_MONTHLY_D = 90


def _bdays_between(d0: date, d1: date) -> int:
    """Giorni LAVORATIVI tra d0 e d1 (esclusi weekend; festivi ignorati: prudente)."""
    if d0 >= d1:
        return 0
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _parse_iso(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------- fetcher live
def _fetch_usd():
    """FRED DGS10 (plumbing esistente); fallback dichiarato Treasury.gov (piu' fresco di ~1g)."""
    try:
        from bellomberg.agents.agent_tools import _fred_fetch_series
        r = _fred_fetch_series("DGS10", last_n=6)
        obs = [o for o in ((r or {}).get("observations") or []) if o.get("value") is not None]
        if obs:
            return float(obs[-1]["value"]) / 100.0, str(obs[-1].get("date"))[:10], "FRED DGS10 (daily)", "daily"
    except Exception:
        pass
    try:  # fonte primaria del medesimo dato (pubblico dominio)
        import requests
        y = date.today().year
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               "daily-treasury-rates.csv/%d/all?type=daily_treasury_yield_curve"
               "&field_tdr_date_value=%d&page&_format=csv" % (y, y))
        t = requests.get(url, headers=_UA, timeout=30).text
        rows = [ln.split(",") for ln in t.splitlines() if ln.strip()]
        hdr = [h.strip().strip('"') for h in rows[0]]
        j = hdr.index("10 Yr")
        d, v = rows[1][0].strip('"'), float(rows[1][j])
        dd = datetime.strptime(d, "%m/%d/%Y").date().isoformat()
        return v / 100.0, dd, "Treasury.gov 10Yr (daily, fallback dichiarato di FRED)", "daily"
    except Exception:
        return None


def _fetch_eur():
    """Bundesbank: Bund 10Y daily (Svensson). CSV con VIRGOLA decimale, lag 0."""
    try:
        import requests
        url = ("https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
               "D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
               "?lastNObservations=8&format=csv")
        t = requests.get(url, headers=_UA, timeout=30).text
        best = None
        for ln in t.splitlines():
            cells = [c.strip().strip('"') for c in ln.split(";")]
            if len(cells) < 2:
                cells = [c.strip().strip('"') for c in ln.split(",")]
            d = _parse_iso(cells[0]) if cells else None
            if not d:
                continue
            for c in cells[1:3]:
                try:
                    v = float(c.replace(",", "."))
                except ValueError:
                    continue
                if -2.0 < v < 30.0:
                    if best is None or d > best[1]:
                        best = (v, d)
                    break
        if best:
            return best[0] / 100.0, best[1].isoformat(), "Bundesbank Bund 10Y (daily)", "daily"
    except Exception:
        pass
    return None


def _fetch_gbp():
    """Bank of England IADB, serie IUDMNZC (gilt 10y nominal zero coupon, daily)."""
    try:
        import requests
        d1, d0 = date.today(), date.today() - timedelta(days=21)
        url = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp"
               "?csv.x=yes&Datefrom=%s&Dateto=%s&SeriesCodes=IUDMNZC&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
               % (d0.strftime("%d/%b/%Y"), d1.strftime("%d/%b/%Y")))
        t = requests.get(url, headers=_UA, timeout=30).text
        best = None
        for ln in t.splitlines():
            cells = [c.strip().strip('"') for c in ln.split(",")]
            if len(cells) < 2:
                continue
            try:
                d = datetime.strptime(cells[0], "%d %b %Y").date()
                v = float(cells[1])
            except ValueError:
                continue
            if -2.0 < v < 30.0 and (best is None or d > best[1]):
                best = (v, d)
        if best:
            return best[0] / 100.0, best[1].isoformat(), "BoE IADB IUDMNZC gilt 10y (daily)", "daily"
    except Exception:
        pass
    return None


def _fetch_jpy():
    """MoF Giappone: JGB constant maturity, colonna 10Y (versione inglese, date gregoriane)."""
    try:
        import requests
        url = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
        t = requests.get(url, headers=_UA, timeout=30).text
        rows = [ln.split(",") for ln in t.splitlines() if ln.strip()]
        hdr_i = next(i for i, r in enumerate(rows) if any(c.strip() == "10Y" for c in r))
        j = [c.strip() for c in rows[hdr_i]].index("10Y")
        best = None
        for r in rows[hdr_i + 1:]:
            if len(r) <= j:
                continue
            try:
                d = datetime.strptime(r[0].strip(), "%Y/%m/%d").date()
                v = float(r[j])
            except ValueError:
                continue
            if -2.0 < v < 30.0 and (best is None or d > best[1]):
                best = (v, d)
        if best:
            return best[0] / 100.0, best[1].isoformat(), "MoF Japan JGB 10Y (daily)", "daily"
    except Exception:
        pass
    return None


def _fetch_dkk():
    """ECB Data Portal, 'long-term rate for convergence purposes' DKK — MENSILE
    (nessun daily ufficiale gratuito: dichiarato; 2 mesi piu' fresca della OECD/FRED)."""
    try:
        import requests
        url = ("https://data-api.ecb.europa.eu/service/data/IRS/"
               "M.DK.L.L40.CI.0000.DKK.N.Z?lastNObservations=3&format=csvdata")
        t = requests.get(url, headers=_UA, timeout=30).text
        rows = [ln.split(",") for ln in t.splitlines() if ln.strip()]
        hdr = [h.strip().strip('"') for h in rows[0]]
        ji, jv = hdr.index("TIME_PERIOD"), hdr.index("OBS_VALUE")
        best = None
        for r in rows[1:]:
            try:
                per, v = r[ji].strip('"'), float(r[jv])
            except (ValueError, IndexError):
                continue
            m = re.match(r"^(\d{4})-(\d{2})$", per)
            if m and -2.0 < v < 30.0:
                d = date(int(m.group(1)), int(m.group(2)), 28)  # fine mese ~ conservativo
                if best is None or d > best[1]:
                    best = (v, d, per)
        if best:
            return best[0] / 100.0, best[1].isoformat(), \
                "ECB convergence rate DKK (MENSILE, periodo %s)" % best[2], "monthly"
    except Exception:
        pass
    return None


_FETCHERS = {"USD": _fetch_usd, "EUR": _fetch_eur, "GBP": _fetch_gbp,
             "JPY": _fetch_jpy, "DKK": _fetch_dkk}


# ------------------------------------------------------- last-known-good su disco
def _lkg_load():
    try:
        with io.open(_LKG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _lkg_save(cur, rec):
    try:
        allv = _lkg_load()
        allv[cur] = rec
        os.makedirs(os.path.dirname(_LKG_PATH), exist_ok=True)
        # review B2: scrittura ATOMICA (tmp + os.replace) — backend e run girano
        # insieme e un file troncato a meta' avrebbe azzerato il last-known-good
        tmp = _LKG_PATH + ".tmp.%d" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(allv, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _LKG_PATH)
    except Exception:
        pass  # il LKG e' un aiuto, non un requisito: se il disco fallisce si va avanti


# ------------------------------------------------------------------ API pubblica
def get_risk_free_ex(currency="USD"):
    """Risk-free 10Y per VALUTA con fonte, data osservazione e staleness DICHIARATE.

    Ritorna {'value','source','obs_date','cadence','stale','age','tier'}:
      tier = 'live' | 'lkg' (last-known-good datato) | 'static-snapshot' | 'static-hardcoded'
      stale = True quando il dato viola le soglie della sua cadenza (va MOSTRATO).
    Mai None: l'ultima rete e' la tabella statica, etichettata come tale.
    """
    cur = (currency or "USD").upper()
    c = _cached("rfex:" + cur)
    if c is not None:
        return dict(c)
    today = date.today()
    rec = None
    fetch = _FETCHERS.get(cur)
    if fetch:
        got = fetch()
        if got:
            v, obs, src, cadence = got
            if -0.02 <= v <= 0.30:  # review B3: i tassi negativi (JPY/CHF/Bund storici) sono legittimi
                od = _parse_iso(obs)
                if cadence == "monthly":
                    age = (today - od).days if od else None
                    stale = age is None or age > _STALE_MONTHLY_D
                    dead = age is None or age > _KO_MONTHLY_D
                else:
                    age = _bdays_between(od, today) if od else None
                    stale = age is None or age > _STALE_BDAYS
                    dead = age is None or age > _KO_BDAYS
                if not dead:
                    rec = {"value": v, "source": src, "obs_date": obs, "cadence": cadence,
                           "stale": bool(stale), "age": age, "tier": "live"}
                    _lkg_save(cur, {"value": v, "obs_date": obs, "source": src,
                                    "cadence": cadence})
    if rec is None:
        lkg = _lkg_load().get(cur)
        od = _parse_iso((lkg or {}).get("obs_date"))
        if lkg and od and (today - od).days <= 30:
            age = _bdays_between(od, today)
            rec = {"value": lkg["value"], "obs_date": lkg["obs_date"],
                   "source": "last-known-good del %s (%s)" % (lkg["obs_date"], lkg.get("source")),
                   "cadence": lkg.get("cadence"), "stale": True, "age": age, "tier": "lkg"}
    if rec is None:
        try:
            from bellomberg.market_data.damodaran_data import get_rf_static
            st = get_rf_static(cur)
        except Exception:
            st = None
        if st:
            rec = {"value": st["value"], "obs_date": st.get("asof"), "source": st["source"],
                   "cadence": "static", "stale": True, "age": None, "tier": "static-snapshot"}
    if rec is None:
        rec = {"value": _RF_FALLBACK.get(cur, 0.04), "obs_date": None,
               "source": "static hardcoded (tabella modulo — valuta senza fonte live: "
                         "dato di riserva, NON di mercato)",
               "cadence": "static", "stale": True, "age": None, "tier": "static-hardcoded"}
    _CACHE["rfex:" + cur] = (dict(rec), time.time())
    return rec


def get_risk_free(currency="USD"):
    """SCALARE retro-compatibile (advanced_metrics, twr_engine). Il motore di
    valutazione usa get_risk_free_ex() per avere fonte e staleness."""
    return get_risk_free_ex(currency)["value"]


def get_erp_ex():
    """ERP implied Damodaran con versione dichiarata (snapshot committato, V1.1).
    Ultima rete: 5% etichettato 'default hardcoded' se lo snapshot e' illeggibile."""
    try:
        from bellomberg.market_data.damodaran_data import get_erp as _dd_erp
        e = _dd_erp()
        return {"value": e["value"], "source": e["source"], "asof": e.get("asof"),
                "stale": e.get("stale", False)}
    except Exception:
        return {"value": _ERP_DEFAULT, "source": "default hardcoded 5% (snapshot "
                "Damodaran illeggibile: dichiarato)", "asof": None, "stale": True}


def get_erp(market="US"):
    """SCALARE retro-compatibile. ERP implied dallo snapshot Damodaran."""
    return get_erp_ex()["value"]


def _cached(key):
    v = _CACHE.get(key)
    if v and (time.time() - v[1]) < _TTL:
        return v[0]
    return None


def snapshot():
    """Per debug/log: i tassi live correnti con fonte e staleness."""
    out = {}
    for cur in ("USD", "EUR", "GBP", "JPY", "DKK"):
        r = get_risk_free_ex(cur)
        out["rf_" + cur] = {"value": r["value"], "src": r["source"],
                            "obs": r["obs_date"], "stale": r["stale"], "tier": r["tier"]}
    out["erp"] = get_erp_ex()
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
