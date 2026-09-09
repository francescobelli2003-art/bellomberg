"""Voce 8, SECONDO PEZZO (ok PM 13/08 "ok procedi tu", Fable 5): le FONTI
per-ticker della trimestrale automatica.

Il grilletto del registro (guidance_watch) copre solo i nomi con righe attive
in company_guidance; il resto del book e' fuori radar (§9-octotrigies). Questo
modulo e' la mappa curata delle fonti UFFICIALI per-ticker (pagina IR,
calendario finanziario, prossima data di report attesa) che estende il radar a
tutto il book — MAI finnhub (inaffidabile, misura 03/08), MAI una tabella
nuova (migrazione = lettera A/B del PM, ancora aperta).

Contratto:
- `fonte_per(ticker)`: MAI None silenzioso — ticker ignoto -> stato 'assente'
  col motivo (regola PM 14/07).
- `classifica_book(tickers)`: ogni ticker del book finisce in ESATTAMENTE una
  di quattro liste dichiarate (con_fonte / non_applicabili / scoperti piu'
  con_fonte_senza_data dentro con_fonte via campo) — niente buchi zitti.
- `radar_fonti(today, entro_giorni, fonti)`: date deterministiche iniettabili;
  chi non ha data verificata esce in `non_verificati`, non sparisce.
- Igiene dei dati veri: gira SEMPRE sull'esempio tracciato
  (`fonti_guidance.example.json`, che c'e' in ogni clone) e IN PIU' sul negozio
  privato dove esiste — una data senza fonte e senza giorno di verifica e' un
  segnaposto, non una misura, e viene bocciata. Mai un passaggio a vuoto: se
  non c'e' niente da controllare il test CADE dicendolo.
"""
import importlib
import json
import os
import re
import sqlite3
import subprocess
import sys

import pytest

from bellomberg.market_data.fonti_guidance import (TIPI_APPLICABILI, classifica_book, fonte_per,
                            radar_fonti)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FONTI_COLLAUDO = {
    "ACME.MI": {
        "nome": "Acme SpA", "tipo": "equity",
        "ir_url": "https://acme.example/investors",
        "calendario_url": "https://acme.example/investors/calendar",
        "next_report_date": "2026-08-10",   # gia' passata al 17/08
        "next_report_source": "calendario IR acme.example",
        "verificato_il": "2026-08-01",
    },
    "BETA.DE": {
        "nome": "Beta AG", "tipo": "equity",
        "ir_url": "https://beta.example/ir",
        "calendario_url": "https://beta.example/ir/termine",
        "next_report_date": "2026-08-25",   # entro 14 giorni dal 17/08
        "next_report_source": "calendario IR beta.example",
        "verificato_il": "2026-08-01",
    },
    "GAMMA": {
        "nome": "Gamma Corp", "tipo": "equity",
        "ir_url": "https://gamma.example/investors",
        "calendario_url": None,
        "next_report_date": None,           # fonte c'e', data MAI verificata
        "next_report_source": None,
        "verificato_il": None,
    },
    "ORO.MI": {
        "nome": "Oro ETC", "tipo": "etc",
        "note": "replica fisica: niente trimestrali",
    },
}


# ---------- fonte_per: mai un None silenzioso ----------

def test_fonte_ignota_dichiarata_assente_mai_none():
    r = fonte_per("INESISTENTE.XX", fonti=FONTI_COLLAUDO)
    assert r is not None
    assert r["stato"] == "assente"
    assert "INESISTENTE.XX" in r["motivo"]


def test_fonte_nota_esce_con_stato_ok():
    r = fonte_per("ACME.MI", fonti=FONTI_COLLAUDO)
    assert r["stato"] == "ok"
    assert r["nome"] == "Acme SpA"
    assert r["ir_url"].startswith("https://")


def test_senza_fonti_si_legge_il_negozio_A_OGNI_CHIAMATA(tmp_path, monkeypatch):
    """Decisione del Task 6 (04/09): il default di `fonte_per` e
    `classifica_book` e' uno snapshot PER CHIAMATA, non piu' il valore
    dell'import. Misura sul comportamento — il negozio cambia fra due
    chiamate — cosi' cade sia se il default torna a essere l'import, sia se
    qualcuno memorizza il caricamento."""
    import bellomberg.market_data.fonti_guidance as fg
    negozio = tmp_path / "fonti_guidance.json"
    negozio.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fg, "PERCORSO_FONTI", str(negozio))

    assert fg.fonte_per("ACME.MI")["stato"] == "assente"
    assert fg.classifica_book(["ACME.MI"])["scoperti"] == ["ACME.MI"]

    negozio.write_text(json.dumps({
        "ACME.MI": {"nome": "Acme SpA", "tipo": "equity",
                    "ir_url": "https://acme.invalid/investors",
                    "next_report_date": "2026-12-01",
                    "next_report_source": "calendario IR acme.invalid",
                    "verificato_il": "2026-09-01"}}), encoding="utf-8")

    assert fg.fonte_per("ACME.MI")["stato"] == "ok", \
        "fonte_per sta ancora leggendo uno snapshot vecchio"
    assert fg.classifica_book(["ACME.MI"])["con_fonte"] == ["ACME.MI"], \
        "classifica_book sta ancora leggendo uno snapshot vecchio"


# ---------- classifica_book: conservazione, nessun buco zitto ----------

def test_ogni_ticker_del_book_finisce_in_una_lista_sola():
    book = ["ACME.MI", "BETA.DE", "GAMMA", "ORO.MI", "IGNOTO.L"]
    r = classifica_book(book, fonti=FONTI_COLLAUDO)
    tutte = (r["con_fonte"] + r["non_applicabili"] + r["scoperti"])
    assert sorted(tutte) == sorted(book)          # conservazione
    assert len(tutte) == len(set(tutte))          # nessun doppione


def test_classifica_separa_i_tre_destini():
    r = classifica_book(["ACME.MI", "ORO.MI", "IGNOTO.L"], fonti=FONTI_COLLAUDO)
    assert r["con_fonte"] == ["ACME.MI"]
    assert r["non_applicabili"] == ["ORO.MI"]
    assert r["scoperti"] == ["IGNOTO.L"]


def test_classifica_conta_le_fonti_senza_data_verificata():
    """GAMMA ha la pagina IR ma nessuna data: e' con_fonte, MA il buco della
    data e' dichiarato a parte — non annegato dentro la lista buona."""
    r = classifica_book(["GAMMA"], fonti=FONTI_COLLAUDO)
    assert r["con_fonte"] == ["GAMMA"]
    assert r["con_fonte_senza_data"] == ["GAMMA"]


# ---------- radar_fonti: date deterministiche, buchi dichiarati ----------

def test_report_uscito_entra_nel_radar_con_ritardo():
    r = radar_fonti(today="2026-08-17", entro_giorni=14, fonti=FONTI_COLLAUDO)
    tk = [x["ticker"] for x in r["usciti"]]
    assert tk == ["ACME.MI"]
    acme = r["usciti"][0]
    assert acme["giorni_dalla_data_attesa"] == 7
    assert acme["next_report_source"] == "calendario IR acme.example"


def test_report_imminente_dentro_finestra():
    r = radar_fonti(today="2026-08-17", entro_giorni=14, fonti=FONTI_COLLAUDO)
    assert [x["ticker"] for x in r["imminenti"]] == ["BETA.DE"]
    assert r["imminenti"][0]["fra_giorni"] == 8


def test_data_mai_verificata_esce_dichiarata_non_sparisce():
    r = radar_fonti(today="2026-08-17", entro_giorni=14, fonti=FONTI_COLLAUDO)
    assert [x["ticker"] for x in r["non_verificati"]] == ["GAMMA"]


def test_non_applicabili_contati_nel_radar():
    r = radar_fonti(today="2026-08-17", entro_giorni=14, fonti=FONTI_COLLAUDO)
    assert r["non_applicabili"] == 1  # ORO.MI


def test_radar_dichiara_la_fonte_e_rinnega_finnhub():
    r = radar_fonti(today="2026-08-17", entro_giorni=14, fonti=FONTI_COLLAUDO)
    assert "finnhub" not in r["fonte"].lower() or "mai" in r["fonte"].lower()


def test_radar_fonti_SENZA_fonti_non_cicla_il_negozio_ma_SOLLEVA():
    """Il buco strutturale lasciato aperto dal Task 5 e chiuso qui (04/09).
    `radar_fonti` e' l'unica funzione la cui uscita e' lunga quanto la mappa
    che riceve: col vecchio default un chiamante che si dimenticava `fonti=`
    ciclava il negozio INTERO — la stessa fuga tolta dal `__main__`, riaperta
    da un'omissione. Un parametro senza default non si dimentica in silenzio.

    Si misura anche che NON esista una via posizionale: `radar_fonti(mappa)`
    metterebbe la mappa in `today` e il buco tornerebbe da un'altra porta."""
    with pytest.raises(TypeError):
        radar_fonti(today="2026-08-17", entro_giorni=14)
    with pytest.raises(TypeError):
        radar_fonti()
    with pytest.raises(TypeError):
        radar_fonti(FONTI_COLLAUDO)
    # e con la parola chiave funziona come sempre: il presidio non ha rotto l'uso
    assert radar_fonti(today="2026-08-17", fonti=FONTI_COLLAUDO)["usciti"]


# ---------- igiene: sempre sull'ESEMPIO, in piu' sul negozio se c'e' ----------
#
# Prima del Task 4 questi tre giravano su `FONTI`, cioe' sul dict committato.
# Tolto il dict dal sorgente (Task 3), sul negozio VUOTO di un clone si
# comportavano in due modi entrambi falsi, misurati col tracciato degli assert
# davvero eseguiti: `campi_minimi` cadeva su `len(FONTI) > 0` (61 assert -> 1,
# quello che cade), e gli altri due passavano A VUOTO (18 assert -> 0), perche'
# le loro asserzioni stanno dentro un `for` su una mappa vuota. Il verde a
# vuoto e' il guasto peggiore del rosso: un test che dichiara di misurare un
# dato e non ne tocca nessuno.

def _mappe_da_controllare():
    """Le mappe su cui l'igiene gira: l'ESEMPIO tracciato SEMPRE, il negozio
    privato IN PIU' dove esiste.

    `fonti_guidance.example.json` e' committato, quindi c'e' in ogni clone e
    nel tree pubblico: i test misurano un dato vero anche dove il negozio del
    PM non c'e' e non deve esserci. Il negozio si aggiunge dove c'e' — la sua
    assenza non e' un errore, ma non deve nemmeno far passare i test in
    silenzio (regola PM 14/07): a impedirlo sono i contatori qui sotto, che
    fanno cadere il test quando non ha controllato nulla.

    ASSENZA e GUASTO del negozio NON sono la stessa cosa, e vanno separati:
    l'assenza e' legittima (e' il caso del clone e del repo pubblico) e si
    prosegue sull'esempio; un negozio ILLEGGIBILE e' un guasto, e proseguire
    zitti sarebbe il ripiego silenzioso che il progetto vieta (regola PM
    14/07). Misurato in review: con un `fonti_guidance.json` corrotto i tre
    test davano 8/4/4 assert e PASS, identici al mondo senza negozio, e
    nessuno diceva che il file del PM non si legge. Ora si cade, col motivo
    che `carica_fonti` ha gia' scritto.

    L'esempio si cerca ACCANTO AL MODULO (`fonti_guidance.__file__`), non due
    cartelle sopra questo test: e' li' che sta, ed e' — fra le due ancore
    considerate — quella che regge anche quando sorgente e batteria vengono
    copiati in un layout piatto, come fa
    `prove_run/fonti/banco_mutazioni_caricatore.py`; il percorso relativo a
    questo file li' dentro punterebbe alla cartella temp di Windows."""
    import bellomberg.market_data.fonti_guidance as fg
    from bellomberg.core.paths import EXAMPLES_DIR
    percorso = os.path.join(str(EXAMPLES_DIR), "fonti_guidance.example.json")
    esempio = fg.carica_fonti(path=percorso)
    assert esempio["origine"] not in ("assente", "illeggibile"), (
        "l'esempio tracciato deve esistere ed essere leggibile (%s): origine=%s"
        " motivo=%s" % (percorso, esempio["origine"], esempio["motivo"]))
    mappe = [("esempio", esempio["fonti"])]
    negozio = fg.carica_fonti()
    assert negozio["origine"] != "illeggibile", (
        "il negozio privato c'e' ma NON SI LEGGE (%s): %s — l'assenza del "
        "negozio e' legittima, il guasto no: l'igiene non prosegue in silenzio "
        "sul solo esempio come se il file non esistesse"
        % (fg.PERCORSO_FONTI, negozio["motivo"]))
    if negozio["origine"] != "assente":
        mappe.append(("negozio", negozio["fonti"]))
    return mappe


def test_ogni_voce_ha_i_campi_minimi():
    controllate = 0
    for nome, mappa in _mappe_da_controllare():
        assert mappa, f"{nome}: mappa vuota, non ci sarebbe niente da misurare"
        for tk, f in mappa.items():
            controllate += 1
            assert f.get("nome"), f"{nome}/{tk}: nome mancante"
            assert f.get("tipo"), f"{nome}/{tk}: tipo mancante"
    # Il contatore e' ridondante con l'assert `mappa` qui sopra e con la
    # guardia sull'esempio dentro l'helper: e' scritto lo stesso perche' negli
    # altri due test la garanzia e' esplicita, e una garanzia implicita e' cio'
    # che si perde alla prima modifica.
    assert controllate, ("nessuna voce controllata in nessuna mappa: il test "
                         "passerebbe senza aver misurato un solo campo")


def test_le_voci_applicabili_hanno_ir_https():
    controllate = 0
    for nome, mappa in _mappe_da_controllare():
        for tk, f in mappa.items():
            # `f.get("tipo")` e non `f["tipo"]`: una voce SENZA tipo viene
            # saltata qui invece di esplodere in un KeyError che parlerebbe di
            # un'altra regola. Il buco non resta scoperto, ma il presidio e'
            # ALTROVE: a bocciare la voce senza tipo e'
            # `test_ogni_voce_ha_i_campi_minimi`, che pretende `tipo` su ogni
            # voce di ogni mappa. E' quel test a reggere questa scorciatoia: se
            # sparisse lui, di qui una voce senza tipo passerebbe zitta.
            if f.get("tipo") in TIPI_APPLICABILI:
                controllate += 1
                assert str(f.get("ir_url", "")).startswith("https://"), \
                    f"{nome}/{tk}: ir_url assente o non https"
    assert controllate, ("nessuna voce di tipo applicabile in nessuna mappa: "
                         "il test passerebbe senza aver controllato un ir_url")


def test_una_data_senza_fonte_e_verifica_e_un_segnaposto():
    """Regola 'la frase in pagina e' un'affermazione': se c'e' una
    next_report_date DEVE portare la sua fonte e il giorno della verifica.
    Una data nuda e' un segnaposto, e il test la boccia — sull'esempio come
    sul negozio. Il contatore finale e' la meta' che mancava: senza, una mappa
    di sole voci senza data avrebbe fatto passare il test a vuoto invece di
    dire che la regola non e' stata misurata."""
    from datetime import date
    controllate = 0
    for nome, mappa in _mappe_da_controllare():
        for tk, f in mappa.items():
            nrd = f.get("next_report_date")
            if nrd is not None:
                controllate += 1
                date.fromisoformat(nrd)  # ISO valida o esplode
                assert f.get("next_report_source"), f"{nome}/{tk}: data senza fonte"
                assert f.get("verificato_il"), \
                    f"{nome}/{tk}: data senza giorno di verifica"
                date.fromisoformat(f["verificato_il"])
    assert controllate, ("nessuna voce con next_report_date in nessuna mappa: "
                         "la regola della data nuda non e' stata misurata")


def test_negozio_ASSENTE_lascia_girare_l_igiene_sul_solo_esempio(tmp_path,
                                                                 monkeypatch):
    """Meta' dell'asimmetria: senza negozio l'igiene DEVE proseguire. E' il
    mondo del repo pubblico e di ogni clone, e un test che pretendesse il
    negozio del PM per passare non potrebbe starci."""
    import bellomberg.market_data.fonti_guidance as fg
    monkeypatch.setattr(fg, "PERCORSO_FONTI", str(tmp_path / "non_esiste.json"))
    mappe = _mappe_da_controllare()
    assert [n for n, _ in mappe] == ["esempio"], \
        "senza negozio resta l'esempio, e resta da solo"
    assert mappe[0][1], "l'esempio deve comunque portare voci da misurare"


def test_negozio_ILLEGGIBILE_ferma_l_igiene_invece_di_degradare(tmp_path,
                                                                monkeypatch):
    """L'altra meta', ed e' quella che mancava (fix round 1). Con un negozio
    CORROTTO i tre test d'igiene passavano su 8/4/4 assert — gli stessi numeri
    del mondo senza negozio — e nessuno diceva che il file del PM non si legge:
    un guasto degradato a assenza, cioe' il fallback silenzioso che la regola
    PM 14/07 vieta. La prova impone il negozio da se' (monkeypatch), quindi
    misura la stessa cosa nei due mondi, col negozio vero e senza."""
    import bellomberg.market_data.fonti_guidance as fg
    rotto = tmp_path / "fonti_guidance.json"
    rotto.write_text("{ questo non e' json", encoding="utf-8")
    monkeypatch.setattr(fg, "PERCORSO_FONTI", str(rotto))
    with pytest.raises(AssertionError) as caduta:
        _mappe_da_controllare()
    messaggio = str(caduta.value)
    assert "NON SI LEGGE" in messaggio, messaggio
    assert "JSONDecodeError" in messaggio, \
        "il motivo scritto da carica_fonti deve arrivare fino a chi legge"


# ---------- il caricatore: ogni buco DICHIARATO (regola 14/07) ----------

def test_negozio_assente_da_mappa_vuota_DICHIARATA(tmp_path):
    import bellomberg.market_data.fonti_guidance as fg
    r = fg.carica_fonti(path=str(tmp_path / "non_esiste.json"))
    assert r["fonti"] == {}
    assert r["origine"] == "assente"
    assert "non trovato" in r["motivo"], r["motivo"]


def test_negozio_illeggibile_e_un_ERRORE_non_una_mappa_vuota_zitta(tmp_path):
    import bellomberg.market_data.fonti_guidance as fg
    rotto = tmp_path / "rotto.json"
    rotto.write_text("{ questo non e' json", encoding="utf-8")
    r = fg.carica_fonti(path=str(rotto))
    assert r["fonti"] == {}
    assert r["origine"] == "illeggibile"
    assert r["motivo"], "un file rotto deve dire PERCHE'"


def test_negozio_buono_carica_le_voci_e_dichiara_l_origine(tmp_path):
    import bellomberg.market_data.fonti_guidance as fg
    buono = tmp_path / "buono.json"
    buono.write_text(json.dumps({
        "_leggimi": "ignorato",
        "ACME.MI": {"nome": "Acme", "tipo": "equity", "ir_url": "https://example.invalid/ir"},
    }), encoding="utf-8")
    r = fg.carica_fonti(path=str(buono))
    assert set(r["fonti"]) == {"ACME.MI"}, "le chiavi con _ non sono voci"
    assert r["origine"] == str(buono)
    assert r["motivo"] is None


def test_negozio_non_oggetto_e_illeggibile(tmp_path):
    """Un negozio che e' una lista (o un numero) NON deve passare per buono:
    senza questa guardia `grezzo.items()` esplode addosso al chiamante invece
    di dichiarare il buco."""
    import bellomberg.market_data.fonti_guidance as fg
    lista = tmp_path / "lista.json"
    lista.write_text("[1, 2, 3]", encoding="utf-8")
    r = fg.carica_fonti(path=str(lista))
    assert r["fonti"] == {}
    assert r["origine"] == "illeggibile"
    assert r["motivo"], "un negozio non-oggetto deve dire PERCHE'"
    assert "list" in r["motivo"], \
        "il motivo deve dire QUALE tipo ha trovato, come fanno gli altri rami"


def test_negozio_con_byte_non_utf8_e_illeggibile(tmp_path):
    """`except Exception` promette PIU' del JSON malformato, e l'ampiezza va
    misurata: un file che non e' UTF-8 solleva UnicodeDecodeError dentro
    json.load, non JSONDecodeError. Senza questa prova, stringere l'except al
    solo JSONDecodeError non farebbe cadere niente e meta' di cio' che la
    docstring dichiara si romperebbe in silenzio — il guasto tornerebbe a
    esplodere in faccia al chiamante invece di dichiararsi."""
    import bellomberg.market_data.fonti_guidance as fg
    binario = tmp_path / "binario.json"
    binario.write_bytes(b'{"ACME.MI": "\xff\xfe"}')
    r = fg.carica_fonti(path=str(binario))
    assert r["fonti"] == {}
    assert r["origine"] == "illeggibile"
    assert "UnicodeDecodeError" in r["motivo"], r["motivo"]


def test_negozio_che_e_una_cartella_e_illeggibile(tmp_path):
    """Stessa CLASSE di guasto del file binario, per un'altra via: cio' che
    `except Exception` copre non e' solo il JSON malformato, ma tutto quello che
    puo' andare storto nell'aprire e leggere il file — e in questa casa il file
    lockato da un altro processo non e' teorico.

    Il TIPO dell'eccezione dipende dal sistema (PermissionError su Windows,
    IsADirectoryError su POSIX): per questo la prova asserisce lo STATO
    dichiarato e la presenza del motivo, MAI il tipo. La dipendenza dalla
    piattaforma giustifica il non asserire il tipo, non lo scartare il caso."""
    import bellomberg.market_data.fonti_guidance as fg
    cartella = tmp_path / "negozio_cartella.json"
    cartella.mkdir()
    r = fg.carica_fonti(path=str(cartella))
    assert r["fonti"] == {}
    assert r["origine"] == "illeggibile"
    assert r["motivo"], "una cartella al posto del negozio deve dire PERCHE'"


# ---------- le interfacce che il Task 3 consumera' ----------

def test_fonti_correnti_da_le_sole_voci_e_RILEGGE_a_ogni_chiamata(tmp_path,
                                                                  monkeypatch):
    """La ragione per cui `fonti_correnti()` esiste: rilegge il negozio a ogni
    chiamata, mentre `FONTI` e' lo snapshot preso all'import. OGGI I DUE
    COINCIDONO — nessuno scrive il negozio mentre una run gira — e si separano
    solo se il file cambia a processo vivo: una modifica sul disco, o una fase
    futura che lo scrivesse durante una run. E' quella rilettura la proprieta'
    misurata qui, riscrivendo il file fra due chiamate e pretendendo che il
    valore cambi. (Prima diceva che la fase 2 SCRIVE il negozio durante una
    run: e' l'affermazione che il Task 3 ha gia' corretto nel modulo — il test
    era giusto, la motivazione scritta no.)"""
    import bellomberg.market_data.fonti_guidance as fg
    negozio = tmp_path / "negozio.json"
    negozio.write_text(json.dumps({
        "_leggimi": "ignorato",
        "ACME.MI": {"nome": "Acme", "tipo": "equity",
                    "ir_url": "https://example.invalid/ir"},
    }), encoding="utf-8")
    monkeypatch.setattr(fg, "PERCORSO_FONTI", str(negozio))

    assert set(fg.fonti_correnti()) == {"ACME.MI"}, "solo le voci, mai i commenti _"

    negozio.write_text(json.dumps({
        "ACME.MI": {"nome": "Acme", "tipo": "equity",
                    "ir_url": "https://example.invalid/ir"},
        "BETA.DE": {"nome": "Beta", "tipo": "equity",
                    "ir_url": "https://example.invalid/beta"},
    }), encoding="utf-8")
    assert set(fg.fonti_correnti()) == {"ACME.MI", "BETA.DE"}, \
        "fonti_correnti() deve RILEGGERE il negozio, non ricordare l'import"


def test_percorso_fonti_punta_al_negozio_dentro_data(monkeypatch):
    """Senza BELLOMBERG_DATA_DIR il negozio sta in `data/` accanto al modulo.
    La variabile viene TOLTA e il modulo ricaricato: senza questo il test era
    rosso su una configurazione che il progetto supporta (data dir altrove),
    cioe' una mina per chiunque imposti la variabile."""
    import bellomberg.market_data.fonti_guidance as fg
    prima = fg.PERCORSO_FONTI
    monkeypatch.delenv("BELLOMBERG_DATA_DIR", raising=False)
    try:
        import bellomberg.core.paths as paths
        importlib.reload(paths)
        importlib.reload(fg)
        assert os.path.isabs(fg.PERCORSO_FONTI), "mai un percorso relativo alla cwd"
        assert os.path.basename(fg.PERCORSO_FONTI) == "fonti_guidance.json"
        assert os.path.basename(os.path.dirname(fg.PERCORSO_FONTI)) == "data"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)
        importlib.reload(fg)
    assert fg.PERCORSO_FONTI == prima, "modulo non ripristinato"


def test_percorso_fonti_rispetta_BELLOMBERG_DATA_DIR(tmp_path, monkeypatch):
    """La variabile d'ambiente vince sulla cartella del modulo (il DB e' su un
    disco a parte via junction). Provata ricaricando il modulo: PERCORSO_FONTI
    si calcola all'import, quindi asserire solo sulla forma della stringa non
    misurerebbe il ramo `os.environ.get`.

    Il ripristino passa da `monkeypatch.undo()`, non da `delenv`: se
    l'ambiente aveva GIA' la variabile, toglierla lascerebbe il modulo sul
    percorso di default mentre il teardown rimette la variabile — modulo ed
    ambiente desincronizzati per il resto del processo. E lo si misura sul
    valore di partenza, non sull'assenza di tmp_path: quella era una garanzia
    dichiarata che passava anche quando il ripristino non era avvenuto."""
    import bellomberg.market_data.fonti_guidance as fg
    prima = fg.PERCORSO_FONTI
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", str(tmp_path))
    try:
        import bellomberg.core.paths as paths
        importlib.reload(paths)
        importlib.reload(fg)
        assert fg.PERCORSO_FONTI == os.path.join(str(tmp_path),
                                                 "fonti_guidance.json")
    finally:
        monkeypatch.undo()
        importlib.reload(paths)
        importlib.reload(fg)
    assert fg.PERCORSO_FONTI == prima, "modulo non ripristinato"


def test_origine_fonti_riporta_lo_stato_VERO_del_caricamento():
    """`ORIGINE_FONTI` si presenta come l'esito dell'ultimo caricamento: se
    fosse una frase fissa sarebbe un'affermazione falsa. Vale in ogni stato —
    col negozio assente dice 'assente' col motivo, col negozio a posto dice il
    percorso — perche' la prova confronta con un caricamento VIVO invece di
    fissare lo stato di oggi."""
    import bellomberg.market_data.fonti_guidance as fg
    assert set(fg.ORIGINE_FONTI) == {"origine", "motivo"}
    assert fg.ORIGINE_FONTI["origine"] != "non ancora caricato", \
        "ORIGINE_FONTI non e' mai stato caricato davvero"
    vivo = fg.carica_fonti()
    assert fg.ORIGINE_FONTI == {"origine": vivo["origine"],
                                "motivo": vivo["motivo"]}, \
        "ORIGINE_FONTI deve essere l'esito VERO del caricamento di PERCORSO_FONTI"


# ---------- il punto d'ingresso: `python fonti_guidance.py` ----------
#
# Il main chiamava `radar_fonti()` senza argomenti: quella funzione cicla la
# mappa che le passi e senza argomenti prende il NEGOZIO, cioe' stampava le
# posizioni di chi il negozio ce l'ha a chiunque lanci il comando. Il radar sul
# book di chi esegue e' `guidance_watch`, che le posizioni le legge dal DB.
#
# Tre cinture in queste prove:
#  · i messaggi di caduta NON riversano l'uscita del main — col main vecchio
#    quell'uscita E' il book, e il test che caccia la fuga non puo' esserne la
#    causa nel log di pytest. Nemmeno le CHIAVI si riportano: in una fuga a
#    mappa le chiavi di primo livello SONO i simboli del negozio;
#  · la prova di "non stampa le voci" gira su un negozio INVENTATO scritto dal
#    test: e' l'unico modo di sapere esattamente che cosa cercare nell'uscita
#    senza nominare un dato vero. Il solo `"ir_url" not in` misura la presenza
#    di un nome di campo, non l'assenza delle voci;
#  · il comando si prova anche CON un argomento: la fuga vecchia passava da
#    `sys.argv[1]`, e un radar rimesso li' sopravvivrebbe a ogni prova che
#    lancia il comando nudo.

CHIAVI_MAIN = ["come_si_usa", "motivo", "origine", "voci_nel_negozio"]


def _radice_del_modulo():
    """La cartella dove sta `fonti_guidance.py`: la radice del repo quando i
    test girano da `tests/`, la cartella stessa quando la batteria gira nel
    layout PIATTO del banco delle mutazioni. Si CERCA, non si deduce da
    `__file__` con due dirname: quel calcolo ha gia' rotto il banco al Task 4.
    Si guarda PRIMA accanto al test e poi il livello sopra: nel layout piatto
    il livello sopra e' la temp di sistema, dove un `fonti_guidance.py`
    estraneo farebbe eseguire al sottoprocesso il modulo sbagliato. Quest'ordine
    e' corretto in entrambi i layout — nel repo `tests/` non contiene il
    modulo, quindi la ricerca scende comunque sulla radice."""
    qui = os.path.dirname(os.path.abspath(__file__))
    for cand in (qui, os.path.dirname(qui)):
        if os.path.exists(os.path.join(cand, "fonti_guidance.py")):
            return cand
    raise AssertionError("fonti_guidance.py non trovato ne' in %s ne' in %s"
                         % (qui, os.path.dirname(qui)))


def _lancia_il_main(data_dir=None, argomenti=()):
    """Lancia `python fonti_guidance.py` come lo lancia una persona, in un
    processo a parte. Con `data_dir` il negozio su cui gira e' quello scelto
    dal test (BELLOMBERG_DATA_DIR); senza, e' quello della macchina.
    `argomenti` sono quelli di riga di comando, che il main non deve leggere."""
    amb = dict(os.environ)
    amb["PYTHONPATH"] = os.path.join(REPO, "src")
    if data_dir is not None:
        amb["BELLOMBERG_DATA_DIR"] = str(data_dir)
    r = subprocess.run([sys.executable, "-m", "bellomberg.market_data.fonti_guidance"]
                       + list(argomenti), cwd=REPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=amb)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _descrivi_senza_riversare(rc, out, err):
    """Descrive l'uscita del main SENZA copiarla, NEMMENO le chiavi. Dire "le
    chiavi sono letterali del sorgente" era falso proprio nel caso che queste
    prove difendono: se il main stampasse la mappa, le chiavi di primo livello
    sarebbero i simboli del negozio. Escono solo le chiavi ATTESE che sono
    comparse, e QUANTE ne sono comparse di ignote."""
    testa = "rc=%s, stdout=%d caratteri, stderr=%d caratteri" % (rc, len(out), len(err))
    try:
        letto = json.loads(out)
    except Exception as e:
        return "%s, uscita non JSON (%s)" % (testa, type(e).__name__)
    if not isinstance(letto, dict):
        return "%s, JSON ma non un oggetto (%s)" % (testa, type(letto).__name__)
    attese = sorted(set(letto) & set(CHIAVI_MAIN))
    return ("%s, chiavi attese presenti=%s, chiavi ignote=%d"
            % (testa, attese, len(set(letto) - set(CHIAVI_MAIN))))


def test_il_main_del_modulo_non_stampa_la_mappa():
    """`python fonti_guidance.py` mostrava il book di chi ha il negozio: il
    radar vero e' guidance_watch, che filtra sulle posizioni di chi esegue.
    Gira sull'ambiente VERO della macchina — col negozio o senza, il main deve
    comportarsi allo stesso modo."""
    rc, out, err = _lancia_il_main()
    nota = _descrivi_senza_riversare(rc, out, err)
    assert rc == 0, "il main deve uscire pulito: " + nota
    try:
        dati = json.loads(out)
    except Exception:
        pytest.fail("il main deve stampare un JSON solo: " + nota)
    # Gli esiti si calcolano PRIMA dell'assert: `assert "ir_url" not in out`
    # farebbe stampare `out` a pytest, cioe' proprio il dato che difendiamo, e
    # `assert sorted(dati) == CHIAVI_MAIN` stamperebbe le chiavi — che in una
    # fuga a mappa sono i simboli del negozio. La cintura non puo' dipendere
    # dall'ordine in cui cadono gli assert.
    forma_ok = sorted(dati) == CHIAVI_MAIN
    assert forma_ok, \
        "il main deve stampare lo STATO del negozio, non il suo contenuto: " + nota
    manda_al_radar = "guidance_watch" in out
    assert manda_al_radar, "il main deve mandare al radar vero: " + nota
    stampa_le_voci = ("ir_url" in out) or ("ir_url" in err)
    assert not stampa_le_voci, \
        "il main NON deve stampare le voci della mappa: " + nota
    import bellomberg.market_data.fonti_guidance as fg
    conta_misurata = dati["voci_nel_negozio"] == len(fg.fonti_correnti())
    assert conta_misurata, \
        "la conta dichiarata dal main non e' quella del negozio: " + nota


def test_il_main_non_rimette_il_radar_dietro_un_argomento():
    """La fuga vecchia passava da `sys.argv[1]` (la finestra in giorni): un
    radar rimesso li' — stato senza argomenti, mappa CON l'argomento —
    passerebbe ogni prova che lancia il comando nudo, e `python
    fonti_guidance.py 14` tornerebbe a stampare il book. Qui l'uscita deve
    essere IDENTICA con e senza argomenti: il main non li legge."""
    rc, out, err = _lancia_il_main()
    rc_arg, out_arg, err_arg = _lancia_il_main(argomenti=["14"])
    identica = (rc_arg, out_arg, err_arg) == (rc, out, err)
    assert identica, (
        "l'argomento cambia l'uscita del main — senza: %s | con: %s"
        % (_descrivi_senza_riversare(rc, out, err),
           _descrivi_senza_riversare(rc_arg, out_arg, err_arg)))
    # Ridondante finche' l'uscita e' identica, e voluta: misura la fuga di
    # petto anche se un domani l'identita' venisse allentata.
    stampa_le_voci = ("ir_url" in out_arg) or ("ir_url" in err_arg)
    assert not stampa_le_voci, ("col parametro il main stampa le voci: "
                                + _descrivi_senza_riversare(rc_arg, out_arg, err_arg))


def _db_inventato(cartella):
    """Un DB col nome che `memory_db` cerca dentro BELLOMBERG_DATA_DIR, con UNA
    posizione e UN preferito INVENTATI. Le colonne di `company_guidance` sono
    quelle che il grilletto legge: con lo schema ridotto il radar cadrebbe su
    «no such column: id», cioe' per un motivo che con la frase non c'entra."""
    percorso = os.path.join(str(cartella), "consigliere.db")
    con = sqlite3.connect(percorso)
    con.executescript("""
        CREATE TABLE positions (ticker TEXT, nome TEXT, quantita REAL);
        CREATE TABLE favorite_companies (ticker TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE company_guidance (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, metric TEXT,
            period TEXT, unit TEXT, status TEXT, valid_until TEXT,
            valid_until_source TEXT, source_date TEXT, note TEXT);
        INSERT INTO positions VALUES ('ACME.MI', 'Acme SpA', 10);
        INSERT INTO favorite_companies VALUES ('ZETA.DE', 'Zeta AG');
    """)
    con.commit()
    con.close()
    return percorso


def test_il_radar_indicato_dal_main_CICLA_DAVVERO_il_DB(tmp_path):
    """`come_si_usa` afferma una cosa su un ALTRO modulo, e una frase e' vera
    finche' qualcosa la lega. Fino al 04/09 il legame era `"FROM positions"`
    cercato nel SORGENTE del radar: un proxy scelto da questo test, non il
    significato. MISURATO al Task 5: cambiando la frase in una bugia («cicla i
    PREFERITI, non le posizioni») il test passava lo stesso. E dal Task 6 la
    frase dice posizioni **e** preferiti, quindi quel proxy sarebbe passato
    mentre la domanda si era spostata.

    Ora la misura e' sull'ESECUZIONE: si costruisce un DB temporaneo con una
    posizione e un preferito INVENTATI, si lancia il comando che la frase
    nomina, e si pretende che compaiano ENTRAMBI nella sua uscita. Cade se il
    radar smette di leggere una delle due tabelle, se il comando non esiste
    piu' o se la frase manda altrove.

    LIMITE DICHIARATO: la GRAMMATICA della frase non si analizza. Si pretende
    che nomini un comando, che prometta entrambe le cose (le due parole) e che
    il comando le faccia davvero; una frase che le nominasse negandole
    passerebbe. Il difetto che resta e' di documentazione, non di radar.

    Nessun dato vero in ballo (data-dir e DB sono dentro tmp_path, negozio
    assente): i messaggi possono riportare l'uscita."""
    rc, out, err = _lancia_il_main(tmp_path)
    assert rc == 0, out + err
    frase = json.loads(out)["come_si_usa"]
    nomi = re.findall(r"python -m ([A-Za-z0-9_.]+)", frase)
    assert nomi, "come_si_usa deve nominare il comando del radar: %r" % frase
    bassa = frase.lower()
    assert "posizion" in bassa and "preferit" in bassa, \
        ("la frase deve promettere ENTRAMBE le meta' dell'universo, che sono "
         "quelle misurate qui sotto: %r" % frase)

    _db_inventato(tmp_path)
    amb = dict(os.environ, BELLOMBERG_DATA_DIR=str(tmp_path),
               PYTHONPATH=os.path.join(REPO, "src"))
    for nome in nomi:
        r = subprocess.run([sys.executable, "-m", nome, "7"],
                           cwd=REPO, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=amb)
        assert r.returncode == 0, \
            ("%s non e' lanciabile da riga di comando (rc=%s, %d caratteri su "
             "stderr)" % (nome, r.returncode, len(r.stderr or "")))
        try:
            copertura = json.loads(r.stdout or "")["copertura"]
        except Exception as e:
            pytest.fail("%s non ha stampato il JSON del radar (%s)"
                        % (nome, type(e).__name__))
        # PRIMA la cintura, e con un messaggio fatto di SOLI NUMERI: se il
        # comando avesse aperto un DB diverso da quello inventato qui, il
        # messaggio di caduta non deve riversarne il contenuto (e' il difetto
        # M1 trovato dal revisore al Task 5 — l'assert che difende un dato e
        # poi lo stampa). Passata questa, le chiavi sono i due simboli
        # inventati e i messaggi seguenti possono nominarli.
        solo_inventati = sorted(copertura["provenienza"]) == ["ACME.MI", "ZETA.DE"]
        assert solo_inventati, \
            ("%s ha ciclato un universo che non e' quello inventato dal test "
             "(%d ticker, %d posizioni, %d preferiti): il DB letto non e' il "
             "temporaneo" % (nome, copertura["universo"], copertura["book"],
                             copertura["preferiti"]))
        assert copertura["provenienza"]["ACME.MI"] == "posizione", \
            "%s non ha ciclato le POSIZIONI del DB: il main lo promette" % nome
        assert copertura["provenienza"]["ZETA.DE"] == "preferito", \
            "%s non ha ciclato i PREFERITI del DB: il main lo promette" % nome


def test_il_main_dichiara_il_negozio_senza_stamparne_le_voci(tmp_path):
    """La prova che l'uscita non contiene le VOCI, non solo che non contiene la
    stringa 'ir_url'. Il negozio lo scrive il test, con simboli inventati:
    quindi sa esattamente che cosa non deve comparire, e questi messaggi di
    caduta possono riportare l'uscita per intero."""
    negozio = tmp_path / "fonti_guidance.json"
    negozio.write_text(json.dumps({
        "ACME.MI": {"nome": "Acme SpA", "tipo": "equity",
                    "ir_url": "https://acme.invalid/investors",
                    "next_report_date": "2026-12-01",
                    "next_report_source": "calendario IR acme.invalid",
                    "verificato_il": "2026-09-01"},
        "ZETA.DE": {"nome": "Zeta AG", "tipo": "equity",
                    "ir_url": "https://zeta.invalid/ir"},
    }, indent=2), encoding="utf-8")
    rc, out, err = _lancia_il_main(tmp_path)
    testo = out + err
    assert rc == 0, testo
    dati = json.loads(out)
    assert sorted(dati) == CHIAVI_MAIN, testo
    assert dati["origine"] == str(negozio), "il main deve dire DA DOVE ha letto"
    assert dati["motivo"] is None
    assert dati["voci_nel_negozio"] == 2, "la conta e' una misura del negozio"
    for pezzo in ("ACME.MI", "ZETA.DE", "Acme SpA", "Zeta AG", "acme.invalid",
                  "zeta.invalid", "ir_url", "next_report_date", "2026-12-01"):
        assert pezzo not in testo, \
            "il main ha stampato %r, cioe' una voce del negozio" % pezzo


def test_il_main_senza_negozio_dichiara_l_assenza_col_motivo(tmp_path):
    """Mai un'uscita muta e mai un ripiego zitto (regola PM 14/07): dove il
    negozio non c'e' — un clone qualsiasi, il repo pubblico — il main lo DICE,
    col percorso in cui ha cercato, e manda comunque al radar vero."""
    rc, out, err = _lancia_il_main(tmp_path)          # cartella vuota
    testo = out + err
    assert rc == 0, testo
    dati = json.loads(out)
    assert sorted(dati) == CHIAVI_MAIN, testo
    assert dati["origine"] == "assente"
    assert dati["motivo"], "l'assenza va dichiarata col motivo, non con un None"
    assert str(tmp_path) in dati["motivo"], "il motivo deve dire DOVE ha cercato"
    assert dati["voci_nel_negozio"] == 0
    assert "bellomberg.market_data.guidance_watch" in dati["come_si_usa"]
