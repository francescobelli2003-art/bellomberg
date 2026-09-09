"""B4 (02/09, pubblicazione): `DB_DIR = "data"` era relativo alla cwd — chi avviava il
backend da un'altra cartella creava un DB VUOTO altrove senza saperlo (lezione
CLAUDE.md «0 posizioni = path sbagliato»). Ora e' ancorato a memory_db.py, o a
BELLOMBERG_DATA_DIR. Le misure girano in un SOTTOPROCESSO con cwd diversa: e' l'unico
modo di riprodurre l'input vero (lezione «misure circolari»).
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODICE = 'from bellomberg.storage import memory_db; import os; print(os.path.abspath(memory_db.SQLITE_PATH))'


def _env_pulito(env_extra=None):
    """BELLOMBERG_DATA_DIR VUOTA, non assente: memory_db legge il .env del repo
    (review 02/09, F2) e dotenv non sovrascrive una chiave gia' presente — cosi' il
    test misura il codice, non il .env del PM."""
    env = dict(os.environ)
    env["BELLOMBERG_DATA_DIR"] = ""
    env["PYTHONPATH"] = REPO
    env.update(env_extra or {})
    return env


def _sqlite_path_da(cwd, env_extra=None):
    r = subprocess.run([sys.executable, "-c", CODICE], cwd=str(cwd), env=_env_pulito(env_extra),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    return os.path.normcase(r.stdout.strip())


def test_bellomberg_data_dir_relativa_e_relativa_al_repo_non_alla_cwd(tmp_path):
    """F7 della review: `BELLOMBERG_DATA_DIR=dati_rel` nel .env da una cwd diversa
    finiva sotto la cwd — lo stesso bug che B4 chiude, riaperto dalla variabile."""
    p = _sqlite_path_da(tmp_path, {"BELLOMBERG_DATA_DIR": "dati_rel"})
    assert p == os.path.normcase(os.path.join(REPO, "dati_rel", "consigliere.db"))


def test_agents_live_e_heartbeat_sono_lo_stesso_file():
    """F6 della review: `AGENTS_LIVE_PATH` (bellomberg_api) non era inchiodato. In
    process, contro DB_DIR (non contro Blackboard.HEARTBEAT_PATH: il conftest lo
    ridirige in tmp)."""
    import bellomberg.api.bellomberg_api as api
    from bellomberg.storage import memory_db
    atteso = os.path.normcase(os.path.normpath(os.path.join(memory_db.DB_DIR, "current_run.json")))
    assert os.path.normcase(os.path.normpath(api.AGENTS_LIVE_PATH)) == atteso


def test_nessun_modulo_apre_il_db_con_un_percorso_proprio():
    """F1 della review: 5 apritori del DB (favorites, tesi/guidance/favorites in
    current_facts, cef_lookthrough, guidance_watch) costruivano `<file>/data/consigliere.db`
    per conto loro — con BELLOMBERG_DATA_DIR impostata: DB fantasma nel repo e split-brain
    silenzioso. L'unica fonte del percorso e' memory_db.SQLITE_PATH."""
    ESCLUSI = re.compile(r"^(tests/|attic/|archive/|mockup_|prove_|prova_|diag_|probe_|scripts/|tools/|nodejs/)")
    # sul testo intero: guidance_watch.py spezzava la join su due righe
    proprio = re.compile(r"""abspath\(__file__\)\)\s*,\s*["']data["']\s*,\s*["']consigliere\.db["']""")
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True,
                           text=True, encoding="utf-8").stdout.split()
    colpe = []
    for f in files:
        if ESCLUSI.match(f):
            continue
        testo = open(os.path.join(REPO, f), encoding="utf-8", errors="replace").read()
        for m in proprio.finditer(testo):
            colpe.append(f"{f}:{testo.count(chr(10), 0, m.start()) + 1}")
    assert not colpe, colpe


def test_da_una_cwd_diversa_il_db_resta_nel_repo(tmp_path):
    p = _sqlite_path_da(tmp_path)
    atteso = os.path.normcase(os.path.join(REPO, "data", "consigliere.db"))
    assert p == atteso, f"{p} != {atteso}"


def test_bellomberg_data_dir_sposta_i_dati(tmp_path):
    altrove = tmp_path / "dati_miei"
    p = _sqlite_path_da(tmp_path, {"BELLOMBERG_DATA_DIR": str(altrove)})
    assert p == os.path.normcase(str(altrove / "consigliere.db"))


def test_bellomberg_data_dir_vuota_vale_assente(tmp_path):
    p = _sqlite_path_da(tmp_path, {"BELLOMBERG_DATA_DIR": "   "})
    assert p == os.path.normcase(os.path.join(REPO, "data", "consigliere.db"))


def test_heartbeat_deriva_da_db_dir(tmp_path):
    """Il commento in bellomberg_api diceva «DEVE combaciare con Blackboard.HEARTBEAT_PATH»:
    ora derivano entrambi da DB_DIR. In sottoprocesso: il conftest reindirizza
    HEARTBEAT_PATH in tmp dentro pytest (presidio del 26/08), qui serve il valore VERO."""
    codice = ('import os; from bellomberg.storage import memory_db; from bellomberg.agents.specialists.base import Blackboard; print(os.path.abspath(Blackboard.HEARTBEAT_PATH))')
    altrove = tmp_path / "dati_miei"
    env = _env_pulito({"BELLOMBERG_DATA_DIR": str(altrove)})
    r = subprocess.run([sys.executable, "-c", codice], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert os.path.normcase(r.stdout.strip()) == os.path.normcase(str(altrove / "current_run.json"))


def test_nessun_letterale_data_relativo_nei_moduli():
    """Il guardiano: un letterale 'data/…', 'report/…' o 'research_notes' non ancorato
    non deve rinascere nei moduli di prodotto."""
    moduli = ["src/bellomberg/api/bellomberg_api.py",
              "src/bellomberg/storage/memory_db.py",
              "src/bellomberg/agents/specialists/base.py",
              "src/bellomberg/cli/recover_db.py",
              "src/bellomberg/portfolio/portfolio_factors.py",
              "src/bellomberg/reporting/charts_agent.py",
              "src/bellomberg/agents/consigliere_multi.py",
              "src/bellomberg/market_data/news_aggregator.py"]
    # Un letterale e' «relativo» quando APRE il percorso: in testa a join/makedirs/glob,
    # assegnato nudo, o con la barra dentro. `os.path.join(root, "report")` — secondo
    # argomento di una base ancorata — e' lecito (bellomberg_api.py:1349).
    cattivo = re.compile(
        r"""["']data[/\\]|["']report[/\\]|["']research_notes[/\\]"""
        r"""|(?:os\.path\.join|makedirs|glob)\(\s*["'](?:data|report|research_notes)["']"""
        r"""|=\s*["'](?:data|report|research_notes)["']""")
    colpe = []
    for m in moduli:
        for i, riga in enumerate(open(os.path.join(REPO, m), encoding="utf-8"), 1):
            # F5 della review: l'esenzione per NOME lasciava passare `DB_DIR = "data"`;
            # e' ancorata solo una join/glob che PARTE da una costante ancorata.
            ancorata = re.search(r"(?:join|glob)\(\s*(?:DB_DIR|REPO_DIR|REPORT_DIR|RESEARCH_NOTES_DIR)\b", riga)
            commento = riga.lstrip().startswith("#") or '"""' in riga or "'''" in riga
            if cattivo.search(riga) and not ancorata and not commento:
                colpe.append(f"{m}:{i}: {riga.strip()[:90]}")
    assert not colpe, "\n".join(colpe)
