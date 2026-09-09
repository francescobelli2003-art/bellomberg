"""V6 guidance Lotto 1 (audit/19, decisioni PM D1-D4 23/07): registro
company_guidance su DB TEMPORANEO — zero rete, date esplicite (mai date.today()
implicita nei casi di staleness). Aspettative scritte a mano nei commenti.
"""
from datetime import date

import pytest

from bellomberg.storage.memory_db import MemoryDB

OGGI = date(2026, 7, 23)


@pytest.fixture()
def db(tmp_path):
    return MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))


def test_migrazione_3_registrata(db):
    with db._conn() as conn:
        tabelle = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "company_guidance" in tabelle
        vers = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
        assert 3 in vers      # la voce MIGRATIONS documenta il cambio


def test_fonte_obbligatoria_d1(db):
    # senza source_doc/source_date la guidance NON entra (D1)
    r = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18, today=OGGI)
    assert "error" in r and "fonte OBBLIGATORIA" in r["error"]
    r2 = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18,
                         source_doc="press release Q2", source_date="",
                         today=OGGI)
    assert "error" in r2
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM company_guidance").fetchone()[0] == 0


def test_bande_plausibilita_e_range(db):
    # 18 passato come 18 (percento) invece di 0.18 (frazione): fuori banda, rifiutato
    r = db.add_guidance("ALFA", "revenue_growth", "FY2026", 18,
                        source_doc="pr", source_date="2026-07-22", today=OGGI)
    assert "error" in r and "banda di plausibilita'" in r["error"]
    # range incoerente (low > mid)
    r2 = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18, value_low=0.20,
                         source_doc="pr", source_date="2026-07-22", today=OGGI)
    assert "error" in r2 and "range incoerente" in r2["error"]
    # metric other senza unit esplicita: rifiutata (dichiarare cos'e')
    r3 = db.add_guidance("ALFA", "other", "FY2026", 5.0,
                         source_doc="pr", source_date="2026-07-22", today=OGGI)
    assert "error" in r3 and "unit" in r3["error"]
    # metric sconosciuta
    r4 = db.add_guidance("ALFA", "boh", "FY2026", 0.18,
                         source_doc="pr", source_date="2026-07-22", today=OGGI)
    assert "error" in r4


def test_add_supersede_e_storico(db):
    r1 = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.17,
                         value_low=0.16, value_high=0.18,
                         source_doc="press release Q1 FY26", source_date="2026-04-29",
                         today=date(2026, 4, 30))
    assert r1.get("ok") and r1["superseded"] == 0
    # guidance ALZATA alla trimestrale dopo: supersede automatico, storico conservato
    r2 = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18,
                         value_low=0.17, value_high=0.19,
                         source_doc="press release Q2 FY26", source_date="2026-07-22",
                         today=OGGI)
    assert r2.get("ok") and r2["superseded"] == 1
    g = db.get_guidance("ALFA", include_history=True, today=OGGI)
    assert len(g["active"]) == 1
    assert g["active"][0]["value_mid"] == pytest.approx(0.18)
    assert g["active"][0]["stale"] is False
    assert len(g["history"]) == 1
    assert g["history"][0]["status"] == "superseded"
    assert g["history"][0]["superseded_by"] == r2["id"]
    # metrica DIVERSA sullo stesso ticker: NON supersede (convivono)
    r3 = db.add_guidance("ALFA", "ebitda_margin", "FY2026", 0.30,
                         source_doc="slide 12 Q2", source_date="2026-07-22", today=OGGI)
    assert r3.get("ok") and r3["superseded"] == 0
    assert len(db.get_guidance("ALFA", today=OGGI)["active"]) == 2


def test_scadenza_d4_fallback_e_staleness(db):
    # senza calendar: fallback effective+120g DICHIARATO
    r = db.add_guidance("NORD", "revenue_growth", "FY2026", 0.10,
                        source_doc="pr", source_date="2026-03-01",
                        today=date(2026, 3, 2))
    assert r.get("ok")
    assert r["valid_until"] == "2026-06-30"          # 02/03 + 120g
    assert "fallback" in r["valid_until_source"]
    # oggi 23/07 > 30/06: STALE dichiarata in lettura, status in DB INVARIATO
    g = db.get_guidance("NORD", today=OGGI)
    assert g["active"][0]["stale"] is True
    assert "STALE" in g["active"][0]["stale_note"]
    assert g["active"][0]["status"] == "active"      # la colonna non si tocca (D4)
    assert "STALE" in g.get("nota", "")
    # con valid_until dal chiamante (calendar): usato tal quale
    r2 = db.add_guidance("NORD", "eps", "FY2026", 4.10,
                         source_doc="pr", source_date="2026-07-22",
                         valid_until="2026-11-05",
                         valid_until_source="prossima trimestrale dal calendar",
                         today=OGGI)
    assert r2["valid_until"] == "2026-11-05"
    g2 = db.get_guidance("NORD", today=OGGI)
    eps_row = [x for x in g2["active"] if x["metric"] == "eps"][0]
    assert eps_row["stale"] is False


def test_review_v6_correzioni(db, monkeypatch):
    # M1: valid_until non-ISO = guidance immortale in silenzio -> RIFIUTATA
    r = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18,
                        source_doc="pr", source_date="2026-07-22",
                        valid_until="Q3 2026", today=OGGI)
    assert "error" in r and "non ISO" in r["error"]
    # B1: source_date non-ISO rifiutata
    r2 = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18,
                         source_doc="pr", source_date="22/07/2026", today=OGGI)
    assert "error" in r2 and "source_date" in r2["error"]
    # B2: unit override su metric non-other rifiutato (la banda presume l'unita')
    r3 = db.add_guidance("ALFA", "revenue_abs", "FY2026", 4500,
                         unit="miliardi EUR", source_doc="pr",
                         source_date="2026-07-22", today=OGGI)
    assert "error" in r3 and "unit" in r3["error"]
    # M2: capex/ricavi 0.8 (capital-intensive in ciclo investimenti) ora ENTRA
    r4 = db.add_guidance("EPSILON.MI", "capex_pct", "FY2026", 0.80,
                         source_doc="piano industriale", source_date="2026-03-01",
                         today=OGGI)
    assert r4.get("ok")
    # range: anche value_high < value_mid e' incoerente (B7)
    r5 = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18, value_high=0.15,
                         source_doc="pr", source_date="2026-07-22", today=OGGI)
    assert "error" in r5 and "range incoerente" in r5["error"]


def test_period_normalizzato_m3(db):
    # "FY26" e "FY2026" sono la STESSA guidance: la seconda supersede la prima
    r1 = db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.17,
                         source_doc="pr Q1", source_date="2026-04-29", today=OGGI)
    r2 = db.add_guidance("ALFA", "revenue_growth", "fy26", 0.18,
                         source_doc="pr Q2", source_date="2026-07-22", today=OGGI)
    assert r1.get("ok") and r2.get("ok")
    assert r2["period"] == "FY2026" and r2["superseded"] == 1
    # "Q3 2026" -> "Q3-2026"
    r3 = db.add_guidance("ALFA", "revenue_growth", "Q3 2026", 0.05,
                         source_doc="pr", source_date="2026-07-22", today=OGGI)
    assert r3["period"] == "Q3-2026"
    # PERIOD diverso stesso ticker+metric: convivono attivi (B7)
    g = db.get_guidance("ALFA", today=OGGI)
    assert len(g["active"]) == 2


def test_next_earnings_date_solo_futuro_a1(monkeypatch):
    # review V6 A1: l'evento di OGGI (release appena letta) NON e' la scadenza —
    # si prende il primo evento STRETTAMENTE futuro; nessuno = None (fallback)
    from bellomberg.market_data import finnhub_news
    eventi = [{"date": "2026-07-23", "symbol": "ALFA"},   # oggi: gia' riportato
              {"date": "2026-10-28", "symbol": "ALFA"},
              {"date": "2027-02-24", "symbol": "ALFA"}]
    monkeypatch.setattr(finnhub_news, "fetch_earnings_for_portfolio",
                        lambda tks, days_ahead=14: eventi)
    assert finnhub_news.next_earnings_date("ALFA", today="2026-07-23") == "2026-10-28"
    monkeypatch.setattr(finnhub_news, "fetch_earnings_for_portfolio",
                        lambda tks, days_ahead=14: [eventi[0]])
    assert finnhub_news.next_earnings_date("ALFA", today="2026-07-23") is None
    # calendar rotto = None dichiarato (fallback a valle), mai eccezione
    def _boom(tks, days_ahead=14):
        raise RuntimeError("api giu'")
    monkeypatch.setattr(finnhub_news, "fetch_earnings_for_portfolio", _boom)
    assert finnhub_news.next_earnings_date("ALFA", today="2026-07-23") is None


def test_backfill_vecchio_warning_b6(db):
    # documento di 4+ mesi fa col fallback 120g: warning dichiarato
    r = db.add_guidance("NORD", "revenue_growth", "FY2026", 0.10,
                        source_doc="pr Q4 FY25", source_date="2026-02-05", today=OGGI)
    assert r.get("ok") and "warning" in r
    assert "trimestrale nuova" in r["warning"]


def test_get_guidance_ticker_vuoto_e_unit_default(db):
    assert "error" in db.get_guidance("")
    r = db.add_guidance("OMEGA.VI", "gross_margin", "FY2026", 0.13,
                        source_doc="pr", source_date="2026-07-01", today=OGGI)
    assert r.get("ok")
    g = db.get_guidance("OMEGA.VI", today=OGGI)
    assert g["active"][0]["unit"] == "pct"           # default dichiarato per metrica
    # other CON unit esplicita: entra
    r2 = db.add_guidance("OMEGA.VI", "other", "FY2026", 1.8, unit="mtpa acciaio",
                         source_doc="piano industriale", source_date="2026-07-01",
                         note="volumi spedizioni", today=OGGI)
    assert r2.get("ok")


# ============================================================
# V6 LOTTO 2 (audit/19 V6.2-V6.3-V6.5): consumo nel motore — helper PURI,
# zero rete, guidance iniettata come payload di get_guidance costruito a mano
# ============================================================
from bellomberg.valuation.dcf_calibration import (_consume_guidance_growth, _consume_guidance_driver)


def _riga(metric="revenue_growth", period="FY2026", mid=0.18, low=None, high=None,
          stale=False, valid_until="2026-10-28", **kw):
    r = {"id": 1, "ticker": "ALFA", "metric": metric, "period": period,
         "value_low": low, "value_mid": mid, "value_high": high, "unit": "pct",
         "source_doc": "press release Q2 FY26", "source_date": "2026-07-22",
         "valid_until": valid_until, "stale": stale}
    r.update(kw)
    return r


def _gpay(*righe):
    return {"ticker": "ALFA", "active": list(righe), "as_of": OGGI.isoformat()}


def test_growth_da_guidance_fade_e_range():
    # mid 18% -> anno 1; anni 2+ = STESSA coda convergente del blend (fade al
    # prior 8%, Y5 a meta' strada verso il terminale 2%)
    g = _consume_guidance_growth(_gpay(_riga(low=0.17, high=0.19)), last_rev=4000,
                                 rep_cur="USD", pg=0.08, terminal_g=0.02,
                                 gf=-0.15, gc=0.45, today=OGGI)
    assert g["growth_fwd"] == [0.18, 0.155, 0.13, 0.08, 0.05]
    assert "GUIDANCE societaria" in g["source"]
    assert "press release Q2 FY26" in g["source"] and "2026-07-22" in g["source"]
    assert "fade verso prior" in g["source"]
    assert g["range_y1"]["low"] == pytest.approx(0.17)
    assert g["range_y1"]["high"] == pytest.approx(0.19)
    assert len(g["used"]) == 1 and g["used"][0]["source_doc"]


def test_growth_guidance_stale_ignorata_e_due_anni():
    # stale -> nessun consumo (il chiamante torna al blend/prior)
    assert _consume_guidance_growth(_gpay(_riga(stale=True)), last_rev=4000,
                                    rep_cur="USD", pg=0.08, terminal_g=0.02,
                                    gf=-0.15, gc=0.45, today=OGGI) is None
    # FY2026 + FY2027 consecutivi -> anno 1 e anno 2 dalla guidance, poi fade
    g = _consume_guidance_growth(
        _gpay(_riga(mid=0.18), _riga(period="FY2027", mid=0.14, id=2)),
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert g["growth_fwd"] == [0.18, 0.14, 0.11, 0.08, 0.05]
    assert len(g["used"]) == 2
    assert "anni 3+ fade" in g["source"]


def test_growth_revenue_abs_conversione_dichiarata():
    # 4720 musd su 4000 reali = +18%; conversione SOLO con flussi USD
    g = _consume_guidance_growth(
        _gpay(_riga(metric="revenue_abs", mid=4720, low=4600, high=4840, unit="musd")),
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert g["growth_fwd"][0] == pytest.approx(0.18)
    assert g["range_y1"]["low"] == pytest.approx(0.15)
    assert g["range_y1"]["high"] == pytest.approx(0.21)
    assert "conversione dichiarata" in g["used"][0]["note"]
    # flussi NON-USD: skip DICHIARATO (mai cambio zitto), growth_fwd None
    g2 = _consume_guidance_growth(
        _gpay(_riga(metric="revenue_abs", mid=4720, unit="musd")),
        last_rev=4000, rep_cur="DKK", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert g2["growth_fwd"] is None
    assert any("NON convertita" in s for s in g2["skips"])
    # a parita' di anno vince revenue_growth (dato diretto)
    g3 = _consume_guidance_growth(
        _gpay(_riga(metric="revenue_abs", mid=9999, unit="musd"), _riga(mid=0.18, id=2)),
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert g3["growth_fwd"][0] == pytest.approx(0.18)


def test_growth_fuori_banda_profilo_dichiarato_e_period_non_fy():
    # collaudo live del 23/07 su un titolo del book: la guidance NON si clampa al floor/cap del profilo
    # (il registro l'ha gia' validata) — fuori banda = si usa e si DICHIARA
    g = _consume_guidance_growth(_gpay(_riga(mid=0.60)), last_rev=4000,
                                 rep_cur="USD", pg=0.08, terminal_g=0.02,
                                 gf=-0.15, gc=0.45, today=OGGI)
    assert g["growth_fwd"][0] == pytest.approx(0.60)
    assert "FUORI dal floor/cap" in g["source"]
    # solo period trimestrale: non consumato nel growth annuale, skip dichiarato
    g2 = _consume_guidance_growth(_gpay(_riga(period="Q3-2026")), last_rev=4000,
                                  rep_cur="USD", pg=0.08, terminal_g=0.02,
                                  gf=-0.15, gc=0.45, today=OGGI)
    assert g2["growth_fwd"] is None
    assert any("non-FY" in s for s in g2["skips"])
    # esercizio PASSATO: non guida il forward
    g3 = _consume_guidance_growth(_gpay(_riga(period="FY2025")), last_rev=4000,
                                  rep_cur="USD", pg=0.08, terminal_g=0.02,
                                  gf=-0.15, gc=0.45, today=OGGI)
    assert g3["growth_fwd"] is None


def test_guidance_warn_scadenza_7g_v64():
    g = _consume_guidance_growth(_gpay(_riga(valid_until="2026-07-28")), last_rev=4000,
                                 rep_cur="USD", pg=0.08, terminal_g=0.02,
                                 gf=-0.15, gc=0.45, today=OGGI)
    assert "scade il 2026-07-28" in g["source"] and "7 giorni" in g["source"]


def test_driver_margine_da_guidance():
    d = _consume_guidance_driver(_gpay(_riga(metric="gross_margin", mid=0.44)),
                                 "gross_margin", today=OGGI)
    assert d["mid"] == pytest.approx(0.44)
    assert "GUIDANCE societaria" in d["label"] and "press release" in d["label"]
    # FY piu' vicina quando ce ne sono due; esercizi passati ignorati
    d2 = _consume_guidance_driver(
        _gpay(_riga(metric="ebitda_margin", period="FY2027", mid=0.35, id=2),
              _riga(metric="ebitda_margin", period="FY2026", mid=0.30),
              _riga(metric="ebitda_margin", period="FY2025", mid=0.99, id=3)),
        "ebitda_margin", today=OGGI)
    assert d2["mid"] == pytest.approx(0.30)
    assert _consume_guidance_driver(_gpay(), "gross_margin", today=OGGI) is None


def test_scenari_dal_range_floor_b14():
    from bellomberg.valuation.dcf_buyside_v3 import _default_scenarios
    base_spec = {"growth_path": [0.18, 0.155, 0.13, 0.08, 0.05], "gross_margin": 0.45,
                 "sector": "", "scenario_spread": 0.03, "_cyclical": True}
    # low 17% MENO prudente del bear meccanico 15% -> vince il MECCANICO (floor B14)
    sc = _default_scenarios(dict(base_spec, _guidance_growth_range={
        "low": 0.17, "high": 0.19, "mid": 0.18, "period": "FY2026",
        "src": "press release Q2 FY26, 2026-07-22"}), history=None)
    assert sc["bear"]["revenue_growth"][0] == pytest.approx(0.15)
    assert "MECCANICO" in sc["bear"]["commentary"]["_guidance_range"]
    # bull anno-1 = high della guidance (19%), NON il meccanico 21%
    assert sc["bull"]["revenue_growth"][0] == pytest.approx(0.19)
    assert "guidance societaria" in sc["bull"]["commentary"]["_guidance_range"]
    # base intatto; anni 2+ restano allo spread meccanico
    assert sc["base"]["revenue_growth"][0] == pytest.approx(0.18)
    assert sc["bear"]["revenue_growth"][1] == pytest.approx(0.125)
    # low 5% PIU' prudente del meccanico -> vince il low della guidance
    sc2 = _default_scenarios(dict(base_spec, _guidance_growth_range={
        "low": 0.05, "high": None, "mid": 0.18, "period": "FY2026",
        "src": "pr, 2026-07-22"}), history=None)
    assert sc2["bear"]["revenue_growth"][0] == pytest.approx(0.05)
    assert "low della guidance" in sc2["bear"]["commentary"]["_guidance_range"]
    # senza high: bull resta MECCANICO e senza nota
    assert sc2["bull"]["revenue_growth"][0] == pytest.approx(0.21)
    assert "_guidance_range" not in sc2["bull"]["commentary"]
    # senza range: comportamento IDENTICO a prima (retrocompatibilita')
    sc3 = _default_scenarios(dict(base_spec), history=None)
    assert sc3["bear"]["revenue_growth"][0] == pytest.approx(0.15)
    assert sc3["bull"]["revenue_growth"][0] == pytest.approx(0.21)
    assert all("_guidance_range" not in sc3[s]["commentary"] for s in sc3)


def test_scenari_range_decade_con_analista():
    # l'analista che fornisce revenue_growth in scenarios sostituisce bear/bull:
    # la nota sul range della guidance DECADE con lui (sovranita' intatta)
    from bellomberg.valuation.dcf_buyside_v3 import _default_scenarios, _merge_scenarios
    defaults = _default_scenarios(
        {"growth_path": [0.18, 0.155, 0.13, 0.08, 0.05], "gross_margin": 0.45,
         "sector": "", "scenario_spread": 0.03, "_cyclical": True,
         "_guidance_growth_range": {"low": 0.05, "high": 0.19, "mid": 0.18,
                                    "period": "FY2026", "src": "pr, 2026-07-22"}},
        history=None)
    merged = _merge_scenarios({"bear": {"revenue_growth": [0.02] * 5}}, defaults)
    assert merged["bear"]["revenue_growth"][0] == pytest.approx(0.02)
    assert "_guidance_range" not in merged["bear"]["commentary"]
    # il bull (non toccato dall'analista) conserva la sua nota
    assert "_guidance_range" in merged["bull"]["commentary"]


def test_fetch_guidance_mai_db_fantasma(monkeypatch, tmp_path):
    # incidente app/data 23/07: cwd sbagliato NON deve creare un DB vuoto —
    # registro assente = buco dichiarato, zero file creati
    from bellomberg.storage import memory_db; from bellomberg.valuation import dcf_engine
    fantasma = tmp_path / "cwd_sbagliato" / "data" / "consigliere.db"
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(fantasma))
    monkeypatch.setattr(dcf_engine, "_MEMDB_GUID", None)
    g, nota = dcf_engine._fetch_guidance("ALFA")
    assert g is None and "assente" in nota
    assert not fantasma.parent.exists()
    # col DB vero (tmp) e una guidance dentro: payload attivo
    vero = MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))
    vero.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18,
                      source_doc="pr", source_date="2026-07-22", today=OGGI)
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.setattr(memory_db, "CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(dcf_engine, "_MEMDB_GUID", None)
    g2, nota2 = dcf_engine._fetch_guidance("ALFA")
    assert nota2 is None and len(g2["active"]) == 1
    # ticker senza guidance: (None, None) — non e' un buco
    g3, nota3 = dcf_engine._fetch_guidance("NORD")
    assert g3 is None and nota3 is None


def test_review_l2_alta1_revenue_abs_anno2_annualizzato():
    # ALTA-1: revenue_abs FY2027 e' un LIVELLO cumulato — l'anno 2 del path vuole
    # il growth ANNUALE sopra l'anno 1, non il cumulato sulla base storica
    g = _consume_guidance_growth(
        _gpay(_riga(metric="revenue_abs", mid=4400, unit="musd"),
              _riga(metric="revenue_abs", period="FY2027", mid=4800, unit="musd", id=2)),
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert g["growth_fwd"][0] == pytest.approx(0.10)
    assert g["growth_fwd"][1] == pytest.approx(4800.0 / 4400.0 - 1, abs=1e-3)  # ~9.1%, NON 20%
    assert "growth ANNUALE ricalcolato" in g["used"][1]["note"]
    # mix: revenue_growth FY2026 + revenue_abs FY2027, stessa annualizzazione
    g2 = _consume_guidance_growth(
        _gpay(_riga(mid=0.10), _riga(metric="revenue_abs", period="FY2027",
                                     mid=4800, unit="musd", id=2)),
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert g2["growth_fwd"][1] == pytest.approx(4800.0 / 4400.0 - 1, abs=1e-3)


def test_review_l2_media3_fy_oltre_orizzonte_dichiarata():
    # FY2028 non consecutiva: consumata solo FY2026, ma la riga persa si DICHIARA
    g = _consume_guidance_growth(
        _gpay(_riga(mid=0.10), _riga(period="FY2028", mid=0.20, id=2)),
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert g["growth_fwd"][0] == pytest.approx(0.10)
    assert len(g["used"]) == 1
    assert any("FY2028" in s for s in g["skips"])
    assert "NON consumate" in g["source"] or "NON consumata" in g["source"]


def test_review_l2_media2_driver_skip_dichiarato():
    # gross_margin H1-2026 (comune in Europa): non consumabile in v1 ma il buco
    # torna al chiamante come skip_note, non None zitto
    d = _consume_guidance_driver(
        _gpay(_riga(metric="gross_margin", period="H1-2026", mid=0.44)),
        "gross_margin", today=OGGI)
    assert d["mid"] is None and "non-FY" in d["skip_note"]
    # nessuna riga per la metrica: None (non c'e' nulla da dichiarare)
    assert _consume_guidance_driver(_gpay(_riga(mid=0.18)), "gross_margin",
                                    today=OGGI) is None


def test_review_l2_media1_stale_dichiarata_nel_fetch(monkeypatch, tmp_path):
    # registro con SOLO guidance scadute: il fetch propaga la nota STALE nel
    # payload (guidance_note) — mai tornare al CAGR cieco in silenzio
    from bellomberg.storage import memory_db; from bellomberg.valuation import dcf_engine
    db_path = str(tmp_path / "data" / "test.db")
    db = MemoryDB(db_path=db_path, chroma_path=str(tmp_path / "chroma"))
    db.add_guidance("ALFA", "revenue_growth", "FY2026", 0.18,
                    source_doc="pr", source_date="2026-03-01",
                    valid_until="2026-06-30", today=date(2026, 3, 2))
    monkeypatch.setattr(memory_db, "SQLITE_PATH", db_path)
    monkeypatch.setattr(memory_db, "CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(dcf_engine, "_MEMDB_GUID", None)
    g, nota = dcf_engine._fetch_guidance("ALFA")
    assert g is not None and "STALE" in nota
    # e la calibrazione la filtra: righe stale mai consumate
    assert _consume_guidance_growth(g, last_rev=4000, rep_cur="USD", pg=0.08,
                                    terminal_g=0.02, gf=-0.15, gc=0.45,
                                    today=OGGI) is None


def test_review_l2_media4_eps_valuta_e_negativo(monkeypatch):
    from bellomberg.market_data import consensus_estimates; from bellomberg.valuation import dcf_engine
    fake = {"ticker": "NORD", "eps_estimates": [{"period": "0y", "avg": 2.9}]}
    monkeypatch.setattr(consensus_estimates, "get_consensus", lambda t: fake)
    # eps guidato in DKK, consensus ADR in USD: confronto NON fatto, dichiarato
    out = dcf_engine._guidance_vs_consensus(
        _gpay(_riga(metric="eps", mid=31.0, unit="eps")), "NORD",
        fin_currency="DKK", quote_currency="USD", today=OGGI)
    assert "NON fatto" in out[0]["note"]
    # consensus eps negativo: delta ASSOLUTO dichiarato, mai un % col segno rotto
    fake2 = {"ticker": "X", "eps_estimates": [{"period": "0y", "avg": -2.0}]}
    monkeypatch.setattr(consensus_estimates, "get_consensus", lambda t: fake2)
    out2 = dcf_engine._guidance_vs_consensus(
        _gpay(_riga(metric="eps", mid=-1.0, unit="eps")), "X",
        fin_currency="USD", quote_currency="USD", today=OGGI)
    assert out2[0]["delta_abs"] == pytest.approx(1.0)
    assert "delta_pct" not in out2[0] and "assoluto" in out2[0]["note"]


# ============================================================
# V6 LOTTO 3 (audit/19 V6.5-V6.6): nudge anti-deriva + entered_by fine
# ============================================================

def test_confronto_override_vs_guidance_v65():
    # review L3 B3: helper puro estratto — il confronto quando l'analista vince
    from bellomberg.valuation.dcf_calibration import _compare_override_vs_guidance
    # confrontabile: suffisso con fonte + deviazione = analista - guidance
    suff, dev = _compare_override_vs_guidance(
        _gpay(_riga(mid=-0.032, valid_until="2026-10-28")), override_y1=0.05,
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert dev == pytest.approx(8.2)
    assert "GUIDANCE attiva" in suff and "vince l'analista" in suff
    assert "press release" in suff
    # review L3 MEDIA-1a: guidance in scadenza entro 7g -> WARN anche a chi
    # la sta scavalcando (V6.4)
    suff2, _ = _compare_override_vs_guidance(
        _gpay(_riga(mid=-0.032, valid_until="2026-07-28")), override_y1=0.05,
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert "scade il 2026-07-28" in suff2
    # review L3 MEDIA-1b: guidance presente ma NON confrontabile (solo H1) ->
    # dichiarata in etichetta, deviazione None (il nudge non scatta)
    suff3, dev3 = _compare_override_vs_guidance(
        _gpay(_riga(period="H1-2026")), override_y1=0.05,
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI)
    assert dev3 is None and "NON confrontabile" in suff3 and "non-FY" in suff3
    # registro senza righe revenue: (None, None), etichetta intatta
    assert _compare_override_vs_guidance(
        _gpay(_riga(metric="gross_margin", mid=0.44)), override_y1=0.05,
        last_rev=4000, rep_cur="USD", pg=0.08, terminal_g=0.02,
        gf=-0.15, gc=0.45, today=OGGI) == (None, None)


def test_nudge_anti_deriva_v65():
    from bellomberg.agents.chat_tools import _guidance_drift_nudge
    # deviazione oltre 5pp e variant view MUTA sulla guidance: nudge
    n = _guidance_drift_nudge({"guidance_deviation_pp": 8.2},
                              "vedo re-rating sul backlog")
    assert n and "NUDGE GUIDANCE" in n and "+8.2pp" in n
    # review L3 B2: NEGARE la guidance senza numeri NON sopprime il nudge
    assert _guidance_drift_nudge({"guidance_deviation_pp": 8.2},
                                 "non c'e' guidance disponibile") is not None
    # deviazione negativa oltre soglia: nudge anche al ribasso
    assert _guidance_drift_nudge({"guidance_deviation_pp": -6.0}, "") is not None
    # entro i 5pp: silenzio (la soglia e' del design, non un blocco)
    assert _guidance_drift_nudge({"guidance_deviation_pp": 4.9}, "") is None
    assert _guidance_drift_nudge({"guidance_deviation_pp": -5.0}, "") is None
    # variant view che CITA la guidance: l'analista ha dichiarato, niente nudge
    assert _guidance_drift_nudge({"guidance_deviation_pp": 9.0},
                                 "guidance +18%, io +9% per execution risk") is None
    # nessuna guidance attiva / payload rotto: silenzio, mai eccezioni
    assert _guidance_drift_nudge({"guidance_deviation_pp": None}, "x") is None
    assert _guidance_drift_nudge({}, "x") is None
    assert _guidance_drift_nudge(None, "x") is None


def test_entered_by_fine_via_dispatch_b4(monkeypatch, tmp_path):
    # review B4 (Lotto 1) chiusa: il registro sa CHI ha scritto — il caller del
    # dispatch finisce in entered_by; senza caller resta il generico v1.
    from bellomberg.storage import memory_db; from bellomberg.market_data import finnhub_news; from bellomberg.agents import chat_tools
    db = MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                  chroma_path=str(tmp_path / "chroma"))
    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: db)
    monkeypatch.setattr(finnhub_news, "next_earnings_date", lambda t: None)
    base = {"ticker": "ALFA", "metric": "revenue_growth", "period": "FY2026",
            "value_mid": 0.18, "source_doc": "pr Q2", "source_date": "2026-07-22"}
    # il dispatch incapsula l'esito del tool sotto "data" (_stamp)
    r1 = chat_tools.dispatch("add_guidance", dict(base),
                             caller="specialista-run:fundamentals")
    assert r1["data"].get("ok"), r1
    r2 = chat_tools.dispatch("add_guidance", dict(base, period="FY2027"),
                             caller="chat:fundamentals")
    assert r2["data"].get("ok"), r2
    r3 = chat_tools.dispatch("add_guidance", dict(base, period="FY2028"))
    assert r3["data"].get("ok"), r3
    with db._conn() as conn:
        by_period = {r["period"]: r["entered_by"] for r in conn.execute(
            "SELECT period, entered_by FROM company_guidance")}
    assert by_period["FY2026"] == "specialista-run:fundamentals"
    assert by_period["FY2027"] == "chat:fundamentals"
    assert by_period["FY2028"] == "agente/chat (D1)"    # retrocompatibilita'


def test_guidance_vs_consensus_v65(monkeypatch):
    from bellomberg.market_data import consensus_estimates; from bellomberg.valuation import dcf_engine
    fake = {"ticker": "ALFA",
            "revenue_estimates": [{"period": "0y", "avg": 3.9e9, "growth": 0.15},
                                  {"period": "+1y", "avg": 4.3e9, "growth": 0.11}],
            "eps_estimates": [{"period": "0y", "avg": 9.0}]}
    monkeypatch.setattr(consensus_estimates, "get_consensus", lambda t: fake)
    guid = _gpay(_riga(metric="revenue_growth", mid=0.18), _riga(metric="eps", mid=9.9, id=2, unit="eps"))
    out = dcf_engine._guidance_vs_consensus(guid, "ALFA", fin_currency="USD", today=OGGI)
    per_metric = {o["metric"]: o for o in out}
    assert per_metric["revenue_growth"]["delta_pp"] == pytest.approx(3.0)
    assert per_metric["eps"]["delta_pct"] == pytest.approx(10.0)
    assert all("[src:" in o["src"] for o in out)
    # revenue_abs con flussi non-USD: confronto NON fatto, dichiarato
    out2 = dcf_engine._guidance_vs_consensus(
        _gpay(_riga(metric="revenue_abs", mid=4720, unit="musd")),
        "NORD", fin_currency="DKK", today=OGGI)
    assert "NON fatto" in out2[0]["note"]
    # consensus rotto: nota dichiarata, mai eccezione
    def _boom(t):
        raise RuntimeError("yahoo giu'")
    monkeypatch.setattr(consensus_estimates, "get_consensus", _boom)
    out3 = dcf_engine._guidance_vs_consensus(guid, "ALFA", fin_currency="USD", today=OGGI)
    assert "n.d." in out3["note"]


def test_other_con_unit_diverse_non_si_supersedono(db):
    """20/08 (gate delle trimestrali arretrate, Opus 5): i 5 target FY26 di una
    societa' EU del book erano tutti in EURO, quindi tutti metric='other' (revenue_abs ha
    unit fissa 'musd') — l'unica cosa che li distingue e' `unit`. Col supersede
    su (ticker, metric, period) l'EBITA veniva dichiarato "sostituito" dal FOCF,
    che non lo sostituisce affatto: misurato su copia del DB, 3 righe inserite e
    1 sola attiva. Grandezze diverse DEVONO convivere.
    """
    comune = dict(source_doc="Guidance 2026 sito IR + PR H1 30/07/2026",
                  source_date="2026-07-30", valid_until="2026-11-05",
                  today=date(2026, 8, 20))
    r1 = db.add_guidance("GAMMA.MI", "other", "FY2026", 31500,
                         unit="meur (ordini FY)", **comune)
    r2 = db.add_guidance("GAMMA.MI", "other", "FY2026", 2450,
                         unit="meur (EBITA FY)", **comune)
    r3 = db.add_guidance("GAMMA.MI", "other", "FY2026", 1180,
                         unit="meur (FOCF FY)", **comune)
    assert r1.get("ok") and r1["superseded"] == 0
    assert r2.get("ok") and r2["superseded"] == 0
    assert r3.get("ok") and r3["superseded"] == 0
    attive = db.get_guidance("GAMMA.MI", today=date(2026, 8, 20))["active"]
    assert len(attive) == 3
    assert {a["unit"] for a in attive} == {"meur (ordini FY)", "meur (EBITA FY)",
                                           "meur (FOCF FY)"}


def test_stessa_unit_supersede_ancora(db):
    """La cura non deve spegnere il supersede VERO: stessa grandezza alla
    trimestrale dopo = sostituzione, storico conservato (regressione della
    modifica 20/08)."""
    r1 = db.add_guidance("GAMMA.MI", "other", "FY2026", 29600,
                         unit="meur (ordini FY)", source_doc="PR Q1 2026",
                         source_date="2026-05-07", today=date(2026, 5, 8))
    r2 = db.add_guidance("GAMMA.MI", "other", "FY2026", 31500,
                         unit="meur (ordini FY)", source_doc="PR H1 2026",
                         source_date="2026-07-30", today=date(2026, 8, 20))
    assert r1.get("ok") and r2.get("ok")
    assert r2["superseded"] == 1
    g = db.get_guidance("GAMMA.MI", include_history=True, today=date(2026, 8, 20))
    assert len(g["active"]) == 1
    assert g["active"][0]["value_mid"] == 31500
    assert g["history"][0]["superseded_by"] == r2["id"]


def test_unit_refuso_maiuscole_supersede_lo_stesso(db):
    """Il rovescio del rischio: se `unit` distingue le grandezze, un refuso di
    battitura ('MEUR (Ordini FY)' contro 'meur (ordini FY)') creerebbe DUE righe
    attive per la stessa grandezza — il registro direbbe due numeri veri insieme.
    Il confronto e' quindi insensibile a maiuscole e spazi ai bordi."""
    db.add_guidance("GAMMA.MI", "other", "FY2026", 29600, unit="meur (ordini FY)",
                    source_doc="PR Q1", source_date="2026-05-07",
                    today=date(2026, 5, 8))
    r2 = db.add_guidance("GAMMA.MI", "other", "FY2026", 31500,
                         unit="  MEUR (Ordini FY) ", source_doc="PR H1",
                         source_date="2026-07-30", today=date(2026, 8, 20))
    assert r2["superseded"] == 1
    assert len(db.get_guidance("GAMMA.MI", today=date(2026, 8, 20))["active"]) == 1


def test_unit_con_spazi_invisibili_e_la_stessa_grandezza(db):
    """Review avversariale 20/08: `lower(trim(unit))` di SQLite non toglie gli
    spazi INTERNI, quindi 'meur (ordini FY)' scritto con uno spazio unificatore
    (NBSP) restava una riga attiva ACCANTO a quella con lo spazio normale — due
    numeri diversi per la stessa grandezza, e a schermo le due etichette sono
    identiche. Il confronto ora si fa in Python su una chiave normalizzata."""
    db.add_guidance("GAMMA.MI", "other", "FY2026", 29600, unit="meur (ordini FY)",
                    source_doc="PR Q1", source_date="2026-05-07", today=date(2026, 5, 8))
    # NBSP fra 'meur' e la parentesi + uno zero-width space in mezzo alla parola
    r2 = db.add_guidance("GAMMA.MI", "other", "FY2026", 31500,
                         unit="meur\u00a0(ordini\u200b FY)", source_doc="PR H1",
                         source_date="2026-07-30", today=date(2026, 8, 20))
    assert r2["superseded"] == 1
    assert len(db.get_guidance("GAMMA.MI", today=date(2026, 8, 20))["active"]) == 1


def test_unit_con_spazi_ripetuti_e_la_stessa_grandezza(db):
    """La docstring di `_chiave_unit` promette anche «spazi ripetuti»: fino al 05/09
    nessun test lo misurava, e il banco delle batterie riscritte l'ha trovato (una
    mutazione che toglieva `" ".join(s.split())` passava la batteria intera)."""
    db.add_guidance("GAMMA.MI", "other", "FY2026", 29600, unit="meur (ordini FY)",
                    source_doc="PR Q1", source_date="2026-05-07", today=date(2026, 5, 8))
    r2 = db.add_guidance("GAMMA.MI", "other", "FY2026", 31500,
                         unit="meur   (ordini    FY)", source_doc="PR H1",
                         source_date="2026-07-30", today=date(2026, 8, 20))
    assert r2["superseded"] == 1
    assert len(db.get_guidance("GAMMA.MI", today=date(2026, 8, 20))["active"]) == 1


def test_unit_accentata_maiuscola_e_la_stessa_grandezza(db):
    """La garanzia "insensibile a maiuscole" era una frase piu' larga della misura:
    lower() di SQLite e' solo-ASCII ('UTILITÀ' -> 'utilitÀ'), quindi un'etichetta
    accentata in maiuscolo NON faceva il supersede. casefold() in Python si'."""
    db.add_guidance("DELTA.MI", "other", "FY2026", 2150,
                    unit="meur (EBITDA rettificato FY, utilità)",
                    source_doc="slide 23", source_date="2026-02-24", today=date(2026, 2, 25))
    r2 = db.add_guidance("DELTA.MI", "other", "FY2026", 2050,
                         unit="MEUR (EBITDA RETTIFICATO FY, UTILITÀ)",
                         source_doc="slide 23 update", source_date="2026-07-28",
                         today=date(2026, 8, 20))
    assert r2["superseded"] == 1
    assert len(db.get_guidance("DELTA.MI", today=date(2026, 8, 20))["active"]) == 1


def test_righe_che_restano_accanto_sono_DICHIARATE(db):
    """regola 14/07 applicata al rovescio del supersede: `superseded: 0` vale sia
    "prima registrazione" sia "ho lasciato attiva una riga che forse e' la stessa
    grandezza con un'altra etichetta". I due casi devono essere distinguibili dal
    chiamante, altrimenti il registro puo' affermare due numeri per la stessa cosa
    in silenzio."""
    r1 = db.add_guidance("GAMMA.MI", "other", "FY2026", 2450, unit="meur (EBITA FY)",
                         source_doc="PR H1", source_date="2026-07-30",
                         today=date(2026, 8, 20))
    # prima registrazione: nessuna convivenza da dichiarare
    assert r1["superseded"] == 0 and "convivono" not in r1

    # stessa grandezza, etichetta cambiata al trimestre dopo: NON sostituisce, e lo dice
    r2 = db.add_guidance("GAMMA.MI", "other", "FY2026", 2500, unit="meur (EBITA FY26)",
                         source_doc="PR 9M", source_date="2026-11-05",
                         today=date(2026, 11, 6))
    assert r2["superseded"] == 0
    assert "convivono" in r2
    assert [c["value_mid"] for c in r2["convivono"]] == [2450]
    assert "RESTANO ATTIVE" in r2["nota"]
    assert len(db.get_guidance("GAMMA.MI", today=date(2026, 11, 6))["active"]) == 2


def test_metriche_a_unit_fissa_non_dichiarano_convivenze(db):
    """Il commento nel codice AFFERMA che per le metriche a unit fissa nulla cambia:
    qui e' una misura. revenue_growth ha unit imposta ('pct') e un override viene
    rifiutato, quindi due registrazioni si sostituiscono come sempre e non c'e'
    nessuna convivenza da dichiarare."""
    db.add_guidance("NORD", "revenue_growth", "FY2026", -0.06,
                    source_doc="pr Q1", source_date="2026-05-06", today=date(2026, 5, 7))
    r2 = db.add_guidance("NORD", "revenue_growth", "FY2026", -0.02,
                         source_doc="pr Q2", source_date="2026-08-04", today=date(2026, 8, 20))
    assert r2["superseded"] == 1 and "convivono" not in r2
    # e una metrica DIVERSA convive senza essere segnalata come doppione ambiguo:
    # metric diverso = chiave diversa, non e' il caso che la dichiarazione copre
    r3 = db.add_guidance("NORD", "ebitda_margin", "FY2026", 0.38,
                         source_doc="pr Q2", source_date="2026-08-04", today=date(2026, 8, 20))
    assert r3["superseded"] == 0 and "convivono" not in r3
