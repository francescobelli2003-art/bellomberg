# -*- coding: utf-8 -*-
"""aggiorna_damodaran.py — scarica i dataset Damodaran, li converte e rigenera lo
snapshot versionato `damodaran_snapshot.json` (repo) usato da damodaran_data.py
(audit/13 V1.1, ok PM 16/07: ERP live mensile + CRP completo + industry data).

La VERSIONE di ogni sezione e' letta dalla cella in-file ("Date updated"/"Date of
update"), MAI dal nome file o dalla data di download. I .xls binari vengono
convertiti in CSV via Excel COM (scripts/xls_to_csv.ps1: xlrd non e' installato).
I file grezzi restano in data/damodaran/ (runtime, sacrificabili); copia datata in
data/damodaran/archive/AAAAMMGG/. Lo snapshot committato nel repo e' l'archivio vero.

USO (dalla radice del repo):
  python scripts\\aggiorna_damodaran.py                  <- download + build completo
  python scripts\\aggiorna_damodaran.py --skip-download  <- solo build dai file gia' scaricati

Cadenza consigliata: gennaio (aggiornamento annuale industry) + quando esce una
release CRP infra-annuale (probe automatico sui pattern ctryprem{Apr|July}{YY}.xlsx)
+ una volta al mese per l'ERP. Lancio del PM.
"""
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, date

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", ROOT)
sys.path.insert(0, ROOT)  # per importare market_inputs (rf live) dal repo
from bellomberg.core.paths import DATA_DIR
DATA = os.path.join(DATA_DIR, "damodaran")
CSVD = os.path.join(DATA, "csv")
SNAPSHOT = os.path.join(ROOT, "src", "bellomberg", "resources", "examples",
                        "damodaran_snapshot.json")
BASE_URL = "https://pages.stern.nyu.edu/~adamodar/"
UA = {"User-Agent": "Mozilla/5.0 (Bellomberg; uso personale con attribuzione)"}

# file annuali (gennaio) — URL correnti stabili, sovrascritti da Damodaran a gennaio
ANNUAL_FILES = [
    "pc/datasets/betas.xls", "pc/datasets/betaEurope.xls", "pc/datasets/betaJapan.xls",
    "pc/datasets/betaGlobal.xls", "pc/datasets/betaemerg.xls",
    "pc/datasets/fundgr.xls", "pc/datasets/fundgrEurope.xls", "pc/datasets/fundgrJapan.xls",
    "pc/datasets/fundgrEB.xls", "pc/datasets/fundgrEBEurope.xls", "pc/datasets/fundgrEBJapan.xls",
    "pc/datasets/histgr.xls", "pc/datasets/histgrEurope.xls", "pc/datasets/histgrJapan.xls",
    "pc/datasets/capex.xls", "pc/datasets/capexEurope.xls", "pc/datasets/capexJapan.xls",
    "pc/datasets/countrytaxrates.xls",
]
ERP_FILE = "pc/implprem/ERPbymonth.xlsx"
# regione -> (beta, fundgr, fundgrEB, histgr, capex); None = variante non pubblicata
REGIONS = {
    "US": ("betas", "fundgr", "fundgrEB", "histgr", "capex"),
    "Europe": ("betaEurope", "fundgrEurope", "fundgrEBEurope", "histgrEurope", "capexEurope"),
    "Japan": ("betaJapan", "fundgrJapan", "fundgrEBJapan", "histgrJapan", "capexJapan"),
    "Global": ("betaGlobal", None, None, None, None),
    "Emerging": ("betaemerg", None, None, None, None),
}
# sanity: paesi che DEVONO esserci nel CRP (book attuale) e industry campione
REQUIRED_COUNTRIES = ["Italy", "Germany", "Japan", "Brazil", "United Kingdom",
                      "United States", "Denmark", "Netherlands", "Luxembourg", "Switzerland"]
REQUIRED_INDUSTRIES = ["Aerospace/Defense", "Semiconductor", "Drugs (Pharmaceutical)",
                       "Bank (Money Center)", "Banks (Regional)", "Steel",
                       "Oilfield Svcs/Equip.", "Semiconductor Equip"]


def _num(s):
    """'15.56%' -> 0.1556 · '0.85' -> 0.85 · 'NA'/'#DIV/0!'/'' -> None (buco dichiarato)."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("$", "")
    if not t or t.upper() in ("NA", "N/A", "NONE") or "#" in t:
        return None
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    try:
        v = float(t)
    except ValueError:
        return None
    return round(v / 100.0, 6) if pct else round(v, 6)


def _parse_date_label(s):
    """'5-Jan-26' -> '2026-01-05' (formato in-file Damodaran)."""
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def download():
    import requests
    os.makedirs(DATA, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    arch = os.path.join(DATA, "archive", stamp)
    os.makedirs(arch, exist_ok=True)
    urls = [ERP_FILE] + ANNUAL_FILES + _probe_ctryprem()
    for rel in urls:
        name = os.path.basename(rel)
        try:
            r = requests.get(BASE_URL + rel, headers=UA, timeout=60)
        except Exception as e:
            print("KO  %-28s %s" % (name, e))
            continue
        if r.status_code != 200 or len(r.content) < 5000:
            print("KO  %-28s HTTP %s (%d byte)" % (name, r.status_code, len(r.content)))
            continue
        dest = os.path.join(DATA, name)
        with open(dest, "wb") as f:
            f.write(r.content)
        shutil.copy2(dest, os.path.join(arch, name))
        print("OK  %-28s %d KB" % (name, len(r.content) // 1024))


def _probe_ctryprem():
    """Trova la release CRP piu' recente: probe sui pattern datati (Apr/July) + gennaio."""
    import requests
    yy = date.today().year % 100
    candidates = []
    for y in (yy, yy - 1):
        for tag in ("July", "Apr"):
            candidates.append("pc/datasets/ctryprem%s%02d.xlsx" % (tag, y))
    candidates.append("pc/datasets/ctryprem.xlsx")  # edizione gennaio corrente
    found = []
    for rel in candidates:
        try:
            h = requests.head(BASE_URL + rel, headers=UA, timeout=30)
            if h.status_code == 200:
                found.append(rel)
        except Exception:
            continue
    if not found:
        print("ATTENZIONE: nessuna release ctryprem raggiungibile (probe HEAD tutti KO)")
    return found[:1]  # la prima trovata = la piu' recente per costruzione


def convert():
    ps1 = os.path.join(ROOT, "tools", "ops", "xls_to_csv.ps1")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", ps1, "-SrcDir", DATA],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("conversione COM fallita: " + (r.stderr or r.stdout or "?"))
    n = len([ln for ln in (r.stdout or "").splitlines() if ln.startswith("OK ")])
    print("Convertiti %d fogli in CSV." % n)


def _read_csv(name):
    p = os.path.join(CSVD, name)
    if not os.path.exists(p):
        return None
    with io.open(p, encoding="utf-8-sig", errors="replace") as f:
        return list(csv.reader(f))


def _industry_csv(base):
    """Il foglio 'Industry Averages' del file (nomi foglio leggermente diversi tra file)."""
    for suff in ("Industry_Averages",):
        rows = _read_csv("%s__%s.csv" % (base, suff))
        if rows:
            return rows
    return None


def _parse_industry_file(base, colmap):
    """Ritorna (date_iso, {industry: {campo: valore}}). colmap: header-substring -> campo."""
    rows = _industry_csv(base)
    if not rows:
        return None, {}
    dt = None
    for r in rows[:4]:
        if r and "date updated" in (r[0] or "").lower():
            dt = _parse_date_label(r[1])
            break
    hdr_i = next((i for i, r in enumerate(rows)
                  if r and (r[0] or "").strip().lower() == "industry name"), None)
    if hdr_i is None:
        return dt, {}
    hdr = [(h or "").strip().lower() for h in rows[hdr_i]]
    idx = {}
    for sub, field in colmap.items():
        for j, h in enumerate(hdr):
            if j > 0 and sub in h and field not in idx.values():
                idx[j] = field
                break
    out = {}
    for r in rows[hdr_i + 1:]:
        name = (r[0] or "").strip()
        if not name or name.lower().startswith("total") or name.lower().startswith("grand total"):
            continue
        rec = {}
        for j, field in idx.items():
            if j < len(r):
                rec[field] = _num(r[j])
        if rec:
            out[name] = rec
    return dt, out


def parse_all():
    snap = {"_meta": {"built": datetime.now().isoformat(timespec="seconds"),
                      "source": "Damodaran Online, NYU Stern (pages.stern.nyu.edu/~adamodar) — "
                                "gratuito con attribuzione, versioni lette dalle celle in-file",
                      "files": {}}}

    # --- ERP implied mensile (colonna 'ERP (T12m)', ultima riga) ---
    from openpyxl import load_workbook
    erp_p = os.path.join(DATA, "ERPbymonth.xlsx")
    wb = load_workbook(erp_p, read_only=True, data_only=True)
    ws = wb["Historical ERP"]
    data = [r for r in ws.iter_rows(values_only=True) if r and r[0] is not None]
    wb.close()
    hdr = [str(c or "").strip() for c in data[0]]
    j_erp = hdr.index("ERP (T12m)")
    last = data[-1]
    if not isinstance(last[0], datetime) or last[j_erp] is None:
        raise RuntimeError("ERPbymonth: ultima riga senza data o senza ERP (T12m)")
    snap["erp"] = {"value": round(float(last[j_erp]), 4),
                   "asof": last[0].date().isoformat(),
                   "series": "ERPbymonth.xlsx col 'ERP (T12m)' (implied, S&P 500)",
                   "tbond_10y": round(float(last[2]), 4) if last[2] is not None else None}
    snap["_meta"]["files"]["ERPbymonth.xlsx"] = snap["erp"]["asof"]

    # --- CRP: release piu' recente presente su disco (per data in-file B2) ---
    best = None
    for fn in os.listdir(DATA):
        if fn.lower().startswith("ctryprem") and fn.lower().endswith(".xlsx"):
            wb = load_workbook(os.path.join(DATA, fn), read_only=True, data_only=True)
            try:
                ws = wb["ERPs by country"]
                b2 = ws.cell(row=2, column=2).value
                d = b2.date().isoformat() if isinstance(b2, datetime) else None
                if d and (best is None or d > best[1]):
                    best = (fn, d)
            finally:
                wb.close()
    if not best:
        raise RuntimeError("nessun ctryprem*.xlsx con data leggibile in data/damodaran/")
    fn, crp_date = best
    wb = load_workbook(os.path.join(DATA, fn), read_only=True, data_only=True)
    ws = wb["ERPs by country"]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, r in enumerate(rows) if r and str(r[0]).strip() == "Country")
    hdr = [str(c or "").strip() for c in rows[hdr_i]]
    j_crp = next(j for j, h in enumerate(hdr) if h == "Country Risk Premium")
    crp = {}
    for r in rows[hdr_i + 1:]:
        if not r or r[0] is None:
            continue
        name = str(r[0]).strip()
        v = r[j_crp]
        if isinstance(v, (int, float)):
            crp[name] = round(float(v), 6)
    mature = ws.cell(row=3, column=5).value  # E3 = implied ERP mature market
    # foglio gemello: tax marginali per paese DELLA STESSA release (fonte primaria)
    tax_ctry = {}
    if "Country Tax Rates" in wb.sheetnames:
        for r in wb["Country Tax Rates"].iter_rows(values_only=True):
            if r and isinstance(r[0], str) and isinstance(r[1], (int, float)) and 0 < r[1] < 1:
                tax_ctry[r[0].strip()] = round(float(r[1]), 6)
    wb.close()
    snap["crp"] = crp
    snap["crp_meta"] = {"asof": crp_date, "file": fn,
                        "column": "Country Risk Premium (rating-based)",
                        "mature_erp_check": round(float(mature), 4) if isinstance(mature, (int, float)) else None}
    snap["_meta"]["files"][fn] = crp_date

    # --- tax marginali per paese (countrytaxrates.xls, fonte Tax Foundation) ---
    rows = _read_csv("countrytaxrates__Sheet1.csv")
    if rows:
        dt = None
        for r in rows[:4]:
            if r and "date updated" in (r[0] or "").lower():
                dt = _parse_date_label(r[1])
        hdr_i = next((i for i, r in enumerate(rows) if r and (r[0] or "").strip() == "Country"), None)
        tx = {}
        if hdr_i is not None:
            for r in rows[hdr_i + 1:]:
                if r and (r[0] or "").strip():
                    v = _num(r[1] if len(r) > 1 else None)
                    if v is not None:
                        tx[r[0].strip()] = v
        # merge dichiarato: base = countrytaxrates (annuale), integrata dal foglio
        # della release CRP per i paesi che mancano
        for k, v in tax_ctry.items():
            tx.setdefault(k, v)
        snap["tax_marginal"] = tx
        snap["tax_meta"] = {"asof": dt, "file": "countrytaxrates.xls (Tax Foundation) "
                                                "+ foglio 'Country Tax Rates' della release CRP"}
        snap["_meta"]["files"]["countrytaxrates.xls"] = dt

    # --- industry data per regione ---
    maps = {
        "beta": {"number of firms": "n", "beta": "beta", "d/e ratio": "de",
                 "effective tax rate": "tax_eff", "unlevered beta": "beta_u",
                 "cash/firm value": "cash_fv",
                 "unlevered beta corrected for cash": "beta_u_cash"},
        "fundgr": {"roe": "roe", "retention ratio": "retention",
                   "fundamental growth": "g_eps_fund"},
        "fundgrEB": {"roc": "roc", "reinvestment rate": "reinv",
                     "expected growth in ebit": "g_ebit"},
        "histgr": {"cagr in net income": "cagr_ni5", "cagr in revenues": "cagr_rev5",
                   "next 2 years": "exp_rev2", "revenues - next 5": "exp_rev5",
                   "eps - next 5": "exp_eps5"},
        "capex": {"cap ex/deprecn": "capex_deprecn", "net cap ex/sales": "netcapex_sales",
                  "invested capital": "sales_ic"},
    }
    # nota: in 'beta' il match substring va disambiguato — ordino i match per lunghezza
    industries = {}
    ind_dates = {}
    for region, files in REGIONS.items():
        merged = {}
        for kind, base in zip(("beta", "fundgr", "fundgrEB", "histgr", "capex"), files):
            if base is None:
                continue
            colmap = maps[kind]
            if kind == "beta":
                dt, rec = _parse_beta_file(base)
            else:
                dt, rec = _parse_industry_file(base, colmap)
            if dt:
                ind_dates[base] = dt
            for name, fields in rec.items():
                merged.setdefault(name, {}).update(fields)
        if merged:
            industries[region] = merged
    snap["industries"] = industries
    snap["industries_meta"] = {"asof": max(ind_dates.values()) if ind_dates else None,
                               "per_file": ind_dates,
                               "regions_full": [r for r, f in REGIONS.items() if f[1]],
                               "regions_beta_only": [r for r, f in REGIONS.items() if not f[1]]}
    for b, d in ind_dates.items():
        snap["_meta"]["files"][b + ".xls"] = d
    return snap


def _parse_beta_file(base):
    """Il file beta ha colonne ambigue per substring ('Unlevered beta' vs '... corrected
    for cash' vs 'Beta'): match per NOME ESATTO normalizzato."""
    rows = _industry_csv(base)
    if not rows:
        return None, {}
    dt = None
    for r in rows[:4]:
        if r and "date updated" in (r[0] or "").lower():
            dt = _parse_date_label(r[1])
            break
    hdr_i = next((i for i, r in enumerate(rows)
                  if r and (r[0] or "").strip().lower() == "industry name"), None)
    if hdr_i is None:
        return dt, {}
    exact = {"number of firms": "n", "beta": "beta", "d/e ratio": "de",
             "effective tax rate": "tax_eff", "unlevered beta": "beta_u",
             "cash/firm value": "cash_fv",
             "unlevered beta corrected for cash": "beta_u_cash"}
    idx = {}
    for j, h in enumerate(rows[hdr_i]):
        key = (h or "").strip().lower()
        if j > 0 and key in exact:
            idx[j] = exact[key]
    out = {}
    for r in rows[hdr_i + 1:]:
        name = (r[0] or "").strip()
        if not name or name.lower().startswith("total") or name.lower().startswith("grand total"):
            continue
        rec = {}
        for j, field in idx.items():
            if j < len(r):
                rec[field] = _num(r[j])
        if rec:
            out[name] = rec
    return dt, out


def validate(snap):
    errs, warns = [], []
    if not (0.02 <= (snap.get("erp", {}).get("value") or 0) <= 0.08):
        errs.append("ERP fuori banda [2%%,8%%]: %s" % snap.get("erp"))
    mm = snap.get("crp_meta", {}).get("mature_erp_check")
    if mm is not None and abs(mm - snap["erp"]["value"]) > 0.005:
        warns.append("ERP mensile %.4f vs mature-market della release CRP %.4f: >50bp, "
                     "release disallineate (dichiarare quale si usa)" % (snap["erp"]["value"], mm))
    for c in REQUIRED_COUNTRIES:
        if c not in snap.get("crp", {}):
            errs.append("CRP: paese richiesto ASSENTE: " + c)
        if c not in snap.get("tax_marginal", {}):
            warns.append("tax_marginal: paese richiesto assente: " + c)
    for region in ("US", "Europe", "Japan"):
        ind = snap.get("industries", {}).get(region, {})
        if len(ind) < 90:
            errs.append("industries[%s]: solo %d industry (attese ~94)" % (region, len(ind)))
        for k in REQUIRED_INDUSTRIES:
            if k not in ind:
                errs.append("industries[%s]: industry richiesta ASSENTE: %s" % (region, k))
            elif ind[k].get("beta_u_cash") is None:
                warns.append("industries[%s][%s]: beta_u_cash n.d." % (region, k))
    return errs, warns


def main():
    skip_dl = "--skip-download" in sys.argv
    if not skip_dl:
        download()
    convert()
    snap = parse_all()
    errs, warns = validate(snap)
    for w in warns:
        print("WARN:", w)
    if errs:
        for e in errs:
            print("ERRORE:", e)
        print("SNAPSHOT NON SCRITTO: correggere prima (regola no-fallback-silenziosi).")
        sys.exit(1)
    # rf statici di fallback: best-effort dal canale live (V1.2); se il modulo non
    # c'e' ancora o le fonti sono giu', la sezione resta quella precedente (dichiarato)
    try:
        from bellomberg.market_data.market_inputs import get_risk_free_ex
        rf = {}
        for cur in ("USD", "EUR", "GBP", "JPY", "DKK", "BRL", "CHF"):
            r = get_risk_free_ex(cur)
            # review pre-commit (M9): nello snapshot entrano SOLO osservazioni LIVE —
            # mai promuovere un hardcoded/LKG a "static-snapshot" con data fresca
            if r and r.get("value") and r.get("tier") == "live":
                rf[cur] = {"value": round(r["value"], 5), "asof": r.get("obs_date"),
                           "source": r.get("source")}
        if rf:
            snap["rf_static"] = rf
            snap["rf_static_meta"] = {"refreshed": date.today().isoformat(),
                                      "nota": "fallback DICHIARATO del canale rf live "
                                              "(market_inputs), rinfrescato a ogni run di "
                                              "questo script"}
    except Exception as e:
        old = {}
        if os.path.exists(SNAPSHOT):
            with io.open(SNAPSHOT, encoding="utf-8") as f:
                old = json.load(f)
        if old.get("rf_static"):
            snap["rf_static"] = old["rf_static"]
            snap["rf_static_meta"] = old.get("rf_static_meta")
            print("WARN: rf live non disponibile (%s): rf_static ereditato dallo "
                  "snapshot precedente (asof invariati)." % e)
        else:
            print("WARN: rf live non disponibile (%s) e nessuno snapshot precedente: "
                  "sezione rf_static ASSENTE (il canale rf usera' i suoi statici "
                  "etichettati)." % e)
    with io.open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1, sort_keys=True)
    n_ind = {r: len(v) for r, v in snap["industries"].items()}
    print("\nSNAPSHOT SCRITTO: %s" % SNAPSHOT)
    print("  ERP %.2f%% (asof %s) | CRP %d paesi (asof %s, %s) | tax %d paesi | industry %s"
          % (snap["erp"]["value"] * 100, snap["erp"]["asof"], len(snap["crp"]),
             snap["crp_meta"]["asof"], snap["crp_meta"]["file"],
             len(snap.get("tax_marginal", {})), n_ind))
    print("Ora: commit di damodaran_snapshot.json (git add + commit).")


if __name__ == "__main__":
    main()
