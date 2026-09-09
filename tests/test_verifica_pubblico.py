"""P2 (02/09, pubblicazione): il cancello anti-leak. Questa batteria e' PUBBLICA:
tree finti, valori finti, nomi finti. Ogni controllo ha un test che lo vede CADERE
su un leak finto e uno che lo vede passare sul tree pulito."""
import hashlib
import io
import json
import os
import sys
import types
import zipfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
from tools.release import verifica_pubblico as vp  # noqa: E402


@pytest.mark.parametrize("rel,pattern,atteso", [
    ("capo.py", "*.py", True),
    ("scripts/x.py", "*.py", False),            # '*' non attraversa '/'
    ("tests/test_a.py", "tests/test_*.py", True),
    ("tests/sub/test_a.py", "tests/test_*.py", False),
    ("app/src/lib/api.ts", "app/**", True),
    ("app", "app/**", True),
    ("audit/00.md", "audit/", True),            # 'dir/' = prefisso
    ("auditing.py", "audit/", False),
    ("mockup_f4/x.png", "mockup_*/", True),
    ("docs/superpowers/plans/p.md", "docs/superpowers/", True),
    ("docs/ARCHITETTURA.md", "docs/superpowers/", False),
    ("a/b/c.png", "**/*.png", True),
    ("c.png", "**/*.png", True),
    ("ab.py", "a?.py", True),                   # review T1: '?' = un carattere, non '/'
    ("a/b.py", "a?b.py", False),
])
def test_combacia(rel, pattern, atteso):
    assert vp.combacia(rel, pattern) is atteso


def test_combacia_rifiuta_il_doppio_asterisco_nudo():
    """Review T1: '**' vale solo come '**/x' o 'x/**'; ogni altra forma degradava in
    silenzio a '*' (regola 14/07: si dichiara, non si degrada)."""
    for pattern in ("**", "docs/**.md", "a**b"):
        with pytest.raises(ValueError):
            vp.combacia("a/x.py", pattern)


def test_leggi_lista_salta_commenti_e_vuote(tmp_path):
    p = tmp_path / "l.txt"
    p.write_text("# intestazione\n\n*.py   # commento in coda\n!prova_*.py\n", encoding="utf-8")
    assert vp.leggi_lista(str(p)) == ["*.py", "!prova_*.py"]


def test_leggi_lista_ignora_il_bom(tmp_path):
    """Review T1: un BOM UTF-8 in testa non deve diventare parte del primo pattern."""
    p = tmp_path / "l.txt"
    p.write_bytes(b"\xef\xbb\xbf*.py\n!a.py\n")
    assert vp.leggi_lista(str(p)) == ["*.py", "!a.py"]


def test_leggi_eccezioni_pretende_il_motivo(tmp_path):
    p = tmp_path / "e.txt"
    p.write_text("# file\tcontrollo\ttoken\tmotivo\nLICENSE\tvietate\tMario Rossi\ttitolare scelto 03/09\n", encoding="utf-8")
    assert vp.leggi_eccezioni(str(p)) == {("LICENSE", "vietate", "Mario Rossi")}
    for riga in ("LICENSE\tvietate\tMario Rossi\n",                       # 3 campi
                 "LICENSE\tvietate\t\tmotivo\n",                          # token vuoto
                 "LICENSE\tvietate\tMario Rossi\tmotivo\tquinto\n"):      # 5 campi
        p.write_text(riga, encoding="utf-8")
        with pytest.raises(ValueError):
            vp.leggi_eccezioni(str(p))


# ---------------------------------------------------------------------------
# T2: tree in memoria, maschera, verdetto, controlli vietate / esclusi / dimensione.
# Tutto finto: nomi, email, percorsi, cifre. Le stringhe vere stanno nella policy privata.
# ---------------------------------------------------------------------------

def _tree(**file):
    """tree finto: nome (posix) -> bytes"""
    return {k: (v if isinstance(v, bytes) else v.encode("utf-8")) for k, v in file.items()}


PULITO = {"capo.py": "x = 1\n", "tests/test_a.py": "def test_a():\n    assert 1\n"}
VIETATE_FINTE = ["Mario Rossi", "mrossi@example.com", "C:\\Users\\mrossi", "CloudDriveX"]


def test_carica_tree_relpath_posix_salta_git(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.py").write_bytes(b"1\r\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_bytes(b"ref")
    assert vp.carica_tree(str(tmp_path)) == {"a/b.py": b"1\r\n"}


def test_carica_tree_salta_anche_il_gitfile_di_un_worktree(tmp_path):
    """Review T2 (F7): in un worktree `.git` e' un FILE con dentro il percorso assoluto del
    repo privato; il filtro saltava solo la cartella."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.py").write_bytes(b"ok\n")
    (tmp_path / ".git").write_bytes(b"gitdir: C:/x/.git/worktrees/w\n")
    assert vp.carica_tree(str(tmp_path)) == {"a/b.py": b"ok\n"}


def test_maschera_non_rivela_il_token():
    """04/09: la seconda riga asseriva `maschera("ab") == "ab\u2026(2)"`, cioe' il token
    INTERO. Il test si chiamava \u00abnon rivela il token\u00bb e certificava il contrario:
    scritto guardando il codice invece del comportamento, aveva fissato il difetto
    come regola. Ora si asserisce la PROPRIETA' su ogni lunghezza \u2014 3 delle 40 basi
    ticker del DB hanno 2 lettere, e l'output del cancello finisce in log e chat."""
    m = vp.maschera("segretissimo-12345")
    assert m == "se\u2026(18)" and "segretissimo" not in m
    for t in ("ab", "abc", "abcd", "abcdefg", "ab.mi"):
        assert t not in vp.maschera(t), (t, vp.maschera(t))
    assert vp.maschera("ab") == "\u2026(2)" and vp.maschera("abc") == "\u2026(3)"


def test_vietate_cade_sul_nome_anche_minuscolo_e_dice_file_e_riga():
    tree = _tree(**PULITO, **{"README.md": "# titolo\nautore: mario rossi\n"})
    e = vp.controllo_vietate(tree, VIETATE_FINTE)
    assert [(h.file, h.riga) for h in e.hit] == [("README.md", 2)]
    assert "Mario" not in e.hit[0].token and "rossi" not in e.hit[0].token.lower()
    assert not e.ok


def test_vietate_numera_le_righe_come_l_editor_anche_con_crlf():
    """file:riga deve portare all'editor: CRLF e un form feed (che splitlines()
    conterebbe come a-capo, grep -n no) non spostano il numero."""
    tree = _tree(**PULITO, **{"x.bat": b"@echo off\r\nrem \x0c pagina\r\nset D=C:\\Users\\mrossi\r\n"})
    e = vp.controllo_vietate(tree, VIETATE_FINTE)
    assert [(h.file, h.riga) for h in e.hit] == [("x.bat", 3)]


def test_vietate_passa_sul_tree_pulito():
    assert vp.controllo_vietate(_tree(**PULITO), VIETATE_FINTE).ok


def test_cerca_con_regex_vede_i_confini_e_la_riga_arriva_senza_il_cr():
    """Review T2 (F2): il ramo regex di _cerca (lo usano T4/T5) non aveva test; e la riga
    contata su '\\n' portava il '\\r' dei file CRLF, che spegneva ogni regex con '$'."""
    import re
    testi = vp._testi(_tree(**{"a.md": b"x 5.500 y\r\n15.500,00\r\ntot 5.500\r\n",
                               "b.md": b"valore 5500 qui\n"}))
    confini = re.compile(r"(?<![\d.,])5\.500(?![\d])")
    assert vp._cerca(testi, "5.500", regex=confini) == [("a.md", 1, "5.500"), ("a.md", 3, "5.500")]
    assert vp._cerca(testi, "5.500", regex=re.compile(r"5\.500$")) == [("a.md", 3, "5.500")]
    # il pre-filtro vale anche col ramo regex: la regex DEVE contenere il token letterale
    assert vp._cerca({"b.md": testi["b.md"]}, "5.500", regex=re.compile(r"5[.,]?500")) == []


def test_vietate_rispetta_una_eccezione_motivata_e_la_conta():
    tree = _tree(**PULITO, **{"NOTICE": "Copyright Mario Rossi\n", "README.md": "Mario Rossi\n"})
    e = vp.controllo_vietate(tree, VIETATE_FINTE, eccezioni={("NOTICE", "vietate", "Mario Rossi")})
    assert [h.file for h in e.hit] == ["README.md"]
    assert "1 eccezion" in e.note


def test_esclusi_cade_se_un_glob_largo_ha_portato_dentro_un_escluso():
    tree = _tree(**PULITO, **{"prova_x.py": "1\n", "audit/00.md": "1\n"})
    e = vp.controllo_esclusi(tree, ["prova_*.py", "audit/"])
    assert sorted(h.file for h in e.hit) == ["audit/00.md", "prova_x.py"]
    assert vp.controllo_esclusi(_tree(**PULITO), ["prova_*.py", "audit/"]).ok


def test_dimensione_cade_su_file_grande_e_su_tree_grande():
    # i file di PULITO pesano 6 e 27 byte (misurati): il tetto per-file sta sopra (40), big.json sotto (50)
    e = vp.controllo_dimensione(_tree(**PULITO, **{"app/big.json": b"x" * 50}), max_file=40)
    assert [h.file for h in e.hit] == ["app/big.json"]
    # review T2 (F5): grandi_ammessi in forma posix come le liste, anche in sottocartella
    e = vp.controllo_dimensione(_tree(**PULITO, **{"app/big.json": b"x" * 50}), grandi_ammessi=("app/big.json",), max_file=40)
    assert e.ok
    e = vp.controllo_dimensione(_tree(**PULITO), max_tree=5)
    assert not e.ok and e.hit[0].file == "<tree>"


def test_dimensione_le_soglie_sono_strette_al_byte():
    """Review T2 (F6): un file di max_file byte passa, max_file+1 cade; idem il tree."""
    assert vp.controllo_dimensione({"a": b"x" * 40}, max_file=40, max_tree=10**6).ok
    e = vp.controllo_dimensione({"a": b"x" * 41}, max_file=40, max_tree=10**6)
    assert [h.file for h in e.hit] == ["a"]
    assert vp.controllo_dimensione({"a": b"x" * 60, "b": b"x" * 40}, max_file=100, max_tree=100).ok
    e = vp.controllo_dimensione({"a": b"x" * 60, "b": b"x" * 41}, max_file=100, max_tree=100)
    assert [h.file for h in e.hit] == ["<tree>"]


def test_carica_tree_non_finge_un_tree_vuoto(tmp_path):
    """Review T2 (F1): root inesistente, root che e' un file o cartella vuota davano {}
    e un VERDETTO PULITO su zero file (regola 14/07: si dichiara, non si degrada)."""
    with pytest.raises(FileNotFoundError):
        vp.carica_tree(str(tmp_path / "manca"))
    (tmp_path / "solo.txt").write_bytes(b"1")
    with pytest.raises(FileNotFoundError):
        vp.carica_tree(str(tmp_path / "solo.txt"))
    (tmp_path / "vuota").mkdir()
    with pytest.raises(ValueError):
        vp.carica_tree(str(tmp_path / "vuota"))


def test_una_eccezione_toglie_solo_quel_token_per_quel_controllo():
    """Review T2 (F3): la chiave e' (file, controllo, token); il test di prima pinnava solo il file."""
    tree = _tree(**PULITO, **{"NOTICE": "Copyright Mario Rossi - mrossi@example.com\n"})
    e = vp.controllo_vietate(tree, VIETATE_FINTE, eccezioni={("NOTICE", "vietate", "Mario Rossi")})
    assert [(h.file, h.token) for h in e.hit] == [("NOTICE", vp.maschera("mrossi@example.com"))]
    assert "1 eccezione applicata" in e.note
    e = vp.controllo_vietate(tree, VIETATE_FINTE, eccezioni={("NOTICE", "valori_db", "Mario Rossi")})
    assert sorted(h.token for h in e.hit) == sorted([vp.maschera("Mario Rossi"), vp.maschera("mrossi@example.com")])
    assert "applicat" not in e.note


def test_una_eccezione_senza_riscontro_viene_dichiarata():
    """Review T2 (F4): un token con maiuscole diverse dalla lista o un file con refuso non
    combacia mai: l'hit resta (STOP) e la riga stantia di ECCEZIONI.txt va dichiarata."""
    tree = _tree(**PULITO, **{"NOTICE": "Copyright Mario Rossi\n"})
    e = vp.controllo_vietate(tree, VIETATE_FINTE, eccezioni={("NOTICE", "vietate", "mario rossi"),
                                                            ("NOTICE.txt", "vietate", "Mario Rossi")})
    assert [h.file for h in e.hit] == ["NOTICE"]
    assert "2 eccezioni senza riscontro" in e.note
    # un'eccezione di un ALTRO controllo non e' senza riscontro qui: la giudica quel controllo
    e = vp.controllo_vietate(_tree(**PULITO), VIETATE_FINTE, eccezioni={("x.py", "valori_db", "5.500")})
    assert e.ok and "senza riscontro" not in e.note


def test_vietate_legge_anche_un_file_non_utf8_senza_cadere():
    """Review T2 (F6): un byte fuori UTF-8 (immagine, file cp1252) non deve far cadere il
    cancello con un traceback: si decodifica con sostituzione e la vietata si trova lo stesso."""
    tree = _tree(**PULITO, **{"img.bin": b"\xff\xd8\xff\xe0Mario Rossi\x00\xfe"})
    e = vp.controllo_vietate(tree, VIETATE_FINTE)
    assert [(h.file, h.riga) for h in e.hit] == [("img.bin", 1)]


def test_verdetto_0_solo_se_tutti_eseguiti_e_a_zero(capsys):
    ok = vp.Esito("vietate")
    ko = vp.Esito("esclusi", hit=[vp.Hit("prova_x.py", 0, "-")])
    guasto = vp.Esito("env", errore=".env assente")
    assert vp.verdetto([ok]) == 0
    assert vp.verdetto([ok, ko]) == 1
    assert vp.verdetto([ok, guasto]) == 2
    assert vp.verdetto([ok], non_eseguiti=("gitleaks",)) == 2
    out = capsys.readouterr().out
    assert "NON ESEGUIT" in out and "prova_x.py" in out


def test_verdetto_stampa_200_hit_e_dichiara_quanti_ne_nasconde(capsys):
    """Review T2 (F6): la riga 'altri N hit' e' l'unico segnale nel log che ci sono hit nascosti."""
    hit = [vp.Hit("f%03d.py" % i, i, "-") for i in range(1, 202)]
    assert vp.verdetto([vp.Esito("vietate", hit=hit)]) == 1
    out = capsys.readouterr().out
    assert "f200.py:200" in out and "f201.py" not in out and "altri 1 hit" in out
    vp.verdetto([vp.Esito("vietate", hit=hit[:200])])
    assert "altri" not in capsys.readouterr().out


def test_il_verdetto_stampa_file_riga_e_token_mascherato_mai_il_valore(capsys):
    """La proprieta' di tutto il cancello: l'output puo' finire in un log o in una chat."""
    tree = _tree(**PULITO, **{"README.md": "contatto: mrossi@example.com\n"})
    rc = vp.verdetto([vp.controllo_vietate(tree, VIETATE_FINTE)])
    out = capsys.readouterr().out
    assert rc == 1 and "README.md:1" in out
    assert "mrossi@example.com" not in out and "example.com" not in out


# ---------------------------------------------------------------------------
# T3: controllo 3 — i valori del .env vivo, tenuti in memoria, e l'autoprova che li
# vede cadere. Qui solo .env FINTI in tmp_path: il vero lo legge il CLI, e stampa conteggi.
# ---------------------------------------------------------------------------

VALORI_ENV_FINTI = [("CHIAVE_A", "sk-finta-0123456789abcdef"), ("ALTRA", "altro-valore-finto")]


def test_valori_env_legge_solo_i_valori_lunghi_e_dichiara_i_corti(tmp_path):
    p = tmp_path / ".env"
    p.write_text('CHIAVE_A="sk-finta-0123456789abcdef"\nPIN=1234\nVUOTA=\nB=abcdefgh\n# commento\n',
                 encoding="utf-8")
    assert vp.valori_env(str(p)) == [("CHIAVE_A", "sk-finta-0123456789abcdef"), ("B", "abcdefgh")]
    assert vp.variabili_corte(str(p)) == ["PIN"]        # 4 caratteri: NON cercata, e si dice


def test_env_cade_dove_il_valore_appare_e_il_token_e_il_nome_non_il_valore():
    """Il token del controllo env e' NOME…(lunghezza): per un PIN o una password di 8
    caratteri anche due caratteri in chiaro sarebbero entropia regalata."""
    # 'CHIAVE' e non 'KEY': con la parola inglese la riga diventa un finding di gitleaks
    # (regola generic-api-key) nel tree pubblico, e il controllo 1 resterebbe rosso per sempre
    tree = _tree(**PULITO, **{"config.py": 'CHIAVE = "sk-finta-0123456789abcdef"\n', "x.md": "niente\n"})
    e = vp.controllo_env(tree, VALORI_ENV_FINTI)
    assert [(h.file, h.riga, h.token) for h in e.hit] == [("config.py", 1, "CHIAVE_A…(25)")]
    assert "2 valori" in e.note
    assert vp.controllo_env(_tree(**PULITO), VALORI_ENV_FINTI).ok


def test_env_dichiara_le_variabili_corte_nella_nota():
    """Un valore sotto MIN_ENV caratteri non si cerca (troppo corto per essere unico):
    il verdetto lo DICE, per nome (i nomi stanno in .env.example, i valori mai)."""
    e = vp.controllo_env(_tree(**PULITO), VALORI_ENV_FINTI[1:], corte=["PIN", "PORTA"])
    assert e.ok and "2 variabili con valore corto non cercate: PIN, PORTA" in e.note


def test_env_assente_e_un_ko_dichiarato(tmp_path):
    with pytest.raises(FileNotFoundError):
        vp.valori_env(str(tmp_path / "manca.env"))


def test_prova_env_conta_quanti_valori_vede_cadere():
    """(n, visti cadere, nomi NON caduti): il KO deve dire quale variabile (review T3 F6)."""
    assert vp.prova_env(VALORI_ENV_FINTI) == (2, 2, [])
    assert vp.prova_env([]) == (0, 0, [])
    # review T3 (F3): duplicato fra variabili + valore sottostringa di un altro: i file
    # distinti caduti sono 3, gli hit grezzi 4 — contare gli hit darebbe un falso KO
    assert vp.prova_env([("A", "abcdefgh"), ("B", "abcdefghij"), ("C", "abcdefgh")]) == (3, 3, [])


def test_prova_env_vede_un_valore_che_non_cade():
    """La garanzia e' una misura, non una frase (banco: il mutante «torna n su n» passava
    la batteria): un valore dotenv su piu' righe coi pezzi sotto MIN_ENV non si puo'
    cercare riga per riga, e prova_env deve dirlo con caduti < n e col NOME."""
    assert vp.prova_env([("A", "sk-finta-0123456789abcdef"), ("B", "ab-cd\nef-gh")]) == (2, 1, ["B"])
    # review T3 (F3, verso fail-open): con un duplicato in mezzo il conto resta per file
    assert vp.prova_env([("A", "abcdefgh"), ("B", "abcdefghij"), ("C", "ab-cd\nef-gh")]) == (3, 2, ["C"])


def test_env_senza_valori_lunghi_e_un_ko_del_controllo_non_un_verde():
    """Review T3 (F1, dallo scettico): T7 chiamera' controllo_env direttamente; un .env
    che non produce valori cercabili non deve dare «0 hit = OK»."""
    e = vp.controllo_env(_tree(**PULITO), [])
    assert not e.ok and "nessun valore" in e.errore


def test_env_con_una_riga_non_interpretabile_e_un_ko_non_un_valore_in_meno(tmp_path, capsys):
    """Review T3 (F1): dotenv salta una riga che non capisce con un WARNING nel log e
    basta; il valore su quella riga non verrebbe mai cercato. Si dichiara, coi soli
    numeri di riga (mai il testo)."""
    p = tmp_path / ".env"
    p.write_text('K1="valore-mai-chiuso-1234\nK2=ok-valore-12345\n', encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        vp.valori_env(str(p))
    assert "1" in str(ei.value) and "mai-chiuso" not in str(ei.value)
    assert vp.main(["--prova-env", "--env", str(p)]) == 2
    out = capsys.readouterr().out
    assert "KO" in out and "mai-chiuso" not in out


def test_main_prova_env_stampa_i_conteggi_e_mai_i_valori(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_text('A="sk-finta-0123456789abcdef"\nPIN=1234\n', encoding="utf-8")
    assert vp.main(["--prova-env", "--env", str(p)]) == 0
    out = capsys.readouterr().out
    assert "valori 1, visti cadere 1" in out and "PIN" in out and "sk-finta" not in out
    p.write_text("PIN=1234\n", encoding="utf-8")          # nessun valore lungo: NON e' un verde
    assert vp.main(["--prova-env", "--env", str(p)]) == 1
    assert "nessun valore da 8" in capsys.readouterr().out
    # review T3 (F2): un valore non visto cadere = KO, e il KO dice quale (F6)
    p.write_text('A="sk-finta-0123456789abcdef"\nB="ab-cd\nef-gh"\n', encoding="utf-8")
    assert vp.main(["--prova-env", "--env", str(p)]) == 1
    out = capsys.readouterr().out
    assert "NON vist" in out and ": B" in out and "ab-cd" not in out
    assert vp.main(["--prova-env", "--env", str(tmp_path / "manca.env")]) == 2
    assert "KO" in capsys.readouterr().out


def test_main_env_di_default_e_quello_del_repo():
    """Il default di --env e' il .env del repo, non uno relativo alla cwd (banco M21)."""
    assert vp._argparser().parse_args(["--prova-env"]).env == os.path.join(vp.REPO, ".env")


@pytest.mark.parametrize("extra", [["--tree", "x"], ["--solo", "vietate"], ["--scarica-gitleaks"]])
def test_prova_env_si_usa_da_sola(tmp_path, capsys, extra):
    """Review T3 (F7): --prova-env insieme a --tree/--solo/--scarica-gitleaks ignorava le
    altre opzioni in silenzio e usciva 0: un KO dichiarato diventava un verde."""
    p = tmp_path / ".env"
    p.write_text('A="sk-finta-0123456789abcdef"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as ex:
        vp.main(["--prova-env", "--env", str(p), *extra])
    assert ex.value.code == 2 and "prova-env" not in capsys.readouterr().out


def test_env_trova_i_pezzi_di_un_valore_su_piu_righe_e_dichiara_i_non_cercabili():
    """Review T3 (F4): dotenv legge valori su piu' righe (virgolette multiriga, `\\n`
    letterale, chiavi PEM); la ricerca riga per riga non li vedeva MAI: verdetto verde col
    segreto nel tree. Si cercano i pezzi da MIN_ENV in su; un valore senza pezzi cercabili
    si dichiara per nome, e prova_env lo conta fra i non caduti."""
    coppie = [("PEM", "prima-riga-finta\nseconda-riga-finta"), ("CORTA", "ab-cd\nef-gh")]
    tree = _tree(**PULITO, **{"k.json": '{"k": "x seconda-riga-finta y"}\n'})
    e = vp.controllo_env(tree, coppie)
    assert [(h.file, h.riga, h.token) for h in e.hit] == [("k.json", 1, "PEM…(35)")]
    assert "1 valore non cercabile" in e.note and "CORTA" in e.note
    assert vp.prova_env(coppie) == (2, 1, ["CORTA"])


def test_env_cerca_anche_la_forma_grezza_di_un_valore_con_escape(tmp_path):
    """Review T3 (F5): fra virgolette doppie dotenv decodifica `\\"` e `\\t`; una riga del
    .env incollata tal quale in un file porta il testo GREZZO, che sfuggiva."""
    p = tmp_path / ".env"
    p.write_text('K1="ab\\"cd-efgh-1234"\nK2=liscio-valore-1234\n', encoding="utf-8")
    coppie = vp.valori_env(str(p))
    assert ("K1", 'ab"cd-efgh-1234') in coppie and ("K1", 'ab\\"cd-efgh-1234') in coppie
    tree = _tree(**PULITO, **{"copia.txt": 'K1="ab\\"cd-efgh-1234"\n'})
    assert [h.token for h in vp.controllo_env(tree, coppie).hit] == ["K1…(16)"]


# ---------------------------------------------------------------------------
# T4: controllo A (valori vivi del DB nei 3 formati, regola calibrata coi confini numerici)
# e controllo B (la lista privata di tests/system/prova_numeri_del_book.py). DB finto in tmp_path, cifre
# finte; il DB vero non entra mai nei test.
# ---------------------------------------------------------------------------

def test_forme_numero_regola_calibrata():
    assert vp.forme_numero(27500.0) == {"27.500"}
    assert vp.forme_numero(1500000.0) == {"1.500.000", "1500000"}
    assert vp.forme_numero(613.0) == set()                     # intero < 1000: controllo 5
    assert vp.forme_numero(5500.0) == set()                    # BUCO DICHIARATO: '5.500' ha 5 caratteri
    assert vp.forme_numero(10000.0) == {"10.000"}              # da qui in su la forma con le migliaia si cerca
    assert vp.forme_numero(250000.0) == {"250.000"}            # le cifre nude ('250000') solo da 1.000.000
    assert vp.forme_numero(1234.56) == {"1234.56", "1234,56", "1.234,56"}
    assert vp.forme_numero(7.5) == set()                       # '7.50' ha 4 caratteri
    f = vp.forme_numero(3117.456789)
    assert {"3117.46", "3117,46", "3.117,46", "3117.456789", "3117,456789"} <= f
    assert vp.forme_numero(-1234.56) == vp.forme_numero(1234.56)   # il segno non e' il segreto
    assert vp.forme_numero(1000000.0) == {"1.000.000", "1000000"}  # review T4 (F4d): 1e6 esatto
    assert {"2345.125", "2345,125"} <= vp.forme_numero(2345.125)  # review T4 (F4g): 3 decimali
    # review T4 (F3): un REAL a 1 decimale copiato in una fixture e' '2345.5', non '2345.50'
    assert {"2345.5", "2345,5", "2.345,5", "2345.50", "2.345,50"} <= vp.forme_numero(-2345.5)


def _db_finto(tmp_path):
    import sqlite3
    p = str(tmp_path / "finto.db")
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE cash_movements(date TEXT, type TEXT, amount_eur REAL);
    CREATE TABLE nav_snapshots(date TEXT, nav_total_eur REAL, invested_eur REAL, cash_eur REAL);
    CREATE TABLE positions(ticker TEXT, quantita REAL, prezzo_medio REAL);
    CREATE TABLE trade_history(ticker TEXT, quantita REAL, prezzo REAL, realized_local REAL, realized_eur REAL);
    INSERT INTO cash_movements VALUES ('2026-01-01','DEPOSIT',27500);
    INSERT INTO cash_movements VALUES ('2026-01-03','WITHDRAW',-2345.5);
    INSERT INTO nav_snapshots VALUES ('2026-01-02',98765.43,71234.5,21987.65);
    INSERT INTO positions VALUES ('ABCD.MI',613,3117.456789);
    INSERT INTO trade_history VALUES ('ABCD.MI',613,3117.456789,NULL,NULL);
    INSERT INTO trade_history VALUES ('WXYZ',3,312.47,555.55,512.34);
    CREATE TABLE company_guidance(ticker TEXT, metric TEXT, unit TEXT, value_low REAL, value_mid REAL, value_high REAL);
    INSERT INTO company_guidance VALUES ('ABCD.MI','ebitda_margin','pct',26.5,27.5,28.5);
    INSERT INTO company_guidance VALUES ('ABCD.MI','ricavi','meur (ricavi FY)',NULL,123456,NULL);
    CREATE TABLE position_prices(ticker TEXT, prezzo REAL, valuta TEXT, source TEXT, timestamp TEXT);
    INSERT INTO position_prices VALUES ('ABCD.MI',3210.75,'EUR','finto','2026-01-02T10:00:00');
    """)
    c.commit()
    c.close()
    return p


def test_valori_db_legge_le_colonne_vive_in_sola_lettura_e_ricorda_da_dove(tmp_path):
    """Le dieci COLONNE_VIVE (bloccanti) e, a parte, le quattro COLONNE_ESTESE del 05/09 (buco D5
    4b: guidance low/mid/high e prezzi delle posizioni, che prima nessun controllo leggeva) — in
    osservazione col controllo `valori_estesi` finche' il loro conto non e' zero."""
    db = _db_finto(tmp_path)
    v = vp.valori_db(db)
    assert set(v) == {27500.0, -2345.5, 98765.43, 71234.5, 21987.65, 613.0, 3117.456789,
                      3.0, 312.47, 555.55, 512.34}
    e = vp.valori_db(db, vp.COLONNE_ESTESE)
    assert set(e) == {26.5, 27.5, 28.5, 123456.0, 3210.75}
    assert e[3210.75] == "position_prices.prezzo" and e[123456.0] == "company_guidance.value_mid"
    assert v[27500.0] == "cash_movements.amount_eur"
    assert v[613.0] == "positions.quantita/trade_history.quantita"     # stesso valore in due colonne
    assert v[512.34] == "trade_history.realized_eur"
    assert v[3117.456789] == "positions.prezzo_medio/trade_history.prezzo"   # review T4 (F4a)


def test_valori_db_assente_e_un_ko_dichiarato(tmp_path):
    with pytest.raises(FileNotFoundError):
        vp.valori_db(str(tmp_path / "manca.db"))


def test_il_db_si_apre_in_sola_lettura(tmp_path):
    """P2 non scrive mai sul DB del PM (banco: il mutante che apriva in scrittura passava)."""
    import sqlite3
    c = vp._apri_ro(_db_finto(tmp_path))
    try:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("CREATE TABLE scrittura_vietata(a)")
    finally:
        c.close()


def test_valori_db_cade_nei_tre_formati_e_rispetta_i_confini(tmp_path):
    valori = vp.valori_db(_db_finto(tmp_path))
    tree = _tree(**PULITO, **{
        "a.py": "x = 98765.43\n", "b.tsx": "// 98.765,43 EUR\n", "c.md": "cassa 98765,43\n",
        "d.py": "y = 198765.43\n",              # confine a sinistra: non e' 123456.78
        "e.py": "TIMEOUT = 27500\n",             # intero nudo < 1e6: NON cercato
        "f.md": "bonifico da 27.500 EUR\n",      # forma con le migliaia: SI'
        "g.md": "saldo 27.500,50\n",             # confine a destra: e' un altro numero
        "h.md": "prelievo di 2.345,50\n",        # il valore negativo del DB, senza segno
    })
    e = vp.controllo_valori_db(tree, valori)
    assert sorted((h.file, h.riga) for h in e.hit) == [("a.py", 1), ("b.tsx", 1), ("c.md", 1), ("f.md", 1), ("h.md", 1)]
    # il token dice DA DOVE viene il numero (tabella.colonna) e quanto e' lungo, mai una cifra
    assert {h.token for h in e.hit if h.file == "a.py"} == {"nav_snapshots.nav_total_eur…(8)"}
    assert all(not any(c.isdigit() for c in h.token.split("…")[0]) for h in e.hit)
    assert vp.controllo_valori_db(_tree(**PULITO), valori).ok


def test_valori_db_eccezione_per_file_e_token(tmp_path):
    valori = vp.valori_db(_db_finto(tmp_path))
    tree = _tree(**PULITO, **{"README.md": "esempio: versamento di 27.500 EUR\n"})
    e = vp.controllo_valori_db(tree, valori, eccezioni={("README.md", "valori_db", "27.500")})
    assert e.ok and "1 eccezion" in e.note


def test_lista_privata_appiattisce_VIETATI_senza_eseguire_il_modulo_e_cade_ovunque(tmp_path):
    p = tmp_path / "prova_finta.py"
    p.write_text('import sys\nVIETATI = {"a.py": ["12.345", "~77 EUR"], "b.py": ["12.345"]}\n'
                 'raise SystemExit("il modulo NON va eseguito: si legge il dict e basta")\n', encoding="utf-8")
    s = vp.lista_privata(str(p))
    assert set(s) == {"12.345", "~77 EUR"} and s["12.345"] == "a.py"
    tree = _tree(**PULITO, **{"altro.md": "vale ~77 EUR\n"})
    e = vp.controllo_lista_privata(tree, s)
    assert [(h.file, h.riga, h.token) for h in e.hit] == [("altro.md", 1, "a.py…(7)")]
    with pytest.raises(FileNotFoundError):
        vp.lista_privata(str(tmp_path / "manca.py"))
    p.write_text("ALTRO = 1\n", encoding="utf-8")            # senza VIETATI: KO, non «0 stringhe»
    with pytest.raises(ValueError):
        vp.lista_privata(str(p))


def test_zero_valori_dal_db_o_lista_vuota_sono_un_ko_non_un_verde(tmp_path):
    """Review T4 (F1, ALTO): un DB con lo schema e 0 righe e' quello che il backend crea a un
    percorso sbagliato (lezione «0 posizioni = path sbagliato»); dava VERDETTO PULITO senza
    aver cercato nulla. Idem `VIETATI = {}`, e N valori senza nessun token cercabile."""
    import sqlite3
    p = str(tmp_path / "vuoto.db")
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE cash_movements(amount_eur REAL); CREATE TABLE nav_snapshots(nav_total_eur REAL, invested_eur REAL, cash_eur REAL);
    CREATE TABLE positions(quantita REAL, prezzo_medio REAL);
    CREATE TABLE trade_history(quantita REAL, prezzo REAL, realized_local REAL, realized_eur REAL);
    CREATE TABLE company_guidance(unit TEXT, value_low REAL, value_mid REAL, value_high REAL);
    CREATE TABLE position_prices(prezzo REAL);
    """)
    c.commit(); c.close()
    assert vp.valori_db(p) == {}
    e = vp.controllo_valori_db(_tree(**PULITO), vp.valori_db(p))
    assert not e.ok and "nessun valore" in e.errore
    e = vp.controllo_valori_db(_tree(**PULITO), {3.0: "positions.quantita", 7.0: "trade_history.quantita"})
    assert not e.ok and "nessun token" in e.errore              # valori ci sono, ma niente da cercare
    e = vp.controllo_lista_privata(_tree(**PULITO), {})
    assert not e.ok and "nessuna stringa" in e.errore
    q = tmp_path / "prova_finta.py"
    q.write_text("VIETATI = {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        vp.lista_privata(str(q))


def test_lista_privata_rifiuta_forme_che_degradano_la_ricerca(tmp_path):
    """Review T4 (F7): una stringa nuda al posto della lista diventava caratteri singoli
    (STOP su tutto), un dict annidato usava le chiavi (la cifra passava ZITTA), una
    stringa vuota colpiva ogni riga."""
    p = tmp_path / "prova_finta.py"
    for testo in ('VIETATI = {"a.py": "12.345"}\n',
                  'VIETATI = {"a.py": {"prezzo": ["12.345"]}}\n',
                  'VIETATI = {"a.py": ["12.345", ""]}\n',
                  'VIETATI = {"a.py": [12.345]}\n'):
        p.write_text(testo, encoding="utf-8")
        with pytest.raises(ValueError):
            vp.lista_privata(str(p))
    p.write_text('VIETATI = {"a.py": ("12.345",)}\n', encoding="utf-8")   # una tupla va bene
    assert vp.lista_privata(str(p)) == {"12.345": "a.py"}


def test_lista_privata_rispetta_una_eccezione(tmp_path):
    """Review T4 (F4b): ECCEZIONI.txt ammette il controllo lista_privata, nessun test lo provava."""
    s = {"~77 EUR": "a.py"}
    tree = _tree(**PULITO, **{"altro.md": "vale ~77 EUR\n"})
    e = vp.controllo_lista_privata(tree, s, eccezioni={("altro.md", "lista_privata", "~77 EUR")})
    assert e.ok and "1 eccezion" in e.note


def test_regex_numero_confini_naturali_e_punto_letterale():
    """Review T4 (F2 + F4f): la repr di un REAL ('1500000.0'), una lista senza spazi e una
    colonna CSV dopo una parola devono colpire; '27.500,50', '27.500.000', il numero
    dentro un altro e la cifra+separatore a sinistra no; il '.' del token e' letterale."""
    valori = {1500000.0: "nav_snapshots.nav_total_eur", 98765.43: "nav_snapshots.cash_eur",
              27500.0: "cash_movements.amount_eur", 555.55: "trade_history.realized_local"}
    tree = _tree(**PULITO, **{
        "a.py": "nav = 1500000.0\n", "b.py": "x = [98765.43,555.55]\n", "c.csv": "2026-01-01,DEPOSIT,27.500\n",
        "d.md": "stesso numero: 27.500,00\n",
        "e.md": "saldo 27.500,50\n", "f.md": "totale 27.500.000\n", "g.md": "dentro 198765.43\n",
        "h.md": "sinistra 1.555,55 e 12.555,55\n",
        "i.md": "jolly 27x500 e 98765x43\nvero 27.500\n",   # il '.' del token e' letterale: solo la riga 2
    })
    e = vp.controllo_valori_db(tree, valori)
    assert sorted({h.file for h in e.hit}) == ["a.py", "b.py", "c.csv", "d.md", "i.md"]
    assert [h.riga for h in e.hit if h.file == "i.md"] == [2]
    assert sorted(h.token for h in e.hit if h.file == "b.py") == ["nav_snapshots.cash_eur…(8)", "trade_history.realized_local…(6)"]


def test_valori_db_token_con_due_origini_sulla_stessa_riga(tmp_path):
    """Review T4 (F4c): un valore presente in due colonne porta entrambe le origini nel token."""
    valori = vp.valori_db(_db_finto(tmp_path))
    e = vp.controllo_valori_db(_tree(**PULITO, **{"p.py": "p = 3117.456789\n"}), valori)
    assert [h.token for h in e.hit] == ["positions.prezzo_medio/trade_history.prezzo…(11)"]


def test_apri_ro_regge_cancelletto_e_percento_nel_percorso(tmp_path):
    """Review T4 (F6): l'URI file:...?mode=ro non codificava il percorso: con '#' sqlite
    scartava il frammento, apriva in SCRITTURA un file spurio e mode=ro spariva."""
    import os as _os, sqlite3
    for cartella in ("canc#ello", "perc%41ent", "spazio e apostrofo'"):
        d = tmp_path / cartella
        d.mkdir()
        p = _db_finto(d)
        prima = sorted(_os.listdir(tmp_path))
        assert 27500.0 in vp.valori_db(p)
        assert sorted(_os.listdir(tmp_path)) == prima                # nessun file spurio
        c = vp._apri_ro(p)
        try:
            with pytest.raises(sqlite3.OperationalError):
                c.execute("CREATE TABLE scrittura_vietata(a)")
        finally:
            c.close()


# --- T5: controllo 5, i lotti (ticker + quantita'/prezzo sulla stessa riga) --------------------
# Lotti FINTI: gli stessi di _db_finto (verificati contro DB e lista privata in T4) piu' una
# posizione sola-positions; le tuple finte ricalcano la forma dello script in attic.

def _db_lotti(tmp_path):
    """_db_finto + una posizione che sta SOLO in positions (un lotto che manca in
    trade_history deve entrare lo stesso)."""
    import sqlite3
    p = _db_finto(tmp_path)
    c = sqlite3.connect(p)
    c.execute("INSERT INTO positions VALUES ('QRST', 11, 9.75)")
    c.execute("INSERT INTO positions VALUES ('', 613, 9.75)")                 # ticker vuoto: saltata
    c.execute("INSERT INTO trade_history VALUES ('NOPE', NULL, 9.75, NULL, NULL)")   # quantita' NULL: saltata
    c.commit()
    c.close()
    return p


LOTTI_FINTI = {("ABCD.MI", 613.0, 3117.456789), ("WXYZ", 3.0, 312.47), ("QRST", 11.0, 9.75)}
TUPLE_FINTE = ('TRADES = [\n    # 01/02/2025\n'
               '    ("01/02/2025", "ABCD",  "BUY",    613, 3117.46),\n'
               '    ("03/04/2025", "WXYZ",  "SELL",     3,  312.47),\n]\n')


def test_lotti_db_unisce_posizioni_e_trade(tmp_path):
    assert vp.lotti_db(_db_lotti(tmp_path)) == LOTTI_FINTI


def test_lotti_db_assente_e_un_ko_dichiarato(tmp_path):
    with pytest.raises(FileNotFoundError):
        vp.lotti_db(str(tmp_path / "manca.db"))


def test_lotti_da_tuple_legge_la_forma_dello_script_in_attic(tmp_path):
    p = tmp_path / "tuple.py"
    p.write_text(TUPLE_FINTE, encoding="utf-8")
    assert vp.lotti_da_tuple(str(p)) == {("ABCD", 613.0, 3117.46), ("WXYZ", 3.0, 312.47)}


def test_lotti_da_tuple_assente_o_senza_tuple_e_un_ko_non_zero_lotti(tmp_path):
    """Un file senza la lista TRADES, o con TRADES vuota, e' un file sbagliato, non «0 lotti»
    (regola 14/07, come il DB vuoto in T4)."""
    with pytest.raises(FileNotFoundError):
        vp.lotti_da_tuple(str(tmp_path / "manca.py"))
    p = tmp_path / "vuoto.py"
    for testo in ("TRADES = []\n", "ALTRO = [('01/02/2025', 'ABCD', 'BUY', 613, 3117.46)]\n", "TRADES = 3\n"):
        p.write_text(testo, encoding="utf-8")
        with pytest.raises(ValueError):
            vp.lotti_da_tuple(str(p))


def test_forme_lotto_intere_decimali_migliaia_e_parte_intera():
    assert vp._forme_lotto(613) == {"613"} == vp._forme_lotto(-613)          # il segno non e' il segreto
    assert vp._forme_lotto(1234.0) == {"1234", "1.234", "1,234"}       # migliaia italiane e inglesi (review F2)
    assert {"3117.456789", "3117,456789", "3117.5", "3117,5", "3.117,5", "3117.46", "3117,46",
            "3.117,46", "3117", "3.117"} <= vp._forme_lotto(3117.456789)
    assert {"9.75", "9,75", "9.8", "9,8", "9"} <= vp._forme_lotto(9.75)
    assert all(vp._cifre(t) == n for t, n in (("3.117,46", 6), ("9.75", 3), ("613", 3), ("1.234", 4)))


def test_lotti_cade_su_ticker_con_quantita_e_prezzo_o_con_numero_a_4_cifre(tmp_path):
    lotti = vp.lotti_db(_db_lotti(tmp_path))
    tree = _tree(**PULITO, **{
        "t1.py": "INSERT INTO positions VALUES ('ABCD.MI', 1, 613, 3117.46)\n",   # q E p (p a 2 decimali)
        "t2.py": "carico ABCD 3.117,46\n",                                        # ticker base + p a 4+ cifre
        "t3.py": "WXYZ 3 @ 312,47\n",                                             # q E p
        "t4.py": "WXYZ ha 3 posizioni\n",                                         # solo q a 1 cifra: NO
        "t5.py": "ABCDE 613 3117.46\n",                                           # ticker diverso: NO
        "t6.py": "XABCD 613 3117.46\n",                                           # confine alfanumerico a sinistra: NO
        "t7.py": "ABCD.MI 613\n",                                                 # solo q a 3 cifre: NO
        "t8.py": "ABCD 613\nWXYZ 312.47\n",                                       # riga 2: solo p, ma a 5 cifre: SI'
        "t9.py": "ABCD 3117\n",                                                   # la sola parte intera del prezzo, 4 cifre: SI'
        "t10.py": "WXYZ 3 @ 1312.47\n",                                           # p dentro un altro numero: NO
        "t11.py": "QRST 9.75\n",                                                  # solo p a 3 cifre: NO
        "t12.py": "x = 1\nABCD.MI 613 3117.456789\n",                             # repr del REAL: SI', riga 2
        "t13.py": "QRST 111 @ 9.75\n",                                            # q dentro un altro numero, p a 3 cifre: NO
    })
    e = vp.controllo_lotti(tree, lotti)
    assert sorted((h.file, h.riga) for h in e.hit) == [
        ("t1.py", 1), ("t12.py", 2), ("t2.py", 1), ("t3.py", 1), ("t8.py", 2), ("t9.py", 1)]
    assert [h.token for h in e.hit if h.file == "t1.py"] == ["ABCD.MI…(7)"]      # il ticker intero + lunghezza
    assert all(not any(c.isdigit() for c in h.token.split("…")[0]) for h in e.hit)   # mai quantita' o prezzo
    assert "3 lotti" in e.note
    assert vp.controllo_lotti(_tree(**PULITO), lotti).ok


def test_lotti_dal_db_e_dalle_tuple_insieme_un_hit_per_ticker_e_riga(tmp_path):
    """Due lotti dello stesso ticker (o lo stesso lotto da DB e da tuple) sulla stessa riga
    valgono UN hit per ticker; ticker DB e ticker delle tuple sono distinti nel token."""
    p = tmp_path / "tuple.py"
    p.write_text(TUPLE_FINTE, encoding="utf-8")
    lotti = vp.lotti_db(_db_lotti(tmp_path)) | vp.lotti_da_tuple(str(p)) | {("WXYZ", 5.0, 312.47)}
    tree = _tree(**PULITO, **{"a.py": "WXYZ 3 @ 312.47\n", "b.py": "ABCD 613 @ 3117.46\n"})
    e = vp.controllo_lotti(tree, lotti)
    assert [(h.file, h.token) for h in e.hit] == [("a.py", "WXYZ…(4)"), ("b.py", "ABCD…(4)"), ("b.py", "ABCD.MI…(7)")]


def test_lotti_numera_le_righe_come_l_editor_anche_con_crlf(tmp_path):
    lotti = vp.lotti_db(_db_lotti(tmp_path))
    e = vp.controllo_lotti(_tree(**PULITO, **{"t.py": "x = 1\r\nWXYZ 3 @ 312.47\r\n"}), lotti)
    assert [(h.file, h.riga) for h in e.hit] == [("t.py", 2)]


def test_lotti_rispetta_una_eccezione_per_file_e_ticker(tmp_path):
    lotti = vp.lotti_db(_db_lotti(tmp_path))
    tree = _tree(**PULITO, **{"t1.py": "ABCD.MI 613 @ 3117.46\n", "t3.py": "WXYZ 3 @ 312,47\n"})
    e = vp.controllo_lotti(tree, lotti, eccezioni={("t3.py", "lotti", "WXYZ")})
    assert [h.file for h in e.hit] == ["t1.py"] and "1 eccezione applicata" in e.note


def test_zero_lotti_e_un_ko_non_un_verde():
    e = vp.controllo_lotti(_tree(**PULITO), set())
    assert e.errore and not e.ok


def test_lotti_quantita_a_4_cifre_da_sola_vale_come_il_prezzo():
    lotti = {("QRST", 1234.0, 9.75)}
    tree = _tree(**PULITO, **{"a.py": "QRST 1234\n", "b.py": "QRST 1.234\n", "c.py": "QRST 9.75\n"})
    assert sorted(h.file for h in vp.controllo_lotti(tree, lotti).hit) == ["a.py", "b.py"]


# --- review T5: un test per finding, cifre e ticker finti verificati contro DB e lista privata ---

def test_lotti_cade_anche_in_cella_csv_o_array_compatto():
    """Review T5 F1: in 'T,q,p' la virgola dopo la quantita' intera la faceva sparire ai confini
    numerici di _regex_numero (45/136 lotti veri passavano in forma CSV — la riga stessa di
    importa_trade_csv — e nessun altro controllo li prendeva). Ogni forma si cerca anche sulla
    riga con le virgole sostituite da spazi; un numero italiano con la virgola resta un numero."""
    lotti = {("QRST", 11.0, 9.75), ("KLMN", 1234.0, 9.75), ("WXYZ", 3.0, 312.47)}
    tree = _tree(**PULITO, **{
        "a.csv": "QRST,11,9.75\n",
        "b.csv": "2026-01-15,QRST,BUY,11,9.75,EUR,\n",            # la riga di importa_trade_csv
        "c.py": '["QRST",11,9.75]\n',
        "d.csv": "KLMN,1234,9.75\n",                              # q a 4 cifre seguita dalla virgola
        "e.csv": "WXYZ,3,312,936\n",                              # la parte intera di p seguita da un'altra colonna
        "f.py": "QRST 11,5\n",                                    # un numero italiano: NO
        "g.py": "WXYZ 3 posizioni, 5 trade\n",                    # NO
    })
    assert sorted(h.file for h in vp.controllo_lotti(tree, lotti).hit) == ["a.csv", "b.csv", "c.py", "d.csv", "e.csv"]


def test_forme_lotto_notazione_inglese():
    """Review T5 F2: 15/15 quantita' e 18/18 prezzi da 1.000 in su del book passavano scritti
    come li formatta il ':,' di Python (22 file del tree) o un export inglese; '1,234' ha i
    confini di un intero ('1,234,567' e' un altro numero)."""
    assert {"1,234"} <= vp._forme_lotto(1234)
    assert {"3,117.46", "3,117.5", "3,117"} <= vp._forme_lotto(3117.456789)
    assert {"1,000.00", "1.000,00"} <= vp._forme_lotto(999.996)
    lotti = {("KLMN", 1234.0, 9.75), ("ABCD.MI", 613.0, 3117.456789)}
    tree = _tree(**PULITO, **{"a.py": "KLMN 1,234 @ 9.75\n", "b.md": "carico ABCD 3,117.46\n",
                              "c.py": "x = [KLMN, 1,234,567]\n"})
    assert sorted(h.file for h in vp.controllo_lotti(tree, lotti).hit) == ["a.py", "b.md"]


def test_forme_lotto_a_3_e_4_decimali_e_le_cifre_si_contano_sul_valore():
    """Review T5 F3: un prezzo ricopiato con round(p, 3) o '%.4f' (117-121/136 lotti veri
    passavano anche con la quantita' accanto). Le cifre della regola «4+ da sola» sono quelle
    del VALORE (_significative): uno zero di padding ('9.750') non ne aggiunge."""
    assert {"3117.457", "3117.4568", "3.117,4568"} <= vp._forme_lotto(3117.456789)
    assert {"9.750", "9.7500"} <= vp._forme_lotto(9.75)
    assert (vp._significative(9.75), vp._significative(1000.0), vp._significative(3117.456789)) == (3, 4, 10)
    lotti = {("QRST", 11.0, 9.75), ("ABCD.MI", 613.0, 3117.456789)}
    tree = _tree(**PULITO, **{"a.py": "QRST 11 @ 9.750\n", "b.py": "ABCD 613 @ 3117.4568\n",
                              "c.py": "QRST 9.750\n", "d.py": "ABCD 3117.457\n"})
    assert sorted(h.file for h in vp.controllo_lotti(tree, lotti).hit) == ["a.py", "b.py", "d.py"]


def test_lotti_da_tuple_dichiara_gli_elementi_non_conformi(tmp_path):
    """Review T5 F4: la regex perdeva IN SILENZIO le tuple che non capiva (sesto campo, apici
    singoli, quantita' negativa), il KO scattava solo a zero. La lista TRADES si legge con ast:
    un elemento che non e' (str, str, str, numero, numero) e' un ValueError coi numeri di riga."""
    p = tmp_path / "t.py"
    p.write_text(TUPLE_FINTE.replace('    ("03/04/2025", "WXYZ",  "SELL",     3,  312.47),\n',
                                     '    ("03/04/2025", "WXYZ",  "SELL",     3,  312.47, "EUR"),\n'), encoding="utf-8")
    with pytest.raises(ValueError, match=r"righe \[4\]"):
        vp.lotti_da_tuple(str(p))
    p.write_text("TRADES = [\n    ('01/02/2025', 'ABCD', 'BUY', 613, 3117.46),   # apici singoli: vale\n]\n", encoding="utf-8")
    assert vp.lotti_da_tuple(str(p)) == {("ABCD", 613.0, 3117.46)}


def test_lotti_quantita_tonda_a_4_cifre_da_sola_e_un_hit():
    """Review T5 F5 (confutato dallo scettico): una quantita' tonda accanto al ticker e' un hit
    come gli altri — la classe di T4 F5, la decide il PM riga per riga in D5 — e la nota conta
    quanti hit vengono da un solo numero tondo, per il rapporto di T8."""
    lotti = {("QRST", 1000.0, 9.75), ("QRST", 11.0, 55.5)}
    tree = _tree(**PULITO, **{"a.py": "QRST 1000\n", "b.py": "QRST 1.000\n", "c.py": "QRST 1000.0\n",
                              "d.py": "QRST 100\n", "e.py": "QRST 1000 @ 9.75\n",
                              "f.py": "QRST 11 @ 55.5 e 1000\n"})      # un altro lotto pieno sulla riga: non e' un tondo
    e = vp.controllo_lotti(tree, lotti)
    assert sorted(h.file for h in e.hit) == ["a.py", "b.py", "c.py", "e.py", "f.py"]
    assert "3 hit da un solo numero tondo" in e.note
    assert "tondo" not in vp.controllo_lotti(_tree(**PULITO, **{"e.py": "QRST 1000 @ 9.75\n"}), lotti).note


def test_forme_lotto_confine_delle_migliaia_e_quantita_frazionaria():
    """Review T5 F6: la forma con le migliaia nasce A 1000 (4 lotti veri stanno esattamente
    sulla soglia); 999 non ne ha; una quantita' frazionaria tiene i decimali (colonne REAL)."""
    assert vp._forme_lotto(1000) == {"1000", "1.000", "1,000"}
    assert vp._forme_lotto(999) == {"999"}
    assert {"0.5", "0,5"} <= vp._forme_lotto(0.5)


def test_lotti_db_tiene_quantita_zero_frazionaria_e_prezzo_zero_e_il_ticker_non_e_preceduto_da_cifre(tmp_path):
    """Review T5 F6: il DB vero ha posizioni chiuse (quantita' 0) col loro prezzo medio: un
    `if t and q and p` le perderebbe zitto; idem un prezzo 0 e una quantita' frazionaria
    (`float(int(q))` la azzera). E una cifra attaccata al ticker non e' il ticker."""
    import sqlite3
    p = str(tmp_path / "z.db")
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE positions(ticker TEXT, quantita REAL, prezzo_medio REAL);
    CREATE TABLE trade_history(ticker TEXT, quantita REAL, prezzo REAL);
    INSERT INTO positions VALUES ('ZERO', 0, 9.75);
    INSERT INTO positions VALUES ('GRATIS', 11, 0);
    INSERT INTO trade_history VALUES ('FRAC', 0.5, 9.75);
    """)
    c.commit()
    c.close()
    lotti = vp.lotti_db(p)
    assert lotti == {("ZERO", 0.0, 9.75), ("GRATIS", 11.0, 0.0), ("FRAC", 0.5, 9.75)}
    tree = _tree(**PULITO, **{"a.py": "FRAC 0.5 @ 9.75\n", "b.py": "9QRST 11 @ 9.75\n", "c.py": "ZERO 0 @ 9.75\n"})
    assert sorted(h.file for h in vp.controllo_lotti(tree, lotti | {("QRST", 11.0, 9.75)}).hit) == ["a.py", "c.py"]


def test_buco_dichiarato_ticker_minuscolo(tmp_path):
    """Pinna un BUCO DICHIARATO (docstring di controllo_lotti): il ticker in minuscolo non si
    cerca, ne' dal pre-filtro `base not in testi[rel]` ne' dalla regex. Chi lo chiude (T9/D5)
    capovolge QUESTO test e cambia entrambi i punti: uno solo non basta (review T5 F8)."""
    lotti = vp.lotti_db(_db_lotti(tmp_path))
    assert vp.controllo_lotti(_tree(**PULITO, **{"m.py": "abcd 613 3117.46\n"}), lotti).ok


def test_lotti_dei_ticker_londra_cercati_anche_in_gbp():
    """Review T5 F9: per i ticker .L il DB tiene il prezzo in GBX; una fixture che lo scrive in
    GBP (p/100) passava. Solo per i .L: per gli altri p/100 e' un altro numero."""
    lotti = {("ABCD.L", 3.0, 312.47), ("WXYZ", 3.0, 312.47)}
    tree = _tree(**PULITO, **{"a.py": "ABCD.L 3 @ 3.1247\n", "b.py": "ABCD 3 @ 3.12\n", "c.py": "WXYZ 3 @ 3.1247\n"})
    assert sorted(h.file for h in vp.controllo_lotti(tree, lotti).hit) == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# F1 (04/09): controllo 9 — i ticker del DB cercati DA SOLI.
# Il difetto del 03/09 in una riga: `controllo_lotti` segnala il ticker solo se sulla
# stessa riga ci sono anche quantita' E prezzo (sua docstring: «i ticker sono pubblici,
# decisione PM 02/09»). Un elenco NUDO di simboli non e' un lotto, non e' un valore di
# colonna numerica, non e' in VIETATE: nessuno degli 8 controlli poteva vederlo, e il
# book era ricostruibile dal sorgente. Questo controllo cerca il simbolo E BASTA.
# ---------------------------------------------------------------------------

def test_un_elenco_nudo_di_ticker_e_cieco_a_lotti_e_lo_vede_il_controllo_nuovo():
    """I due fatti nello stesso test: il controllo vecchio passa, il nuovo cade."""
    tree = _tree(**PULITO, **{"tassonomia.py": 'SETTORI = {"WXYZ": "tech", "QRST": "energy"}\n'})
    assert vp.controllo_lotti(tree, LOTTI_FINTI).ok          # il buco del 03/09
    esito = vp.controllo_ticker_soli(tree, {"WXYZ", "QRST"})
    assert not esito.ok
    assert sorted((h.file, h.riga) for h in esito.hit) == [("tassonomia.py", 1), ("tassonomia.py", 1)]


def _db_ticker(tmp_path):
    """_db_finto + i PREFERITI, che nessun controllo guardava: un titolo messo tra i
    preferiti e mai comprato identifica chi lo scrive esattamente come una posizione."""
    import sqlite3
    p = _db_finto(tmp_path)
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE favorite_companies(ticker TEXT, name TEXT);
    INSERT INTO favorite_companies VALUES ('KLMN.PA','Finta SA');
    INSERT INTO favorite_companies VALUES ('','senza ticker');
    INSERT INTO positions VALUES ('  qrst.l  ', 11, 9.75);
    """)
    c.commit()
    c.close()
    return p


def test_ticker_db_prende_le_basi_dalle_tre_tabelle_preferiti_compresi(tmp_path):
    """Base = prima del punto, senza spazi, maiuscola; ticker vuoto saltato."""
    assert vp.ticker_db(_db_ticker(tmp_path)) == {"ABCD", "WXYZ", "KLMN", "QRST"}


def test_zero_ticker_e_un_ko_non_un_verde():
    """Come per i lotti: un insieme vuoto vuol dire DB sbagliato o schema cambiato, non
    «niente da cercare» (regola 14/07: mai un verde per assenza di misura)."""
    esito = vp.controllo_ticker_soli(_tree(**PULITO), set())
    assert not esito.ok
    assert "nessun ticker" in esito.errore


def test_una_tabella_che_manca_e_dichiarata_non_saltata(tmp_path):
    """Se domani lo schema perde una tabella, il controllo deve ROMPERSI a voce: cercare
    su due tabelle su tre e dire OK e' la classe di fuga del 03/09."""
    import sqlite3
    p = str(tmp_path / "monca.db")
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE positions(ticker TEXT, quantita REAL, prezzo_medio REAL);"
                    "INSERT INTO positions VALUES ('WXYZ',3,1.5);")
    c.commit()
    c.close()
    with pytest.raises(sqlite3.OperationalError, match="trade_history"):
        vp.ticker_db(p)


def test_un_controllo_in_osservazione_conta_ma_non_ferma_e_il_verdetto_lo_dichiara(capsys):
    """Il nono controllo nasce in osservazione: oggi darebbe ~1.342 riscontri e un cancello
    sempre rosso viene spento (lezione «una guardia che grida al lupo»). Ma il verdetto non
    puo' dire PULITO e basta — sarebbe di nuovo una frase piu' larga della misura: deve dire
    su COSA e' pulito e quanto resta scoperto."""
    esiti = [vp.Esito("vietate"),
             vp.Esito("ticker_soli", [vp.Hit("m.py", 1, "WX…(4)")], osservazione=True)]
    rc = vp.verdetto(esiti)
    testo = capsys.readouterr().out
    assert rc == 0
    assert "OSSERVAZIONE" in testo
    assert "PULITO sui controlli bloccanti" in testo
    assert "1 riscontro in osservazione" in testo
    assert "ticker_soli" in testo.splitlines()[-1]


def test_lo_stesso_esito_fuori_dall_osservazione_ferma_il_cancello():
    """Il giorno in cui il conto e' sceso, il nono controllo si sposta fra i bloccanti
    togliendo il suo nome da OSSERVAZIONE: questo test dice che quella riga BASTA."""
    hit = [vp.Hit("m.py", 1, "WX…(4)")]
    assert vp.verdetto([vp.Esito("ticker_soli", list(hit), osservazione=True)]) == 0
    assert vp.verdetto([vp.Esito("ticker_soli", list(hit))]) == 1


def test_un_ko_in_osservazione_resta_dichiarato_anche_se_non_ferma(capsys):
    """Un controllo che non ha potuto misurare e' peggio di uno con riscontri: se non
    blocca deve almeno GRIDARE, altrimenti sparisce."""
    rc = vp.verdetto([vp.Esito("ticker_soli", errore="DB assente", osservazione=True)])
    testo = capsys.readouterr().out
    assert rc == 0
    assert "KO" in testo and "OSSERVAZIONE" in testo
    assert "1 controllo in osservazione NON eseguito" in testo


def test_la_nota_dice_quanti_ticker_cercati_quanti_presenti_e_in_quanti_file():
    """Ogni altro controllo dichiara la dimensione della sua misura. Questo deve dire anche
    QUANTI simboli sono usciti e in quanti file: e' il numero che deve scendere fino a zero
    prima che il controllo diventi bloccante."""
    tree = _tree(**PULITO, **{"a.py": "WXYZ\n", "b.py": "WXYZ e QRST\n"})
    esito = vp.controllo_ticker_soli(tree, {"WXYZ", "QRST", "KLMN"})
    assert esito.note == "3 ticker cercati, 2 presenti in 2 file (prima delle eccezioni)"
    assert len(esito.hit) == 3


def test_una_eccezione_motivata_toglie_l_hit_e_la_colonna_conta_il_residuo():
    """Le due deroghe del PM (DAT, US_OPTIONS) vivranno qui: una riga per file e simbolo,
    col motivo scritto accanto — non un insieme cablato nel codice."""
    tree = _tree(**PULITO, **{"signal_engine.py": "WXYZ\n", "altro.py": "WXYZ\n"})
    ecc = {("signal_engine.py", "ticker_soli", "WXYZ")}
    esito = vp.controllo_ticker_soli(tree, {"WXYZ"}, ecc)
    assert [h.file for h in esito.hit] == ["altro.py"]
    assert "1 eccezione applicata" in esito.note


def test_il_token_stampato_e_mascherato_non_il_simbolo_intero():
    """PIN, non ciclo rosso-verde: `controllo_lotti` stampa il ticker INTERO sulla premessa
    «i ticker sono pubblici» — la premessa da cui il 03/09 e' uscito il book. L'output del
    cancello finisce nei log e in chat: qui il simbolo non si scrive."""
    esito = vp.controllo_ticker_soli(_tree(**PULITO, **{"m.py": "WXYZ\n"}), {"WXYZ"})
    assert [h.token for h in esito.hit] == ["WX…(4)"]


def test_i_confini_non_prendono_il_simbolo_dentro_una_parola():
    """PIN dei confini: una base di 2-3 caratteri (ne abbiamo) dentro una parola piu' lunga
    non e' un riscontro, ma attaccata a un punto (la forma «BASE.BORSA») si'.
    La sigla d'esempio e' INVENTATA e verificata assente dal DB: la prima stesura di questo
    test usava una base VERA del book, e a trovarla e' stato il controllo che sta provando.
    `d.py` serve al confine SINISTRO da solo: senza di esso il banco di mutazioni ha visto
    sopravvivere la caduta del `(?<!...)`, perche' in «AZQM» a fermare tutto bastava il
    confine destro (banco 04/09, mutazione sopravvissuta)."""
    tree = _tree(**PULITO, **{"a.py": "ZQUOVO AZQM zq\n", "b.py": "ZQ\n", "c.py": "ZQ.MI\n",
                              "d.py": "AZQ = 1\n"})
    assert sorted(h.file for h in vp.controllo_ticker_soli(tree, {"ZQ"}).hit) == ["b.py", "c.py"]


def test_il_simbolo_attaccato_a_un_trattino_basso_e_un_riscontro():
    """Trovato dagli scettici (04/09): il confine escludeva `_`, quindi la forma `px_BASE` —
    un identificatore che NOMINA il titolo — restava invisibile. Sul perimetro vero erano 59
    riscontri su 49 righe in 7 file. Il trattino basso non protegge niente: separa e basta."""
    tree = _tree(**PULITO, **{"a.py": "px_ZQ = 1\n", "b.py": "ZQ_close = 2\n", "c.py": "AZQM = 3\n"})
    assert sorted(h.file for h in vp.controllo_ticker_soli(tree, {"ZQ"}).hit) == ["a.py", "b.py"]


def test_il_simbolo_nel_NOME_DEL_FILE_e_un_riscontro():
    """Trovato dagli scettici (04/09): ogni controllo gira su `_testi(tree)` e l'unico che
    guarda i percorsi (controllo_esclusi) li confronta con glob, mai coi ticker. Un file
    chiamato `test_BASE_qualcosa.py` si legge nell'ELENCO FILE di GitHub senza aprire nulla.
    Riga 0 come in controllo_esclusi: il riscontro e' il percorso, non una riga."""
    tree = _tree(**PULITO, **{"tests/test_zq_fonte.py": "x = 1\n", "docs/zquadro.md": "y\n"})
    hit = vp.controllo_ticker_soli(tree, {"ZQ"}).hit
    assert [(h.file, h.riga) for h in hit] == [("tests/test_zq_fonte.py", 0)]


def test_buco_dichiarato_il_simbolo_in_minuscolo(tmp_path):
    """Era il PIN di un BUCO DICHIARATO («si cerca com'e' scritto nel DB, maiuscolo»): capovolto
    il 05/09 (buco D5, 4b) per le basi dalle 4 lettere, che ora si cercano anche in minuscolo
    come nel controllo 10. RESTA dichiarato sotto le 4 lettere: su basi di 2-3 lettere il
    minuscolo sommergerebbe il conto di collisioni («qr» in «quadro»)."""
    assert not vp.controllo_ticker_soli(_tree(**PULITO, **{"m.py": "wxyz\n"}), {"WXYZ"}).ok
    assert vp.controllo_ticker_soli(_tree(**PULITO, **{"m.py": "la parola qrs minuscola\n"}), {"QRS"}).ok


def test_il_nono_controllo_e_nella_lista_e_marcato_osservazione():
    """Pin del cablaggio: se domani qualcuno lo toglie dai CONTROLLI, `esegui_tutti` non lo
    esegue piu' e nessun altro test se ne accorge (lezione «testare l'helper non e' testare
    il cablaggio»)."""
    assert "ticker_soli" in vp.CONTROLLI
    assert "ticker_soli" in vp.OSSERVAZIONE


def test_esegui_tutti_esegue_davvero_il_nono_controllo(tmp_path, monkeypatch, capsys):
    """CABLAGGIO, non helper. Se il ramo di `esegui_tutti` sparisce, il controllo non gira
    e nessun altro test se ne accorge: `non_eseguiti` non lo vedrebbe, perche' il nome resta
    in `da_fare`. Il cancello direbbe la sua riga in meno e nessuno la cercherebbe."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "m.py").write_text('SETTORI = {"WXYZ": "tech"}\n', encoding="utf-8")
    db = _db_ticker(tmp_path)
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))
    rc = vp.esegui_tutti(str(tree), solo=["ticker_soli"])
    testo = capsys.readouterr().out
    assert "ticker_soli" in testo and "WX…(4)" in testo and "OSSERVAZIONE" in testo
    assert rc == 2      # gli altri otto non eseguiti: un cancello parziale non e' mai verde


def test_un_controllo_registrato_e_mai_cablato_non_passa_inosservato(tmp_path, monkeypatch, capsys):
    """IL BUCO CHE AVREBBE RESO INERTE IL DECIMO CONTROLLO (04/09). `da_fare` nasce da
    CONTROLLI, ma il corpo di `esegui_tutti` e' una catena if/elif SENZA `else`: un nome
    registrato nella tupla e mai cablato veniva saltato in silenzio, e `non_eseguiti` non
    poteva vederlo perche' il nome sta in `da_fare`. Il cancello usciva col suo verdetto
    come se fosse girato tutto — cioe' registrare il decimo controllo senza scriverne il
    ramo avrebbe dato «PULITO» senza aver misurato niente."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "m.py").write_text("ok\n", encoding="utf-8")
    db = _db_ticker(tmp_path)
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))
    monkeypatch.setattr(vp, "CONTROLLI", tuple(vp.CONTROLLI) + ("mai_cablato",))
    rc = vp.esegui_tutti(str(tree), solo=["mai_cablato"])
    testo = capsys.readouterr().out
    assert "mai_cablato" in testo and "NON e' stato eseguito" in testo
    assert "PULITO" not in testo
    assert rc == 2


def test_quando_si_deposita_l_osservazione_torna_a_bloccare(tmp_path, monkeypatch, capsys):
    """La cura che spegne lascia acceso il peggio (trovato dagli scettici, 04/09): in
    osservazione il cancello esce 0 — e `export_pubblico` deposita proprio su `rc == 0`,
    quindi il book uscirebbe lo stesso, con la riga di verdetto che lo dice a un lettore
    umano e a nessuna macchina. L'osservazione non deve fermare la MISURA (il dry-run
    serve a vedere il conto scendere), ma deve fermare il DEPOSITO."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "m.py").write_text('SETTORI = {"WXYZ": "tech"}\n', encoding="utf-8")
    db = _db_ticker(tmp_path)
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))

    vp.esegui_tutti(str(tree), solo=["ticker_soli"])
    misura = capsys.readouterr().out
    assert "OSSERVAZIONE (non blocca)" in misura

    vp.esegui_tutti(str(tree), solo=["ticker_soli"], blocca_osservazione=True)
    deposito = capsys.readouterr().out
    assert "STOP" in deposito and "OSSERVAZIONE" not in deposito


# ---------------------------------------------------------------------------
# T6: controllo 1 — gitleaks pinnato. Niente rete e niente binario: `fetch` e `esegui`
# sono iniettati, quindi la batteria gira anche su CI Linux (dove gitleaks.exe non c'e').
# Il motore e' di terzi: ogni suo esito ambiguo deve diventare un KO DICHIARATO, mai un
# «0 hit» (regola 14/07). gitleaks esce 1 sia per «leak trovati» sia per «scansione
# fallita» (cmd/root.go v8.30.1: `if err != nil { os.Exit(1) }` viene PRIMA di
# `os.Exit(exitCode)`), percio' il codice dei leak lo spostiamo su GITLEAKS_EXIT_HIT.
# ---------------------------------------------------------------------------

def _zip_finto(nome="gitleaks.exe"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(nome, b"MZ finto")
        z.writestr("README.md", b"x")
    return buf.getvalue()


def _exe_finto(tmp_path):
    exe = tmp_path / "gitleaks.exe"
    exe.write_bytes(b"MZ finto")
    return str(exe)


def _peso(root):
    """I byte dei file sotto `root`: quanti gitleaks ne dichiara di aver letto quando li legge tutti."""
    return sum(os.path.getsize(os.path.join(c, n)) for c, _, nomi in os.walk(root) for n in nomi)


def _gitleaks_finto(finding=(), rc=None, versione=None, rc_versione=0, visti=None,
                    stderr=None, scrivi_report=True, letti=None):
    """Finto `esegui`: risponde sia a `version` sia a `dir`, e REGISTRA in `visti` gli
    argomenti e i kwargs di ogni chiamata (un test pretende COSA e' stato lanciato).
    Su `dir` dichiara i byte letti come fa il motore vero (riga «INF scanned ~N bytes»):
    `letti=None` = li ha letti tutti, un numero = ne ha saltati."""
    def esegui(args, **kw):
        if visti is not None:
            visti.append((list(args), kw))
        if args[1:2] == ["version"]:
            detta = vp.GITLEAKS_VERSIONE if versione is None else versione
            return types.SimpleNamespace(returncode=rc_versione, stdout=detta + "\n", stderr="")
        rep = args[args.index("--report-path") + 1]
        if scrivi_report:
            with open(rep, "w", encoding="utf-8") as fh:
                fh.write(finding if isinstance(finding, str) else json.dumps(list(finding)))
        codice = rc if rc is not None else (vp.GITLEAKS_EXIT_HIT if finding else 0)
        detto = stderr
        if detto is None:
            n = _peso(args[2]) if letti is None else letti
            detto = "INF scanned ~%d bytes (%d bytes) in 3ms" % (n, n)
        return types.SimpleNamespace(returncode=codice, stdout="", stderr=detto)
    return esegui


def test_scarica_gitleaks_rifiuta_sha256_diverso_e_non_scrive(tmp_path):
    dest = tmp_path / "bin" / "gitleaks.exe"
    with pytest.raises(RuntimeError, match="sha256"):
        vp.scarica_gitleaks("https://esempio.invalid/x.zip", "0" * 64, str(dest), fetch=lambda u: _zip_finto())
    assert not dest.exists()


def test_scarica_gitleaks_estrae_solo_l_exe_se_l_impronta_torna(tmp_path):
    dati = _zip_finto()
    dest = tmp_path / "bin" / "gitleaks.exe"
    out = vp.scarica_gitleaks("https://esempio.invalid/x.zip", hashlib.sha256(dati).hexdigest(),
                              str(dest), fetch=lambda u: dati)
    assert out == str(dest) and dest.read_bytes() == b"MZ finto"
    assert sorted(p.name for p in (tmp_path / "bin").iterdir()) == ["gitleaks.exe"]


def test_scarica_gitleaks_dichiara_uno_zip_senza_l_exe(tmp_path):
    dati = _zip_finto("altro.exe")
    dest = tmp_path / "bin" / "gitleaks.exe"
    with pytest.raises(RuntimeError, match="gitleaks.exe"):
        vp.scarica_gitleaks("https://esempio.invalid/x.zip", hashlib.sha256(dati).hexdigest(),
                            str(dest), fetch=lambda u: dati)
    assert not dest.exists()


def test_gitleaks_assente_e_un_ko_dichiarato_non_un_salto(tmp_path):
    """La frase intera, non la parola: `tmp_path` porta dentro il NOME del test, quindi
    `"assente" in e.errore` sarebbe vero per via del percorso anche senza la guardia
    (mutante M03 del banco: sopravvive a un'asserzione cosi')."""
    e = vp.controllo_gitleaks(str(tmp_path), exe=str(tmp_path / "manca.exe"))
    assert e.errore.startswith("binario assente:") and "--scarica-gitleaks" in e.errore and not e.ok


def test_gitleaks_cade_sui_finding_del_report_senza_il_segreto(tmp_path):
    """Il token dell'hit e' il NOME DELLA REGOLA: il segreto non entra mai nell'Esito.
    Il secondo finding e' annidato: il percorso arriva relativo al tree e in forma posix."""
    tree = tmp_path / "tree"
    tree.mkdir()
    # i due `Secret` non hanno forma di chiave apposta: una stringa realistica in un file di
    # tests/ sarebbe un finding VERO di gitleaks sul tree pubblico (misurato: lo era)
    finding = [{"RuleID": "generic-api-key", "File": str(tree / "config.py"), "StartLine": 7,
                "Secret": "questo-non-deve-uscire"},
               {"RuleID": "aws-access-key", "File": str(tree / "app" / "src" / "lib" / "api.ts"),
                "StartLine": 12, "Secret": "nemmeno-questo"}]
    e = vp.controllo_gitleaks(str(tree), exe=_exe_finto(tmp_path), esegui=_gitleaks_finto(finding))
    assert [(h.file, h.riga, h.token) for h in e.hit] == [("config.py", 7, "generic-api-key"),
                                                          ("app/src/lib/api.ts", 12, "aws-access-key")]
    assert "non-deve-uscire" not in str(e) and "nemmeno" not in str(e)
    assert vp.controllo_gitleaks(str(tree), exe=_exe_finto(tmp_path), esegui=_gitleaks_finto([])).ok


def test_gitleaks_che_esplode_e_un_ko_dichiarato(tmp_path):
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto(rc=126, stderr="unknown flag: --redact"))
    assert e.errore and "126" in e.errore and "unknown flag" in e.errore and not e.ok


def test_gitleaks_exit_1_e_una_scansione_fallita_non_un_tree_pulito(tmp_path):
    """IL BUCO PIU' GRAVE del disegno originale: gitleaks esce 1 anche quando la scansione
    muore (config illeggibile, cartella sparita, giro parziale) e scrive un report vuoto.
    Con `--exit-code 1` quel guasto sarebbe uscito «gitleaks 0 OK» -> VERDETTO PULITO."""
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto([], rc=1, stderr="failed scan directory"))
    assert "exit 1" in e.errore and "failed scan directory" in e.errore and not e.ok


def test_gitleaks_senza_report_e_un_ko_anche_se_esce_bene(tmp_path):
    """gitleaks scrive il report PRIMA di uscire: se non c'e', la scansione non e' arrivata
    in fondo (os.Stat del tree in `logging.Fatal`). Un file assente non e' «zero finding»."""
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto(scrivi_report=False))
    assert "nessun report" in e.errore and "NON conclusa" in e.errore and not e.ok


@pytest.mark.parametrize("contenuto,atteso", [("{non e' json", "report illeggibile"),
                                              ('{"finding": []}', "report di forma ignota")])
def test_gitleaks_report_illeggibile_o_di_forma_ignota_e_un_ko(tmp_path, contenuto, atteso):
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto(contenuto, rc=0))
    assert atteso in e.errore and not e.ok


def test_gitleaks_e_puntato_al_tree_e_alle_regole_PINNATE(tmp_path, monkeypatch):
    """Il bersaglio e' il tree passato (non la cartella corrente) e le regole sono le nostre:
    `gitleaks:allow` ignorato, `--gitleaks-ignore-path` tolto dal default '.' (che sarebbe la
    cwd di chi lancia), le due variabili d'ambiente che sostituiscono la configurazione via."""
    monkeypatch.setenv("GITLEAKS_CONFIG", "C:\\finto\\regole.toml")
    monkeypatch.setenv("GITLEAKS_CONFIG_TOML", "[[rules]]")
    tree = tmp_path / "tree"
    tree.mkdir()
    visti = []
    e = vp.controllo_gitleaks(str(tree), exe=_exe_finto(tmp_path), esegui=_gitleaks_finto(visti=visti))
    args, kw = visti[-1]
    assert args[1:3] == ["dir", str(tree)]
    assert "--no-banner" in args and "--redact" in args and "--ignore-gitleaks-allow" in args
    assert args[args.index("--report-format") + 1] == "json"
    codice = args[args.index("--exit-code") + 1]
    assert codice == str(vp.GITLEAKS_EXIT_HIT)
    # 0 = pulito, 1 = guasto (o leak in una scansione parziale), 126 = flag ignoto: il codice
    # dei leak dev'essere un ALTRO, o «trovati» e «non eseguito» tornano indistinguibili
    assert codice not in ("0", "1", "126")
    ignora = args[args.index("--gitleaks-ignore-path") + 1]
    assert ignora != "." and ignora == os.path.dirname(args[args.index("--report-path") + 1])
    # senza --no-color i percorsi su stderr arrivano con i codici ANSI in mezzo (`path=\x1b[36m"...`)
    # e la copertura non si legge; debug basta per i nomi dei saltati, trace stamperebbe i SEGRETI
    assert "--no-color" in args and args[args.index("--log-level") + 1] == "debug"
    assert "GITLEAKS_CONFIG" not in kw["env"] and "GITLEAKS_CONFIG_TOML" not in kw["env"]
    assert kw["env"].get("PATH") == os.environ.get("PATH")   # ripulito, non azzerato
    # il contratto con subprocess vale per ENTRAMBE le chiamate: senza `encoding` capture_output
    # torna BYTES (lezione «subprocess Windows: encoding esplicito»), senza capture_output stderr e' None
    assert [a[1] for a, _ in visti] == ["version", "dir"]
    for _, k in visti:
        assert k.get("capture_output") is True and k.get("encoding") == "utf-8" and k.get("errors") == "replace"
    assert e.ok


def test_gitleaks_che_legge_meno_byte_del_tree_e_un_ko_non_un_pulito(tmp_path):
    """LA COPERTURA E' UNA MISURA, NON UNA DEDUZIONE (review T6 F1). Il motore salta di suo dei
    file (allowlist globale del suo config: lockfile, node_modules, .png, .zip, archivi oltre la
    profondita' massima, file che non riesce ad aprire) e la scansione esce comunque 0 con report
    vuoto: sul tree vero erano 357.316 byte su 5.887.695, dichiarati «0 hit, PULITO»."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "visto.py").write_bytes(b"x = 1\n")
    (tree / "saltato.json").write_bytes(b"y" * 500)
    e = vp.controllo_gitleaks(str(tree), exe=_exe_finto(tmp_path), esegui=_gitleaks_finto(letti=6))
    assert not e.ok and "500 byte" in e.errore and "NON scansionati" in e.errore


def test_gitleaks_e_verde_se_i_byte_mancanti_sono_quelli_dichiarati_fuori_dal_raggio(tmp_path):
    """L'unico file del perimetro che il motore salta per disegno suo e' dichiarato nel codice:
    resta verde, ma il suo nome finisce nella nota (lo leggono gli altri controlli, in memoria)."""
    fuori = vp.GITLEAKS_SCOPERTI[0]
    tree = tmp_path / "tree"
    (tree / os.path.dirname(fuori)).mkdir(parents=True)
    (tree / fuori).write_bytes(b"z" * 700)
    (tree / "visto.py").write_bytes(b"x = 1\n")
    e = vp.controllo_gitleaks(str(tree), exe=_exe_finto(tmp_path), esegui=_gitleaks_finto(letti=6))
    assert e.ok and fuori in e.note and "6/706" in e.note


def test_gitleaks_che_non_dice_quanti_byte_ha_letto_e_un_ko(tmp_path):
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto(stderr="INF no leaks found"))
    assert not e.ok and "copertura NON misurata" in e.errore


@pytest.mark.parametrize("nome", [".gitleaks.toml", ".gitleaksignore"])
def test_gitleaks_rifiuta_un_tree_che_porta_le_sue_regole(tmp_path, nome):
    """gitleaks legge SEMPRE `{tree}/.gitleaks.toml` come configurazione (cmd/root.go: «if
    config path is not set, then use the {source}/.gitleaks.toml path») e `{tree}/.gitleaksignore`
    anche quando `--gitleaks-ignore-path` punta altrove: un file cosi' nel tree esportato
    spegnerebbe le regole IN SILENZIO. Meglio non scansionare e dirlo."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / nome).write_text("# spegnimi\n", encoding="utf-8")
    e = vp.controllo_gitleaks(str(tree), exe=_exe_finto(tmp_path), esegui=_gitleaks_finto())
    assert e.errore and nome in e.errore and not e.ok


@pytest.mark.parametrize("detta", ["8.18.0", vp.GITLEAKS_VERSIONE + "1"])
def test_gitleaks_di_versione_diversa_dal_pin_e_un_ko(tmp_path, detta):
    """La seconda e' quella che una verifica «per contenuto» (`in`) lascerebbe passare."""
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto(versione=detta))
    assert detta in e.errore and vp.GITLEAKS_VERSIONE in e.errore and not e.ok


def test_gitleaks_version_che_fallisce_e_un_ko_anche_se_stampa_il_numero_giusto(tmp_path):
    """Un exe che stampa la versione giusta ma esce != 0 non e' un binario sano."""
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto(rc_versione=1))
    assert vp.GITLEAKS_VERSIONE in e.errore and not e.ok


def test_la_nota_porta_la_versione_LETTA_dal_binario_non_la_costante(tmp_path):
    """Lezione del progetto: una garanzia dichiarata dev'essere una MISURA. La nota diceva
    «gitleaks 8.30.1» dopo aver solo verificato che a quel percorso ESISTESSE un file."""
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path),
                              esegui=_gitleaks_finto(versione="v" + vp.GITLEAKS_VERSIONE))
    assert e.ok and vp.GITLEAKS_VERSIONE in e.note and "letta" in e.note


def test_gitleaks_non_eseguibile_e_un_ko_dichiarato_non_un_traceback(tmp_path):
    """Un exe troncato o di un'altra architettura: OSError (WinError 193) invece di uscire."""
    def esegui(args, **kw):
        raise OSError(8, "Formato di %s non valido" % args[0])
    e = vp.controllo_gitleaks(str(tmp_path), exe=_exe_finto(tmp_path), esegui=esegui)
    assert e.errore.startswith("binario non eseguibile:") and not e.ok


def test_env_eccezione_motivata_toglie_l_hit_e_la_dichiara():
    """La regola «un valore del .env non ha mai un motivo per stare nel pubblico» e' stata
    falsificata il 03/09: il PM ha deciso che il suo nome sta nel NOTICE, e quello stesso nome
    il `.env` lo tiene in una variabile. La chiave dell'eccezione e' il NOME della variabile,
    mai il valore: il file delle eccezioni non deve contenere un segreto."""
    tree = _tree(**PULITO, **{"NOTICE.txt": "Copyright 2026 Mario Rossi Finto\n"})
    coppie = [("NOME_PM", "Mario Rossi Finto")]
    assert vp.controllo_env(tree, coppie).hit                      # senza eccezione, ferma
    e = vp.controllo_env(tree, coppie, eccezioni=[("NOTICE.txt", "env", "NOME_PM")])
    assert e.ok and "1 eccezione applicata" in e.note


def test_env_eccezione_che_non_copre_nulla_e_dichiarata():
    e = vp.controllo_env(_tree(**PULITO), [("A", "abcdefgh")],
                         eccezioni=[("mai.py", "env", "A")])
    assert e.ok and "senza riscontro" in e.note


# ---------------------------------------------------------------------------------------
# T10 (04/09): controllo 10, il PAYLOAD — il testo SPEDITO AI MODELLI.
# I nove controlli precedenti misurano cosa viene PUBBLICATO; questo misura cosa DECIDE.
# Le sigle e i nomi qui sotto sono INVENTATI e verificati assenti dal book (la prima
# stesura del test del nono controllo usava una base VERA, e a trovarla fu il controllo
# che stava provando).
# ---------------------------------------------------------------------------------------

def _db_nomi(tmp_path):
    """_db_ticker + i NOMI per esteso: `positions.nome` non c'e' nel finto di base.
    I due preferiti scendono a 4 caratteri apposta: sotto MIN_NOME devono sparire."""
    import sqlite3
    p = _db_ticker(tmp_path)
    c = sqlite3.connect(p)
    c.executescript("""
    ALTER TABLE positions ADD COLUMN nome TEXT;
    UPDATE positions SET nome='Zorvex Quantum Holdings' WHERE ticker='ABCD.MI';
    UPDATE favorite_companies SET name='Alfa' WHERE ticker='KLMN.PA';
    UPDATE favorite_companies SET name='Beta' WHERE ticker='';
    """)
    c.commit()
    c.close()
    return p


def test_nomi_db_prende_i_nomi_e_scarta_quelli_troppo_corti(tmp_path):
    assert vp.nomi_db(_db_nomi(tmp_path)) == {"Zorvex Quantum Holdings"}


def test_le_parole_distintive_escludono_le_generiche():
    """«Holdings» non deve far scattare ogni riga che dice holdings; «Zorvex» si'.
    «Alfa» e' sotto MIN_PAROLA: e' un buco dichiarato, non una svista."""
    p = vp._parole_distintive({"Zorvex Quantum Holdings", "Alfa Banca SpA"})
    assert set(p) == {"Zorvex", "Quantum"}


def test_payload_un_canale_non_reso_non_e_uno_zero():
    """La regola che rende leggibile uno zero: se un canale non si rende, il controllo e'
    un KO, non un verde. Uno zero su un corpus incompleto e' una bugia."""
    e = vp.controllo_payload({"a": "pulito"}, {"WXYZ"}, guasti=[("tool/x", "ImportError: boom")])
    assert not e.ok and e.errore and "tool/x" in e.errore and "incompleto" in e.errore


def test_payload_corpus_vuoto_e_zero_ticker_sono_KO_non_verdi():
    assert not vp.controllo_payload({}, {"WXYZ"}).ok
    assert not vp.controllo_payload({"a": "x"}, set()).ok


def test_payload_trova_la_base_del_ticker_nel_testo_spedito():
    e = vp.controllo_payload({"desk/finto": "riga uno\nusa WXYZ per il tema\n"}, {"WXYZ"})
    assert [(h.file, h.riga) for h in e.hit] == [("desk/finto", 2)]
    assert e.hit[0].token == "WX…(4)"          # mascherato, mai il simbolo intero


def test_payload_cerca_il_minuscolo_dalle_quattro_lettere_in_su():
    """Il payload e' PROSA: uno slug minuscolo dentro la description di un tool e' un
    riscontro (misurato: nel registro vivo c'e' un nome di gestore tutto minuscolo).
    Sotto le 4 lettere il minuscolo sommergerebbe il conto: BUCO DICHIARATO, e questo
    test lo PIN a come e' scritto, cosi' il giorno che si allarga si vede."""
    assert len(vp.controllo_payload({"c": "wxyz minuscolo"}, {"WXYZ"}).hit) == 1
    assert len(vp.controllo_payload({"c": "la parola qr minuscola"}, {"QR"}).hit) == 0
    assert len(vp.controllo_payload({"c": "la sigla QR maiuscola"}, {"QR"}).hit) == 1


def test_payload_trova_il_nome_per_esteso_anche_senza_ticker_accanto():
    """L'ago che decide se il controllo vale qualcosa: senza i NOMI, il payload puo'
    segnare ZERO col book scritto in chiaro."""
    e = vp.controllo_payload({"capo/system": "come Zorvex Quantum Holdings insegna"},
                             {"WXYZ"}, {"Zorvex Quantum Holdings"})
    assert len(e.hit) >= 1 and e.hit[0].file == "capo/system"
    assert all("Zorvex" not in h.token for h in e.hit)     # il nome non si scrive nell'output


def test_payload_il_nome_INTERO_e_un_ago_a_se_quando_nessuna_parola_e_distintiva():
    """ISOLA l'ago dei nomi da quello delle parole. Il test qui sopra non lo isolava: le
    parole distintive del nome lo trovavano lo stesso, e spegnere la ricerca del nome
    INTERO non faceva cadere niente — mutazione SOPRAVVISSUTA al banco del 04/09.
    «Alfa Banca SpA» non ha nessuna parola sopra MIN_PAROLA fuori dalle generiche: solo
    la ricerca del nome intero puo' trovarlo. E' il caso vero dei nomi corti e comuni."""
    nome = "Alfa Banca SpA"
    assert vp._parole_distintive({nome}) == {}
    e = vp.controllo_payload({"capo/system": "il caso di Alfa Banca SpA insegna"},
                             {"WXYZ"}, {nome})
    assert [(h.file, h.riga) for h in e.hit] == [("capo/system", 1)]


def test_payload_una_parola_generica_del_nome_non_e_un_riscontro():
    e = vp.controllo_payload({"c": "tutte le holdings europee"}, {"WXYZ"},
                             {"Zorvex Quantum Holdings"})
    assert e.hit == []


def test_payload_un_nome_di_funzione_non_e_una_menzione_del_book():
    """04/09: 7 riscontri su 136 erano `get_hyperliquid_intel` e simili — il confine del
    controllo e' `(?<![A-Za-z0-9])` e l'underscore NON e' alfanumerico, quindi un nome di
    tool contava come fuga. Ora e' separato: fuori dal totale, DENTRO la nota."""
    e = vp.controllo_payload({"d": "chiama get_wxyz_intel per il tema\n"}, {"WXYZ"})
    assert e.hit == []
    assert "DENTRO identificatori" in e.note and "1 riscontri" in e.note


def test_payload_la_stessa_parola_in_prosa_resta_un_riscontro():
    """La separazione vale per riga: se sulla stessa riga il token compare ANCHE fuori da
    un identificatore, il riscontro resta. Altrimenti basterebbe un nome di funzione
    accanto per far sparire una fuga vera."""
    e = vp.controllo_payload({"d": "usa get_wxyz_intel su WXYZ del book\n"}, {"WXYZ"})
    assert [(h.file, h.riga) for h in e.hit] == [("d", 1)]
    assert "DENTRO identificatori" not in e.note


def test_payload_i_separati_sono_dichiarati_col_conto_e_col_token_mascherato():
    """Non e' un'esclusione zitta (regola 14/07): il verdetto porta quanti sono e quali."""
    e = vp.controllo_payload({"d": "get_wxyz_intel\n", "e": "vedi get_wxyz_intel qui\n"}, {"WXYZ"})
    assert e.hit == []
    assert "2 riscontri DENTRO identificatori" in e.note and "WX…(4)" in e.note


def test_payload_i_tre_system_vivi_sono_nel_corpus():
    """Un `system=` fuori dal corpus e' uno zero NON MISURATO: un edit li' e' invisibile
    al cancello. I tre trovati il 04/09 valgono zero oggi, e devono restare misurati."""
    canali = {c for (c, _, _) in vp.CANALI_PAYLOAD}
    for atteso in ("estrazione/system", "red_team/system", "reflection/system"):
        assert atteso in canali, (atteso, sorted(canali))


def test_payload_il_prompt_del_sentiment_delle_news_e_nel_corpus():
    """05/09 (a1 l'ha trovato, a2 lo misura): il prompt con cui Haiku classifica OGNI notizia
    (`news_aggregator.NEWS_SENTIMENT_PROMPT`, prima una f-string dentro una funzione) e' un
    canale del payload che il corpus non rendeva: uno zero NON misurato. Da oggi e' misurato."""
    assert ("news/sentiment", "bellomberg.market_data.news_aggregator",
            "NEWS_SENTIMENT_PROMPT") in vp.CANALI_PAYLOAD
    from bellomberg.market_data import news_aggregator
    assert isinstance(news_aggregator.NEWS_SENTIMENT_PROMPT, str) and news_aggregator.NEWS_SENTIMENT_PROMPT.strip()


def test_payload_una_parola_di_settore_non_identifica_una_societa():
    """`healthcare` e' parola distintiva del nome di un preferito: senza questa riga, ogni
    frase che parla del SETTORE contava come fuga (4 riscontri su 136 il 04/09)."""
    parole = vp._parole_distintive({"Zorvex Healthcare Inc"})
    assert "Healthcare" not in parole and "healthcare" not in parole
    assert "Zorvex" in parole          # la parola VERAMENTE distintiva resta un ago


def test_payload_statico_rende_i_desk_dagli_attributi_di_CLASSE(monkeypatch):
    """Non si istanzia niente: i costruttori degli specialisti hanno effetti collaterali.
    E un modulo che non si importa diventa un GUASTO dichiarato, non un canale saltato."""
    class _Finto:
        name = "finto"
        system_prompt = "prompt del desk finto"

    def _imp(nome):
        if nome == "modulo_che_non_esiste":
            raise ImportError("no")
        return types.SimpleNamespace(COSTANTE="testo del canale")

    testi, guasti = vp.payload_statico(
        canali=(("c/uno", "modulo_qualsiasi", "COSTANTE"),
                ("c/due", "modulo_che_non_esiste", "COSTANTE")),
        importa=_imp, specialisti=[_Finto])
    assert testi == {"c/uno": "testo del canale", "desk/finto": "prompt del desk finto"}
    assert len(guasti) == 1 and guasti[0][0] == "c/due"


def test_payload_un_registro_di_schemi_diventa_il_suo_JSON():
    """Gli schemi arrivano al modello serializzati nel campo `tools=`: il corpus deve
    essere il JSON, non il repr di una lista di dict."""
    testi, guasti = vp.payload_statico(
        canali=(("tool/x", "m", "SCHEMI"),),
        importa=lambda n: types.SimpleNamespace(SCHEMI=[{"description": "usa WXYZ"}]),
        specialisti=[])
    assert guasti == [] and '"usa WXYZ"' in testi["tool/x"]
    assert vp.controllo_payload(testi, {"WXYZ"}).hit != []


def _tree_payload_pulito(root, valore, con_capo=True):
    root.mkdir()
    pkg = root / "src" / "bellomberg"
    agents = pkg / "agents"
    specialists = agents / "specialists"
    specialists.mkdir(parents=True)
    for directory in (pkg, agents):
        (directory / "__init__.py").write_text("", encoding="utf-8")
    if con_capo:
        (agents / "capo.py").write_text("PROMPT = %r\n" % valore, encoding="utf-8")
    (specialists / "__init__.py").write_text(
        "class Desk:\n"
        "    name = 'finto'\n"
        "    system_prompt = %r\n"
        "ALL_SPECIALISTS = [Desk]\n" % ("desk-" + valore),
        encoding="utf-8")


def test_payload_da_due_tree_non_si_contamina_nello_stesso_orchestratore(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    _tree_payload_pulito(a, "PROMPT-A")
    _tree_payload_pulito(b, "PROMPT-B")
    monkeypatch.setattr(vp, "CANALI_PAYLOAD", (
        ("capo/system", "bellomberg.agents.capo", "PROMPT"),))

    testi_a, guasti_a = vp.payload_statico_da_tree(str(a))
    testi_b, guasti_b = vp.payload_statico_da_tree(str(b))

    assert guasti_a == guasti_b == []
    assert testi_a["capo/system"] == "PROMPT-A"
    assert testi_b["capo/system"] == "PROMPT-B"
    assert testi_a["desk/finto"] == "desk-PROMPT-A"
    assert testi_b["desk/finto"] == "desk-PROMPT-B"


def test_payload_da_tree_preserva_gli_altri_canali_se_un_import_fallisce(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    _tree_payload_pulito(tree, "SANO", con_capo=False)
    monkeypatch.setattr(vp, "CANALI_PAYLOAD", (
        ("capo/system", "bellomberg.agents.capo", "PROMPT"),))

    testi, guasti = vp.payload_statico_da_tree(str(tree))

    assert testi["desk/finto"] == "desk-SANO"
    assert guasti and guasti[0][0] == "capo/system"


def test_payload_da_tree_dichiara_il_timeout(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()

    def scade(*args, **kwargs):
        raise vp.subprocess.TimeoutExpired(args[0], 60)

    monkeypatch.setattr(vp.subprocess, "run", scade)
    testi, guasti = vp.payload_statico_da_tree(str(tree))
    assert testi == {}
    assert guasti == [("payload/processo", "processo pulito: timeout dopo 60 s")]


def test_input_privato_e_congelato_e_il_manifest_non_espone_valori_o_path(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.py").write_text("X = 1\n", encoding="utf-8")
    liste = tmp_path / "liste"
    liste.mkdir()
    corpus = tmp_path / "corpus-riservato"
    corpus.mkdir()
    segreto_a = "segreto-finto-AAAAAAAA"
    segreto_b = "segreto-finto-BBBBBBBB"
    (corpus / ".env").write_text("CHIAVE=%s\n" % segreto_a, encoding="utf-8")

    primo = vp.congela_input(str(tree), ["env"], str(liste), str(corpus))
    manifest = primo.manifest()
    serializzato = json.dumps(manifest, sort_keys=True)
    (corpus / ".env").write_text("CHIAVE=%s\n" % segreto_b, encoding="utf-8")

    assert primo.leggi("env_coppie") == [("CHIAVE", segreto_a)]
    assert segreto_a not in serializzato and str(corpus) not in serializzato
    assert manifest["sha256_payload"] is None
    secondo = vp.congela_input(str(tree), ["env"], str(liste), str(corpus))
    assert secondo.sha256_corpus_privato != primo.sha256_corpus_privato


def test_input_privato_usa_le_sorgenti_riordinate(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()
    liste = tmp_path / "liste"
    liste.mkdir()
    corpus = tmp_path / "corpus"
    tuple_path = (corpus / "archive" / "private" / "attic" / "oneshot" /
                  "import_user_trades.py")
    lista_path = corpus / "tests" / "system" / "prova_numeri_del_book.py"
    db_path = corpus / "data" / "consigliere.db"
    for path in (tuple_path, lista_path, db_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    visti = {}
    monkeypatch.setattr(vp, "lotti_db", lambda path: set())

    def lotti_finti(path):
        visti["tuple"] = os.path.normpath(path)
        return set()

    def lista_finta(path):
        visti["lista"] = os.path.normpath(path)
        return {"finto"}

    monkeypatch.setattr(vp, "lotti_da_tuple", lotti_finti)
    monkeypatch.setattr(vp, "lista_privata", lista_finta)

    congelati = vp.congela_input(
        str(tree), ["lotti", "lista_privata"], str(liste), str(corpus))

    assert visti == {
        "tuple": os.path.normpath(str(tuple_path)),
        "lista": os.path.normpath(str(lista_path)),
    }
    assert "lotti_tuple" in congelati.valori
    assert "lista_privata" in congelati.valori


def test_lista_e_hash_restano_quelli_congelati_se_il_file_cambia(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.py").write_text("TOKEN_VECCHIO\n", encoding="utf-8")
    liste = tmp_path / "liste"
    liste.mkdir()
    vietate = liste / "VIETATE.txt"
    originale = b"TOKEN_VECCHIO\n"
    vietate.write_bytes(originale)
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    congelati = vp.congela_input(
        str(tree), ["vietate"], str(liste), str(corpus))
    vietate.write_text("TOKEN_NUOVO\n", encoding="utf-8")
    esiti, _ = vp.esegui_controlli(
        str(tree), solo=["vietate"], pubblico=str(liste),
        input_congelati=congelati)

    assert congelati.hash_liste["VIETATE.txt"] == hashlib.sha256(originale).hexdigest()
    assert [h.file for h in esiti[0].hit] == ["a.py"]


def test_esegui_tutti_esegue_davvero_il_decimo_controllo(tmp_path, monkeypatch, capsys):
    """CABLAGGIO, non helper: se il ramo `elif nome == "payload"` sparisce, il controllo
    non gira e nessun altro test se ne accorge — `non_eseguiti` non lo vedrebbe, perche'
    il nome resta in `da_fare` (e' il buco chiuso oggi con l'`else`)."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "m.py").write_text("ok\n", encoding="utf-8")
    db = _db_nomi(tmp_path)
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))
    monkeypatch.setattr(vp, "payload_statico",
                        lambda: ({"desk/finto": "compra WXYZ adesso"}, []))
    rc = vp.esegui_tutti(str(tree), solo=["payload"])
    testo = capsys.readouterr().out
    assert "payload" in testo and "desk/finto" in testo and "WX…(4)" in testo
    assert "OSSERVAZIONE" in testo and rc == 2


def test_il_decimo_controllo_e_registrato_e_nasce_in_osservazione():
    """Pin del cablaggio: se domani sparisce da CONTROLLI, `esegui_tutti` non lo esegue
    piu'. Nasce in OSSERVAZIONE come il nono: un controllo che parte con 160 riscontri
    renderebbe il cancello rosso per sempre e verrebbe spento."""
    assert "payload" in vp.CONTROLLI and "payload" in vp.OSSERVAZIONE


def test_esegui_tutti_un_guasto_nel_preambolo_esce_2_non_1(tmp_path, capsys):
    """1 vuol dire «hit trovati». Un cancello che non e' nemmeno partito (tree inesistente,
    lista illeggibile) deve uscire 2: un cancello parziale non e' mai verde (regola 14/07)."""
    rc = vp.esegui_tutti(str(tmp_path / "non_esiste"))
    out = capsys.readouterr().out
    assert rc == 2 and "KO" in out and "NON ESEGUITO" in out


def test_importare_memory_db_non_lascia_il_env_del_pm_nell_ambiente():
    """`memory_db` carica il `.env` del privato con dotenv: senza salvare e ripristinare
    l'ambiente, quei valori resterebbero nel processo e la suite del tree ESPORTATO (T7) li
    erediterebbe tutti. Qui l'import e' finto: la batteria e' pubblica e gira anche su CI."""
    def finto():
        os.environ["CHIAVE_DI_PROVA_T7"] = "valore-finto"
        return "modulo"
    assert "CHIAVE_DI_PROVA_T7" not in os.environ
    assert vp._importa_memory_db(importa=finto) == "modulo"
    assert "CHIAVE_DI_PROVA_T7" not in os.environ


def test_main_scarica_gitleaks_stampa_il_percorso(monkeypatch, capsys):
    monkeypatch.setattr(vp, "scarica_gitleaks", lambda: "C:\\finto\\pubblico\\bin\\gitleaks.exe")
    assert vp.main(["--scarica-gitleaks"]) == 0
    assert "gitleaks.exe" in capsys.readouterr().out


@pytest.mark.parametrize("guasto", [RuntimeError("sha256 dello zip = 00000000…: NON estratto"),
                                    OSError("[Errno 11001] getaddrinfo failed")])   # rete assente
def test_main_scarica_gitleaks_guasto_esce_2_non_1(monkeypatch, capsys, guasto):
    """2 = controllo non eseguito/guasto; 1 vuol dire «hit trovati» in tutto il cancello."""
    def esplode():
        raise guasto
    monkeypatch.setattr(vp, "scarica_gitleaks", esplode)
    assert vp.main(["--scarica-gitleaks"]) == 2
    assert "KO" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [["--tree", "x"], ["--solo", "vietate"]])
def test_scarica_gitleaks_si_usa_da_solo(extra):
    with pytest.raises(SystemExit) as ex:
        vp.main(["--scarica-gitleaks"] + extra)
    assert ex.value.code == 2


def test_main_tree_su_una_cartella_che_non_esiste_esce_2(tmp_path, capsys):
    """Da T7 `--tree` esegue il cancello intero. Un tree inesistente e' un guasto del preambolo:
    exit 2 (non eseguito), non 1 (che vuol dire «hit trovati»)."""
    assert vp.main(["--tree", str(tmp_path / "non_esiste")]) == 2
    out = capsys.readouterr().out
    assert "KO" in out and "NON ESEGUITO" in out


# ---------------------------------------------------------------------------
# 05/09 (voce 8, a2): un valore del .env IDENTICO al default committato in `.env.example` e'
# pubblico per costruzione (gli slug dei modelli: 46 riscontri a HEAD, tutti nel template e nei
# test) — non si cerca, e la nota lo dice per nome. Un valore DIVERSO dal template si cerca; un
# tree senza template non esclude nulla (conservativo).
# ---------------------------------------------------------------------------
TEMPLATE_FINTO = "# esempio\nMODELLO_CHAT=z-ai/glm-finto-5\nCHIAVE_A=\nSCONOSCIUTA=abc\n"
COPPIE_MODELLO = [("MODELLO_CHAT", "z-ai/glm-finto-5"), ("CHIAVE_A", "sk-finta-0123456789abcdef")]


def test_env_valore_identico_al_template_pubblicato_non_si_cerca():
    tree = _tree(**PULITO, **{".env.example": TEMPLATE_FINTO,
                              "config.py": 'M = "z-ai/glm-finto-5"\nC = "sk-finta-0123456789abcdef"\n'})
    e = vp.controllo_env(tree, COPPIE_MODELLO)
    assert [(h.file, h.riga, h.token) for h in e.hit] == [("config.py", 2, "CHIAVE_A…(25)")]
    assert "1 valore identico al template pubblicato non cercato: MODELLO_CHAT" in e.note


def test_env_valore_diverso_dal_template_si_cerca():
    tree = _tree(**PULITO, **{".env.example": "MODELLO_CHAT=altro/modello-finto\n",
                              "config.py": 'M = "z-ai/glm-finto-5"\n'})
    e = vp.controllo_env(tree, COPPIE_MODELLO)
    assert [(h.file, h.token) for h in e.hit] == [("config.py", "MODELLO_CHAT…(16)")]
    assert "identico al template" not in e.note


def test_env_senza_template_nel_tree_non_esclude_nulla():
    tree = _tree(**PULITO, **{"config.py": 'M = "z-ai/glm-finto-5"\n'})
    e = vp.controllo_env(tree, COPPIE_MODELLO)
    assert [h.token for h in e.hit] == ["MODELLO_CHAT…(16)"]


def test_payload_una_eccezione_per_canale_e_token_toglie_l_hit_e_la_dichiara():
    """Le deroghe del PM sul payload (05/09: «Hyperliquid» e' il nome del MERCATO che un tool
    interroga, non una posizione) vivono in ECCEZIONI.txt con il CANALE al posto del file: la
    stessa chiave (canale, "payload", token) degli altri controlli. La chiave e' ESATTA, maiuscole
    comprese, anche se la ricerca non lo e': la riga in minuscolo non copre nulla, viene
    dichiarata e l'hit resta."""
    testi = {"tool/finto": "dati del mercato Zorvex: funding e OI\n", "capo/finto": "niente qui\n"}
    nomi = {"Zorvex Quantum Holdings"}
    senza = vp.controllo_payload(testi, {"WXYZ"}, nomi)
    assert [h.file for h in senza.hit] == ["tool/finto"]
    con = vp.controllo_payload(testi, {"WXYZ"}, nomi, eccezioni={("tool/finto", "payload", "Zorvex")})
    assert con.hit == [] and "1 eccezione applicata" in con.note
    minuscola = vp.controllo_payload(testi, {"WXYZ"}, nomi, eccezioni={("tool/finto", "payload", "zorvex")})
    assert [h.file for h in minuscola.hit] == ["tool/finto"]
    assert "1 eccezione senza riscontro" in minuscola.note


def test_payload_la_nota_conta_i_canali_colpiti_prima_delle_eccezioni_e_lo_dice():
    """Con due deroghe applicate la nota diceva ancora «10 canali colpiti» (misurato 05/09 sul
    corpus vero: hit 89 → 87, canali colpiti invariati): il conto e' fatto PRIMA delle eccezioni,
    come in ticker_soli, e come li' lo deve DIRE — altrimenti la nota afferma piu' di quanto
    l'esito mostra."""
    testi = {"tool/finto": "mercato Zorvex\n", "capo/finto": "niente\n"}
    e = vp.controllo_payload(testi, {"WXYZ"}, {"Zorvex Quantum Holdings"},
                             eccezioni={("tool/finto", "payload", "Zorvex")})
    assert e.hit == []
    assert "1 canali colpiti (prima delle eccezioni)" in e.note


def test_esegui_controlli_legge_le_liste_dalla_cartella_indicata(tmp_path):
    """Voce 3 (05/09): l'hook passa le liste prese dalla FONTE che misura (indice o HEAD); il
    default resta `PUBBLICO`. Qui VIETATE viene dalla cartella passata, non dal disco di casa."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.py").write_text("QUALCOSA = 'Zorvex'\n", encoding="utf-8")
    liste = tmp_path / "liste"
    liste.mkdir()
    (liste / "VIETATE.txt").write_text("Zorvex\n", encoding="utf-8")
    (liste / "ESCLUSI.txt").write_text("nulla/\n", encoding="utf-8")
    (liste / "ECCEZIONI.txt").write_text("# vuota\n", encoding="utf-8")
    esiti, _ = vp.esegui_controlli(str(tree), solo=["vietate"], pubblico=str(liste))
    assert [e.nome for e in esiti] == ["vietate"] and [h.file for h in esiti[0].hit] == ["a.py"]


def test_leggi_simboli_pretende_il_motivo_e_torna_le_basi_maiuscole(tmp_path):
    """Settimo buco D5 (8c, 05/09): il simbolo NUDO di una posizione che nel DB sta in un'altra
    forma non e' derivabile ne' dal ticker ne' dal nome (sotto MIN_PAROLA). Si DICHIARA in
    `policy/SIMBOLI.txt` (base<TAB>motivo): senza motivo non e' una dichiarazione, e' un buco;
    file assente = nessun simbolo dichiarato."""
    p = tmp_path / "SIMBOLI.txt"
    p.write_text("# commento\n\nqrst\tposizione in altra forma nel DB\n", encoding="utf-8")
    assert vp.leggi_simboli(str(p)) == {"QRST"}
    p.write_text("QRST\n", encoding="utf-8")
    with pytest.raises(ValueError):
        vp.leggi_simboli(str(p))
    assert vp.leggi_simboli(str(tmp_path / "assente.txt")) == set()


def test_il_nono_controllo_cerca_anche_i_simboli_dichiarati_e_lo_dice(tmp_path, monkeypatch):
    """CABLAGGIO: il dispatcher unisce le basi del DB e i simboli dichiarati, e la nota dice quanti
    sono dichiarati — altrimenti «N ticker cercati» attribuirebbe al DB cio' che viene da una lista."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "m.py").write_text('FOCUS = "ZZQQ"\n', encoding="utf-8")
    liste = tmp_path / "liste"
    liste.mkdir()
    (liste / "SIMBOLI.txt").write_text("ZZQQ\tdichiarato nel test\n", encoding="utf-8")
    db = _db_ticker(tmp_path)
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))
    esiti, _ = vp.esegui_controlli(str(tree), solo=["ticker_soli"], pubblico=str(liste))
    e = esiti[0]
    assert e.nome == "ticker_soli" and not e.errore, e.errore
    assert [(h.file, h.token) for h in e.hit] == [("m.py", "ZZ…(4)")]
    assert "1 dichiarato in SIMBOLI.txt" in e.note, e.note


def test_il_decimo_controllo_cerca_anche_i_simboli_dichiarati_e_lo_dice(tmp_path, monkeypatch):
    """Stesso cablaggio per il payload: il token nudo a 4 lettere stava in chiaro nei registri dei
    tool e nessun controllo lo contava (8c, 05/09). Dichiarato, si cerca anche in minuscolo (4+)."""
    liste = tmp_path / "liste"
    liste.mkdir()
    (liste / "SIMBOLI.txt").write_text("ZZQQ\tdichiarato nel test\n", encoding="utf-8")
    db = _db_ticker(tmp_path)
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))
    monkeypatch.setattr(vp, "nomi_db", lambda path: set())
    monkeypatch.setattr(vp, "payload_statico", lambda **kw: ({"desk/finto": "focus su zzqq oggi\n"}, []))
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "m.py").write_text("ok\n", encoding="utf-8")
    esiti, _ = vp.esegui_controlli(str(tree), solo=["payload"], pubblico=str(liste))
    e = esiti[0]
    assert e.nome == "payload" and not e.errore, e.errore
    assert [(h.file, h.token) for h in e.hit] == [("desk/finto", "ZZ…(4)")]
    assert "1 dichiarato in SIMBOLI.txt" in e.note, e.note


# --- voce 4b (05/09, a2 = d4): i buchi D5 sui controlli BLOCCANTI ----------------------------------


def test_forme_percentuale_non_passano_dal_filtro_di_lunghezza():
    """Buco D5: `MIN_TOKEN=6` scartava «27,5%» (5 caratteri): una percentuale del book era
    invisibile. Il segno % e' la specificita' che la lunghezza non da'."""
    assert {"27,5%", "27.5%", "27,50%", "27.50%", "27,5 %"} <= vp.forme_percentuale(27.5)
    assert {"27,00%", "27.00%"} <= vp.forme_percentuale(27.0)
    assert len("27,5%") < vp.MIN_TOKEN                          # e' proprio cio' che il filtro perdeva
    # specificita' minima: 4 caratteri prima del segno. «27%» e «5%» NON si cercano (misurato 05/09:
    # «5%» colpiva CSS e fixture ovunque): un'intera in percentuale senza decimali e' un buco DICHIARATO
    assert not {"27%", "5%", "5.0%"} & vp.forme_percentuale(27.0)      # «27,0%» (4 prima del segno) resta
    assert vp.forme_percentuale(5.0) == {"5,00%", "5.00%", "5,00 %", "5.00 %"}   # di un'intera resta solo la forma a 2 decimali
    assert vp.forme_percentuale(-27.5) == vp.forme_percentuale(27.5)


def test_percentuali_db_prende_solo_le_guidance_in_percentuale(tmp_path):
    """Le guidance con unita' `pct...` (margini, crescite) sono percentuali del book; quelle in
    valuta no (le copre valori_db con le sue forme)."""
    p = vp.percentuali_db(_db_finto(tmp_path))
    assert p == {26.5: "company_guidance.value_low", 27.5: "company_guidance.value_mid",
                 28.5: "company_guidance.value_high"}


def test_valori_db_cerca_le_percentuali_dichiarate_con_l_origine(tmp_path):
    """CABLAGGIO: `controllo_valori_db(..., percentuali=)` trova «27,5%» e la maschera con
    l'origine; senza il parametro (o senza il segno %) la riga non e' un hit di questo controllo."""
    tree = _tree(**PULITO, **{"nota.md": "margine EBITDA atteso 27,5% nel FY\n", "altro.md": "27,5 gradi\n"})
    perc = {27.5: "company_guidance.value_mid"}
    e = vp.controllo_valori_db(tree, {123456.0: "x.y"}, percentuali=perc)
    assert [(h.file, h.token) for h in e.hit] == [("nota.md", "company_guidance.value_mid…(5)")]
    assert "1 percentuale" in e.note
    assert vp.controllo_valori_db(tree, {123456.0: "x.y"}).hit == []


def test_colonne_estese_e_i_due_controlli_nuovi_nascono_in_osservazione():
    """Come ticker_soli e payload: misurati il 05/09 su HEAD davano riscontri bloccanti in file
    di altre chat (fixture, app, script) — un controllo che nasce rosso fermerebbe ogni push.
    Nascono con un nome a parte e in OSSERVAZIONE; a zero entrano nei controlli bloccanti."""
    for coppia in (("company_guidance", "value_low"), ("company_guidance", "value_mid"),
                   ("company_guidance", "value_high"), ("position_prices", "prezzo")):
        assert coppia in vp.COLONNE_ESTESE and coppia not in vp.COLONNE_VIVE, coppia
    for nome in ("valori_estesi", "vietate_forme"):
        assert nome in vp.CONTROLLI and nome in vp.OSSERVAZIONE, nome


def test_candidati_numerici_coprono_ogni_token_che_i_confini_accettano():
    """Pre-filtro per `position_prices` (18.291 prezzi distinti nel DB vero: ~80.000 token, la
    ricerca uno per uno costerebbe minuti): dal tree si estraggono una volta i numeri e ogni
    loro pezzo che comincia dopo un separatore e finisce prima di uno. E' un SOVRAINSIEME di cio'
    che `_regex_numero` puo' accettare: un token fuori dai candidati non puo' essere un hit."""
    c = vp._candidati_numerici({"a": "tot 1.234,56; 27,5% e 98765.43, fine 7 e 27.500,00\n"})
    assert {"1.234,56", "1.234", "234,56", "27,5", "98765.43", "7", "27.500", "27.500,00", "500,00"} <= c
    assert "1234" not in c and "" not in c


def test_valori_db_col_prefiltro_trova_ancora_l_intero_dentro_la_forma_con_i_decimali():
    """«27.500,00» E' 27.500 per `_regex_numero`: il pre-filtro non deve perderlo."""
    tree = _tree(**PULITO, **{"a.md": "saldo 27.500,00 EUR\n"})
    e = vp.controllo_valori_db(tree, {27500.0: "cash_movements.amount_eur"})
    assert [h.file for h in e.hit] == ["a.md"]


def test_vietate_trova_anche_il_percorso_escapato_e_con_le_barre_in_avanti():
    """Buco D5: `C:\\finto\\Repo` in un JSON o in una stringa Python e' `C:\\\\finto\\\\Repo`, e in
    uno script portabile `C:/finto/Repo`: la ricerca letterale li perdeva. Il token resta la
    stringa di VIETATE.txt, cosi' una riga di ECCEZIONI.txt la copre in tutte le forme."""
    tree = _tree(**PULITO, **{"cfg.json": '{"p": "C:\\\\finto\\\\Repo"}\n', "run.sh": "cd C:/finto/Repo\n",
                              "x.py": "P = r\"C:\\finto\\Repo\"\n",
                              "doppio.md": "vedi C:\\finto\\Repo oppure C:/finto/Repo\n"})
    e = vp.controllo_vietate_forme(tree, ["C:\\finto\\Repo"])
    # x.py lo conta gia' il controllo 2; doppio.md pure (la riga porta anche la forma letterale):
    # qui entrano SOLO le righe che la ricerca letterale non da', altrimenti i due conti si sommano
    assert sorted(h.file for h in e.hit) == ["cfg.json", "run.sh"]
    assert {h.token for h in e.hit} == {vp.maschera("C:\\finto\\Repo")}
    assert sorted(h.file for h in vp.controllo_vietate(tree, ["C:\\finto\\Repo"]).hit) == ["doppio.md", "x.py"]
    ecc = {("cfg.json", "vietate_forme", "C:\\finto\\Repo"), ("run.sh", "vietate_forme", "C:\\finto\\Repo")}
    assert vp.controllo_vietate_forme(tree, ["C:\\finto\\Repo"], eccezioni=ecc).hit == []


def test_ticker_soli_cerca_il_minuscolo_dalle_quattro_lettere_in_su():
    """Buco D5: il nono cercava la base solo MAIUSCOLA; il decimo gia' no. Stesso confine del
    decimo: dalle 4 lettere anche minuscolo (71 righe in 13 file, misura di a1 del 04/09), sotto
    le 4 no (il rumore sommergerebbe il conto: buco dichiarato, invariato)."""
    tree = _tree(**PULITO, **{"a.py": "usa wxyz qui\n", "b.py": "la parola qr minuscola\n", "c.py": "SIGLA QR\n"})
    e = vp.controllo_ticker_soli(tree, {"WXYZ", "QR"})
    assert sorted((h.file, h.token) for h in e.hit) == [("a.py", "WX…(4)"), ("c.py", "…(2)")]


def test_valori_estesi_e_cablato_e_nasce_in_osservazione(tmp_path, monkeypatch):
    """CABLAGGIO: il ramo `valori_estesi` legge COLONNE_ESTESE + percentuali dal DB, produce un
    esito col SUO nome e con osservazione=True (conta, non ferma)."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "m.py").write_text("PREZZO = 3.210,75  # e margine 27,5%\n", encoding="utf-8")
    db = _db_finto(tmp_path)
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))
    esiti, _ = vp.esegui_controlli(str(tree), solo=["valori_estesi"])
    e = esiti[0]
    assert e.nome == "valori_estesi" and not e.errore and e.osservazione, (e.nome, e.errore)
    assert sorted(h.token for h in e.hit) == ["company_guidance.value_mid…(5)", "position_prices.prezzo…(8)"]
    assert "3 percentuali" in e.note
    # e valori_db (bloccante) NON vede quelle colonne: i due controlli non si sommano
    esiti, _ = vp.esegui_controlli(str(tree), solo=["valori_db"])
    assert esiti[0].nome == "valori_db" and esiti[0].hit == [] and not esiti[0].osservazione


def test_valori_estesi_applica_eccezione_solo_a_controllo_file_e_token(tmp_path, monkeypatch):
    """La deroga degli estesi non deve diventare una deroga ai valori bloccanti, ne'
    coprire lo stesso token in un altro file o un altro token nello stesso file."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.py").write_text("PREZZO = 3.210,75  # margine 27,5%\n", encoding="utf-8")
    (tree / "b.py").write_text("PREZZO = 3.210,75\n", encoding="utf-8")
    liste = tmp_path / "liste"
    liste.mkdir()
    (liste / "ECCEZIONI.txt").write_text(
        "a.py\tvalori_estesi\t3.210,75\tfixture sintetica 09/09/2026\n",
        encoding="utf-8")

    db = _db_finto(tmp_path)
    import sqlite3
    with sqlite3.connect(db) as c:
        c.execute("UPDATE cash_movements SET amount_eur=3210.75 WHERE amount_eur=27500")
    monkeypatch.setattr(vp, "_importa_memory_db", lambda: types.SimpleNamespace(SQLITE_PATH=db))

    esiti, _ = vp.esegui_controlli(
        str(tree), solo=["valori_db", "valori_estesi"], pubblico=str(liste))
    per_nome = {e.nome: e for e in esiti}

    assert sorted(h.file for h in per_nome["valori_db"].hit) == ["a.py", "b.py"]
    assert sorted((h.file, h.token) for h in per_nome["valori_estesi"].hit) == [
        ("a.py", "company_guidance.value_mid…(5)"),
        ("b.py", "position_prices.prezzo…(8)"),
    ]
    assert "1 eccezione applicata" in per_nome["valori_estesi"].note
    errore = vp.controllo_valori_db(
        _tree(**PULITO), {}, nome_controllo="valori_estesi")
    assert errore.nome == "valori_estesi" and errore.errore


def test_vietate_forme_e_cablato_e_nasce_in_osservazione(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "cfg.json").write_text('{"p": "C:\\\\finto\\\\Repo"}\n', encoding="utf-8")
    liste = tmp_path / "liste"
    liste.mkdir()
    (liste / "VIETATE.txt").write_text("C:\\finto\\Repo\n", encoding="utf-8")
    esiti, _ = vp.esegui_controlli(str(tree), solo=["vietate", "vietate_forme"], pubblico=str(liste))
    per_nome = {e.nome: e for e in esiti}
    assert per_nome["vietate"].hit == [] and not per_nome["vietate"].osservazione
    assert [h.file for h in per_nome["vietate_forme"].hit] == ["cfg.json"] and per_nome["vietate_forme"].osservazione


# --- 06/09 (a2): uno ZERO non e' un segreto ------------------------------------------------
# Misura su HEAD del 05/09: dei 57 riscontri di `valori_estesi`, **42** venivano da UNA sola
# guidance in percentuale che nel DB vale 0. `forme_percentuale(0.0)` genera «0.00%», che e' il
# FORMATO NUMERICO dei fogli Excel scritto in ogni motore DCF (dcf_buyside_v3 23, dcf_modeler 8,
# dcf_rab 6, dcf_bank 2, briefing_engine 1, 3 in test). Un controllo che grida su un formato e'
# il controllo che qualcuno spegne: e uno zero non lo si puo' comunque dedurre da un token che
# compare 40 volte per ragioni che non c'entrano col book.


def test_uno_zero_non_genera_token_da_cercare():
    """Un valore ZERO non porta informazione: le sue forme non si cercano, in nessuno dei due
    generatori. La cura NON deve spegnere le percentuali vere (guardia nella stessa prova)."""
    assert vp.forme_percentuale(0.0) == set()
    assert vp.forme_percentuale(-0.0) == set()
    assert vp.forme_numero(0.0) == set()
    # guardia: una percentuale VERA resta cercata come prima (se cade questa, la cura ha spento
    # il controllo invece del solo zero)
    assert {"27,5%", "27.5%"} <= vp.forme_percentuale(27.5)
    assert "98.765,43" in vp.forme_numero(98765.43)


def test_il_formato_dei_fogli_non_e_piu_un_riscontro():
    """CABLAGGIO sul caso vero: una guidance a zero e il formato «0.00%» di un motore DCF nello
    stesso tree. Prima della cura era un hit; dopo no — e la percentuale vera accanto lo resta,
    cosi' la prova non passerebbe se il controllo fosse stato spento del tutto."""
    tree = _tree(**PULITO, **{
        "dcf_finto.py": 'F(ws, "B4", fv.get("wacc"), "0.00%")\n',
        "nota.md": "margine EBITDA atteso 27,5% nel FY\n",
    })
    e = vp.controllo_valori_db(tree, {123456.0: "x.y"},
                               percentuali={0.0: "company_guidance.value_high",
                                            27.5: "company_guidance.value_mid"})
    assert [(h.file, h.token) for h in e.hit] == [("nota.md", "company_guidance.value_mid…(5)")]


def test_lo_zero_non_cercato_e_dichiarato_nella_nota():
    """Il buco si DICHIARA: la nota dice quanti valori non sono stati cercati perche' zero.
    Un controllo che smette di cercare qualcosa in silenzio e' peggio di uno che non lo cercava."""
    tree = _tree(**PULITO)
    e = vp.controllo_valori_db(tree, {0.0: "position_prices.prezzo", 123456.0: "x.y"},
                               percentuali={0.0: "company_guidance.value_high",
                                            27.5: "company_guidance.value_mid"})
    assert "2 valori ZERO non cercati" in e.note, e.note
    # i valori restano CONTATI (il DB li ha davvero): cambia solo cosa si cerca
    assert "2 valori," in e.note and "2 percentuali" in e.note, e.note


def test_lo_zero_resta_non_cercato_anche_senza_il_filtro_di_lunghezza(monkeypatch):
    """La garanzia «uno zero non si cerca» non deve dipendere da MIN_TOKEN, che vive per
    un'altra ragione: il 05/09 le percentuali sono nate proprio TOGLIENDO quel filtro, e con
    esso sarebbe rientrato lo zero dalla finestra. Col filtro a 1 lo zero resta fuori.
    (Il banco lo ha provato: senza questa prova la mutazione che toglie la guardia SOPRAVVIVE.)"""
    monkeypatch.setattr(vp, "MIN_TOKEN", 1)
    assert vp.forme_numero(0.0) == set()
    assert vp.forme_numero(5.0) == {"5"}      # guardia: col filtro a 1 le altre forme ESCONO
