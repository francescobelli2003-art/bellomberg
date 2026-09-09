"""
BELLOMBERG - Fama-French Factor Decomposition Engine v2

Modello base: Fama-French 5-factor + Momentum (Carhart-style 6 factor).
Estensione: BTC factor opzionale per asset crypto-correlated.

PRINCIPI ANTI-ALLUCINAZIONE:
1. TUTTI i numeri arrivano da calcolo statistico Python (statsmodels OLS).
2. Source data: Kenneth French Data Library (Dartmouth) - dati ufficiali.
3. Standard errors: Newey-West HAC con lag = floor(4*(n/100)^(2/9)).
4. Significativita': t-stat e p-value calcolati esplicitamente.
5. Sample size minimo: 60 daily observations per regression.
6. Asset NON-US listed sono ESCLUSI: i fattori FF sono costruiti su universo
   US (NYSE/Nasdaq/Amex). Regredire asset EU/EM/HK su questi fattori genera
   alpha spurio. Skipped con motivazione "non-US listing".
7. Asset crypto-correlated (dal negozio privato dei fattori) ricevono BTC come 7° fattore
   per evitare alpha spurio dovuto a esposizione crypto non modellata.

References:
- Fama & French (2015) "A five-factor asset pricing model", JFE 116(1)
- Carhart (1997) "On Persistence in Mutual Fund Performance", JoF 52(1)
- Newey & West (1987) Econometrica 55(3)
"""
import os
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

_IMPORT_ERRORS: Dict[str, str] = {}

try:
    import numpy as np
    import pandas as pd
    NUMPY_OK = True
except Exception as _e:
    NUMPY_OK = False
    _IMPORT_ERRORS["numpy/pandas"] = repr(_e)

try:
    import yfinance as yf
    YF_OK = True
except Exception as _e:
    YF_OK = False
    _IMPORT_ERRORS["yfinance"] = repr(_e)

try:
    import statsmodels.api as sm
    SM_OK = True
except Exception as _e:
    SM_OK = False
    _IMPORT_ERRORS["statsmodels"] = repr(_e)

try:
    import requests, zipfile, io
    REQ_OK = True
except Exception as _e:
    REQ_OK = False
    _IMPORT_ERRORS["requests/zipfile"] = repr(_e)

PDR_OK = REQ_OK  # alias retro-compat

from bellomberg.storage.memory_db import MemoryDB, DB_DIR


CACHE_DIR = os.path.join(DB_DIR, "cache")
FF5_CACHE_FILE = os.path.join(CACHE_DIR, "ff5_daily.csv")
MOM_CACHE_FILE = os.path.join(CACHE_DIR, "mom_daily.csv")
FF5_REFRESH_DAYS = 7

FACTORS_RESULT_CACHE: Dict[str, Any] = {"ts": 0, "key": "", "data": None}
CACHE_TTL_SEC = 3600

MIN_OBSERVATIONS = 60

# Suffissi di exchange NON-US. Asset con questi suffissi sono esclusi
# dalla regressione FF (i fattori sono US-only).
_NON_US_SUFFIXES = (
    ".L",    # London
    ".MI",   # Milan
    ".DE",   # Frankfurt / Xetra
    ".PA",   # Paris (Euronext)
    ".AS",   # Amsterdam
    ".VI",   # Vienna
    ".SW",   # Switzerland
    ".BR",   # Brussels
    ".MC",   # Madrid
    ".LS",   # Lisbon
    ".CO",   # Copenhagen
    ".ST",   # Stockholm
    ".HE",   # Helsinki
    ".OL",   # Oslo
    ".HK",   # Hong Kong
    ".T",    # Tokyo
    ".TW",   # Taiwan
    ".KS",   # Korea (KOSPI)
    ".KQ",   # Korea (KOSDAQ)
    ".SS",   # Shanghai
    ".SZ",   # Shenzhen
    ".NS",   # Mumbai NSE
    ".BO",   # Mumbai BSE
    ".AX",   # Sydney
    ".TO",   # Toronto
    ".V",    # Toronto Venture
    ".SA",   # Sao Paulo
    ".MX",   # Mexico
)

# Ticker US-listed crypto-correlated (tesorerie di token, exchange, miner): ricevono BTC come
# settimo fattore per evitare alpha spurio. La lista e gli override di regione vivono nel negozio
# privato dei fattori (negozi_privati.carica_fattori: data/fattori_portafoglio.json, forma in
# fattori_portafoglio.example.json), riletto a ogni chiamata; assente o illeggibile = nessun
# fattore BTC e nessun override, DICHIARATO nel payload (filters.negozio_fattori).
def _fattori() -> Dict[str, Any]:
    from bellomberg.storage.negozi_privati import carica_fattori
    return carica_fattori(regioni_valide=set(REGIONAL_FF))


def _log(msg: str):
    try:
        print(f"[FACTORS] {msg}", flush=True)
    except OSError:
        pass  # pipe stdout chiusa (es. parent Electron morto): mai uccidere l'endpoint


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_age_days(path: str) -> float:
    if not os.path.exists(path):
        return 1e9
    return (time.time() - os.path.getmtime(path)) / 86400.0


# I simboli senza serie prezzi utile stanno nel NEGOZIO PRIVATO dei prezzi speciali
# (negozi_privati.carica_prezzi_speciali: data/prezzi_speciali.json, forma in
# prezzi_speciali.example.json), riletto A OGNI CHIAMATA. Come per i fattori, qui il
# negozio assente NON ferma il calcolo: il simbolo finirebbe in `skipped` con un motivo
# DIVERSO (dataset/regressione) e quel motivo e' l'unico che arriva in pagina — percio'
# l'origine del negozio si DICHIARA in filters.negozio_prezzi accanto a negozio_fattori.
def prezzi_speciali() -> Dict[str, Any]:
    """L'esito INTERO del negozio: {"prezzi": {...}, "origine": ..., "motivo": ...}."""
    from bellomberg.storage.negozi_privati import carica_prezzi_speciali
    return carica_prezzi_speciali()


def _is_us_listed(ticker: str, salta: frozenset) -> bool:
    """NB (misurato 05/09, lotto 6): oggi NESSUNO la chiama — la regione la decide
    `_region_for_ticker`. Resta col parametro come le sorelle: se torna in uso, il
    negozio glielo passa il chiamante e non c'e' un insieme vuoto di default.
    True se ticker e' quotato su un exchange US (NYSE/Nasdaq/Amex).
    Heuristica: nessun suffisso di exchange = US. Per suffissi esteri = non-US.
    `salta` arriva dal chiamante (letto una volta per giro dal negozio).
    """
    t = (ticker or "").strip().upper()
    for s in _NON_US_SUFFIXES:
        if t.endswith(s):
            return False
    # Tickers crypto custom non sono "US-listed equity"
    if t in salta:
        return False
    return True


def _is_crypto_correlated(ticker: str, fattori: Optional[Dict[str, Any]] = None) -> bool:
    """`fattori`: l'esito di `_fattori()` gia' caricato — un giro su N titoli lo legge UNA volta,
    cosi' il payload descrive lo stesso stato usato per ogni titolo; senza, si rilegge."""
    f = fattori if fattori is not None else _fattori()
    return (ticker or "").strip().upper() in f["fattori"]["crypto_correlati"]


FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"

# --- bugfix #166: dataset FF REGIONALI (Kenneth French Developed daily) ---
_KF_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
# Per ogni regione: lista di URL candidati (il sito K.French ha nomi con
# capitalizzazione incoerente Mom/MOM — proviamo le varianti in ordine).
REGIONAL_FF: Dict[str, Dict[str, Any]] = {
    "us": {
        "label": "US (NYSE/Nasdaq/Amex)",
        "ff5": [FF5_URL],
        "mom": [MOM_URL],
    },
    "europe": {
        "label": "Europe (developed)",
        "ff5": [_KF_BASE + "Europe_5_Factors_Daily_CSV.zip"],
        "mom": [_KF_BASE + "Europe_Mom_Factor_Daily_CSV.zip",
                _KF_BASE + "Europe_MOM_Factor_Daily_CSV.zip"],
    },
    "japan": {
        "label": "Japan",
        "ff5": [_KF_BASE + "Japan_5_Factors_Daily_CSV.zip"],
        "mom": [_KF_BASE + "Japan_Mom_Factor_Daily_CSV.zip",
                _KF_BASE + "Japan_MOM_Factor_Daily_CSV.zip"],
    },
    "asia_pacific": {
        "label": "Asia-Pacific ex Japan (developed)",
        "ff5": [_KF_BASE + "Asia_Pacific_ex_Japan_5_Factors_Daily_CSV.zip"],
        "mom": [_KF_BASE + "Asia_Pacific_ex_Japan_Mom_Factor_Daily_CSV.zip",
                _KF_BASE + "Asia_Pacific_ex_Japan_MOM_Factor_Daily_CSV.zip"],
    },
    "global": {
        "label": "Developed/Global (proxy per EM: FF Emerging daily non esiste)",
        "ff5": [_KF_BASE + "Developed_5_Factors_Daily_CSV.zip",
                _KF_BASE + "Global_5_Factors_Daily_CSV.zip"],
        "mom": [_KF_BASE + "Developed_Mom_Factor_Daily_CSV.zip",
                _KF_BASE + "Global_Mom_Factor_Daily_CSV.zip",
                _KF_BASE + "Developed_MOM_Factor_Daily_CSV.zip"],
    },
}

# Gli override di regione (esposizione ECONOMICA, non listino: veicoli quotati in una piazza
# e investiti in un'altra) stanno nel negozio privato dei fattori, sezione `regioni`.

_EUROPE_SUFFIXES = (".L", ".MI", ".DE", ".PA", ".AS", ".VI", ".SW", ".BR",
                    ".MC", ".LS", ".CO", ".ST", ".HE", ".OL")
_JAPAN_SUFFIXES = (".T",)
_APAC_SUFFIXES = (".HK", ".AX")
_GLOBAL_SUFFIXES = (".TW", ".KS", ".KQ", ".SS", ".SZ", ".NS", ".BO",
                    ".SA", ".MX", ".TO", ".V")


def _region_for_ticker(ticker: str, fattori: Optional[Dict[str, Any]] = None) -> str:
    """Regione fattoriale per un ticker (#166): override economici (dal negozio privato dei
    fattori, `fattori` gia' caricato o riletto), poi suffisso di quotazione."""
    t = (ticker or "").strip().upper()
    regioni = (fattori if fattori is not None else _fattori())["fattori"]["regioni"]
    if t in regioni:
        return regioni[t]
    if t.endswith(_EUROPE_SUFFIXES):
        return "europe"
    if t.endswith(_JAPAN_SUFFIXES):
        return "japan"
    if t.endswith(_APAC_SUFFIXES):
        return "asia_pacific"
    if t.endswith(_GLOBAL_SUFFIXES):
        return "global"
    return "us"


def _fetch_ff_zip_csv(url: str, expected_cols: list) -> pd.DataFrame:
    """Download a Kenneth French zip, extract the CSV, parse it."""
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    csv_name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    raw = z.read(csv_name).decode("utf-8", errors="ignore")

    lines = raw.splitlines()
    header_idx = None
    for i, ln in enumerate(lines):
        cells = [c.strip() for c in ln.split(",")]
        if all(col in cells for col in expected_cols):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError(f"K. French CSV header not found for {expected_cols}")

    data_rows = []
    for ln in lines[header_idx + 1:]:
        if not ln.strip():
            break
        first = ln.split(",")[0].strip()
        if not (len(first) == 8 and first.isdigit()):
            continue
        data_rows.append(ln)

    if not data_rows:
        raise RuntimeError(f"No data rows parsed from {url}")

    df = pd.read_csv(io.StringIO(lines[header_idx] + "\n" + "\n".join(data_rows)))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
    df = df.set_index("Date").sort_index()
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce") / 100.0
    # fix review quant 22/07 (E6): sentinelle missing di K.French (-99.99/-999)
    # — senza filtro entrerebbero nella regressione come rendimenti -99,99% zitti
    df = df.mask(df.le(-0.99))
    return df.dropna(how="all")


def download_ff5_returns(force: bool = False, region: str = "us") -> pd.DataFrame:
    """Download FF 5-factor + Momentum daily per la regione indicata (#166).
    region: us | europe | japan | asia_pacific | global.
    Cache su disco PER REGIONE per FF5_REFRESH_DAYS giorni.
    """
    if not NUMPY_OK:
        raise RuntimeError("numpy/pandas non installati")
    if not REQ_OK:
        raise RuntimeError("requests/zipfile non disponibili")

    _ensure_cache_dir()
    region = (region or "us").lower()
    spec = REGIONAL_FF.get(region) or REGIONAL_FF["us"]
    if region == "us":  # retro-compat coi vecchi file di cache
        ff5_cache, mom_cache = FF5_CACHE_FILE, MOM_CACHE_FILE
    else:
        ff5_cache = os.path.join(CACHE_DIR, f"ff5_{region}_daily.csv")
        mom_cache = os.path.join(CACHE_DIR, f"mom_{region}_daily.csv")

    use_cache = (not force) and _cache_age_days(ff5_cache) < FF5_REFRESH_DAYS and \
                _cache_age_days(mom_cache) < FF5_REFRESH_DAYS

    if use_cache:
        try:
            ff5 = pd.read_csv(ff5_cache, index_col=0, parse_dates=True)
            mom = pd.read_csv(mom_cache, index_col=0, parse_dates=True)
            merged = ff5.join(mom, how="inner")
            _log(f"[{region}] loaded {len(merged)} obs from cache "
                 f"({_cache_age_days(ff5_cache):.1f}d old)")
            return merged
        except Exception as e:
            _log(f"[{region}] cache read failed, redownloading: {e}")

    _log(f"[{region}] downloading FF5 + Momentum from Kenneth French website...")
    ff5 = None
    last_err: Optional[Exception] = None
    for url in spec["ff5"]:
        try:
            ff5 = _fetch_ff_zip_csv(url, expected_cols=["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"])
            break
        except Exception as e:
            last_err = e
    if ff5 is None:
        raise RuntimeError(f"FF5 [{region}] download failed: {last_err}")

    mom_raw = None
    for url in spec["mom"]:
        for header in ("Mom", "WML"):  # i file regionali usano a volte WML
            try:
                mom_raw = _fetch_ff_zip_csv(url, expected_cols=[header])
                if header == "WML":
                    mom_raw = mom_raw.rename(columns={"WML": "Mom"})
                break
            except Exception as e:
                last_err = e
        if mom_raw is not None:
            break
    if mom_raw is None:
        raise RuntimeError(f"Momentum [{region}] download failed: {last_err}")
    if "Mom" not in mom_raw.columns and len(mom_raw.columns) == 1:
        mom_raw.columns = ["Mom"]
    mom = mom_raw[["Mom"]] if "Mom" in mom_raw.columns else mom_raw

    ff5.to_csv(ff5_cache)
    mom.to_csv(mom_cache)

    merged = ff5.join(mom, how="inner")
    _log(f"[{region}] downloaded {len(merged)} daily obs, latest: {merged.index[-1].date()}")
    return merged


_BTC_RETURNS_CACHE: Dict[str, pd.Series] = {}


def _download_btc_returns(period: str = "3y") -> Optional[pd.Series]:
    """Daily BTC-USD returns from yfinance. Cached per period."""
    if period in _BTC_RETURNS_CACHE:
        return _BTC_RETURNS_CACHE[period]
    try:
        raw = yf.download("BTC-USD", period=period, progress=False,
                           auto_adjust=True, threads=False)
        if raw.empty:
            return None
        close = raw["Close"] if "Close" in raw.columns else raw.iloc[:, 0]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        # fix review quant 22/07 (E5): BTC quota 7/7 — il pct_change di calendario
        # dava al lunedi' il solo tratto dom->lun mentre asset e fattori coprono
        # ven->lun: ~2/7 della varianza BTC spariva dal regressore (beta_btc
        # attenuato). Chiusure dei soli weekday: il lunedi' compone ven->lun.
        close = close[close.index.dayofweek < 5]
        ret = close.pct_change().dropna()
        ret.index = pd.to_datetime(ret.index).tz_localize(None).normalize()
        ret.name = "BTC"
        _BTC_RETURNS_CACHE[period] = ret
        return ret
    except Exception as e:
        _log(f"BTC download failed: {e}")
        return None


def _yf_ticker(ticker: str, salta: frozenset) -> Optional[str]:
    """`salta` arriva dal chiamante: senza default, cosi' un punto di chiamata
    dimenticato e' un TypeError e non un insieme vuoto zitto."""
    t = ticker.strip().upper()
    if t in salta:
        return None
    return t


def compute_holding_exposure(ticker: str, period: str = "3y",
                              ff_returns: Optional[pd.DataFrame] = None,
                              add_btc_factor: bool = False,
                              factor_region: str = "us",
                              salta: Optional[frozenset] = None) -> Optional[Dict[str, Any]]:
    """Calcola Fama-French exposure di un singolo ticker.

    Args:
      ticker: symbol (must be US-listed for FF factors to make sense)
      period: yfinance period string (default 3y)
      ff_returns: pre-downloaded FF DataFrame (optional optimization)
      add_btc_factor: aggiungi BTC-USD come 7. fattore (per asset crypto-correlated)

    Returns dict con: alpha, betas, t-stats, R^2, n_obs.
    Se add_btc_factor=True, include anche beta_btc + beta_btc_tstat.
    """
    if not (NUMPY_OK and YF_OK and SM_OK):
        raise RuntimeError("numpy/yfinance/statsmodels non installati")

    # `salta` arriva dal chiamante quando c'e' un giro in corso (compute_portfolio_factors
    # legge il negozio UNA volta): cosi' la lettura dichiarata nel payload e quella usata per
    # gli holding sono LA STESSA. Chiamata singola (tool, __main__) = None: legge lei.
    if salta is None:
        salta = prezzi_speciali()["prezzi"]["senza_yfinance"]
    yf_sym = _yf_ticker(ticker, salta)
    if not yf_sym:
        return None

    if ff_returns is None:
        ff_returns = download_ff5_returns()

    try:
        from bellomberg.cli.price_updater import data_ticker as _dt
        prices = yf.download(_dt(yf_sym), period=period, progress=False,
                              auto_adjust=True, threads=False)
        if prices.empty:
            return None
        if "Close" in prices.columns:
            px = prices["Close"]
        else:
            px = prices.iloc[:, 0]
        if isinstance(px, pd.DataFrame):
            px = px.iloc[:, 0]
    except Exception as e:
        _log(f"yf download {ticker} failed: {e}")
        return None

    asset_returns = px.pct_change().dropna()
    if len(asset_returns) < MIN_OBSERVATIONS:
        return None

    asset_returns.index = pd.to_datetime(asset_returns.index).tz_localize(None).normalize()
    ff = ff_returns.copy()
    ff.index = pd.to_datetime(ff.index).normalize()

    df = pd.DataFrame({"asset": asset_returns}).join(ff, how="inner").dropna()
    if len(df) < MIN_OBSERVATIONS:
        return None

    df["asset_exc"] = df["asset"] - df["RF"]
    factor_cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]

    # Aggiungi BTC factor se richiesto
    if add_btc_factor:
        btc_ret = _download_btc_returns(period=period)
        if btc_ret is not None:
            df_ff6 = df  # audit/11 §4: il fallback deve RIPRISTINARE il campione pre-join
            df = df.join(btc_ret, how="inner").dropna()
            if len(df) >= MIN_OBSERVATIONS:
                # BTC factor = BTC return - risk-free (excess return)
                df["BTC_exc"] = df["BTC"] - df["RF"]
                factor_cols = factor_cols + ["BTC_exc"]
            else:
                # Not enough overlap, fall back to FF6 only — sul df INTERO, non su
                # quello gia' troncato dal join con BTC (regressione sotto MIN_OBS)
                add_btc_factor = False
                df = df_ff6

    X = df[factor_cols]
    X = sm.add_constant(X)
    y = df["asset_exc"]

    n = len(df)
    nw_lag = max(1, int(np.floor(4 * (n / 100) ** (2 / 9))))

    try:
        model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": nw_lag})
    except Exception as e:
        _log(f"OLS {ticker} failed: {e}")
        return None

    params = model.params
    tvals = model.tvalues
    pvals = model.pvalues

    result = {
        "ticker": ticker,
        "n_obs": int(n),
        "period": period,
        "factor_region": factor_region,
        "factor_set": (REGIONAL_FF.get(factor_region) or {}).get("label", factor_region),
        # audit/11 §5: i fattori K.French regionali sono denominati in USD, i rendimenti
        # dell'asset in valuta di quotazione: per i non-USD il beta/alpha include il
        # rumore del cambio. Dichiarato finche' non si converte in USD.
        "fx_caveat": ("rendimenti asset in valuta locale vs fattori USD: beta/alpha "
                      "contaminati dal cambio" if "." in (ticker or "") else None),
        "alpha_daily_pct": round(float(params["const"]) * 100, 4),
        "alpha_annualized_pct": round(float(params["const"]) * 252 * 100, 2),
        "alpha_tstat": round(float(tvals["const"]), 2),
        "alpha_pvalue": round(float(pvals["const"]), 4),
        "beta_market": round(float(params["Mkt-RF"]), 3),
        "beta_market_tstat": round(float(tvals["Mkt-RF"]), 2),
        "beta_smb": round(float(params["SMB"]), 3),
        "beta_smb_tstat": round(float(tvals["SMB"]), 2),
        "beta_hml": round(float(params["HML"]), 3),
        "beta_hml_tstat": round(float(tvals["HML"]), 2),
        "beta_rmw": round(float(params["RMW"]), 3),
        "beta_rmw_tstat": round(float(tvals["RMW"]), 2),
        "beta_cma": round(float(params["CMA"]), 3),
        "beta_cma_tstat": round(float(tvals["CMA"]), 2),
        "beta_mom": round(float(params["Mom"]), 3),
        "beta_mom_tstat": round(float(tvals["Mom"]), 2),
        "r_squared": round(float(model.rsquared), 3),
        "r_squared_adj": round(float(model.rsquared_adj), 3),
        "f_pvalue": round(float(model.f_pvalue), 6),
        "nw_lag": nw_lag,
        "cov_type": "HAC-NeweyWest",
        "has_btc_factor": False,
        "beta_btc": None,
        "beta_btc_tstat": None,
    }

    if add_btc_factor and "BTC_exc" in factor_cols:
        result["has_btc_factor"] = True
        result["beta_btc"] = round(float(params["BTC_exc"]), 3)
        result["beta_btc_tstat"] = round(float(tvals["BTC_exc"]), 2)

    return result


def compute_portfolio_factors(period: str = "3y", force: bool = False) -> Dict[str, Any]:
    """Aggregate Fama-French factor exposure del portfolio.

    Asset NON-US listed sono ESCLUSI (FF factors sono US-only).
    Asset crypto-correlated (negozio privato dei fattori) ricevono BTC come 7. fattore.

    Per ogni holding US calcola exposure individuale, poi aggrega ponderato
    sul SUB-portfolio US (con coverage % esplicito).
    """
    cache_key = f"portfolio_factors:v2:{period}"

    if not (NUMPY_OK and YF_OK and SM_OK and REQ_OK):
        missing = []
        if not NUMPY_OK: missing.append("numpy/pandas")
        if not YF_OK: missing.append("yfinance")
        if not SM_OK: missing.append("statsmodels")
        if not REQ_OK: missing.append("requests/zipfile")
        return {"error": f"libraries with failed import: {', '.join(missing)}",
                "import_errors": _IMPORT_ERRORS,
                "timestamp": datetime.now().isoformat()}

    try:
        db = MemoryDB()
        snap = db.get_portfolio_summary()
    except Exception as e:
        return {"error": f"portfolio fetch failed: {e}", "timestamp": datetime.now().isoformat()}
    if snap.get("fx_incomplete"):
        return {"error": "FX incompleto: pesi fattori EUR n.d. (" +
                         ", ".join(snap["fx_incomplete"]) + ")",
                "timestamp": datetime.now().isoformat()}
    if not force and FACTORS_RESULT_CACHE["key"] == cache_key and \
       FACTORS_RESULT_CACHE["data"] and (time.time() - FACTORS_RESULT_CACHE["ts"] < CACHE_TTL_SEC):
        return FACTORS_RESULT_CACHE["data"]

    positions = snap.get("positions", [])
    if not positions:
        return {"error": "no positions", "timestamp": datetime.now().isoformat()}

    total_eur = sum((p.get("valore_mercato") or 0) for p in positions)
    if total_eur <= 0:
        return {"error": "zero portfolio value", "timestamp": datetime.now().isoformat()}

    # il negozio dei fattori si legge UNA volta per giro: il payload (filters.negozio_fattori)
    # descrive lo stesso stato usato per ogni titolo (review 05/09: il nome era usato e mai
    # assegnato, e nessun test esercitava questa funzione)
    _fatt = _fattori()
    # stessa regola per il negozio dei prezzi speciali: UNA lettura per giro, passata giu'
    _prezzi = prezzi_speciali()
    _salta = _prezzi["prezzi"]["senza_yfinance"]

    # #166: scarica i fattori SOLO per le regioni effettivamente in portfolio
    regions_needed: Dict[str, List[str]] = {}
    for p in positions:
        tk = p.get("ticker") or ""
        if _yf_ticker(tk, _salta) is None:
            continue
        regions_needed.setdefault(_region_for_ticker(tk, _fatt), []).append(tk)

    ff_by_region: Dict[str, pd.DataFrame] = {}
    region_errors: Dict[str, str] = {}
    for reg in sorted(regions_needed):
        try:
            ff_by_region[reg] = download_ff5_returns(force=False, region=reg)
        except Exception as e:
            region_errors[reg] = str(e)
            _log(f"[{reg}] FF download failed: {e}")

    if not ff_by_region:
        return {"error": "FF download failed per tutte le regioni",
                "region_errors": region_errors,
                "timestamp": datetime.now().isoformat()}

    per_holding: Dict[str, Any] = {}
    skipped: List[Dict[str, Any]] = []
    n_with_btc = 0
    for p in positions:
        ticker = p["ticker"]
        weight = (p.get("valore_mercato") or 0) / total_eur
        if weight <= 0:
            continue

        if _yf_ticker(ticker, _salta) is None:
            skipped.append({"ticker": ticker, "weight_pct": round(weight*100, 2),
                            "reason": "ticker in SKIP list (e.g. crypto custom)"})
            continue

        # #166: ogni holding regredisce sui fattori della PROPRIA regione
        region = _region_for_ticker(ticker, _fatt)
        ff_reg = ff_by_region.get(region)
        if ff_reg is None:
            skipped.append({"ticker": ticker, "weight_pct": round(weight*100, 2),
                            "reason": f"dataset FF '{region}' non scaricabile: "
                                      f"{region_errors.get(region, 'unknown')}"})
            continue

        # BTC factor per crypto-correlated
        add_btc = _is_crypto_correlated(ticker, _fatt)

        exposure = compute_holding_exposure(ticker, period=period, salta=_salta,
                                             ff_returns=ff_reg,
                                             add_btc_factor=add_btc,
                                             factor_region=region)
        if exposure is None:
            skipped.append({"ticker": ticker, "weight_pct": round(weight*100, 2),
                            "reason": "insufficient yfinance history or OLS failed"})
            continue
        exposure["weight"] = round(weight, 4)
        exposure["weight_pct"] = round(weight * 100, 2)
        # review 22/07 (E7): dichiarare se la regione e' una scelta economica
        # (override) o solo dedotta dal suffisso di quotazione
        exposure["region_source"] = ("override" if ticker.strip().upper() in _fatt["fattori"]["regioni"]
                                     else "suffisso di quotazione (euristica)")
        per_holding[ticker] = exposure
        if exposure.get("has_btc_factor"):
            n_with_btc += 1

    if not per_holding:
        return {"error": "no holdings could be analyzed",
                "skipped_detail": skipped,
                "timestamp": datetime.now().isoformat()}

    # Aggregate (renormalize weights over analyzable holdings only)
    total_w = sum(h["weight"] for h in per_holding.values())
    agg = {f: 0.0 for f in [
        "alpha_annualized_pct", "beta_market", "beta_smb", "beta_hml",
        "beta_rmw", "beta_cma", "beta_mom"]}
    for h in per_holding.values():
        w_norm = h["weight"] / total_w if total_w > 0 else 0
        for k in agg:
            agg[k] += w_norm * h[k]
    agg = {k: round(v, 3) for k, v in agg.items()}

    # Weighted avg R^2 over analyzed
    weighted_r2 = sum((h["weight"] / total_w) * h["r_squared"]
                      for h in per_holding.values()) if total_w > 0 else 0

    # Statistical significance summary
    n_alpha_significant = sum(1 for h in per_holding.values()
                                if abs(h.get("alpha_tstat", 0)) >= 1.96)

    result = {
        "timestamp": datetime.now().isoformat(),
        "version": "v3-regional",
        "model": "Fama-French 5-factor + Momentum (Carhart) REGIONALE + optional BTC factor",
        "method": "OLS with Newey-West HAC standard errors",
        "data_source": "Kenneth French Data Library (US/Europe/Japan/Asia-Pacific/Global daily) + yfinance BTC-USD",
        "period": period,
        "filters": {
            "regional_factors": True,
            "region_overrides": _fatt["fattori"]["regioni"],
            "btc_factor_for": sorted(_fatt["fattori"]["crypto_correlati"]),
            # lo stato del negozio privato: assente/illeggibile = nessun override e nessun
            # fattore BTC, e qui si legge PERCHE' (regola 14/07)
            "negozio_fattori": {"origine": _fatt["origine"], "motivo": _fatt["motivo"]},
            # idem per i prezzi speciali: negozio assente = nessun simbolo saltato, e in
            # `skipped` comparirebbe un motivo diverso da quello vero
            "negozio_prezzi": {"origine": _prezzi["origine"], "motivo": _prezzi["motivo"]},
            "note_aggregate": "betas aggregati cross-region (composite): ogni holding "
                              "regredisce sui fattori della propria regione",
        },
        "regions": {
            reg: {
                "label": (REGIONAL_FF.get(reg) or {}).get("label", reg),
                "n_holdings": sum(1 for h in per_holding.values()
                                  if h.get("factor_region") == reg),
                "weight_pct": round(sum(h["weight"] for h in per_holding.values()
                                        if h.get("factor_region") == reg) * 100, 1),
            }
            for reg in sorted({h.get("factor_region", "us") for h in per_holding.values()})
        },
        "region_errors": region_errors,
        "n_holdings_analyzed": len(per_holding),
        "n_holdings_skipped": len(skipped),
        "n_holdings_with_btc_factor": n_with_btc,
        "n_alpha_significant_5pct": n_alpha_significant,
        "skipped_tickers": [s["ticker"] for s in skipped],  # backward-compat
        "skipped_detail": skipped,                           # new: with reasons
        "coverage_weight_pct": round(total_w * 100, 1),
        "portfolio_aggregate": agg,
        "portfolio_avg_r_squared": round(weighted_r2, 3),
        "per_holding": per_holding,
        "ff_data_last_date": str((ff_by_region.get("us")
                                  if ff_by_region.get("us") is not None
                                  else list(ff_by_region.values())[0]).index[-1].date()),
        "ff_data_n_obs": int(len(ff_by_region.get("us")
                                 if ff_by_region.get("us") is not None
                                 else list(ff_by_region.values())[0])),
        "ff_data_by_region": {reg: {"last_date": str(df.index[-1].date()),
                                    "n_obs": int(len(df))}
                              for reg, df in ff_by_region.items()},
    }

    FACTORS_RESULT_CACHE["ts"] = time.time()
    FACTORS_RESULT_CACHE["key"] = cache_key
    FACTORS_RESULT_CACHE["data"] = result
    _log(f"FF v3-regional: {len(per_holding)} analyzed ({result['coverage_weight_pct']:.1f}% NAV), "
         f"{len(skipped)} skipped, {n_with_btc} w/ BTC factor, "
         f"alpha_ann={agg['alpha_annualized_pct']:.2f}%, "
         f"beta_mkt={agg['beta_market']:.2f}, R2={weighted_r2:.2f}, "
         f"alpha_signif={n_alpha_significant}/{len(per_holding)}")
    return result


def invalidate_cache():
    FACTORS_RESULT_CACHE["ts"] = 0
    FACTORS_RESULT_CACHE["data"] = None


if __name__ == "__main__":
    import json
    r = compute_portfolio_factors(force=True)
    print(json.dumps(r, indent=2, default=str)[:4000])
