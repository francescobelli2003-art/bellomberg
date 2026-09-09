"""Test OFFLINE scorekeeper (voce P0 audit/21 §4 n.1, fix 26/07).

Copre il fix "scorekeeper a vuoto non dichiarato": causa dei fetch KO
registrata e loggata (niente bare except muto), flag degraded quando ci
sono candidati direzionali ma zero misure, snapshot degradato MAI riusato
dal TTL (nemmeno quello legacy avvelenato del 25/07 senza flag), blocco
TRACK RECORD "n.d. — MISURA FALLITA" dichiarato al posto della stringa
vuota (regola 14/07), guardia reflection su n=0.

Zero rete: yfinance e price_updater stubbati in sys.modules; prezzi a
gradini scelti a mano (100 -> 105 a t+1w -> 110 a t+4w: BUY hit con edge
+10, TRIM miss con edge -10).
"""
import json
import sqlite3
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------
# stub e fabbriche
# ---------------------------------------------------------------

D0 = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")  # 4w osservabile


def _stub_price_updater():
    m = types.ModuleType("price_updater")
    m.data_ticker = lambda t: t
    return m


def _stub_yf_sano():
    """Serie a gradini nota: 100 sotto d0+7gg, 105 sotto d0+28gg, 110 dopo."""
    m = types.ModuleType("yfinance")

    def download(ticker, start=None, progress=False, auto_adjust=True):
        import pandas as pd
        idx = pd.date_range(start=start, end=datetime.now().strftime("%Y-%m-%d"),
                            freq="D")
        vals = []
        for ts in idx:
            days = (ts - pd.Timestamp(D0)).days
            vals.append(100.0 if days < 7 else (105.0 if days < 28 else 110.0))
        return pd.DataFrame({"Close": vals}, index=idx)

    m.download = download
    return m


def _stub_yf_rotto():
    m = types.ModuleType("yfinance")

    def download(*a, **k):
        raise RuntimeError("Yahoo giu (test)")

    m.download = download
    return m


def _stub_yf_muto():
    """Failure-mode reale piu' comune: yfinance NON solleva, ritorna df vuoto."""
    m = types.ModuleType("yfinance")

    def download(*a, **k):
        import pandas as pd
        return pd.DataFrame()

    m.download = download
    return m


def _fake_db(rows, reports=()):
    """DB in-memory con lo schema minimo letto dallo scorekeeper."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, memo_id INT,"
                 " timestamp TEXT, action TEXT, ticker TEXT, eur_amount REAL,"
                 " confidence TEXT, status TEXT)")
    conn.execute("CREATE TABLE specialist_reports (memo_id INT, specialist TEXT,"
                 " content TEXT)")
    for r in rows:
        conn.execute("INSERT INTO decisions (memo_id, timestamp, action, ticker,"
                     " eur_amount, confidence, status) VALUES (?,?,?,?,?,?,?)", r)
    for r in reports:
        conn.execute("INSERT INTO specialist_reports VALUES (?,?,?)", r)
    conn.commit()

    class FakeDB:
        @contextmanager
        def _conn(self):
            yield conn

    return FakeDB()


class _BoomDB:
    """Sentinella: se lo scorekeeper tocca il DB quando non deve, il test esplode."""
    def _conn(self):
        raise AssertionError("il DB NON doveva essere toccato (snapshot riusabile)")


DUE_DECISIONI = [
    (1, D0 + "T10:00:00", "BUY", "ALFA", 1000.0, "ALTA", "PENDING"),
    (1, D0 + "T10:00:00", "TRIM", "BETA", 500.0, "MEDIA", "PENDING"),
]


@pytest.fixture()
def sk(monkeypatch, tmp_path):
    """scorekeeper con price_updater stubbato, stato pulito, SNAP_PATH su tmp."""
    monkeypatch.setitem(sys.modules, 'bellomberg.cli.price_updater', _stub_price_updater())
    import bellomberg.agents.scorekeeper as sk_mod
    sk_mod._MEM_CACHE.clear()
    sk_mod._FETCH_FAIL.clear()
    monkeypatch.setattr(sk_mod, "SNAP_PATH", str(tmp_path / "snap.json"))
    yield sk_mod
    sk_mod._MEM_CACHE.clear()
    sk_mod._FETCH_FAIL.clear()


def _scrivi_snapshot(sk_mod, scorecard, age_h=0.0):
    ts = (datetime.now() - timedelta(hours=age_h)).isoformat(timespec="seconds")
    scorecard = dict(scorecard, computed_at=ts)
    with open(sk_mod.SNAP_PATH, "w", encoding="utf-8") as f:
        json.dump({"computed_at": ts, "scorecard": scorecard}, f)


# ---------------------------------------------------------------
# calcolo: percorso sano e percorso guasto
# ---------------------------------------------------------------

def test_compute_sano_valori_a_mano(sk, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    reports = [(1, "quant", "analisi su ALFA e basta")]
    sc = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI, reports), force=True)
    # BUY su +10% = hit (edge +10); TRIM su +10% = miss (edge -10)
    assert sc["overall"]["n"] == 2
    assert sc["overall"]["hits"] == 1
    assert sc["overall"]["hit_rate_pct"] == 50.0
    per_ticker = {d["ticker"]: d for d in sc["details"]}
    assert per_ticker["ALFA"]["hit"] is True
    assert per_ticker["ALFA"]["edge_pct"] == pytest.approx(10.0)
    assert per_ticker["ALFA"]["horizon_used"] == "4w"
    assert per_ticker["BETA"]["hit"] is False
    assert per_ticker["BETA"]["edge_pct"] == pytest.approx(-10.0)
    assert sc["by_confidence"]["ALTA"]["n"] == 1
    assert sc["by_specialist"]["quant"]["n"] == 1        # firma: ALFA citato
    # campi nuovi: misura sana dichiarata tale
    assert sc["degraded"] is False
    assert sc["n_fetch_fail"] == 0
    assert sc["n_directional_candidates"] == 2
    # snapshot su disco coerente
    with open(sk.SNAP_PATH, encoding="utf-8") as f:
        snap = json.load(f)
    assert snap["scorecard"]["degraded"] is False


def test_fetch_ko_causa_dichiarata_e_degraded(sk, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_rotto())
    sc = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI), force=True)
    assert sc["overall"]["n"] == 0
    assert sc["degraded"] is True
    assert sc["n_directional_candidates"] == 2
    assert sc["n_fetch_fail"] == 2                        # ALFA + BETA
    assert sc["n_unmeasurable"] == 2
    cause = " ".join(sc["fetch_fail_causes"].values())
    assert "RuntimeError" in cause and "Yahoo giu" in cause
    # la cache dei None avvelenati va svuotata: un retry deve RIPROVARE la rete
    assert all(v is not None for v in sk._MEM_CACHE.values())
    # lo snapshot degradato resta su disco come evidenza, marcato
    with open(sk.SNAP_PATH, encoding="utf-8") as f:
        snap = json.load(f)
    assert snap["scorecard"]["degraded"] is True


def test_provider_muto_serie_vuote_dichiarato(sk, monkeypatch):
    # yfinance che ritorna df vuoto SENZA eccezione: degraded lo stesso, causa
    # dichiarata come "serie prezzi vuote" (review 26/07, finding 5)
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_muto())
    sc = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI), force=True)
    assert sc["degraded"] is True
    assert sc["n_fetch_fail"] == 0                        # niente eccezioni
    from bellomberg.agents.scorekeeper import format_track_record_for_capo
    blocco = format_track_record_for_capo(sc)
    assert "MISURA FALLITA" in blocco
    assert "serie prezzi vuote" in blocco


def test_retry_dopo_guasto_riprova_davvero(sk, monkeypatch):
    """Guasto transitorio: primo giro KO, secondo giro (stesso processo) OK."""
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_rotto())
    sc1 = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI), force=True)
    assert sc1["degraded"] is True
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc2 = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI), force=True)
    assert sc2["overall"]["n"] == 2                       # senza purge sarebbe 0
    assert sc2["degraded"] is False


# ---------------------------------------------------------------
# riuso snapshot: degradato mai, sano si'
# ---------------------------------------------------------------

def test_snapshot_degradato_in_cooldown_riusato_dichiarato(sk):
    # entro il cooldown (15') il degradato si RIUSA senza sweep di rete: porta
    # il flag, quindi ogni consumer lo rende come n.d. — dichiarato, non muto
    _scrivi_snapshot(sk, {"overall": {"n": 0}, "n_unmeasurable": 2,
                          "degraded": True}, age_h=0.1)
    sc = sk.compute_scorecard(db=_BoomDB(), force=False)  # DB mai toccato
    assert sc["degraded"] is True


def test_snapshot_degradato_oltre_cooldown_ricalcolato(sk, monkeypatch):
    _scrivi_snapshot(sk, {"overall": {"n": 0}, "n_unmeasurable": 2,
                          "degraded": True}, age_h=0.5)   # > DEGRADED_COOLDOWN_H
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI), force=False)
    assert sc["overall"]["n"] == 2                        # ricalcolato, non 0


def test_snapshot_avvelenato_legacy_non_riusato(sk, monkeypatch):
    # formato PRE-fix (il 25/07 alle 14:30): niente chiave degraded, n=0/170
    _scrivi_snapshot(sk, {"overall": {"n": 0}, "n_unmeasurable": 170}, age_h=0.1)
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI), force=False)
    assert sc["overall"]["n"] == 2


def test_snapshot_sano_fresco_riusato(sk):
    _scrivi_snapshot(sk, {"overall": {"n": 5, "hits": 3}, "n_unmeasurable": 1,
                          "degraded": False}, age_h=0.1)
    sc = sk.compute_scorecard(db=_BoomDB(), force=False)  # DB mai toccato
    assert sc["overall"]["n"] == 5


def test_snapshot_vuoto_genuino_riusato(sk):
    # finestra davvero senza decisioni: n=0 MA zero non-misurabili -> non e'
    # un guasto, il TTL vale (nessun ricalcolo a raffica su un book nuovo)
    _scrivi_snapshot(sk, {"overall": {"n": 0}, "n_unmeasurable": 0,
                          "degraded": False}, age_h=0.1)
    sc = sk.compute_scorecard(db=_BoomDB(), force=False)
    assert sc["overall"]["n"] == 0


# ---------------------------------------------------------------
# resa: blocco dichiarato, mai stringa vuota su misura fallita
# ---------------------------------------------------------------

SC_DEGRADATO = {
    "overall": {"n": 0}, "degraded": True, "n_unmeasurable": 170,
    "n_directional_candidates": 83, "n_fetch_fail": 83,
    "fetch_fail_causes": {"ALFA": "RuntimeError: Yahoo giu (test)"},
}


def test_blocco_capo_nd_dichiarato():
    from bellomberg.agents.scorekeeper import format_track_record_for_capo
    blocco = format_track_record_for_capo(SC_DEGRADATO)
    assert blocco != ""
    assert "MISURA FALLITA" in blocco
    assert "83" in blocco                                 # candidati dichiarati
    assert "RuntimeError" in blocco                       # causa dichiarata
    assert "n.d." in blocco


def test_blocco_capo_vuoto_solo_se_genuino():
    from bellomberg.agents.scorekeeper import format_track_record_for_capo
    # nessuna decisione in finestra e nessun guasto: niente blocco (com'era)
    assert format_track_record_for_capo(
        {"overall": {"n": 0}, "degraded": False, "n_unmeasurable": 0}) == ""
    assert format_track_record_for_capo({}) == ""
    assert format_track_record_for_capo(None) == ""


def test_blocco_capo_sano_intatto():
    from bellomberg.agents.scorekeeper import format_track_record_for_capo
    sc = {"overall": {"n": 2, "hits": 1, "hit_rate_pct": 50.0,
                      "avg_edge_pct": 0.0, "small_sample": True},
          "by_action": {}, "n_unmeasurable": 0, "method_note": "test"}
    blocco = format_track_record_for_capo(sc)
    assert "TRACK RECORD" in blocco and "1/2 hit (50.0%)" in blocco
    assert "MISURA FALLITA" not in blocco
    assert "fetch prezzi KO" not in blocco                # 0 KO: tail pulito
    # degrado PARZIALE (n>0 ma qualche fetch KO): dichiarato nel tail
    blocco2 = format_track_record_for_capo(dict(sc, n_fetch_fail=3))
    assert "di cui 3 per fetch prezzi KO" in blocco2


def test_blocco_specialista_nd_dichiarato():
    from bellomberg.agents.scorekeeper import format_track_record_for_specialist
    blocco = format_track_record_for_specialist(SC_DEGRADATO, "quant")
    assert blocco != ""
    assert "misura fallita" in blocco.lower()
    # su scorecard sano senza firma propria il comportamento resta quello vecchio
    sano = {"overall": {"n": 2, "hits": 1, "hit_rate_pct": 50.0,
                        "avg_edge_pct": 0.0}, "by_specialist": {}}
    assert "misura fallita" not in format_track_record_for_specialist(
        sano, "quant").lower()


# ---------------------------------------------------------------
# reflection: mai una "lezione" da misura guasta
# ---------------------------------------------------------------

def test_reflection_salta_su_misura_guasta(sk, monkeypatch):
    from bellomberg.core import llm_client
    from bellomberg.agents import reflection

    class _MaiChiamare:
        def __init__(self, *a, **k):
            raise AssertionError("chiamata API su misura guasta: VIETATA")

    monkeypatch.setattr(llm_client, "OpenRouterClient", _MaiChiamare)
    monkeypatch.setattr(sk, "compute_scorecard", lambda *a, **k: dict(SC_DEGRADATO))
    usage = {}
    out = reflection.generate_lesson("## ACTION TABLE\n|a|b|\n|1|2|\n",
                                     usage_out=usage)
    assert out == ""
    assert usage["status"] == "skipped"


# ---------------------------------------------------------------
# confidence: i bucket non perdono piu' righe in silenzio
# (voce I-3 passo 2, 26/07 sera-5, Opus 5 — ok PM "correggere ora,
#  prima della V6"). Il bug non era un conteggio: sul DB vero
#  rovesciava la lettura della calibrazione dentro il prompt del
#  Capo (ALTA 4/9=44,4% "peggio" di MEDIA 27/49=55,1%; coi sinonimi
#  al loro posto ALTA 12/20=60,0% MEGLIO di MEDIA 27/50=54,0%).
# ---------------------------------------------------------------

MISTE = [
    (1, D0 + "T10:00:00", "BUY", "ALFA", 1000.0, "ALTA", "PENDING"),
    (1, D0 + "T10:00:00", "BUY", "AAPL", 1000.0, "HIGH", "PENDING"),
    (1, D0 + "T10:00:00", "BUY", "BETA", 1000.0, "high", "PENDING"),
    (1, D0 + "T10:00:00", "BUY", "TSLA", 1000.0, "MEDIUM", "PENDING"),
    (1, D0 + "T10:00:00", "BUY", "AMD", 1000.0, "Media", "PENDING"),
    (1, D0 + "T10:00:00", "TRIM", "INTC", 500.0, "MEDIUM-HIGH", "PENDING"),
    (1, D0 + "T10:00:00", "TRIM", "IBM", 500.0, "-", "PENDING"),
]


def test_conf_bucket_mappa_solo_cio_che_non_va_interpretato():
    import bellomberg.agents.scorekeeper as sk_mod
    assert sk_mod.conf_bucket("HIGH") == "ALTA"
    assert sk_mod.conf_bucket("medium") == "MEDIA"
    assert sk_mod.conf_bucket("Alta") == "ALTA"
    assert sk_mod.conf_bucket("LOW") == "BASSA"
    # queste NON si mappano: forzarle sarebbe interpretare al posto del PM
    for ambigua in ("MEDIUM-HIGH", "LOW-MED", "-", "", None, "boh"):
        assert sk_mod.conf_bucket(ambigua) is None, ambigua


def test_i_sinonimi_inglesi_entrano_nel_bucket(sk, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    # 3 ALTA-equivalenti (ALTA, HIGH, high) e 2 MEDIA-equivalenti (MEDIUM, Media)
    assert sc["by_confidence"]["ALTA"]["n"] == 3, sc["by_confidence"]
    assert sc["by_confidence"]["MEDIA"]["n"] == 2
    # col bug erano 1 e 1: la prova che non e' una tautologia
    assert sc["by_confidence"]["ALTA"]["hits"] == 3   # BUY su +10% = hit


def test_le_intermedie_sono_scartate_ma_DICHIARATE(sk, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    scarti = sc["by_confidence_scartate"]
    assert scarti["n"] == 2
    assert scarti["etichette"] == {"MEDIUM-HIGH": 1, "-": 1}, scarti
    assert scarti["motivo"] and "forzate" in scarti["motivo"]
    assert scarti["sinonimi_applicati"]["HIGH"] == "ALTA"
    # nessuna intermedia e' finita in un bucket per sbaglio
    assert "MEDIUM-HIGH" not in sc["by_confidence"]


def test_invariante_bucket_piu_scarti_uguale_totale(sk, monkeypatch):
    """L'unica asserzione che rende impossibile il ritorno del bug: se un
    domani qualcuno aggiunge un'etichetta nuova e non la mappa, la somma
    non torna e questo test cade."""
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    somma = (sum(v["n"] for v in sc["by_confidence"].values())
             + sc["by_confidence_scartate"]["n"])
    assert somma == sc["overall"]["n"] == 7


def test_letichetta_vera_non_si_perde(sk, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    per_tk = {d["ticker"]: d for d in sc["details"]}
    assert per_tk["INTC"]["confidence"] == "MEDIUM-HIGH"      # raw conservato
    assert per_tk["INTC"]["confidence_bucket"] is None        # e dichiarato fuori
    assert per_tk["AAPL"]["confidence"] == "HIGH"
    assert per_tk["AAPL"]["confidence_bucket"] == "ALTA"


def test_il_capo_legge_che_il_quadro_e_parziale(sk, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    blocco = sk.format_track_record_for_capo(sc)
    assert "PER CONFIDENCE" in blocco
    assert "2 call fuori dai bucket" in blocco, blocco
    assert "MEDIUM-HIGH=1" in blocco
    assert "PARZIALE" in blocco


def test_senza_scarti_il_capo_non_legge_avvisi_inutili(sk, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(DUE_DECISIONI), force=True)
    assert sc["by_confidence_scartate"]["n"] == 0
    blocco = sk.format_track_record_for_capo(sc)
    assert "fuori dai bucket" not in blocco and "PARZIALE" not in blocco, (
        "avviso di quadro parziale su un quadro completo: rumore nel prompt")


def test_snapshot_vecchio_senza_il_campo_non_esplode():
    """Uno snapshot scritto prima del fix non ha `by_confidence_scartate`:
    il blocco del Capo e il payload API devono reggerlo dichiarandolo."""
    import bellomberg.agents.scorekeeper as sk_mod
    vecchio = {"overall": {"n": 2, "hits": 1, "hit_rate_pct": 50.0, "avg_edge_pct": 0.0},
               "by_confidence": {"ALTA": {"n": 2, "hits": 1, "hit_rate_pct": 50.0,
                                          "avg_edge_pct": 0.0}},
               "by_action": {}, "by_specialist": {}, "degraded": False}
    blocco = sk_mod.format_track_record_for_capo(vecchio)
    assert "PER CONFIDENCE" in blocco and "fuori dai bucket" not in blocco
    assert "PRIMA del fix" in sk_mod._caveat_confidence(vecchio)
    # e il buco NON e' "non lo so": su un vecchio snapshot il numero di call
    # fuori dai bucket e' CALCOLABILE (overall.n meno la somma dei bucket)
    parziale = dict(vecchio, by_confidence={"ALTA": {"n": 1, "hits": 1,
                                                     "hit_rate_pct": 100.0,
                                                     "avg_edge_pct": 0.0}})
    assert "1 call su 2" in sk_mod._caveat_confidence(parziale), \
        sk_mod._caveat_confidence(parziale)


def test_il_taglio_del_blocco_e_dichiarato_non_muto(sk, monkeypatch):
    """`[:max_chars]` zitto su un blocco che serve a PESARE LE FIRME e' un
    fallback silenzioso: il modello vedrebbe una frase interrotta senza sapere
    se il dato manca o e' stato tagliato. Misura del 26/07: il blocco reale e'
    1648 char, il tetto era 1700 -> 52 di margine, da qui il tetto a 2000."""
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    intero = sk.format_track_record_for_capo(sc)
    assert "TRONCATO" not in intero, "dichiara un taglio che non c'e'"
    corto = sk.format_track_record_for_capo(sc, max_chars=300)
    assert len(corto) <= 300
    assert "BLOCCO TRACK RECORD TRONCATO" in corto.splitlines()[-1]
    assert "non dedurne assenza" in corto
    # lo stesso vale per il blocco degli specialisti
    sp = sk.format_track_record_for_specialist(sc, "quant", max_chars=120)
    assert len(sp) <= 120 and "TRONCATO" in sp


def test_il_tetto_del_capo_ha_margine_sul_blocco_vero(sk, monkeypatch):
    """Guardia di margine: se il blocco cresce fino a sfiorare il tetto, questo
    test cade PRIMA che il taglio inizi a mangiare la coda (peggiori call +
    nota di metodo) in una run da ~10 EUR."""
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    import inspect
    tetto = inspect.signature(sk.format_track_record_for_capo).parameters["max_chars"].default
    assert tetto >= 2000, "tetto abbassato: rileggi la misura del 26/07 (1648 char reali)"


def test_la_riga_del_quadro_parziale_resta_CORTA(sk, monkeypatch):
    """Budget, non estetica: il contesto del Capo e' a 8365 char su un cap di
    8800 (misurato 26/07 sera-5) e il taglio mangia la CODA — che e' la frase
    con cui il PM gli dice cosa fare. La prima versione di questa riga era 184
    char e sforava. Non e' un test di stile: e' il tetto che protegge la frase
    finale della memoria."""
    monkeypatch.setitem(sys.modules, "yfinance", _stub_yf_sano())
    sc = sk.compute_scorecard(db=_fake_db(MISTE), force=True)
    riga = [l for l in sk.format_track_record_for_capo(sc).splitlines()
            if "ATTENZIONE" in l]
    assert len(riga) == 1, riga
    assert len(riga[0]) <= 160, (
        f"riga da {len(riga[0])} char: sopra i 160 il contesto del Capo sfora e "
        "perde la frase finale (misura del 26/07)")
