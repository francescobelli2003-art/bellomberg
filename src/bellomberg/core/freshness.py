"""
freshness.py — Rilevatore dati stantii (audit/07 §2-3, P1; regola PM 14/07: MAI
fallback silenziosi, sempre precisione dichiarata).

Il caso che l'ha reso necessario: il funding perp "+10,95%" citato IDENTICO al
centesimo nei memo #36/#39/#42 (fermo da settimane) e il "DXY" 120,69 del #42 che
era un dato FRED di 11 giorni prima presentato come corrente e "in risalita".

Meccanica: a ogni run i valori chiave dei dati esterni vengono confrontati con lo
snapshot della run precedente (data/freshness_snapshot.json — file JSON, NIENTE
tabelle nuove nel DB):
  - valore IDENTICO da piu' di IDENTICAL_DAYS giorni  -> STALE (fonte ferma/cachata)
  - osservazione piu' vecchia del limite per la serie -> STALE (dato non corrente)
I limiti di osservazione sono per FREQUENZA della serie (una CPI mensile di 30
giorni e' normale, un VIX di 6 giorni no).
Lo snapshot si aggiorna SEMPRE (anche per i dati sani), cosi' il confronto e'
sempre con l'ultima run reale.
"""
import json
import os
from datetime import date
from bellomberg.core.paths import DATA_DIR

SNAP_PATH = str(DATA_DIR / "freshness_snapshot.json")
IDENTICAL_DAYS = 7          # valore identico da > N giorni = fonte ferma
_OBS_LIMITS = (             # (keyword nel nome serie, giorni max di osservazione)
    ("gdp", 130),                       # trimestrale
    ("cpi", 45), ("unemployment", 45), ("retail", 45), ("industrial", 45),
    ("housing", 45), ("ism", 45), ("export", 45), ("euribor", 45),
    ("bund", 45), ("gilt", 45), ("jgb", 45), ("discount", 45), ("selic", 45),
    ("call_rate", 45),          # boj_call_rate: mensile OCSE (P1 14/07, ex discount rate)
    ("3m_rate", 45), ("boe", 45), ("fed_funds", 45),  # mensili OCSE/FRED (FEDFUNDS e' mensile)
    ("claims", 12),                     # settimanale
    ("wti", 12),                        # DCOILWTICO: FRED pubblica con ~1 settimana di lag
)
OBS_DEFAULT_DAYS = 6                    # serie giornaliere (VIX, tassi, FX, oil)


def _obs_limit(key: str) -> int:
    k = key.lower()
    for kw, days in _OBS_LIMITS:
        if kw in k:
            return days
    return OBS_DEFAULT_DAYS


def _load() -> dict:
    try:
        with open(SNAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(snap: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
        tmp = SNAP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SNAP_PATH)
    except Exception as e:
        print("[FRESHNESS] save failed: " + str(e), flush=True)


def check_and_update(current: dict, today: date = None) -> dict:
    """current: {chiave: {"value": num/str, "obs_date": "YYYY-MM-DD"|None}}.
    Ritorna {"stale": [testo...], "fresh": n, "checked": n} e aggiorna lo snapshot."""
    today = today or date.today()
    snap = _load()
    stale = []
    fresh = 0
    for key, cur in sorted(current.items()):
        val = cur.get("value")
        if val is None:
            continue
        sval = repr(val)
        prev = snap.get(key) or {}
        first_seen = today.isoformat()
        if prev.get("value") == sval and prev.get("first_seen"):
            first_seen = prev["first_seen"]
        reasons = []
        try:
            same_days = (today - date.fromisoformat(first_seen)).days
        except Exception:
            same_days = 0
        # soglia "identico" per FREQUENZA: una CPI mensile identica da 10 giorni e'
        # normale (nuova stampa una volta al mese), un funding live fermo da 10 no.
        _ident_lim = max(IDENTICAL_DAYS, _obs_limit(key))
        if prev.get("value") == sval and same_days > _ident_lim:
            reasons.append(f"valore IDENTICO da {same_days} giorni ({val}): fonte ferma o cachata")
        od = cur.get("obs_date")
        if od:
            try:
                obs_age = (today - date.fromisoformat(str(od)[:10])).days
                lim = _obs_limit(key)
                if obs_age > lim:
                    reasons.append(f"osservazione del {str(od)[:10]} = {obs_age} giorni fa "
                                   f"(limite {lim} per questa serie)")
            except Exception:
                pass
        if reasons:
            stale.append(key + ": " + "; ".join(reasons))
        else:
            fresh += 1
        snap[key] = {"value": sval, "obs_date": (str(od)[:10] if od else None),
                     "first_seen": first_seen, "last_run": today.isoformat()}
    _save(snap)
    return {"stale": stale, "fresh": fresh, "checked": len(current)}


def format_for_memo(report: dict):
    """Blocco markdown appeso al MEMO dal codice (21/07: i 24 STALE del #46 erano
    nel prompt del Capo con obbligo di dichiarazione, ma nel memo non ne compariva
    NESSUNO — la dichiarazione non puo' dipendere dalla disciplina dell'LLM: la
    appende il codice, come linter e validator). Stringa vuota se tutto fresco."""
    if not report:
        return ""
    stale = report.get("stale") or []
    if not stale:
        return ""
    head = "## QUALITA' DATI (freshness check — blocco automatico, appeso dal codice)"
    lines = ["- " + s for s in stale]
    foot = ("*({} serie esterne controllate: {} fresche, {} STALE. Le cifre del memo "
            "basate sui dati sopra valgono alla data di osservazione indicata, non a "
            "oggi.)*".format(report.get("checked", "?"), report.get("fresh", "?"),
                             len(stale)))
    return "\n".join([head] + lines + [foot])


def format_for_capo(report: dict):
    """Blocco per il prompt del Capo. None se non c'e' nulla di stantio."""
    if not report or not report.get("stale"):
        return None
    return ("\n\n=== FRESHNESS CHECK: DATI STANTII RILEVATI ===\n- "
            + "\n- ".join(report["stale"])
            + "\nREGOLA (PM): questi dati NON sono correnti. Nel memo si usano SOLO "
              "dichiarando la data/eta' di osservazione; VIETATO presentarli come "
              "ricerca corrente o descriverne la 'direzione' (es. 'in risalita') "
              "sulla base di un valore fermo.")
