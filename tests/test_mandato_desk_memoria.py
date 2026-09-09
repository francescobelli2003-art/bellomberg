import pytest
from types import SimpleNamespace

from bellomberg.storage import memory_db
from bellomberg.core import mandato_pm
from bellomberg.storage.memory_db import MemoryDB


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    from bellomberg.agents import scorekeeper
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist", lambda *_a, **_k: "")
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo", lambda *_a, **_k: "")
    return MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))


def _memo(fp=None, corpo="tesi storica disponibile"):
    marker = "" if fp is None else (
        "[MANDATO PM: impronta %s; origine dichiarato; dichiarato_il 2026-09-09]\n" % fp)
    return "# Memo\n" + marker + "\n" + corpo


def test_report_di_altro_mandato_resta_visibile_ma_viene_etichettato(db, monkeypatch):
    monkeypatch.setattr(memory_db, "_fingerprint_mandato_corrente", lambda: "b" * 64)
    memo_id = db.save_memo(_memo("a" * 64), title="storico")
    db.save_specialist_report(memo_id, "options", 2, "REPORT CHE DEVE RESTARE")

    ctx = db.build_specialist_memory_context("options", max_chars=99999)

    assert "REPORT CHE DEVE RESTARE" in ctx
    assert "impronta diversa dal mandato corrente" in ctx
    assert "non trattare il testo come preferenza PM corrente" in ctx


def test_report_senza_marker_dichiara_provenienza_non_disponibile(db, monkeypatch):
    monkeypatch.setattr(memory_db, "_fingerprint_mandato_corrente", lambda: "b" * 64)
    memo_id = db.save_memo(_memo(None), title="legacy")
    db.save_specialist_report(memo_id, "macro", 2, "REPORT LEGACY VISIBILE")

    ctx = db.build_specialist_memory_context("macro", max_chars=99999)

    assert "REPORT LEGACY VISIBILE" in ctx
    assert "provenienza mandato non disponibile" in ctx


def test_parser_accetta_solo_marker_esatto_a_inizio_riga():
    fp = "c" * 64
    assert memory_db._fingerprint_memo(_memo(fp)) == fp
    assert memory_db._fingerprint_memo("prosa con impronta " + fp) is None
    assert memory_db._fingerprint_memo(
        " [MANDATO PM: impronta %s; origine x; dichiarato_il y]" % fp) is None


def test_marker_storico_resta_dichiarato_se_il_mandato_corrente_manca():
    label = memory_db._etichetta_mandato_storico(_memo("f" * 64), None)
    assert "mandato corrente non dichiarato" in label
    assert "non trattare il testo come preferenza PM corrente" in label


class _ChromaFinto:
    def __init__(self, memo_id):
        self.memo_id = memo_id

    def query(self, **_kwargs):
        return {
            "ids": [["chunk-1"]],
            "metadatas": [[{"memo_id": self.memo_id}]],
            "documents": [["CONTENUTO SEMANTICO"]],
            "distances": [[0.1]],
        }


def test_ricerca_semantica_normalizza_memo_id_stringa(db, monkeypatch):
    fp = "1" * 64
    monkeypatch.setattr(memory_db, "_fingerprint_mandato_corrente", lambda: fp)
    memo_id = db.save_memo(_memo(fp), title="memo")
    db.col_memos = _ChromaFinto(str(memo_id))

    risultati = db.search_memos_semantic("query", n_results=1)

    assert "stessa impronta" in risultati[0]["content"]
    assert "CONTENUTO SEMANTICO" in risultati[0]["content"]


def test_ricerca_semantica_preserva_contenuto_se_lookup_memo_fallisce(db, monkeypatch):
    monkeypatch.setattr(memory_db, "_fingerprint_mandato_corrente", lambda: "2" * 64)
    db.col_memos = _ChromaFinto("7")

    class _ConnRotta:
        def __enter__(self):
            raise RuntimeError("sqlite indisponibile")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(db, "_conn", lambda: _ConnRotta())
    risultati = db.search_memos_semantic("query", n_results=1)

    assert "CONTENUTO SEMANTICO" in risultati[0]["content"]
    assert "provenienza mandato non disponibile" in risultati[0]["content"]
    assert "lookup memo fallito: RuntimeError: sqlite indisponibile" in risultati[0]["content"]


def test_memoria_capo_etichetta_il_memo_storico(db, monkeypatch):
    monkeypatch.setattr(memory_db, "_fingerprint_mandato_corrente", lambda: "d" * 64)
    db.save_memo(_memo("e" * 64, "CORPO CAPO"), title="prima")

    ctx = db.build_capo_memory_context(max_chars=99999)

    assert "CORPO CAPO" in ctx
    assert "impronta diversa dal mandato corrente" in ctx


def test_options_rilegge_mandato_a_b_nel_system_del_client(tmp_path, monkeypatch):
    from bellomberg.agents.specialists import base
    from bellomberg.agents.specialists.options import OptionsSpecialist
    from bellomberg.core import current_facts

    path = str(tmp_path / "mandato.json")
    monkeypatch.setattr(mandato_pm, "PERCORSO_MANDATO", path)
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    for nome in ("current_facts_block", "favorites_block", "pm_theses_block"):
        monkeypatch.setattr(current_facts, nome, lambda: "")

    consegne = []

    class Stop(BaseException):
        pass

    def cattura(**kwargs):
        consegne.append(kwargs)
        raise Stop()

    bb = SimpleNamespace(
        data={}, memory_db=None,
        mark_specialist_start=lambda *_a, **_k: None,
    )
    spec = OptionsSpecialist(
        bb, client=SimpleNamespace(messages=SimpleNamespace(create=cattura)))
    monkeypatch.setattr(spec, "_build_tools_schema", lambda: [{"name": "finto"}])
    monkeypatch.setattr(spec, "_build_round_context", lambda _round: "CONTESTO")
    monkeypatch.setattr(spec, "_model_for_round", lambda _round: "modello-finto")

    a = mandato_pm.profilo_esempio()
    mandato_pm.salva(a, path)
    with pytest.raises(Stop):
        spec.run(0)

    b = mandato_pm.profilo_esempio()
    b["opzioni"].update({
        "opzioni_abilitate": True,
        "strumenti_ammessi": ["short_premium_nudo"],
        "budget_premio_pct": 2,
    })
    mandato_pm.salva(b, path)
    with pytest.raises(Stop):
        spec.run(0)

    assert len(consegne) == 2
    assert "NON usa opzioni" in consegne[0]["system"]
    assert "VENDITA DI PREMIO NUDA" in consegne[1]["system"]
    assert "NON usa opzioni" not in consegne[1]["system"]
    assert all("{MANDATO:" not in c["system"] for c in consegne)


def test_quant_non_conserva_trigger_personali_con_mandati_opposti(tmp_path, monkeypatch):
    from bellomberg.agents.specialists import base
    from bellomberg.agents.specialists.quant import QuantSpecialist
    from bellomberg.core import current_facts

    path = str(tmp_path / "mandato.json")
    monkeypatch.setattr(mandato_pm, "PERCORSO_MANDATO", path)
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    for name in ("current_facts_block", "favorites_block", "pm_theses_block"):
        monkeypatch.setattr(current_facts, name, lambda: "")
    systems = []

    class Stop(BaseException):
        pass

    def capture(**kwargs):
        systems.append(kwargs["system"])
        raise Stop()

    board = SimpleNamespace(data={}, memory_db=None, mark_specialist_start=lambda *_a, **_k: None)
    specialist = QuantSpecialist(board, client=SimpleNamespace(messages=SimpleNamespace(create=capture)))
    monkeypatch.setattr(specialist, "_build_tools_schema", lambda: [{"name": "synthetic"}])
    monkeypatch.setattr(specialist, "_build_round_context", lambda _round: "SYNTHETIC CONTEXT")
    monkeypatch.setattr(specialist, "_model_for_round", lambda _round: "synthetic-model")
    for bilateral, threshold in ((True, 37), (False, 42)):
        mandate = mandato_pm.profilo_esempio()
        mandate["rischio"].update(drawdown_bilaterale=bilateral, drawdown_significativo_pct=threshold)
        mandato_pm.salva(mandate, path)
        with pytest.raises(Stop):
            specialist.run(0)
    assert ">37%" in systems[0]
    assert "non ha dichiarato la regola bilaterale" in systems[1]
    assert ">37%" not in systems[1]
    assert all(">20%" not in text and "regola PM 16/07" not in text for text in systems)


def test_priorita_ricerca_ed_esempi_non_raccontano_il_book_del_creatore():
    from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist
    from bellomberg.agents import capo

    assert "BLOCK storici (" not in FundamentalsSpecialist.system_prompt
    assert 'se la settimana scorsa il PM ha detto "hai tagliato' not in capo.CAPO_SYSTEM_PROMPT
