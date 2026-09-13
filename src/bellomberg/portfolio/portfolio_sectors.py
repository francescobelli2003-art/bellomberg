"""
portfolio_sectors.py — Esposizione SETTORIALE del book (Quant fase 1a, 23/07,
audit/20 rosa; prerequisito della Brinson attribution).

Prima non esisteva NESSUNA vista per settore (grep `sector` su analytics/risk:
zero — voce Quant §E): la concentrazione tematica del book era invisibile.

Regole (no-fallback 14/07):
- settore = yfinance `.info` sector/industry SOLO su cache-miss, cache su disco
  `data/sector_cache.json` TTL 30gg (i settori non cambiano ogni settimana);
- ETF/panieri e veicoli: yfinance NON ha un settore sensato → OVERRIDE
  DICHIARATI (source='override'), mai un settore inventato. Dal 05/09 (lotto 2b)
  gli override (`settore_tema`) e i bucket economici (`bucket_economico`) sono
  VISTE del negozio dei veicoli `data/veicoli.json`, rilette una volta per
  operazione; gli export snapshot restano per compatibilita'. Negozio assente o
  illeggibile = nessun override, DICHIARATO nelle note del payload;
- DAT: il settore di LISTINO (Technology/Software) e' fuorviante —
  l'esposizione economica e' il sottostante crypto: override dichiarato
  'Crypto treasury (...)'. E' la stessa filosofia del look-through FX;
- ticker senza settore da nessuna fonte → bucket "n.d." DICHIARATO nel payload
  (mai spalmato altrove, mai escluso in silenzio);
- il modulo CONFRONTA la mappa con `sizing_engine.SECTOR_OF` (mappa MANUALE di
  policy per i cap) e dichiara le divergenze: due mappe che divergono zitte
  sono la classe di bug _infer_currency (F-16).

Cash: ESCLUSO dai pesi (esposizione = capitale investito), dichiarato nel payload.
"""
from bellomberg.core.presentation import message as _message, render_payload, join_messages

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import bellomberg.storage.classificazione as cl
from bellomberg.core.paths import DATA_DIR

CACHE_PATH = str(DATA_DIR / "sector_cache.json")
CACHE_TTL_S = 30 * 86400   # 30 giorni

# OVERRIDE DICHIARATI (source='override' nel payload) — ETF/veicoli/DAT del book:
# yfinance ha sector vuoto o di listino per questi nomi. La mappa e' il campo
# `settore_tema` del negozio dei veicoli (tema dell'ETF, NON un settore GICS).
#
# ASSE ECONOMICO UNICO (fase 1b, chiude review MEDIA-4 fase 1a): ogni strumento
# in UN bucket GICS-like, cosi' l'aggregazione cross-strumento (ETF settoriale +
# single-stock dello stesso settore) diventa visibile e l'HHI smette di
# mescolare temi-ETF e GICS. Regole DICHIARATE:
# - single-stock: bucket = settore GICS yfinance (NON nel negozio);
# - ETF SETTORIALE/tematico: settore economico equivalente [src: tema dell'ETF];
# - ETF PAESE/broad e holding: panieri multi-settore -> bucket dichiarato a se'
#   (prefisso _MULTI_PREFIX), MAI spalmati su un settore senza le holdings;
# - DAT/ETN: esposizione economica del sottostante (stessa tesi look-through).
# La mappa e' il campo `bucket_economico` del negozio dei veicoli.
# review 1b (BASSA-4): prefisso dei bucket-paniere come costante condivisa tra
# mappa e filtro (un refuso nell'etichetta uscirebbe dal conteggio in silenzio).
_MULTI_PREFIX = "Multi-settore"


def settori_tema_del_negozio(negozio=None) -> Dict[str, str]:
    """La VISTA che sostituisce SECTOR_OVERRIDES cablato: {TICKER: settore_tema} per le voci
    che lo dichiarano. Negozio assente/illeggibile = dict vuoto (motivo in carica_veicoli)."""
    n = negozio if negozio is not None else cl.carica_veicoli()
    return {t: v["settore_tema"] for t, v in n["veicoli"].items() if v["settore_tema"]}


def bucket_economici_del_negozio(negozio=None) -> Dict[str, str]:
    """La VISTA che sostituisce ECON_BUCKET_OF cablato: {TICKER: bucket_economico}."""
    n = negozio if negozio is not None else cl.carica_veicoli()
    return {t: v["bucket_economico"] for t, v in n["veicoli"].items() if v["bucket_economico"]}


# Export di compatibilita' per strumenti di fotografia/import legacy. Le operazioni
# pubbliche rileggono il negozio e passano lo stesso esito a tutti gli helper.
NEGOZIO_ESITO = cl.carica_veicoli()
SECTOR_OVERRIDES = settori_tema_del_negozio(NEGOZIO_ESITO)
ECON_BUCKET_OF = bucket_economici_del_negozio(NEGOZIO_ESITO)


def nota_negozio(esito=None) -> Optional[str]:
    """La frase che esposizione E attribution mettono nelle note quando il negozio dei veicoli
    e' assente/illeggibile nell'operazione: un consumatore solo, una frase sola."""
    e = esito if esito is not None else cl.carica_veicoli()
    if e["origine"] not in ("assente", "illeggibile"):
        return None
    return (_message("negozio dei veicoli {origin} nell'operazione ({reason}): nessun override tematico ne' bucket economico dichiarato — ETF, veicoli e DAT escono col settore di listino di Yahoo o n.d., NON con la loro esposizione (dichiarato, regola 14/07)", "Vehicle store {origin} for this operation ({reason}): no thematic override or economic bucket declared — ETFs, vehicles and DAT show Yahoo listing sector or n/a, NOT their exposure (declared, rule 14/07)", origin=e["origine"].upper(), reason=e["motivo"]))


def econ_bucket_for(tk: str, ent: Optional[Dict[str, Any]],
                    negozio=None) -> Tuple[str, Optional[str]]:
    """Bucket economico (ASSE UNICO, fase 1b) per un ticker data la sua entry di
    get_sector_map. Ritorna (bucket, anomalia):
      anomalia = 'no_bucket'        override tematico SENZA voce econ -> n.d. dichiarato
               | 'shadowed:<gics>'  voce manuale che maschera un GICS vero (classe F-16)
               | None               nessuna anomalia.
    Funzione UNICA usata da esposizione (compute_sector_exposure) e attribution
    (portfolio_attribution): due consumatori, un solo asse — mai divergenze zitte."""
    tku = (tk or "").upper()
    ent = ent or {}
    econ_bucket_of = bucket_economici_del_negozio(negozio)
    if tku in econ_bucket_of:
        eb = econ_bucket_of[tku]
        # review 1b (MEDIA-1): la mappa econ deve coprire SOLO gli override
        # tematici. Un ticker con GICS vero che finisce qui con voce diversa
        # mascherebbe la fonte vera in silenzio: dichiarato al chiamante.
        if ent.get("source") != "override" and ent.get("sector") \
                and ent["sector"] != eb:
            return eb, "shadowed:" + str(ent["sector"])
        return eb, None
    if ent.get("source") == "override":
        # riusare l'etichetta tematica rimescolerebbe gli assi (vizio MEDIA-4)
        return "n.d.", "no_bucket"
    if ent.get("sector"):
        return ent["sector"], None    # single-stock: GICS = bucket economico
    return "n.d.", None               # fetch fallito: n.d. dichiarato


def _log(msg: str):
    try:
        print(f"[SECTORS] {msg}", flush=True)
    except OSError:
        pass


def _load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception as e:
        _log(f"cache non scritta ({e}): si rifetcha la prossima volta")


def _fetch_yf_sector(ticker: str) -> Dict[str, Optional[str]]:
    """Fetch sector/industry da yfinance. Errore/assente = None DICHIARATO."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return {"sector": info.get("sector") or None,
                "industry": info.get("industry") or None}
    except Exception:
        return {"sector": None, "industry": None}


def get_sector_map(tickers: List[str], fetch=None, negozio=None) -> Dict[str, Dict[str, Any]]:
    """{ticker: {sector, industry, source, asof}} — override > cache > yfinance.
    `fetch` e `negozio` sono iniettabili; senza negozio ne legge uno una volta."""
    fetch = fetch or _fetch_yf_sector
    n = negozio if negozio is not None else cl.carica_veicoli()
    sector_overrides = settori_tema_del_negozio(n)
    cache = _load_cache()
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    dirty = False
    for tk in tickers:
        tku = (tk or "").upper()
        if tku in sector_overrides:
            out[tku] = {"sector": sector_overrides[tku], "industry": None,
                        "source": "override", "asof": None}
            continue
        ent = cache.get(tku)
        if ent and (now - float(ent.get("ts") or 0)) < CACHE_TTL_S:
            out[tku] = {"sector": ent.get("sector"), "industry": ent.get("industry"),
                        "source": "cache(yfinance)", "asof": ent.get("asof")}
            continue
        got = fetch(tku)
        asof = datetime.now().isoformat(timespec="seconds")
        # review fase 1a (MEDIA-2): un fetch FALLITO (None) NON si cachea — prima
        # un timeout transitorio diventava "n.d. per 30 giorni" e un refetch
        # fallito SOVRASCRIVEVA un valore buono con null. Ora: null mai in cache
        # (retry al prossimo giro); se esiste un valore vecchio scaduto, si tiene
        # DICHIARATO come STALE invece di perderlo.
        if got.get("sector") is None:
            if ent and ent.get("sector"):
                out[tku] = {"sector": ent.get("sector"), "industry": ent.get("industry"),
                            "source": "cache(yfinance) STALE (refetch fallito, dichiarato)",
                            "source_note": _message("Refetch fallito: cache STALE dichiarata", "Refetch failed: STALE cache declared"),
                            "asof": ent.get("asof")}
            else:
                out[tku] = {"sector": None, "industry": None,
                            "source": "yfinance (fetch fallito/assente: non cacheato, retry al prossimo giro)",
                            "source_note": _message("Fetch fallito/assente: non cacheato, nuovo tentativo al prossimo giro", "Fetch failed/missing: not cached, retry on next call"),
                            "asof": asof}
            continue
        cache[tku] = {"sector": got.get("sector"), "industry": got.get("industry"),
                      "ts": now, "asof": asof}
        dirty = True
        out[tku] = {"sector": got.get("sector"), "industry": got.get("industry"),
                    "source": "yfinance", "asof": asof}
    if dirty:
        _save_cache(cache)
    return out


# compatibilita' policy-sizing -> settore atteso (keyword nel sector lowercase).
# Tabella ESPLICITA per non fare rumore sui casi ovvi (banks vs "Financial
# Services" e' compatibile, non una divergenza).
_POLICY_COMPAT = {
    "banks": ("financial",),
    "semis": ("semiconductor", "technology"),
    "clean_energy": ("energy", "technology", "solar"),
    "energy_services": ("energy",),
    "crypto_dat": ("crypto treasury",),
    "fintech": ("financial", "technology"),
    "tech": ("technology", "communication"),
    "steel": ("basic materials", "steel"),
}


def _sizing_map_divergences(sector_map: Dict[str, Dict[str, Any]]) -> List[str]:
    """Confronto DICHIARATO con la mappa manuale di policy del sizing (SECTOR_OF):
    non risolve (sono due assi diversi: policy cap vs esposizione), ma un ticker
    che il sizing chiama 'banks' e la mappa 'Technology' va visto, non taciuto."""
    notes = []
    try:
        from bellomberg.portfolio.sizing_engine import SECTOR_OF
    except Exception:
        return [_message('sizing_engine.SECTOR_OF non importabile: confronto n.d.', 'Cannot import sizing_engine.SECTOR_OF: comparison unavailable')]
    for tk, policy_sector in SECTOR_OF.items():
        got = sector_map.get(tk.upper())
        if not got:
            continue
        s = (got.get("sector") or "").lower()
        if not s:
            continue   # settore n.d.: gia' dichiarato nel bucket, niente doppio rumore
        compat = _POLICY_COMPAT.get(policy_sector, (policy_sector.split("_")[0].lower(),))
        if not any(kw in s for kw in compat):
            notes.append(_message("{v0}: sizing policy '{v1}' vs mappa '{v2}' [{v3}] — divergenza dichiarata, verificare", "{v0}: sizing policy '{v1}' vs map '{v2}' [{v3}] — declared divergence, verify", v0=tk, v1=policy_sector, v2=got.get('sector'), v3=got.get('source')))
    return notes


def compute_sector_exposure(summary: Optional[Dict[str, Any]] = None,
                            fetch=None) -> Dict[str, Any]:
    """Esposizione del book per settore, pesi sul valore di mercato EUR.
    summary iniettabile per i test (default: MemoryDB().get_portfolio_summary())."""
    if summary is None:
        from bellomberg.storage.memory_db import MemoryDB
        summary = MemoryDB().get_portfolio_summary()
    positions = summary.get("positions") or []
    if not positions:
        return {"error": _message("nessuna posizione", "No positions"), "timestamp": datetime.now().isoformat()}
    if summary.get("fx_incomplete"):
        return {"error": _message("FX incompleto: esposizione settoriale EUR n.d. ({currencies})", "Incomplete FX: EUR sector exposure unavailable ({currencies})", currencies=", ".join(summary["fx_incomplete"])),
                "timestamp": datetime.now().isoformat()}
    fx_inc = None

    negozio = cl.carica_veicoli()
    smap = get_sector_map([p["ticker"] for p in positions], fetch=fetch,
                          negozio=negozio)
    buckets: Dict[str, Dict[str, Any]] = {}
    total = 0.0
    nd_tickers = []
    for p in positions:
        tk = (p.get("ticker") or "").upper()
        val = float(p.get("valore_mercato") or 0)   # gia' in EUR nel summary
        total += val
        sec = (smap.get(tk) or {}).get("sector")
        if not sec:
            sec = "n.d."
            nd_tickers.append(tk)
        b = buckets.setdefault(sec, {"value_eur": 0.0, "tickers": []})
        b["value_eur"] += val
        b["tickers"].append(tk)

    by_sector = []
    hhi = 0.0
    for sec, b in buckets.items():
        w = (b["value_eur"] / total * 100.0) if total > 0 else 0.0
        hhi += (w / 100.0) ** 2
        by_sector.append({"sector": sec, "weight_pct": round(w, 2),
                          "value_eur": round(b["value_eur"], 2),
                          "n_positions": len(b["tickers"]),
                          "tickers": sorted(b["tickers"])})
    by_sector.sort(key=lambda x: -x["weight_pct"])

    # --- ASSE ECONOMICO UNICO (fase 1b): un solo asse per HHI e attribution ---
    econ_buckets: Dict[str, Dict[str, Any]] = {}
    econ_no_bucket: List[str] = []   # override tematico SENZA voce in ECON_BUCKET_OF
    econ_shadowed: List[str] = []    # voce econ manuale che maschera un GICS vero
    for p in positions:
        tk = (p.get("ticker") or "").upper()
        val = float(p.get("valore_mercato") or 0)
        # logica di assegnazione ESTRATTA in econ_bucket_for (lotto 2): stessa
        # funzione per esposizione e attribution, mai due assi che divergono.
        eb, anom = econ_bucket_for(tk, smap.get(tk), negozio=negozio)
        if anom == "no_bucket":
            econ_no_bucket.append(tk)
        elif anom and anom.startswith("shadowed:"):
            econ_shadowed.append(_message("{v0}: voce manuale '{v1}' vs GICS '{v2}'", "{v0}: manual entry '{v1}' vs GICS '{v2}'", v0=tk, v1=eb, v2=anom.split(':', 1)[1]))
        b = econ_buckets.setdefault(eb, {"value_eur": 0.0, "tickers": []})
        b["value_eur"] += val
        b["tickers"].append(tk)

    by_econ = []
    hhi_econ = 0.0
    for eb, b in econ_buckets.items():
        w = (b["value_eur"] / total * 100.0) if total > 0 else 0.0
        hhi_econ += (w / 100.0) ** 2
        by_econ.append({"bucket": eb, "weight_pct": round(w, 2),
                        "value_eur": round(b["value_eur"], 2),
                        "n_positions": len(b["tickers"]),
                        "tickers": sorted(b["tickers"])})
    by_econ.sort(key=lambda x: -x["weight_pct"])
    econ_nd_val = (econ_buckets.get("n.d.") or {}).get("value_eur", 0.0)
    # review 1b (BASSA-3): percentuale dai valori GREZZI, non dai pesi arrotondati
    multi_val = sum(b["value_eur"] for eb, b in econ_buckets.items()
                    if eb.startswith(_MULTI_PREFIX))
    multi_pct = round(multi_val / total * 100.0, 2) if total > 0 else 0.0

    notes = []
    nota_n = nota_negozio(negozio)
    if nota_n:
        notes.append(nota_n)
    if econ_no_bucket:
        notes.append(_message("asse unico: override tematico SENZA bucket economico per {tickers} -> n.d. dichiarato (dichiarare bucket_economico nella voce del negozio dei veicoli)", "Single axis: thematic override WITHOUT economic bucket for {tickers} -> explicitly unavailable (declare bucket_economico in the vehicle-store entry)", tickers=", ".join(sorted(econ_no_bucket))))
    if econ_shadowed:
        notes.append(_message("asse unico: il bucket_economico del negozio DIVERGE dal GICS vero per {tickers} — divergenza dichiarata, verificare la mappa (classe F-16)", "Single axis: store bucket_economico DIVERGES from actual GICS for {tickers} — declared divergence, verify the map (F-16 class)", tickers=join_messages("; ", sorted(econ_shadowed))))
    if nd_tickers:
        notes.append(_message("settore n.d. per {tickers} — bucket dichiarato, NON spalmato altrove", "Sector unavailable for {tickers} — explicit bucket, NOT allocated elsewhere", tickers=", ".join(sorted(nd_tickers))))
    stale = summary.get("stale_positions")
    if stale:
        notes.append(_message("prezzi STALE (valore al costo) per {tickers}: i pesi di quei nomi sono al carico, dichiarato", "STALE prices (carried at cost) for {tickers}: those holdings are weighted at cost, as declared", tickers=", ".join(stale)))
    notes.extend(_sizing_map_divergences(smap))
    # review fase 1a (MEDIA-3): scope del confronto + single-stock senza policy
    # di settore nel sizing (= NESSUN cap settoriale li vincola) — dichiarato.
    try:
        from bellomberg.portfolio.sizing_engine import SECTOR_OF
        single_no_cap = sorted(tk for tk, v in smap.items()
                               if v.get("source", "").find("override") < 0
                               and v.get("sector") and tk not in SECTOR_OF)
        if single_no_cap:
            notes.append(_message("single-stock SENZA policy di settore nel sizing (nessun cap settoriale li vincola): {tickers} — confronto policy limitato ai nomi in book", "Single stocks WITHOUT a sizing sector policy (no sector cap applies): {tickers} — policy comparison limited to held names", tickers=", ".join(single_no_cap)))
    except Exception:
        pass

    return {
        "by_sector": by_sector,
        "hhi_sector": round(hhi, 4),
        "effective_n_sectors": round(1.0 / hhi, 2) if hhi > 0 else None,
        "econ_axis": {
            "by_bucket": by_econ,
            "hhi": round(hhi_econ, 4),
            "effective_n": round(1.0 / hhi_econ, 2) if hhi_econ > 0 else None,
            "coverage_pct": round((total - econ_nd_val) / total * 100.0, 2) if total > 0 else 0.0,
            "multi_sector_weight_pct": multi_pct,
            # review 1b (MEDIA-2): il degrado FX si dichiara ANCHE qui — questo
            # blocco verra' letto da solo dall'attribution, non deve autodescriversi
            # pulito con pesi degradati.
            "basis": _message("{basis}{suffix}", "{basis}{suffix}", basis=_message("ASSE UNICO (fase 1b): single-stock = settore GICS; ETF settoriali mappati al settore economico equivalente (bucket_economico del negozio dei veicoli, dichiarato); panieri paese/EM e holding = bucket 'Multi-settore' a se', MAI spalmati senza le holdings; DAT/ETN = sottostante (Crypto/oro). Qui l'HHI NON mescola temi e GICS (chiude review MEDIA-4 fase 1a).", "SINGLE AXIS (phase 1b): single stock = GICS sector; sector ETFs mapped to the equivalent economic sector (vehicle-store bucket_economico, declared); country/EM baskets and holdings = separate 'Multi-settore' bucket, NEVER allocated without holdings; DAT/ETN = underlying (Crypto/gold). Here HHI does NOT mix themes and GICS (closes phase 1a MEDIA-4 review)."), suffix=_message(" [DEGRADATO: FX incompleto per {currencies}]", " [DEGRADED: incomplete FX for {currencies}]", currencies=", ".join(fx_inc))
                        if fx_inc else ""),
        },
        "coverage_pct": round((total - sum(b["value_eur"] for s, b in buckets.items()
                                           if s == "n.d.")) / total * 100.0, 2) if total > 0 else 0.0,
        "basis": _message("{basis}{suffix}", "{basis}{suffix}", basis=_message("pesi sul valore di mercato EUR del summary (cash ESCLUSO: esposizione del capitale investito); ETF/veicoli/DAT con override DICHIARATI (tema, non settore GICS) — NB: hhi_sector mescola i due assi (ogni ETF e' un bucket a se': la concentrazione TEMATICA cross-ETF, es. 4 ETF minerari, non e' catturata dall'HHI — per l'asse unico vedi econ_axis, fase 1b)", 'Weights on EUR market value from summary (cash EXCLUDED: invested capital exposure); ETFs/vehicles/DAT with DECLARED overrides (theme, not GICS sector) — NB: hhi_sector mixes both axes (each ETF is a separate bucket: cross-ETF THEMATIC concentration, e.g. 4 mining ETFs, is not captured by HHI — see econ_axis, phase 1b, for the single axis)'), suffix=_message(" [DEGRADATO: FX incompleto per {currencies}]", " [DEGRADED: incomplete FX for {currencies}]", currencies=", ".join(fx_inc))
                    if fx_inc else ""),
        "sector_sources": {tk: {"sector": v.get("sector"), "source": v.get("source"), "source_note": v.get("source_note")}
                           for tk, v in smap.items()},
        "notes": notes,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "_source": "portfolio_sectors.compute_sector_exposure",
    }


if __name__ == "__main__":
    out = compute_sector_exposure()
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
