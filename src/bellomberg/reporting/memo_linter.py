"""
memo_linter.py — LINTER POST-MEMO (audit/07 §3-4, P2; v1 SOLO FLAG come #191).

Errori meccanici ricorrenti nei 6 memo auditati che nessuno intercettava:
percentuali "del NAV" calcolate sull'investito (#33/#36/#39), dry powder che
non torna di 6-25k (#25/#39), scenari che sommano oltre il 100% (#25),
decine di numeri chiave senza tag [src]. Tutti rilevabili SENZA dati esterni:
check DETERMINISTICI (niente LLM).

Pattern identico all'ACTION VALIDATOR #191 (scelta PM: Opzione A flag-only):
blocco markdown appeso al memo, numeri del Capo INTOCCATI, un guasto del
linter non tocca la run. Nella run gira DOPO il Capo e PRIMA dell'ACTION
VALIDATOR, cosi' analizza il memo pulito.
"""
import re
from datetime import datetime
from bellomberg.core.language import scoped_language, text as _lt

# tolleranze tarate per non fare rumore (flag-only ma un falso allarme a memo
# brucia la fiducia del PM nel blocco)
NAV_PCT_TOL_ABS = 0.5       # punti percentuali
NAV_PCT_TOL_REL = 0.08      # 8% relativo sul valore dichiarato
SCENARI_SUM_RANGE = (95.0, 105.0)
CASH_TOL_REL = 0.05         # 5% sul cash del DB
SRC_MIN_MISSING = 3         # sotto questa soglia il conteggio [src] non fa rumore

_AMOUNT = r"(?:€\s*)?\d[\d.,]*\s*(?:(?:billion|million|thousand|miliard[oi]|milion[ei]|mila|mld|mln|bn|mm|k|m)\b)?\s*(?:€|eur)?"


def _amt(s):
    from bellomberg.storage.memory_db import _parse_eur_amount
    return _parse_eur_amount(s)


def _pct(s):
    try:
        return float(str(s).replace(",", "."))
    except Exception:
        return None


def _check_nav_percentages(memo, nav):
    """'25k (9,7% del NAV)' e '9,7% del NAV (25k)': ricalcolo contro il NAV vero."""
    if not nav or nav <= 0:
        return []
    out = []
    pats = [
        # importo ( X% del NAV )
        re.compile(r"(" + _AMOUNT + r")\s*\(\s*(?:~|circa\s*|about\s*)?(\d+(?:[.,]\d+)?)\s*%\s*(?:del|of(?:\s+the)?)\s*NAV\s*\)", re.IGNORECASE),
        # X% del NAV ( importo )
        re.compile(r"(?<![\d,.])(\d+(?:[.,]\d+)?)\s*%\s*(?:del|of(?:\s+the)?)\s*NAV\s*\(\s*(?:~|circa\s*|about\s*)?(" + _AMOUNT + r")\s*\)", re.IGNORECASE),
    ]
    for i, pat in enumerate(pats):
        for m in pat.finditer(memo):
            raw_amt, raw_pct = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            amount, claimed = _amt(raw_amt), _pct(raw_pct)
            if not amount or claimed is None or amount < 100:
                continue
            implied = amount / nav * 100.0
            if abs(implied - claimed) > max(NAV_PCT_TOL_ABS, claimed * NAV_PCT_TOL_REL):
                out.append(
                    _lt(f"**% del NAV non torna**: \"{m.group(0).strip()[:70]}\" — "
                        f"{amount:,.0f}€ su NAV {nav:,.0f}€ = {implied:.1f}%, "
                        f"dichiarato {claimed:.1f}% (base probabilmente diversa dal NAV)",
                        f"**NAV percentage mismatch**: \"{m.group(0).strip()[:70]}\" — "
                        f"€{amount:,.0f} / NAV €{nav:,.0f} = {implied:.1f}%, "
                        f"disclosed {claimed:.1f}% (the denominator likely differs from NAV)"))
    return out[:4]


def _check_scenario_sum(memo):
    """Check each scenario distribution, never adding its declared total twice."""
    def cells(row):
        return [c.strip() for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))]

    def normalized(value):
        return re.sub(r"[*_`]", "", value).strip().rstrip(":").strip().casefold()

    out = []
    sections = re.finditer(
        r"^##[ \t]+[^\n]*(?:Tabella Scenari|Scenario Table)[^\n]*\n"
        r"(.*?)(?=^#{1,2}[ \t]+|\Z)", memo, re.IGNORECASE | re.DOTALL | re.MULTILINE)
    for section in sections:
        body = section.group(1)
        previous_end = 0
        for index, block in enumerate(re.finditer(
                r"^[ \t]*\|[^\n]*(?:\n[ \t]*\|[^\n]*)*", body, re.MULTILINE), 1):
            context = body[previous_end:block.start()]
            previous_end = block.end()
            rows = [cells(row) for row in block.group().splitlines()]
            if len(rows) < 3 or not all(re.fullmatch(r":?-{3,}:?", c) for c in rows[1]):
                continue
            header = [normalized(c) for c in rows[0]]
            col = next((j for j, h in enumerate(header) if "prob" in h), None)
            if col is None:
                continue
            label_col = next((j for j, h in enumerate(header)
                              if re.search(r"\b(?:scenario|scenari|case|caso)\b", h)), 0)
            data = rows[2:]
            prefix = _lt(f"**Tabella Scenari (tabella {index})**", f"**Scenario Table (table {index})**")
            denominator_cols = [j for j, h in enumerate(header)
                                if re.search(r"denominat|condizion|condition|given", h) and j != col]
            different_bases = any(len({normalized(row[j]) for row in data
                                       if len(row) > max(j, label_col)
                                       and normalized(row[label_col]) not in {"totale", "total"}}) > 1
                                  for j in denominator_cols)
            probability_text = header[col] + "\n" + "\n".join(
                row[col] for row in data if len(row) > col)
            conditional = re.search(
                r"\b(?:condizionat\w*|conditional\w*)\b|\bp\s*\([^)]*\\\|",
                probability_text, re.IGNORECASE) or re.search(
                r"\bp\s*\([^)]*\\\|",
                "\n".join(row[label_col] for row in data if len(row) > label_col),
                re.IGNORECASE) or re.search(
                r"\bprobabilit\S*\s+(?:(?:sono|are)\s+)?(?:condizionat\w*|conditional)\b|"
                r"\bconditional\s+probabilit\w*\b",
                context + "\n" + section.group().split("\n", 1)[0], re.IGNORECASE)
            if conditional or different_bases:
                out.append(prefix + _lt(
                    ": controllo non applicabile — probabilita' condizionate o denominatori differenti; "
                    "la somma a 100% non e' verificabile come distribuzione unica.",
                    ": check not applicable — conditional probabilities or different denominators; "
                    "a total of 100% cannot be checked as a single distribution."))
                continue
            probs, declared = [], []
            missing = False
            numeric_input = False
            for row in data:
                if len(row) <= max(col, label_col):
                    missing = True
                    continue
                is_total = normalized(row[label_col]) in {"totale", "total"}
                numeric_input = numeric_input or bool(re.search(r"\d|%", row[col]))
                interval = re.search(r"\d(?:[.,]\d+)?\s*%?\s*[-–—]\s*\d", row[col])
                negative = re.search(r"[-−]\s*\d+(?:[.,]\d+)?\s*%", row[col])
                matches = re.findall(r"(?<![\d.,+\-–—])\+?(\d+(?:[.,]\d+)?)\s*%", row[col])
                if interval or negative or len(matches) != 1:
                    missing = True
                    continue
                probability = _pct(matches[0])
                if not is_total and not (0 <= probability <= 100):
                    missing = True
                    continue
                (declared if is_total else probs).append(probability)
            if missing and numeric_input:
                out.append(prefix + _lt(
                    ": controllo non applicabile — probabilita' mancanti o ambigue; somma non verificabile.",
                    ": check not applicable — missing or ambiguous probabilities; total cannot be checked."))
                continue
            if not probs:
                continue
            tot = sum(probs)
            if len(probs) >= 2 and not (SCENARI_SUM_RANGE[0] <= tot <= SCENARI_SUM_RANGE[1]):
                out.append(prefix + _lt(
                    f": le probabilita' sommano {tot:g}% "
                    f"({' + '.join(f'{p:g}' for p in probs)}) invece di ~100: "
                    "scenari non mutuamente esclusivi o proxy (regole sez. 10)",
                    f": probabilities total {tot:g}% "
                    f"({' + '.join(f'{p:g}' for p in probs)}) instead of ~100: "
                    "scenarios are not mutually exclusive, or are proxies (section 10 rules)"))
            for total in declared:
                # The existing ~100 tolerance also bounds the arithmetic residual.
                if not (SCENARI_SUM_RANGE[0] - 100 <= total - tot <= SCENARI_SUM_RANGE[1] - 100):
                    out.append(prefix + _lt(
                        f": totale dichiarato {total:g}% non coerente con la somma degli scenari {tot:g}%.",
                        f": declared total {total:g}% does not match the scenario sum {tot:g}%."))
    return out


def _check_cash_quadrature(memo, cash_eur):
    """Prima dichiarazione di livello del dry powder/cash vs il cash del DB."""
    if not cash_eur or cash_eur <= 0:
        return []
    _kw = r"(?:dry powder|liquidit[aà'] disponibile|cash disponibile|available cash|cash available|available liquidity)"
    pats = [
        # "dry powder di 101k" / "cash disponibile: 34.716€"
        re.compile(_kw + r"\D{0,25}?(" + _AMOUNT + r")", re.IGNORECASE),
        # "~30k di dry powder" (frasario reale dei memo #39/#42)
        re.compile(r"[~≈]?\s*(" + _AMOUNT + r")\s*(?:di|of|in)\s*" + _kw, re.IGNORECASE),
    ]
    m = min((mm for p in pats if (mm := p.search(memo))),
            key=lambda mm: mm.start(), default=None)
    if not m:
        return []
    amount = _amt(m.group(1))
    if not amount or amount < 1000:
        return []
    if abs(amount - cash_eur) > cash_eur * CASH_TOL_REL:
        return [_lt(f"**Dry powder non quadra**: il memo dichiara {amount:,.0f}€, "
                f"il DB dice {cash_eur:,.0f}€ di cash disponibile "
                f"(delta {amount - cash_eur:+,.0f}€)",
                f"**Cash mismatch**: the memo states €{amount:,.0f}; "
                f"Database available cash is €{cash_eur:,.0f} "
                f"(difference €{amount - cash_eur:+,.0f})")]
    return []


def _check_src_coverage(memo):
    """Importi in EUR in prosa senza un tag [src|Decisione #] nella stessa riga."""
    missing = []
    amount_line = re.compile(r"(?:€\s*\d|\d[\d.,]*\s*(?:(?:k|mila|thousand|million|billion|bn|mm|mln|mld)\b|€)|\d[\d.,]*\s*eur\b)",
                             re.IGNORECASE)
    for line in memo.split("\n"):
        ls = line.strip()
        if not ls or ls.startswith(("|", "#", "*(")):
            continue  # tabelle (hanno i loro validator), header, footer dei blocchi
        if "[src" in ls.lower() or re.search(r"(?:\[decisione?\b|decisione?\s*#)", ls, re.IGNORECASE):
            continue
        if amount_line.search(ls):
            missing.append(ls[:75])
    if len(missing) < SRC_MIN_MISSING:
        return []
    esempi = "; ".join('"' + s + '..."' for s in missing[:3])
    return [_lt(f"**{len(missing)} righe con importi in EUR senza tag [src]** — esempi: {esempi}",
                f"**{len(missing)} lines with EUR amounts and no [src] tag** — examples: {esempi}")]


_DRAWDOWN_PCT = re.compile(r"(?P<pct>-\s?\d{1,3}(?:[.,]\d+)?)\s?%\s+(?:da(?:l|i)\s+massim|(?:from|below)\s+(?:the\s+)?(?:highs?|peaks?))", re.IGNORECASE)
_DRAWDOWN_STOP = {"IL", "LA", "LO", "LE", "GLI", "I", "UN", "UNA", "NEL", "NELLA", "AL", "ALLA", "DEL",
                  "DELLA", "CON", "PER", "MA", "E", "SE", "CHE", "IN", "A", "DA", "OGGI", "IERI", "RSI",
                  "NAV", "EUR", "USD", "P&L", "SMA20", "SMA50", "THE", "AN", "AT", "OF", "WITH",
                  "FOR", "BUT", "AND", "IF", "THAT", "FROM", "TODAY", "YESTERDAY"}


def _check_drawdown_duplicati(memo):
    """Audit 11/09 (Fable 5.1, memo #53): lo stesso indice con DUE drawdown «dal massimo»
    (-10,8% dal tool, -14,79% da un articolo web vecchio) nello stesso memo, mai riconciliati.
    Check deterministico e generale: stesso nome + «-X% dal/dai massim…» con valori diversi.
    Il nome e' l'ultima parola maiuscola (o simbolo ^INDICE) della stessa riga prima del
    numero, saltando articoli e sigle di contorno."""
    visti = {}
    for m in _DRAWDOWN_PCT.finditer(memo):
        prima = memo[max(0, m.start() - 90):m.start()].split("\n")[-1]
        tokens = re.findall(r"[A-Z^][A-Za-z0-9^&.\-]{1,30}", prima)
        nome = next((t for t in reversed(tokens) if t.upper().strip(".") not in _DRAWDOWN_STOP), None)
        if not nome:
            continue
        chiave = nome.upper().strip(".")
        val = _pct(m.group("pct").replace(" ", ""))
        if val is None:
            continue
        visti.setdefault(chiave, {"nome": nome, "valori": []})
        if val not in visti[chiave]["valori"]:
            visti[chiave]["valori"].append(val)
    out = []
    for chiave, info in visti.items():
        if len(info["valori"]) > 1 and (max(info["valori"]) - min(info["valori"])) > 0.3:
            out.append(_lt("**{}: piu' drawdown «dal massimo» nello stesso memo** ({}) senza "
                           "riconciliazione — un numero da tool e uno da fonte web/vecchia?",
                           "**{}: multiple drawdowns from highs in the same memo** ({}) without "
                           "reconciliation — a tool value and an older/web-source value?").format(
                           info["nome"], " / ".join("{:g}%".format(v) for v in info["valori"])))
    return out


@scoped_language
def build_linter_block(memo_markdown, portfolio=None):
    """Ritorna il blocco markdown '## MEMO LINTER' (o stringa vuota se pulito).
    Non solleva mai: ogni check e' guarded (il chiamante ha comunque il try/except)."""
    nav = cash = None
    if isinstance(portfolio, dict):
        nav = portfolio.get("nav_total_eur")
        cash = portfolio.get("cash_disponibile_eur")

    warnings = []
    for label, check in (
            (_lt("percentuali del NAV", "NAV percentages"), lambda: _check_nav_percentages(memo_markdown, nav)),
            (_lt("somma scenari", "scenario totals"), lambda: _check_scenario_sum(memo_markdown)),
            (_lt("quadratura liquidita'", "cash reconciliation"), lambda: _check_cash_quadrature(memo_markdown, cash)),
            (_lt("copertura fonti", "source coverage"), lambda: _check_src_coverage(memo_markdown)),
            (_lt("drawdown doppi", "conflicting drawdowns"), lambda: _check_drawdown_duplicati(memo_markdown))):
        try:
            warnings.extend(check())
        except Exception as exc:
            # Flag-only: keep running the other checks, but never confuse a
            # failed check with a clean result. Do not expose the raw payload.
            warnings.append("**CHECK_UNAVAILABLE — " + label + "**: " + _lt(
                "controllo automatico non eseguito", "automatic check not performed")
                + " (" + type(exc).__name__ + ").")

    if not warnings:
        return ""
    block = [_lt("## MEMO LINTER (verifica aritmetica automatica — flag-only, i numeri del Capo NON sono stati modificati)",
                 "## MEMO LINTER (automatic arithmetic checks — flags only, the Capo's numbers have NOT been changed)")]
    block += [f"- {w}" for w in warnings]
    block.append(f"*(linter v1 — {datetime.now().strftime('%d/%m %H:%M')}; " + _lt(
                 "check deterministici: % del NAV ricalcolate, somma scenari, quadratura dry powder, copertura tag [src], drawdown doppi)*",
                 "deterministic checks: NAV percentages, scenario totals, cash reconciliation, [src] coverage, conflicting drawdowns)*"))
    return "\n".join(block)


if __name__ == "__main__":
    # Test standalone read-only sull'ultimo memo reale
    from bellomberg.storage.memory_db import MemoryDB
    db = MemoryDB()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, full_markdown FROM memos WHERE LENGTH(full_markdown) > 1000 "
            "AND substr(COALESCE(notes,''),1,11) <> 'trade_idea:' "
            "ORDER BY id DESC LIMIT 3").fetchall()
    pf = None
    try:
        pf = db.get_portfolio_summary()
        print(f"NAV {pf['nav_total_eur']:,.0f}€, cash {pf['cash_disponibile_eur']:,.0f}€")
    except Exception as e:
        print("[test] portfolio non disponibile:", e)
    for memo_id, md in rows:
        print("=" * 70)
        print(f"Test su memo #{memo_id} ({len(md)} char)")
        print(build_linter_block(md, pf) or "(nessun avvertimento)")
