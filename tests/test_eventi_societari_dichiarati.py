"""Voce E (dossier audit/25 §E): `get_corporate_events_for_ticker` non
interrogava MAI il ticker che gli chiedevi.

Il difetto, misurato il 21-22/08 sul codice vero:
- il dispatcher chiamava `fetch_corporate_events(days, max_items)` SENZA il
  ticker e filtrava dopo in Python (chat_tools.py:1443-1459);
- `news_aggregator.fetch_corporate_events` interroga SEC solo sui ticker US del
  PORTAFOGLIO corrente, quindi un nome fuori da quella lista non veniva mai
  chiesto a nessuno;
- meta' del payload veniva buttata dal filtro stesso: gli item NewsAPI non
  hanno MAI la chiave `ticker_mentioned` su cui il filtro confronta;
- la cache aveva per chiave `corp_events:{days}:{max_items}` — SENZA il ticker:
  dalla seconda chiamata in poi si filtrava la lista costruita per un altro nome;
- Finnhub non era mai contattato (`grep -c -i finnhub news_aggregator.py` = 0)
  benche' la risposta fosse FIRMATA `_source: "SEC EDGAR + Finnhub ..."`.
Risultato: `count: 0` firmato come misura. Sui 37 log veri di
`data/consigliere_run.log`, 23 chiamate su 37 potevano solo restituire zero.

I test qui asseriscono cio' che l'AGENTE legge (regola 14/07: un buco si
DICHIARA), non la forma interna del codice. Riproducono gli input veri della
funzione — il dispatcher vero, non un'approssimazione (lezione 21/08 (i)).
"""
import re

import pytest

from bellomberg.agents import chat_tools
from bellomberg.market_data import finnhub_news
from bellomberg.market_data import sec_edgar

# Le funzioni VERE, catturate all'import: la fixture autouse qui sotto le
# sostituisce per tenere la suite offline, ma i test UNITARI sui motivi devono
# misurare il codice di produzione, non lo stub (altrimenti asserirebbero il
# comportamento della mia fixture — un test verde che non prova nulla).
_API_GET_VERO = finnhub_news._api_get
_LOOKUP_CIK_VERO = sec_edgar.lookup_cik


# ---------------------------------------------------------------- helper veri

def _chiama(ticker="SIGMA.MI", days=30):
    return chat_tools.dispatch("get_corporate_events_for_ticker",
                               {"ticker": ticker, "days": days})


def _dati(risposta):
    """Il dispatcher avvolge tutto in _stamp: {_source, _timestamp, data}."""
    assert "error" not in risposta, risposta.get("error")
    return risposta["data"]


@pytest.fixture(autouse=True)
def niente_rete(monkeypatch):
    """Nessuna delle due fonti deve uscire in rete. Ogni test che vuole una
    fonte VIVA la riaccende da se'."""
    def _api_spenta(path, params=None, timeout=12, motivo=None, **k):
        # il vero `_api_get` NON torna mai None senza depositare il motivo: uno
        # stub che lo facesse misurerebbe la mia finzione, non il codice.
        if motivo is not None:
            motivo.append("rete spenta dal test")
        return None

    def _cik_spento(ticker, motivo=None, **k):
        if motivo is not None:
            motivo.append("rete spenta dal test")
        return None

    monkeypatch.setattr(finnhub_news, "_api_get", _api_spenta)
    monkeypatch.setattr(sec_edgar, "get_corporate_events_for_portfolio",
                        lambda tickers, days=14, **k: [])
    monkeypatch.setattr(sec_edgar, "lookup_cik", _cik_spento)


# ------------------------------------------------- 1. interrogare il ticker

def test_il_ticker_chiesto_arriva_davvero_alla_fonte_sec(monkeypatch):
    """Il difetto centrale: SEC riceveva i ticker US del PORTAFOGLIO, mai
    quello chiesto dall'agente."""
    visti = []

    def _recorder(tickers, days=14, **k):
        visti.append(list(tickers))
        return []

    monkeypatch.setattr(sec_edgar, "get_corporate_events_for_portfolio", _recorder)
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")

    _chiama(ticker="ALFA")

    assert visti, "SEC EDGAR non e' stata interrogata affatto"
    assert visti[0] == ["ALFA"], (
        "SEC EDGAR interrogata su %r invece che sul ticker chiesto: e' il "
        "difetto E — il tool chiedeva gli eventi del PORTAFOGLIO e poi filtrava"
        % (visti[0],))


def test_il_ticker_chiesto_arriva_davvero_a_finnhub(monkeypatch):
    """Finnhub non era mai contattato, benche' la firma lo nominasse."""
    visti = []

    def _recorder(path, params=None, timeout=12, **k):
        visti.append((path, dict(params or {})))
        return []

    monkeypatch.setattr(finnhub_news, "_api_get", _recorder)

    _chiama(ticker="ALFA")

    assert visti, "Finnhub non e' stato contattato: la firma lo nomina da mesi"
    simboli = {p.get("symbol") for _, p in visti}
    assert "ALFA" in simboli, (
        "Finnhub interrogato coi simboli %r, non col ticker chiesto" % (simboli,))


def test_il_nome_europeo_viene_interrogato_su_finnhub_col_simbolo_normalizzato(monkeypatch):
    """I 17 nomi non-US delle 37 chiamate vere non sono filer SEC: Finnhub e'
    l'unica fonte che possa dire qualcosa, e finnhub_news._norm_ticker sa gia'
    tradurre i suffissi europei."""
    visti = []
    monkeypatch.setattr(finnhub_news, "_api_get",
                        lambda path, params=None, timeout=12, **k:
                            visti.append(dict(params or {})) or [])

    _chiama(ticker="BETA.DE")

    simboli = {p.get("symbol") for p in visti}
    assert simboli, "Finnhub non interrogato su un nome europeo"
    assert "BETA" in simboli, (
        "atteso il simbolo normalizzato BETA (via _norm_ticker), visti %r" % (simboli,))


# --------------------------------------- 2. lo zero smette di essere firmato

def test_zero_con_fonti_mute_e_dichiarato_NON_misurato():
    """Il danno vero di E: `count: 0` indistinguibile da una misura. Con
    entrambe le fonti mute il payload deve dirlo a parole."""
    d = _dati(_chiama(ticker="SIGMA.MI"))

    assert d["count"] == 0
    avviso = d.get("avviso", "")
    assert "ZERO NON MISURATO" in avviso, (
        "count:0 con TUTTE le fonti mute e nessun avviso: e' esattamente lo "
        "zero falso del difetto E. avviso=%r" % (avviso,))


def test_la_firma_non_nomina_una_fonte_che_non_ha_risposto():
    """`_source: "SEC EDGAR + Finnhub corporate events"` era un letterale:
    firmava Finnhub anche quando Finnhub non era stato contattato."""
    r = _chiama(ticker="SIGMA.MI")
    firma = r["_source"]

    assert not re.search(r"SEC EDGAR \+ Finnhub", firma), (
        "la firma e' ancora il letterale che nomina due fonti a prescindere: %r"
        % (firma,))
    # e non basta: dev'essere DERIVATA da chi ha risposto. Qui non ha risposto
    # nessuno, quindi non puo' esserci nessun nome dopo "interrogate:".
    interrogate = firma.split("interrogate:", 1)[1].split(";", 1)[0]
    assert "finnhub" not in interrogate and "sec_edgar" not in interrogate, (
        "la firma dichiara interrogate fonti che sono MUTE: %r" % (firma,))
    assert "nessuna" in interrogate


def test_ogni_fonte_dichiara_se_e_stata_interrogata_e_con_quale_esito(monkeypatch):
    """Serve all'agente per sapere se il silenzio e' quiete o cecita'."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [{"ticker": tickers[0], "type": "8-K",
                                    "title": "x", "snippet": "s",
                                    "date": "2026-08-20", "url": "u",
                                    "importance": 4}])

    d = _dati(_chiama(ticker="ALFA"))
    fonti = d.get("fonti", {})

    assert "sec_edgar" in fonti and "finnhub" in fonti, (
        "il payload non dichiara le fonti una per una: %r" % (fonti,))
    assert "interrogata" in fonti["sec_edgar"].lower()
    assert "muta" in fonti["finnhub"].lower(), (
        "Finnhub e' morto (nessuna chiave) e il payload non lo dice: %r"
        % (fonti["finnhub"],))


def test_chiave_morta_e_zero_eventi_non_producono_la_stessa_risposta(monkeypatch):
    """La distinzione che oggi non esiste in finnhub_news: `_api_get` torna
    None su 401/429/chiave assente, e ogni funzione pubblica lo traduce in
    lista vuota. Per l'agente 'nessun evento' e 'fonte morta' erano identici."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")

    monkeypatch.setattr(finnhub_news, "_api_get", _API_GET_VERO)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: None)   # chiave morta
    morta = _dati(_chiama(ticker="ALFA"))["fonti"]["finnhub"]

    # forma VERA dell'endpoint: /company-news risponde una LISTA (dal 25/08,
    # voce C, e' l'unica chiamata Finnhub del tool; il ramo dict del finto e'
    # morto e resta solo per pigrizia innocua — un test lo garantisce:
    # `simboli == {"/company-news": ...}`).
    monkeypatch.setattr(finnhub_news, "_api_get",
                        lambda path, *a, **k: {} if "press-releases" in path else [])
    vuota = _dati(_chiama(ticker="ALFA"))["fonti"]["finnhub"]

    assert morta != vuota, (
        "fonte morta e fonte che risponde 'zero eventi' danno la stessa "
        "stringa %r: il ripiego resta muto" % (morta,))
    assert "muta" in morta.lower()
    assert "interrogata" in vuota.lower()


def test_chiedere_un_ticker_non_apre_il_portafoglio(monkeypatch):
    """Reso esplicito cio' che il presidio del conftest ha rivelato guardando
    fallire questi test: oggi chiedere gli eventi di UN ticker costruisce una
    MemoryDB e legge il PORTAFOGLIO, perche' la lista dei nomi da interrogare
    veniva da li'. Dopo la cura la fonte e' il ticker chiesto, e il DB non
    c'entra piu' nulla: se qualcuno ri-instrada il tool su
    news_aggregator.fetch_corporate_events, questo test lo prende."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")

    # nessun ProduzioneToccata: il presidio (b) di conftest sorveglia
    # memory_db.connect_sqlite e solleva se si apre il DB vero.
    r = _chiama(ticker="ALFA")

    assert "error" not in r, (
        "il tool e' andato in errore invece di rispondere: %r" % (r.get("error"),))


def test_nessuna_chiamata_a_press_releases(monkeypatch):
    """Voce C 25/08 (prima meta' di §9-quaterquadragies): il piano Finnhub e'
    GRATUITO (confermato dal PM il 22/08) e `/press-releases` risponde 403 PER
    SEMPRE — misurato di nuovo oggi con `prova_eventi_societari.py --vero`:
    3 invocazioni su 3, una HTTP sprecata l'una, e il rumore del 403 nel campo
    `fonti` spingeva fuori vista il caveat del simbolo ricostruito. Il finto
    qui ESPLODE se l'endpoint viene toccato: e' la misura della cura (zero
    HTTP sprecate), non solo l'assenza del sintomo."""
    def _api(path, params=None, timeout=12, **k):
        # L'AssertionError viene inghiottito dall'except di produzione e
        # diventa una fonte MUTA: a catturare e' l'uguaglianza stretta
        # sull'esito qui sotto, l'esplosione da sola non basterebbe.
        assert "press-releases" not in path, (
            "il tool ha chiamato /press-releases: e' la HTTP sprecata che la "
            "voce C ha tolto (403 per sempre sul piano gratuito)")
        return []

    monkeypatch.setattr(finnhub_news, "_api_get", _api)
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")

    d = _dati(_chiama(ticker="ALFA"))
    assert d["fonti"]["finnhub"] == "interrogata: 0 eventi", (
        "una fonte sana non e' piu' PARZIALE ne' porta il rumore del 403: %r"
        % (d["fonti"]["finnhub"],))
    # e la description non deve piu' PROMETTERE cio' che il piano non da'.
    # Nominarle per NEGARLE va bene (buco dichiarato, regola 14/07): un agente
    # che non le trova deve sapere che non arriveranno, non sospettare un guasto.
    desc = [t for t in chat_tools.TOOL_DEFINITIONS
            if t["name"] == "get_corporate_events_for_ticker"][0]["description"]
    assert "se il piano Finnhub le comprende" not in desc, (
        "la vecchia promessa condizionale e' ancora nella description")
    if "press release" in desc.lower():
        assert "NON sono nel piano dati" in desc, (
            "la description nomina le press release senza negarle: %r" % (desc,))


def test_il_payload_pieno_resta_sotto_il_tetto_dei_tool_result(monkeypatch):
    """Se sforasse TETTO_TOOL_RESULT verrebbe tagliato a valle da
    specialists/base.py con un marcatore senza numeri: un taglio muto nuovo,
    che e' esattamente cio' che il PM ha vietato. Misurato, non supposto."""
    import json

    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K", "title": "T" * 300,
             "snippet": "S" * 300, "date": "2026-08-20",
             "url": "https://www.sec.gov/" + "u" * 200, "importance": 4}
            for _ in range(40)])

    r = _chiama(ticker="ALFA")
    peso = len(json.dumps(r, ensure_ascii=False, default=str))
    assert peso <= chat_tools.TETTO_TOOL_RESULT, (
        "il payload pesa %d contro un tetto di %d: verrebbe tagliato a valle "
        "senza dichiararlo" % (peso, chat_tools.TETTO_TOOL_RESULT))


# ------------------------------------------- 3. i motivi, non solo il fatto

def test_finnhub_dice_PERCHE_e_vuoto(monkeypatch):
    """Unita' su finnhub_news: oggi il motivo finisce solo su stdout."""
    monkeypatch.setattr(finnhub_news, "_api_get", _API_GET_VERO)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: None)
    motivo = []

    out = finnhub_news.fetch_company_news("ALFA", days=7, motivo=motivo)

    assert out == []
    assert motivo, "lista vuota senza motivo: il chiamante non puo' distinguere"
    assert "FINNHUB_API_KEY" in " ".join(motivo)


def test_lookup_cik_dice_PERCHE_non_ha_trovato_il_cik(monkeypatch):
    """Unita' su sec_edgar: None significa tre cose diverse (non e' un filer /
    HTTP != 200 / eccezione). Scrivere 'non e' un filer SEC' senza saperlo
    sarebbe inventare una misura — la malattia che stiamo curando."""
    class _R:
        status_code = 200

        @staticmethod
        def json():
            return {"0": {"ticker": "ALFA", "cik_str": 1234567}}

    monkeypatch.setattr(sec_edgar, "lookup_cik", _LOOKUP_CIK_VERO)
    sec_edgar._CIK_CACHE.pop("SIGMA", None)
    monkeypatch.setattr(sec_edgar.requests, "get", lambda *a, **k: _R())

    motivo = []
    assert sec_edgar.lookup_cik("SIGMA.MI", motivo=motivo) is None
    testo = " ".join(motivo).lower()
    assert "filer" in testo or "elenco" in testo, (
        "il motivo non distingue 'assente dall'elenco SEC' dagli altri due "
        "casi: %r" % (motivo,))


def test_rete_giu_non_viene_scambiata_per_non_e_un_filer(monkeypatch):
    """Il rovescio del test precedente, ed e' quello che conta: un HTTP che
    non risponde NON autorizza a dire che l'emittente non deposita alla SEC."""
    class _R:
        status_code = 503

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(sec_edgar, "lookup_cik", _LOOKUP_CIK_VERO)
    sec_edgar._CIK_CACHE.pop("ALFA", None)
    monkeypatch.setattr(sec_edgar.requests, "get", lambda *a, **k: _R())

    motivo = []
    assert sec_edgar.lookup_cik("ALFA", motivo=motivo) is None
    testo = " ".join(motivo).lower()
    assert "503" in testo or "http" in testo
    assert "non risulta" not in testo, (
        "una rete giu' viene dichiarata come 'non e' un filer SEC': %r" % (motivo,))


# --------------------------------------------- 4. il taglio a 20, dichiarato

def test_il_taglio_a_20_eventi_si_dichiara_coi_numeri(monkeypatch):
    """Terzo taglio muto dentro le stesse 17 righe: `count` diceva 35 e
    `events` ne portava 20, senza una parola."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K", "title": "evento %d" % i,
             "snippet": "s", "date": "2026-08-%02d" % (1 + i % 28), "url": "u",
             "importance": 4}
            for i in range(35)])

    d = _dati(_chiama(ticker="ALFA"))

    assert d["count"] == 35
    assert len(d["events"]) == 20
    assert "mostrati" in d, "manca il conteggio di cio' che e' davvero nel payload"
    assert "taglio" in d, (
        "15 eventi su 35 restano fuori e il payload non lo dichiara: %r"
        % (sorted(d),))
    taglio = d["taglio"]
    assert "35" in taglio and "20" in taglio and "15" in taglio, (
        "la dichiarazione del taglio non porta i tre numeri (trovati, mostrati, "
        "esclusi): %r" % (taglio,))
    assert "VECCHI" in taglio.upper(), (
        "il taglio non dice QUALI eventi restano fuori: %r" % (taglio,))


# ------------------------------------- 5. il letterale non deve tornare mai

def test_nessun_letterale_firma_due_fonti_a_prescindere():
    """Guardia di non-regressione sulla CLASSE del difetto: una firma che
    nomina le fonti prima di sapere se hanno risposto."""
    with open(chat_tools.__file__, encoding="utf-8") as f:
        righe = f.read().splitlines()
    # via i commenti: la guardia riguarda il codice, non la prosa che spiega il
    # difetto (questo file e il changelog devono poterlo citare per nome).
    codice = "\n".join(r for r in righe if not r.strip().startswith("#"))
    assert "SEC EDGAR + Finnhub corporate events" not in codice, (
        "il letterale che firmava lo zero falso e' tornato in chat_tools.py")

def test_zero_con_TUTTE_le_fonti_vive_e_dichiarato_MISURATO(monkeypatch):
    """Il rovescio, ed e' la frase che vale di piu': se entrambe le fonti hanno
    risposto e non c'e' nulla, quello zero E' una misura — e l'agente deve
    poterlo sapere, altrimenti tratta ogni zero come sospetto."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(finnhub_news, "_api_get",
                        lambda path, *a, **k: {} if "press-releases" in path else [])

    d = _dati(_chiama(ticker="ALFA"))

    assert d["count"] == 0
    avviso = d.get("avviso", "")
    assert "MISURATO" in avviso and "ZERO NON MISURATO" not in avviso, (
        "zero con tutte le fonti vive e nessuna dichiarazione che sia una "
        "misura: %r" % (avviso,))


def test_la_description_del_tool_non_promette_un_fallback_che_non_c_e_piu():
    """La description e' l'unica cosa che l'agente legge PRIMA di chiamare.
    Diceva «Usa Finnhub se disponibile, fallback SEC EDGAR»: oggi le fonti sono
    interrogate ENTRAMBE e unite, non e' piu' un fallback."""
    d = [t for t in chat_tools.TOOL_DEFINITIONS
         if t["name"] == "get_corporate_events_for_ticker"][0]["description"]
    assert "fallback" not in d.lower(), (
        "la description promette ancora un fallback: %r" % (d,))


def test_la_chiamata_finnhub_caduta_si_dichiara_col_motivo(monkeypatch):
    """Dalla voce C (25/08) Finnhub e' UNA chiamata (company-news): se cade
    (429), la fonte e' MUTA — e il motivo deve arrivare all'agente, non un
    silenzio ne' un generico. (Prima erano DUE chiamate e questo stato usciva
    «PARZIALMENTE»: quel verdetto ora e' riservato a chi ha davvero risposto
    a meta' — oggi, nessuno.)"""
    class _R429:
        status_code = 429
        text = ""

    monkeypatch.setattr(finnhub_news, "_api_get", _API_GET_VERO)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: "chiave-finta")
    monkeypatch.setattr(finnhub_news, "requests",
                        type("R", (), {"get": staticmethod(
                            lambda url, **k: _R429())}))
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")

    esito = _dati(_chiama(ticker="ALFA"))["fonti"]["finnhub"]

    assert esito.startswith("MUTA"), (
        "l'unica chiamata Finnhub e' caduta e la fonte non e' dichiarata "
        "MUTA: %r" % (esito,))
    assert "429" in esito, (
        "il motivo della chiamata caduta non arriva all'agente: %r" % (esito,))


def test_un_nome_non_filer_non_fa_interrogare_la_sec_a_vuoto(monkeypatch):
    """Sui 17 nomi non-US delle 37 chiamate vere il CIK non esiste: chiamare
    comunque SEC costa due HTTP e 0,4s di sleep per nulla, a ogni chiamata.
    Il CIK si risolve PRIMA proprio per questo."""
    chiamate = []
    monkeypatch.setattr(sec_edgar, "get_corporate_events_for_portfolio",
                        lambda tickers, days=14, **k: chiamate.append(list(tickers)) or [])
    monkeypatch.setattr(sec_edgar, "lookup_cik",
                        lambda t, motivo=None, **k: (
                            motivo.append("%s assente dall'elenco dei filer SEC"
                                          % t) if motivo is not None else None)
                        or None)

    d = _dati(_chiama(ticker="ZETA"))

    assert chiamate == [], (
        "SEC interrogata su un nome senza CIK: %r" % (chiamate,))
    assert "filer" in d["fonti"]["sec_edgar"], (
        "e il motivo non arriva all'agente: %r" % (d["fonti"]["sec_edgar"],))


# ------------------------------------- 6. difetti trovati sulle API VERE

def test_fonte_sana_a_zero_eventi_e_una_misura_pulita(monkeypatch):
    """L'erede del caso SIGMA.MI del 22/08 (403 su /press-releases + 200 con
    zero notizie → «interrogata PARZIALMENTE» col rumore del 403 in coda):
    dalla voce C quella HTTP non parte piu', quindi la FONTE finnhub esce come
    zero misurato pulito — niente MUTA, niente PARZIALE, niente 403 nel suo
    verdetto. La SEC su SIGMA.MI resta «NON INTERROGATA (apposta)» (suffisso →
    guardia omonimi; scatta PRIMA di lookup_cik, che qui non serve stubbare) e
    percio' l'AVVISO aggregato dice onestamente «zero PARZIALMENTE misurato»:
    la quiete e' accertata solo per chi ha risposto. Il finto sul trasporto
    ESPLODE se l'endpoint viene toccato — l'AssertionError viene inghiottito
    dall'except di produzione e diventa una fonte MUTA: a catturare la
    regressione e' l'asserzione stretta sull'esito, non l'esplosione."""
    class _Rvuota:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return []      # 200 con zero item: una MISURA

    def _get(url, **k):
        assert "press-releases" not in url, (
            "una HTTP verso /press-releases e' partita dal trasporto vero")
        return _Rvuota()

    monkeypatch.setattr(finnhub_news, "_api_get", _API_GET_VERO)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: "chiave-finta")
    monkeypatch.setattr(finnhub_news, "requests",
                        type("R", (), {"get": staticmethod(_get)}))

    d = _dati(_chiama(ticker="SIGMA.MI"))
    esito = d["fonti"]["finnhub"]

    assert esito.startswith("interrogata"), (
        "Finnhub ha risposto (200, zero notizie): l'esito atteso e' una "
        "misura pulita, non %r" % (esito,))
    assert "PARZIALMENTE" not in esito and "403" not in esito, (
        "il rumore delle press release e' ancora nel verdetto: %r" % (esito,))
    avviso = d.get("avviso", "")
    assert "ZERO NON MISURATO" not in avviso, (
        "l'avviso dichiara che nessuna fonte ha risposto, ma company-news ha "
        "risposto: %r" % (avviso,))
    assert "PARZIALMENTE misurato" in avviso, (
        "manca la meta' onesta dell'aggregato: da sec_edgar (NON INTERROGATA "
        "apposta) non c'e' misura, e l'avviso deve dirlo: %r" % (avviso,))


def test_zero_non_misurato_solo_se_NESSUNA_chiamata_ha_risposto(monkeypatch):
    """Il confine dell'affermazione precedente: quando davvero nessuna
    chiamata risponde, la frase forte deve restare."""
    class _R500:
        status_code = 500
        text = ""

    monkeypatch.setattr(finnhub_news, "_api_get", _API_GET_VERO)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: "chiave-finta")
    monkeypatch.setattr(finnhub_news, "requests",
                        type("R", (), {"get": staticmethod(lambda url, **k: _R500())}))

    d = _dati(_chiama(ticker="SIGMA.MI"))

    assert "ZERO NON MISURATO" in d.get("avviso", ""), (
        "nessuna chiamata ha risposto e l'avviso non lo dice: %r"
        % (d.get("avviso"),))


def test_un_simbolo_TRADOTTO_non_spaccia_lo_zero_per_quiete_accertata(monkeypatch):
    """Su un nome non-US il simbolo Finnhub e' RICOSTRUITO da `_norm_ticker`
    (BETA.DE -> BETA) togliendo il suffisso: gli alias VERIFICATI stanno nel negozio privato.
    Se il simbolo ricostruito non esiste, Finnhub risponde 200 con lista vuota:
    indistinguibile da «nessuna notizia». Dire «quiete accertata» li' sarebbe
    lo stesso difetto E, un livello piu' in basso — quindi la traduzione va
    DICHIARATA, che e' l'unica cosa misurabile senza una chiamata in piu'."""
    monkeypatch.setattr(finnhub_news, "_api_get",
                        lambda path, *a, **k: {} if "press-releases" in path else [])
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: None)

    esito = _dati(_chiama(ticker="BETA.DE"))["fonti"]["finnhub"]

    assert "BETA" in esito, (
        "il simbolo con cui Finnhub e' stato davvero interrogato non e' "
        "dichiarato: %r" % (esito,))
    assert "tradotto" in esito.lower() or "ricostruito" in esito.lower(), (
        "la traduzione del simbolo non e' dichiarata: uno zero su un simbolo "
        "inesistente sembrerebbe una misura. %r" % (esito,))


def test_un_ticker_US_non_porta_la_dichiarazione_di_traduzione(monkeypatch):
    """Il confine: su ALFA non c'e' nessuna traduzione, e aggiungere un caveat
    che non serve e' rumore che svaluta quelli veri."""
    monkeypatch.setattr(finnhub_news, "_api_get",
                        lambda path, *a, **k: {} if "press-releases" in path else [])
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: None)

    esito = _dati(_chiama(ticker="ALFA"))["fonti"]["finnhub"]

    assert "tradotto" not in esito.lower(), (
        "caveat di traduzione su un ticker che non e' stato tradotto: %r" % (esito,))


# ========================================================================
# 7. DIFETTI TROVATI DALLA REVIEW AVVERSARIALE (22/08) — introdotti da ME
#    curando E. Ognuno confermato eseguendo la mutazione.
# ========================================================================

def test_la_sec_NON_viene_interrogata_su_un_ticker_col_suffisso(monkeypatch):
    """IL PIU' GRAVE. `sec_edgar.lookup_cik` risolve il CIK su
    `ticker.split(".")[0]`: BA.L -> BA -> **il CIK di Boeing**. Prima della cura
    la SEC non vedeva MAI un nome col punto, perche' `news_aggregator` filtrava
    `"." not in ticker`; passando il ticker grezzo ho tolto quel filtro e aperto
    un canale di contaminazione: i filing di un'ALTRA societa' tornerebbero
    intitolati col ticker chiesto (`sec_edgar` costruisce
    f"{ticker}: 8-K Material Event filed") e firmati «interrogata: N eventi».
    Il repo l'aveva gia' misurato: audit/12, un .MI del book -> un fondo US omonimo."""
    interrogati = []
    monkeypatch.setattr(
        sec_edgar, "lookup_cik",
        lambda t, motivo=None, **k: interrogati.append(("cik", t)) or "0000012927")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: interrogati.append(("eventi", tickers)) or [
            {"ticker": tickers[0], "type": "8-K",
             "title": "%s: 8-K Material Event filed" % tickers[0],
             "snippet": "s", "date": "2026-08-20", "url": "u", "importance": 4}])

    d = _dati(_chiama(ticker="BA.L"))

    assert interrogati == [], (
        "la SEC e' stata interrogata su un ticker col suffisso: il CIK viene "
        "risolto su 'BA' e sono i filing di Boeing. Chiamate: %r" % (interrogati,))
    assert not d["events"], "eventi di un'altra societa' nel payload: %r" % (d["events"],)
    esito = d["fonti"]["sec_edgar"]
    assert "omonim" in esito.lower() or "suffisso" in esito.lower(), (
        "il payload non dichiara PERCHE' la SEC non e' stata interrogata: %r" % (esito,))


def test_un_alias_VERIFICATO_invece_la_sec_la_interroga(monkeypatch, tmp_path):
    """Il confine: un alias VERIFICATO (l'emittente deposita davvero presso la SEC con un
    altro simbolo) vive nel negozio privato degli alias, sezione sec: qui iniettato con un
    simbolo inventato. Bloccare anche quello butterebbe un dato vero."""
    from bellomberg.storage import negozi_privati
    negozio = tmp_path / "alias_fonti.json"
    negozio.write_text('{"sec": {"ACME": "ACM"}}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(negozio))
    interrogati = []
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t, motivo=None, **k: "0000012927")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: interrogati.append(list(tickers)) or [])

    _chiama(ticker="ACME.MI")

    assert interrogati == [["ACME.MI"]], (
        "un alias verificato non viene interrogato: %r" % (interrogati,))


def test_un_filing_sec_non_viene_sfrattato_da_venti_headline(monkeypatch):
    """L'ordinamento era per sola data, lessicografico: a parita' di giorno
    '2026-08-20T09:00' batte SEMPRE '2026-08-20', quindi 20 headline Finnhub
    dello stesso giorno espellono l'8-K — e il tool si chiama «eventi
    societari», non «rassegna stampa». Il vecchio percorso ordinava per
    IMPORTANZA e poi per data; la mia cura aveva buttato `importance`."""
    # STESSO GIORNO del filing: e' il caso che fa mordere il difetto. Con un
    # epoch a caso le headline finivano nel 2025 e il test passava per sbaglio.
    import datetime as _dt
    epoch = int(_dt.datetime(2026, 8, 20, 9, 0, 0).timestamp())
    monkeypatch.setattr(
        finnhub_news, "_api_get",
        lambda path, *a, **k: ({} if "press-releases" in path else
                               [{"headline": "headline %d" % i, "summary": "s",
                                 "url": "u", "datetime": epoch, "source": "x",
                                 "category": "company"} for i in range(20)]))
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "SC 13D",
             "title": "ALFA: SC 13D - Stake attivista", "snippet": "s",
             "date": "2026-08-20", "url": "u", "importance": 5}])

    d = _dati(_chiama(ticker="ALFA"))
    titoli = [e.get("title", "") for e in d["events"]]

    assert any("13D" in t for t in titoli), (
        "il filing SEC e' stato sfrattato da 20 headline dello stesso giorno: "
        "nel payload solo %r" % (titoli[:3],))


def test_le_dichiarazioni_precedono_gli_eventi_nel_JSON(monkeypatch):
    """Il taglio a valle (specialists/base.py) e' CIECO E IN CODA:
    result_str[:TETTO] + '...[truncated]'. Con `events` serializzato prima di
    `fonti`/`avviso`, uno sforamento mangia esattamente le dichiarazioni che
    questa cura esiste per consegnare, lasciando gli eventi."""
    import json
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K", "title": "t%d" % i, "snippet": "s",
             "date": "2026-08-%02d" % (1 + i % 28), "url": "u", "importance": 4}
            for i in range(30)])

    testo = json.dumps(_chiama(ticker="ALFA"), ensure_ascii=False, default=str)

    for chiave in ("fonti", "avviso", "taglio"):
        if '"%s"' % chiave not in testo:
            continue
        assert testo.index('"%s"' % chiave) < testo.index('"events"'), (
            "%r e' serializzato DOPO events: un taglio in coda lo cancella "
            "per primo" % (chiave,))


def test_la_sec_caduta_non_viene_dichiarata_interrogata(monkeypatch):
    """`ok_sec = 1` era un LETTERALE messo perche' il CIK si era risolto — ma il
    CIK viene da www.sec.gov/files/company_tickers.json mentre i filing vengono
    da data.sec.gov/submissions: due endpoint diversi. Le chiamate ai filing
    inghiottono 403/500/timeout in silenzio, quindi il payload poteva scrivere
    «zero MISURATO: tutte le fonti hanno risposto» su uno zero cieco."""
    def _eventi_caduti(tickers, days=14, motivo=None, **k):
        if motivo is not None:
            motivo.append("submissions HTTP 403 for %s" % tickers[0])
        return []

    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(sec_edgar, "get_corporate_events_for_portfolio", _eventi_caduti)
    monkeypatch.setattr(
        finnhub_news, "_api_get",
        lambda path, *a, **k: {} if "press-releases" in path else [])

    d = _dati(_chiama(ticker="ALFA"))

    assert not d["fonti"]["sec_edgar"].startswith("interrogata:"), (
        "SEC e' caduta e il payload la dichiara interrogata: %r"
        % (d["fonti"]["sec_edgar"],))
    assert "403" in d["fonti"]["sec_edgar"], (
        "il motivo della caduta non arriva all'agente: %r" % (d["fonti"]["sec_edgar"],))
    assert "zero MISURATO" not in d.get("avviso", ""), (
        "zero cieco dichiarato MISURATO: %r" % (d.get("avviso"),))


def test_non_e_un_filer_NON_e_la_stessa_cosa_di_una_fonte_muta(monkeypatch):
    """Su SIGMA.MI l'elenco SEC HA risposto, e la risposta e' un fatto: quell'
    emittente non deposita presso la SEC. Chiamarla «MUTA» e scrivere «nessuna
    fonte ha risposto» e' cecita' dichiarata dove c'e' una misura."""
    monkeypatch.setattr(
        finnhub_news, "_api_get",
        lambda path, *a, **k: {} if "press-releases" in path else [])
    monkeypatch.setattr(
        sec_edgar, "lookup_cik",
        lambda t, motivo=None, **k: (
            motivo.append("%s assente dall'elenco dei filer SEC (nessun CIK): "
                          "non deposita presso la SEC" % t)
            if motivo is not None else None) or None)

    # ticker SENZA suffisso: col punto scatterebbe (giustamente) la guardia
    # sull'ambiguita' del simbolo, che e' uno stato diverso.
    d = _dati(_chiama(ticker="ZETA"))
    esito = d["fonti"]["sec_edgar"]

    assert not esito.startswith("MUTA"), (
        "«non e' un filer SEC» e' una RISPOSTA, non un silenzio: %r" % (esito,))
    assert "NON COPRE" in esito, (
        "manca lo stato distinto per «la fonte non ha giurisdizione»: %r" % (esito,))
    assert "nessuna fonte ha risposto" not in d.get("avviso", ""), (
        "l'avviso nega una risposta che c'e' stata: %r" % (d.get("avviso"),))


def test_lo_stesso_evento_da_due_fonti_non_viene_contato_due_volte(monkeypatch):
    """Il vecchio percorso deduplicava (news_aggregator._dedupe, due volte).
    Un 8-K e la company-news che lo racconta arrivano con lo stesso titolo e
    lo stesso giorno: senza dedup `count` si gonfia, e la description ordina
    all'agente di leggere `count`. (Fino al 25/08 il doppione di prova era
    press-release+news: con la voce C la coppia reale e' SEC+news — la prima
    stesura post-C lasciava il dedup SCOPERTO, banco: mutazione sopravvissuta.)"""
    import datetime as _dt
    monkeypatch.setattr(
        finnhub_news, "_api_get",
        lambda path, *a, **k: [
            {"headline": "Alfa Corp acquista bitcoin", "summary": "s",
             "url": "https://x",
             "datetime": int(_dt.datetime(2026, 8, 20, 10, 0, 0).timestamp()),
             "source": "y", "category": "company"}])
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K",
             "title": "Alfa Corp acquista bitcoin", "snippet": "s",
             "date": "2026-08-20", "url": "https://sec", "importance": 4}])

    d = _dati(_chiama(ticker="ALFA"))

    assert d["count"] == 1, (
        "lo stesso evento contato %d volte: %r"
        % (d["count"], [e.get("title") for e in d["events"]]))


# ========================================================================
# 8. LE ASSERZIONI CHE LA REVIEW HA DICHIARATO DEBOLI, STRETTE
#    Ognuna nasce da una mutazione che PASSAVA. Il commento dice quale.
# ========================================================================

def test_una_fonte_pienamente_riuscita_non_viene_dichiarata_parziale(monkeypatch):
    """Mutazione che passava: `if ko:` -> `if True:`. Tutte le asserzioni sul
    ramo felice usavano `"interrogata" in ...`, e «interrogata PARZIALMENTE»
    contiene «interrogata». Un buco inventato e' rumore che svaluta i caveat
    veri."""
    monkeypatch.setattr(
        finnhub_news, "_api_get",
        lambda path, *a, **k: {"majorDevelopment": []} if "press-releases" in path else [])
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")

    esito = _dati(_chiama(ticker="ALFA"))["fonti"]["finnhub"]

    assert "PARZIALMENTE" not in esito, (
        "l'unica chiamata Finnhub ha risposto e la fonte e' dichiarata "
        "parziale: %r" % (esito,))
    assert esito.startswith("interrogata: "), esito


def test_la_fonte_dichiara_QUANTI_eventi_ha_reso(monkeypatch):
    """Mutazione che passava: `return "interrogata"` senza il numero. La
    description promette all'agente «ogni fonte dichiara se ha risposto e con
    quanti eventi»: una promessa scritta che smette di essere vera."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K", "title": "t%d" % i, "snippet": "s",
             "date": "2026-08-%02d" % (1 + i), "url": "u", "importance": 4}
            for i in range(3)])

    esito = _dati(_chiama(ticker="ALFA"))["fonti"]["sec_edgar"]

    assert esito == "interrogata: 3 eventi", (
        "la fonte non dichiara il numero esatto di eventi resi: %r" % (esito,))


def test_mostrati_conta_gli_eventi_DAVVERO_nel_payload(monkeypatch):
    """Mutazione che passava: `"mostrati": totale`. Il payload dichiarava 35
    mostrati con 20 eventi dentro."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K", "title": "t%d" % i, "snippet": "s",
             "date": "2026-08-%02d" % (1 + i % 28), "url": "u", "importance": 4}
            for i in range(35)])

    d = _dati(_chiama(ticker="ALFA"))

    assert d["mostrati"] == len(d["events"]), (
        "«mostrati» dice %d ma nel payload ci sono %d eventi"
        % (d["mostrati"], len(d["events"])))


def test_i_numeri_del_taglio_stanno_nel_ruolo_giusto(monkeypatch):
    """Mutazione che passava: invertire i primi due argomenti della %% —
    «eventi trovati 20, nel payload 35». Le tre cifre erano tutte presenti e
    l'asserzione le cercava come sottostringhe scollegate dal ruolo."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K", "title": "t%d" % i, "snippet": "s",
             "date": "2026-08-%02d" % (1 + i % 28), "url": "u", "importance": 4}
            for i in range(35)])

    taglio = _dati(_chiama(ticker="ALFA"))["taglio"]

    assert taglio.startswith("eventi trovati 35, nel payload 20:"), (
        "i numeri del taglio non stanno nel ruolo che la frase annuncia: %r"
        % (taglio,))
    assert "i 15 esclusi" in taglio, taglio


def test_la_chiamata_finnhub_usa_il_ticker_chiesto(monkeypatch):
    """Mutazione che passava (quando le chiamate erano due): un ticker cablato
    su un endpoint si nascondeva in un set che raccoglieva i simboli di
    ENTRAMBI. Dalla voce C l'endpoint e' UNO (company-news): l'uguaglianza
    stretta sul dict path→simbolo copre sia il ticker sbagliato sia una
    chiamata di troppo."""
    simboli = {}
    monkeypatch.setattr(
        finnhub_news, "_api_get",
        lambda path, params=None, timeout=12, **k:
            simboli.__setitem__(path, (params or {}).get("symbol")) or [])

    _chiama(ticker="ALFA")

    assert simboli == {"/company-news": "ALFA"}, (
        "attesa UNA chiamata Finnhub (company-news) col ticker chiesto: %r"
        % (simboli,))


def test_il_parametro_days_arriva_a_ENTRAMBE_le_fonti(monkeypatch):
    """Mutazione che passava: `days=7` cablato su Finnhub, e `days` non passato
    a SEC. Il difetto E era «il ticker non arriva alla fonte»: il parametro
    gemello non aveva copertura."""
    visti = {"finnhub": [], "sec": []}
    monkeypatch.setattr(
        finnhub_news, "_api_get",
        lambda path, params=None, timeout=12, **k:
            visti["finnhub"].append((params or {}).get("from")) or
            ({"majorDevelopment": []} if "press-releases" in path else []))
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: visti["sec"].append(days) or [])

    import datetime as _dt
    _chiama(ticker="ALFA", days=3)

    assert visti["sec"] == [3], (
        "days non arriva a SEC: %r" % (visti["sec"],))
    atteso = (_dt.datetime.now() - _dt.timedelta(days=3)).strftime("%Y-%m-%d")
    assert visti["finnhub"] and all(f == atteso for f in visti["finnhub"]), (
        "days non arriva a Finnhub: finestra da %r, attesa %r"
        % (visti["finnhub"], atteso))


def test_get_recent_filings_dichiara_perche_e_vuota(monkeypatch):
    """Mutazione che passava: togliere l'append del motivo in
    `sec_edgar.get_recent_filings`. I test sul payload stubbano
    `get_corporate_events_for_portfolio`, quindi il cablaggio VERO del motivo
    dentro sec_edgar non era esercitato da nessuno — ed e' esattamente il punto
    in cui «interrogata: 0 eventi» poteva essere una bugia."""
    class _R503:
        status_code = 503
        text = ""

    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t, motivo=None, **k: "0000000042")
    monkeypatch.setattr(sec_edgar.requests, "get", lambda *a, **k: _R503())

    motivo = []
    out = sec_edgar.get_recent_filings("ALFA", days=30, motivo=motivo)

    assert out == []
    assert motivo, "lista vuota da un 503 senza motivo: il chiamante non puo' "\
                    "distinguere «nessun filing» da «SEC non ha risposto»"
    assert "503" in " ".join(motivo), motivo


def test_gli_eventi_senza_data_sono_dichiarati(monkeypatch):
    """Mutazione che passava: togliere il blocco `senza_data`. Una data assente
    ordina in fondo e sarebbe descritta come «piu' vecchia»: ma non e' vecchia,
    e' senza data — e il prompt di eventdesk dice «senza data non e' un
    catalyst, e' una chiacchiera»."""
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(
        sec_edgar, "get_corporate_events_for_portfolio",
        lambda tickers, days=14, **k: [
            {"ticker": tickers[0], "type": "8-K", "title": "senza data",
             "snippet": "s", "date": "", "url": "u", "importance": 4}])

    d = _dati(_chiama(ticker="ALFA"))

    assert "senza_data" in d, (
        "un evento senza data entra nel payload e nulla lo dichiara: %r"
        % (sorted(d),))
    assert "1 eventi su 1" in d["senza_data"], d["senza_data"]
