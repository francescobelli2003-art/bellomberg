"""La suite non deve poter toccare la PRODUZIONE (voce I-3 passo 0, 26/07 sera-5).

Il bug che questi test inchiodano e' stato MISURATO, non dedotto: lanciare
`pytest tests/test_persistence.py` (4 test, dichiarati offline dal conftest)
riscriveva `data/scorekeeper_snapshot.json` di produzione con uno scorecard
DEGRADATO (`overall {"n": 0}`, 32 fetch KO) letto dal DB VERO. Catena:

    test stubba `price_updater` (senza `data_ticker`)
      -> db.build_specialist_memory_context(...)
        -> memory_db.py:1912  from scorekeeper import get_track_record_for_specialist
          -> scorekeeper.compute_scorecard(db=None)
            -> MemoryDB()            <- DB DI PRODUZIONE (74 candidati veri)
            -> open(SNAP_PATH, "w")  <- SNAPSHOT DI PRODUZIONE, degradato

Conseguenza misurata: per 15' dopo ogni giro di suite (cooldown
`scorekeeper.DEGRADED_COOLDOWN_H`) il track record su disco dichiarava
"MISURA FALLITA" su una misura sana. La run vera si salvava per caso
(`consigliere_multi.py:473` ricalcola con `force=True`), l'endpoint di I-3 —
che per specifica NON ricalcola — no.

Zero rete, zero DB vivo: tutto su tmp_path.
"""
import json
import os

import pytest

from bellomberg.storage import memory_db
from bellomberg.agents import scorekeeper
from tests.conftest import DB_PRODUZIONE, ProduzioneToccata


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db_tmp(tmp_path, monkeypatch):
    """MemoryDB vuoto su tmp: zero decisioni -> compute_scorecard non fa rete."""
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", _no_chroma)
    return memory_db.MemoryDB(db_path=str(tmp_path / "data" / "t.db"),
                              chroma_path=str(tmp_path / "chroma"))


# --------------------------------------------------------------------------
# 1. la guardia e' ACCESA su ogni test, senza che il test la chieda
# --------------------------------------------------------------------------

def test_lo_snapshot_e_dirottato_su_tmp():
    vero = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "scorekeeper_snapshot.json")
    assert os.path.normcase(scorekeeper.SNAP_PATH) != os.path.normcase(vero), \
        "SNAP_PATH punta ancora alla produzione: la fixture autouse non ha girato"


def test_aprire_il_db_di_produzione_fallisce_il_test():
    with pytest.raises(ProduzioneToccata):
        memory_db.connect_sqlite(DB_PRODUZIONE)


def test_anche_il_path_relativo_e_la_junction_sono_coperti():
    """`self.db_path` e' "data/consigliere.db" RELATIVO alla cwd (voce aperta
    sui path cwd-relativi): la guardia confronta i realpath, non le stringhe."""
    with pytest.raises(ProduzioneToccata):
        memory_db.connect_sqlite(os.path.join("data", "consigliere.db"))


def test_un_db_temporaneo_passa_indisturbato(tmp_path):
    conn = memory_db.connect_sqlite(str(tmp_path / "libero.db"))
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 2. il pezzo che vale: la guardia BUCA i `except Exception: pass`
# --------------------------------------------------------------------------

def test_la_guardia_non_e_inghiottibile_da_except_exception():
    """Scelta deliberata: BaseException. I due siti che portano alla produzione
    (memory_db.py:1912 e :2229) sono dentro `try/except Exception: pass` — con
    una Exception normale la guardia sarebbe MUTA, cioe' il difetto che chiude."""
    assert not issubclass(ProduzioneToccata, Exception)
    with pytest.raises(ProduzioneToccata):
        try:
            memory_db.connect_sqlite(DB_PRODUZIONE)
        except Exception:            # noqa: BLE001 - e' il punto del test
            pytest.fail("inghiottita: la guardia sarebbe invisibile")


def test_la_catena_che_ha_avvelenato_lo_snapshot_ora_fallisce(db_tmp):
    """Riproduce il colpevole vero (test_persistence.py:108) senza stub dello
    scorekeeper: build_specialist_memory_context deve schiantare, non lavorare
    di nascosto sulla produzione."""
    with pytest.raises(ProduzioneToccata):
        db_tmp.build_specialist_memory_context("quant")


def test_la_catena_del_capo_e_coperta_uguale(db_tmp):
    with pytest.raises(ProduzioneToccata):
        db_tmp.build_capo_memory_context()


# --------------------------------------------------------------------------
# 3. il calcolo vero scrive su tmp e NON tocca il file di produzione
# --------------------------------------------------------------------------

def test_compute_scorecard_scrive_su_tmp_e_lascia_stare_la_produzione(db_tmp):
    vero_snap = DB_PRODUZIONE.replace("consigliere.db", "scorekeeper_snapshot.json")
    if not os.path.exists(vero_snap):
        # in CI il file non c'e': meta' del test non e' misurabile e va DETTO,
        # non saltato in silenzio dentro un `if` (il nome del test promette di
        # verificare la produzione — review 26/07 sera-5)
        pytest.skip("snapshot di produzione assente (CI): la seconda meta' del "
                    "test non e' misurabile qui")
    prima = os.path.getmtime(vero_snap)
    sc = scorekeeper.compute_scorecard(db=db_tmp, force=True)
    assert sc["overall"]["n"] == 0 and sc["n_directional_candidates"] == 0, \
        "il DB tmp non e' vuoto: il test avrebbe fatto rete"
    # scritto dove deve
    with open(scorekeeper.SNAP_PATH, encoding="utf-8") as f:
        assert json.load(f)["scorecard"]["overall"] == {"n": 0}
    assert os.path.getmtime(vero_snap) == prima, \
        "lo snapshot di produzione e' stato riscritto DA UN TEST"


def test_uno_scorecard_vuoto_non_e_degradato(db_tmp):
    """Non-regressione del fix (46): vuoto GENUINO (zero candidati) non deve
    marcarsi degradato, altrimenti la guardia di cui sopra maschererebbe il
    caso vero dietro un falso allarme."""
    sc = scorekeeper.compute_scorecard(db=db_tmp, force=True)
    assert sc["degraded"] is False and sc["n_fetch_fail"] == 0


# --------------------------------------------------------------------------
# 4. i buchi trovati dalla review avversariale del 26/07 sera-5
# --------------------------------------------------------------------------

def test_i_moduli_con_binding_a_import_time_sono_coperti():
    """La prima versione della guardia dichiarava questo limite al FUTURO ("se
    un domani un test importa uno di quelli"): era falso, la suite li importa
    GIA' (test_f12_gex, test_log_pipe_morta, test_news_providers,
    test_verita_numeri_lotto_b/c). Il peggiore e' `bellomberg_api._fav_db()`,
    che ha il path di produzione HARDCODATO e ci fa CREATE TABLE + ALTER TABLE.
    """
    from tests import conftest
    import importlib
    for nome in conftest.MODULI_CON_BINDING_A_IMPORT:
        mod = importlib.import_module(nome)
        assert hasattr(mod, "connect_sqlite"), nome
        with pytest.raises(ProduzioneToccata):
            mod.connect_sqlite(DB_PRODUZIONE)


def test_il_fav_db_hardcoded_non_arriva_alla_produzione():
    """`_fav_db()` non passa da `SQLITE_PATH`: ricostruisce il path da
    `__file__`. E' il caso che il confronto per stringa non prenderebbe."""
    from bellomberg.api import bellomberg_api
    with pytest.raises(ProduzioneToccata):
        bellomberg_api._fav_db()


def test_la_famiglia_wal_e_shm_e_lo_stesso_database():
    from bellomberg.storage import memory_db
    for suffisso in ("-wal", "-shm"):
        with pytest.raises(ProduzioneToccata):
            memory_db.connect_sqlite(DB_PRODUZIONE + suffisso)


def test_un_path_non_risolvibile_BLOCCA_invece_di_passare():
    """Fail-CLOSED: `_stessa_cosa` ritornava False su qualunque path che
    realpath non digerisce (bytes, URI sqlite) — cioe' passava in silenzio,
    che e' il fallback zitto che questa guardia esiste per chiudere."""
    from tests import conftest
    assert conftest._e_il_db_di_produzione(b"\xff\xfe/data/consigliere.db", "") is True


def test_il_last_known_good_del_risk_free_e_dirottato():
    """`tests/test_benchmark_series.py` riscriveva `data/rf_cache.json` di
    PRODUZIONE dopo un fetch VIVO a Bundesbank (trovato con un audit hook)."""
    from bellomberg.market_data import market_inputs
    vero = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "rf_cache.json")
    assert os.path.normcase(market_inputs._LKG_PATH) != os.path.normcase(vero)


def test_il_db_fantasma_di_un_altro_test_resta_lecito(tmp_path):
    """Non-regressione: `tests/test_guidance.py` costruisce APPOSTA un
    `data/consigliere.db` finto sotto tmp per provare la lezione del DB
    fantasma. La rete di sicurezza sul nome non deve romperlo."""
    finto = tmp_path / "cwd_sbagliato" / "data" / "consigliere.db"
    finto.parent.mkdir(parents=True)
    conn = memory_db.connect_sqlite(str(finto))
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()
