"""La SPIA DELLE SCRITTURE sa vedere cio' che promette di vedere? (22/08 sera-2)

Voce (1b) del MASTER, FASE A (ok PM «misura, poi il numero»): prima di
installare un tripwire sulle scritture dei test, la spia che lo alimenta deve
essere collaudata da sola — una guardia che non scatta e' peggio di nessuna
guardia (lezione «guardia che si accusa da sola» e «una garanzia dichiarata
dev'essere una misura»). Questi test NON toccano la produzione: la radice
sorvegliata e' una cartella in tmp_path che IMITA il repo (data/, report/,
research_notes/, portfolio.json).

Cosa deve vedere, piu' di `prova_edge_scan.SpiaFile` (22/08):
  - `io.open` e `pathlib.Path.write_text/open`: `io.open` e' un NOME diverso
    da `builtins.open` (stesso oggetto all'import, ma patchare uno non cambia
    l'altro) — e' la via degli zip dei backup e di `market_inputs`;
  - le CANCELLAZIONI (`os.remove`/`os.unlink`/`os.rmdir`): cancellare un file
    sotto data/ e' una scrittura;
  - `portfolio.json` in RADICE, fuori dai tre prefissi censiti.
LIMITE DICHIARATO (e testato come tale): `os.open` a basso livello e chi
scrive da C (sqlite, chromadb) non passano di qui — sqlite ha il suo
tripwire in conftest.
"""
import io
import os
import sys
import pathlib
import tempfile

import pytest

from _spia_scritture import SpiaScritture


@pytest.fixture
def radice_finta(tmp_path):
    """Un finto repo con la stessa forma del vero: data/ report/ research_notes/
    e portfolio.json in radice. Tutto dentro tmp_path, quindi fuori dalla
    produzione per costruzione."""
    r = tmp_path / "repo_finto"
    for d in ("data", "report", "research_notes", "altro"):
        (r / d).mkdir(parents=True)
    (r / "portfolio.json").write_text("{}", encoding="utf-8")
    (r / "data" / "esiste.txt").write_text("x", encoding="utf-8")
    return r


@pytest.fixture
def spia(radice_finta):
    """Spia installata sulla radice finta; il tmp dei test NON e' esentato
    perche' qui la radice finta STA nel tmp — la prova di esenzione e' a parte."""
    s = SpiaScritture(radice=str(radice_finta), esenti=[])
    s.installa()
    try:
        yield s
    finally:
        s.disinstalla()


def test_vede_open_in_scrittura_sotto_data(spia, radice_finta):
    with open(radice_finta / "data" / "nuovo.txt", "w", encoding="utf-8") as fh:
        fh.write("x")
    assert len(spia.scritture) == 1
    ev = spia.scritture[0]
    assert ev["op"] == "open" and ev["modo"] == "w"
    assert ev["percorso"].lower().endswith(os.path.join("data", "nuovo.txt"))


def test_ignora_le_letture(spia, radice_finta):
    with open(radice_finta / "data" / "esiste.txt", encoding="utf-8") as fh:
        fh.read()
    assert spia.scritture == []


def test_ignora_cio_che_sta_fuori_dagli_alberi_sorvegliati(spia, radice_finta):
    with open(radice_finta / "altro" / "libero.txt", "w", encoding="utf-8") as fh:
        fh.write("x")
    assert spia.scritture == []


def test_vede_io_open_che_sfugge_a_builtins_open(spia, radice_finta):
    """`io.open` e' il nome che `zipfile`, `market_inputs` e i backup usano:
    patchare SOLO builtins.open lo lascia passare (misurato sul 22/08)."""
    with io.open(radice_finta / "report" / "memo.md", "w", encoding="utf-8") as fh:
        fh.write("x")
    assert [e["op"] for e in spia.scritture] == ["open"]


def test_vede_pathlib_write_text(spia, radice_finta):
    (radice_finta / "research_notes" / "n.md").write_text("x", encoding="utf-8")
    assert len(spia.scritture) == 1


def test_vede_makedirs_replace_e_cancellazioni(spia, radice_finta):
    os.makedirs(radice_finta / "data" / "cache_nuova")
    src = radice_finta / "altro" / "tmp.txt"
    src.write_text("x", encoding="utf-8")          # fuori: non conta
    os.replace(src, radice_finta / "data" / "arrivato.txt")
    os.remove(radice_finta / "data" / "esiste.txt")
    os.rmdir(radice_finta / "data" / "cache_nuova")
    ops = [e["op"] for e in spia.scritture]
    assert ops == ["makedirs", "replace", "remove", "rmdir"], ops


def test_portfolio_json_in_radice_e_sorvegliato(spia, radice_finta):
    """Sta in RADICE, fuori dai tre prefissi: e' il file che il backend
    riscrive (cassa) e che un test distratto potrebbe toccare."""
    with open(radice_finta / "portfolio.json", "w", encoding="utf-8") as fh:
        fh.write("{}")
    assert len(spia.scritture) == 1
    assert spia.scritture[0]["percorso"].lower().endswith("portfolio.json")


def test_il_tmp_esente_non_viene_registrato(radice_finta, tmp_path):
    """Con l'esenzione del tmp (come in produzione), una scrittura sotto una
    cartella esente NON conta anche se sta sotto la radice sorvegliata."""
    esente = radice_finta / "data" / "tmp_esente"
    esente.mkdir()
    s = SpiaScritture(radice=str(radice_finta), esenti=[str(esente)])
    s.installa()
    try:
        (esente / "x.txt").write_text("x", encoding="utf-8")
        (radice_finta / "data" / "y.txt").write_text("y", encoding="utf-8")
    finally:
        s.disinstalla()
    assert [os.path.basename(e["percorso"]) for e in s.scritture] == ["y.txt"]


@pytest.mark.skipif(sys.platform != "win32",
                    reason="misura la case-insensitivity di NTFS: su ext4 il path maiuscolo non esiste (CI rossa 31/08)")
def test_maiuscole_e_slash_diversi_non_la_ingannano(spia, radice_finta):
    """Windows: `C:\\DEV\\...` e `c:/dev/...` sono lo stesso posto."""
    p = str(radice_finta / "data" / "caso.txt").upper().replace("\\", "/")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("x")
    assert len(spia.scritture) == 1


def test_registra_il_test_corrente(spia, radice_finta):
    """Il rapporto deve dire CHI ha scritto: il nodeid del test in corso."""
    spia.test_corrente = "tests/test_x.py::test_y"
    (radice_finta / "data" / "z.txt").write_text("x", encoding="utf-8")
    assert spia.scritture[0]["test"] == "tests/test_x.py::test_y"


def test_disinstalla_ripristina_gli_originali(radice_finta):
    orig = (open, io.open, os.makedirs, os.replace, os.remove, os.unlink, os.rmdir)
    s = SpiaScritture(radice=str(radice_finta), esenti=[])
    s.installa()
    assert open is not orig[0]
    s.disinstalla()
    import builtins
    assert (builtins.open, io.open, os.makedirs, os.replace, os.remove,
            os.unlink, os.rmdir) == orig


def test_il_rapporto_raggruppa_per_file_e_conta_i_test(spia, radice_finta):
    spia.test_corrente = "t1"
    (radice_finta / "data" / "a.txt").write_text("x", encoding="utf-8")
    spia.test_corrente = "t2"
    (radice_finta / "data" / "a.txt").write_text("y", encoding="utf-8")
    (radice_finta / "report" / "b.md").write_text("z", encoding="utf-8")
    r = spia.rapporto()
    assert "2 test" in r and "a.txt" in r and "b.md" in r
    assert "3 scritture" in r


def test_la_spia_di_SESSIONE_e_installata_e_sa_chi_sono(request):
    """Il CANARINO della Fase A: lo «zero scritture» stampato a fine suite vale
    solo se la spia del conftest era ACCESA durante i test e attribuiva le
    scritture al test giusto. Senza questo, un conftest che smette di
    installarla stampa «0 scritture» con la faccia di una misura (lezione
    «una garanzia dichiarata dev'essere una misura»). Verificato a mano il
    22/08 sera-2: senza `_SPIA.installa()` questo test CADE."""
    import builtins
    from tests import conftest
    # la fixture `spia` di questo file NON e' attiva qui: quella che vediamo
    # e' la spia di sessione del conftest
    assert conftest._SPIA._installata, "la spia di sessione non e' installata"
    assert builtins.open.__name__ == "open_spia", (
        "builtins.open non e' la spia: %r" % builtins.open)
    assert io.open is builtins.open, "io.open e builtins.open divergono: io.open sfugge"
    assert conftest._SPIA.test_corrente == request.node.nodeid, (
        "la spia non sa quale test sta girando: %r" % conftest._SPIA.test_corrente)
    assert os.path.normcase(conftest._SPIA.radice) == os.path.normcase(
        os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_in_modalita_tripwire_la_scrittura_solleva_PRIMA_di_avvenire(radice_finta):
    """FASE B (ok PM 22/08 sera-2): il test che scrive in produzione FALLISCE,
    e il file NON nasce — si solleva prima di chiamare l'originale. Il messaggio
    dice file, test e cura. E l'evento resta nel rapporto (si vede cosa ha
    fatto scattare il tripwire)."""
    from _spia_scritture import ScritturaInProduzione
    s = SpiaScritture(radice=str(radice_finta), esenti=[], tripwire=True)
    s.installa()
    try:
        s.test_corrente = "tests/test_x.py::test_colpevole"
        bersaglio = radice_finta / "data" / "mai_nato.txt"
        with pytest.raises(ScritturaInProduzione) as ei:
            open(bersaglio, "w", encoding="utf-8")
        assert not bersaglio.exists(), "il file e' stato scritto PRIMA di sollevare"
        msg = str(ei.value)
        assert "mai_nato.txt" in msg and "test_colpevole" in msg
        assert "tmp_path" in msg, "il messaggio non dice la cura"
        assert len(s.scritture) == 1 and s.scritture[0]["op"] == "open"
    finally:
        s.disinstalla()


def test_il_tripwire_passa_attraverso_except_Exception(radice_finta):
    """Come `ProduzioneToccata` del DB: deriva da BaseException, perche' i punti
    che scrivono spesso stanno dentro `try/except Exception: pass` — un'eccezione
    normale verrebbe INGHIOTTITA e il tripwire sarebbe muto."""
    from _spia_scritture import ScritturaInProduzione
    assert issubclass(ScritturaInProduzione, BaseException)
    assert not issubclass(ScritturaInProduzione, Exception)
    s = SpiaScritture(radice=str(radice_finta), esenti=[], tripwire=True)
    s.installa()
    try:
        with pytest.raises(ScritturaInProduzione):
            try:
                os.makedirs(radice_finta / "data" / "inghiottita")
            except Exception:
                pass
        assert not (radice_finta / "data" / "inghiottita").exists()
    finally:
        s.disinstalla()


def test_in_modalita_registra_non_solleva(spia, radice_finta):
    """La fixture `spia` e' in REGISTRA (default esplicito): registra e lascia
    fare — e' la Fase A, che resta disponibile via BELLOMBERG_SPIA_SCRITTURE."""
    assert spia.tripwire is False
    (radice_finta / "data" / "passa.txt").write_text("x", encoding="utf-8")
    assert len(spia.scritture) == 1


def test_la_spia_di_SESSIONE_e_in_tripwire(request):
    """Il canarino della Fase B: lo stato vero della suite. Salvo
    BELLOMBERG_SPIA_SCRITTURE=registra (la leva per tornare alla sola misura,
    dichiarata nel rapporto), la spia di sessione MORDE."""
    from tests import conftest
    atteso = os.environ.get("BELLOMBERG_SPIA_SCRITTURE", "tripwire") != "registra"
    assert conftest._SPIA.tripwire is atteso


def test_il_limite_os_open_e_dichiarato_nel_rapporto(spia, radice_finta):
    """`os.open` a basso livello NON passa dalla spia: il rapporto lo dice,
    perche' un «zero» che non nomina cio' che non vede e' una bugia."""
    fd = os.open(str(radice_finta / "data" / "basso.txt"),
                 os.O_WRONLY | os.O_CREAT)
    os.close(fd)
    assert spia.scritture == [], "se un giorno la vede, aggiorna il limite dichiarato"
    assert "os.open" in spia.rapporto()
