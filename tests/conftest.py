"""Suite pytest OFFLINE del progetto (§9-bis n.7, piano ok PM 21/07).

Regole della suite:
- ZERO rete, ZERO chiamate API, ZERO DB vivo: ogni test usa DB temporanei
  (tmp_path) e client Anthropic FINTI. I moduli che toccherebbero rete/DB
  (current_facts, chat_tools, price_updater, estrazione Sonnet dell'ACTION
  TABLE, FX di llm_pricing) vengono stubbati nei singoli file di test.
- I test cristallizzano i collaudi del 15-21/07 (V4 banca, robustezza run,
  migrazioni DB, persistenza, freshness, GNews, pricing, sanity).
- Dove l'API reale non combacia col test previsto si adatta IL TEST, mai
  il codice (nota operativa del piano).

Questo conftest mette la root del progetto sul sys.path (i moduli si
importano come dal root, senza installazione) e garantisce una
ANTHROPIC_API_KEY fittizia dove l'ambiente non ne fornisce una (CI).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# 02/10: il pacchetto vive in src/ e la suite non deve dipendere dal .pth
# dell'install editable: macOS (Desktop in iCloud) lo rimarca "hidden" e
# Python 3.14 salta i .pth nascosti. Stessa cura del launcher; PYTHONPATH
# arriva anche ai sottoprocessi lanciati dai test.
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
_pp = os.environ.get("PYTHONPATH", "")
if SRC not in _pp.split(os.pathsep):
    os.environ["PYTHONPATH"] = SRC + (os.pathsep + _pp if _pp else "")

# ============================================================================
# SIGILLO DELLA CARTELLA DATI VERA (06/10/2026, Opus 5.5 — rapporto LOCK).
# PRIMA di ogni import di bellomberg: `core.paths` e `memory_db` fissano DATA_DIR e
# SQLITE_PATH all'import, quindi BELLOMBERG_DATA_DIR va spostata QUI, non in una fixture
# (la fixture arriva quando i moduli hanno gia' il percorso vero). Da qui in poi il DB, il
# lock della run pagata, l'heartbeat, i journal, trade_ideas/ e consensus_cache del
# processo dei test nascono in una cartella tmp di SESSIONE. Le cartelle VERE (junction
# `data`, `data` del checkout principale, BELLOMBERG_DATA_DIR dichiarata) si calcolano
# PRIMA dello spostamento e vanno al sigillo (audit hook, sotto) e alla spia (c).
# Dettagli e limiti: tests/_sigillo_dati.py. Prova: tests/test_sigillo_dati_veri.py.
# ============================================================================
_QUI_SIGILLO = os.path.dirname(os.path.abspath(__file__))
if _QUI_SIGILLO not in sys.path:
    sys.path.insert(0, _QUI_SIGILLO)
import _sigillo_dati  # noqa: E402

_DATI = _sigillo_dati.prepara_ambiente(os.environ, ROOT)
CARTELLE_VERE = _DATI["protette"]
CARTELLA_DATI_SESSIONE = _DATI["sessione"]
# installato subito: copre anche gli import e la collection. Uno per processo (vedi
# `sigillo_di_sessione`): `from tests.conftest import ...` rilegge questo file
_SIGILLO = _sigillo_dati.sigillo_di_sessione(
    CARTELLE_VERE, os.environ.get(_sigillo_dati.VARIABILE_MODALITA, "tripwire") != "conta",
    CARTELLA_DATI_SESSIONE)
CARTELLA_DATI_SESSIONE = _SIGILLO.sessione

# In CI non c'e' .env: una chiave fittizia basta perche' NESSUN test deve
# mai creare un client Anthropic vero (setdefault: quella vera non si tocca).
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-offline-test-suite")
# Il lifecycle dell'API viene esercitato da alcuni TestClient: il pull automatico
# deve restare spento nella suite offline, mentre i test del manager lo riattivano
# esplicitamente con un puller finto.
os.environ["NEWS_AUTO_REFRESH_ENABLED"] = "false"   # G3: assegnazione forte, un true del .env non accende il timer
# Idem per il controllo filing: nei test del lifespan non deve toccare il DB reale.
os.environ.setdefault("FILING_AUTO_REFRESH_ENABLED", "false")
# 02/09 (pubblicazione B2): SEC_CONTACT_EMAIL e' letta A OGNI CHIAMATA da
# sec_edgar._headers()/esef._headers(); senza, sec_edgar solleva ContattoMancante PRIMA
# della richiesta e i test che simulano la rete non arrivano al mock (esef dal 05/10
# usa invece uno User-Agent generico: decisione PM «ESEF senza contatto SEC»). Un
# contatto finto basta (setdefault: quello vero del .env non si tocca); i test
# del caso ASSENTE lo tolgono con monkeypatch.delenv.
os.environ.setdefault("SEC_CONTACT_EMAIL", "test-suite@example.com")
# 05/09 (OpenRouter, ordine PM): llm_client risolve chiave e modelli dal .env a OGNI
# chiamata e una variabile assente e' un errore dichiarato; la suite gira offline con
# valori di PROVA (setdefault: quelli veri del .env del PM non si toccano). Sono i
# modelli di IERI (a listino in llm_pricing), cosi' i test che misurano il costo di una
# run coi client finti — che non portano `cost_usd` — restano prezzabili come prima;
# la rete non si tocca comunque: la fixture `niente_rete_openrouter` sotto lo ferma prima.
for _nome, _valore in (
    ("OPENROUTER_API_KEY", "sk-or-dummy-offline-test-suite"),
    ("CHAT_MODEL", "claude-sonnet-5"), ("CHAT_MAX_TOKENS", "5600"),
    ("CONSIGLIERE_MODEL", "claude-opus-5"), ("CONSIGLIERE_R0_MODEL", "claude-sonnet-5"),
    ("CAPO_MODEL", "claude-opus-5"), ("RED_TEAM_MODEL", "claude-sonnet-5"),
    ("REFLECTION_MODEL", "claude-sonnet-5"), ("ACTION_EXTRACTOR_MODEL", "claude-sonnet-5"),
    ("BRIEFING_MODEL", "claude-haiku-4-5-20251001"),
    ("NEWS_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001"),
):
    os.environ.setdefault(_nome, _valore)
# Gli OVERRIDE per agente/desk del .env VERO del PM (CHAT_QUANT_MODEL, CONSIGLIERE_QUANT_MODEL...)
# entrerebbero nei test via load_dotenv (che non sovrascrive una chiave gia' presente, anche
# vuota): qui li si fissa a "" — che per llm_client vale «assente» — cosi' ogni desk risolve
# la variabile base di prova, non la tabella del PM. Misurato il 05/09: senza questa riga
# quant usciva su z-ai/glm-5.3 e il suo costo diventava model_unknown.
for _nome in ("CHAT_CAPO_MODEL", "CHAT_MACRO_MODEL", "CHAT_QUANT_MODEL", "CHAT_OPTIONS_MODEL",
              "CHAT_FUNDAMENTALS_MODEL", "CHAT_CRYPTO_MODEL", "CHAT_EVENTDESK_MODEL",
              "CHAT_POLITICS_MODEL", "CHAT_NEWS_MODEL", "CONSIGLIERE_MACRO_MODEL",
              "CONSIGLIERE_QUANT_MODEL", "CONSIGLIERE_OPTIONS_MODEL",
              "CONSIGLIERE_FUNDAMENTALS_MODEL", "CONSIGLIERE_CRYPTO_MODEL",
              "CONSIGLIERE_EVENTDESK_MODEL", "NEWS_SUMMARY_MODEL",
              # MOD-TI 06/10: vuota = default MODEL_IDS della Trade Idea
              "TRADE_IDEA_SPECIALIST_MODEL", "TRADE_IDEA_RED_TEAM_MODEL",
              "TRADE_IDEA_CAPO_MODEL", "TRADE_IDEA_AUX_MODEL"):
    os.environ.setdefault(_nome, "")


@pytest.fixture(autouse=True)
def niente_rete_siti_emittenti(monkeypatch, tmp_path):
    """TRIPWIRE di rete (fase F, 04/10/2026): GLEIF e il sito della societa' (yfinance) non si
    toccano nei test. GLEIF «irraggiungibile» lascia l'esito di prima (nessun candidato); senza
    sito noto l'esplorazione non parte. I test di questi moduli passano `cerca=`/`info_fn=`."""
    import bellomberg.market_data.esef_sito as _sito

    def _gleif(nome):
        raise ConnectionError("rete GLEIF nei test: passa cerca=")

    monkeypatch.setattr(_sito, "gleif_lei_records", _gleif)
    monkeypatch.setattr(_sito, "_info_yfinance", lambda ticker: {})
    monkeypatch.setattr(_sito, "CARTELLA", tmp_path / "filing_sito")
    monkeypatch.setattr(_sito, "consigliere_in_corso", lambda: False)  # i processi veri non contano


@pytest.fixture(autouse=True)
def niente_rete_ripiego_ixbrl(monkeypatch):
    """TRIPWIRE di rete (R-FONTI 10/10, Opus 5.5; v2 riserva BASSI): il ripiego iXBRL dello storico SEC (elenco
    depositi e documento primario) che tenta la rete in un test FA FALLIRE il test. Si solleva `pytest.fail`
    (BaseException): l'`except Exception` del ripiego NON lo inghiotte (prima: ConnectionError dichiarato come
    «errore» e il test restava verde con una chiamata di rete imprevista). Chi prova il ripiego monta finti
    espliciti su `_submissions_sec` / `_scarica_documento_sec`; chi prova la rete giu' usa `ripiego_ixbrl_giu`."""
    import bellomberg.market_data.sec_xbrl as _sx

    def _vietata(*a, **k):
        pytest.fail("rete SEC imprevista nel test: ripiego iXBRL senza finti espliciti "
                    "(monta _submissions_sec/_scarica_documento_sec o la fixture ripiego_ixbrl_giu)", pytrace=False)
    monkeypatch.setattr(_sx, "_submissions_sec", _vietata)
    monkeypatch.setattr(_sx, "_scarica_documento_sec", _vietata)


@pytest.fixture
def ripiego_ixbrl_giu(monkeypatch):
    """Rete SEC «giu'» ESPLICITA per il ripiego iXBRL: ConnectionError, che il ripiego dichiara come «errore»."""
    import bellomberg.market_data.sec_xbrl as _sx

    def _giu(*a, **k):
        raise ConnectionError("rete SEC giu' (finto esplicito del test)")
    monkeypatch.setattr(_sx, "_submissions_sec", _giu)
    monkeypatch.setattr(_sx, "_scarica_documento_sec", _giu)


@pytest.fixture(autouse=True)
def niente_rete_openrouter(monkeypatch):
    """TRIPWIRE di rete (05/09): un client OpenRouter costruito SENZA trasporto finto
    (httpx.MockTransport) e' un test che sta per chiamare l'API vera con la chiave dummy:
    fallisce QUI, con la causa, invece di un 401 di rete o — peggio — di una spesa vera.
    I test montano i finti su `llm_client.OpenRouterClient` (o `capo.`/`base.`), come prima
    facevano su `anthropic.Anthropic`; tests/test_llm_client.py passa sempre `trasporto=`."""
    import bellomberg.core.llm_client as _lc

    def _vietato(timeout, trasporto, _orig=_lc._nuovo_client_http):
        if trasporto is None:
            raise AssertionError("client OpenRouter VERO costruito in un test: monta un finto "
                                 "su llm_client.OpenRouterClient o passa trasporto=MockTransport")
        return _orig(timeout, trasporto)

    def _vietato_async(timeout, trasporto, _orig=_lc._nuovo_client_http_async):
        if trasporto is None:
            raise AssertionError("client OpenRouter VERO (async) costruito in un test: monta un "
                                 "finto su llm_client.AsyncOpenRouterClient o passa trasporto=")
        return _orig(timeout, trasporto)

    monkeypatch.setattr(_lc, "_nuovo_client_http", _vietato)
    monkeypatch.setattr(_lc, "_nuovo_client_http_async", _vietato_async)


# G3 (04/10/2026): i test delle notizie non toccano la rete (Yahoo, provider news, siti).
# Nome del file di test che contiene una di queste parole = test delle notizie.
_TEST_NOTIZIE = ("news", "notizie", "headline", "briefing")
# REV_G3 R4: anche i file che NOMINANO un modulo delle notizie (import o monkeypatch per stringa),
# qualunque sia il loro nome. Letto dal sorgente del file di test, una volta per file.
_MODULI_NOTIZIE = ("news_aggregator", "news_sources", "finnhub_news", "tiingo_news", "reddit_news",
                   "news_topics", "article_summary", "headline_translation", "news_refresh_manager",
                   "briefing_engine", "errori_sicuri")
_COPERTI: dict = {}


def _test_delle_notizie(path) -> bool:
    if any(parola in path.name.lower() for parola in _TEST_NOTIZIE):
        return True
    if path not in _COPERTI:
        try:
            sorgente = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            sorgente = ""
        _COPERTI[path] = any(nome in sorgente for nome in _MODULI_NOTIZIE)
    return _COPERTI[path]


@pytest.fixture(autouse=True)
def niente_rete_notizie(request, monkeypatch):
    """TRIPWIRE di rete per i test delle notizie (G3, 04/10/2026). Prima nessuna guardia
    generale: un test di search_news_for_ticker con ticker col punto poteva interrogare
    Yahoo davvero (_nome_emittente_yahoo, _ticker_us_yahoo_univoco).
    - socket di Python: connect/getaddrinfo verso host non locali (requests, urllib, feedparser);
    - yfinance: Ticker/Tickers/download/Search, perche' con curl_cffi esce dal socket di Python.
    La chiamata fallisce con ConnectionError (i fetcher la inghiottono come guasto) e il test
    FALLISCE in chiusura con l'host: un except del codice non puo' nasconderla. 127.0.0.1/::1
    restano liberi (socketpair di asyncio su Windows, TestClient). Chi prova la rete monta i
    suoi finti con monkeypatch: vincono su questa fixture."""
    if not _test_delle_notizie(request.node.path):
        yield
        return
    import socket

    violazioni = []
    locali = ("127.0.0.1", "::1", "localhost")

    def _vieta(host):
        if host in locali or str(host).startswith("127."):
            return
        violazioni.append(str(host))
        raise ConnectionError("rete vera in un test delle notizie: %r (monta un finto)" % (host,))

    def _connect(sock, address, _orig=socket.socket.connect):
        _vieta(address[0] if isinstance(address, tuple) else address)
        return _orig(sock, address)

    def _connect_ex(sock, address, _orig=socket.socket.connect_ex):
        _vieta(address[0] if isinstance(address, tuple) else address)
        return _orig(sock, address)

    def _getaddrinfo(host, *args, _orig=socket.getaddrinfo, **kwargs):
        _vieta(host)
        return _orig(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    try:
        import yfinance
    except ImportError:
        yfinance = None
    if yfinance is not None:
        for nome in ("Ticker", "Tickers", "download", "Search"):
            if hasattr(yfinance, nome):
                monkeypatch.setattr(yfinance, nome,
                                    lambda *a, _n=nome, **k: _vieta("yfinance." + _n))
    yield violazioni          # la prova del tripwire la legge e la svuota
    if violazioni:
        pytest.fail("test delle notizie che usa la rete vera: %s" % sorted(set(violazioni)))


# ============================================================================
# GUARDIA "MAI LA PRODUZIONE" (26/07 sera-5, Opus 5 — voce I-3 passo 0)
#
# Il bug che l'ha resa necessaria, MISURATO non dedotto: `pytest
# tests/test_persistence.py` (4 test, 2,3s, dichiarati OFFLINE) riscriveva
# `data/scorekeeper_snapshot.json` di PRODUZIONE con uno scorecard degradato
# (overall n=0, 32 fetch KO). Catena: test_persistence stubba `price_updater`
# senza `data_ticker` -> `db.build_specialist_memory_context()` ->
# memory_db.py:1912 `get_track_record_for_specialist()` ->
# `scorekeeper.compute_scorecard(db=None)` -> apre il DB VERO (74 candidati
# direzionali letti dalla produzione) e scrive il SNAP_PATH VERO.
# Conseguenza: per 15' dopo ogni giro di suite (cooldown scorekeeper.py:37)
# il track record su disco dichiarava "MISURA FALLITA" su una misura sana.
#
# Due presidi, di natura DIVERSA di proposito:
#  (a) SNAP_PATH -> tmp: sandbox. Nessun test puo' scrivere quel file.
#  (b) DB di produzione -> TRIPWIRE che FALLISCE il test, non redirezione
#      silenziosa: se un test aprisse il DB vero e trovasse un tmp vuoto
#      passerebbe comunque, e nessuno imparerebbe niente (sarebbe un
#      fallback zitto, regola PM 14/07).
# ============================================================================


class ProduzioneToccata(BaseException):
    """Un test ha aperto il DB di produzione.

    Deriva da **BaseException**, non da Exception, di proposito: i due punti
    che ci arrivano stanno dentro `try/except Exception: pass`
    (memory_db.py:1912 track record specialista, :2229 track record Capo) —
    un'eccezione normale verrebbe INGHIOTTITA e la guardia sarebbe muta,
    cioe' esattamente il difetto che sta chiudendo.
    """


# in cima, non dentro la fixture autouse: se `specialists.base` non fosse
# importabile, dentro la fixture diventerebbero 718 errori identici su test che col
# Blackboard non c'entrano nulla, invece di UN errore di collection dichiarato una
# volta (confutatore 21/08).
from bellomberg.agents.specialists.base import Blackboard as _Blackboard
import bellomberg.storage.memory_db as _memory_db

# B4 (02/09): il DB del PM e' dove dice memory_db (BELLOMBERG_DATA_DIR compresa),
# catturato QUI prima di ogni fixture — non piu' un join su ROOT.
# SIGILLO 06/10: ora e' il DB della cartella di SESSIONE (il vero e' sotto il sigillo).
# La regola (b) resta com'era: un test che apre il percorso di DEFAULT invece del suo
# tmp_path cade, ovunque punti il default.
DB_PRODUZIONE = _memory_db.SQLITE_PATH

# SIGILLO 06/10: controllo d'avvio. Se un percorso fissato all'import puntasse ancora a una
# cartella vera (un import di bellomberg arrivato PRIMA della testa di questo file, una
# BELLOMBERG_DATA_DIR rimessa dal .env...), la suite non parte: nessun test gira.
# Una volta per processo: `from tests.conftest import ...` rilegge questo file DENTRO un test,
# quando le fixture hanno gia' ripuntato HEARTBEAT_PATH al tmp_path del test.
from bellomberg.core import paths as _paths_sigillo  # noqa: E402
_PERCORSI_ALL_AVVIO = () if getattr(_sigillo_dati, "avvio_controllato", False) else (
    ("core.paths.DATA_DIR", _paths_sigillo.DATA_DIR),
    ("core.paths.SQLITE_PATH", _paths_sigillo.SQLITE_PATH),
    ("memory_db.DB_DIR", _memory_db.DB_DIR),
    ("memory_db.SQLITE_PATH", _memory_db.SQLITE_PATH),
    ("Blackboard.HEARTBEAT_PATH", _Blackboard.HEARTBEAT_PATH))
for _nome_p, _valore_p in _PERCORSI_ALL_AVVIO:
    if _SIGILLO.tocca_il_vero(_valore_p) or not _sigillo_dati._sotto(_valore_p, CARTELLA_DATI_SESSIONE):
        raise RuntimeError(
            "SIGILLO: %s = %s non e' nella cartella dati di SESSIONE (%s): la suite si ferma "
            "prima di toccare i dati veri (%s)." % (_nome_p, _valore_p, CARTELLA_DATI_SESSIONE,
                                                     "; ".join(CARTELLE_VERE)))
_sigillo_dati.avvio_controllato = True

# i 3 moduli che legano `connect_sqlite` a import-time: NON basta patchare
# `memory_db.connect_sqlite`, il nome locale resta il loro. E non e' un rischio
# futuro: la suite li importa GIA' (test_f12_gex, test_log_pipe_morta,
# test_news_providers, test_verita_numeri_lotto_b/c). Il peggiore e'
# `bellomberg_api._fav_db()`, che ha il path di produzione HARDCODATO e ci fa
# CREATE TABLE + ALTER TABLE. Trovato dalla review avversariale 26/07 sera-5:
# la prima versione di questo file lo dichiarava come ipotetico ("se un domani
# un test importa uno di quelli") — era falso, era gia' oggi.
MODULI_CON_BINDING_A_IMPORT = (
    "bellomberg.api.bellomberg_api", "bellomberg.market_data.news_aggregator",
    "bellomberg.portfolio.twr_engine",
)


def _reale(p) -> str:
    """Path confrontabile su Windows: junction risolta (data/ ->
    C:\\BellombergData), maiuscole normalizzate."""
    return os.path.normcase(os.path.realpath(str(p)))


def _sotto(p, base: str) -> bool:
    try:
        return _reale(p).startswith(_reale(base))
    except Exception:
        return False


def _e_il_db_di_produzione(path, tmp_consentito: str) -> bool:
    """Vero se `path` e' (o punta a) il DB del PM.

    Tre regole, in ordine:
      1. tutto cio' che sta nel tmp dei test e' lecito (compresi i DB fantasma
         costruiti apposta, es. tests/test_guidance.py);
      2. confronto per realpath col DB vero, **famiglia inclusa** (`-wal`,
         `-shm`: sono lo stesso database);
      3. rete di sicurezza sul NOME: fino al 02/09 `SQLITE_PATH` era relativo
         alla cwd e `pytest` lanciato da `tests/` lo faceva risolvere altrove;
         dal 02/09 (B4) e' ancorato a memory_db.py o a BELLOMBERG_DATA_DIR —
         la regola resta come cintura (lezione CLAUDE.md "0 posizioni = path
         sbagliato").
    """
    if isinstance(path, (bytes, bytearray)):
        # sqlite3 accetta i bytes, ma `str(b"...")` darebbe la loro REPR e il
        # confronto diventerebbe insensato: si decodifica, e se non si puo' si
        # blocca (fail-closed dichiarato, non un False zitto)
        try:
            path = bytes(path).decode("utf-8")
        except Exception:
            print(f"[GUARDIA] path in bytes non decodificabile, blocco: {path!r}")
            return True
    p = str(path)
    if p == ":memory:" or p.startswith("file::memory:"):
        return False
    if tmp_consentito and _sotto(p, tmp_consentito):
        return False
    try:
        vero, dato = _reale(DB_PRODUZIONE), _reale(p)
        if dato == vero or dato.startswith(vero + "-"):
            return True
    except Exception:
        # FAIL-CLOSED dichiarato: un path che realpath non digerisce (bytes,
        # URI sqlite) non deve passare in silenzio — sarebbe il fallback zitto
        # che questa guardia esiste per chiudere.
        print(f"[GUARDIA] path non risolvibile, blocco per prudenza: {p!r}")
        return True
    q = p.replace("\\", "/").lower()
    return q.endswith("/data/consigliere.db") or q == "data/consigliere.db"


@pytest.fixture(autouse=True)
def mai_la_produzione(monkeypatch, tmp_path):
    """Attiva per OGNI test della suite (autouse), senza che i test la chiedano.

    **Limiti DICHIARATI** (misurati dalla review avversariale 26/07 sera-5, non
    supposti):
    - copre `memory_db.connect_sqlite` + il nome locale nei 3 moduli che lo
      legano a import-time. NON copre le `sqlite3.connect` **raw** che
      scavalcano l'helper — censimento riverificato il 21/08 (le righe del 01/08 erano gia' scivolate: e' la
      ragione per cui un censimento con file:riga va rigenerato, non citato a memoria):
      `current_facts.py:314,406` (entrambe `?mode=ro`) · `cef_lookthrough.py:102` ·
      `bellomberg_api.py:555-556` · `retro_title_chats.py:178-179` (il suo
      `_backup` parte PRIMA della connect_sqlite: un test con `apply=True`
      senza `db_path` scriverebbe un backup vero prima che il tripwire scatti)
      · `bonifica_error_round2.py:47,81` (script one-off, guardia `__main__`).
      Oggi nessun test le raggiunge col path di produzione, ma sono cieche
      per costruzione;
    - non e' attiva in **collection** (le fixture girano dopo l'import dei
      moduli di test): codice a livello modulo che aprisse il DB sarebbe
      invisibile. Verificato che oggi non ce n'e';
    - `MemoryDB.__init__` fa `os.makedirs` **prima** di `_init_sqlite`, quindi
      su un checkout pulito i test che esercitano il tripwire creano una `data/`
      vuota prima di fallire.
    """
    from bellomberg.market_data import market_inputs
    from bellomberg.storage import memory_db
    from bellomberg.agents import scorekeeper
    from bellomberg.reporting import email_sender

    tmp_radice = str(tmp_path)

    # (0) F43(1) 27/08 (review): la cassa operativa. `memory_db.PORTFOLIO_JSON_PATH`
    #     e' ancorato alla radice del repo, quindi senza questo redirect SETTE
    #     test della suite leggevano il portfolio.json VERO del PM (misurato con
    #     una spia delle aperture): la suite e' offline per contratto.
    monkeypatch.setattr(memory_db, "PORTFOLIO_JSON_PATH",
                        os.path.join(tmp_radice, "portfolio.json"))

    # (a) lo snapshot dello scorekeeper: SNAP_PATH e' riletto a ogni chiamata
    #     (lettura in `compute_scorecard`, scrittura a fine calcolo) ->
    #     redirigerlo basta. Idioma gia' in casa: tests/test_scorekeeper.py.
    monkeypatch.setattr(scorekeeper, "SNAP_PATH",
                        str(tmp_path / "scorekeeper_snapshot.json"))

    # (a-bis) il last-known-good del risk-free: `tests/test_benchmark_series.py`
    #     riscriveva `data/rf_cache.json` di PRODUZIONE dopo un fetch VIVO a
    #     Bundesbank (trovato dalla review con un audit hook, non a occhio).
    #     Il buco "rete" resta aperto ed e' una voce a registro; qui si chiude
    #     almeno la scrittura.
    monkeypatch.setattr(market_inputs, "_LKG_PATH",
                        str(tmp_path / "rf_cache.json"), raising=False)

    # (a-ter) chroma: `MemoryDB()` di default aprirebbe lo store vero. Il
    #     tripwire sul DB scatta prima, ma solo perche' `_init_sqlite` precede
    #     `_init_chroma` — se qualcuno stubba il primo (idioma in casa), il
    #     secondo resterebbe scoperto.
    monkeypatch.setattr(memory_db, "CHROMA_PATH", str(tmp_path / "chroma"))

    # (a-quater) l'HEARTBEAT DI F4. Buco trovato il 21/08, MISURATO non dedotto:
    #     `Blackboard.__init__` scrive `data/current_run.json` alla sola COSTRUZIONE
    #     (472 byte, `running: true`, `current_round: 0`), e i test lo costruiscono
    #     senza reindirizzare — verificato cancellando il file e lanciando
    #     `pytest tests/test_blackboard_fra_desk.py`: ricompare.
    #     Conseguenza per il PM: dopo ogni giro di suite la pagina Agents Live
    #     dichiara una run IN CORSO che non esiste, e `updated_at` fresco spegne
    #     pure lo `stale_warning` che l'API avrebbe alzato (bellomberg_api,
    #     GET /agents/live) — cioe' il fantasma smette persino di sembrare vecchio.
    #     Stessa natura di (a): sandbox, non tripwire. Scrivere un heartbeat e'
    #     lecito per un test, e' il PATH a non doverlo essere.
    monkeypatch.setattr(_Blackboard, "HEARTBEAT_PATH", str(tmp_path / "current_run.json"))

    # (a-quinquies) l'outbox durevole delle email. Il mittente accoda il MIME
    # prima della consegna SMTP, quindi anche i test con SMTP finto scriverebbero
    # sotto data/email_outbox del checkout. L'accodamento e' lecito e viene
    # provato, ma deve restare nel sandbox del singolo test.
    monkeypatch.setattr(email_sender, "OUTBOX_DIR", tmp_path / "email_outbox")

    # (a-sexies) la cache in-process dell'edge scanner (22/08 sera, decisione
    #     (a) del PM): vive a livello modulo e sopravvive FRA i test, quindi la
    #     scansione finta del test precedente diventerebbe la "misura" del
    #     successivo (stessa chiave: il libro finto e' identico). Si pulisce
    #     SOLO se il modulo e' gia' importato — importarlo qui per l'intera
    #     suite sarebbe un costo senza ragione. Stessa natura di (a):
    #     isolamento, non tripwire.
    _se = sys.modules.get('bellomberg.portfolio.signal_engine')
    if _se is not None and hasattr(_se, "clear_scan_cache"):
        _se.clear_scan_cache()

    # (b) il DB vero: TRIPWIRE, non redirezione silenziosa.
    _vera = memory_db.connect_sqlite

    def _guardia(path=None, **kwargs):
        # default risolto a ogni chiamata, non congelato al setup della
        # fixture: i test che ripatchano `memory_db.SQLITE_PATH` (es.
        # test_guidance) devono vedere il LORO path, non quello di allora.
        p = memory_db.SQLITE_PATH if path is None else path
        if _e_il_db_di_produzione(p, tmp_radice):
            raise ProduzioneToccata(
                "un test ha aperto il DB DI PRODUZIONE (%s). La suite e' "
                "offline per contratto: usa tmp_path, oppure stubba il "
                "chiamante. Se ci sei arrivato da build_specialist_memory_context "
                "o build_capo_memory_context, il colpevole e' lo scorekeeper "
                "(monkeypatch.setattr(scorekeeper, 'get_track_record_for_"
                "specialist', lambda *a, **k: ''))." % p)
        return _vera(p, **kwargs)

    monkeypatch.setattr(memory_db, "connect_sqlite", _guardia)
    for _nome in MODULI_CON_BINDING_A_IMPORT:
        _mod = sys.modules.get(_nome)
        if _mod is not None and hasattr(_mod, "connect_sqlite"):
            monkeypatch.setattr(_mod, "connect_sqlite", _guardia)


@pytest.fixture(autouse=True)
def mai_la_produzione_store_watch(monkeypatch, tmp_path):
    """(b-bis) 04/10, rilievo RV-P-A 5: TradeIdeaWatchStore usa `sqlite3.connect` RAW
    (come TradeIdeaStore), quindi la guardia (b) non lo vede. Dopo la migrazione vera
    il worker aperto sul DB del PM scriverebbe in `decisions` e manderebbe email vere.
    TRIPWIRE sul path, stessa regola `_e_il_db_di_produzione` di (b): si guarda
    `_connect`, l'unico punto da cui lo store apre il DB (anche il costruttore).
    Import anticipato di proposito: un test che importa lo store DENTRO il corpo
    resta coperto. RAGGIO DICHIARATO: solo questo store."""
    from bellomberg.storage import trade_idea_watch_store as _ws

    tmp_radice = str(tmp_path)
    _vero_connect = _ws.TradeIdeaWatchStore._connect

    def _guardia_watch(self, **kwargs):
        if _e_il_db_di_produzione(self.db_path, tmp_radice):
            raise ProduzioneToccata(
                "un test ha aperto TradeIdeaWatchStore sul DB DI PRODUZIONE (%s): "
                "usa un DB in tmp_path." % self.db_path)
        return _vero_connect(self, **kwargs)

    monkeypatch.setattr(_ws.TradeIdeaWatchStore, "_connect", _guardia_watch)


@pytest.fixture(autouse=True)
def valuta_dal_book_finta(monkeypatch, tmp_path):
    """(b-ter) 04/10 (W1, Opus 5.5, assegnata da main): i wrapper dei tool solo-USA leggono la
    valuta della posizione con `copertura.valuta_dal_book`, che apre il DB del book. Nei test
    il book NON si legge mai: la funzione e' sostituita da un finto che risponde «fuori book»
    con una nota che lo dice. Chi vuole provare la lettura vera ripristina l'originale
    (`copertura.valuta_dal_book_vera`) e la punta su un DB in tmp_path, oppure passa
    `db_path` esplicito (il finto allora legge davvero quel DB)."""
    from bellomberg.market_data import copertura as _cop
    if not hasattr(_cop, "valuta_dal_book_vera"):
        monkeypatch.setattr(_cop, "valuta_dal_book_vera", _cop.valuta_dal_book, raising=False)
    _vera = _cop.valuta_dal_book_vera if hasattr(_cop, "valuta_dal_book_vera") else _cop.valuta_dal_book

    def _finta(ticker, db_path=None):
        # db_path ESPLICITO (un DB del test in tmp_path, come fanno i test dell'API): lettura
        # vera, sempre dietro il tripwire di connect_sqlite; senza db_path = il book di
        # produzione -> mai letto
        if db_path is not None:
            return _vera(ticker, db_path=db_path)
        return {"valuta": None, "origine": "fuori_book", "nota": "test: book non letto",
                "nome": None}
    monkeypatch.setattr(_cop, "valuta_dal_book", _finta)

    # W1 handoff-3 05/10: la risoluzione automatica dell'ISIN dei .MI
    # (market_data/isin_automatico.py) fa RETE in due punti soli: `_nome_dal_fornitore`
    # (yfinance) e `_risolvi` (Borsa Italiana). Nei test si neutralizzano QUEI due punti
    # dell'orchestratore, NON `borsa_italiana.risolvi_isin` ne' i lettori: i test di IT1/IT2
    # che provano la funzione vera la chiamano intatta. La memoria dei tentativi va in
    # tmp_path (mai data/cache_fonti_it). Le originali restano in `_nome_dal_fornitore_vero`
    # e `_risolvi_vero` per chi prova il cablaggio vero con fornitori finti.
    from bellomberg.market_data import isin_automatico as _ia
    for nome_attr in ("_nome_dal_fornitore", "_risolvi"):
        if not hasattr(_ia, nome_attr + "_vero"):
            monkeypatch.setattr(_ia, nome_attr + "_vero", getattr(_ia, nome_attr), raising=False)
    monkeypatch.setattr(_ia, "_nome_dal_fornitore",
                        lambda ticker: (None, None, "test: nome dal fornitore prezzi non chiesto"))
    monkeypatch.setattr(_ia, "_risolvi", lambda ticker, nome: {
        "ticker": str(ticker or "").strip().upper(), "stato": "non_trovato",
        "errore": "test", "motivo": "test: risoluzione ISIN non eseguita", "isin": None,
        "salvato": False, "negozio": None})
    monkeypatch.setattr(_ia, "PERCORSO_TENTATIVI", str(tmp_path / "isin_tentativi_auto.json"))


@pytest.fixture(autouse=True)
def negozi_isin_in_tmp(monkeypatch, tmp_path):
    """(W1 handoff-3, richiesta main 05/10) I negozi ISIN dei titoli italiani si ridirigono
    SEMPRE in tmp_path per TUTTA la suite: negozio confermato (data/isin_it.json della macchina),
    negozio automatico, cache delle fonti italiane, memoria dei tentativi. Altrimenti un test
    darebbe esiti diversi in un clone pulito (nessun negozio) e sulla macchina del PM (negozio
    vero). Si ridirigono PERCORSI, non funzioni: risolvi_isin e i lettori restano veri. Chi
    vuole un negozio lo scrive in tmp_path (o ripunta il percorso col proprio monkeypatch)."""
    from bellomberg.market_data import borsa_italiana as _bi
    from bellomberg.market_data import isin_automatico as _ia
    monkeypatch.setattr(_bi, "PERCORSO_ISIN", str(tmp_path / "isin_it_test.json"))
    monkeypatch.setattr(_bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto_test.json"))
    monkeypatch.setattr(_bi, "CACHE_DIR", str(tmp_path / "cache_fonti_it_test"))
    monkeypatch.setattr(_ia, "PERCORSO_TENTATIVI", str(tmp_path / "isin_tentativi_auto.json"))


@pytest.fixture(scope="session")
def _mandato_esempio_su_disco(tmp_path_factory):
    """05/09 (criterio 5): il PROFILO DI ESEMPIO di `mandato_pm.example.json` scritto UNA volta
    nel tmp della sessione — numeri inventati di un profilo prudente, nessun valore di chi
    lancia la suite."""
    from bellomberg.core import mandato_pm
    p = str(tmp_path_factory.mktemp("mandato") / "mandato_pm.json")
    mandato_pm.salva(mandato_pm.profilo_esempio(), p)
    return p


@pytest.fixture(autouse=True)
def mandato_di_prova(monkeypatch, _mandato_esempio_su_disco):
    """Ogni test legge il mandato dal file di esempio in tmp, MAI `data/mandato_pm.json` di chi
    lancia la suite: e' privato, nel tree pubblico non esiste, e un test che lo leggesse
    sarebbe verde a casa e rosso altrove. Chi prova l'ASSENZA ripunta
    `mandato_pm.PERCORSO_MANDATO` a un path suo (tests/test_mandato_pm.py). Sandbox, non
    tripwire: come (a)."""
    from bellomberg.core import mandato_pm
    monkeypatch.setattr(mandato_pm, "PERCORSO_MANDATO", _mandato_esempio_su_disco)


@pytest.fixture(scope="session")
def _prezzi_esempio_su_disco(tmp_path_factory):
    """05/09 (criterio 1, lotto 6): l'ESEMPIO TRACCIATO dei prezzi speciali copiato UNA volta
    nel tmp della sessione — simboli INVENTATI, nessun simbolo del book di chi lancia."""
    import shutil
    from bellomberg.core.paths import EXAMPLES_DIR
    sorgente = str(EXAMPLES_DIR / "prezzi_speciali.example.json")
    if not os.path.exists(sorgente):
        raise RuntimeError(
            "manca l'esempio tracciato %s: senza di lui questa fixture non puo' redirigere "
            "il negozio dei prezzi speciali e la suite leggerebbe data/ di chi lancia. "
            "Se stai girando su un tree ESPORTATO, il file deve essere in pubblico/ALLOWLIST.txt."
            % sorgente)
    p = str(tmp_path_factory.mktemp("prezzi") / "prezzi_speciali.json")
    shutil.copyfile(sorgente, p)
    return p


@pytest.fixture(autouse=True)
def negozio_prezzi_di_prova(monkeypatch, _prezzi_esempio_su_disco):
    """I sei motori di portafoglio e price_updater leggono i simboli senza yfinance e la mappa
    CoinGecko dal negozio privato: senza questa fixture 11 file di test leggerebbero
    `data/prezzi_speciali.json` di chi lancia la suite — verdi a casa e diversi nel clone.
    Chi prova l'ASSENZA ripunta `negozi_privati.PERCORSO_PREZZI` a un path suo (lo fanno
    tests/test_prezzi_speciali_dichiarati.py e la sezione B7 di tests/test_negozi_privati.py).
    RAGGIO DICHIARATO: solo questa famiglia. Le altre sei (alias, IV, fattori, LEI,
    istituzioni, correzioni) NON sono redirette e chi le esercita si porta la sua fixture,
    come `_negozio_iv` in tests/test_vol_cone.py. Sandbox, non tripwire: come (a)."""
    from bellomberg.storage import negozi_privati
    monkeypatch.setattr(negozi_privati, "PERCORSO_PREZZI", _prezzi_esempio_su_disco)


@pytest.fixture(scope="session")
def _temi_titoli_esempio_su_disco(tmp_path_factory):
    """06/09 (criterio 1, lotto b): l'ESEMPIO TRACCIATO della mappa tema->titoli copiato UNA
    volta nel tmp della sessione — id e simboli INVENTATI, nessun titolo del book di chi
    lancia."""
    import shutil
    from bellomberg.core.paths import EXAMPLES_DIR
    sorgente = str(EXAMPLES_DIR / "news_topics_tickers.example.json")
    if not os.path.exists(sorgente):
        raise RuntimeError(
            "manca l'esempio tracciato %s: senza di lui questa fixture non puo' redirigere "
            "il negozio dei titoli per tema e la suite leggerebbe data/ di chi lancia. "
            "Se stai girando su un tree ESPORTATO, il file deve essere in pubblico/ALLOWLIST.txt."
            % sorgente)
    p = str(tmp_path_factory.mktemp("temi_titoli") / "news_topics_tickers.json")
    shutil.copyfile(sorgente, p)
    return p


@pytest.fixture(autouse=True)
def negozio_temi_titoli_di_prova(monkeypatch, _temi_titoli_esempio_su_disco):
    """`news_aggregator` e `news_topics` risolvono dal negozio privato quali titoli muove un
    tema: senza questa fixture i test leggerebbero `data/news_topics_tickers.json` di chi
    lancia la suite — verdi a casa e diversi nel clone, e con i simboli del book dentro le
    asserzioni. Chi prova l'ASSENZA o un contenuto suo ripunta
    `negozi_privati.PERCORSO_TEMI_TITOLI` (lo fa tests/test_temi_titoli_dichiarati.py).
    RAGGIO DICHIARATO: solo questa famiglia. Sandbox, non tripwire: come (a).
    NOTA: l'esempio usa id di temi INVENTATI, quindi con questa fixture NESSUN tema vero
    riceve titoli — e' voluto: un test che si aspetta un legame se lo scrive lui."""
    from bellomberg.storage import negozi_privati
    monkeypatch.setattr(negozi_privati, "PERCORSO_TEMI_TITOLI", _temi_titoli_esempio_su_disco)


@pytest.fixture(autouse=True)
def preferenze_di_prova(monkeypatch, tmp_path):
    """13/09 (Claude Opus 5): la LINGUA della suite non la decide chi la lancia.
    `capture_language()` (src/bellomberg/core/language.py), senza lingua esplicita e senza un
    contesto attivo, legge `preferences.PREFERENCES_PATH` = `DATA_DIR/preferences.json`
    (src/bellomberg/storage/preferences.py): la preferenza di RUNTIME di chi lancia la suite.
    Lo stesso fa `LanguageMiddleware` (src/bellomberg/api/language_middleware.py) per ogni
    chiamata API senza X-BB-Language. I test che pretendono frasi italiane (tests/test_mandato_pm.py,
    tests/test_mandato_anteprima.py e gli altri) erano quindi verdi perche' oggi quel file dice
    «it»: rossi il giorno che il PM salva l'inglese, PreferenceError o 503 se il file si rompe.
    Qui il file punta a un percorso ASSENTE nel tmp del test: e' lo stato di un clone al primo
    avvio, che `read_preferences` DICHIARA (`source: compatibility_default`) — uguale a casa e
    fuori. Chi prova una preferenza salvata ripunta il path a uno suo, come gia' fanno
    tests/test_api_language.py e tests/test_language_preferences.py: la loro setattr arriva dopo
    e vince, e il monkeypatch ripristina in ordine inverso.
    Due strade scartate apposta:
      - `language_context('it')` autouse: il ContextVar ereditato scavalcherebbe la lettura della
        preferenza salvata, cioe' proprio il percorso che tests/test_api_language.py misura;
      - BELLOMBERG_DATA_DIR: sposterebbe anche `DB_PRODUZIONE` e gli alberi della spia (c), che
        devono guardare i dati VERI.
    RAGGIO DICHIARATO: vale dai fixture di funzione in poi. Collection, fixture di scope
    SUPERIORE (sessione, modulo, classe: es. `_mandato_esempio_su_disco`) e sottoprocessi restano
    fuori: se rendessero frasi leggerebbero ancora il file vero. Oggi quelle fixture lavorano su
    esempi validi e non ne rendono (lettura del codice, non misura). Sandbox, non tripwire: come
    (a). La prova: tests/test_preferenze_lingua_di_prova.py."""
    from bellomberg.storage import preferences
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")


@pytest.fixture(autouse=True)
def ritmo_sec_di_prova(monkeypatch, tmp_path):
    """Fase D: attendi_sec() tiene il ritmo in DATA_DIR/.sec_ritmo (lock tra processi).
    Nella suite il file vive nel tmp del test, mai in data/ (tripwire (c)). Chi prova il
    percorso reale passa `percorso=` o ripunta `_ritmo_path` con una sua setattr."""
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(sec_edgar, "_ritmo_path", lambda: tmp_path / ".sec_ritmo")


@pytest.fixture(autouse=True)
def ritmo_e_indice_esef_di_prova(monkeypatch, tmp_path):
    """Fase B: ritmo filings.xbrl.org (DATA_DIR/.esef_ritmo) e cache dell'indice depositi
    (DATA_DIR/esef_cache) nel tmp del test, mai in data/."""
    from bellomberg.market_data import esef
    monkeypatch.setattr(esef, "_ritmo_path", lambda: tmp_path / ".esef_ritmo")
    monkeypatch.setattr(esef, "_cache_indice_dir", lambda: tmp_path / "esef_cache")


# ============================================================================
# (c) SPIA DELLE SCRITTURE SU FILE — TRIPWIRE (22/08 sera-2, voce (1b) del
#     MASTER). Due fasi, entrambe con l'ok del PM:
#     FASE A «misura, poi il numero»: modalita' REGISTRA, la suite intera ha
#       dato «0 scritture fuori dal tmp su 840 test» (changelog (82));
#     FASE B «si', accendi»: col numero in mano, il test che scrive FALLISCE.
#
# Sorveglia ogni apertura in scrittura (builtins.open E io.open: nome diverso,
# e' la via di pathlib/zip/market_inputs), makedirs/mkdir, replace/rename e le
# CANCELLAZIONI sotto data/ (junction -> C:\BellombergData: confronto sul
# REALPATH), report/, research_notes/ e su portfolio.json in radice — escluso
# il tmp di sistema, dove vive anche il tmp_path di pytest. Si solleva PRIMA
# che l'originale scriva (il file non nasce), con BaseException (passa gli
# `except Exception: pass`), e il messaggio dice file, test e cura. Il
# rapporto a fine sessione resta, col limite dichiarato (os.open a basso
# livello, scritture da C: lo sqlite ha il tripwire (b)).
#
# La leva per tornare alla sola MISURA — es. per capire cosa scrive un test
# nuovo senza fermarlo: `BELLOMBERG_SPIA_SCRITTURE=registra`. Natura: (a)
# sandbox, (b) tripwire DB, (c) tripwire file — che chiude la CLASSE invece
# di un presidio per costante (lezione 21/08).
# La spia e' `tests/_spia_scritture.py`, collaudata da
# `tests/test_spia_scritture.py` (piu' i due canarini che provano, DENTRO la
# suite, che e' installata, attribuisce al test giusto e sta mordendo).
# ============================================================================
import tempfile as _tempfile

_QUI = os.path.dirname(os.path.abspath(__file__))
if _QUI not in sys.path:
    sys.path.insert(0, _QUI)
from _spia_scritture import SpiaScritture as _SpiaScritture  # noqa: E402

_SPIA_MODALITA = os.environ.get("BELLOMBERG_SPIA_SCRITTURE", "tripwire")
_SPIA = _SpiaScritture(radice=ROOT, esenti=[_tempfile.gettempdir()],
                       tripwire=(_SPIA_MODALITA != "registra"),
                       # B4: anche la cartella dati spostata. SIGILLO 06/10: DB_DIR ora e' il
                       # tmp di sessione (esente); si sorvegliano le cartelle VERE
                       alberi_extra=list(CARTELLE_VERE))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", _sigillo_dati.MARKER + ": il test LEGGE apposta la cartella dati vera "
        "(sqlite solo con mode=ro/immutable=1, file in sola lettura). Scrivere resta vietato.")


def pytest_sessionstart(session):
    _SPIA.installa()


def pytest_sessionfinish(session, exitstatus):
    _SPIA.disinstalla()
    # violazioni del sigillo FUORI da un test (import, collection, thread rimasti vivi dopo
    # l'ultimo test): nessun teardown le ha fatte cadere, le fa cadere la sessione
    if any(v["test"].startswith("<") for v in _SIGILLO.violazioni) and _SIGILLO.tripwire:
        session.exitstatus = 1
    # la cartella di sessione creata QUI si toglie (quella ereditata la toglie il padre).
    # ignore_errors: un thread ancora vivo puo' tenere un file aperto, e resta solo un tmp
    if not _DATI["ereditata"]:
        import shutil
        shutil.rmtree(CARTELLA_DATI_SESSIONE, ignore_errors=True)


@pytest.fixture(autouse=True)
def _spia_sa_chi_scrive(request):
    """Il rapporto deve dire CHI ha scritto: il nodeid del test in corso. Fuori
    da un test (import, collection) la spia etichetta da sola.
    SIGILLO 06/10 (punto 3): la spia solleva PRIMA della scrittura, ma se la scrittura parte
    da un THREAD (worker del lifespan) l'eccezione muore nel thread e pytest la riduce a un
    warning: il test passava. Ora ogni scrittura registrata a nome di questo test, in
    qualunque thread, lo fa FALLIRE in teardown. Stesso controllo per il sigillo."""
    nodeid = request.node.nodeid
    _SPIA.test_corrente = nodeid
    _SIGILLO.test_corrente = nodeid
    _SIGILLO.lettura_consentita = request.node.get_closest_marker(_sigillo_dati.MARKER) is not None
    da_spia, da_sigillo = len(_SPIA.scritture), len(_SIGILLO.violazioni)
    yield
    _SPIA.test_corrente = None
    _SIGILLO.test_corrente = None
    _SIGILLO.lettura_consentita = False
    scritte = [ev for ev in _SPIA.scritture[da_spia:] if ev["test"] == nodeid]
    toccate = _SIGILLO.violazioni_di(nodeid, da_sigillo)
    if _SPIA.tripwire and scritte:
        pytest.fail("SPIA: il test ha provato a scrivere in produzione (anche da un thread): %s"
                    % sorted({"%s %s" % (ev["op"], ev["percorso"]) for ev in scritte}))
    if _SIGILLO.tripwire and toccate:
        pytest.fail("SIGILLO: il test ha toccato la cartella dati VERA: %s"
                    % sorted({"%s %s (%s, thread %s)" % (v["op"], v["percorso"],
                              "lettura" if v["lettura"] else "SCRITTURA", v["thread"])
                              for v in toccate}))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    terminalreporter.section(
        "SPIA SCRITTURE — modalita' %s" % (
            "TRIPWIRE (un test che scrive in produzione FALLISCE)"
            if _SPIA.tripwire else
            "REGISTRA (solo misura: BELLOMBERG_SPIA_SCRITTURE=registra)"))
    for riga in _SPIA.rapporto().splitlines():
        terminalreporter.write_line(riga)
    terminalreporter.section("SIGILLO DATI VERI")
    for riga in _SIGILLO.rapporto().splitlines():
        terminalreporter.write_line(riga)


@pytest.fixture(autouse=True)
def cache_fonti_vuote():
    """Le cache di processo delle fonti (trimestrali Finnhub 6 h, yfinance 12 h) non
    passano da un test all'altro: ognuno vede solo il proprio trasporto finto."""
    from bellomberg.market_data import earnings_yf, finnhub_news
    finnhub_news.svuota_cache_earnings()
    earnings_yf.svuota_cache()
    yield
    finnhub_news.svuota_cache_earnings()
    earnings_yf.svuota_cache()


@pytest.fixture(autouse=True)
def cache_proposte_ai_di_prova(monkeypatch, tmp_path):
    """Fase C: le proposte AI dei profili IR (DATA_DIR/filing_ai) nel tmp del test, mai in data/."""
    from bellomberg.market_data import filing_proposta_ai
    monkeypatch.setattr(filing_proposta_ai, "_cache_dir", lambda: tmp_path / "filing_ai")


@pytest.fixture(autouse=True)
def acquisizione_fund_fuori_dalla_suite(monkeypatch):
    """V0-RETE (06/10/2026, Opus 5.5): ogni `with TestClient(api.app)` esegue il lifespan, che
    avvia FundMarketWorker su SQLITE_PATH/DB_DIR di PRODUZIONE (bellomberg_api, dal 03/10). Il
    primo giro legge il book vero (`_universe`, sqlite3 diretto: fuori dal tripwire (b)) e chiama
    yf.Ticker per ogni ticker con quotazione in cache scaduta (>15'), in un thread: rete vera nei
    test, e un tentativo di scrittura in data/consensus_cache (fermato dalla spia (c) ma solo come
    warning, perche' avviene in un thread). Verde o rosso secondo l'eta' della cache vera: i
    test delle notizie cadevano in teardown con ['yfinance.Ticker'].
    Sandbox, come (a): il giro restituisce uno stato DICHIARATO (unavailable + motivo), mai un
    «completato» zitto. Chi prova il worker (tests/test_fund_market_worker.py) ripatcha lo stesso
    nome dentro il test: la sua setattr vince. La prova: tests/test_fund_worker_fuori_dalla_suite.py."""
    from bellomberg.market_data import fund_market_worker

    def _giro_dichiarato(db_path, *, cache_dir, now=None, stop_event=None):
        return {"contract": "fund-market-refresh/1", "status": "unavailable",
                "error": "fund_market_refresh_disabled_in_test_suite", "universe_count": None,
                "counts": {}, "errors": [{"stage": "test_suite",
                                          "error": "fund_market_refresh_disabled_in_test_suite"}],
                "notices": []}

    monkeypatch.setattr(fund_market_worker, "refresh_followed_market_data", _giro_dichiarato)


@pytest.fixture(autouse=True)
def niente_rete_freschezza(monkeypatch, tmp_path):
    """TRIPWIRE di rete (FRESCHEZZA, 06/10/2026): la cascata dell'ultimo periodo (SEC, EDGAR, yfinance) nei
    test risponde ConnectionError dichiarato; chi la prova passa `fonti=` finte esplicite. R-CASCATA: la
    cache su disco e il budget sono del SINGOLO test (prima la cache attraversava i test)."""
    from bellomberg.market_data import freschezza_trimestrale as _ft
    monkeypatch.setattr(_ft, "_cartella", lambda: str(tmp_path / "freschezza_cache"))
    monkeypatch.setattr(_ft, "_BUDGET", {"lock": __import__("threading").Lock(), "per_titolo": {}, "spesa": []})
    monkeypatch.setattr(_ft, "_SCADUTI", set())

    class _FontiGiu:
        def __getattr__(self, nome):
            def _giu(*a, **k):
                raise ConnectionError("rete nei test: cascata di freschezza senza fonti finte (%s)" % nome)
            return _giu
    monkeypatch.setattr(_ft, "FontiVive", _FontiGiu)
    yield
    # R-CASCATA N4: nessun preriscaldamento sopravvive al test (dopo il teardown FontiVive e' quello vero)
    _ft.ferma_preriscaldamenti(attesa_s=5)
