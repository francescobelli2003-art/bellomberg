"""Fix 2+3 di §9-sextrigies (voce 7 §9-quattuortrigies, ok PM 03/08, Fable 5).

Misura del 03/08 (changelog (63)): 248 news in DB dicevano "non in portafoglio"
su ticker CHE CI SONO — causa a una riga, `context_tickers[:20]` su un book di
27: Haiku vedeva una lista MONCA delle 7 posizioni piu' piccole e, coerente con
cio' che vede, negava chi manca (fra le vittime una fusione annunciata su una
posizione italiana liquidata come irrilevante, e una semestrale su un titolo
europeo del book).

Cura approvata: (a) via il [:20] — il book viaggia INTERO nel prompt (poche
decine di voci ~ 400 char, irrisori per Haiku); (b) regola nel prompt: VIETATO
dichiarare "non in portafoglio" — se il ticker non e' in lista si scrive
l'impatto macro.

Input riprodotti dal chiamante VERO (auto_pull_feed: `ctx_weighted` =
"TICKER (peso%)" per ogni posizione) — regola misure-circolari: mai
un'approssimazione degli input della funzione.

04/09 sera (Fable 5.1, criterio (1), chat a1): il book qui dentro era uno
SNAPSHOT VERO del portafoglio del PM, simboli e pesi, in un file che viaggia
nel repo pubblico. La forma che il test misura e' «un book PIU' LUNGO del
taglio [:20], le voci oltre la ventesima sopravvivono»: non dipende da QUALI
simboli siano ne' da quanti fossero davvero, quindi sono inventati e generati
(N=27 perche' > 20 con margine), con pesi inventati e decrescenti come nel
payload vero (ordinato per valore EUR dal 01/08).
"""
import json

from bellomberg.core import llm_client
import pytest

from bellomberg.core import config
from bellomberg.market_data import news_aggregator


# Il formato e' quello di auto_pull_feed (f"{ticker} ({w:.1f}%)"); N=27 > 20, cosi'
# le ULTIME 7 sono quelle che un [:20] mutilerebbe. Simboli e pesi INVENTATI
# (tests/ e' pubblico): FINTO01.MI ... FINTO27.MI.
BOOK_27 = [f"FINTO{i:02d}.MI ({(28 - i) * 0.6:.1f}%)" for i in range(1, 28)]

RISPOSTA_HAIKU = json.dumps({
    "sentiment": "neutral", "sentiment_score": 0.0, "relevance": 6,
    "headline_it": "Titolo mock", "why_matters": "Perche' mock",
})


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_TextBlock(text)]


@pytest.fixture
def prompt_catturato(monkeypatch):
    """Client Anthropic finto: cattura il prompt e risponde JSON valido."""
    catturati = []

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            catturati.append(kwargs["messages"][0]["content"])
            return _Resp(RISPOSTA_HAIKU)

    monkeypatch.setattr(llm_client, "OpenRouterClient", _FakeAnthropic)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-collaudo", raising=False)
    return catturati


ITEM = {"title": "Acme Manifatture pubblica la semestrale 2026",
        "snippet": "Capex guidance invariata a 1,2-1,4 mld",   # cifra inventata
        "ticker_mentioned": "FINTO14.MI", "theme": ""}


def test_il_book_finto_ha_la_forma_del_caso_dei_248():
    """Se domani qualcuno accorcia il generatore, i test sotto passerebbero a vuoto:
    piu' di 20 voci, tutte diverse, tutte nel formato «TICKER (peso%)» del chiamante vero."""
    assert len(BOOK_27) > 20 and len(set(BOOK_27)) == len(BOOK_27)
    assert all(v.endswith("%)") and " (" in v for v in BOOK_27)


def test_prompt_porta_il_book_intero(prompt_catturato):
    """Il caso dei 248: le 7 posizioni piu' piccole sparivano dal prompt."""
    r = news_aggregator._classify_with_haiku(ITEM, list(BOOK_27))
    assert r.get("relevance") == 6, "harness rotto: la risposta finta non e' arrivata"
    prompt = prompt_catturato[0]
    mancanti = [v for v in BOOK_27 if v not in prompt]
    assert mancanti == [], f"posizioni MUTILATE dal prompt: {mancanti}"


def test_prompt_vieta_la_negazione_di_portafoglio(prompt_catturato):
    """La seconda meta' della cura: anche a lista completa, il modello non
    deve poter negare il book — se il ticker non e' in lista: impatto macro."""
    news_aggregator._classify_with_haiku(ITEM, list(BOOK_27))
    prompt = prompt_catturato[0].lower()
    assert "vietato" in prompt and "non in portafoglio" in prompt, \
        "manca la regola che vieta la negazione di portafoglio"
    assert "impatto macro" in prompt, \
        "manca l'istruzione alternativa (impatto macro) per i ticker fuori lista"
