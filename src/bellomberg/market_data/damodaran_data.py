# -*- coding: utf-8 -*-
"""damodaran_data.py — accesso allo snapshot Damodaran versionato (audit/13 V1.1).

Fonte: damodaran_snapshot.json (committato nel repo), costruito e aggiornato da
tools/ops/aggiorna_damodaran.py (lancio PM). Ogni valore esce SEMPRE con la sua
versione (asof) e la sua fonte: chi consuma li propaga fino al foglio WACC.

Regole (no fallback silenziosi, 14/07):
- paese/industry non nello snapshot -> None (il chiamante dichiara il buco);
- valore di industry fuori dai bound di plausibilita' (es. beta Japan degeneri:
  Banks Regional JP = 99,24 misurato) -> fallback di REGIONE dichiarato nel
  campo 'note' (region -> Global -> US), mai il numero rotto zitto;
- snapshot vecchio -> flag 'stale' True (ERP >60gg, CRP >8 mesi, industry >14 mesi).
"""
import io
import json
import os
from bellomberg.core.paths import EXAMPLES_DIR
from datetime import date, datetime
from typing import Any, Dict, Optional

_SNAP_PATH = str(EXAMPLES_DIR / "damodaran_snapshot.json")
_snap_cache: Optional[Dict[str, Any]] = None

# alias yfinance info['country'] -> nome paese Damodaran (solo dove differiscono)
_COUNTRY_ALIASES = {
    "South Korea": "Korea", "Korea, Republic of": "Korea",
    "Russia": "Russian Federation", "Vietnam": "Viet Nam",
    "Slovakia": "Slovak Republic", "Kyrgyzstan": "Kyrgyz Republic",
    "Hong Kong": "Hong Kong SAR", "Macau": "Macao SAR",
    "Taiwan": "Taiwan, China", "Czech Republic": "Czechia",
}
# bound di plausibilita' per campo (fuori bound = dato degenere -> fallback regione)
_FIELD_BOUNDS = {
    "beta_u_cash": (0.15, 3.0), "beta_u": (0.15, 3.0), "beta": (0.15, 3.5),
    "de": (0.0, 5.0), "tax_eff": (0.0, 0.55),
    "roe": (-0.5, 0.6), "retention": (-1.5, 1.5), "g_eps_fund": (-0.25, 0.40),
    "roc": (-0.5, 1.0), "reinv": (-1.5, 3.0), "g_ebit": (-0.25, 0.40),
    "cagr_ni5": (-0.5, 1.0), "cagr_rev5": (-0.5, 1.0),
    "exp_rev2": (-0.5, 0.6), "exp_rev5": (-0.5, 0.6), "exp_eps5": (-0.5, 0.6),
    "capex_deprecn": (0.0, 6.0), "netcapex_sales": (-0.2, 0.5), "sales_ic": (0.0, 20.0),
}
_REGION_FALLBACK = ["Global", "US"]  # dopo la regione richiesta, in quest'ordine


def _load() -> Dict[str, Any]:
    global _snap_cache
    if _snap_cache is None:
        with io.open(_SNAP_PATH, encoding="utf-8") as f:
            _snap_cache = json.load(f)
    return _snap_cache


def _days_old(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        return (date.today() - datetime.strptime(iso[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return None


def snapshot_meta() -> Dict[str, Any]:
    s = _load()
    return {"built": s.get("_meta", {}).get("built"),
            "erp_asof": s.get("erp", {}).get("asof"),
            "crp_asof": s.get("crp_meta", {}).get("asof"),
            "industries_asof": s.get("industries_meta", {}).get("asof")}


def get_erp() -> Dict[str, Any]:
    """ERP implied mature-market. {'value','asof','source','stale'} — mai None
    (lo snapshot e' nel repo), ma 'stale' va propagato e mostrato."""
    s = _load()
    e = s["erp"]
    age = _days_old(e.get("asof"))
    return {"value": e["value"], "asof": e.get("asof"),
            "source": "Damodaran implied ERP (%s)" % e.get("asof"),
            "stale": bool(age is not None and age > 60), "age_days": age}


def get_crp(country: Optional[str]) -> Optional[Dict[str, Any]]:
    """CRP rating-based del paese. None se il paese non e' nello snapshot:
    il CHIAMANTE dichiara 'paese non mappato' — qui non si inventa nulla."""
    if not country:
        return None
    s = _load()
    # review pre-commit (MEDIA M1): prima il nome RAW (le chiavi vere del file CRP
    # sono 'Taiwan', 'Hong Kong', 'Vietnam', ...), poi l'alias — mai il contrario
    raw = str(country).strip()
    name = raw if raw in s.get("crp", {}) else _COUNTRY_ALIASES.get(raw, raw)
    v = s.get("crp", {}).get(name)
    if v is None:
        return None
    meta = s.get("crp_meta", {})
    age = _days_old(meta.get("asof"))
    return {"value": v, "asof": meta.get("asof"), "country": name,
            "source": "Damodaran CRP %s (%s)" % (meta.get("asof"), meta.get("file")),
            "stale": bool(age is not None and age > 240), "age_days": age}


def get_tax_marginal(country: Optional[str]) -> Optional[Dict[str, Any]]:
    """Aliquota marginale del paese (Tax Foundation via Damodaran). None = non mappato."""
    if not country:
        return None
    s = _load()
    raw = str(country).strip()
    name = raw if raw in s.get("tax_marginal", {}) else _COUNTRY_ALIASES.get(raw, raw)
    v = s.get("tax_marginal", {}).get(name)
    if v is None:
        return None
    return {"value": v, "asof": s.get("tax_meta", {}).get("asof"), "country": name,
            "source": "tax marginale %s (Tax Foundation/Damodaran %s)"
                      % (name, s.get("tax_meta", {}).get("asof"))}


def get_industry_field(industry: Optional[str], field: str,
                       region: str = "US") -> Optional[Dict[str, Any]]:
    """Un campo di una industry Damodaran con fallback di regione DICHIARATO.

    Ritorna {'value','region_used','asof','source','note','stale'} oppure None se
    l'industry non esiste in nessuna regione o il campo e' n.d./degenere ovunque.
    Il vocabolario industry e' quello ESATTO di Damodaran (es. 'Oilfield Svcs/Equip.').
    """
    if not industry:
        return None
    s = _load()
    lo, hi = _FIELD_BOUNDS.get(field, (float("-inf"), float("inf")))
    tried = []
    order = [region] + [r for r in _REGION_FALLBACK if r != region]
    for reg in order:
        rec = s.get("industries", {}).get(reg, {}).get(industry)
        if not rec:
            tried.append("%s: industry n.d." % reg)
            continue
        v = rec.get(field)
        if v is None:
            tried.append("%s: campo n.d." % reg)
            continue
        if not (lo <= v <= hi):
            tried.append("%s: %.4g fuori bound [%g,%g]" % (reg, v, lo, hi))
            continue
        meta = s.get("industries_meta", {})
        age = _days_old(meta.get("asof"))
        note = None
        if reg != region:
            note = "fallback regione %s->%s (%s)" % (region, reg, "; ".join(tried))
        return {"value": v, "region_used": reg, "asof": meta.get("asof"),
                "source": "Damodaran %s '%s' %s (%s)" % (field, industry, reg,
                                                         meta.get("asof")),
                "note": note,
                "stale": bool(age is not None and age > 420), "age_days": age}
    return None


_EUROPE = {"Italy", "Germany", "France", "Spain", "United Kingdom", "Switzerland",
           "Netherlands", "Belgium", "Austria", "Portugal", "Ireland", "Luxembourg",
           "Denmark", "Sweden", "Norway", "Finland", "Greece", "Poland", "Czechia",
           "Czech Republic", "Hungary", "Iceland", "Monaco"}
_EMERGING = {"Brazil", "India", "China", "Mexico", "Indonesia", "Turkey", "South Africa",
             "Argentina", "Chile", "Colombia", "Peru", "Thailand", "Malaysia",
             "Philippines", "Viet Nam", "Vietnam", "Egypt", "Nigeria", "Saudi Arabia",
             "United Arab Emirates", "Korea", "South Korea", "Taiwan", "Taiwan, China"}


def region_for_country(country: Optional[str]) -> str:
    """Regione Damodaran della SOCIETA' (non del profilo): la stessa industry vale
    beta diversi per un titolo USA e uno europeo. Default dichiarabile: Global."""
    c = str(country or "").strip()
    if c in ("United States", "Canada"):
        return "US"
    if c in _EUROPE:
        return "Europe"
    if c == "Japan":
        return "Japan"
    if c in _EMERGING:
        return "Emerging"
    return "Global"


def get_rf_static(currency: Optional[str]) -> Optional[Dict[str, Any]]:
    """rf statico di ULTIMA istanza, rinfrescato da tools/ops/aggiorna_damodaran.py.
    None se la valuta non c'e': il canale rf decide e DICHIARA."""
    if not currency:
        return None
    s = _load()
    rec = s.get("rf_static", {}).get(str(currency).upper())
    if not rec:
        return None
    return {"value": rec["value"], "asof": rec.get("asof"),
            "source": "static (snapshot %s, fonte %s)"
                      % (s.get("rf_static_meta", {}).get("refreshed"), rec.get("source"))}
