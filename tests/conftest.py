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

# In CI non c'e' .env: una chiave fittizia basta perche' NESSUN test deve
# mai creare un client Anthropic vero (setdefault: quella vera non si tocca).
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-offline-test-suite")
# 02/09 (pubblicazione B2): SEC_CONTACT_EMAIL e' letta A OGNI CHIAMATA da
# sec_edgar._headers()/esef._headers(); senza, sollevano ContattoMancante PRIMA
# della richiesta e i test che simulano la rete non arrivano al mock. Un
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
              "CONSIGLIERE_EVENTDESK_MODEL"):
    os.environ.setdefault(_nome, "")


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
# catturato QUI prima di ogni fixture — non piu' un join su ROOT
DB_PRODUZIONE = _memory_db.SQLITE_PATH

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

    # (a-quinquies) la cache in-process dell'edge scanner (22/08 sera, decisione
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
                       alberi_extra=[_memory_db.DB_DIR])   # B4: anche la cartella dati spostata


def pytest_sessionstart(session):
    _SPIA.installa()


def pytest_sessionfinish(session, exitstatus):
    _SPIA.disinstalla()


@pytest.fixture(autouse=True)
def _spia_sa_chi_scrive(request):
    """Il rapporto deve dire CHI ha scritto: il nodeid del test in corso. Fuori
    da un test (import, collection) la spia etichetta da sola."""
    _SPIA.test_corrente = request.node.nodeid
    yield
    _SPIA.test_corrente = None


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    terminalreporter.section(
        "SPIA SCRITTURE — modalita' %s" % (
            "TRIPWIRE (un test che scrive in produzione FALLISCE)"
            if _SPIA.tripwire else
            "REGISTRA (solo misura: BELLOMBERG_SPIA_SCRITTURE=registra)"))
    for riga in _SPIA.rapporto().splitlines():
        terminalreporter.write_line(riga)
