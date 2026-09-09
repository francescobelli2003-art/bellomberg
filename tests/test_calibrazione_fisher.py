"""Voce 3 di MASTER_TODO §9-quattuortrigies (riparata 01/08/2026, Fable 5).

Il blocco TRACK RECORD del Capo intitolava "PER CONFIDENCE (la calibrazione
promessa: ALTA deve battere MEDIA/BASSA)", elencava ALTA 60,0% contro MEDIA
53,8% e si fermava: sotto quel titolo il Capo leggeva 60 > 53,8 e concludeva
che la promessa era mantenuta. Fisher esatto bilaterale sui numeri veri della
V6 (12/20 contro 28/52): p = 0,7922 — la differenza di +6,2 punti NON e'
distinguibile dal rumore. Il verdetto ora e' scritto nel blocco, in una riga
corta (il contesto del Capo ha un budget misurato: v. capo.py:223).

Il p-value di riferimento 0,7922 viene dal ricalcolo indipendente registrato
in §9-quattuortrigies il 28/07: la funzione deve riprodurlo alla quarta cifra.
"""
from bellomberg.agents import scorekeeper
from bellomberg.agents.scorekeeper import fisher_bilaterale, format_track_record_for_capo


def test_fisher_riproduce_il_ricalcolo_del_registro():
    assert round(fisher_bilaterale(12, 20, 28, 52), 4) == 0.7922


def test_fisher_caso_forte_e_caso_nullo():
    assert fisher_bilaterale(19, 20, 5, 52) < 0.001      # differenza enorme
    assert fisher_bilaterale(10, 20, 26, 52) == 1.0      # proporzioni identiche


def test_fisher_dominio_invalido_da_none_dichiarato():
    assert fisher_bilaterale(None, 20, 28, 52) is None   # hits mancante
    assert fisher_bilaterale(12, 0, 28, 52) is None      # bucket vuoto
    assert fisher_bilaterale(21, 20, 28, 52) is None     # hits > n
    assert fisher_bilaterale(True, 20, 28, 52) is None   # bool non e' un conteggio


def _sc(alta=None, media=None):
    by_conf = {}
    if alta:
        by_conf["ALTA"] = alta
    if media:
        by_conf["MEDIA"] = media
    return {
        "overall": {"n": 72, "hits": 40, "hit_rate_pct": 55.6, "avg_edge_pct": 0.5},
        "by_action": {},
        "by_confidence": by_conf,
        "by_confidence_scartate": None,
        "by_specialist": {},
    }


def test_blocco_capo_dichiara_il_rumore():
    """I numeri VERI della V6: la riga deve dire che +6,2 pt e' rumore."""
    out = format_track_record_for_capo(_sc(
        alta={"n": 20, "hits": 12, "hit_rate_pct": 60.0, "avg_edge_pct": 1.2},
        media={"n": 52, "hits": 28, "hit_rate_pct": 53.8, "avg_edge_pct": 0.4},
    ))
    assert "Fisher p=0.79" in out, out
    assert "RUMORE" in out, out
    assert "+6.2 pt" in out, out


def test_blocco_capo_dichiara_la_differenza_reale():
    out = format_track_record_for_capo(_sc(
        alta={"n": 20, "hits": 19, "hit_rate_pct": 95.0, "avg_edge_pct": 2.0},
        media={"n": 52, "hits": 5, "hit_rate_pct": 9.6, "avg_edge_pct": -1.0},
    ))
    assert "REALE" in out, out
    assert "RUMORE" not in out, out


def test_blocco_senza_media_non_inventa_un_confronto():
    out = format_track_record_for_capo(_sc(
        alta={"n": 20, "hits": 12, "hit_rate_pct": 60.0, "avg_edge_pct": 1.2},
    ))
    assert "Fisher" not in out, out
