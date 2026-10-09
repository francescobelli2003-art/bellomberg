"""
freshness.py — Rilevatore dati stantii (audit/07 §2-3, P1; regola PM 14/07: MAI
fallback silenziosi, sempre precisione dichiarata).

Il caso che l'ha reso necessario: il funding perp "+10,95%" citato IDENTICO al
centesimo nei memo #36/#39/#42 (fermo da settimane) e il "DXY" 120,69 del #42 che
era un dato FRED di 11 giorni prima presentato come corrente e "in risalita".

Meccanica: a ogni run i valori chiave dei dati esterni vengono confrontati con lo
snapshot della run precedente (data/freshness_snapshot.json — file JSON, NIENTE
tabelle nuove nel DB):
  - valore IDENTICO: non dimostra da solo che la fonte sia ferma
  - osservazione piu' vecchia del limite per la serie -> STALE (dato non corrente)
  - data assente, invalida o futura / valore assente o invalido -> freschezza n.d.
I limiti di osservazione sono per FREQUENZA della serie (una CPI mensile di 30
giorni e' normale, un VIX di 6 giorni no).
Lo snapshot si aggiorna SEMPRE (anche per i dati sani), cosi' il confronto e'
sempre con l'ultima run reale.

Il contratto nuovo release-freshness/1 e' separato: classificazione pura con
cutoff congelato e calendario attestato. L'eta' non dimostra una release persa;
i chiamanti storici mantengono la meccanica e i formatter senza schema.
"""
import json
import math
import os
from copy import deepcopy
from datetime import date, datetime, timezone
from bellomberg.core.paths import DATA_DIR

SNAP_PATH = str(DATA_DIR / "freshness_snapshot.json")
IDENTICAL_DAYS = 7          # compatibilita': identicita' da sola non prova STALE
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

RELEASE_SCHEMA = "release-freshness/1"


def _release_day(value):
    """A calendar date, never an inferred release from a FRED vintage interval."""
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError("data ISO assente")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("data non ISO YYYY-MM-DD")
    return parsed


def project_macro_observation(entry: dict) -> dict:
    """Project a newly acquired dashboard entry, retaining missing evidence as missing.

    The weekly caller selects this projection only for its frozen release policy.
    Native/legacy sources without publication metadata remain explicitly UNKNOWN.
    """
    metadata = entry.get("release_metadata")
    cur = deepcopy(metadata) if isinstance(metadata, dict) else {}
    cur.update(value=entry.get("value"), obs_date=entry.get("date"))
    cur.setdefault("observation_period", entry.get("date"))
    if entry.get("error"):
        cur["source_error"] = str(entry["error"])
    return cur


def _release_observation(cur: dict, as_of: date) -> dict:
    """Classify evidence available on the cutoff day, with no I/O or wall clock."""
    out = {key: deepcopy(cur.get(key)) for key in (
        "series_id", "observation_period", "release_date", "retrieved_at", "next_expected_release")}
    out.update(status="UNKNOWN", age_days=None, calendar_as_of=None,
               latest_release_date=None, latest_observation_period=None, calendar_source=None)

    def verdict(reason, status="UNKNOWN"):
        out.update(status=status, reason=reason)
        return out

    try:
        period = _release_day(out["observation_period"])
        out["age_days"] = (as_of - period).days
        if period > as_of:
            return verdict("periodo di osservazione futuro rispetto al cutoff")
        if cur.get("obs_date") is not None and _release_day(cur["obs_date"]) != period:
            return verdict("periodo di osservazione in conflitto con la data del valore")
    except (ValueError, TypeError):
        return verdict("periodo di osservazione assente o invalido")
    try:
        value = cur.get("value")
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError("valore invalido")
    except (ValueError, TypeError, OverflowError):
        return verdict("valore assente, non numerico o non finito")
    if cur.get("source_error"):
        return verdict("fonte in errore: " + str(cur["source_error"]))
    try:
        retrieved = datetime.fromisoformat(out["retrieved_at"])
        if retrieved.tzinfo is None or retrieved.utcoffset() is None:
            raise ValueError("fuso assente")
        retrieved_day = retrieved.astimezone(timezone.utc).date()
        if retrieved_day > as_of:
            return verdict("acquisizione futura rispetto al cutoff")
    except (ValueError, TypeError):
        return verdict("data acquisizione assente o invalida: richiesto timestamp con fuso")
    try:
        expected = (_release_day(out["next_expected_release"])
                    if out["next_expected_release"] is not None else None)
    except (ValueError, TypeError):
        return verdict("data della prossima pubblicazione attesa invalida")
    try:
        released = _release_day(out["release_date"])
        if released > as_of or released > retrieved_day or released < period:
            return verdict("data di pubblicazione incompatibile con periodo, acquisizione o cutoff")
    except (ValueError, TypeError):
        return verdict("data di pubblicazione assente o invalida; acquisizione e vintage non la attestano")
    calendar = cur.get("release_calendar")
    if not isinstance(calendar, dict) or calendar.get("status") != "verified":
        if expected is not None and expected <= as_of:
            return verdict("pubblicazione attesa ma esito e calendario non verificati",
                           "RELEASE_EXPECTED_UNCONFIRMED")
        return verdict("calendario non verificato: l'eta' del periodo non prova una release persa")
    out["calendar_source"] = calendar.get("source")
    out["calendar_as_of"] = calendar.get("as_of")
    out["latest_release_date"] = calendar.get("latest_release_date")
    out["latest_observation_period"] = calendar.get("latest_observation_period")
    if (not isinstance(calendar.get("source"), str) or not calendar["source"].strip()
            or not isinstance(cur.get("series_id"), str) or not cur["series_id"].strip()
            or calendar.get("series_id") != cur["series_id"]):
        return verdict("calendario privo di fonte o non attestato per la stessa serie")
    try:
        verified_day = _release_day(calendar.get("as_of"))
        latest_release = _release_day(calendar.get("latest_release_date"))
        latest_period = _release_day(calendar.get("latest_observation_period"))
    except (ValueError, TypeError):
        return verdict("date del calendario verificate assenti o invalide")
    if (verified_day > as_of or latest_release > verified_day or latest_period > latest_release
            or latest_release < released or latest_period < period):
        return verdict("calendario futuro o incoerente con la pubblicazione disponibile")
    if latest_release > released or latest_period > period:
        return verdict("pubblicazione successiva attestata nel calendario e assente dal dato",
                       "SUPERSEDED")
    if expected is not None and expected <= as_of:
        return verdict("pubblicazione attesa: l'esito non e' attestato; possibile rinvio",
                       "RELEASE_EXPECTED_UNCONFIRMED")
    if verified_day < as_of:
        return verdict("calendario verificato solo al " + verified_day.isoformat()
                       + ": non copre il cutoff corrente")
    return verdict("ultima pubblicazione attestata dal calendario al cutoff", "CURRENT_PUBLISHED")


def _release_line(key, row):
    def field(name):
        value = row.get(name)
        return "n.d." if value is None else str(value)
    return (f"{key}: {row['status']} — periodo {field('observation_period')}; "
            f"eta' {field('age_days')} giorni; pubblicazione {field('release_date')}; "
            f"acquisito {field('retrieved_at')}; prossima attesa {field('next_expected_release')}; "
            f"calendario al {field('calendar_as_of')} [fonte: {field('calendar_source')}]; ultima release attestata "
            f"{field('latest_release_date')} (periodo {field('latest_observation_period')}); "
            f"{row['reason']} [src: freshness]")


def check_release_freshness(current: dict, as_of) -> dict:
    """Pure release-freshness/1 report for a frozen, day-precision cutoff.

    Calendar evidence must carry status='verified', a source, the exact series_id,
    as_of and the latest *confirmed* release/observation dates. An expected date
    is only a schedule, not confirmation. Earlier evidence can prove SUPERSEDED;
    CURRENT_PUBLISHED requires coverage through the cutoff. No intraday guarantee.
    Legacy callers keep check_and_update and its historical snapshot/thresholds.
    """
    try:
        cutoff = _release_day(as_of)
    except (ValueError, TypeError) as exc:
        raise ValueError("as_of esplicito richiesto in formato YYYY-MM-DD") from exc
    report = {"schema": RELEASE_SCHEMA, "as_of": cutoff.isoformat(), "precision": "day",
              "stale": [], "unknown": [], "fresh": 0, "checked": len(current), "observations": {}}
    for key, cur in sorted(current.items()):
        row = _release_observation(cur if isinstance(cur, dict) else {}, cutoff)
        report["observations"][key] = row
        if row["status"] == "CURRENT_PUBLISHED":
            report["fresh"] += 1
        elif row["status"] == "SUPERSEDED":
            report["stale"].append(_release_line(key, row))
        else:
            report["unknown"].append(_release_line(key, row))
    return report


def _format_release_report(report, *, for_capo=False):
    if report.get("schema") != RELEASE_SCHEMA:
        raise ValueError("schema freshness non riconosciuto: " + str(report.get("schema")))
    if not report.get("checked"):
        return None if for_capo else ""
    title = ("=== FRESHNESS CHECK: PUBBLICAZIONI ===" if for_capo else
             "## QUALITA' DATI (pubblicazioni — blocco automatico)")
    lines = ["- " + _release_line(key, row) for key, row in report["observations"].items()]
    expected = sum(row["status"] == "RELEASE_EXPECTED_UNCONFIRMED"
                   for row in report["observations"].values())
    foot = (f"Cutoff congelato {report['as_of']} (precisione giornaliera): "
            f"CURRENT_PUBLISHED: {report['fresh']}; SUPERSEDED: {len(report['stale'])}; "
            f"RELEASE_EXPECTED_UNCONFIRMED: {expected}; UNKNOWN: {len(report['unknown']) - expected}. "
            "L'eta' misura il periodo, non dimostra un aggiornamento perso. "
            "Una data attesa non prova la pubblicazione; senza calendario verificato la correntezza "
            "resta n.d. I valori invariati non attestano una fonte ferma. [src: freshness]")
    return "\n".join([title, *lines, foot])


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
    Ritorna stale/unknown (liste), fresh e checked; ogni serie conta una volta.
    La freschezza e' misurata dalla data osservata, non dal cambiamento del valore."""
    today = today or date.today()
    snap = _load()
    stale = []
    unknown = []
    fresh = 0
    for key, cur in sorted(current.items()):
        val = cur.get("value")
        if val is None:
            unknown.append(key + ": n.d. - valore assente")
            continue
        try:
            valid_value = not isinstance(val, bool) and math.isfinite(float(val))
        except (TypeError, ValueError, OverflowError):
            valid_value = False
        if not valid_value:
            unknown.append(key + ": n.d. - valore non numerico o non finito")
            continue
        sval = repr(val)
        prev = snap.get(key) or {}
        first_seen = today.isoformat()
        if prev.get("value") == sval and prev.get("first_seen"):
            first_seen = prev["first_seen"]
        od = cur.get("obs_date")
        unknown_reason = None
        stale_reason = None
        if not od:
            unknown_reason = "data osservazione assente"
        else:
            try:
                # Contratto: YYYY-MM-DD completo. Non troncare un valore invalido.
                od_text = str(od)
                parsed = date.fromisoformat(od_text)
                if parsed.isoformat() != od_text:
                    raise ValueError("data non ISO YYYY-MM-DD")
                obs_age = (today - parsed).days
                lim = _obs_limit(key)
                if obs_age < 0:
                    unknown_reason = "data osservazione futura: " + od_text
                elif obs_age > lim:
                    stale_reason = (f"osservazione del {od_text} = {obs_age} giorni fa "
                                    f"(limite {lim} per questa serie)")
            except (ValueError, TypeError):
                unknown_reason = "data osservazione invalida: " + str(od)
        if unknown_reason:
            unknown.append(key + ": n.d. - " + unknown_reason)
        elif stale_reason:
            stale.append(key + ": " + stale_reason)
        else:
            fresh += 1
        snap[key] = {"value": sval, "obs_date": (str(od) if od else None),
                     "first_seen": first_seen, "last_run": today.isoformat()}
    _save(snap)
    return {"stale": stale, "unknown": unknown, "fresh": fresh, "checked": len(current)}


def format_for_memo(report: dict):
    """Blocco markdown appeso al MEMO dal codice (21/07: i 24 STALE del #46 erano
    nel prompt del Capo con obbligo di dichiarazione, ma nel memo non ne compariva
    NESSUNO — la dichiarazione non puo' dipendere dalla disciplina dell'LLM: la
    appende il codice, come linter e validator). Stringa vuota se tutto fresco."""
    if not report:
        return ""
    if "schema" in report:
        return _format_release_report(report)
    stale = report.get("stale") or []
    unknown = report.get("unknown") or []
    if not stale and not unknown:
        return ""
    head = "## QUALITA' DATI (freshness check — blocco automatico, appeso dal codice)"
    lines = ["- " + s for s in stale + unknown]
    quality = f", {len(unknown)} n.d." if unknown else ""
    foot = ("*({} serie esterne controllate: {} fresche, {} STALE{}. Le cifre del memo "
            "basate sui dati sopra valgono alla data di osservazione indicata, non a "
            "oggi; se n.d., la freschezza non e' verificabile.)*".format(
                report.get("checked", "?"), report.get("fresh", "?"), len(stale), quality))
    return "\n".join([head] + lines + [foot])


def format_for_capo(report: dict):
    """Blocco qualita' per il Capo. None se tutte le date sono fresche."""
    if not report:
        return None
    if "schema" in report:
        return _format_release_report(report, for_capo=True)
    problems = (report.get("stale") or []) + (report.get("unknown") or [])
    if not problems:
        return None
    return ("\n\n=== FRESHNESS CHECK: DATI STALE O FRESCHEZZA N.D. ===\n- "
            + "\n- ".join(problems)
            + "\nREGOLA (PM): questi dati non sono verificati come correnti. Nel memo si usano SOLO "
              "dichiarando la data/eta' di osservazione o la freschezza n.d.; VIETATO presentarli come "
              "ricerca corrente o descriverne la 'direzione' (es. 'in risalita') "
              "sulla base di un valore fermo.")
