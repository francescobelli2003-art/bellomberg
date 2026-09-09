"""Voce 2 di MASTER_TODO §9-quattuortrigies (riparata 01/08/2026, Fable 5).

Il commento a specialists/base.py:469 prometteva: "MAI promuovere a finale un
placeholder d'errore — verrebbe iniettato per settimane nella memoria del desk".
Ma la guardia `not _placeholder` copriva SOLO il ramo di promozione R1→finale:
`_final` scattava con `round_n == 2` da solo, e un errore VERO in round 2
passava dritto. Misurato in DB il 01/08: memo 48 con `quant` e `options`
round 2 = `[ERROR ... 400 ...]`, dentro la finestra LIMIT 3 che
memory_db.get_recent_specialist_reports legge come memoria del desk
(+7 placeholder storici su altri desk).

La semantica giusta e' quella gia' scritta nel commento: NESSUNA riga su DB
-> il dominio risulta SCOPERTO e la regola dei domini scoperti fa il suo
lavoro (onesto). Il red team resta fuori perimetro: la sua persistenza
incondizionata e' una decisione P1 deliberata (regenerate_memo la richiede).
"""
import pytest

from bellomberg.agents.specialists.base import Blackboard


class _MemoriaFinta:
    """Registra le save_specialist_report senza toccare il DB vero."""

    def __init__(self):
        self.salvati = []

    def save_specialist_report(self, memo_id, specialist, round_n, report):
        self.salvati.append((specialist, round_n, report[:60]))


@pytest.fixture
def bb(tmp_path, monkeypatch):
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH",
                        str(tmp_path / "current_run.json"))
    board = Blackboard(memory_db=_MemoriaFinta(), memo_id=999)
    return board


def test_errore_in_round_2_non_diventa_finale(bb):
    """Il caso del 28/07: la chiamata R2 muore (credito esaurito) e il
    placeholder [ERROR...] veniva scritto su DB come report finale."""
    bb.write("quant", 2, "[ERROR quant round 2]: Error code: 400 - credit balance too low")
    assert bb.memory_db.salvati == [], bb.memory_db.salvati


def test_no_output_in_round_2_non_diventa_finale(bb):
    """Seconda forma di placeholder riconosciuta da base.py."""
    bb.write("options", 2, "No output produced in round 2 (vedi log)")
    assert bb.memory_db.salvati == [], bb.memory_db.salvati


def test_report_vero_in_round_2_resta_finale(bb):
    """Controprova: la persistenza dei report VERI non deve cambiare."""
    bb.write("quant", 2, "# QUANT RISK DESK — ROUND 2\n\nreport vero")
    assert bb.memory_db.salvati == [("quant", 2, "# QUANT RISK DESK — ROUND 2\n\nreport vero"[:60])]


def test_promozione_r1_placeholder_resta_vietata(bb):
    """La guardia storica sul ramo R1→finale non deve regredire: chi NON
    replica in R2 e ha un placeholder in R1 non va promosso."""
    bb.r2_specialists = {"fundamentals"}
    bb.write("macro", 1, "[ERROR macro round 1]: Error code: 400")
    assert bb.memory_db.salvati == [], bb.memory_db.salvati


def test_red_team_persiste_comunque(bb):
    """Fuori perimetro DICHIARATO: il red team persiste anche su errore
    (decisione P1 storica — regenerate_memo non rigenera senza red team).
    Questo test cristallizza il confine della voce 2."""
    bb.write("_red_team", 1, "[ERROR red team]: Error code: 400")
    assert bb.memory_db.salvati == [("_red_team", 1, "[ERROR red team]: Error code: 400")]
