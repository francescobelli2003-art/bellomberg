"""
BELLOMBERG - News Topics Registry

Definisce tutti i topic macro/geo/politica/EM/crypto per il news terminal.
Ogni topic ha:
  - id: identifier (slug)
  - label: nome leggibile
  - category: bucket macro (rates|inflation|geopolitics|politics|em|commodities|crypto|corporate)
  - query: stringa per NewsAPI/Marketaux/TheNewsAPI/GNews search
  - rss_filter: lista regex match per filtrare RSS feed (opzionale)
  - importance: 1-5 (5=critico, default 3)

QUALI TITOLI MUOVE UN TEMA non sta qui. 06/09 (lotto (b) del criterio (1), MASTER
§9-unnonagies): la chiave `tickers_affected` era una lista del portafoglio di chi ha
scritto il file, e non stava ferma — `news_aggregator` la copia su OGNI notizia del tema
(tre punti) e `chat_tools` la mette nel payload del tool dei desk, cioe' davanti al
modello. Su un'altra installazione arrivavano i legami tema->titolo del PM, e con essi
nessi economici che per quel portafoglio non esistono. Ora la mappa vive nel negozio
PRIVATO `data/news_topics_tickers.json` (forma tracciata in
`news_topics_tickers.example.json`), si rilegge a ogni chiamata e a negozio assente
NESSUN titolo viene attaccato a nessuna notizia — dichiarandolo, mai in silenzio:
v. `negozi_privati.carica_temi_titoli`, `titoli_per_tema` qui sotto e
`news_aggregator.TEMI_TITOLI_MUTI`.

USO:
    from news_topics import TOPICS, get_topic_query, get_category_topics

    for t in get_category_topics("rates"):
        print(t["label"], t["query"])
"""
from typing import Dict, List, Any
from bellomberg.core.presentation import message, render_payload


# ============================================================
# CATEGORY DEFINITIONS
# ============================================================
CATEGORIES = {
    "rates":        message("Banche Centrali & Tassi", "Central Banks & Rates"),
    "inflation":    message("Inflazione & Dati Macro", "Inflation & Macro Data"),
    "geopolitics":  message("Geopolitica & Conflitti", "Geopolitics & Conflicts"),
    "politics":     message("Politica IT/UE/USA", "IT/EU/US Politics"),
    "em":           message("Mercati Emergenti", "Emerging Markets"),
    "commodities":  message("Energia & Materie Prime", "Energy & Commodities"),
    "crypto":       message("Cripto: Macro & Regolamentazione", "Crypto Macro & Regulation"),
    "corporate":    message("Eventi Societari di Forte Impatto", "High-Impact Corporate Events"),
}


# ============================================================
# TOPICS - the master list
# ============================================================
TOPICS: List[Dict[str, Any]] = [
    # ---------- RATES & CENTRAL BANKS ----------
    {
        "id": "fed", "label": message("Federal Reserve", "Federal Reserve"), "category": "rates",
        "query": "Federal Reserve Powell OR FOMC OR \"Fed rate\"",
        "importance": 5,
    },
    {
        "id": "ecb", "label": message("BCE Lagarde", "ECB Lagarde"), "category": "rates",
        "query": "ECB OR \"European Central Bank\" OR Lagarde OR \"euro area rates\"",
        "importance": 5,
    },
    {
        "id": "boe", "label": message("Banca d'Inghilterra", "Bank of England"), "category": "rates",
        "query": "\"Bank of England\" OR \"BoE rate\" OR Bailey BOE",
        "importance": 3,
    },
    {
        "id": "boj", "label": message("Banca del Giappone", "Bank of Japan"), "category": "rates",
        "query": "\"Bank of Japan\" OR BoJ OR Ueda OR \"yen carry\"",
        "importance": 4,
    },
    {
        "id": "pboc", "label": message("PBoC & Tassi Cina", "PBoC & China Rates"), "category": "rates",
        "query": "PBoC OR \"China central bank\" OR \"LPR rate\" OR yuan",
        "importance": 3,
    },

    # ---------- INFLATION & MACRO DATA ----------
    {
        "id": "cpi_us", "label": message("CPI / PCE USA", "US CPI / PCE"), "category": "inflation",
        "query": "\"US CPI\" OR \"core PCE\" OR \"US inflation\"",
        "importance": 5,
    },
    {
        "id": "nfp", "label": message("Occupazione USA / NFP", "US Jobs / NFP"), "category": "inflation",
        "query": "\"nonfarm payrolls\" OR \"US unemployment\" OR NFP report",
        "importance": 4,
    },
    {
        "id": "gdp_us", "label": message("PIL USA & ISM", "US GDP & ISM"), "category": "inflation",
        "query": "\"US GDP\" OR \"ISM manufacturing\" OR \"ISM services\"",
        "importance": 3,
    },
    {
        "id": "cpi_eu", "label": message("CPI Eurozona", "Eurozone CPI"), "category": "inflation",
        "query": "\"eurozone inflation\" OR \"HICP\" OR \"euro area CPI\"",
        "importance": 4,
    },
    {
        "id": "pmi_global", "label": message("PMI Globali", "Global PMIs"), "category": "inflation",
        "query": "\"PMI manufacturing\" OR \"PMI services\" OR \"global PMI\"",
        "importance": 2,
    },

    # ---------- GEOPOLITICS ----------
    {
        "id": "ukraine", "label": message("Russia / Ucraina", "Russia / Ukraine"), "category": "geopolitics",
        "query": "Ukraine OR Russia war OR Putin OR Zelensky OR sanctions Russia",
        "importance": 4,
    },
    {
        "id": "middle_east", "label": message("Israele / Iran / Medio Oriente", "Israel / Iran / Middle East"), "category": "geopolitics",
        "query": "Israel Iran OR Hamas OR Gaza OR Hezbollah OR Houthi",
        "importance": 5,
    },
    {
        "id": "taiwan_china", "label": message("Taiwan & Cina USA", "Taiwan & US-China"), "category": "geopolitics",
        "query": "Taiwan China OR \"chip war\" OR \"US China tariffs\"",
        "importance": 5,
    },
    {
        "id": "korea", "label": message("Corea del Nord", "North Korea"), "category": "geopolitics",
        "query": "\"North Korea\" OR Pyongyang OR Kim Jong Un",
        "importance": 2,
    },

    # ---------- POLITICS ----------
    {
        "id": "italy_politics", "label": message("Politica Italia / Differenziale BTP", "Italian Politics / BTP Spread"), "category": "politics",
        "query": "Meloni OR \"Italy budget\" OR \"BTP spread\" OR \"Italy deficit\"",
        "importance": 4,
    },
    {
        "id": "eu_politics", "label": message("UE / Macron / Merz", "EU / Macron / Merz"), "category": "politics",
        "query": "\"European Union\" OR Macron OR Merz Germany OR \"EU Commission\"",
        "importance": 3,
    },
    {
        "id": "us_politics", "label": message("Politica USA / Trump / Congresso", "US Politics / Trump / Congress"), "category": "politics",
        "query": "Trump OR Congress OR \"US Senate\" OR \"White House\"",
        "importance": 4,
    },
    {
        "id": "trade_wars", "label": message("Dazi & Commercio", "Tariffs & Trade"), "category": "politics",
        "query": "tariffs OR \"trade war\" OR \"export controls\" OR \"chip export\"",
        "importance": 4,
    },

    # ---------- EMERGING MARKETS ----------
    {
        "id": "china_econ", "label": message("Cina: Economia & Immobiliare", "China: Economy & Real Estate"), "category": "em",
        "query": "China economy OR \"China real estate\" OR Evergrande OR Country Garden",
        "importance": 4,
    },
    {
        "id": "india", "label": message("India Modi & Macro", "India Modi & Macro"), "category": "em",
        "query": "India Modi OR \"India economy\" OR RBI India",
        "importance": 2,
    },
    {
        "id": "em_currencies", "label": message("Valute Emergenti (TRY, BRL, ARS)", "EM Currencies (TRY, BRL, ARS)"), "category": "em",
        "query": "\"emerging markets\" OR Turkey lira OR Brazil real OR Argentina peso",
        "importance": 2,
    },

    # ---------- COMMODITIES ----------
    {
        "id": "oil", "label": message("Petrolio & OPEC+", "Oil & OPEC+"), "category": "commodities",
        "query": "OPEC OR \"oil price\" OR Brent crude OR WTI",
        "importance": 4,
    },
    {
        "id": "gas", "label": message("Gas Naturale UE", "EU Natural Gas"), "category": "commodities",
        "query": "\"natural gas\" OR TTF gas OR \"EU energy\"",
        "importance": 3,
    },
    {
        "id": "gold", "label": message("Oro & Metalli Preziosi", "Gold & Precious Metals"), "category": "commodities",
        "query": "gold price OR \"central bank gold\" OR \"real rates gold\"",
        "importance": 3,
    },
    {
        "id": "uranium", "label": message("Uranio & Nucleare", "Uranium & Nuclear"), "category": "commodities",
        "query": "uranium OR Cameco OR \"nuclear power\"",
        "importance": 2,
    },

    # ---------- CRYPTO MACRO ----------
    {
        "id": "btc_macro", "label": message("Bitcoin: Macro & Flussi ETF", "Bitcoin Macro & ETF Flows"), "category": "crypto",
        "query": "Bitcoin OR \"BTC ETF\" OR IBIT OR \"spot Bitcoin\"",
        "importance": 5,
    },
    {
        "id": "crypto_regulation", "label": message("Cripto: Regolamentazione SEC", "Crypto SEC Regulation"), "category": "crypto",
        "query": "SEC crypto OR \"crypto regulation\" OR \"Gary Gensler\" OR \"stablecoin bill\"",
        "importance": 4,
    },
    {
        # ID storico conservato per filtri e notizie gia' archiviate.
        "id": "mstr_saylor", "label": message("Tesorerie quotate Bitcoin", "Listed Bitcoin treasuries"), "category": "crypto",
        "query": "\"Bitcoin treasury\" OR \"corporate Bitcoin holdings\" OR \"BTC acquisition\"",
        "importance": 5,
    },
    {
        "id": "ethereum", "label": message("Ethereum & DeFi", "Ethereum & DeFi"), "category": "crypto",
        "query": "Ethereum OR ETH ETF OR DeFi OR L2 rollup",
        "importance": 3,
    },

    # ---------- CORPORATE EVENTS HIGH-IMPACT ----------
    {
        "id": "earnings_megacap", "label": message("Risultati Mega Capitalizzazioni (MAG7)", "Megacap Earnings (MAG7)"), "category": "corporate",
        "query": "Apple earnings OR Microsoft earnings OR Nvidia earnings OR Tesla earnings",
        "importance": 4,
    },
    {
        "id": "ma_deals", "label": message("Grandi Operazioni M&A", "Major M&A Deals"), "category": "corporate",
        "query": "merger OR acquisition OR \"takeover bid\" OR \"private equity buyout\"",
        "importance": 3,
    },
    {
        "id": "downgrades", "label": message("Principali Ribassi / Rialzi del Rating", "Major Downgrades / Upgrades"), "category": "corporate",
        "query": "\"credit downgrade\" OR \"sovereign rating\" OR Moody's OR \"S&P downgrade\"",
        "importance": 3,
    },
]


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def get_topic_query(topic_id: str) -> str:
    """Ritorna la query string per un dato topic id."""
    for t in TOPICS:
        if t["id"] == topic_id:
            return t["query"]
    return ""


def get_category_topics(category: str) -> List[Dict[str, Any]]:
    """Ritorna tutti i topic di una categoria."""
    return render_payload([t for t in TOPICS if t["category"] == category])


def get_all_topics_by_importance(min_importance: int = 1) -> List[Dict[str, Any]]:
    """Ritorna topic ordinati per importanza decrescente, filtrati min."""
    return render_payload(sorted(
        [t for t in TOPICS if t.get("importance", 3) >= min_importance],
        key=lambda t: -t.get("importance", 3),
    ))


def titoli_per_tema() -> Dict[str, Any]:
    """L'esito INTERO del negozio privato {id_tema: [SIMBOLO, ...]}, riletto a ogni chiamata.
    Si rende l'esito e non la sola mappa perche' chi legge deve poter distinguere «questo
    tema non muove niente» da «non so quali titoli muove»: sono due frasi diverse e con la
    sola mappa avrebbero la stessa forma (regola PM 14/07). Chi vuole solo la mappa usa
    `titoli_del_tema`."""
    from bellomberg.storage import negozi_privati
    return negozi_privati.carica_temi_titoli()


def titoli_del_tema(topic_id: str, mappa: Dict[str, Any] = None) -> List[str]:
    """I titoli che il tema muove, dal negozio privato. `mappa` si passa quando si e' gia'
    letto l'esito nel giro (una lettura per giro, non una per notizia)."""
    m = titoli_per_tema()["temi"] if mappa is None else mappa
    return list(m.get(topic_id, ()))


def temi_senza_riscontro(mappa: Dict[str, Any] = None) -> List[str]:
    """Gli id del negozio che non sono (piu') temi del registro: quel legame non si applica
    a niente. A negozio ASSENTE la lista e' vuota di proposito — li' il buco lo dichiara
    `providers_blocked`, e due dichiarazioni per lo stesso guasto nasconderebbero il caso
    vero, che e' il negozio scritto bene ma andato fuori sincrono col registro."""
    m = titoli_per_tema()["temi"] if mappa is None else mappa
    noti = {t["id"] for t in TOPICS}
    return sorted(k for k in m if k not in noti)


def get_topics_affecting_ticker(ticker: str) -> List[Dict[str, Any]]:
    """Ritorna i topic che muovono un dato titolo, secondo il negozio privato.
    A negozio assente o illeggibile torna vuoto: nessun legame e' noto, e nessuno viene
    inventato. Il buco e' dichiarato da `news_aggregator.providers_blocked()`."""
    mappa = titoli_per_tema()["temi"]
    return render_payload([t for t in TOPICS if ticker in mappa.get(t["id"], ())])


# RSS extra dedicati a topic specifici (sempre attivi)
EXTRA_RSS_FEEDS = {
    # Crypto
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    # Macro / EU
    "ECB Press": "https://www.ecb.europa.eu/rss/press.html",
    "Eurostat": "https://ec.europa.eu/eurostat/themes-rss/rss_news.xml",
    # Italia
    "Sole24Ore Mercati": "https://www.ilsole24ore.com/rss/mercati.xml",
    "Repubblica Economia": "https://www.repubblica.it/rss/economia/rss2.0.xml",
    # Geopolitica
    "Reuters World": "https://feeds.reuters.com/Reuters/worldNews",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    # Markets
    "MarketWatch Top": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Investing.com": "https://www.investing.com/rss/news.rss",
    "Barron's": "https://feeds.dowjones.io/public/rss/RSSBarronsHome",
}


if __name__ == "__main__":
    # Questo blocco e' codice esportato come tutto il resto: fino al 06/09 stampava la
    # colonna `tickers:` leggendola dal SORGENTE, cioe' `python news_topics.py` rendeva
    # il portafoglio di chi aveva scritto il file, raggruppato per tema e ordinato per
    # importanza. Ora i titoli vengono dal negozio privato, quindi su un clone la colonna
    # e' vuota e il perche' e' DICHIARATO: una colonna vuota senza motivo si legge
    # «nessun tema muove niente», che e' falso.
    _esito = titoli_per_tema()
    _mappa = _esito["temi"]
    print(f"Total topics: {len(TOPICS)}")
    print(f"Categories: {list(CATEGORIES.keys())}")
    if _esito["origine"] in ("assente", "illeggibile"):
        print(f"negozio dei titoli per tema {_esito['origine']}: {_esito['motivo']}")
        print("nessun titolo verra' attaccato alle notizie finche' il negozio non c'e'.")
    else:
        _fuori = temi_senza_riscontro(_mappa)
        if _fuori:
            print(f"ATTENZIONE: {len(_fuori)} voci del negozio non sono temi del registro "
                  f"e non si applicano a niente: {', '.join(_fuori)}")
    print()
    for cat, label in CATEGORIES.items():
        topics_in_cat = get_category_topics(cat)
        print(f"=== {label} ({len(topics_in_cat)} topics) ===")
        for t in topics_in_cat:
            tk = ",".join(titoli_del_tema(t["id"], _mappa))
            print(f"  [{t['importance']}] {t['label']:35s} | tickers: {tk}")
        print()
