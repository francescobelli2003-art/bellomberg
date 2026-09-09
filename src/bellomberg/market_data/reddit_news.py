"""
BELLOMBERG - Reddit Sentiment & Catalyst Scraper

Estrae top post da subreddit finance senza richiedere OAuth.
Reddit espone JSON publicamente su /r/<sub>/{top,hot,new}.json (con UA custom).

Subreddit monitorati:
  - r/wallstreetbets : retail momentum / meme stocks
  - r/stocks         : analisi mainstream
  - r/options        : flow su opzioni
  - r/SecurityAnalysis : value/long-term
  - r/investing      : generalista

OUTPUT: lista di dict normalizzati (compatibile con news_aggregator schema):
  {title, snippet, url, provider, sentiment, relevance, ticker, published_at, score}

USO:
    from reddit_news import fetch_reddit_top, fetch_reddit_for_ticker
    posts = fetch_reddit_top(limit_per_sub=10)
    msft = fetch_reddit_for_ticker("MSFT", days=2)
"""
import time
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

try:
    import requests
    REQ_OK = True
except Exception:
    REQ_OK = False

# Custom UA obbligatorio per Reddit JSON API
USER_AGENT = "Bellomberg/0.5 (Personal AI Hedge Fund Terminal - by /u/anonymous)"

SUBREDDITS = {
    "wallstreetbets":   {"limit": 15, "min_score": 100,  "weight": 1.0},
    "stocks":           {"limit": 15, "min_score": 50,   "weight": 1.2},
    "options":          {"limit": 10, "min_score": 30,   "weight": 1.1},
    "SecurityAnalysis": {"limit": 8,  "min_score": 10,   "weight": 1.5},
    "investing":        {"limit": 10, "min_score": 30,   "weight": 1.0},
}

# Ticker regex: solo $TICKER o TICKER allcaps 2-5 lettere
TICKER_RE = re.compile(r"(?:\$([A-Z]{2,5})|\b([A-Z]{2,5})\b)")

# Blacklist parole comuni che matcherebbero il regex ma non sono ticker
COMMON_WORDS_BLACKLIST = {
    "IPO", "CEO", "CFO", "ETF", "USD", "EUR", "GBP", "FED", "ECB", "FOMC",
    "EPS", "PE", "PEG", "ROE", "ROI", "FCF", "EBITDA", "EBIT", "WACC",
    "OK", "YES", "NO", "I", "A", "AN", "THE", "IT", "IS", "AS", "AT",
    "TO", "OF", "BY", "ON", "IN", "OR", "BE", "ME", "MY", "WE", "US",
    "ALL", "AND", "BUT", "FOR", "NOT", "YOU", "CAN", "HAS", "HAD", "WHO",
    "ANY", "NEW", "OUT", "WAY", "TWO", "USE", "ONE", "OFF", "SEE", "GOT",
    "DD", "TLDR", "LOL", "IMO", "AFAIK", "BTW", "TBH", "FOMO", "DCA", "WSB",
    "GAIN", "LOSS", "HOLD", "BUY", "SELL", "PUMP", "DUMP", "MOON", "DROP",
    "BULL", "BEAR", "CALL", "PUT", "PUTS", "CALLS", "STOCK", "MARKET",
    "OPEN", "CLOSE", "HIGH", "LOW", "RED", "GREEN", "BLACK", "GOLD",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    "GOD", "WTF", "OMG", "IRL", "AMA", "NSFW", "MOD", "BAN", "EDIT",
    "FREE", "NOW", "WIN", "WOW", "FAR", "BEST", "GOOD", "BAD", "LOOK",
    "AI", "AR", "VR", "ML", "CPU", "GPU", "RAM", "SSD", "API",
}


def _log(msg: str):
    print(f"[REDDIT] {msg}", flush=True)


def _extract_tickers(text: str, max_tickers: int = 3) -> List[str]:
    """Estrae ticker mentionati nel post (preferendo $TICKER)."""
    found = []
    for m in TICKER_RE.finditer(text):
        dollar_t, plain_t = m.groups()
        t = dollar_t or plain_t
        if not t or t in COMMON_WORDS_BLACKLIST:
            continue
        if t not in found:
            found.append(t)
        if len(found) >= max_tickers:
            break
    return found


def _fetch_subreddit(subreddit: str, listing: str = "top", t: str = "day",
                      limit: int = 15) -> List[Dict[str, Any]]:
    """Fetch top/hot da un singolo subreddit via JSON."""
    if not REQ_OK:
        return []
    url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
    params = {"limit": limit}
    if listing == "top":
        params["t"] = t  # hour|day|week|month|year|all
    try:
        r = requests.get(url, params=params, timeout=10,
                          headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            _log(f"  {subreddit} HTTP {r.status_code}")
            return []
        data = r.json()
        posts = data.get("data", {}).get("children", [])
        out = []
        for p in posts:
            d = p.get("data", {})
            if d.get("stickied") or d.get("over_18"):
                continue
            title = d.get("title", "")
            selftext = (d.get("selftext", "") or "")[:400]
            score = d.get("score", 0)
            num_comments = d.get("num_comments", 0)
            created_utc = d.get("created_utc", 0)
            permalink = d.get("permalink", "")
            url = f"https://www.reddit.com{permalink}" if permalink else d.get("url", "")
            flair = d.get("link_flair_text", "") or ""

            tickers = _extract_tickers(title + " " + selftext)

            # Naive sentiment: flair + score-based
            flair_lower = flair.lower()
            sentiment = "neutral"
            if any(w in flair_lower for w in ["dd", "discussion"]):
                sentiment = "neutral"
            elif any(w in flair_lower for w in ["yolo", "gain", "moon"]):
                sentiment = "bullish"
            elif any(w in flair_lower for w in ["loss", "puts", "drill"]):
                sentiment = "bearish"

            out.append({
                "title": title[:200],
                "snippet": selftext[:300],
                "url": url,
                "provider": f"Reddit r/{subreddit}",
                "sentiment": sentiment,
                "relevance": min(10, max(1, int(score / 100))),  # score 100->1, 1000->10
                "ticker": tickers[0] if tickers else "",
                "tickers_mentioned": tickers,
                "score": score,
                "num_comments": num_comments,
                "flair": flair,
                "published_at": datetime.fromtimestamp(created_utc).isoformat() if created_utc else "",
            })
        return out
    except Exception as e:
        _log(f"  {subreddit} error: {e}")
        return []


def fetch_reddit_top(listing: str = "top", t: str = "day",
                      filter_min_score: bool = True) -> List[Dict[str, Any]]:
    """Fetch top posts da TUTTI i subreddit configurati."""
    if not REQ_OK:
        return []
    all_posts = []
    for sub, cfg in SUBREDDITS.items():
        posts = _fetch_subreddit(sub, listing=listing, t=t, limit=cfg["limit"])
        if filter_min_score:
            posts = [p for p in posts if p.get("score", 0) >= cfg["min_score"]]
        # Apply per-sub weight to relevance
        weight = cfg.get("weight", 1.0)
        for p in posts:
            p["relevance"] = min(10, int(p["relevance"] * weight))
        all_posts.extend(posts)
        time.sleep(0.6)  # be nice to Reddit
    # Sort by score desc
    all_posts.sort(key=lambda p: -p.get("score", 0))
    _log(f"fetched {len(all_posts)} posts from {len(SUBREDDITS)} subs")
    return all_posts


def fetch_reddit_for_ticker(ticker: str, days: int = 2,
                             include_subs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Cerca post che menzionano un ticker specifico negli ultimi N giorni."""
    if not REQ_OK:
        return []
    subs = include_subs or list(SUBREDDITS.keys())
    cutoff = datetime.now() - timedelta(days=days)
    matches = []
    ticker_upper = ticker.upper().split(".")[0]  # rimuovi suffisso .MI etc.
    for sub in subs:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {
            "q": ticker_upper,
            "restrict_sr": "on",
            "sort": "top",
            "t": "week" if days <= 7 else "month",
            "limit": 10,
        }
        try:
            r = requests.get(url, params=params, timeout=10,
                              headers={"User-Agent": USER_AGENT})
            if r.status_code != 200:
                continue
            data = r.json()
            posts = data.get("data", {}).get("children", [])
            for p in posts:
                d = p.get("data", {})
                if d.get("stickied") or d.get("over_18"):
                    continue
                created = datetime.fromtimestamp(d.get("created_utc", 0))
                if created < cutoff:
                    continue
                title = d.get("title", "")
                # Verifica menzione effettiva (no false match)
                text_full = (title + " " + (d.get("selftext", "") or "")).upper()
                if ticker_upper not in text_full:
                    continue
                matches.append({
                    "title": title[:200],
                    "snippet": (d.get("selftext", "") or "")[:300],
                    "url": f"https://www.reddit.com{d.get('permalink', '')}",
                    "provider": f"Reddit r/{sub}",
                    "sentiment": "neutral",  # caller can classify
                    "relevance": min(10, max(1, int(d.get("score", 0) / 50))),
                    "ticker": ticker,
                    "score": d.get("score", 0),
                    "num_comments": d.get("num_comments", 0),
                    "published_at": created.isoformat(),
                })
            time.sleep(0.4)
        except Exception as e:
            _log(f"  search {sub}/{ticker} error: {e}")
            continue
    matches.sort(key=lambda p: -p.get("score", 0))
    return matches[:15]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # Cerca per ticker
        t = sys.argv[1].upper()
        posts = fetch_reddit_for_ticker(t, days=3)
        print(f"=== {len(posts)} posts mentioning {t} ===")
    else:
        posts = fetch_reddit_top(listing="top", t="day")
        print(f"=== Top {len(posts)} posts del giorno ===")
    for p in posts[:25]:
        tk = ",".join(p.get("tickers_mentioned", [])[:3]) or p.get("ticker", "")
        print(f"  [score={p.get('score',0):5d}] {p.get('provider','')[:25]:25s} | "
              f"tickers={tk:15s} | {p.get('title','')[:90]}")
