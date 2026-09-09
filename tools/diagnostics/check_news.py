"""
Diagnostica #161 — cosa vede il briefing, senza quoting hell di PowerShell.
Uso:
  python check_news.py         -> solo lettura DB (nessuna chiamata AI)
  python check_news.py full    -> rigenera anche il briefing (1 chiamata Haiku)
"""
import sys

from bellomberg.cli.briefing_engine import _fetch_recent_news

ns = _fetch_recent_news(lookback_h=5, limit=50)
print(f"{len(ns)} news viste dal briefing (finestre: ticker 5h, macro 24h, geo 48h)\n")
for n in ns[:20]:
    print(f"- rel{n.get('relevance', '?')} | {(n.get('title') or '')[:85]}")

geo_keys = ("iran", "israel", "peace", "ceasefire", "ukraine", "russia", "sanction")
geo_hits = [n for n in ns if any(k in (n.get("title") or "").lower() for k in geo_keys)]
print(f"\nNews geo/guerra/pace nella lista: {len(geo_hits)}")
for n in geo_hits[:8]:
    print(f"  * {(n.get('title') or '')[:90]}")

if len(sys.argv) > 1 and sys.argv[1].lower() == "full":
    from bellomberg.cli.briefing_engine import generate_briefing
    r = generate_briefing("afternoon")
    print("\n=== BRIEFING RIGENERATO ===\n")
    print(r.get("briefing_md", "ERR: " + str(r.get("error"))))
