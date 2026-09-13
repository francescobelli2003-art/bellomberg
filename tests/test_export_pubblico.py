# -*- coding: utf-8 -*-
"""P2/T7 (03/09, pubblicazione): l'export end-to-end. Batteria PUBBLICA: repo git FINTI in
`tmp_path`, nomi finti («Mario Rossi»), nessun percorso e nessun dato del PM. Non tocca il repo
vero, non scrive fuori dalla cartella temporanea e non pusha MAI: il push e' P4 e lo ordina il PM.
"""
import os
import json
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
from tools.release import export_pubblico as ep  # noqa: E402

NOREPLY = "123+mrossi@users.noreply.github.com"


def _cancello_sano(*_a, **_k):
    return ep.RisultatoCancello(0, {
        "fonti_ko": {}, "conteggi": {"payload_canali_guasti": 0},
        "sha256_corpus_privato": "a" * 64, "sha256_payload": "b" * 64,
    }, {
        "rigoroso": True, "non_eseguiti": [],
        "controlli": [{"nome": n, "hit": 0, "errore": False, "osservazione": False}
                      for n in ep.vp.CONTROLLI],
    })


def _git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True,
                          encoding="utf-8", errors="replace", check=True).stdout


@pytest.fixture
def repo_finto(tmp_path):
    """Un repo git minimo che somiglia al privato: due file che escono, uno che resta."""
    d = tmp_path / "privato"
    (d / "tests").mkdir(parents=True)
    (d / "capo.py").write_text("X = 1\n", encoding="utf-8")
    (d / "prova_x.py").write_text("PRIVATO = 2\n", encoding="utf-8")
    (d / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    _git(d, "init", "-q")
    _git(d, "config", "user.name", "Mario Rossi")
    _git(d, "config", "user.email", NOREPLY)
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "iniziale")
    return d


@pytest.fixture(autouse=True)
def registro_finto(tmp_path_factory, monkeypatch):
    """D6 (04/09): `pubblica_in` misura la storia contro `tools/release/policy/STORIA.txt` e vi
    APPENDE l'hash depositato. Quel file e' il registro VERO del PM: qui se ne monta uno finto,
    per ogni test, cosi' nessun deposito finto viene registrato in produzione. Sta in una cartella
    SUA, non in tmp_path: tre test usano tmp_path come tree e ne contano i file."""
    p = tmp_path_factory.mktemp("registro") / "STORIA_finta.txt"
    p.write_text("# registro finto\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REGISTRO", str(p))
    return p


@pytest.fixture
def clone_pubblico(tmp_path, registro_finto):
    """Un clone del pubblico con UN commit gia' depositato: il suo hash sta nel registro finto,
    come starebbe nel vero dopo un sync (la storia estranea e' provata in test_export_pubblico_storia)."""
    d = tmp_path / "pubblico"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.name", "Mario Rossi")
    _git(d, "config", "user.email", NOREPLY)
    (d / "vecchio.txt").write_text("da cancellare\n", encoding="utf-8")
    _git(d, "add", "vecchio.txt")
    _git(d, "commit", "-q", "-m", "iniziale")
    with open(str(registro_finto), "a", encoding="utf-8") as fh:
        fh.write("%s  # iniziale\n" % _git(d, "rev-parse", "HEAD").strip())
    return d


@pytest.fixture
def allowlist_finta(tmp_path):
    p = tmp_path / "ALLOWLIST.txt"
    p.write_text("*.py\n!prova_*.py\ntests/*.py\n", encoding="utf-8")
    return p


@pytest.fixture
def vietate_finte(tmp_path, monkeypatch):
    """`autore_pubblico` legge `tools/release/policy/VIETATE.txt`, che sta nel privato: la batteria e'
    pubblica e nel tree esportato quel file non c'e'. Qui se ne monta una finta."""
    d = tmp_path / "pubblico_finto"
    d.mkdir()
    (d / "VIETATE.txt").write_text("# finta\nCloudDriveX\n", encoding="utf-8")
    monkeypatch.setattr(ep.vp, "PUBBLICO", str(d))
    return d


def test_copia_in_temp_conserva_i_byte_e_l_albero(repo_finto, tmp_path):
    """copy2: i .bat del progetto restano CRLF anche nel tree pubblico."""
    (repo_finto / "run.bat").write_bytes(b"@echo off\r\nexit /b 0\r\n")
    dest = tmp_path / "temp"
    n = ep.copia_in_temp(str(repo_finto), ["capo.py", "tests/test_a.py", "run.bat"], str(dest))
    assert (dest / "run.bat").read_bytes() == b"@echo off\r\nexit /b 0\r\n"
    assert (dest / "tests" / "test_a.py").exists() and not (dest / "prova_x.py").exists()
    assert n == sum(os.path.getsize(os.path.join(str(repo_finto), f.replace("/", os.sep)))
                    for f in ("capo.py", "tests/test_a.py", "run.bat"))


@pytest.mark.parametrize("opzioni", [
    ["--commit", "--solo", "vietate"],
    ["--commit"],
])
def test_deposito_rifiuta_opzioni_non_certificabili_prima_di_copiare(
        repo_finto, clone_pubblico, allowlist_finta, vietate_finte,
        monkeypatch, opzioni):
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    chiamate = []
    monkeypatch.setattr(ep, "copia_in_temp", lambda *_a, **_k: chiamate.append("copia") or 0)
    monkeypatch.setattr(ep, "hash_artefatto", lambda *_a: ("a" * 64, 2))
    monkeypatch.setattr(ep, "_cancello", lambda *_a, **_k: 0)
    monkeypatch.setattr(ep, "esegui_suite", lambda *_a, **_k: (0, "suite finta verde"))
    monkeypatch.setattr(ep, "pubblica_in", lambda *_a, **_k: chiamate.append("deposito"))
    args = opzioni + ["--corpus-root", str(repo_finto)]
    if "--solo" in opzioni:
        args += ["--dest", str(clone_pubblico)]
    assert ep.main(args) == 2
    assert chiamate == []


def test_deposito_completo_installa_guardia_e_certifica_push_locale(
        repo_finto, clone_pubblico, allowlist_finta, vietate_finte, monkeypatch, tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q")
    _git(clone_pubblico, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", _cancello_sano)
    assert ep.main(["--dest", str(clone_pubblico), "--commit",
                    "--corpus-root", str(repo_finto)]) == 0
    certificate = clone_pubblico / ".git" / "bellomberg-release" / "certificate.json"
    assert json.loads(certificate.read_text())["manifest"]["verifica"]["stato"] == "OK"
    branch = _git(clone_pubblico, "symbolic-ref", "--short", "HEAD").strip()
    _git(clone_pubblico, "push", "origin", branch)
    assert _git(remote, "rev-parse", branch) == _git(clone_pubblico, "rev-parse", "HEAD")


def test_exit_zero_senza_prove_complete_non_autorizza_il_deposito(
        repo_finto, clone_pubblico, allowlist_finta, vietate_finte, monkeypatch):
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda *_a, **_k: 0)
    monkeypatch.setattr(ep, "esegui_suite", lambda *_a, **_k: (0, "synthetic suite"))
    called = []
    monkeypatch.setattr(ep, "pubblica_in", lambda *_a, **_k: called.append(True))
    assert ep.main(["--dest", str(clone_pubblico), "--commit",
                    "--corpus-root", str(repo_finto)]) == 2
    assert called == []


def test_hash_artefatto_e_stabile_e_cambia_coi_byte(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.py").write_bytes(b"A\r\n")
    primo = ep.hash_artefatto(str(tree))
    assert ep.hash_artefatto(str(tree)) == primo
    (tree / "a.py").write_bytes(b"B\r\n")
    assert ep.hash_artefatto(str(tree))[0] != primo[0]


def test_manifest_separa_hash_corpus_e_payload_senza_valori_privati(tmp_path):
    path = tmp_path / "manifest.json"
    input_manifest = {
        "sha256_corpus_privato": "a" * 64,
        "sha256_payload": "b" * 64,
        "hash_liste": {"VIETATE.txt": "c" * 64},
        "conteggi": {"ticker": 2},
        "fonti_assenti": ["lista:SIMBOLI.txt"],
        "fonti_ko": {},
    }
    ep.scrivi_manifest(
        str(path), "d" * 40, "e" * 64, 3, input_manifest, 0, 0,
        esiti_manifest={"rigoroso": True, "controlli": [], "non_eseguiti": []})
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    assert doc["input"]["sha256_corpus_privato"] != doc["input"]["sha256_payload"]
    assert doc["sorgente"]["commit"] == "d" * 40
    assert doc["artefatto"]["sha256"] == "e" * 64
    assert doc["verifica"]["stato"] == "OK"
    assert doc["verifica"]["rigoroso"] is True


def test_manifest_dichiara_incompleto_se_manca_una_prova(tmp_path):
    path = tmp_path / "manifest.json"
    doc = ep.scrivi_manifest(
        str(path), "d" * 40, "e" * 64, 3,
        {"sha256_corpus_privato": "a" * 64, "sha256_payload": "b" * 64,
         "hash_liste": {}, "conteggi": {}, "fonti_assenti": [], "fonti_ko": {}},
        0, 0, senza_suite=True)
    assert doc["verifica"]["stato"] == "INCOMPLETO"


def test_manifest_dichiara_incompleto_se_un_canale_payload_e_guasto(tmp_path):
    path = tmp_path / "manifest.json"
    doc = ep.scrivi_manifest(
        str(path), "d" * 40, "e" * 64, 3,
        {"sha256_corpus_privato": "a" * 64, "sha256_payload": "b" * 64,
         "hash_liste": {}, "conteggi": {"payload_canali_guasti": 1},
         "fonti_assenti": [], "fonti_ko": {}},
        2, 0)
    assert doc["verifica"]["stato"] == "INCOMPLETO"


def test_manifest_permissivo_non_dice_ok_e_hash_corrente_e_quello_post_suite(tmp_path):
    path = tmp_path / "manifest.json"
    doc = ep.scrivi_manifest(
        str(path), "d" * 40, "e" * 64, 3,
        {"sha256_corpus_privato": "a" * 64, "sha256_payload": "b" * 64,
         "hash_liste": {}, "conteggi": {}, "fonti_assenti": [], "fonti_ko": {}},
        0, 2,
        esiti_manifest={
            "rigoroso": False,
            "controlli": [{"nome": "ticker_soli", "hit": 316,
                            "errore": False, "osservazione": True}],
            "non_eseguiti": []},
        sha_artefatto_post="f" * 64, n_file_post=4)
    assert doc["verifica"]["stato"] == "INCOMPLETO"
    assert doc["verifica"]["controlli"][0]["hit"] == 316
    assert doc["artefatto"] == {
        "sha256": "f" * 64, "file": 4,
        "sha256_prima_suite": "e" * 64, "file_prima_suite": 3,
        "immutato_dalla_suite": False,
    }


def test_modificati_vede_lo_sporco_solo_sui_file_scelti(repo_finto):
    """L'export deve corrispondere a un commit: un file scelto e modificato lo ferma. Uno
    modificato che NON esce (prova_x.py) non c'entra nulla e non deve fermarlo."""
    (repo_finto / "capo.py").write_text("X = 2\n", encoding="utf-8")
    (repo_finto / "prova_x.py").write_text("PRIVATO = 3\n", encoding="utf-8")
    assert ep.modificati(str(repo_finto), ["capo.py", "tests/test_a.py"]) == ["capo.py"]


def test_modificati_vede_un_file_scelto_rinominato_via(repo_finto):
    """Il record di una rinomina porta DUE percorsi: `R  nuovo\\0vecchio`. Il secondo e' nudo (non
    ha lo stato davanti) e va letto tale e quale: se e' uno degli scelti, quel file non c'e' piu'
    e la copia fallirebbe. Tagliarne i primi 3 caratteri produce un nome fantasma."""
    _git(repo_finto, "mv", "capo.py", "capo_nuovo.py")
    assert ep.modificati(str(repo_finto), ["capo.py", "tests/test_a.py"]) == ["capo.py"]
    assert ep.modificati(str(repo_finto), ["capo_nuovo.py"]) == ["capo_nuovo.py"]


def test_modificati_vede_anche_lo_staged_e_il_cancellato(repo_finto):
    (repo_finto / "capo.py").write_text("X = 2\n", encoding="utf-8")
    _git(repo_finto, "add", "capo.py")
    os.remove(str(repo_finto / "tests" / "test_a.py"))
    assert ep.modificati(str(repo_finto), ["capo.py", "tests/test_a.py"]) == ["capo.py", "tests/test_a.py"]


def test_esegui_suite_dentro_il_tree(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert 1\n", encoding="utf-8")
    rc, riga = ep.esegui_suite(str(tmp_path))
    assert rc == 0 and "1 passed" in riga
    (tmp_path / "tests" / "test_ko.py").write_text("def test_ko():\n    assert 0\n", encoding="utf-8")
    rc, riga = ep.esegui_suite(str(tmp_path))
    assert rc != 0 and "1 failed" in riga


def test_main_ferma_se_la_suite_modifica_i_byte_dell_artefatto(
        repo_finto, allowlist_finta, monkeypatch, capsys):
    def suite_che_sporca(tree_dir, attesi=None):
        with open(os.path.join(tree_dir, "capo.py"), "a", encoding="utf-8") as fh:
            fh.write("# generato dalla suite\n")
        return 0, "prove verdi"

    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda *a, **k: 0)
    monkeypatch.setattr(ep, "esegui_suite", suite_che_sporca)

    assert ep.main([]) == 1
    assert "suite ha modificato l'artefatto" in capsys.readouterr().out


@pytest.mark.parametrize("leva", ["--rigoroso", "--commit"])
def test_main_rifiuta_una_certificazione_senza_corpus_esplicito(
        leva, monkeypatch, capsys):
    """Un verdetto bloccante deve identificare lo snapshot privato confrontato.

    Il rifiuto precede materializzazione, cancello e suite: altrimenti un errore di
    operatore puo' produrre un artefatto misurato contro il repo sorgente implicito.
    """
    chiamate = []
    monkeypatch.setattr(ep, "file_tracciati", lambda *_: chiamate.append("selezione") or [])
    monkeypatch.setattr(ep, "copia_in_temp", lambda *_: chiamate.append("copia"))
    monkeypatch.setattr(ep, "_cancello", lambda *_a, **_k: chiamate.append("cancello") or 0)
    monkeypatch.setattr(ep, "esegui_suite", lambda *_a, **_k: chiamate.append("suite") or (0, "ok"))

    assert ep.main([leva]) == 2
    assert chiamate == []
    out = capsys.readouterr().out
    assert "STOP" in out and "--corpus-root" in out


def test_manifest_usa_allowlist_della_selezione_anche_se_cambia_durante_la_suite(
        repo_finto, allowlist_finta, monkeypatch):
    import hashlib

    iniziale = allowlist_finta.read_bytes()
    catturato = {}
    scrivi = ep.scrivi_manifest

    def suite(tree_dir, attesi=None):
        allowlist_finta.write_text('*.diverso\n', encoding='utf-8')
        return 0, 'sintetica'

    def manifest(path, *a, **kw):
        catturato.update(scrivi(path, *a, **kw))
        return catturato

    monkeypatch.setattr(ep, 'REPO', str(repo_finto))
    monkeypatch.setattr(ep, 'ALLOWLIST', str(allowlist_finta))
    monkeypatch.setattr(ep, '_cancello', lambda *a, **kw: 0)
    monkeypatch.setattr(ep, 'esegui_suite', suite)
    monkeypatch.setattr(ep, 'scrivi_manifest', manifest)
    assert ep.main([]) == 0
    assert catturato['input']['hash_liste']['ALLOWLIST.txt'] == hashlib.sha256(iniziale).hexdigest()
    assert catturato['verifica']['stato'] == 'INCOMPLETO'


def test_esegui_suite_non_fa_vedere_al_tree_il_db_del_pm(tmp_path, monkeypatch):
    """`memory_db` si ancora alla cartella del file, ma BELLOMBERG_DATA_DIR la scavalcherebbe:
    dentro il tree esportato quella variabile non deve arrivare."""
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", "C:\\finta\\cartella\\dati")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_env.py").write_text(
        "import os\n\n\ndef test_env():\n    assert not os.environ.get('BELLOMBERG_DATA_DIR')\n",
        encoding="utf-8")
    rc, riga = ep.esegui_suite(str(tmp_path))
    assert rc == 0 and "1 passed" in riga


def test_autore_pubblico_pretende_l_email_noreply(clone_pubblico):
    assert ep.autore_pubblico(str(clone_pubblico), vietate=[]) == ("Mario Rossi", NOREPLY)
    _git(clone_pubblico, "config", "user.email", "mrossi@example.com")
    with pytest.raises(RuntimeError, match="noreply"):
        ep.autore_pubblico(str(clone_pubblico), vietate=[])


def test_pubblica_in_svuota_copia_e_fa_un_commit_solo_senza_push(repo_finto, clone_pubblico, tmp_path):
    temp = tmp_path / "temp"
    scelti = ["capo.py", "tests/test_a.py"]
    ep.copia_in_temp(str(repo_finto), scelti, str(temp))
    comandi = []

    def esegui(args, **kw):
        comandi.append(args)
        return subprocess.run(args, **kw)

    h = ep.pubblica_in(str(clone_pubblico), str(temp), "sync 2026-09-03 (privato abc1234)", scelti, esegui=esegui, vietate=[])
    assert len(h) >= 7
    assert not (clone_pubblico / "vecchio.txt").exists() and (clone_pubblico / "capo.py").exists()
    assert (clone_pubblico / ".git").is_dir()
    log = _git(clone_pubblico, "log", "--format=%s")
    assert log.splitlines()[0] == "sync 2026-09-03 (privato abc1234)" and len(log.splitlines()) == 2
    assert not any("push" in c for c in comandi)
    assert _git(clone_pubblico, "status", "--porcelain") == ""


def test_il_commit_pubblico_e_noreply_anche_nel_COMMITTER(repo_finto, clone_pubblico, tmp_path):
    """`git commit --author` scrive solo l'AUTORE: il committer lo decide la configurazione, e
    due variabili d'ambiente (GIT_COMMITTER_*) la scavalcano. Se il committer non e' la noreply,
    l'email vera del PM finisce in ogni commit pubblico: si misura, non si assume."""
    temp = tmp_path / "temp"
    ep.copia_in_temp(str(repo_finto), ["capo.py"], str(temp))
    # i due vettori ostili insieme: l'ambiente e la config del clone
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Qualcun Altro")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "altro@example.com")
    _git(clone_pubblico, "config", "committer.email", "altro@example.com")
    try:
        ep.pubblica_in(str(clone_pubblico), str(temp), "sync 2026-09-03 (privato abc1234)", ["capo.py"], vietate=[])
    finally:
        monkeypatch.undo()
    autore, committer = _git(clone_pubblico, "log", "-1", "--format=%an <%ae>%n%cn <%ce>").splitlines()[:2]
    assert autore == "Mario Rossi <%s>" % NOREPLY
    assert committer == "Mario Rossi <%s>" % NOREPLY


def test_pubblica_in_rifiuta_una_cartella_che_non_e_un_clone(tmp_path):
    (tmp_path / "niente").mkdir()
    with pytest.raises(RuntimeError, match="clone"):
        ep.pubblica_in(str(tmp_path / "niente"), str(tmp_path), "sync", [], vietate=[])


@pytest.mark.parametrize("verso", ["stesso", "dentro", "contiene"])
def test_pubblica_in_rifiuta_il_repo_privato_come_dest(clone_pubblico, tmp_path, monkeypatch, verso):
    """`--dest <privato> --commit` cancellerebbe il working tree del privato, `.env` compreso.
    Il confronto passa per realpath: la junction del progetto risolve nel percorso vero."""
    dentro = clone_pubblico / "sotto"
    dentro.mkdir()
    _git(dentro, "init", "-q")
    finto_repo = {"stesso": str(clone_pubblico), "dentro": str(clone_pubblico.parent),
                  "contiene": str(dentro)}[verso]
    monkeypatch.setattr(ep, "REPO", finto_repo)
    with pytest.raises(RuntimeError, match="privato"):
        ep.pubblica_in(str(clone_pubblico), str(tmp_path / "temp"), "sync", [], vietate=[])


def test_pubblica_in_pubblica_solo_i_file_scelti(repo_finto, clone_pubblico, tmp_path):
    """Il cancello certifica un tree; se si copiasse la CARTELLA, tutto cio' che vi finisce dopo
    il verdetto (i .pyc della suite, un file scritto da un test) verrebbe pubblicato non visto."""
    temp = tmp_path / "temp"
    scelti = ["capo.py"]
    ep.copia_in_temp(str(repo_finto), scelti, str(temp))
    (temp / "artefatto.json").write_text("{}\n", encoding="utf-8")
    (temp / "__pycache__").mkdir()
    (temp / "__pycache__" / "capo.pyc").write_bytes(b"\x00")
    ep.pubblica_in(str(clone_pubblico), str(temp), "sync", scelti, vietate=[])
    assert (clone_pubblico / "capo.py").exists()
    assert not (clone_pubblico / "artefatto.json").exists()
    assert not (clone_pubblico / "__pycache__").exists()
    assert _git(clone_pubblico, "status", "--porcelain", "--ignored") == ""


def test_pubblica_in_dichiara_che_non_c_e_niente_da_pubblicare(repo_finto, clone_pubblico, tmp_path):
    """Due export identici di fila: git esce 1 con «nothing to commit» e il piano lo faceva
    esplodere in un CalledProcessError nudo. Un tree identico non e' un guasto: si dichiara."""
    temp = tmp_path / "temp"
    ep.copia_in_temp(str(repo_finto), ["capo.py"], str(temp))
    primo = ep.pubblica_in(str(clone_pubblico), str(temp), "sync uno", ["capo.py"], vietate=[])
    secondo = ep.pubblica_in(str(clone_pubblico), str(temp), "sync due", ["capo.py"], vietate=[])
    assert primo and secondo is None
    assert len(_git(clone_pubblico, "log", "--format=%h").splitlines()) == 2


def test_pubblica_in_riporta_la_spiegazione_di_git(repo_finto, clone_pubblico, tmp_path):
    """Un git che fallisce deve arrivare al PM con la sua riga di errore, non come traceback."""
    temp = tmp_path / "temp"
    ep.copia_in_temp(str(repo_finto), ["capo.py"], str(temp))
    _git(clone_pubblico, "config", "commit.gpgsign", "true")
    _git(clone_pubblico, "config", "gpg.program", "programma-che-non-esiste")
    with pytest.raises(RuntimeError, match="git"):
        ep.pubblica_in(str(clone_pubblico), str(temp), "sync", ["capo.py"], vietate=[])


def test_autore_pubblico_rifiuta_un_nome_che_e_una_stringa_vietata(clone_pubblico):
    """L'email e' meta' identita': il NOME entra verbatim nell'header di ogni commit pubblico.
    Il token si stampa MASCHERATO, come ogni altro esito del cancello."""
    _git(clone_pubblico, "config", "user.name", "MARIO ROSSI")   # il confronto ignora le maiuscole
    with pytest.raises(RuntimeError, match="vietata") as ex:
        ep.autore_pubblico(str(clone_pubblico), vietate=["Mario Rossi"])
    assert "Mario Rossi" not in str(ex.value)


def test_la_suite_non_lascia_pyc_nel_tree_gia_verificato(tmp_path, monkeypatch):
    """Il cancello misura il tree PRIMA della suite: se pytest ci lascia dentro i .pyc, viene
    pubblicato materiale che nessun controllo ha visto (misurati 168 file, 6 MB). La variabile si
    toglie dall'ambiente: deve essere il CODICE a metterla, non chi lancia (mutante M03)."""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert 1\n", encoding="utf-8")
    ep.esegui_suite(str(tmp_path))
    assert not list(tmp_path.rglob("__pycache__")) and not list(tmp_path.rglob("*.pyc"))


def test_la_suite_gira_in_un_tree_che_e_un_repo_git_e_non_lo_lascia(tmp_path):
    """Tre test del progetto derivano i loro input da `git ls-files`/`git check-ignore`: in una
    cartella nuda git esce 128, quei test asseriscono sul VUOTO e cadono con un messaggio falso.
    Il `.git` serve solo alla misura e non deve sopravvivere: nel pubblico c'e' gia' il suo."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_git.py").write_text(
        "import subprocess\n\n\ndef test_git():\n"
        "    r = subprocess.run(['git', 'ls-files'], capture_output=True, encoding='utf-8')\n"
        "    assert r.returncode == 0 and 'tests/test_git.py' in r.stdout\n", encoding="utf-8")
    rc, riga = ep.esegui_suite(str(tmp_path))
    assert rc == 0 and "1 passed" in riga
    assert not (tmp_path / ".git").exists()


def test_la_suite_dichiara_se_l_indice_non_copre_il_perimetro(tmp_path):
    """`git add -A` obbedisce al .gitignore PUBBLICATO e al core.excludesFile di chi lancia: se
    indicizza meno file di quelli scelti, le guardie che usano git misurerebbero meno del vero."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("segreto.txt\n", encoding="utf-8")
    (tmp_path / "segreto.txt").write_text("x\n", encoding="utf-8")
    rc, riga = ep.esegui_suite(str(tmp_path), attesi=3)
    assert rc != 0 and "indice" in riga


def _tree_suite(tmp_path, test_codice, lockfile=False):
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_x.py").write_text(test_codice, encoding="utf-8")
    if lockfile:
        (tree / "app").mkdir()
        (tree / "app" / "package-lock.json").write_text("{}\n", encoding="utf-8")
    return tree


def test_la_suite_non_eredita_le_variabili_del_env_privato(tmp_path, monkeypatch):
    """13/09 (Claude Opus 5): nel dry-run il payload legacy importava i moduli nel processo e
    `config.py` caricava il `.env` del PM; la suite dell'export ereditava 44 variabili, chiavi
    comprese (misurato sui NOMI, due rossi in piu' rispetto a una suite pulita). Qualunque via le
    porti nel processo, la suite non le vede e il verdetto dice QUALI nomi ha tolto."""
    privato = tmp_path / "privato"
    privato.mkdir()
    (privato / ".env").write_text("OPENROUTER_API_KEY=sk-finto-0000\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(privato))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-finto-0000")
    tree = _tree_suite(tmp_path, "import os\n\n\ndef test_env():\n"
                                 "    assert 'OPENROUTER_API_KEY' not in os.environ\n")
    rc, riga = ep.esegui_suite(str(tree))
    assert rc == 0 and "1 passed" in riga
    assert "OPENROUTER_API_KEY" in riga and "sk-finto" not in riga


def test_la_suite_gira_su_una_copia_e_non_scrive_nell_artefatto(tmp_path):
    """Il cancello certifica i byte dell'artefatto: la suite (e le dipendenze Node che installa)
    lavora su una copia verificata, e dopo non resta nulla ne' dentro ne' accanto."""
    tree = _tree_suite(tmp_path, "import pathlib\n\n\ndef test_scrive():\n"
                                 "    pathlib.Path('generato.txt').write_text('x')\n")
    rc, riga = ep.esegui_suite(str(tree))
    assert rc == 0 and "1 passed" in riga
    assert sorted(p.name for p in tree.rglob("*") if p.is_file()) == ["test_x.py"]
    assert not os.path.exists(str(tree) + ".suite")


def test_la_suite_installa_le_dipendenze_node_dal_lockfile_esportato(tmp_path):
    """La CI pubblica fa `npm ci` prima di pytest (ci.yml): senza, 18 rossi del cancello erano
    moduli Node assenti e non difetti (misurato 13/09: 23 rossi, 5 con le dipendenze)."""
    tree = _tree_suite(tmp_path, "import pathlib\n\n\ndef test_node():\n"
                                 "    assert pathlib.Path('app/node_modules/.installato').is_file()\n",
                       lockfile=True)
    viste = []

    def installa(app_dir, env):
        viste.append(app_dir)
        os.makedirs(os.path.join(app_dir, "node_modules"))
        open(os.path.join(app_dir, "node_modules", ".installato"), "w").close()
        return 0, "installato"

    rc, riga = ep.esegui_suite(str(tree), installa_node=installa)
    assert rc == 0 and "1 passed" in riga
    assert len(viste) == 1 and not viste[0].startswith(str(tree) + os.sep)
    assert not (tree / "app" / "node_modules").exists()


def test_un_npm_ci_fallito_e_un_ko_dichiarato_non_una_suite_senza_dipendenze(tmp_path):
    tree = _tree_suite(tmp_path, "def test_ok():\n    assert 1\n", lockfile=True)
    rc, riga = ep.esegui_suite(str(tree), installa_node=lambda app_dir, env: (1, "rete assente"))
    assert rc != 0 and "npm ci" in riga and "rete assente" in riga and "passed" not in riga


def test_senza_npm_nel_path_la_suite_con_lockfile_e_un_ko_dichiarato(tmp_path, monkeypatch):
    """Cablaggio dell'installatore VERO: senza npm non si fa finta di aver installato."""
    monkeypatch.setattr(ep.shutil, "which", lambda *a, **k: None)
    tree = _tree_suite(tmp_path, "def test_ok():\n    assert 1\n", lockfile=True)
    rc, riga = ep.esegui_suite(str(tree))
    assert rc != 0 and "npm" in riga and "passed" not in riga


def test_senza_lockfile_la_suite_dichiara_che_non_ha_installato_node(tmp_path):
    tree = _tree_suite(tmp_path, "def test_ok():\n    assert 1\n")

    def installa(*_a):
        raise AssertionError("senza lockfile non si installa")

    rc, riga = ep.esegui_suite(str(tree), installa_node=installa)
    assert rc == 0 and "1 passed" in riga and "package-lock.json" in riga


def test_la_suite_nomina_i_rossi_e_salva_l_output_completo(tmp_path):
    """Il 12/09 il tool restituiva solo l'ultima riga: i 23 rossi si sono dovuti rincorrere da
    fuori, e la rincorsa ha misurato una condizione diversa (senza .git)."""
    tree = _tree_suite(tmp_path, "def test_rosso():\n    assert 0\n")
    rc, riga = ep.esegui_suite(str(tree))
    assert rc != 0 and "1 failed" in riga and "tests/test_x.py::test_rosso" in riga
    log = str(tree) + ".pytest.log"
    assert os.path.isfile(log) and "test_rosso" in open(log, encoding="utf-8").read()


def test_main_ferma_anche_sporco_insieme_a_commit(repo_finto, clone_pubblico, allowlist_finta, vietate_finte,
                                                  monkeypatch, capsys):
    """Il messaggio del commit pubblico dichiara un commit privato: con --anche-sporco quel
    commit NON contiene il codice pubblicato, e la frase sarebbe falsa."""
    (repo_finto / "capo.py").write_text("X = 99\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False,
                        corpus_root=None: 0)
    assert ep.main(["--dest", str(clone_pubblico), "--commit", "--anche-sporco",
                    "--corpus-root", str(repo_finto)]) == 2
    assert "--anche-sporco" in capsys.readouterr().out


def test_main_un_guasto_del_deposito_esce_2_e_dice_come_recuperare(repo_finto, clone_pubblico,
                                                                   allowlist_finta, vietate_finte,
                                                                   monkeypatch, capsys):
    """Ogni guasto di `pubblica_in` arriva come RuntimeError con la riga di git dentro: se `main`
    non la prende, il PM vede un traceback, l'uscita e' 1 (che vuol dire «hit trovati») e il
    `finally` intanto ha cancellato la temp gia' certificata. Review T7."""
    def esplode(*a, **k):
        raise RuntimeError("git commit: exit 128 — error: gpg failed to sign the data")
    monkeypatch.setattr(ep, "pubblica_in", esplode)
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", _cancello_sano)
    assert ep.main(["--dest", str(clone_pubblico), "--commit",
                    "--corpus-root", str(repo_finto)]) == 2
    out = capsys.readouterr().out
    assert "STOP" in out and "gpg failed" in out
    assert "git -C" in out and "restore" in out          # il comando per rimettere il clone a posto
    assert "temp conservata" in out                       # il tree certificato non si butta


def test_pubblica_in_si_ferma_se_l_indice_del_clone_non_copre_il_perimetro(repo_finto, clone_pubblico,
                                                                           tmp_path):
    """Un `.gitignore` gia' pubblicato (o un `.git/info/exclude` del clone) puo' togliere file
    dall'indice DOPO la copia: il commit pubblicherebbe meno di quanto il cancello ha misurato."""
    temp = tmp_path / "temp"
    scelti = ["capo.py", "tests/test_a.py"]
    ep.copia_in_temp(str(repo_finto), scelti, str(temp))
    (clone_pubblico / ".git" / "info").mkdir(exist_ok=True)
    (clone_pubblico / ".git" / "info" / "exclude").write_text("capo.py\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="indice del clone"):
        ep.pubblica_in(str(clone_pubblico), str(temp), "sync", scelti, vietate=[])
    assert len(_git(clone_pubblico, "log", "--format=%h").splitlines()) == 1


def test_main_senza_suite_non_si_combina_con_commit(repo_finto, clone_pubblico, allowlist_finta,
                                                    vietate_finte, monkeypatch, capsys):
    """La suite dentro il tree e' l'unica misura che il perimetro sia coerente: saltarla E
    depositare vorrebbe dire uscire 0 dichiarando una verifica che non c'e' stata."""
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False,
                        corpus_root=None: 0)
    assert ep.main(["--dest", str(clone_pubblico), "--commit", "--senza-suite",
                    "--corpus-root", str(repo_finto)]) == 2
    assert "--senza-suite" in capsys.readouterr().out
    assert len(_git(clone_pubblico, "log", "--format=%h").splitlines()) == 1


def test_main_dichiara_l_autore_del_commit_pubblico(repo_finto, clone_pubblico, allowlist_finta, vietate_finte,
                                                    monkeypatch, capsys):
    """D6: il nome che finira' nell'header dei commit pubblici il PM lo vede PRIMA di ordinare
    il push, anche in dry-run."""
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False: 0)
    ep.main(["--dest", str(clone_pubblico), "--senza-suite"])
    assert NOREPLY in capsys.readouterr().out


def test_main_chiede_il_cancello_BLOCCANTE_solo_quando_deposita(repo_finto, clone_pubblico, allowlist_finta,
                                                                vietate_finte, monkeypatch, capsys):
    """CABLAGGIO (scettici 04/09). I controlli in OSSERVAZIONE non cambiano l'exit: il dry-run
    deve restare 0 per vedere il conto scendere, ma sul DEPOSITO l'esenzione va tolta — se no
    `pubblica_in` parte su un exit 0 che dichiara i riscontri solo a parole, e le parole non le
    legge nessuna macchina. Se questa riga si stacca, nessun altro test se ne accorge."""
    visti = []

    def finto(tree_dir, solo=None, blocca_osservazione=False, corpus_root=None):
        visti.append((blocca_osservazione, corpus_root))
        return 0

    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", finto)
    ep.main(["--senza-suite"])                                        # misura
    ep.main(["--dest", str(clone_pubblico), "--senza-suite"])         # misura, con dest ma senza deposito
    ep.main(["--dest", str(clone_pubblico), "--commit",              # deposito vero
             "--corpus-root", str(repo_finto)])
    assert visti == [(False, None), (False, None), (True, str(repo_finto))]


def test_cancello_inoltra_davvero_la_leva_al_cancello(monkeypatch):
    """Il banco di mutazioni (04/09) ha visto sopravvivere `_cancello` che ACCETTA la leva e
    non la passa: tutti i test di main stubbano `_cancello`, quindi il corpo vero non era
    esercitato da nessuno. Qui si stubba un livello piu' sotto."""
    visti = []
    monkeypatch.setattr(ep.vp, "esegui_tutti",
                        lambda tree_dir, solo=None, blocca_osservazione=False,
                        corpus_root=None, **kwargs:
                        visti.append((solo, blocca_osservazione, corpus_root)) or 0)
    ep._cancello("/tmp/x", solo=["vietate"], blocca_osservazione=True)
    ep._cancello("/tmp/x")
    assert visti == [(["vietate"], True, None), (None, False, None)]


def test_cancello_inoltra_tree_e_corpus_distinti(monkeypatch):
    visti = []

    def finto(tree_dir, solo=None, blocca_osservazione=False, corpus_root=None,
              registro_input=None, registro_esiti=None):
        visti.append((tree_dir, corpus_root))
        registro_input.update({"sha256_corpus_privato": "a" * 64})
        registro_esiti.update({"rigoroso": True})
        return 0

    monkeypatch.setattr(ep.vp, "esegui_tutti", finto)
    esito = ep._cancello("/artefatto", corpus_root="/snapshot-privato")
    assert visti == [("/artefatto", "/snapshot-privato")]
    assert esito.input_manifest["sha256_corpus_privato"] == "a" * 64


def test_main_dry_run_non_tocca_dest_e_riporta(repo_finto, clone_pubblico, allowlist_finta, vietate_finte,
                                               monkeypatch, capsys):
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False: 0)
    rc = ep.main(["--dest", str(clone_pubblico), "--senza-suite"])
    out = capsys.readouterr().out
    assert rc == 0 and "file: 2" in out and "NON ESEGUITA" in out and "commit: NO" in out
    assert (clone_pubblico / "vecchio.txt").exists()


def test_main_ferma_se_il_cancello_ferma_e_non_committa(repo_finto, clone_pubblico, allowlist_finta, vietate_finte,
                                                        monkeypatch):
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False,
                        corpus_root=None: 1)
    rc = ep.main(["--dest", str(clone_pubblico), "--commit",   # niente --senza-suite: ora e' uno STOP
                  "--corpus-root", str(repo_finto)])
    assert rc == 1
    assert len(_git(clone_pubblico, "log", "--format=%h").splitlines()) == 1


def test_main_ferma_se_la_suite_cade_e_non_committa(repo_finto, clone_pubblico, allowlist_finta, vietate_finte,
                                                    monkeypatch, capsys):
    """La suite dentro il tree esportato e' una misura di coerenza: se cade, niente commit."""
    (repo_finto / "tests" / "test_b.py").write_text("def test_b():\n    assert 0\n", encoding="utf-8")
    _git(repo_finto, "add", "-A")
    _git(repo_finto, "commit", "-q", "-m", "test che cade")
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False,
                        corpus_root=None: 0)
    rc = ep.main(["--dest", str(clone_pubblico), "--commit",
                  "--corpus-root", str(repo_finto)])
    assert rc != 0 and "1 failed" in capsys.readouterr().out
    assert len(_git(clone_pubblico, "log", "--format=%h").splitlines()) == 1


def test_main_ferma_sui_file_sporchi(repo_finto, allowlist_finta, monkeypatch, capsys):
    (repo_finto / "capo.py").write_text("X = 99\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allowlist_finta))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False: 0)
    assert ep.main(["--senza-suite"]) == 2
    out = capsys.readouterr().out
    assert "STOP" in out and "capo.py" in out


def test_main_ferma_se_un_pattern_dell_allowlist_non_nomina_nulla(repo_finto, tmp_path,
                                                                  monkeypatch, capsys):
    """Review T1: una lista stantia mente su cosa esce. Vale anche per i '!'."""
    allow = tmp_path / "ALLOWLIST.txt"
    allow.write_text("*.py\ndocs/*.md\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(repo_finto))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allow))
    assert ep.main(["--senza-suite"]) == 2
    assert "docs/*.md" in capsys.readouterr().out


def test_copia_in_temp_dichiara_i_file_tracciati_ma_assenti_dal_disco(repo_finto, tmp_path):
    """Un file tracciato ma ASSENTE dal disco (rinomina o cancellazione in volo di un'altra chat:
    successo il 05/09, buco D5) faceva cadere la copia con un traceback su UN percorso. Ora si
    dichiarano TUTTI i mancanti prima di copiare qualunque cosa, e in `dest` non resta nulla a meta'."""
    os.remove(str(repo_finto / "capo.py"))
    dest = tmp_path / "temp"
    with pytest.raises(RuntimeError) as e:
        ep.copia_in_temp(str(repo_finto), ["capo.py", "tests/test_a.py"], str(dest))
    assert "capo.py" in str(e.value) and "assent" in str(e.value).lower()
    assert not (dest / "tests" / "test_a.py").exists()
