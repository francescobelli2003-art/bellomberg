# -*- coding: utf-8 -*-
"""Voce 6 dell'ordine approvato (04/09, D6): la guardia sulla STORIA di `--dest`.

Il pubblico e' GENERATO: ogni commit che l'export deposita nel clone viene REGISTRATO in un
file privato (tools/release/policy/STORIA.txt, mai esportato). Prima di svuotare il clone, `pubblica_in`
misura la storia raggiungibile da HEAD: un commit che il registro non conosce e' un commit che
l'export non ha prodotto, quindi storia che il cancello non ha misurato — e un push la
pubblicherebbe. Batteria PUBBLICA: repo git FINTI in `tmp_path`, nomi finti («Mario Rossi»),
nessun percorso e nessun dato del proprietario. Non tocca il repo vero e non pusha MAI.
"""
import os
import stat
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
from tools.release import export_pubblico as ep  # noqa: E402

NOREPLY = "123+mrossi@users.noreply.github.com"


def _git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def _temp_con(tmp_path, nome, contenuto):
    """Un tree 'certificato' finto con un file solo: cio' che `pubblica_in` copia nel clone."""
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    (temp / nome).write_text(contenuto, encoding="utf-8")
    return temp


@pytest.fixture
def clone_vergine(tmp_path):
    """Un clone come sara' quello del repo NUOVO al primo sync: git init, autore noreply, ZERO commit."""
    d = tmp_path / "pubblico_nuovo"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.name", "Mario Rossi")
    _git(d, "config", "user.email", NOREPLY)
    return d


@pytest.fixture
def clone_con_storia_estranea(tmp_path):
    """Un clone con UN commit che l'export non ha mai prodotto: storia non misurata dal cancello."""
    d = tmp_path / "pubblico_vecchio"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.name", "Mario Rossi")
    _git(d, "config", "user.email", NOREPLY)
    (d / "vecchio.txt").write_text("pubblicato ieri\n", encoding="utf-8")
    _git(d, "add", "vecchio.txt")
    _git(d, "commit", "-q", "-m", "iniziale")
    return d


@pytest.fixture
def registro(tmp_path):
    """Il registro privato, con la sola intestazione: nessun commit ancora prodotto."""
    p = tmp_path / "STORIA.txt"
    p.write_text("# registro finto: un hash per riga, il resto della riga e' commento\n", encoding="utf-8")
    return p


def test_registro_assente_ferma_prima_di_svuotare_il_clone(clone_con_storia_estranea, tmp_path):
    """Regola 14/07: un registro che manca non vale «storia vuota». Si ferma PRIMA di toccare
    il working tree del clone, e dice quale file manca."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    manca = tmp_path / "non_esiste" / "STORIA.txt"
    with pytest.raises(RuntimeError, match="registro") as ex:
        ep.pubblica_in(str(clone_con_storia_estranea), str(temp), "sync", ["capo.py"],
                       vietate=[], registro=str(manca))
    assert str(manca) in str(ex.value)
    assert (clone_con_storia_estranea / "vecchio.txt").exists()
    assert len(_git(clone_con_storia_estranea, "log", "--format=%h").splitlines()) == 1


def test_clone_vergine_passa_e_il_commit_finisce_nel_registro(clone_vergine, registro, tmp_path):
    """Primo sync sul repo NUOVO: zero commit nel clone, registro vuoto. Passa, e l'hash del
    commit depositato viene scritto nel registro: e' cio' che rende misurabile il sync dopo."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    h = ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                       registro=str(registro))
    assert h and len(h) >= 40
    assert h in registro.read_text(encoding="utf-8")
    assert _git(clone_vergine, "rev-parse", "HEAD").strip() == h


def test_un_commit_che_il_registro_non_conosce_ferma_prima_di_svuotare(clone_con_storia_estranea,
                                                                       registro, tmp_path):
    """Il caso che ha reso necessaria la guardia: `--dest` punta a un clone la cui storia
    l'export non ha prodotto. STOP dichiarato (conteggio + hash abbreviato), working tree intatto,
    nessun commit nuovo."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    estraneo = _git(clone_con_storia_estranea, "rev-parse", "--short", "HEAD").strip()
    with pytest.raises(RuntimeError, match="STORIA") as ex:
        ep.pubblica_in(str(clone_con_storia_estranea), str(temp), "sync", ["capo.py"],
                       vietate=[], registro=str(registro))
    msg = str(ex.value)
    assert "1 commit" in msg and estraneo in msg
    assert (clone_con_storia_estranea / "vecchio.txt").exists()
    assert not (clone_con_storia_estranea / "capo.py").exists()
    assert len(_git(clone_con_storia_estranea, "log", "--format=%h").splitlines()) == 1
    assert estraneo not in registro.read_text(encoding="utf-8")


def test_il_secondo_sync_su_una_storia_registrata_passa(clone_vergine, registro, tmp_path):
    """Dopo il primo deposito la storia del clone e' TUTTA nel registro: il secondo sync passa
    e registra anche il suo commit (due hash, in ordine)."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    primo = ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                           registro=str(registro))
    (temp / "capo.py").write_text("X = 2\n", encoding="utf-8")
    secondo = ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                             registro=str(registro))
    assert primo and secondo and primo != secondo
    righe = [r.split()[0] for r in registro.read_text(encoding="utf-8").splitlines()
             if r.strip() and not r.startswith("#")]
    assert righe == [primo, secondo]


def test_un_ref_pull_nel_clone_ferma(clone_vergine, registro, tmp_path):
    """Un `refs/pull/N/head` e' la traccia indelebile di una PR: se sta nel clone, un
    `push --mirror` lo porterebbe sul repo nuovo. Il deposito vuole un clone nudo."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                   registro=str(registro))
    _git(clone_vergine, "update-ref", "refs/pull/1/head", "HEAD")
    with pytest.raises(RuntimeError, match="refs/pull/1/head"):
        ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                       registro=str(registro))
    assert len(_git(clone_vergine, "log", "--format=%h").splitlines()) == 1


def test_un_ramo_locale_oltre_a_quello_corrente_ferma(clone_vergine, registro, tmp_path):
    """Un ramo locale con commit che l'export non ha prodotto non e' raggiungibile da HEAD, ma
    `push --all` lo pubblicherebbe. Si ferma e lo nomina."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                   registro=str(registro))
    _git(clone_vergine, "branch", "ramo-locale-1")
    with pytest.raises(RuntimeError, match="ramo-locale-1"):
        ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                       registro=str(registro))


def test_i_ref_remoti_non_fermano(clone_vergine, registro, tmp_path):
    """I `refs/remotes/origin/*` sono lo stato del SERVER visto dal clone (dopo un fetch ci
    finiscono anche i rami di Dependabot, entro minuti dal primo push): non escono con un push
    normale e non devono bloccare ogni sync successivo al primo."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                   registro=str(registro))
    _git(clone_vergine, "update-ref", "refs/remotes/origin/dependabot/pip/x-1.2.3", "HEAD")
    (temp / "capo.py").write_text("X = 2\n", encoding="utf-8")
    assert ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                          registro=str(registro))


def test_un_clone_guasto_non_passa_per_vergine(clone_vergine, registro, tmp_path):
    """Regola 14/07: «HEAD non risolve» ha DUE cause — nessun commit (rev-parse esce 1, muto) e
    un .git guasto (esce 128 con la sua riga). Solo la prima e' un clone vergine; la seconda si
    ferma con la riga di git, non si tratta come «primo sync»."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    (clone_vergine / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="rev-parse"):
        ep.pubblica_in(str(clone_vergine), str(temp), "sync", ["capo.py"], vietate=[],
                       registro=str(registro))
    assert registro.read_text(encoding="utf-8").count("\n") == 1     # niente registrato


def test_una_riga_del_registro_che_non_e_un_hash_intero_ferma(clone_vergine, registro, tmp_path):
    """Un hash ABBREVIATO copiato a mano nel registro non combacerebbe mai con `rev-list`: la
    riga sarebbe un ripiego muto (STOP identico a ogni sync, senza dire perche'). Si ferma e
    nomina la riga."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    with open(str(registro), "a", encoding="utf-8") as fh:
        fh.write("abc1234  # copiato a mano, abbreviato\n")
    with pytest.raises(RuntimeError, match="registro") as ex:
        ep.pubblica_in(str(clone_vergine), str(temp), "sync", ["capo.py"], vietate=[],
                       registro=str(registro))
    assert "riga 2" in str(ex.value) and "abc1234" in str(ex.value)


def test_un_bom_in_testa_al_registro_non_diventa_un_hash(clone_vergine, registro, tmp_path):
    """Notepad mette un BOM: la prima riga non deve diventare il token `\\ufeff...`."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    primo = ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                           registro=str(registro))
    registro.write_bytes(b"\xef\xbb\xbf" + registro.read_bytes())
    (temp / "capo.py").write_text("X = 2\n", encoding="utf-8")
    assert ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                          registro=str(registro)) != primo


def test_il_commit_fatto_ma_non_registrato_viene_dichiarato_con_l_hash(clone_vergine, registro,
                                                                        tmp_path, monkeypatch):
    """Il commit riesce e POI il registro non si scrive (permessi, disco): il sync dopo si
    fermerebbe sul commit dell'export stesso. L'errore dice che il commit C'E', e porta l'hash
    intero e la riga da aggiungere a mano — non «nessun commit»."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")

    def esplode(registro, h, messaggio):
        raise OSError("disco pieno")

    monkeypatch.setattr(ep, "registra_commit", esplode)
    with pytest.raises(RuntimeError, match="NON registrato") as ex:
        ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                       registro=str(registro))
    h = _git(clone_vergine, "rev-parse", "HEAD").strip()
    assert h in str(ex.value) and "disco pieno" in str(ex.value)


def test_origin_del_ramo_corrente_deve_essere_un_commit_registrato(clone_vergine, registro, tmp_path):
    """`refs/remotes/*` non escono con un push normale, ma con `push --mirror` si': se
    `origin/<ramo>` punta a un commit che l'export non ha prodotto (un clone con HEAD rifatto
    da zero sopra una storia estranea), si ferma. I rami remoti DIVERSI dal corrente restano
    ignorati (Dependabot)."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                   registro=str(registro))
    ramo = _git(clone_vergine, "symbolic-ref", "--short", "HEAD").strip()
    albero = _git(clone_vergine, "rev-parse", "HEAD^{tree}").strip()
    estraneo = _git(clone_vergine, "commit-tree", albero, "-m", "estraneo").strip()
    _git(clone_vergine, "update-ref", "refs/remotes/origin/" + ramo, estraneo)
    (temp / "capo.py").write_text("X = 2\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="origin/" + ramo):
        ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                       registro=str(registro))


def test_un_head_staccato_ferma_e_lo_dice(clone_vergine, registro, tmp_path):
    """La frase, non la parola: `tmp_path` porta il NOME del test («staccato» sta nel percorso
    stampato dal messaggio), e il banco ha visto sopravvivere la mutazione per questo."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                   registro=str(registro))
    _git(clone_vergine, "checkout", "-q", "--detach")
    with pytest.raises(RuntimeError, match="HEAD STACCATO"):
        ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                       registro=str(registro))


def test_un_object_store_guasto_non_e_un_traceback(clone_vergine, registro, tmp_path):
    """`rev-parse --verify HEAD` riesce anche se l'oggetto del commit MANCA: e' `rev-list` a
    dire «bad object» (128). Deve arrivare come STOP con la riga di git, non come
    CalledProcessError (che `main` non prende: traceback ed exit 1 = «hit trovati»)."""
    temp = _temp_con(tmp_path, "capo.py", "X = 1\n")
    h = ep.pubblica_in(str(clone_vergine), str(temp), "sync uno", ["capo.py"], vietate=[],
                       registro=str(registro))
    oggetto = str(clone_vergine / ".git" / "objects" / h[:2] / h[2:])
    os.chmod(oggetto, stat.S_IWRITE)          # gli oggetti di git sono in sola lettura su Windows
    os.remove(oggetto)
    with pytest.raises(RuntimeError, match="rev-list"):
        ep.pubblica_in(str(clone_vergine), str(temp), "sync due", ["capo.py"], vietate=[],
                       registro=str(registro))


def test_il_registro_vero_sta_fuori_dal_perimetro_pubblico():
    """Il registro vive in tools/release/policy/ (ESCLUSI.txt): non deve combaciare con l'allowlist.
    Il percorso di default e' quello, e la cartella e' quella delle altre liste private.
    Nel tree pubblico le liste non ci sono: il test lo dice e salta (pattern di casa)."""
    if not os.path.exists(ep.ALLOWLIST):
        pytest.skip("policy/ALLOWLIST.txt assente: siamo nel tree pubblico, le liste private non escono")
    assert os.path.dirname(ep.REGISTRO) == ep.PUBBLICO
    righe = ep.vp.leggi_lista(ep.ALLOWLIST)
    rel = os.path.relpath(ep.REGISTRO, ep.REPO).replace(os.sep, "/")
    assert not any(ep.vp.combacia(rel, p) for p in righe if not p.startswith("!"))


def test_main_con_dest_misura_la_storia_prima_del_cancello(clone_con_storia_estranea, tmp_path,
                                                          monkeypatch, capsys):
    """CABLAGGIO: gia' in dry-run con `--dest`, PRIMA di copiare e misurare, il PM deve sapere
    se quel clone e' accettabile. Un clone con storia estranea esce 2 con STOP e il working tree
    del clone resta intatto."""
    privato = tmp_path / "privato"
    privato.mkdir()
    (privato / "capo.py").write_text("X = 1\n", encoding="utf-8")
    _git(privato, "init", "-q")
    _git(privato, "config", "user.name", "Mario Rossi")
    _git(privato, "config", "user.email", NOREPLY)
    _git(privato, "add", "-A")
    _git(privato, "commit", "-q", "-m", "iniziale")
    allow = tmp_path / "ALLOWLIST.txt"
    allow.write_text("*.py\n", encoding="utf-8")
    liste = tmp_path / "pubblico_finto"
    liste.mkdir()
    (liste / "VIETATE.txt").write_text("# finta\nCloudDriveX\n", encoding="utf-8")
    reg = liste / "STORIA.txt"
    reg.write_text("# finto\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(privato))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allow))
    monkeypatch.setattr(ep.vp, "PUBBLICO", str(liste))
    monkeypatch.setattr(ep, "REGISTRO", str(reg))
    chiamate = []
    monkeypatch.setattr(ep, "_cancello",
                        lambda tree_dir, solo=None, blocca_osservazione=False: chiamate.append(1) or 0)
    rc = ep.main(["--dest", str(clone_con_storia_estranea), "--senza-suite"])
    out = capsys.readouterr().out
    assert rc == 2 and "STOP" in out and "STORIA" in out
    assert chiamate == []                                    # fermato PRIMA del cancello
    assert (clone_con_storia_estranea / "vecchio.txt").exists()


def test_main_dichiara_il_commit_fatto_quando_il_registro_non_si_scrive(clone_vergine, tmp_path,
                                                                       monkeypatch, capsys):
    """Se il registro non si scrive DOPO il commit, `main` non deve dire «nessun commit» e
    proporre un `git restore`: il commit c'e'. Dice la riga da aggiungere a mano."""
    privato = tmp_path / "privato"
    privato.mkdir()
    (privato / "capo.py").write_text("X = 1\n", encoding="utf-8")
    _git(privato, "init", "-q")
    _git(privato, "config", "user.name", "Mario Rossi")
    _git(privato, "config", "user.email", NOREPLY)
    _git(privato, "add", "-A")
    _git(privato, "commit", "-q", "-m", "iniziale")
    allow = tmp_path / "ALLOWLIST.txt"
    allow.write_text("*.py\n", encoding="utf-8")
    liste = tmp_path / "pubblico_finto"
    liste.mkdir()
    (liste / "VIETATE.txt").write_text("# finta\nCloudDriveX\n", encoding="utf-8")
    reg = liste / "STORIA.txt"
    reg.write_text("# finto\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(privato))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allow))
    monkeypatch.setattr(ep.vp, "PUBBLICO", str(liste))
    monkeypatch.setattr(ep, "REGISTRO", str(reg))
    # Reach the post-commit registry failure with complete synthetic gate evidence.
    # Exit zero alone is intentionally insufficient to authorize a deposit.
    def cancello_sano(*args, **kwargs):
        return ep.RisultatoCancello(0, {
            "fonti_ko": {}, "conteggi": {"payload_canali_guasti": 0},
            "sha256_corpus_privato": "a" * 64, "sha256_payload": "b" * 64,
        }, {
            "rigoroso": True, "non_eseguiti": [],
            "controlli": [{"nome": n, "hit": 0, "errore": False, "osservazione": False}
                          for n in ep.vp.CONTROLLI],
        })
    monkeypatch.setattr(ep, "_cancello", cancello_sano)
    monkeypatch.setattr(ep, "esegui_suite", lambda temp_dir, attesi=None: (0, "1 passed (finto)"))

    def esplode(registro, h, messaggio):
        raise OSError("disco pieno")

    monkeypatch.setattr(ep, "registra_commit", esplode)
    rc = ep.main(["--dest", str(clone_vergine), "--commit",
                  "--corpus-root", str(privato)])
    out = capsys.readouterr().out
    assert rc == 2 and "STOP" in out and "NON registrato" in out and "aggiungi a mano" in out
    assert "nessun commit" not in out and "restore" not in out
    assert len(_git(clone_vergine, "log", "--format=%h").splitlines()) == 1


def test_main_con_un_dest_inesistente_esce_2_con_stop(tmp_path, monkeypatch, capsys):
    """Su Windows un cwd inesistente in subprocess e' NotADirectoryError (WinError 267), non
    FileNotFoundError: `main` deve stamparlo come STOP, non come traceback (exit 1 = «hit»)."""
    privato = tmp_path / "privato"
    privato.mkdir()
    (privato / "capo.py").write_text("X = 1\n", encoding="utf-8")
    _git(privato, "init", "-q")
    _git(privato, "config", "user.name", "Mario Rossi")
    _git(privato, "config", "user.email", NOREPLY)
    _git(privato, "add", "-A")
    _git(privato, "commit", "-q", "-m", "iniziale")
    allow = tmp_path / "ALLOWLIST.txt"
    allow.write_text("*.py\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(privato))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allow))
    rc = ep.main(["--dest", str(tmp_path / "non_esiste"), "--senza-suite"])
    assert rc == 2 and "STOP" in capsys.readouterr().out


def test_main_con_dest_dichiara_la_storia_registrata(clone_vergine, tmp_path, monkeypatch, capsys):
    """La riga di stato al PM: quanti commit ha il clone e che sono tutti registrati."""
    privato = tmp_path / "privato"
    privato.mkdir()
    (privato / "capo.py").write_text("X = 1\n", encoding="utf-8")
    _git(privato, "init", "-q")
    _git(privato, "config", "user.name", "Mario Rossi")
    _git(privato, "config", "user.email", NOREPLY)
    _git(privato, "add", "-A")
    _git(privato, "commit", "-q", "-m", "iniziale")
    allow = tmp_path / "ALLOWLIST.txt"
    allow.write_text("*.py\n", encoding="utf-8")
    liste = tmp_path / "pubblico_finto"
    liste.mkdir()
    (liste / "VIETATE.txt").write_text("# finta\nCloudDriveX\n", encoding="utf-8")
    reg = liste / "STORIA.txt"
    reg.write_text("# finto\n", encoding="utf-8")
    monkeypatch.setattr(ep, "REPO", str(privato))
    monkeypatch.setattr(ep, "ALLOWLIST", str(allow))
    monkeypatch.setattr(ep.vp, "PUBBLICO", str(liste))
    monkeypatch.setattr(ep, "REGISTRO", str(reg))
    monkeypatch.setattr(ep, "_cancello", lambda tree_dir, solo=None, blocca_osservazione=False: 0)
    rc = ep.main(["--dest", str(clone_vergine), "--senza-suite"])
    out = capsys.readouterr().out
    assert rc == 0 and "storia del clone: 0 commit" in out
