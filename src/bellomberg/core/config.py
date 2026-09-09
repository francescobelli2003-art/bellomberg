"""
Configurazione globale del sistema.
Le chiavi API vengono lette dal file .env (template: .env.example, con ogni variabile spiegata).
"""
from bellomberg.core.paths import PROJECT_ROOT
import os
from dotenv import load_dotenv

# Carica le variabili dal file .env nella stessa cartella
load_dotenv(PROJECT_ROOT / ".env")

# === CHIAVI API ===
# 05/09 (ordine PM): il prodotto chiama OpenRouter (llm_client.py). ANTHROPIC_API_KEY
# resta SOLO per gli script laterali non migrati (prova_vincoli_pm.py: count_tokens;
# mockup_f3_chat/titles_haiku.py): dichiarato in .env.example.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

# === MODELLI (STORICI) ===
# 05/09: il prodotto NON legge piu' queste costanti — i modelli vivono nel .env, uno per
# funzione, risolti da llm_client.modello() (spec 2026-09-05-openrouter-migrazione-design).
# Restano per attic/ e per gli script laterali che le importano ancora.
MODEL_CLASSIFIER = "claude-haiku-4-5-20251001"
# PM 26/07: sonnet-4-6 -> sonnet-5 (red team, synthesizer, extract, reflection,
# R0 via specialists.base; intro $2/$10 fino al 31/08, poi $3/$15 - v. llm_pricing)
MODEL_SYNTHESIZER = "claude-sonnet-5"

# Consigliere agentico settimanale (lunedi mattina + on-demand)
# NB censimento 26/07: costante usata SOLO da attic/consigliere_v1 (morta nel
# codice vivo — il comitato usa specialists.base.DEFAULT_MODEL e capo.CAPO_MODEL)
MODEL_CONSIGLIERE = "claude-opus-5"
MAX_AGENT_ITERATIONS = 15   # cap su iterazioni di tool use (controllo costo)
MAX_AGENT_TOTAL_TOKENS = 500000  # cap totale tokens cumulati nella sessione

# === PARAMETRI ANALISI ===
RILEVANZA_MINIMA = "media"
MAX_NEWS_PER_TICKER = 10
GIORNI_LOOKBACK = 3  # 3 giorni per coprire delay 24h di NewsAPI free

# === MACRO & GEOPOLITICA ===
MACRO_NEWS_DOMAINS = "bloomberg.com,reuters.com,ft.com,wsj.com,cnbc.com,economist.com,apnews.com"
MAX_NEWS_PER_TEMA_MACRO = 5

# === MULTI-FONTE NEWS (alternative a NewsAPI, tutte free tier 100/day) ===
# Marketaux: copertura finanziaria specifica, no delay
# https://www.marketaux.com/account/dashboard
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")

# TheNewsAPI: copertura generale, real-time
# https://www.thenewsapi.com/account/dashboard
THENEWSAPI_API_KEY = os.getenv("THENEWSAPI_API_KEY")

# GNews: basato su Google News, real-time
# https://gnews.io/dashboard
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

# Tavily Search API per il consigliere (web search vero per agent)
# https://tavily.com/ - free tier 1000 req/mese
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
# FRED API (Federal Reserve Economic Data, St. Louis Fed) per dati macro USA
# https://fred.stlouisfed.org/docs/api/api_key.html - GRATIS, no limiti reali
FRED_API_KEY = os.getenv("FRED_API_KEY")

# Finnhub: news stock + earnings calendar + insider Form 4 real-time + IPO calendar
# https://finnhub.io/dashboard - free tier 60 req/min
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")


# Yahoo Finance news: integrata in yfinance, no API key necessaria
USE_YFINANCE_NEWS = True

# === EMAIL DELIVERY (Gmail SMTP) ===
# IMPORTANTE: EMAIL_PASSWORD deve essere una Gmail APP PASSWORD (non la password normale).
# Crearne una qui: https://myaccount.google.com/apppasswords
# Vedi SETUP_SCHEDULAZIONE.md per istruzioni dettagliate.
EMAIL_FROM = os.getenv("EMAIL_FROM")        # es: nome@example.com (Gmail: serve una APP PASSWORD, v. sotto)
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") # es: "abcd efgh ijkl mnop" (16 char app password)
EMAIL_TO = os.getenv("EMAIL_TO")            # destinatario, puo' essere uguale a EMAIL_FROM

# === IDENTITA' (pubblicazione 02/09, B3) ===
# Il nome con cui il Capo e i prompt si rivolgono al PM, e il footer dei PDF.
# Default "PM": e' un'etichetta di cortesia, non un dato - dichiarato in .env.example.
PM_NAME = (os.getenv("PM_NAME") or "PM").strip() or "PM"
# Come i prompt si riferiscono al PM: «Mario Rossi (il PM)» col nome, «il PM» col
# default — altrimenti verrebbe «di PM (il PM)» / «per il PM PM» (review 02/09).
PM_DESC = f"{PM_NAME} (il PM)" if PM_NAME != "PM" else "il PM"


def verifica_config():
    """Controlla che le chiavi API obbligatorie siano presenti."""
    mancanti = []
    # 05/09: la chiave OpenRouter e le 11 variabili modello (llm_client.VARIABILI_BASE)
    try:
        from bellomberg.core.llm_client import variabili_mancanti as _vm
        mancanti.extend(_vm())
    except Exception as e:
        mancanti.append("llm_client non importabile: " + str(e))
    if not NEWS_API_KEY or NEWS_API_KEY.startswith("xxxx"):
        mancanti.append("NEWS_API_KEY")
    if mancanti:
        raise RuntimeError(
            f"Chiavi API mancanti nel file .env: {', '.join(mancanti)}. "
            "Copia .env.example in .env e inserisci le tue chiavi."
        )
