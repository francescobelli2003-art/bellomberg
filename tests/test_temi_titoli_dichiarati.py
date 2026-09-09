"""La mappa TEMA -> TITOLI vive nel negozio privato, e a negozio assente si DICHIARA.

06/09 (Opus 5, chat `bellomberg-14`, criterio (1) lotto (b), MASTER §9-unnonagies).

PERCHE' QUESTA BATTERIA ESISTE. `news_topics.TOPICS` portava per ogni tema una lista
`tickers_affected` col book di chi ha scritto il file. Non era prosa: `news_aggregator`
la copia su OGNI notizia del tema (tre punti), `chat_tools` la mette nel payload del tool
e il payload finisce in un `tool_result` dentro un messaggio `role: "user"` — cioe'
davanti al modello che alloca soldi veri. Ed e' successo: nel blackboard del 03/09 l'event
desk ha scritto di sua iniziativa «[src: get_macro_news_by_topic geopolitics, tickers_affected
...]» ricopiando due sigle. Su un'altra installazione quei legami tema->titolo sarebbero
ereditati da chi non possiede quei titoli, e un nesso economico falso (un veicolo China tech
elencato sotto «politica italiana») arriva al modello come se fosse un fatto.

COSA SI PRETENDE QUI, e ognuna e' una riga che deve poter CADERE:
  A. il SORGENTE non porta piu' nessuna lista di titoli;
  B. il negozio assente/illeggibile rende una mappa VUOTA con `origine` e `motivo`, e una
     voce malformata rende illeggibile il negozio INTERO (mezzo negozio e' un ripiego muto);
  C. la dichiarazione arriva al MODELLO: `providers_blocked()` porta la chiave del negozio,
     e il tool la rende in `fonti_mute` — «nessuna notizia» e «non so a chi legarle» non
     possono essere la stessa frase (regola PM 14/07);
  D. il CABLAGGIO: non basta che il caricatore funzioni, devono usarlo i tre punti di
     `fetch_macro_news` — qui si esercita la funzione VERA coi fetcher finti;
  E. la rilettura e' A OGNI CHIAMATA, non a import-time (un backend acceso deve vedere una
     modifica del negozio senza riavvio);
  F. una chiave del negozio che non corrisponde a nessun tema e' DICHIARATA, non ignorata:
     un legame che non si applica piu' e' un buco silenzioso.

I simboli usati qui sono INVENTATI: questo file esce nel tree pubblico.
"""
import json
import os

import pytest

from bellomberg.storage import negozi_privati
from bellomberg.market_data import news_aggregator
from bellomberg.market_data import news_topics


# simboli e temi INVENTATI: nessun nome del book in questo file
TEMA_FINTO = "fed"          # un id che esiste davvero nel registro (serve al cablaggio)
SIMBOLI_FINTI = ["KRYPTO", "GAMMA.MI"]


@pytest.fixture
def negozio(tmp_path, monkeypatch):
    """Scrive un negozio finto e ci punta la famiglia. Torna una funzione che lo riscrive."""
    p = tmp_path / "news_topics_tickers.json"

    def scrivi(contenuto):
        if isinstance(contenuto, str):
            p.write_text(contenuto, encoding="utf-8")
        else:
            p.write_text(json.dumps(contenuto), encoding="utf-8")
        return str(p)

    scrivi({TEMA_FINTO: SIMBOLI_FINTI})
    monkeypatch.setattr(negozi_privati, "PERCORSO_TEMI_TITOLI", str(p))
    return scrivi


@pytest.fixture
def senza_negozio(tmp_path, monkeypatch):
    monkeypatch.setattr(negozi_privati, "PERCORSO_TEMI_TITOLI",
                        str(tmp_path / "non_esiste.json"))


# ============================================================
# A. IL SORGENTE
# ============================================================
def test_nessun_tema_porta_piu_una_lista_di_titoli_nel_sorgente():
    """La chiave `tickers_affected` non esiste piu' su NESSUN tema: e' il dato del book.
    Si asserisce la PROPRIETA' (nessuna chiave), non un elenco: un elenco atteso sarebbe
    a sua volta il book scritto in un file che esce nel pubblico."""
    con_titoli = [t["id"] for t in news_topics.TOPICS if "tickers_affected" in t]
    assert con_titoli == [], (
        "questi temi portano ancora la lista dei titoli nel sorgente: %s" % con_titoli)


def test_il_registro_dei_temi_resta_un_attributo_di_modulo_coi_suoi_campi():
    """`TOPICS` e' un canale del controllo 10 del cancello (`verifica_pubblico.py`,
    CANALI_PAYLOAD): se sparisse come attributo, `payload_statico` lo metterebbe fra i
    guasti, il controllo tornerebbe un ERRORE (non un conteggio ridotto) e
    `export_pubblico --commit` fermerebbe il deposito. Quindi il lotto toglie il DATO,
    mai il registro."""
    assert isinstance(news_topics.TOPICS, list) and news_topics.TOPICS
    for t in news_topics.TOPICS:
        for campo in ("id", "label", "category", "query", "importance"):
            assert campo in t, "il tema %r ha perso il campo %r" % (t.get("id"), campo)


# ============================================================
# B. IL NEGOZIO
# ============================================================
def test_negozio_assente_rende_mappa_vuota_e_dichiara_origine_e_motivo(senza_negozio):
    e = negozi_privati.carica_temi_titoli()
    assert e["temi"] == {}
    assert e["origine"] == "assente"
    assert e["motivo"] and "news_topics_tickers.example.json" in e["motivo"], (
        "il motivo deve dire a chi legge COME rimediare, nominando l'esempio tracciato")


def test_negozio_illeggibile_dichiara_illeggibile_non_assente(negozio):
    negozio("{ questo non e' json")
    e = negozi_privati.carica_temi_titoli()
    assert e["temi"] == {}
    assert e["origine"] == "illeggibile"
    assert e["motivo"]


def test_una_voce_malformata_rende_illeggibile_il_negozio_intero(negozio):
    """Mezzo negozio caricato e' un ripiego muto: le voci buone passerebbero e quella rotta
    sparirebbe senza che nessuno lo sappia."""
    negozio({TEMA_FINTO: SIMBOLI_FINTI, "ecb": "non-una-lista"})
    e = negozi_privati.carica_temi_titoli()
    assert e["origine"] == "illeggibile"
    assert e["temi"] == {}, "nessuna voce deve sopravvivere a un negozio illeggibile"
    assert "ecb" in e["motivo"], "il motivo deve nominare la voce che ha rotto il negozio"


def test_lista_vuota_e_malformata_perche_si_scrive_togliendo_la_voce(negozio):
    negozio({TEMA_FINTO: []})
    assert negozi_privati.carica_temi_titoli()["origine"] == "illeggibile"


def test_simbolo_non_canonico_e_illeggibile(negozio):
    """Il lookup dei simboli e' MAIUSCOLO ovunque: una voce minuscola non verrebbe MAI
    trovata e il codice direbbe «nessun titolo», vero per la macchina e falso per chi ha
    appena scritto la voce."""
    negozio({TEMA_FINTO: ["krypto"]})
    e = negozi_privati.carica_temi_titoli()
    assert e["origine"] == "illeggibile"
    assert "krypto" in e["motivo"].lower()


@pytest.mark.parametrize("chiave", ["FED", "Fed", "fed rate", "fed-rate", "", "fed__x_"])
def test_un_id_di_tema_non_canonico_e_illeggibile(negozio, chiave):
    """La chiave e' l'id del tema come sta nel registro: uno slug MINUSCOLO. Il lookup
    confronta `t["id"]`, quindi una chiave MAIUSCOLA o con spazi non verrebbe MAI trovata e
    il codice direbbe «questo tema non muove titoli» — vero per la macchina e falso per chi
    ha appena scritto la voce. E' l'opposto delle altre famiglie, dove la chiave e' un
    simbolo e va MAIUSCOLA: il vincolo e' lo stesso (la forma in cui la si cerca), la forma
    e' l'altra."""
    negozio({chiave: SIMBOLI_FINTI})
    e = negozi_privati.carica_temi_titoli()
    assert e["origine"] == "illeggibile", "la chiave %r e' stata accettata" % chiave
    assert "id di tema" in e["motivo"]


def test_le_chiavi_con_underscore_sono_note_e_non_temi(negozio):
    negozio({"_leggimi": "una nota", TEMA_FINTO: SIMBOLI_FINTI})
    e = negozi_privati.carica_temi_titoli()
    assert e["origine"] not in ("assente", "illeggibile")
    assert set(e["temi"]) == {TEMA_FINTO}


def test_negozio_buono_rende_i_titoli(negozio):
    e = negozi_privati.carica_temi_titoli()
    assert e["temi"] == {TEMA_FINTO: SIMBOLI_FINTI}
    assert e["motivo"] is None
    assert e["origine"] not in ("assente", "illeggibile")


# ============================================================
# C. LA DICHIARAZIONE ARRIVA AL MODELLO
# ============================================================
def test_providers_blocked_dichiara_il_negozio_assente(senza_negozio):
    """La stessa via con cui il negozio dei termini news si dichiara: il tool macro dei
    desk rende `providers_blocked()` in `fonti_mute`, quindi il modello VEDE il buco."""
    muti = news_aggregator.providers_blocked()
    assert news_aggregator.TEMI_TITOLI_MUTI in muti
    assert muti[news_aggregator.TEMI_TITOLI_MUTI].startswith("NEGOZIO_ASSENTE")


def test_providers_blocked_dichiara_il_negozio_illeggibile(negozio):
    negozio("{ rotto")
    muti = news_aggregator.providers_blocked()
    assert muti[news_aggregator.TEMI_TITOLI_MUTI].startswith("NEGOZIO_ILLEGGIBILE")


def test_providers_blocked_tace_a_negozio_buono(negozio):
    assert news_aggregator.TEMI_TITOLI_MUTI not in news_aggregator.providers_blocked()


def test_il_modulo_del_negozio_non_importabile_e_DICHIARATO_non_sollevato(monkeypatch):
    """`providers_blocked()` e' chiamata da `chat_tools` e da cinque endpoint dell'API: se il
    modulo del negozio non fosse importabile, un'eccezione qui trasformerebbe «dichiara il
    buco» in un 500 — cioe' il ripiego muto dentro il codice scritto per impedirlo. E' il
    trattamento che la funzione riserva gia' a `tiingo_news` due righe sopra."""
    import sys
    monkeypatch.setitem(sys.modules, 'bellomberg.storage.negozi_privati', None)
    muti = news_aggregator.providers_blocked()
    assert news_aggregator.TEMI_TITOLI_MUTI in muti
    assert muti[news_aggregator.TEMI_TITOLI_MUTI].startswith("MODULO_ASSENTE")


def test_il_giro_col_modulo_non_importabile_non_esplode(monkeypatch, senza_rete):
    """Stessa cosa dal lato del giro: `fetch_macro_news` deve rendere le notizie SENZA titoli,
    non morire."""
    import sys
    monkeypatch.setitem(sys.modules, 'bellomberg.storage.negozi_privati', None)
    items = news_aggregator.fetch_macro_news(categories=["rates"], min_importance=5, days=1,
                                             max_per_topic=1, include_reddit=False)
    assert items, "senza notizie la prova non misura niente"
    assert all(it.get("tickers_affected") == [] for it in items)


def test_la_dichiarazione_scatta_sull_origine_non_sul_motivo(monkeypatch, senza_negozio):
    """`motivo` e' la CONSEGUENZA del guasto, `origine` e' il guasto: se un ramo di
    fallimento futuro dimenticasse di riempire il motivo, una guardia appesa al motivo
    sparirebbe ZITTA. (Lezione del lotto 6, §9-duooctogies.)"""
    monkeypatch.setattr(negozi_privati, "carica_temi_titoli",
                        lambda *a, **k: {"temi": {}, "origine": "assente", "motivo": None})
    muti = news_aggregator.providers_blocked()
    assert news_aggregator.TEMI_TITOLI_MUTI in muti


# ============================================================
# D. IL CABLAGGIO — non l'helper: i punti che lo usano
# ============================================================
@pytest.fixture
def senza_rete(monkeypatch):
    """Zittisce ogni fonte esterna: resta il solo cablaggio dei titoli."""
    monkeypatch.setattr(news_aggregator, "_fetch_newsapi",
                        lambda q, **k: [{"title": "titolo finto", "url": "http://x",
                                         "provider": "finto", "snippet": ""}])
    monkeypatch.setattr(news_aggregator, "_fetch_gnews", lambda q, **k: [])
    monkeypatch.setattr(news_aggregator, "_fetch_rss", lambda n, u, **k: [])
    news_aggregator._CACHE.clear()


def test_le_notizie_ricevono_i_titoli_dal_negozio(negozio, senza_rete):
    """Il punto (1) di `fetch_macro_news`: la lista arriva sulle notizie del tema, e viene
    dal NEGOZIO, non dal sorgente."""
    items = news_aggregator.fetch_macro_news(categories=["rates"], min_importance=5,
                                             days=1, max_per_topic=1, include_reddit=False)
    del_tema = [it for it in items if it.get("topic_id") == TEMA_FINTO]
    assert del_tema, "il tema di prova non ha prodotto notizie: la prova non misura niente"
    assert del_tema[0]["tickers_affected"] == SIMBOLI_FINTI


def test_a_negozio_assente_le_notizie_non_portano_titoli(senza_negozio, senza_rete):
    items = news_aggregator.fetch_macro_news(categories=["rates"], min_importance=5,
                                             days=1, max_per_topic=1, include_reddit=False)
    assert items, "senza notizie la prova non misura niente"
    assert all(it.get("tickers_affected") == [] for it in items)


def test_anche_le_notizie_RSS_ricevono_i_titoli_dal_negozio(negozio, monkeypatch):
    """Il punto (2) di `fetch_macro_news`: le notizie RSS sono attribuite a un tema per
    KEYWORD della sua query, e ricevono i titoli per una strada DIVERSA da quella delle
    notizie API. Il banco di mutazioni l'ha trovato scoperto: la fixture `senza_rete`
    zittisce anche l'RSS, quindi le altre prove non passavano mai di qui."""
    monkeypatch.setattr(news_aggregator, "_fetch_newsapi", lambda q, **k: [])
    monkeypatch.setattr(news_aggregator, "_fetch_gnews", lambda q, **k: [])
    monkeypatch.setattr(news_aggregator, "RSS_FEEDS", {"finto": "http://esempio.invalid/rss"})
    # «FOMC» e' una keyword della query del tema `fed`: e' cosi' che l'RSS viene attribuito
    monkeypatch.setattr(news_aggregator, "_fetch_rss", lambda n, u, **k: [
        {"title": "FOMC minutes", "url": "http://esempio.invalid/1", "provider": "rss",
         "snippet": ""}])
    news_aggregator._CACHE.clear()
    items = news_aggregator.fetch_macro_news(categories=["rates"], min_importance=5, days=1,
                                             max_per_topic=1, include_reddit=False)
    da_rss = [it for it in items if it.get("provider") == "rss"]
    assert da_rss, "nessuna notizia RSS attribuita: la prova non misura niente"
    assert da_rss[0]["tickers_affected"] == SIMBOLI_FINTI


def test_il_giro_dichiara_nel_log_il_negozio_rotto(senza_negozio, senza_rete, capsys):
    """Il payload lo dichiara al modello; il LOG lo dichiara a chi guarda la run. Sono due
    lettori diversi e servono tutt'e due: chi legge `consigliere_run.log` non vede il
    payload dei tool."""
    news_aggregator.fetch_macro_news(categories=["rates"], min_importance=5, days=1,
                                     max_per_topic=1, include_reddit=False)
    uscita = capsys.readouterr().out
    assert "negozio dei titoli per tema assente" in uscita
    assert "fetch_macro_news" in uscita, "la dichiarazione deve dire in quale giro e' successo"


def test_il_negozio_sparito_non_serve_la_cache_coi_titoli_di_prima(negozio, senza_rete,
                                                                   monkeypatch, tmp_path):
    """La cache dura 900 s e la sua chiave non conteneva lo stato del negozio: un negozio
    sparito a meta' TTL avrebbe lasciato per un quarto d'ora notizie COI titoli attaccati
    accanto alla dichiarazione «negozio assente» — il dato e la sua smentita nello stesso
    payload."""
    primi = news_aggregator.fetch_macro_news(categories=["rates"], min_importance=5, days=1,
                                             max_per_topic=1, include_reddit=False)
    assert any(it.get("tickers_affected") for it in primi), "la prova non parte dal caso utile"
    monkeypatch.setattr(negozi_privati, "PERCORSO_TEMI_TITOLI", str(tmp_path / "sparito.json"))
    dopo = news_aggregator.fetch_macro_news(categories=["rates"], min_importance=5, days=1,
                                            max_per_topic=1, include_reddit=False)
    assert dopo, "senza notizie la prova non misura niente"
    assert all(it.get("tickers_affected") == [] for it in dopo), (
        "la cache ha servito i titoli di prima a negozio gia' sparito")


def test_i_topic_che_impattano_un_titolo_si_risolvono_dal_negozio(negozio):
    temi = news_topics.get_topics_affecting_ticker(SIMBOLI_FINTI[0])
    assert [t["id"] for t in temi] == [TEMA_FINTO]


def test_i_topic_che_impattano_un_titolo_sono_vuoti_a_negozio_assente(senza_negozio):
    assert news_topics.get_topics_affecting_ticker(SIMBOLI_FINTI[0]) == []


# ============================================================
# E. RILETTURA A OGNI CHIAMATA
# ============================================================
def test_il_negozio_e_riletto_a_ogni_chiamata_non_a_import_time(negozio):
    """Un negozio letto a import-time e' un backend che non vede una dichiarazione nuova
    finche' non lo riavvii: e' un fallback silenzioso con un ritardo."""
    assert negozi_privati.carica_temi_titoli()["temi"] == {TEMA_FINTO: SIMBOLI_FINTI}
    negozio({TEMA_FINTO: ["ALTRO"]})
    assert negozi_privati.carica_temi_titoli()["temi"] == {TEMA_FINTO: ["ALTRO"]}


# ============================================================
# F. UNA VOCE CHE NON SI APPLICA PIU' SI DICHIARA
# ============================================================
def test_una_chiave_che_non_e_un_tema_viene_dichiarata(negozio, caplog):
    """Se un tema viene tolto dal registro e la voce resta nel negozio, quel legame non si
    applica piu' a niente. Ignorarlo in silenzio e' la stessa specie di guasto che questo
    lotto cura."""
    negozio({TEMA_FINTO: SIMBOLI_FINTI, "tema_che_non_esiste_piu": ["GAMMA.MI"]})
    senza_riscontro = news_topics.temi_senza_riscontro()
    assert senza_riscontro == ["tema_che_non_esiste_piu"]


def test_nessuna_voce_senza_riscontro_a_negozio_buono(negozio):
    assert news_topics.temi_senza_riscontro() == []


def test_nessuna_voce_senza_riscontro_a_negozio_assente(senza_negozio):
    """A negozio assente non c'e' NIENTE che non si applichi: il buco lo dichiara
    `providers_blocked`, non questa misura. Due dichiarazioni per lo stesso guasto
    direbbero due volte la stessa cosa e nasconderebbero il caso vero."""
    assert news_topics.temi_senza_riscontro() == []


# ============================================================
# G. L'ESEMPIO TRACCIATO
# ============================================================
def test_l_esempio_tracciato_esiste_ed_e_leggibile_dal_caricatore():
    """Il file che esce nel clone dev'essere un negozio VALIDO: e' la forma che chi installa
    copia in data/. Un esempio che il caricatore rifiuta insegna la forma sbagliata."""
    sorgente = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "bellomberg", "resources", "examples",
                            "news_topics_tickers.example.json")
    assert os.path.exists(sorgente), "manca l'esempio tracciato %s" % sorgente
    e = negozi_privati.carica_temi_titoli(sorgente)
    assert e["origine"] == sorgente, e["motivo"]
    assert e["temi"], "l'esempio deve mostrare almeno una voce"


def test_l_esempio_non_porta_nessun_id_di_tema_vero():
    """L'esempio e' pubblico: se usasse gli id VERI del registro direbbe a chi legge quali
    temi il PM lega al proprio portafoglio — cioe' meta' del dato che il lotto toglie."""
    sorgente = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "bellomberg", "resources", "examples",
                            "news_topics_tickers.example.json")
    with open(sorgente, encoding="utf-8") as fh:
        grezzo = json.load(fh)
    veri = {t["id"] for t in news_topics.TOPICS}
    usati = {k for k in grezzo if not k.startswith("_")}
    assert not (usati & veri), "l'esempio usa id di temi VERI: %s" % sorted(usati & veri)
