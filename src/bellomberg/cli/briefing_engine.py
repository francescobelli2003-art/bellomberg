"""
BELLOMBERG - Daily Market Briefing Engine

Genera briefing stile FT Briefing / Bloomberg Daybook 4x/giorno (07:30, 11:30, 15:30, 19:30 Lun-Ven).
Cache su disk in data/briefing_cache.json.

PIPELINE:
1. Pulla news ultime N ore dal DB news_feed (gia popolato dallo scheduler ogni 15min)
2. Aggiunge macro snapshot: yield 10y, VIX, DXY, oil, gold, BTC (via market_data)
3. Aggiunge portfolio snapshot conciso (top 5 holdings + cash)
4. Chiama Haiku 4.5 con prompt strutturato -> 4 paragrafi:
   - MARKET TONE: 2-3 frasi su risk-on/risk-off, what moved overnight
   - MACRO HIGHLIGHTS: 2-3 frasi su top news macro/geo del periodo
   - PORTFOLIO IMPACT: 2-3 frasi su impatto specifico sulle posizioni del PM
   - WATCH TODAY: 1-2 frasi su catalyst attesi nel resto della giornata

API:
    generate_briefing(period: str) -> dict
        period in {"morning", "midday", "afternoon", "evening"}
    get_current_briefing() -> dict   # ritorna ultimo cached
    needs_refresh() -> bool
"""
from bellomberg.core.config import PM_DESC  # B3 (02/09)
import json
import os
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from bellomberg.core.paths import DATA_DIR
CACHE_PATH = str(DATA_DIR / "briefing_cache.json")
CACHE_TTL_SEC = 4 * 3600  # 4 hours per slot
NEWS_LOOKBACK_HOURS = 8

# Slot definitions
PERIOD_SLOTS = {
    "morning":   {"hour": 7,  "label": "Morning Briefing (07:30 CET)",   "lookback_h": 14, "focus": "overnight + Asia close"},
    "midday":    {"hour": 11, "label": "Midday Briefing (11:30 CET)",    "lookback_h": 5,  "focus": "EU morning + pre-US open"},
    "afternoon": {"hour": 15, "label": "Afternoon Briefing (15:30 CET)", "lookback_h": 5,  "focus": "US open + EU close"},
    "evening":   {"hour": 19, "label": "Evening Briefing (19:30 CET)",   "lookback_h": 5,  "focus": "US session + after-hours"},
}


def _log(msg: str):
    # pipe stdout morta (backend zombie, autopsia (40)): log perso, mai eccezione
    try:
        print(f"[BRIEFING] {msg}", flush=True)
    except OSError:
        pass


def _ensure_cache_dir():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)


def _load_cache() -> Dict[str, Any]:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log(f"cache load failed: {e}")
        return {}


def _save_cache(data: Dict[str, Any]):
    _ensure_cache_dir()
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        _log(f"cache save failed: {e}")


def _current_period() -> str:
    """Determina lo slot corrente in base all'ora."""
    h = datetime.now().hour
    if h < 9:
        return "morning"
    elif h < 13:
        return "midday"
    elif h < 17:
        return "afternoon"
    else:
        return "evening"


def _fetch_recent_news(lookback_h: int = 8, limit: int = 50) -> List[Dict[str, Any]]:
    """Recupera news dal DB news_feed delle ultime N ore.
    Mix bilanciato: portfolio news + macro/geopolitica/conflitti per evitare
    che il briefing veda solo ticker e perda eventi macro che muovono i mercati.
    """
    conn = None
    try:
        from bellomberg.storage.memory_db import MemoryDB, connect_sqlite
        db = MemoryDB()
        conn = connect_sqlite(db.db_path)  # hardening #32: WAL + busy_timeout
        cur = conn.cursor()
        cutoff = (datetime.now() - timedelta(hours=lookback_h)).isoformat()
        # Geo/macro: finestre ESTESE (#161) — guerre, accordi di pace, mosse Fed/BCE
        # restano fondamentali per giorni: non possono sparire con lookback brevi (5h)
        cutoff_theme = (datetime.now() - timedelta(hours=max(lookback_h, 24))).isoformat()
        cutoff_geo = (datetime.now() - timedelta(hours=max(lookback_h, 48))).isoformat()

        # Step 1: top relevance news (qualsiasi tipo)
        # FIX: colonna corretta = pulled_at (non saved_at)
        cur.execute("""
            SELECT title, snippet, provider, sentiment, relevance, ticker_mentioned, published_at, theme
            FROM news_feed
            WHERE pulled_at >= ?
            ORDER BY relevance DESC, pulled_at DESC
            LIMIT ?
        """, (cutoff, limit))
        top_rows = cur.fetchall()

        # Step 2: news con theme macro/geopolitica (priorita' extra)
        # Catch news taggate per theme di guerra/Fed/ECB/crisi
        cur.execute("""
            SELECT title, snippet, provider, sentiment, relevance, ticker_mentioned, published_at, theme
            FROM news_feed
            WHERE pulled_at >= ?
              AND theme IS NOT NULL AND theme != ''
              AND theme IN ('ukraine','middle_east','fed','ecb','china','cpi','italy','btc_etf','geo','geopolitics','war','tariff','conflict','iran','israel')
            ORDER BY relevance DESC, pulled_at DESC
            LIMIT 20
        """, (cutoff_theme,))
        macro_rows = cur.fetchall()

        # Step 3: pattern-match nei titoli per intercettare guerre/conflitti anche
        # se theme non e' stato settato (fallback safety net)
        cur.execute("""
            SELECT title, snippet, provider, sentiment, relevance, ticker_mentioned, published_at, theme
            FROM news_feed
            WHERE pulled_at >= ?
              AND (lower(title) LIKE '%iran%' OR lower(title) LIKE '%israel%'
                   OR lower(title) LIKE '%ukraine%' OR lower(title) LIKE '%russia%'
                   OR lower(title) LIKE '%putin%' OR lower(title) LIKE '%zelensky%'
                   OR lower(title) LIKE '%nuclear%' OR lower(title) LIKE '%missile%'
                   OR lower(title) LIKE '%ceasefire%' OR lower(title) LIKE '%peace%'
                   OR lower(title) LIKE '%sanction%' OR lower(title) LIKE '%fed%'
                   OR lower(title) LIKE '%ecb%' OR lower(title) LIKE '%powell%'
                   OR lower(title) LIKE '%lagarde%' OR lower(title) LIKE '%opec%'
                   OR lower(title) LIKE '%cpi%' OR lower(title) LIKE '%inflation%'
                   OR lower(title) LIKE '%tariff%' OR lower(title) LIKE '%trump%'
                   OR lower(title) LIKE '%china%' OR lower(title) LIKE '%taiwan%')
            ORDER BY relevance DESC, pulled_at DESC
            LIMIT 30
        """, (cutoff_geo,))
        geo_rows = cur.fetchall()

        conn.close()

        # Merge + dedupe by title
        seen = set()
        rows = []
        # Mix bilanciato: cap per categoria, cosi' le news geo/macro non
        # spariscono ma nemmeno saturano il prompt cancellando le ticker news
        for source_rows, cap in ((macro_rows, 8), (geo_rows, 10), (top_rows, limit)):
            taken = 0
            for r in source_rows:
                key = (r[0] or "").lower().strip()[:120]
                if key in seen or not key:
                    continue
                seen.add(key)
                rows.append(r)
                taken += 1
                if taken >= cap or len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
        return [
            {
                "title": r[0] or "",
                "snippet": (r[1] or "")[:200],
                "provider": r[2] or "",
                "sentiment": r[3] or "neutral",
                "relevance": r[4] or 5,
                "ticker": r[5] or "",
                "published_at": r[6] or "",
            }
            for r in rows
        ]
    except Exception as e:
        _log(f"fetch_recent_news failed: {e}")
        return []
    finally:
        # audit/11 §4: la connessione WAL non deve restare aperta nel backend se una
        # cur.execute fallisce (gia' successo col rename saved_at->pulled_at)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _get_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Prezzo + variazione % giornaliera via yfinance.
    (bugfix: 'from market_data import get_price_live' importava una funzione inesistente)"""
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period="2d")
        if h is None or h.empty:
            return None
        close = h["Close"]
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) >= 2 else None
        chg = round((last - prev) / prev * 100, 2) if prev else None
        return {"price": round(last, 4), "change_pct": chg}
    except Exception:
        return None


def _fetch_macro_snapshot() -> Dict[str, Any]:
    """Snapshot dei macro indicators chiave + market levels."""
    out = {}
    try:
        for sym, label in [
            ("^TNX", "US 10Y"),
            ("^VIX", "VIX"),
            ("DX-Y.NYB", "DXY"),
            ("CL=F", "WTI Oil"),
            ("GC=F", "Gold"),
            ("BTC-USD", "Bitcoin"),
            ("EURUSD=X", "EUR/USD"),
        ]:
            try:
                p = _get_quote(sym)
                if p and isinstance(p, dict):
                    out[label] = {
                        "price": p.get("price"),
                        "change_pct": p.get("change_pct"),
                    }
            except Exception:
                pass
    except Exception as e:
        _log(f"macro snapshot failed: {e}")
    return out


def _fetch_portfolio_snapshot() -> Dict[str, Any]:
    """Top 5 holdings + cash dal portfolio live."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        db = MemoryDB()
        snap = db.get_portfolio_summary()
        positions = snap.get("positions", [])
        # audit/11 §2: get_portfolio_summary parla ITALIANO (valore_mercato/peso_pct/
        # pl_pct/totale_valore_mercato_eur/cash_disponibile_eur): le chiavi inglesi
        # lette prima NON esistevano -> briefing con NAV 0, pesi 0.0%, P/L +0.00%.
        positions = sorted(positions, key=lambda p: -(p.get("valore_mercato") or 0))[:5]
        return {
            "top_holdings": [
                {
                    "ticker": p.get("ticker"),
                    "weight_pct": p.get("peso_pct"),
                    "pnl_pct": p.get("pl_pct"),
                }
                for p in positions
            ],
            "total_eur": snap.get("totale_valore_mercato_eur"),
            "cash_eur": snap.get("cash_disponibile_eur", 0),
        }
    except Exception as e:
        _log(f"portfolio snapshot failed: {e}")
        return {}


def _build_briefing_prompt(period: str, news: List[Dict[str, Any]],
                            macro: Dict[str, Any], portfolio: Dict[str, Any]) -> str:
    """Costruisce il prompt per Haiku."""
    slot = PERIOD_SLOTS.get(period, PERIOD_SLOTS["morning"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M CET")

    # Compatta news (top 20 per relevance)
    top_news = news[:20]
    news_block = "\n".join([
        f"  - [{n.get('sentiment','neutral')[:4]}|rel{n.get('relevance',5)}] "
        f"{n.get('title','')[:140]} ({n.get('provider','')[:25]})"
        for n in top_news
    ]) or "  (no recent news)"

    # Macro block
    def _safe_num(x, default=0.0):
        try:
            return float(x) if x is not None else float(default)
        except (TypeError, ValueError):
            return float(default)

    macro_block = "\n".join([
        f"  - {k}: {v.get('price', 'n/a')} ({_safe_num(v.get('change_pct')):+.2f}%)"
        if isinstance(v.get('change_pct'), (int, float))
        else f"  - {k}: {v.get('price', 'n/a')}"
        for k, v in macro.items()
    ]) or "  (no macro data)"

    # Portfolio block (None-safe formatting)
    holdings = portfolio.get("top_holdings", [])
    port_block = "\n".join([
        f"  - {h.get('ticker','?')}: {_safe_num(h.get('weight_pct')):.1f}% weight, "
        f"P/L {_safe_num(h.get('pnl_pct')):+.2f}%"
        for h in holdings
    ]) or "  (no portfolio data)"

    cash_eur = _safe_num(portfolio.get("cash_eur"))
    total_eur = _safe_num(portfolio.get("total_eur"))

    # 199e: ground truth (cariche, calendario eventi, numeri FRED live) nel briefing
    try:
        from bellomberg.core.current_facts import current_facts_block
        facts = current_facts_block()
    except Exception:
        facts = "(fatti correnti non disponibili)"

    return f"""Sei un Senior Research Analyst di Goldman Sachs che scrive il {slot['label']} per {PM_DESC}.
Data corrente: {now}. Focus periodo: {slot['focus']}.

{facts}

NEWS ULTIME {slot['lookback_h']}H (top {len(top_news)} per relevance):
{news_block}

MARKET LEVELS:
{macro_block}

PORTFOLIO TOP HOLDINGS (NAV totale EUR {total_eur:,.0f}, cash disponibile EUR {cash_eur:,.0f}):
{port_block}

ISTRUZIONI:
Scrivi un briefing in italiano professionale stile FT Briefing / Bloomberg Daybook, MAX 4 paragrafi.
Ogni paragrafo 2-4 frasi complete, italiano scorrevole (non telegrafico). Termini tecnici inglesi OK.

REGOLE FERREE (anti-invenzione, la violazione invalida il briefing):
- OGNI numero che scrivi (livelli, variazioni, probabilita') DEVE comparire in MARKET LEVELS, nelle NEWS qui sopra o nei FATTI CORRENTI. Se un dato non c'e', NON citarlo: ometti o scrivi che non e' disponibile.
- Cariche, riunioni e calendario SOLO dal blocco FATTI CORRENTI: mai dalla memoria di training.
- Se le news del periodo non coprono un tema, dillo in una frase: NON riempire con ricostruzioni plausibili.
- Niente raccomandazioni operative: il briefing informa, le decisioni le fa il consigliere settimanale.

Struttura obbligatoria (usa esattamente questi 4 header markdown ##):

## MARKET TONE
[Tono di mercato: risk-on/off, cosa ha mosso negli ultimi {slot['lookback_h']}h, livelli chiave VIX/yields/DXY]

## MACRO HIGHLIGHTS
[Top 2-3 news macro/geopolitiche del periodo con impatto cross-asset. PRIORITA' OBBLIGATORIA alla geopolitica (Iran/Israele, Ucraina/Russia, dazi, elezioni, OPEC) e alle banche centrali (Fed/BCE) rispetto alle news dei singoli titoli; se nelle news sopra non c'e' nulla di geo/macro rilevante, dillo esplicitamente in una frase]

## PORTFOLIO IMPACT
[Impatto specifico sulle posizioni del PM - cita ticker reali del portfolio]

## WATCH TODAY
[1-2 catalyst attesi nelle prossime ore o nel resto della giornata]

NON aggiungere altri header. NON usare bullet point. Solo prosa fluida sotto ogni header."""


def generate_briefing(period: Optional[str] = None) -> Dict[str, Any]:
    """Genera un nuovo briefing per lo slot indicato (o quello corrente)."""
    if period is None:
        period = _current_period()
    if period not in PERIOD_SLOTS:
        return {"error": f"unknown period: {period}"}

    slot = PERIOD_SLOTS[period]
    _log(f"generating briefing for slot={period} ({slot['label']})")

    # Gather data
    news = _fetch_recent_news(lookback_h=slot["lookback_h"], limit=50)
    macro = _fetch_macro_snapshot()
    portfolio = _fetch_portfolio_snapshot()

    _log(f"  news={len(news)} macro_indicators={len(macro)} top_holdings={len(portfolio.get('top_holdings', []))}")

    # Call Haiku
    try:
        # 05/09 (ordine PM): OpenRouter, modello dal .env (BRIEFING_MODEL); chiave o
        # variabile assente = errore DICHIARATO col nome (era "ANTHROPIC_API_KEY missing").
        from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, ConfigurazioneLLMMancante
        try:
            _modello_briefing = _modello_llm("briefing")
            client = OpenRouterClient()
        except ConfigurazioneLLMMancante as e:
            return {"error": str(e)}
        prompt = _build_briefing_prompt(period, news, macro, portfolio)

        # Retry on overloaded
        import random
        MAX_RETRIES = 3
        BASE_DELAY = 2.0
        msg = None
        last_err = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                msg = client.messages.create(
                    model=_modello_briefing,
                    max_tokens=800,
                    # 05/09 (sonda): senza questo GLM-5.3-flash spende ~520 token a ragionare
                    # su un tetto di 800 e il briefing esce troncato; «spento» su un modello
                    # che lo rifiuta diventa effort minimal (0 token), dichiarato da llm_client.
                    thinking={"type": "disabled"},
                    messages=[{"role": "user", "content": prompt}],
                )
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                status = getattr(e, "status_code", None)
                if status is None and ("529" in err_str or "overloaded" in err_str.lower()):
                    status = 529
                retriable = status in (429, 529) or (status is not None and 500 <= status < 600)
                if not retriable or attempt == MAX_RETRIES:
                    raise
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                _log(f"  Haiku {status} retry {attempt+1}/{MAX_RETRIES} in {delay:.1f}s")
                time.sleep(delay)

        if msg is None:
            raise last_err or RuntimeError("briefing generation failed")

        briefing_text = msg.content[0].text.strip()
        tokens_in = msg.usage.input_tokens
        tokens_out = msg.usage.output_tokens

    except Exception as e:
        _log(f"Haiku call failed: {e}")
        traceback.print_exc()
        return {
            "error": str(e),
            "period": period,
            "generated_at": datetime.now().isoformat(),
        }

    result = {
        "period": period,
        "slot_label": slot["label"],
        "generated_at": datetime.now().isoformat(),
        "lookback_hours": slot["lookback_h"],
        "news_count": len(news),
        "macro_indicators": macro,
        "portfolio_top": portfolio.get("top_holdings", []),
        "briefing_md": briefing_text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }

    # Save to cache
    cache = _load_cache()
    cache[period] = result
    cache["_latest"] = period
    cache["_latest_at"] = result["generated_at"]
    _save_cache(cache)

    _log(f"  briefing OK, {len(briefing_text)} chars, tokens in={tokens_in} out={tokens_out}")
    return result


def get_current_briefing() -> Dict[str, Any]:
    """Ritorna l'ultimo briefing salvato (qualsiasi slot, il piu recente)."""
    cache = _load_cache()
    latest_slot = cache.get("_latest")
    if not latest_slot or latest_slot not in cache:
        return {
            "briefing_md": "## NO BRIEFING YET\n\nIl briefing non e' ancora stato generato. Clicca REFRESH per crearne uno ora.",
            "generated_at": None,
            "period": None,
            "stale": True,
        }
    b = cache[latest_slot]
    # Check staleness
    try:
        gen_at = datetime.fromisoformat(b.get("generated_at", ""))
        age_sec = (datetime.now() - gen_at).total_seconds()
        b["stale"] = age_sec > CACHE_TTL_SEC
        b["age_minutes"] = int(age_sec / 60)
    except Exception:
        b["stale"] = True
    return b


def needs_refresh() -> bool:
    """True se il briefing corrente e' piu vecchio di TTL."""
    b = get_current_briefing()
    return b.get("stale", True)


if __name__ == "__main__":
    import sys
    period = sys.argv[1] if len(sys.argv) > 1 else None
    result = generate_briefing(period)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
