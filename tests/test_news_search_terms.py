"""Lotto 1 del criterio (1), 04/09 sera (Fable 5.1, chat a1): i TERMINI DI RICERCA
delle news escono da `news_aggregator.py` e vivono nel negozio privato
`data/news_search_terms.json`, sul modello di `fonti_guidance` (03/09).

Il difetto che chiude, gemello di quello che il PM ha trovato su fonti_guidance:
chi clona il repo cercava le news coi NOMI delle societa' del book del PM e, per i
SUOI titoli, ricadeva sul ticker nudo — che per i simboli europei le API non trovano.
E i quattro `in {"..."}` cablati che saltavano UN simbolo del book diventano una voce
`null` del negozio: esclusione DICHIARATA, non un letterale.

Regole misurate qui (regola PM 14/07, mai un ripiego muto):
  - negozio assente/illeggibile -> `origine` e `motivo` scritti, mappa VUOTA;
  - una sola voce malformata -> negozio illeggibile INTERO (mezzo negozio caricato
    e' un ripiego muto), col nome della voce nel motivo;
  - voce assente -> si cerca il ticker nudo E lo si dichiara nel log;
  - voce `null` -> nessun provider interrogato, esclusione dichiarata nel log;
  - il prompt del sentiment e' una COSTANTE di modulo che la chiamata compone:
    e' cio' che il decimo controllo del cancello misura (canale «news/sentiment»).

Simboli, nomi e pesi INVENTATI: tests/ e' pubblico.
"""
import json
import os
import subprocess
import sys
import types

import pytest

import bellomberg.market_data.news_aggregator as na

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))   # convenzione di tests/test_fonti_example.py
ESEMPIO = os.path.join(REPO, "src", "bellomberg", "resources", "examples",
                       "news_search_terms.example.json")
SIMBOLI_INVENTATI = {"ACME.MI", "BETA.DE", "GAMMA", "KRYPTO"}
TERMINI_INVENTATI = {"Acme Manifatture", "Acme", "Beta Industrie", "Gamma Robotics"}
CHIAVI_SERVIZIO = {"_leggimi"}


# ------------------------------------------------------------------ il caricatore

def test_negozio_assente_e_dichiarato_col_percorso_e_con_l_esempio_da_copiare(tmp_path):
    p = tmp_path / "mai_scritto.json"
    r = na.carica_termini(str(p))
    assert r["termini"] == {}
    assert r["origine"] == "assente"
    assert str(p) in r["motivo"] and "news_search_terms.example.json" in r["motivo"]


def test_negozio_illeggibile_e_dichiarato_col_tipo_di_errore(tmp_path):
    p = tmp_path / "rotto.json"
    p.write_text("{non json", encoding="utf-8")
    r = na.carica_termini(str(p))
    assert r["termini"] == {}
    assert r["origine"] == "illeggibile"
    assert "JSONDecodeError" in r["motivo"]


def test_negozio_letto_con_le_chiavi_di_servizio_saltate(tmp_path):
    p = tmp_path / "negozio.json"
    p.write_text(json.dumps({"_leggimi": "servizio",
                             "ACME.MI": ["Acme Manifatture", "Acme"],
                             "KRYPTO": None}), encoding="utf-8")
    r = na.carica_termini(str(p))
    assert r["termini"] == {"ACME.MI": ["Acme Manifatture", "Acme"], "KRYPTO": None}
    assert r["origine"] == str(p)
    assert r["motivo"] is None


@pytest.mark.parametrize("voce", ["Acme", ["Acme", 3], [], {"nome": "Acme"}, ["   "], 7])
def test_una_voce_malformata_rende_illeggibile_il_negozio_INTERO(tmp_path, voce):
    """O tutto o niente: un negozio caricato a meta' e' un ripiego muto."""
    p = tmp_path / "negozio.json"
    p.write_text(json.dumps({"ACME.MI": ["Acme"], "BETA.DE": voce}), encoding="utf-8")
    r = na.carica_termini(str(p))
    assert r["termini"] == {}
    assert r["origine"] == "illeggibile"
    assert "BETA.DE" in r["motivo"]


def test_un_negozio_che_non_e_un_oggetto_e_illeggibile(tmp_path):
    p = tmp_path / "lista.json"
    p.write_text(json.dumps(["ACME.MI"]), encoding="utf-8")
    r = na.carica_termini(str(p))
    assert r["termini"] == {} and r["origine"] == "illeggibile"
    assert "list" in r["motivo"]


def test_termini_correnti_rileggono_il_negozio_a_ogni_chiamata(tmp_path, monkeypatch):
    p = tmp_path / "negozio.json"
    p.write_text(json.dumps({"ACME.MI": ["Acme"]}), encoding="utf-8")
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(p))
    assert na.termini_correnti() == {"ACME.MI": ["Acme"]}
    p.write_text(json.dumps({"ACME.MI": ["Acme", "Acme Manifatture"]}), encoding="utf-8")
    assert na.termini_correnti()["ACME.MI"] == ["Acme", "Acme Manifatture"]


def test_origine_termini_all_import_dice_lo_stato_vero_del_negozio():
    """Come ORIGINE_FONTI: l'esito dell'ultimo caricamento all'import, mai un
    «non ancora caricato» che nessuno aggiorna."""
    assert set(na.ORIGINE_TERMINI) == {"origine", "motivo"}
    o = na.ORIGINE_TERMINI["origine"]
    assert o in ("assente", "illeggibile") or os.path.isabs(o), o
    assert na.PERCORSO_TERMINI.endswith("news_search_terms.json")


# ------------------------------------------------------------------ la ricerca

def _negozio(tmp_path, monkeypatch, voci):
    p = tmp_path / "negozio.json"
    p.write_text(json.dumps(voci), encoding="utf-8")
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(p))
    return p


@pytest.fixture
def provider_finti(monkeypatch):
    """Provider finti: catturano (nome, query), zero rete, cache svuotata."""
    chiamate = []

    def _finto(nome):
        def _f(query, *a, **k):
            chiamate.append((nome, query))
            return []
        return _f

    monkeypatch.setattr(na, "_fetch_newsapi", _finto("newsapi"))
    monkeypatch.setattr(na, "_fetch_thenewsapi", _finto("thenewsapi"))
    monkeypatch.setattr(na, "_fetch_gnews", _finto("gnews"))
    monkeypatch.setattr(na, "_fetch_yfinance_news", lambda t, n: [])
    monkeypatch.setattr(na, "_all_rss_cached", lambda: [])
    tiingo = types.ModuleType("tiingo_news")
    tiingo.tiingo_available = lambda: False
    tiingo.fetch_tiingo_news = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.tiingo_news', tiingo)
    na.invalidate_cache()
    return chiamate


@pytest.fixture
def log_catturato(monkeypatch):
    righe = []
    monkeypatch.setattr(na, "_log", lambda msg: righe.append(str(msg)))
    return righe


def test_la_ricerca_usa_i_termini_del_negozio_e_non_il_ticker(tmp_path, monkeypatch, provider_finti):
    _negozio(tmp_path, monkeypatch, {"ACME.MI": ["Acme Manifatture", "Acme"]})
    na.search_news_for_ticker("ACME.MI", days=1, max_per_source=1)
    assert provider_finti, "nessun provider interrogato: harness rotto"
    query = provider_finti[0][1]
    assert query == '"Acme Manifatture" OR Acme'
    assert all(q == query for _, q in provider_finti)


def test_voce_assente_cerca_il_ticker_nudo_e_lo_DICHIARA(tmp_path, monkeypatch, provider_finti, log_catturato):
    _negozio(tmp_path, monkeypatch, {"ACME.MI": ["Acme"]})
    na.search_news_for_ticker("BETA.DE", days=1, max_per_source=1)
    assert provider_finti[0][1] == "BETA.DE"
    dichiarazioni = [r for r in log_catturato if "BETA.DE" in r and "ticker nudo" in r]
    assert dichiarazioni, log_catturato
    assert "assente" in dichiarazioni[0]


def test_negozio_assente_la_ricerca_lo_dichiara_col_motivo(tmp_path, monkeypatch, provider_finti, log_catturato):
    mai = tmp_path / "mai_scritto.json"
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(mai))
    na.search_news_for_ticker("ACME.MI", days=1, max_per_source=1)
    assert provider_finti[0][1] == "ACME.MI"
    assert any("ACME.MI" in r and "negozio" in r and str(mai) in r for r in log_catturato), log_catturato


def test_voce_null_cercata_DIRETTAMENTE_usa_il_ticker_nudo_e_lo_dichiara(tmp_path, monkeypatch, provider_finti, log_catturato):
    """La voce null esclude dal GIRO automatico (e dal filtro SEC), non dalla ricerca
    diretta: a HEAD `search_news_for_ticker` non escludeva nessuno, e un [] muto qui
    sarebbe stato identico a «zero notizie» per chi chiama (review 04/09). Si cerca
    col ticker nudo e lo si dichiara."""
    _negozio(tmp_path, monkeypatch, {"KRYPTO": None})
    na.search_news_for_ticker("KRYPTO", days=1, max_per_source=1)
    assert provider_finti and provider_finti[0][1] == "KRYPTO"
    assert any("KRYPTO" in r and "null" in r and "giro" in r for r in log_catturato), log_catturato


@pytest.mark.parametrize("negozio", [{"krypto": None},
                                     {" ACME.MI": ["Acme"]},
                                     {"ACME.MI": ["Acme"], "acme.mi": ["Acme"]}])
def test_una_chiave_non_canonica_rende_illeggibile_il_negozio_col_motivo_giusto(tmp_path, negozio):
    """Ogni lookup fa .upper() sul ticker: una chiave minuscola o con spazi non verrebbe
    mai trovata e il log direbbe «voce assente» — vero per il codice, falso per chi ha
    appena scritto la voce. O tutto o niente, col nome della chiave nel motivo."""
    p = tmp_path / "negozio.json"
    p.write_text(json.dumps(negozio), encoding="utf-8")
    r = na.carica_termini(str(p))
    assert r["termini"] == {} and r["origine"] == "illeggibile"
    colpevole = [k for k in negozio if k != k.strip().upper() or k.strip().upper() in
                 [x.strip().upper() for x in negozio if x != k]][0]
    assert repr(colpevole) in r["motivo"] or colpevole in r["motivo"], r["motivo"]


def test_la_ricerca_normalizza_il_ticker_a_maiuscolo_come_prima(tmp_path, monkeypatch, provider_finti):
    _negozio(tmp_path, monkeypatch, {"ACME.MI": ["Acme"]})
    na.search_news_for_ticker("acme.mi", days=1, max_per_source=1)
    assert provider_finti[0][1] == "Acme"


# ------------------------------------------------------------------ l'esclusione

def test_escluso_dalle_news_vale_solo_per_la_voce_null(tmp_path, monkeypatch):
    _negozio(tmp_path, monkeypatch, {"ACME.MI": ["Acme"], "KRYPTO": None})
    assert na.escluso_dalle_news("KRYPTO") is True
    assert na.escluso_dalle_news("krypto") is True
    assert na.escluso_dalle_news("ACME.MI") is False
    assert na.escluso_dalle_news("BETA.DE") is False, "assente NON e' escluso"


def test_il_giro_news_salta_gli_esclusi_e_pesa_gli_altri(tmp_path, monkeypatch):
    """L'helper di auto_pull_feed: ticker del giro + contesto «TICKER (peso%)»."""
    _negozio(tmp_path, monkeypatch, {"KRYPTO": None})
    positions = [{"ticker": "ACME.MI", "peso_pct": 4.26},
                 {"ticker": "KRYPTO", "peso_pct": 3.0},
                 {"ticker": "BETA.DE", "weight_pct": "1.5"},
                 {"ticker": "GAMMA", "peso_pct": "n.d."}]
    tickers, ctx = na.giro_news(positions)
    assert tickers == ["ACME.MI", "BETA.DE", "GAMMA"]
    assert ctx == ["ACME.MI (4.3%)", "BETA.DE (1.5%)", "GAMMA (0.0%)"]


class _DBFinto:
    def __init__(self, positions, db_path=None):
        self._positions = positions
        self.db_path = db_path

    def get_portfolio_summary(self):
        return {"positions": list(self._positions)}


def test_search_portfolio_news_non_cerca_gli_esclusi(tmp_path, monkeypatch):
    _negozio(tmp_path, monkeypatch, {"KRYPTO": None})
    cercati = []
    monkeypatch.setattr(na, "MemoryDB", lambda: _DBFinto([{"ticker": "ACME.MI"}, {"ticker": "KRYPTO"}]))
    monkeypatch.setattr(na, "search_news_for_ticker", lambda t, **k: cercati.append(t) or [])
    na.search_portfolio_news(days=1, max_per_ticker=1)
    assert cercati == ["ACME.MI"]


def test_fetch_corporate_events_non_manda_gli_esclusi_alla_sec(tmp_path, monkeypatch, provider_finti):
    _negozio(tmp_path, monkeypatch, {"KRYPTO": None})
    ricevuti = []
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(sec_edgar, "get_corporate_events_for_portfolio",
                        lambda tk, days=14: ricevuti.append(list(tk)) or [])
    monkeypatch.setattr(na, "MemoryDB", lambda: _DBFinto(
        [{"ticker": "ACME.MI"}, {"ticker": "GAMMA"}, {"ticker": "KRYPTO"}]))
    topics = types.ModuleType("news_topics")
    topics.get_category_topics = lambda cat: []
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.news_topics', topics)
    na.fetch_corporate_events(days=1, max_items=5)
    assert ricevuti == [["GAMMA"]]


def test_negozio_illeggibile_le_esclusioni_NON_si_applicano_e_il_giro_lo_dichiara(tmp_path, monkeypatch, provider_finti, log_catturato):
    """Il difetto confermato da tre lenti della review 04/09: con un negozio rotto (una
    virgola in piu' dopo un edit a mano) `termini_correnti` tornava {} buttando origine e
    motivo, e il simbolo con voce null finiva alla SEC senza una riga di log. Ora il giro
    dichiara UNA volta lo stato del negozio; il comportamento (nessuna esclusione
    applicabile) resta, ma non e' piu' muto."""
    p = tmp_path / "rotto.json"
    p.write_text('{"KRYPTO": null,}', encoding="utf-8")
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(p))
    ricevuti = []
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(sec_edgar, "get_corporate_events_for_portfolio",
                        lambda tk, days=14: ricevuti.append(list(tk)) or [])
    monkeypatch.setattr(na, "MemoryDB", lambda: _DBFinto([{"ticker": "GAMMA"}, {"ticker": "KRYPTO"}]))
    topics = types.ModuleType("news_topics")
    topics.get_category_topics = lambda cat: []
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.news_topics', topics)
    na.invalidate_cache()
    na.fetch_corporate_events(days=1, max_items=5)
    assert ricevuti == [["GAMMA", "KRYPTO"]], "senza negozio leggibile non c'e' esclusione da applicare"
    dichiarazioni = [r for r in log_catturato if "illeggibile" in r and "esclusion" in r]
    assert dichiarazioni, log_catturato
    assert "JSONDecodeError" in dichiarazioni[0], "il motivo del caricatore deve viaggiare nel log"


def test_negozio_assente_il_giro_news_lo_dichiara(tmp_path, monkeypatch, log_catturato):
    mai = tmp_path / "mai_scritto.json"
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(mai))
    tickers, ctx = na.giro_news([{"ticker": "ACME.MI", "peso_pct": 1.0}, {"ticker": "KRYPTO"}])
    assert tickers == ["ACME.MI", "KRYPTO"]
    assert any("assente" in r and "esclusion" in r and str(mai) in r for r in log_catturato), log_catturato


def test_auto_pull_feed_e_cablato_sul_giro_news(tmp_path, monkeypatch, provider_finti):
    """Il cablaggio, non solo l'helper: l'escluso non viene cercato e non entra nel
    contesto coi pesi che va al classificatore."""
    from bellomberg.storage import memory_db
    _negozio(tmp_path, monkeypatch, {"KRYPTO": None})
    vero = memory_db.MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                              chroma_path=str(tmp_path / "chroma"))   # mai il DB vero (tripwire)
    positions = [{"ticker": "ACME.MI", "peso_pct": 4.26}, {"ticker": "KRYPTO", "peso_pct": 3.0}]
    monkeypatch.setattr(na, "MemoryDB", lambda: _DBFinto(positions, vero.db_path))
    # un preferito escluso (voce null) non entra nel giro nemmeno dalla porta dei preferiti
    monkeypatch.setattr(na, "_favorites_tickers", lambda: {"KRYPTO", "BETA.DE"})
    cercati = []
    monkeypatch.setattr(na, "search_news_for_ticker", lambda t, **k: cercati.append(t) or [])
    monkeypatch.setattr(na, "search_news_global", lambda q, **k: [])
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    monkeypatch.setattr(na, "_scrivi_stato_giro", lambda out: None)
    contesti = []
    monkeypatch.setattr(na, "_classify_with_haiku",
                        lambda it, ctx: contesti.append(list(ctx)) or
                        {"sentiment": "neutral", "sentiment_score": 0.0, "relevance": 5,
                         "headline_it": "", "why_matters": ""})
    monkeypatch.setattr(na, "_drop_junk", lambda items: items)
    monkeypatch.setattr(na, "_dedupe", lambda items: items)
    # un item finto per far passare il classificatore (cosi' il contesto e' misurabile)
    monkeypatch.setattr(na, "search_news_global",
                        lambda q, **k: [{"title": "Notizia finta", "url": "https://example.invalid/1",
                                         "published_at": "2026-09-04T10:00:00"}])
    out = na.auto_pull_feed(days=1, classify=True, max_per_ticker=1, max_per_theme=1)
    assert cercati == ["ACME.MI", "BETA.DE"]
    assert contesti and contesti[0] == ["ACME.MI (4.3%)"]
    assert out["saved"] >= 1


# ------------------------------------------------------------------ il prompt del sentiment

RISPOSTA_HAIKU = json.dumps({"sentiment": "neutral", "sentiment_score": 0.0, "relevance": 6,
                             "headline_it": "Titolo mock", "why_matters": "Perche' mock"})


@pytest.fixture
def prompt_catturato(monkeypatch):
    from bellomberg.core import llm_client
    from bellomberg.core import config
    catturati = []

    class _Blocco:
        def __init__(self, text):
            self.text = text

    class _Risposta:
        def __init__(self, text):
            self.content = [_Blocco(text)]

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            catturati.append(kwargs["messages"][0]["content"])
            return _Risposta(RISPOSTA_HAIKU)

    monkeypatch.setattr(llm_client, "OpenRouterClient", _FakeAnthropic)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-collaudo", raising=False)
    return catturati


def test_il_prompt_del_sentiment_e_una_costante_che_la_chiamata_COMPONE(prompt_catturato):
    """Tutto il testo statico sta in NEWS_SENTIMENT_PROMPT: e' la costante che il
    cancello rende e misura. Se un esempio o una regola vive fuori dalla costante,
    il prompt spedito non e' piu' uguale alla costante composta, e questo cade."""
    item = {"title": "Acme Manifatture pubblica la semestrale", "snippet": "Capex invariato",
            "ticker_mentioned": "ACME.MI", "theme": "corporate"}
    ctx = ["ACME.MI (4.3%)", "BETA.DE (1.5%)"]
    na._classify_with_haiku(item, ctx)
    atteso = na.NEWS_SENTIMENT_PROMPT.format(
        title=item["title"], snippet=item["snippet"], tk_tag="ACME.MI",
        theme_tag="corporate", tickers_str="ACME.MI (4.3%), BETA.DE (1.5%)")
    assert prompt_catturato[0] == atteso


def test_la_costante_del_prompt_ha_i_cinque_segnaposto_e_niente_altro_da_comporre():
    segnaposto = {"title", "snippet", "tk_tag", "theme_tag", "tickers_str"}
    reso = na.NEWS_SENTIMENT_PROMPT.format(**{k: "<%s>" % k for k in segnaposto})
    for k in segnaposto:
        assert "<%s>" % k in reso, k
    # le graffe del JSON d'esempio restano graffe dopo il format
    assert '{"sentiment"' in reso


def test_gli_esempi_del_prompt_sono_inventati_e_hanno_la_forma_di_prima():
    """Sostituzione, mai cancellazione (accordo con la chat backend 04/09): l'esempio
    di headline (societa' + operazione + variazione) e quello di why_matters (ticker +
    peso) restano, su nomi inventati."""
    t = na.NEWS_SENTIMENT_PROMPT
    assert "Acme lancia offerta su Beta, titolo +6%" in t
    assert "ACME.MI 4,1% del book" in t


# ------------------------------------------------------------------ l'esempio tracciato

def _esempio():
    return json.load(open(ESEMPIO, encoding="utf-8"))


def _voci(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


def test_esempio_mostra_le_due_forme_lista_e_null():
    voci = _voci(_esempio())
    assert any(isinstance(v, list) and v for v in voci.values()), "manca una voce con termini"
    assert any(v is None for v in voci.values()), "manca una voce null (esclusione dichiarata)"


def test_esempio_non_contiene_dati_veri():
    """Le voci sono ESATTAMENTE i simboli inventati, i termini esattamente quelli
    inventati, le chiavi di servizio solo quelle note (un ticker vero sa nascondersi
    dietro un underscore)."""
    d = _esempio()
    voci = _voci(d)
    assert set(voci) == SIMBOLI_INVENTATI, set(voci)
    assert set(d) - set(voci) <= CHIAVI_SERVIZIO, set(d) - set(voci)
    termini = {t for v in voci.values() if v for t in v}
    assert termini == TERMINI_INVENTATI, termini


def test_esempio_si_carica_col_caricatore_vero():
    r = na.carica_termini(ESEMPIO)
    assert r["origine"] == ESEMPIO and r["motivo"] is None
    assert r["termini"]["KRYPTO"] is None
    assert r["termini"]["ACME.MI"] == ["Acme Manifatture", "Acme"]


def test_esempio_SELEZIONATO_dall_export():
    """Essere nominati nell'allowlist non e' uscire: si misura con le funzioni VERE
    dell'export (stesso pattern di tests/test_fonti_example.py). Nel tree pubblico
    salta con motivo dichiarato."""
    allowlist = os.path.join(REPO, "tools", "release", "policy", "ALLOWLIST.txt")
    if not os.path.exists(allowlist):
        pytest.skip("policy/ALLOWLIST.txt assente: siamo nel tree pubblico (P2)")
    from tools.release import export_pubblico as ep
    from tools.release import verifica_pubblico as vp
    scelti, _ = ep.seleziona(ep.file_tracciati(REPO), vp.leggi_lista(allowlist))
    assert "src/bellomberg/resources/examples/news_search_terms.example.json" in scelti
    # il verso opposto, detto e misurato: il negozio VERO non esce (data/ e' ignorato da
    # git, quindi non e' nemmeno fra i tracciati; e ESCLUSI copre data/)
    assert "data/news_search_terms.json" not in scelti
    assert "data/news_search_terms.json" not in ep.file_tracciati(REPO)


def test_esempio_tracciato_e_negozio_ignorato():
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8").returncode

    esempio = "src/bellomberg/resources/examples/news_search_terms.example.json"
    assert git("ls-files", "--error-unmatch", esempio) == 0, \
        "news_search_terms.example.json non e' tracciato da git"
    assert git("check-ignore", esempio) == 1
    assert git("check-ignore", "data/news_search_terms.json") == 0, \
        "data/news_search_terms.json DEVE restare ignorato"


def test_il_letterale_non_esiste_piu_nel_sorgente():
    """La migrazione e' finita solo quando il dict cablato non c'e' piu'."""
    src = open(os.path.join(REPO, "src", "bellomberg", "market_data", "news_aggregator.py"),
               encoding="utf-8").read()
    assert "TICKER_SEARCH_TERMS" not in src
    assert 'in {"' not in src, "un set letterale di ticker da saltare e' ancora nel codice"
