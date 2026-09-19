"""Generate the English documentation fixture from one invented account, offline.

Usage: python docs/guide/generate_documentation_demo.py [--check] [--output FILE]
Reads the versioned book and canonical JSON snapshot. No application import,
SQLite, environment file, provider or network access. Analytics and scenes are
preserved authored snapshots, NOT regenerated. Only book calculations are rebuilt.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name("fixtures") / "documentation-demo-source.json"
OUTPUT = ROOT / "app/tests/fixtures/documentation-demo.json"
MC = "GET /portfolio/montecarlo?drift_mode=zero&force=false&horizon_days=252&lookback_years=5&method=fhs&n_sims=10000&stress=none"
PAGE_ROUTES = {
    "/dashboard", "/performance", "/watchlist", "/market", "/news", "/fundamentals",
    "/factors", "/backtest", "/vol", "/edge", "/chat", "/agents", "/agent-progress",
    "/memos", "/decisions", "/trades", "/movements", "/mandato",
}
ALIASES = {
    "GET /agents/progress": "GET /agents/progress?limit=100",
    "GET /journal": "GET /journal?limit=30&offset=0&query=&status=active",
    "GET /journal/1/versions": "GET /journal/1/versions?limit=20&offset=0",
    "GET /decisions": "GET /decisions?limit=500",
}
OBSERVATION_TIME_FIELDS = {
    "timestamp", "_timestamp", "generated", "generated_at", "updated_at",
    "started_at", "completed_at", "created_at", "observed_at",
    "spot_timestamp", "quote_timestamp", "snapshot_at", "published_at",
    "scansione_delle", "LastRunTime",
}
SCENE_NOTES = {
    "dashboard": "Command Center with the synthetic account, a reconciled close, decisions, market chart and position weights.",
    "performance": "Performance with the invented account history and its declared return methodology.",
    "performance-attribution": "Performance attribution for the same synthetic holdings and historical observations.",
    "performance-book-risk": "Portfolio risk and concentration for the same synthetic account.",
    "watchlist": "Four invented companies with synthetic prices, daily changes and watchlist notes.",
    "market-overview": "Global Markets showing invented index, currency, commodity and rate observations.",
    "market-security": "The selected invented company, its price chart, ownership and demonstration news.",
    "market-financials": "The selected invented company's synthetic financial statements and comparisons.",
    "news-wire": "News Wire populated with invented articles, relevance and sources.",
    "news-desk": "News Desk with the synthetic briefing, events, calendar and source coverage.",
    "fundamentals": "Documented synthetic valuations, with usable and blocked results visibly distinguished.",
    "factor-lab": "Factor Lab with the synthetic account, fitted factor history and beta reconciliation.",
    "montecarlo": "Monte Carlo paths and distributions simulated from the invented account's return history.",
    "vol-deck-tools": "Vol Deck after a synthetic options acquisition: Plotly draws the actual interactive 3D surface.",
    "vol-deck-chain": "Vol Deck's synthetic options chain with populated prices, Greeks and coverage.",
    "vol-deck-laboratory": "Vol Deck strategy laboratory with an invented options strategy and its simulated payoff.",
    "edge-scanner": "Edge Scanner with measured cache state and signals from the invented return history.",
    "agent-chat": "Agent Chat displaying an authored synthetic conversation; no model is called.",
    "agents-live": "Agents Live showing a simulated committee run and invented tool events.",
    "agent-progress": "Agent Progress showing the authored synthetic history and measured-result presentation.",
    "memo-archive": "Memo Archive with an invented committee memo and decisions linked to the same synthetic trade ledger.",
    "decisions": "Decision tracker with active proposals, a research conversation and the executed two-tranche DEMO.A purchase.",
    "trade-entry": "An unsaved ADD ticket for two DEMO.A shares, linked to synthetic decision 32, with cash impact and holdings.",
    "trade-entry-after": "The same unsaved ticket: before-and-after holdings, cost, cash and invested weight.",
    "trade-entry-confirm": "The synthetic trade preview confirms the ticket and cash impact. No trade is recorded.",
    "movements-register": "The single synthetic ledger: one cash deposit and three purchase orders, with explicit decision links.",
    "movements-trails": "The same synthetic ledger displayed as per-security timelines.",
    "movements-diary": "The same synthetic ledger displayed as the PM's authored transaction diary.",
    "mandate": "The public example mandate with a seven-year draft horizon validated in preview, without saving.",
    "journal": "The invented DEMO.A thesis beside its earlier versions, with a documented concentration checkpoint.",
    "settings": "Settings with synthetic scheduler, backups, language and engine status; no machine configuration is changed.",
}


def strict_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Non-finite JSON number")
        return parsed
    return json.loads(text, object_pairs_hook=unique, parse_float=number, parse_constant=number)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _readable_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(", ", ":"), allow_nan=False)


def serialize_fixture(encoded):
    """Keep each definition and document section on its own line, as plain JSON."""
    def sections(value):
        return "{\n" + ",\n".join(_readable_json(k) + ":" + _readable_json(v)
                                   for k, v in value.items()) + "\n}"
    return ("{\n\"schema\":" + _readable_json(encoded["schema"])
            + ",\n\"definitions\":" + sections(encoded["definitions"])
            + ",\n\"document\":" + sections(encoded["document"]) + "\n}\n")


def decode_fixture(encoded):
    """Resolve only local references; reject ambiguity, including unused cycles."""
    if not isinstance(encoded, dict):
        raise ValueError("Fixture object required")
    definitions, document = {}, encoded
    if "schema" in encoded:
        if set(encoded) != {"schema", "definitions", "document"} or encoded["schema"] != "documentation-local-refs/1":
            raise ValueError("Unsupported fixture encoding")
        definitions, document = encoded["definitions"], encoded["document"]
        if not isinstance(definitions, dict) or any(not re.fullmatch('[a-f0-9]{12}', k) for k in definitions):
            raise ValueError("Invalid local definitions")
    active, cache = set(), {}
    def reference(key):
        if not isinstance(key, str) or key not in definitions or key in active:
            raise ValueError("Missing or cyclic local reference")
        if key not in cache:
            active.add(key)
            cache[key] = walk(definitions[key])
            active.remove(key)
        return deepcopy(cache[key])
    def walk(node):
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, dict):
            return node
        if "$demo_ref" in node:
            if set(node) != {"$demo_ref"}:
                raise ValueError("Ambiguous local reference")
            return reference(node["$demo_ref"])
        if "$demo_table" in node:
            table = node["$demo_table"]
            if set(node) != {"$demo_table"} or not isinstance(table, dict) or set(table) != {"columns", "rows"}:
                raise ValueError("Invalid table marker")
            columns, rows = table["columns"], table["rows"]
            if (not isinstance(columns, list) or not columns
                    or any(not isinstance(k, str) or k.startswith('$demo_') for k in columns)
                    or len(set(columns)) != len(columns) or not isinstance(rows, list)
                    or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
                raise ValueError("Ambiguous table columns or row width")
            return [dict(zip(columns, (walk(v) for v in row))) for row in rows]
        if any(k.startswith('$demo_') for k in node):
            raise ValueError("Unknown fixture marker")
        return {k: walk(v) for k, v in node.items()}
    for key in definitions:
        reference(key)
    return walk(document)


def encode_fixture(document):
    """Readable JSON: repeated values once, identical object rows as columns/rows."""
    counts, definitions, identities = Counter(), {}, {}
    def count(node):
        if isinstance(node, (str, list, dict)) and len(_json(node)) > 100:
            counts[_json(node)] += 1
        if isinstance(node, dict):
            if any(k.startswith('$demo_') for k in node):
                raise ValueError("Reserved fixture marker in source")
            for value in node.values(): count(value)
        elif isinstance(node, list):
            for value in node: count(value)
    def refs(node):
        raw = _json(node)
        name = hashlib.sha256(raw.encode()).hexdigest()[:12] if counts[raw] > 1 else None
        if name:
            if name in identities and identities[name] != raw:
                raise ValueError("Local reference digest collision")
            identities[name] = raw
            if name in definitions:
                return {"$demo_ref": name}
        value = ({k: refs(v) for k, v in node.items()} if isinstance(node, dict)
                 else [refs(v) for v in node] if isinstance(node, list) else node)
        if name:
            definitions[name] = value
            return {"$demo_ref": name}
        return value
    def tables(node):
        if isinstance(node, dict): return {k: tables(v) for k, v in node.items()}
        if isinstance(node, list):
            rows = [tables(v) for v in node]
            if (len(rows) > 1 and isinstance(rows[0], dict) and rows[0]
                    and not any(k.startswith('$demo_') for k in rows[0])
                    and all(isinstance(row, dict) and list(row) == list(rows[0]) for row in rows)):
                candidate = {"$demo_table": {"columns": list(rows[0]), "rows": [list(row.values()) for row in rows]}}
                if len(_readable_json(candidate).encode("utf-8")) < len(_readable_json(rows).encode("utf-8")):
                    return candidate
            return rows
        return node
    count(document)
    value = refs(document)
    encoded = {"schema": "documentation-local-refs/1", "definitions": tables(definitions), "document": tables(value)}
    # Deduplicating a parent can leave its children referenced only once. Inline
    # those children: a one-use pointer adds bytes and makes the text harder to read.
    uses = Counter()
    def usage(node):
        if isinstance(node, dict):
            if "$demo_ref" in node: uses[node["$demo_ref"]] += 1
            else:
                for child in node.values(): usage(child)
        elif isinstance(node, list):
            for child in node: usage(child)
    def inline(node):
        if isinstance(node, list): return [inline(v) for v in node]
        if isinstance(node, dict):
            if "$demo_ref" in node and uses[node["$demo_ref"]] == 1:
                return inline(encoded["definitions"][node["$demo_ref"]])
            return {k: inline(v) for k, v in node.items()}
        return node
    usage(encoded)
    return {**encoded, "document": inline(encoded["document"]),
            "definitions": {k: inline(v) for k, v in encoded["definitions"].items() if uses[k] != 1}}


def load_source(path: Path = SOURCE) -> dict:
    source = strict_json(path.read_text(encoding="utf-8"))
    if source.get("schema_version") != 2 or source.get("template_path") != OUTPUT.relative_to(ROOT).as_posix():
        raise ValueError("Unsupported documentation source schema")
    source["fixture_template"] = decode_fixture(strict_json(OUTPUT.read_text(encoding="utf-8")))
    return source


def _canonical_presentation(node):
    """Keep the backend's authored canonical text; the renderer selects English."""
    if isinstance(node, list):
        for value in node:
            _canonical_presentation(value)
    elif isinstance(node, dict):
        metadata = node.get("_presentation_v1")
        if metadata:
            seen = set()
            for item in metadata["texts"]:
                path = item["path"]
                if not path or tuple(path) in seen:
                    raise ValueError("Duplicate or empty presentation path")
                seen.add(tuple(path))
                parent = node
                for key in path[:-1]:
                    parent = parent[key]
                if parent[path[-1]] not in (item["it"], item["en"]):
                    raise ValueError(f"Presentation text drift at {path}")
                parent[path[-1]] = item["it"]
        for key, value in node.items():
            if key != "_presentation_v1":
                _canonical_presentation(value)


def _english_variant(payload, path, english):
    node = payload
    for key in path:
        node = node[key]
    metadata = payload.setdefault("_presentation_v1", {"version": 1, "texts": []})
    existing = [item for item in metadata["texts"] if item["path"] == path]
    if existing:
        if len(existing) != 1 or existing[0]["en"] != english or node not in (existing[0]["it"], english):
            raise ValueError(f"Conflicting presentation variant: {path}")
        return
    metadata["texts"].append({"path": path, "it": node, "en": english})


def _demo_timeline(responses):
    """Authored morning observations; preserve future events and historical quotes."""
    def replace_time(node, before, after):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in OBSERVATION_TIME_FIELDS and isinstance(value, str) and value.startswith(before):
                    node[key] = after + value[len(before):]
                else:
                    replace_time(value, before, after)
        elif isinstance(node, list):
            for value in node:
                replace_time(value, before, after)
    for key, payload in responses.items():
        if key.startswith("GET /news/"):
            replace_time(payload, "2026-09-13T19:", "2026-09-13T09:")
            for index, item in enumerate(payload.get("items", [])):
                if str(item.get("published_at", "")).startswith("2026-09-13"):
                    # Invented morning headlines precede the morning briefing.
                    published = datetime.fromisoformat("2026-09-13T09:00:00+02:00") - timedelta(minutes=15 * index)
                    item["published_at"] = published.isoformat()
        elif "/options/" in key:
            # UTC 07:50 = 09:50 CEST, before the 10:11:40 renderer clock.
            replace_time(payload, "2026-09-13T21:50:", "2026-09-13T07:50:")
        elif key in ("GET /health", "GET /fx", "GET /tasks/scheduled"):
            replace_time(payload, "2026-09-13T16:08:", "2026-09-13T10:08:")
    updater = next(t for t in responses["GET /tasks/scheduled"]["tasks"]
                   if t["TaskName"] == "Bellomberg-PriceUpdater")
    updater.update(LastRunTime="09/13/2026 10:05:00", NextRunTime="09/13/2026 10:20:00")
    portfolio = responses["GET /portfolio"]
    field = "totale_aperto_piu_realizzato_note"
    texts = {"it": "Esempio sintetico DEMO: P&L aperto + realizzato vendite + dividendi. "
                   "Il confronto dimostrativo con un broker esclude i dividendi; nessun conto reale e' stato misurato.",
             "en": "Synthetic DEMO example: open P&L + realized sales P&L + dividends. "
                   "The illustrative broker comparison excludes dividends; no real account was measured."}
    portfolio[field] = texts["it"]
    item = next(t for t in portfolio["_presentation_v1"]["texts"] if t["path"] == [field])
    item.update(texts)
    briefing = responses["GET /news/briefing/current"]
    briefing.update(period="morning", slot_label="DEMO Morning Briefing (09:53 CEST)",
                    generated_at="2026-09-13T09:53:40+02:00", age_minutes=18)
    edge = responses["GET /signals/edge_scan?min_strength=45"]
    edge["generated"] = edge["cache"]["scansione_delle"] = "2026-09-13T09:59:20+02:00"
    edge_note = {"it": "Esempio sintetico DEMO: segnali ordinati per forza illustrativa. "
                       "Nessun backtest empirico viene dichiarato.",
                 "en": "Synthetic DEMO example: signals ranked by illustrative strength. "
                       "No empirical backtest is claimed."}
    edge["_note"] = edge_note["it"]
    next(t for t in edge["_presentation_v1"]["texts"] if t["path"] == ["_note"]).update(edge_note)


def validate_observation_times(fixture):
    """Observation metadata must precede the clock; appointments/expiries may not."""
    cutoff = datetime.fromisoformat(fixture["clock"]["now"].replace("Z", "+00:00"))
    # This fixed September scene is in CEST. Naive API timestamps are local
    # wall times; compare them to the same local cutoff without a host timezone.
    local_cutoff = cutoff.astimezone(timezone(timedelta(hours=2))).replace(tzinfo=None)
    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                here = path + [key]
                if key in OBSERVATION_TIME_FIELDS and isinstance(value, str) and value:
                    moment = (datetime.strptime(value, "%m/%d/%Y %H:%M:%S") if key == "LastRunTime"
                              else datetime.fromisoformat(value.replace("Z", "+00:00")))
                    if moment > (cutoff if moment.tzinfo else local_cutoff):
                        raise ValueError("Future DEMO observation: " + "/".join(map(str, here)))
                else:
                    walk(value, here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + [index])
    walk(fixture["responses"], ["responses"])


def _demo_paths(node):
    """Give authored artifact paths a neutral demo root, retaining their suffix."""
    if isinstance(node, list):
        for value in node:
            _demo_paths(value)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key in {"pdf_path", "appendix_path", "origine"} and isinstance(value, str):
                path = PureWindowsPath(value)
                if path.is_absolute():
                    node[key] = str(PureWindowsPath("C:/DemoTerminal", *path.parts[2:]))
            else:
                _demo_paths(value)


def _demo_cohort_ids(responses):
    """DEMO equality labels, not cryptographic attestations or credentials."""
    points = []
    def walk(node):
        if isinstance(node, dict):
            if "comparison_key" in node and node["comparison_key"] is not None:
                points.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(responses)
    values = [point["comparison_key"] for point in points]
    if not values:
        return
    if all(isinstance(value, str) and re.fullmatch(r"demo-cohort-[0-9]{2,}", value) for value in values):
        ids = {f"demo-cohort-{index:02d}" for index in range(1, len(set(values)) + 1)}
        if set(values) != ids:
            raise ValueError("Invalid DEMO cohort identities")
        return
    if not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in values):
        raise ValueError("Unknown or mixed DEMO cohort identities")
    mapping = {value: f"demo-cohort-{index:02d}" for index, value in enumerate(sorted(set(values)), 1)}
    for point in points:
        point["comparison_key"] = mapping[point["comparison_key"]]


def _mandate_display_copy(responses):
    """Synchronize authored display descriptions; machine values stay intact."""
    descriptions = {
        "tipo_investimento": (
            "Orizzonte con cui investe: lungo termine (anni), medio termine (mesi) o trading (settimane).",
            "Investment horizon: long term (years), medium term (months), or trading (weeks)."),
        "stile": (
            "Concentrato: ammette posizioni di peso elevato; diversificato: mantiene ogni posizione entro i limiti.",
            "Concentrated: allows high-conviction position weights; diversified: keeps each position within its cap."),
    }
    mandate = responses["GET /mandato"]
    for field, (italian, english) in descriptions.items():
        path = ["campi", field, "descrizione"]
        matches = [item for item in mandate["_presentation_v1"]["texts"] if item["path"] == path]
        if len(matches) != 1:
            raise ValueError("Missing or duplicate mandate description: " + field)
        mandate["campi"][field]["descrizione"] = italian
        matches[0].update(it=italian, en=english)


def _demo_presentation_copy(responses):
    """Keep authored labels aligned with the API, without rewriting stored prose."""
    market = responses["GET /market/overview?country=US"]
    names = {"DEMO.GOLD": "Gold DEMO", "DEMO.SILVER": "Silver DEMO",
             "DEMO.WTI": "WTI crude DEMO", "DEMO.BRENT": "Brent DEMO",
             "DEMO.NATGAS": "Natural gas DEMO", "DEMO.COPPER": "Copper DEMO"}
    for index, row in enumerate(market["commodities"]):
        _english_variant(market, ["commodities", index, "name"], names[row["ticker"]])
    beta = responses["GET /portfolio/metrics/beta_reconcile"]
    path = ["definitions", "portfolio_risk_spy"]
    beta["definitions"]["portfolio_risk_spy"] = beta["definitions"]["portfolio_risk_spy"].split(" (fix ", 1)[0]
    item = next(item for item in beta["_presentation_v1"]["texts"] if item["path"] == path)
    for language in ("it", "en"):
        item[language] = item[language].split(" (fix ", 1)[0]
    twr = responses["GET /portfolio/analytics/twr"]
    item = next(item for item in twr["_presentation_v1"]["texts"] if item["path"] == ["notes", 0])
    item.update(it="1 movimento del ledger precede il primo snapshot: nel tratto ricostruito i flussi sono gia' impliciti nei trade (non doppio-contati).",
                en="1 ledger movement precedes the first snapshot: flows in the reconstructed segment are already implicit in trades (not counted twice).")
    twr["notes"][0] = item["it"]
    item = next(item for item in twr["_presentation_v1"]["texts"] if item["path"] == ["as_of", "fx_basis"])
    item.update(it="official: FX live al momento dello snapshot; reconstructed: FX daily storico (CB al cambio storico)",
                en="official: live FX at snapshot time; reconstructed: historical daily FX (cost basis at historical FX)")
    twr["as_of"]["fx_basis"] = item["it"]
    preview = responses["POST /mandato/anteprima"]
    preview["testo"] = (preview["testo"].replace("declared on data n.d.", "declared on unavailable date")
                        .replace("broker: n.d.; tax residence: n.d.;", "broker: n/a; tax residence: n/a;"))


def generate(source: dict) -> dict:
    fixture = deepcopy(source["fixture_template"])
    book = deepcopy(source["book"])
    r = fixture["responses"]
    _demo_timeline(r)
    _demo_cohort_ids(r)
    _demo_paths(r)
    _mandate_display_copy(r)
    _demo_presentation_copy(r)
    # Match the producer's cent precision for total and component EUR VaR.
    var = r["GET /portfolio/analytics/var_contribution?confidence=0.05&lookback_days=252"]
    var["portfolio_var_eur_daily"] = round(sum(item["component_var_eur"] for item in var["items"]), 2)
    trades = book["trades"]
    # The two tranches refer to the same accepted proposal, not new positions.
    for trade in trades:
        if trade["action"] not in ("BUY", "ADD"):
            raise ValueError("This invented account contains purchases only")
        trade["linked_decision_id"] = 22 if trade["ticker"] == "DEMO.A" else 21
        trade["link_origin"] = "explicit"
    r["GET /trades"] = {"count": len(trades), "trades": trades}
    cash = book["cash_movements"]
    r["GET /cash/movements"] = {"count": len(cash), "movements": cash}
    old = {p["ticker"]: p for p in r["GET /portfolio"]["positions"]}
    positions = []
    for security in book["positions"]:
        ticker = security["ticker"]
        rows = [t for t in trades if t["ticker"] == ticker]
        quantity = sum(t["quantita"] for t in rows)
        cost = sum(t["quantita"] * t["prezzo"] for t in rows)
        value = quantity * security["prezzo_live"]
        p = {**old[ticker], **security, "quantita": quantity, "prezzo_medio": cost / quantity,
             "costo_eur": cost, "costo_eur_storico": cost, "valore_mercato": value,
             "pl_eur": value - cost, "pl_eur_fx": value - cost,
             "pl_pct": (value / cost - 1) * 100, "pl_pct_fx": round((value / cost - 1) * 100, 2),
             "data_apertura": min(t["data"] for t in rows)}
        positions.append(p)
        quote = r.get("GET /market/quote?ticker=" + ticker)
        if quote:
            quote.update(price=p["prezzo_live"], prev_close=p["prev_close"], currency=p["valuta"], name=p["nome"])
    invested = sum(p["valore_mercato"] for p in positions)
    cost = sum(p["costo_eur"] for p in positions)
    balance = sum(c["amount_eur"] for c in cash) - cost
    for p in positions:
        p["peso_pct"] = p["valore_mercato"] / invested * 100
    portfolio = r["GET /portfolio"]
    portfolio.update(positions=positions, n_positions=len(positions), totale_valore_mercato_eur=invested,
                     cash_disponibile_eur=balance, nav_total_eur=invested + balance,
                     totale_pl_eur=invested-cost, totale_pl_eur_fx=invested-cost,
                     totale_aperto_piu_realizzato_eur=invested-cost)

    decisions = r["GET /decisions?limit=500"]["decisions"]
    preview = book["preview"]
    demo_decision = {**deepcopy(next(d for d in decisions if d["id"] == 31)),
                     "id": preview["decision_id"], "memo_id": None, "action": preview["action"],
                     "ticker": preview["ticker"], "eur_amount": preview["quantity"] * preview["price"],
                     "confidence": "MEDIUM", "rationale": "DEMO ticket exercise: pending alternative, not an executed trade.",
                     "timing": "Demonstration only"}
    decisions[:] = [d for d in decisions if d["id"] != preview["decision_id"]]
    decisions.append(demo_decision)
    for decision in decisions:
        if decision["id"] == 31:
            decision["rationale"] = "EXISTING POSITION: concentration review at the memo's recorded market close."
        if decision.get("outcome_notes") and "auto-archiviata" in decision["outcome_notes"]:
            decision["outcome_notes"] = "Automatically archived: research without progress for over 30 days."
        if decision.get("esecuzione"):
            rows = sorted((deepcopy(t) for t in trades if t["linked_decision_id"] == decision["id"]), key=lambda t: t["id"])
            paid = sum(t["quantita"] * t["prezzo"] for t in rows)
            decision["esecuzione"].update(trades=rows, trade_ids=[t["id"] for t in rows], eur=paid,
                                          pct=round(paid / decision["eur_amount"] * 100))
    r["GET /decisions?limit=6&status=PENDING"] = {
        "decisions": [deepcopy(d) for d in decisions if d["status"] == "PENDING"][:6]}
    preview_body = r["POST /trade/preview"]
    delta = -preview["quantity"] * preview["price"]
    preview_body.update(cash_disponibile_eur=balance + delta, cash_delta_eur=delta,
                        data=preview["date"] + "T12:00:00", preview_id="DEMO-PREVIEW-NOT-A-LIVE-TOKEN")
    preview_body["decisione"]["id"] = preview["decision_id"]
    preview_body["fx"]["data"] = preview["date"]
    r["GET /portfolio/analytics/twr"]["copertura"]["n_trade_prima_del_primo_snapshot"] = len(trades)

    # These free-text fields had no EN variant in the independently made fragments.
    # Add presentation metadata without changing archived financial evidence.
    progress = r["GET /agents/progress?limit=100"]
    for index, run in enumerate(progress["runs"]):
        _english_variant(progress, ["runs", index, "scorecard", "method_note"],
                         "Market outcome of the call in quote currency, independent of decision status. "
                         "Four-week horizon, or one week when four weeks are not observable. "
                         "Specialist attribution uses tickers cited in reports from the same memo.")
    models = r["GET /fundamentals/models"]
    for model in models["models"]:
        holding = next(p for p in positions if p["ticker"] == model["ticker"])
        usable = model["fair_value"] is not None
        clock = fixture["clock"]["now"]
        raw = {"contract": "market_quote/1", "status": "ok" if usable else "stale",
               "message": "Invented documentation observation; fair value remains at the model date.",
               "source_id": "https://example.invalid/demo/quotes/" + model["ticker"],
               "source_status": "ok", "acquired_as_of": clock[:10],
               "information_cutoff": clock[:10], "exchange": "DEMO",
               "symbol": model["ticker"], "info_symbol": model["ticker"], "exchange_timezone": "Europe/Rome",
               "quote_source_name": "Synthetic documentation fixture", "delayed_minutes": 0,
               "currency": holding["valuta"], "price": holding["prezzo_live"],
               "observed_at": "2026-09-11T15:30:00Z" if usable else "2026-09-10T15:30:00Z",
               "observed_local_date": "2026-09-11" if usable else "2026-09-10",
               "price_model": model["price_at_thesis"],
               "price_model_as_of": model["detail"]["valuation_date"],
               "price_move_since_valuation_pct": round((holding["prezzo_live"] / model["price_at_thesis"] - 1) * 100, 1) if usable else None,
               "fv_basis": "valuation_date_no_rollforward", "sanity_basis": "price_model",
               "freshness_policy": "previous_weekday; holidays_not_modelled"}
        for scenario in ("bear", "base", "bull"):
            value = model["detail"].get("fair_value_" + scenario)
            raw["upside_" + scenario + "_pct"] = round((value / raw["price"] - 1) * 100, 1) if usable and value is not None else None
        model["detail"]["market_quote"] = raw
        model["market_quote"] = {**deepcopy(raw), "status_at_read": raw["status"]}
        model["price_model_as_of"] = raw["price_model_as_of"]
        model["upside_today_pct"] = raw["upside_base_pct"]
    for path in [["models", 1, "analytical_quality", "issues", 0],
                 ["models", 1, "acquisition_tasks", 0, "reason"],
                 ["models", 1, "detail", "analytical_quality", "issues", 0],
                 ["models", 1, "detail", "acquisition_tasks", 0, "reason"]]:
        _english_variant(models, path, "capex_pct: base: documented input missing or not consumed")
    _english_variant(portfolio, ["as_of", "price_basis"],
                     "Synthetic documentation price snapshots; all instruments and observations are invented")
    calendar = r["GET /news/economic-calendar?days_ahead=14"]
    calendar["fonti_mute"]["finnhub /calendar/economic"] = (
        "Esempio sintetico DEMO: fonte del calendario economico volutamente muta; nessuna richiesta live.")
    _english_variant(calendar, ["fonti_mute", "finnhub /calendar/economic"],
                     "Synthetic DEMO: economic calendar source intentionally muted; no live request.")
    _english_variant(calendar, ["fonti_mute", "finnhub /calendar/earnings"],
                     "Earnings calendar not queried: the synthetic book has no US-listed holdings")

    for scene in fixture["scenes"]:
        scene["note"] = SCENE_NOTES[scene["id"]]
        for step in scene.get("steps", []):
            if "f7-decisione" in step.get("select", {}):
                step["select"]["f7-decisione"] = str(preview["decision_id"])
            if "f7-rz" in step.get("fill", {}):
                step["fill"]["f7-rz"] = "DEMO ticket exercise: two shares; preview only, no execution."
    # Compatibility aliases carry the identical new payload, never the old fixture.
    for alias, target in ALIASES.items():
        r[alias] = deepcopy(r[target])
    _canonical_presentation(fixture)
    fixture["responses"] = dict(sorted(r.items()))
    validate(fixture)
    return fixture


def validate(fixture: dict) -> None:
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def equal(left, right, message):
        require(isinstance(left, (int, float)) and not isinstance(left, bool)
                and math.isfinite(left) and math.isclose(left, right, abs_tol=1e-6, rel_tol=1e-8), message)

    r = fixture["responses"]
    p = r["GET /portfolio"]
    trades = r["GET /trades"]["trades"]
    movements = r["GET /cash/movements"]["movements"]
    holdings = {row["ticker"]: row for row in p["positions"]}
    require(len(holdings) == len(p["positions"]) == p["n_positions"], "Duplicate or missing holding")
    require(all(t.startswith("DEMO.") for t in holdings), "Non-DEMO holding")
    require(set(t["ticker"] for t in trades) == set(holdings), "Trade/holding universe mismatch")
    equal(len(trades), r["GET /trades"]["count"], "Trade count mismatch")
    equal(len(movements), r["GET /cash/movements"]["count"], "Cash movement count mismatch")
    require(len({t["id"] for t in trades}) == len(trades), "Duplicate trade ID")
    for ticker, holding in holdings.items():
        rows = [t for t in trades if t["ticker"] == ticker]
        quantity = sum(t["quantita"] for t in rows)
        cost = sum(t["quantita"] * t["prezzo"] for t in rows)
        equal(holding["quantita"], quantity, "Trade quantity mismatch")
        equal(holding["costo_eur"], cost, "Trade cost mismatch")
        equal(holding["costo_eur_storico"], cost, "Historical cost mismatch")
        equal(holding["prezzo_medio"], cost / quantity, "Average cost mismatch")
        equal(holding["valore_mercato"], quantity * holding["prezzo_live"], "Holding value mismatch")
        equal(holding["peso_pct"], holding["valore_mercato"] / p["totale_valore_mercato_eur"] * 100, "Holding weight mismatch")
        quote = r.get("GET /market/quote?ticker=" + ticker)
        if quote:
            equal(quote["price"], holding["prezzo_live"], "Cross-page quote mismatch")
            equal(quote["prev_close"], holding["prev_close"], "Cross-page previous close mismatch")
        bars = r.get("GET /market/ohlc?interval=1d&period=1y&ticker=" + ticker, {}).get("bars")
        if bars:
            equal(bars[-1]["c"], holding["prezzo_live"], "Market chart final close mismatch")
    invested = sum(x["valore_mercato"] for x in holdings.values())
    cost = sum(t["quantita"] * t["prezzo"] for t in trades)
    balance = sum(c["amount_eur"] for c in movements) - cost
    equal(p["cash_disponibile_eur"], balance, "Cash ledger mismatch")
    equal(p["totale_valore_mercato_eur"], invested, "Invested total mismatch")
    equal(p["nav_total_eur"], balance + invested, "NAV total mismatch")
    equal(p["totale_pl_eur"], invested - cost, "P&L mismatch")
    history = r["GET /portfolio/analytics/nav_history"]
    for field, expected in [("final_nav_eur", invested), ("final_cost_basis_eur", cost), ("cash_eur", balance)]:
        equal(history[field], expected, "Historical analytics mismatch: " + field)
    equal(history["nav_eur"][-1], invested, "Historical final observation mismatch")
    equal(history["nav_total_eur"][-1], invested + balance, "Historical total NAV mismatch")
    equal(r["GET /portfolio/risk"]["nav_book_total_eur"], invested, "Risk NAV mismatch")
    equal(r["GET /portfolio/risk"]["nav_eur"], invested, "Risk measured NAV mismatch")
    risk = r["GET /portfolio/risk"]["per_asset"]
    factors = r["GET /portfolio/factors?period=3y"]["per_holding"]
    require(set(risk) == set(factors) == set(holdings), "Risk/factor universe mismatch")
    for ticker, holding in holdings.items():
        equal(risk[ticker]["weight_pct"], round(holding["peso_pct"], 2), "Risk weight mismatch")
        equal(factors[ticker]["weight_pct"], round(holding["peso_pct"], 2), "Factor weight mismatch")
        equal(factors[ticker]["weight"], round(holding["peso_pct"] / 100, 4), "Factor fractional weight mismatch")
    for row in r["GET /portfolio/analytics/concentration"]["by_ticker"]["top_holdings"]:
        equal(row["weight_pct"], round(holdings[row["ticker"]]["peso_pct"], 2), "Concentration weight mismatch")
    for row in r["GET /portfolio/analytics/liquidity"]["items"]:
        equal(row["position_eur"], holdings[row["ticker"]]["valore_mercato"], "Liquidity position mismatch")
    for row in r["GET /news/briefing/current"]["portfolio_top"]:
        equal(row["weight_pct"], round(holdings[row["ticker"]]["peso_pct"], 6), "Briefing weight mismatch")
        equal(row["pnl_pct"], round(holdings[row["ticker"]]["pl_pct"], 6), "Briefing P&L mismatch")
    for model in r["GET /fundamentals/models"]["models"]:
        quote = model["market_quote"]
        equal(quote["price"], holdings[model["ticker"]]["prezzo_live"], "Valuation observed price mismatch")
        equal(quote["price_model"], model["price_at_thesis"], "Valuation historical price mismatch")
        if quote["status_at_read"] == "ok":
            equal(model["upside_today_pct"], round((model["fair_value"] / quote["price"] - 1) * 100, 1), "Observed upside mismatch")
        else:
            require(model["upside_today_pct"] is None, "Stale quote must not display upside")
    equal(r[MC]["base_nav_eur"], invested, "Monte Carlo NAV mismatch")
    require(set(r[MC]["tickers_analyzed"]) == set(holdings), "Monte Carlo universe mismatch")
    for ticker, weight in r[MC]["weights"].items():
        # The actual response contract rounds these fractional weights to four places.
        equal(weight, round(holdings[ticker]["peso_pct"] / 100, 4), "Monte Carlo weight mismatch")
    decisions = r["GET /decisions?limit=500"]["decisions"]
    ids = {d["id"] for d in decisions}
    require(len(ids) == len(decisions), "Duplicate decision id")
    require(all(t["linked_decision_id"] in ids for t in trades), "Ledger decision missing")
    for decision in decisions:
        execution = decision.get("esecuzione")
        if execution:
            rows = sorted((t for t in trades if t["linked_decision_id"] == decision["id"]), key=lambda t: t["id"])
            require(execution["trade_ids"] == [t["id"] for t in rows], "Decision execution IDs mismatch")
            require(execution["trades"] == rows, "Decision execution ledger mismatch")
            equal(execution["eur"], sum(t["quantita"] * t["prezzo"] for t in rows), "Decision execution amount mismatch")
    preview = r["POST /trade/preview"]
    require(preview["decisione"]["id"] in ids, "Preview decision missing")
    decision = next(d for d in decisions if d["id"] == preview["decisione"]["id"])
    equal(preview["cash_delta_eur"], -decision["eur_amount"], "Preview proposal mismatch")
    equal(preview["cash_disponibile_eur"], balance + preview["cash_delta_eur"], "Preview cash mismatch")
    for alias, target in ALIASES.items():
        require(r[alias] == r[target], "Compatibility alias drift: " + alias)
    require(fixture["languages"] == ["en"], "Documentation must be English only")
    require(fixture["clock"] == {"now": "2026-09-13T08:11:40.000Z", "timezone": "Europe/Rome"},
            "Documentation clock must match the authored run and heartbeat")
    validate_observation_times(fixture)
    scenes = fixture["scenes"]
    require(len(scenes) == len({s["id"] for s in scenes}) == 31, "Missing/duplicate documentation scene")
    require({s["route"] for s in scenes} == PAGE_ROUTES and any(s["id"] == "settings" for s in scenes), "Incomplete destination coverage")
    for scene in scenes:
        require(scene["required"] and all(k in r or k == "GET /preferences" for k in scene["required"]), "Missing scene response: " + scene["id"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="Fail on fixture drift; write nothing")
    args = parser.parse_args(argv)
    fixture = generate(load_source(args.source))
    data = serialize_fixture(encode_fixture(fixture)).encode("utf-8")
    if args.check:
        if not args.output.exists() or args.output.read_bytes() != data:
            print("DEMO fixture differs from its source; no files written")
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(data)
    print(json.dumps({"ok": True, "check_only": args.check, "scenes": len(fixture["scenes"]),
                      "destinations": 19, "languages": fixture["languages"], "account": "DEMO"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
