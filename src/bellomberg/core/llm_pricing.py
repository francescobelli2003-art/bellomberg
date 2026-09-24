"""
BELLOMBERG - Listino e calcolo costi delle chiamate LLM (Anthropic).

PERCHE' ESISTE
    Il consigliere multi-agente brucia token su piu' modelli (Opus/Sonnet/Haiku) e su
    piu' round: senza un punto unico di verita' sulle tariffe, il costo di una run e'
    invisibile (o, peggio, stimato a occhio). Questo modulo e' l'UNICO posto dove vivono
    le tariffe: chi vuole un costo chiama cost_usd()/cost_eur(), non moltiplica a mano.
    (Il vecchio calcolo in attic/consigliere_v1 aveva le tariffe Opus 4.6 hardcoded nel
    mezzo del loop, solo in USD, e nessuno se n'e' accorto quando i prezzi sono cambiati.)

DA DOVE VENGONO LE TARIFFE
    Documentazione ufficiale Anthropic, listino del 24/06/2026, in USD per MILIONE di
    token. Non sono stime: se un modello non e' in PRICING_USD_PER_MTOK il costo torna
    None con status "model_unknown" (regola no-fallback-silenziosi: mai 0.0, mai la
    tariffa di un modello "simile").

CACHE: IL WRITE DIPENDE DAL TTL
    - cache_read  = input x 0.10  (sempre)
    - cache_write = input x 1.25  se TTL 5 minuti (default Anthropic)
    - cache_write = input x 2.00  se TTL 1 ora
    Per questo cost_usd/cost_eur vogliono cache_ttl esplicito ("5m" | "1h" | None):
    senza TTL non si sa quanto costa scrivere in cache, quindi cache_ttl=None significa
    "nessun caching" e i token di cache vengono dichiarati non applicabili, non prezzati.
    Le tariffe derivate sono CALCOLATE dai moltiplicatori qui sotto: se domani cambia il
    prezzo input, cache read/write si aggiornano da sole.

EUR
    L'FX arriva dal servizio gia' maturo di price_updater (get_fx_to_eur/get_fx_sources).
    Se il tasso e' su fallback statico, fx_source lo DICE ("fallback"); se manca,
    cost=None e fx_source="n.d.".

FX MEMOIZZATO PER PROCESSO (una run = UN cambio)
    price_updater cacha il ramo live ma NON quello fallback, di proposito (price_updater.py
    :97-105, audit/11 §4: il tasso statico non si cacha per ritentare il live al giro dopo).
    Per il PORTAFOGLIO quella scelta e' giusta. Per la CONTABILITA' di una run e' sbagliata
    due volte:
      1. con yfinance irraggiungibile ogni record_usage rifa una fetch di rete che fallisce
         (~20 tentativi in serie per run, 4,14s il primo, 20 warning [FX] nel log);
      2. soprattutto: una run va prezzata a UN cambio unico e coerente, non a 20 cambi
         diversi presi in 40 minuti (il tasso live si muove durante la run).
    Quindi qui memoizziamo (rate, source) a livello MODULO alla PRIMA risoluzione RIUSCITA
    - ramo fallback incluso - e la riusiamo per tutto il processo.
    Il buco resta dichiarato: se il tasso memoizzato viene dal fallback statico, fx_source
    dice "fallback" per SEMPRE su quella run, non "live". Un ripiego che si spaccia per
    live e' esattamente il bug che questa regola evita.
    Il fallimento totale ("n.d.") NON si memoizza: non e' un cambio, e' un buco, e la
    chiamata dopo ha diritto di ritentare.
    reset_fx_memo() azzera la memo (serve ai test; in produzione nessuno la chiama).

PERIMETRO: COSA IL CONTATORE **NON** COPRE (dichiarato, non taciuto)
    Il contatore #38 registra le chiamate degli AGENTI della run (specialisti R0/R1/R2,
    _red_team, capo, _reflection, _action_table). NON copre le chiamate LLM fatte DENTRO
    un tool: oggi il caso noto e' chat_tools.get_news_briefing(force_refresh=true), che
    invoca briefing_engine.generate_briefing() -> una vera messages.create su Haiku
    (briefing_engine.py:386). Il tool sta nel subset di macro/news/eventdesk, quindi uno
    specialista puo' farla partire durante la run: sono cent, ma sono fuori dal totale.
    Scelta del PM 15/07 (perimetro "solo consigliere"): non si insegue dentro le pipeline
    di chat/news/briefing, che hanno una contabilita' loro. Resta SCRITTO qui perche' un
    buco taciuto e' il fallback silenzioso vietato il 14/07: il totale della run e' "il
    costo degli agenti", non "il costo di ogni token acceso dal processo".
    Voce residua a MASTER_TODO per chiuderlo (registrare _briefing come agente laterale).
"""
import threading
import math
from decimal import Decimal, ROUND_CEILING
from typing import Dict, Optional, Tuple

# === LISTINO UFFICIALE (USD per milione di token) - doc Anthropic verificata 26/07/2026 ===
PRICING_USD_PER_MTOK: Dict[str, Dict[str, float]] = {
    # modelli ATTIVI dal 26/07 (PM: opus-4-8 -> opus-5, sonnet-4-6 -> sonnet-5)
    "claude-opus-5":     {"in": 5.00, "out": 25.00},
    # ATTENZIONE sonnet-5: prezzo INTRO valido fino al 31/08/2026 — dal 01/09
    # va portato a {"in": 3.00, "out": 15.00} (voce a MASTER_TODO con scadenza)
    "claude-sonnet-5":   {"in": 2.00, "out": 10.00},
    # modelli precedenti: restano a listino per i log/run storici (mai rimuovere)
    "claude-opus-4-8":   {"in": 5.00, "out": 25.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5":  {"in": 1.00, "out": 5.00},
}

# Alias dated -> id canonico (le model string in giro per il progetto sono anche dated)
MODEL_ALIASES: Dict[str, str] = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
}

# Moltiplicatori sul prezzo INPUT (regola Anthropic). Fonte unica delle tariffe derivate.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = {
    "5m": 1.25,   # TTL default
    "1h": 2.00,
}

_USAGE_KEYS = ("in", "out", "cache_read", "cache_write")

# Schema del Capo: input_tokens/output_tokens -> in/out
_USAGE_ALIASES = {
    "input_tokens": "in",
    "output_tokens": "out",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
}


def preparation_price_ceiling(metadata, *, model, max_tokens):
    """Live quote, not the historical static list. Reserve the entire context.

    max_price rates are USD/M tokens; Models API rates are USD/token.
    Request fees are explicitly capped at zero; no media/search/tools are sent.
    https://openrouter.ai/docs/guides/routing/provider-selection#max-price
    """
    if metadata.get("id") != model:
        raise ValueError("pricing model mismatch")
    context = metadata.get("context_length")
    if type(context) is not int or type(max_tokens) is not int or not 0 < max_tokens < context:
        raise ValueError("model context or completion cap unavailable")
    rates = {}
    for key in ("prompt", "completion"):
        raw = metadata.get("pricing", {}).get(key)
        if isinstance(raw, bool) or raw is None:
            raise ValueError("pricing unavailable: " + key)
        rate = Decimal(str(raw))
        if not rate.is_finite() or rate < 0:
            raise ValueError("invalid pricing: " + key)
        rates[key] = rate
    ceiling = context * rates["prompt"] + max_tokens * rates["completion"]
    return {"reserve_nano_usd": int((ceiling * 10**9).to_integral_value(rounding=ROUND_CEILING)),
            "context_length": context,
            "max_price": {**{key: float(rate * 10**6) for key, rate in rates.items()}, "request": 0.},
            "basis": "full_model_context_plus_max_completion; no tools/media/cache writes"}


def resolve_model(model: str) -> str:
    """Normalizza una model string all'id canonico del listino (gestisce gli alias dated)."""
    m = (model or "").strip()
    return MODEL_ALIASES.get(m, m)


def normalize_usage(usage: Optional[dict]) -> Dict[str, Optional[int]]:
    """Riduce un usage (schema nostro o schema Anthropic) alle 4 chiavi canoniche.
    Chiavi mancanti o valori invalidi = None, distinti dallo zero misurato."""
    out = {k: None for k in _USAGE_KEYS}
    if not usage:
        return out
    for raw_key, value in usage.items():
        key = _USAGE_ALIASES.get(raw_key, raw_key)
        if key not in out:
            continue
        try:
            n = int(value) if not isinstance(value, bool) else -1
        except (TypeError, ValueError, OverflowError):
            n = -1
        out[key] = n if n >= 0 and str(n) == str(value) else None
    return out


def rates_usd(model: str) -> Optional[Dict[str, float]]:
    """Tariffe complete USD/Mtok per il modello, cache derivate dai moltiplicatori.
    None se il modello non e' a listino. Nota: cache_write_* sono due voci distinte
    perche' il prezzo dipende dal TTL scelto a runtime."""
    base = PRICING_USD_PER_MTOK.get(resolve_model(model))
    if base is None:
        return None
    return {
        "in": base["in"],
        "out": base["out"],
        "cache_read": base["in"] * CACHE_READ_MULTIPLIER,
        "cache_write_5m": base["in"] * CACHE_WRITE_MULTIPLIER["5m"],
        "cache_write_1h": base["in"] * CACHE_WRITE_MULTIPLIER["1h"],
    }


def cost_usd(model: str, usage: dict, cache_ttl: Optional[str] = None) -> dict:
    """Costo in USD di una chiamata.

    model     : model string (anche dated).
    usage     : {"in","out","cache_read","cache_write"} - chiavi mancanti = 0.
    cache_ttl : "5m" | "1h" | None. None = nessun caching (i token di cache non vengono
                prezzati e sono dichiarati non applicabili nel breakdown).

    Ritorna {"cost": float|None, "status": "ok"|"model_unknown", "model": str,
             "breakdown": dict}. Modello fuori listino -> cost=None, status="model_unknown".
    """
    canonical = resolve_model(model)
    tokens = normalize_usage(usage)

    # 05/09 (OpenRouter, ordine PM): se la risposta porta il SUO costo (usage.cost, crediti
    # USD, consegnato in ogni risposta) e' quello la misura — anche per uno slug che il
    # listino di casa non conosce. Il listino resta per le righe storiche e per chi non lo
    # consegna; cost_usd=None vale «non consegnato» e si ricade sul listino (o model_unknown).
    _api = usage.get("cost_usd") if isinstance(usage, dict) else None
    if _api is not None:
        try:
            costo_api = float(_api)
            if not math.isfinite(costo_api) or costo_api < 0 or isinstance(_api, bool):
                raise ValueError("costo non finito o negativo")
        except (TypeError, ValueError, OverflowError):
            return {"cost": None, "status": "pricing_unavailable", "model": canonical,
                    "breakdown": {"tokens": tokens, "reason": "costo provider non valido"}}
        return {
            "cost": costo_api,
            "status": "ok",
            "model": canonical,
            "breakdown": {
                "tokens": tokens,
                "cache_ttl": cache_ttl,
                "fonte": "openrouter_api",
                "reason": "costo consegnato da OpenRouter (usage.cost, USD): listino di casa non usato",
            },
        }

    rates = rates_usd(canonical)

    if rates is None:
        # Regola no-fallback-silenziosi: buco dichiarato, non zero e non tariffa "simile".
        return {
            "cost": None,
            "status": "model_unknown",
            "model": canonical,
            "breakdown": {
                "tokens": tokens,
                "cache_ttl": cache_ttl,
                "reason": f"modello '{canonical}' non a listino: costo non calcolabile",
            },
        }

    ttl = cache_ttl if cache_ttl in CACHE_WRITE_MULTIPLIER else None
    necessari = _USAGE_KEYS if ttl else ("in", "out")
    mancanti = [k for k in necessari if tokens[k] is None]
    if mancanti:
        return {"cost": None, "status": "usage_unknown", "model": canonical,
                "breakdown": {"tokens": tokens, "cache_ttl": cache_ttl,
                              "reason": "contatori mancanti: " + ", ".join(mancanti)}}

    if ttl is None:
        # Senza TTL non c'e' caching: i token di cache eventualmente passati NON sono
        # prezzabili (il write cambia prezzo col TTL). Li dichiariamo ignorati.
        billed = {"in": tokens["in"], "out": tokens["out"], "cache_read": 0, "cache_write": 0}
        write_rate = 0.0
    else:
        billed = dict(tokens)
        write_rate = rates["cache_write_" + ttl]

    unit = {
        "in": rates["in"],
        "out": rates["out"],
        "cache_read": rates["cache_read"] if ttl else 0.0,
        "cache_write": write_rate,
    }
    costs = {k: billed[k] * unit[k] / 1_000_000 for k in _USAGE_KEYS}
    total = sum(costs.values())

    breakdown = {
        "tokens": tokens,
        "billed_tokens": billed,
        "rates_usd_per_mtok": unit,
        "costs_usd": costs,
        "cache_ttl": ttl,
    }
    if ttl is None and (tokens["cache_read"] or tokens["cache_write"]):
        breakdown["note"] = (
            "cache_ttl=None (nessun caching): i token cache_read/cache_write passati "
            "non sono stati prezzati - passa cache_ttl='5m' o '1h' se il caching e' attivo"
        )
    if cache_ttl is not None and ttl is None:
        breakdown["note"] = f"cache_ttl '{cache_ttl}' non riconosciuto (attesi '5m' o '1h'): cache non prezzata"

    return {"cost": total, "status": "ok", "model": canonical, "breakdown": breakdown}


# Memo di processo: (rate, source) della PRIMA risoluzione riuscita. Vedi docstring modulo.
# None = mai risolto. Il lock serve perche' gli specialisti girano in thread paralleli:
# senza, due thread potrebbero memoizzare due cambi diversi presi in istanti diversi.
_FX_MEMO: Optional[Tuple[float, str]] = None
_FX_MEMO_LOCK = threading.Lock()


def reset_fx_memo() -> None:
    """Azzera la memo FX di processo: la prossima risoluzione ripartira' da price_updater.
    Esiste per i test (che devono poter simulare live/fallback/n.d. nello stesso processo).
    In produzione NON va chiamata: la run deve restare prezzata a un cambio unico."""
    global _FX_MEMO
    with _FX_MEMO_LOCK:
        _FX_MEMO = None


def _resolve_fx_usd_to_eur():
    """Interroga price_updater UNA volta e basta: (rate|None, "live"|"fallback"|"n.d.").
    Import in try/except: il modulo deve restare importabile anche senza price_updater
    (e senza yfinance installato)."""
    try:
        from bellomberg.cli.price_updater import get_fx_to_eur, get_fx_sources
    except Exception:
        return None, "n.d."
    try:
        rate = get_fx_to_eur("USD")
    except Exception:
        return None, "n.d."
    if rate is None:
        return None, "n.d."
    try:
        # get_fx_sources() era un ramo morto (nessun chiamante): qui serve a dire se il
        # tasso e' vero o e' il fallback statico di maggio 2026.
        source = get_fx_sources().get("USD")
    except Exception:
        source = None
    if source not in ("live", "fallback"):
        source = "n.d."
    return float(rate), source


def _fx_usd_to_eur():
    """Tasso USD->EUR per la run, memoizzato per processo.
    Ritorna (rate|None, "live"|"fallback"|"n.d."), stessa firma di prima.
    La prima risoluzione riuscita - fallback COMPRESO - vale per tutto il processo, cosi'
    la run e' prezzata a un cambio unico e coerente. Un "n.d." non si memoizza: e' un buco,
    non un cambio, e la chiamata dopo puo' ritentare."""
    global _FX_MEMO
    memo = _FX_MEMO
    if memo is not None:
        return memo

    with _FX_MEMO_LOCK:
        # Ricontrollo dentro il lock: un altro thread puo' aver memoizzato nel frattempo,
        # e il suo cambio deve vincere (uno solo per run).
        if _FX_MEMO is not None:
            return _FX_MEMO
        rate, source = _resolve_fx_usd_to_eur()
        if rate is None:
            return None, "n.d."
        _FX_MEMO = (rate, source)
        return _FX_MEMO


def cost_eur(model: str, usage: dict, cache_ttl: Optional[str] = None) -> dict:
    """Come cost_usd ma in EUR, convertendo col tasso USD->EUR di price_updater.

    Ritorna {"cost","status","model","breakdown","fx_rate","fx_source"}.
    - modello fuori listino -> cost=None, status="model_unknown" (nessun FX richiesto);
    - FX indisponibile      -> cost=None, fx_source="n.d." (il costo USD resta nel breakdown);
    - FX su fallback statico -> cost valorizzato ma fx_source="fallback" (buco dichiarato).
    """
    res = cost_usd(model, usage, cache_ttl)

    if res["status"] != "ok":
        res["fx_rate"] = None
        res["fx_source"] = "n.d."
        return res

    rate, source = _fx_usd_to_eur()
    res["breakdown"]["cost_usd"] = res["cost"]

    if rate is None:
        res["cost"] = None
        res["fx_rate"] = None
        res["fx_source"] = "n.d."
        res["breakdown"]["reason"] = "FX USD->EUR non disponibile: costo in EUR non calcolabile"
        return res

    res["cost"] = res["cost"] * rate
    res["fx_rate"] = rate
    res["fx_source"] = source
    return res


def format_eur(v) -> str:
    """Formatta un importo EUR in stile italiano per i log. None -> 'n.d.'."""
    if v is None:
        return "n.d."
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n.d."
    # 4 decimali sotto il centesimo: le singole chiamate costano spesso frazioni di cent.
    decimals = 2 if abs(f) >= 0.01 else 4
    return f"{f:.{decimals}f}".replace(".", ",") + " EUR"
