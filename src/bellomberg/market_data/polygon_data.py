"""
polygon_data.py — Provider Polygon.io (task #173, stack API a pagamento)

Richiede in .env:  POLYGON_API_KEY=...
  - Piano "Options Starter" ($29/mo)  -> get_option_expirations / get_options_chain
  - get_stock_daily (aggregati daily US): NB 16/07, correzione PM — il PM paga SOLO
    il piano opzioni; gli aggregati stock girano sul tier gratuito della stessa key
    (limiti free: pochi req/min, dati EOD). Se gli aggregati servissero di piu',
    e' una decisione di spesa del PM, non un dato di fatto.
La stessa key funziona per i prodotti a cui sei abbonato.
Docs: https://polygon.io/docs

Risolve anche il bug #162: la chain si puo' chiedere per QUALSIASI expiry,
non solo la nearest come yfinance.
"""
from bellomberg.core.paths import PROJECT_ROOT
from bellomberg.core.errori_sicuri import senza_segreti
import os
import math
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

try:
    import requests
    REQ_OK = True
except ImportError:
    REQ_OK = False

try:  # carica .env anche in esecuzione standalone
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE = "https://api.polygon.io"


def polygon_available() -> bool:
    return bool(REQ_OK and POLYGON_KEY)


def _senza_chiave(testo: str) -> str:
    """La chiave API non entra ne' nel log ne' nel dict di errore. Review 27/08:
    `str(e)` di un ConnectTimeout di requests porta l'URL intero con
    `apiKey=` (misurato); un corpo di errore del WAF potrebbe riecheggiarla."""
    return senza_segreti(testo, POLYGON_KEY)


def _path_log(url: str) -> str:
    """Path SENZA query: la chiave viaggia nei params, ma un `next_url` di
    paginazione puo' portarla nella stringa — nel log non entra."""
    path = url.split("?", 1)[0]
    if path.startswith(BASE):
        path = path[len(BASE):]
    return _senza_chiave(path)


def _log_riga(testo: str) -> None:
    """La riga e' ADDITIVA: se stdout e' morto (pipe chiusa, autopsia (40)) o
    rifiuta l'encoding (`ValueError`/`UnicodeEncodeError`, review 27/08) si
    perde LEI, non il contratto HTTP verso il chiamante."""
    try:
        print("  [POLYGON] " + testo, flush=True)
    except (OSError, ValueError):
        pass


def _log_http(status: int, url: str, body: str) -> None:
    """Riga di log per OGNI risposta non-200 (27/08, run V9 punto «rate-limit»).

    Prima il 429 tornava al chiamante come `{"error"}` e basta: il log della
    run non poteva mostrarlo, quindi «zero righe 429 nel log» NON era una
    misura. Forma `[POLYGON] HTTP <code> on <path>: <corpo>`, analoga alla riga
    generica di `finnhub_news._api_get` (che pero' sul 429 stampa `429 rate
    limited (...)` senza HTTP ne' path: un grep `HTTP 429` conta Polygon e
    Quiver, non Finnhub). Corpo su una riga sola (chi conta i 429 fa grep di
    riga), 120 char, senza chiave.
    """
    corpo = " ".join(_senza_chiave(body or "")[:120].split())
    _log_riga(f"HTTP {status} on {_path_log(url)}: {corpo}")


def _log_eccezione(e: Exception, url: str) -> None:
    """Timeout / rete giu': TIPO e path, mai `str(e)` (v. `_senza_chiave`).
    Cosi' anche «zero guasti Polygon nel log» e' una misura, non solo «zero 429»."""
    _log_riga(f"{type(e).__name__} on {_path_log(url)}")


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not polygon_available():
        return None
    p = dict(params or {})
    p["apiKey"] = POLYGON_KEY
    # path può essere relativo o un next_url assoluto (paginazione)
    url = path if path.startswith("http") else f"{BASE}{path}"
    try:
        r = requests.get(url, params=p, timeout=15)
        if r.status_code != 200:
            _log_http(r.status_code, url, r.text)
            return {"error": f"HTTP {r.status_code}", "_body": _senza_chiave(r.text)[:200]}
        return r.json()
    except Exception as e:
        _log_eccezione(e, url)
        # 05/10 (Opus 5.5): mai il TESTO dell'eccezione, nemmeno mascherato. Il
        # price_updater.log conteneva 48 righe «[IV] ... KO dichiarato: expirations:
        # HTTPSConnectionPool(...) url: ...&apiKey=<chiave in chiaro>»: il testo di
        # requests porta l'URL con la querystring, e la maschera vale solo se la
        # chiave compare identica (codificata, ruotata o di un'altra fonte: no).
        # Tipo + path senza query bastano a dire cosa e' caduto.
        return {"error": f"{type(e).__name__} on {_path_log(url)}"}


def _opzioni_non_coperte(underlying: Optional[str], source: str) -> Optional[Dict[str, Any]]:
    """Polygon/OPRA copre solo opzioni USA (P1, 04/10, Opus 5.5). Un `XXX.MI`
    faceva ~4 chiamate a vuoto e tornava «no data», senza dire che il mercato non
    e' coperto: ora si risponde PRIMA della rete (v. `copertura.copertura_opzioni`).
    Ne beneficiano tutti i chiamanti delle funzioni pubbliche (GEX, summary, chat);
    chi chiama `_get` direttamente (vol_surface) ha la sua. None = si interroga.
    Si ferma solo `non_coperto`: un `indeterminato` (suffisso fuori registro, es.
    una classe di azioni USA col punto) passa col simbolo INTATTO, come chiedono
    i test dei simboli col punto di options_download/options_routes."""
    from bellomberg.market_data.copertura import copertura_opzioni, risposta_non_coperta
    esito = copertura_opzioni(underlying)
    if esito["stato"] != "non_coperto":
        return None
    return risposta_non_coperta(esito, source)


def get_option_expirations(underlying: str, limit: int = 60) -> Dict[str, Any]:
    """Lista expiry disponibili per un sottostante US.
    Paginata (#179 fix): 1000 contratti coprono solo le prime scadenze di un
    sottostante liquido — seguiamo next_url per la lista completa."""
    from datetime import timedelta
    fuori = _opzioni_non_coperte(underlying, "polygon /v3/reference/options/contracts")
    if fuori:
        return fuori
    exps: set = set()
    last = None
    errors = []
    issues = []
    requests_count = 0
    pages_received = 0
    stopped = False

    def _scan(extra_params: Dict[str, Any], pages: int) -> None:
        nonlocal last, requests_count, pages_received, stopped
        if stopped:
            return
        url: Optional[str] = "/v3/reference/options/contracts"
        params: Optional[Dict[str, Any]] = {
            "underlying_ticker": underlying.upper(),
            "limit": 1000,
            "sort": "expiration_date",
            **extra_params,
        }
        for _ in range(pages):
            requests_count += 1
            data = _get(url, params)
            last = data
            if not data or data.get("error"):
                error = (data or {}).get("error", "no data")
                errors.append(error)
                stopped = error in {"HTTP 401", "HTTP 403", "HTTP 429"}
                return
            pages_received += 1
            for c in data.get("results", []):
                if c.get("expiration_date"):
                    exps.add(c["expiration_date"])
            url = data.get("next_url")
            params = None
            if not url:
                return
        if url:
            issues.append("page_limit")

    # Scansione base (scadenze vicine) + finestre future: i sottostanti liquidi
    # hanno decine di migliaia di contratti e la sola paginazione copre poche
    # settimane — saltiamo avanti con filtri data per coprire ~4 mesi.
    _scan({}, 4)
    today = datetime.now().date()
    for shift in (35, 70, 105):
        _scan({"expiration_date.gte": str(today + timedelta(days=shift))}, 1)

    returned = sorted(exps)[:limit]
    if len(returned) < len(exps):
        issues.append("output_limit")
    coverage = {"status": "UNAVAILABLE" if not exps else "PARTIAL" if errors or issues else "COMPLETE",
                "scope": "requested_scan_windows_only",
                "requests": requests_count, "pages_received": pages_received,
                "expirations_observed": len(exps), "expirations_returned": len(returned),
                "errors": errors, "issues": issues, "requests_stopped": stopped}
    if not exps:
        return {"error": errors[0] if errors else "no data",
                "_body": (last or {}).get("_body", ""), "coverage": coverage,
                "_source": "polygon /v3/reference/options/contracts"}
    return {"underlying": underlying.upper(), "expirations": returned, "coverage": coverage,
            "_source": "polygon /v3/reference/options/contracts (paginated)",
            "_timestamp": datetime.now().isoformat()}


def _num(value: Any) -> Optional[float]:
    """Numero finito o None (mai 0 inventato al posto di un campo assente)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _ns_to_iso(stamp: Any) -> Optional[str]:
    value = _num(stamp)
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1e9, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _truncation_reference(results: List[Dict[str, Any]], spot: Optional[float]):
    """Prezzo attorno a cui CENTRARE un taglio della chain (09/10, A1 audit Vol Deck).

    Ordine: spot passato dal chiamante, poi `underlying_asset.price` osservato
    (il piu' recente per `last_updated`), poi la mediana degli strike osservati —
    quest'ultima e' una REGOLA DI TAGLIO dichiarata in coverage, mai uno spot."""
    ref = _num(spot)
    if ref is not None and ref > 0:
        return ref, "spot passato dal chiamante"
    best = None
    for c in results:
        ua = c.get("underlying_asset") if isinstance(c, dict) else None
        price = _num(ua.get("price")) if isinstance(ua, dict) else None
        if price is None or price <= 0:
            continue
        stamp = _num(ua.get("last_updated")) or 0
        if best is None or stamp > best[0]:
            best = (stamp, price)
    if best is not None:
        return best[1], "polygon underlying_asset.price"
    strikes = sorted(s for s in (_num(((c.get("details") or {}) if isinstance(c, dict) else {}).get("strike_price"))
                                 for c in results) if s is not None)
    if strikes:
        return strikes[len(strikes) // 2], "mediana strike osservati (nessuno spot: regola di taglio, NON uno spot)"
    return None, None


def get_options_chain(underlying: str, expiry: Optional[str] = None,
                      max_contracts: int = 250, spot: Optional[float] = None) -> Dict[str, Any]:
    """Chain snapshot con IV, greeks, OI, volume.
    expiry: 'YYYY-MM-DD' opzionale — se None Polygon ritorna tutte (cap max_contracts).

    09/10 (A1 audit Vol Deck, Opus 5.5): se le righe osservate superano
    `max_contracts` il taglio NON tiene piu' gli strike piu' bassi (con SPY a 650
    restavano solo strike <= 599: niente call OTM e ATM spostata di 3,5 punti di
    vol) ma i contratti piu' VICINI al riferimento (spot passato, poi prezzo del
    sottostante osservato, poi mediana strike dichiarata), su entrambi i lati.
    Il taglio e' dichiarato in `coverage.truncation` e lo stato resta PARTIAL.
    Per una superficie completa si usa vol_surface._complete_chain.
    """
    fuori = _opzioni_non_coperte(underlying, "polygon /v3/snapshot/options")
    if fuori:
        return fuori
    params: Dict[str, Any] = {"limit": 250}
    if expiry:
        params["expiration_date"] = expiry
    # Paginazione (#177 fix): i contratti sono ordinati per ticker e le Call
    # vengono prima delle Put — senza next_url si perdono le put.
    results: List[Dict[str, Any]] = []
    url: Optional[str] = f"/v3/snapshot/options/{underlying.upper()}"
    pages = 0
    requests_count = 0
    errors = []
    data = None
    while url and pages < max(1, max_contracts // 250 + 2):
        requests_count += 1
        data = _get(url, params if pages == 0 else None)
        if not data or data.get("error"):
            errors.append((data or {}).get("error", "no data"))
            break
        results.extend(data.get("results", []))
        url = data.get("next_url")
        pages += 1
    issues = []
    if url and not errors:
        issues.append("page_limit")
    truncation = {"applied": False}
    if len(results) > max_contracts:
        issues.append("output_limit")
        ref, ref_source = _truncation_reference(results, spot)

        def _distance(c):
            det = c.get("details", {}) or {}
            k = _num(det.get("strike_price"))
            return (abs(k - ref) if k is not None and ref is not None else float("inf"),
                    str(det.get("expiration_date") or ""), k or 0.0, str(det.get("contract_type") or ""))
        kept = sorted(results, key=_distance)[:max_contracts]
        kept_strikes = [s for s in (_num((c.get("details", {}) or {}).get("strike_price")) for c in kept) if s is not None]
        truncation = {"applied": True, "method": "contratti piu' vicini al riferimento, entrambi i lati",
                      "reference": ref, "reference_source": ref_source,
                      "rows_dropped": len(results) - len(kept),
                      "strike_min_kept": min(kept_strikes) if kept_strikes else None,
                      "strike_max_kept": max(kept_strikes) if kept_strikes else None}
    else:
        kept = results
    coverage = {"status": "UNAVAILABLE" if not results else "PARTIAL" if errors or issues else "COMPLETE",
                "scope": "requested_snapshot_pages_only",
                "requests": requests_count, "pages_received": pages, "rows_observed": len(results),
                "rows_returned": min(len(results), max_contracts), "errors": errors, "issues": issues,
                "truncation": truncation,
                "requests_stopped": any(e in {"HTTP 401", "HTTP 403", "HTTP 429"} for e in errors)}
    if not results:
        return {"error": (data or {}).get("error", "no data"),
                "_body": (data or {}).get("_body", ""), "coverage": coverage,
                "_source": "polygon /v3/snapshot/options"}
    rows = []
    for c in kept:
        det = c.get("details", {}) or {}
        greeks = c.get("greeks", {}) or {}
        day = c.get("day", {}) or {}
        quote = c.get("last_quote") if isinstance(c.get("last_quote"), dict) else {}
        rows.append({
            "contract": det.get("ticker"),
            "type": det.get("contract_type"),          # call / put
            "strike": det.get("strike_price"),
            "expiry": det.get("expiration_date"),
            "iv": c.get("implied_volatility"),
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
            "oi": c.get("open_interest"),
            "volume": day.get("volume"),
            "close": day.get("close"),
            # 09/10 (A3/M3/M6/B7 audit Vol Deck): quota, rettifica e moltiplicatore
            # del contratto; None quando il provider non li manda (mai 0 o 100 inventati).
            "bid": _num(quote.get("bid")), "ask": _num(quote.get("ask")),
            "quote_timestamp": _ns_to_iso(quote.get("last_updated")),
            "quote_timeframe": quote.get("timeframe"),
            "multiplier": _num(det.get("shares_per_contract")),
            "adjusted": bool(det.get("additional_underlyings")),
            # moltiplicatore del contratto (details.shares_per_contract): il GEX lo usa al
            # posto del 100 fisso quando c'e' (rettifiche societarie, mini) — fix 09/10
            "shares_per_contract": det.get("shares_per_contract"),
            # orario della quota del contratto (ns UTC): serve ad allineare lo spot alle quote
            "quote_timestamp_ns": (c.get("last_quote") if isinstance(c.get("last_quote"), dict) else {}).get("last_updated"),
            # Keep the provider observation; a strike/delta is never its price.
            "underlying_asset": c.get("underlying_asset"),
        })
    rows.sort(key=lambda x: (x.get("expiry") or "", x.get("strike") or 0))
    return {"underlying": underlying.upper(), "expiry_filter": expiry,
            "n_contracts": len(rows), "n_pages": pages, "chain": rows, "coverage": coverage,
            "_source": "polygon /v3/snapshot/options (paginated)",
            "_timestamp": datetime.now(timezone.utc).isoformat()}


def _summary_spot_observation(ticker, chain):
    """Preserve observed quotes without asserting undocumented qualifications.

    Massive/Polygon option-chain-snapshot (checked 2026-10-09) documents
    underlying_asset.price/ticker/last_updated/timeframe, but no currency or
    timestamp for implied_volatility. Do not infer USD, an ISIN, or IV time from
    last_quote/day. No freshness tolerance or additional provider call here.
    https://massive.com/docs/rest/options/snapshots/option-chain-snapshot
    """
    result = {"spot": None, "spot_status": "MISSING", "spot_source": None,
              "spot_asof": None, "spot_currency": None, "spot_proxy": None,
              "spot_observation": None, "atm_status": "UNVERIFIED",
              "spot_observations": [], "spot_observation_issues": {},
              "spot_observation_coverage": "UNAVAILABLE",
              "atm_issues": ["currency_unattested", "iv_timestamp_unattested"]}
    observations = []
    issues = result["spot_observation_issues"]
    for contract in chain:
        observation = contract.get("underlying_asset")
        price = observation.get("price") if isinstance(observation, dict) else None
        if observation is None or observation == {}:
            status = "MISSING"
        elif not isinstance(observation, dict):
            status = "INVALID_OBSERVATION"
        elif price is None:
            status = "MISSING_PRICE"
        elif (isinstance(price, bool) or not isinstance(price, (int, float))
                or not math.isfinite(price) or price <= 0):
            status = "INVALID_PRICE"
        elif observation.get("ticker") != ticker.upper():
            status = "IDENTITY_MISMATCH"
        else:
            stamp = observation.get("last_updated")
            try:
                if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp <= 0:
                    raise ValueError("invalid timestamp")
                asof = datetime.fromtimestamp(stamp / 1_000_000_000, timezone.utc)
                if asof > datetime.now(timezone.utc):
                    raise ValueError("future timestamp")
            except (ValueError, OverflowError, OSError):
                status = "INVALID_TIMESTAMP"
            else:
                status = "UNVERIFIED_CURRENCY"
                observations.append(observation)
        # Preserve every received observation independently of aggregate quality.
        result["spot_observations"].append({
            "contract": contract.get("contract"), "type": contract.get("type"),
            "status": status, "observation": observation})
        if status != "UNVERIFIED_CURRENCY":
            issues[status] = issues.get(status, 0) + 1
        if observation is not None:
            result["spot_source"] = "polygon /v3/snapshot/options underlying_asset"
    if not observations:
        result["spot_status"] = next(iter(issues)) if len(issues) == 1 else "UNAVAILABLE"
        result["atm_issues"].append("underlying_quote_unavailable")
        return result
    result["spot_observation_coverage"] = "PARTIAL" if issues else "COMPLETE"
    if issues:
        result["atm_issues"].append("underlying_quote_partial")
    # No tolerance: different observations stay raw, never averaged or combined.
    fields = ("price", "ticker", "last_updated", "timeframe", "currency")
    first = observations[0]
    if any(any(o.get(k) != first.get(k) for k in fields) for o in observations[1:]):
        result["spot_status"] = "CONFLICTING_OBSERVATIONS"
        result["atm_issues"].append("underlying_quotes_conflicting")
        return result
    asof = datetime.fromtimestamp(first["last_updated"] / 1_000_000_000, timezone.utc)
    result.update(spot_status="UNVERIFIED_CURRENCY",
                  spot_asof=asof.isoformat(),
                  spot_observation={k: first[k] for k in fields if k in first})
    # An unexpected currency field remains in the raw observation, not an
    # attestation: this endpoint's documented schema has no currency contract.
    return result


def get_options_summary_polygon(ticker: str, expiry: Optional[str] = None) -> Dict[str, Any]:
    """Summary opzioni in formato tool_get_options_data (#163: Polygon al posto
    di IBKR come fonte primaria): spot, ATM IV call/put, P/C OI ratio, max pain.
    Se expiry e' None usa la prima scadenza >= 2 giorni (evita 0DTE distorti)."""
    src = "polygon options summary"
    fuori = _opzioni_non_coperte(ticker, src)
    if fuori:
        return fuori
    try:
        if not polygon_available():
            return {"error": "POLYGON_API_KEY mancante", "_source": src}
        from bellomberg.core.options_expiry import normalize_expiry, valid_expiry, select_expiry
        try:
            expiry = normalize_expiry(expiry)
            if expiry is not None:
                expiry = valid_expiry(expiry)
        except ValueError as exc:
            return {"error": str(exc), "_source": src}
        expiry_coverage = None
        if expiry is None:
            exp = get_option_expirations(ticker)
            expiry_coverage = exp.get("coverage")
            if exp.get("error") or (expiry_coverage or {}).get("requests_stopped"):
                return {"error": exp.get("error") or expiry_coverage["errors"][-1],
                        "coverage": expiry_coverage, "_source": src}
            try:
                expiry = select_expiry(exp.get("expirations", []), min_days=2)
            except ValueError as exc:
                if str(exc) == "expiry_no_valid_available":
                    return {"error": "Nessuna opzione disponibile: nessuna scadenza valida per la richiesta",
                            "error_code": "expiry_no_valid_available",
                            "coverage": expiry_coverage, "_source": src}
                return {"error": str(exc), "coverage": expiry_coverage, "_source": src}

        ch = get_options_chain(ticker, expiry, max_contracts=1200)
        if ch.get("error"):
            return {"error": ch["error"], "coverage": ch.get("coverage"),
                    "expiry_coverage": expiry_coverage, "_source": src}
        raw_chain = ch.get("chain", [])
        chain = []
        rejected = {}
        for contract in raw_chain:
            try:
                row_expiry = valid_expiry(contract.get("expiry"))
                if row_expiry != expiry:
                    raise ValueError("expiry_mismatch")
            except ValueError as exc:
                reason = str(exc)
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            chain.append(contract)
        # Annotate the summary projection only; preserve the raw chain response.
        coverage = dict(ch.get("coverage") or {"status": "UNKNOWN"})
        coverage.update(rows_used=len(chain), rows_rejected_expiry=sum(rejected.values()),
                        expiry_rejections=[{"reason": reason, "count": count}
                                           for reason, count in sorted(rejected.items())])
        if rejected:
            coverage["status"] = "PARTIAL"
            coverage["issues"] = list(coverage.get("issues") or []) + ["row_expiry_rejected"]
        calls = [c for c in chain if c.get("type") == "call" and c.get("strike")]
        puts = [c for c in chain if c.get("type") == "put" and c.get("strike")]
        if not calls or not puts:
            return {"error": "chain incompleta per expiry richiesta", "coverage": coverage,
                    "expiry_coverage": expiry_coverage, "_source": src}

        spot_contract = _summary_spot_observation(ticker, chain)
        if coverage.get("status") != "COMPLETE":
            spot_contract["atm_issues"].append("chain_not_complete")
        observed_candidates = None
        observation = spot_contract["spot_observation"]
        if observation:
            # A numerical locator for inspecting raw IV, NOT verified moneyness:
            # currency and IV timing remain unattested in this endpoint.
            observed_candidates = {
                "status": "UNVERIFIED_CURRENCY_AND_TIMING",
                "method": "nearest_numeric_strike_to_observed_price; not qualified ATM",
                "coverage": coverage.get("status"),
            }
            for side, contracts in (("call", calls), ("put", puts)):
                candidate = min(contracts, key=lambda c: abs(c["strike"] - observation["price"]))
                observed_candidates[side] = {
                    k: candidate.get(k) for k in ("contract", "strike", "expiry", "iv", "delta")}
        total_call_oi = sum(int(c.get("oi") or 0) for c in calls)
        total_put_oi = sum(int(c.get("oi") or 0) for c in puts)
        pc = round(total_put_oi / total_call_oi, 3) if total_call_oi else None

        # Max pain: strike che minimizza il payout totale agli holder
        strikes = sorted({c["strike"] for c in chain if c.get("strike")})
        oi_call = {}
        oi_put = {}
        for c in calls:
            oi_call[c["strike"]] = oi_call.get(c["strike"], 0) + int(c.get("oi") or 0)
        for p in puts:
            oi_put[p["strike"]] = oi_put.get(p["strike"], 0) + int(p.get("oi") or 0)
        max_pain = None
        best = None
        for K in strikes:
            pain = (sum(oi * max(0.0, K - ks) for ks, oi in oi_call.items())
                    + sum(oi * max(0.0, ks - K) for ks, oi in oi_put.items()))
            if best is None or pain < best:
                best, max_pain = pain, K

        # fix 09/10 (Opus 5.5, audit SCORE-VOL-QUANT §1.1): chain con righe MANCANTI (errore
        # HTTP a meta' paginazione o troncamento a max_contracts) = P/C e max pain n.d.
        # dichiarati: il troncamento per strike taglia le call OTM alte, l'errore le pagine
        # dopo, e il rapporto si sposta senza che il mercato si muova (regola 14/07).
        _chain_cov = ch.get("coverage") or {}
        chain_mancante = _chain_cov.get("status") == "PARTIAL" or bool(_chain_cov.get("errors"))
        pc_nd = None
        if chain_mancante:
            pc_nd = "chain parziale: " + ", ".join(str(x) for x in (list(_chain_cov.get("errors") or [])
                                                                     + list(_chain_cov.get("issues") or [])))
            pc = None
            max_pain = None
        interp = ("n.d. (" + pc_nd + ")" if pc_nd else
                  "P/C alto (>1.2): posizionamento difensivo/hedging prevalente"
                  if pc and pc > 1.2 else
                  "P/C basso (<0.7): posizionamento speculativo rialzista"
                  if pc and pc < 0.7 else "P/C neutrale")

        return {
            "ticker": ticker.upper(),
            "expiry_used": expiry,
            "coverage": coverage,
            "expiry_coverage": expiry_coverage,
            "partial": any((c or {}).get("status") != "COMPLETE" for c in
                           [coverage] + ([expiry_coverage] if expiry_coverage else [])),
            **spot_contract,
            "observed_strike_candidates": observed_candidates,
            "atm_strike": None,
            "iv_atm_call": None,
            "iv_atm_put": None,
            "delta_atm_call": None,
            "gamma_atm": None,
            "theta_atm": None,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "put_call_oi_ratio": pc,
            "put_call_oi_ratio_nd": pc_nd,
            "interpretation_pc": interp,
            "max_pain_strike": max_pain,
            "max_pain_vs_spot_pct": None,
            "n_contracts": len(chain),
            "data_source": "polygon_options_starter (delayed 15min, greeks inclusi)",
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        # mai str(e): v. _get (05/10)
        return {"error": type(e).__name__, "_source": src}


def get_stock_daily(ticker: str, days: int = 120) -> Dict[str, Any]:
    """Aggregati daily US (richiede piano Stocks)."""
    from datetime import timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = _get(f"/v2/aggs/ticker/{ticker.upper()}/range/1/day/{start}/{end}",
                {"adjusted": "true", "sort": "asc", "limit": 5000})
    if not data or data.get("error"):
        return {"error": (data or {}).get("error", "no data"),
                "_source": "polygon /v2/aggs"}
    bars = [{"date": datetime.utcfromtimestamp((b.get("t") or 0) / 1000).strftime("%Y-%m-%d"),
             "o": b.get("o"), "h": b.get("h"), "l": b.get("l"), "c": b.get("c"),
             "v": b.get("v")} for b in data.get("results", [])]
    return {"ticker": ticker.upper(), "n_bars": len(bars), "bars": bars,
            "_source": "polygon /v2/aggs", "_timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    # smoke test: python polygon_data.py
    if not polygon_available():
        print("POLYGON_API_KEY mancante in .env")
    else:
        ex = get_option_expirations("MSTR")
        if ex.get("error"):
            print("ERRORE:", ex["error"], "|", ex.get("_body", "")[:300])
        else:
            print("expirations MSTR:", ex.get("expirations", [])[:6])
        if ex.get("expirations"):
            ch = get_options_chain("MSTR", ex["expirations"][2] if len(ex["expirations"]) > 2 else ex["expirations"][0])
            print("contracts:", ch.get("n_contracts"), "| esempio:", (ch.get("chain") or [{}])[0])
