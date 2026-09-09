"""Quant fase 1a — esposizione settoriale (23/07, audit/20 rosa).
Offline: fetch iniettato, cache su tmp, summary sintetico.

09/09: le operazioni rileggono le VISTE del negozio dei veicoli (`settore_tema`,
`bucket_economico`) una volta per chiamata. Qui si inietta il negozio con simboli
INVENTATI; gli export snapshot restano coperti solo come contratto di compatibilita'.
"""
import json
import os

import pytest

import bellomberg.storage.classificazione as cl
import bellomberg.portfolio.portfolio_sectors as ps
import bellomberg.portfolio.sizing_engine as se

# override tematici e bucket FINTI, nella forma delle viste
OVR = {"TESORO": "Crypto treasury (BTC)", "CHIPX.MI": "ETF Chip (tema)",
       "PAESE.MI": "ETF Paese (tema)", "FONDO.L": "Fondo chiuso (holding finta)"}
ECON = {"TESORO": "Crypto", "CHIPX.MI": "Technology",
        "PAESE.MI": ps._MULTI_PREFIX + " (paniere paese/EM)",
        "FONDO.L": ps._MULTI_PREFIX + " (holding finta)"}
POLICY = {"BANCA.MI": "banks", "SEMI.MI": "semis"}     # la mappa di policy del sizing, finta

VOCI = {
    "TESORO": {"tipo": "dat", "provenienza": "dichiarato", "settore_tema": "Crypto treasury (BTC)",
               "bucket_economico": "Crypto"},
    "CHIPX.MI": {"tipo": "etf", "provenienza": "dichiarato", "settore_tema": "ETF Chip (tema)",
                 "bucket_economico": "Technology"},
    "NUOVO.MI": {"tipo": "etf", "provenienza": "dichiarato", "settore_tema": "ETF Nuovo (tema)"},   # senza bucket
    "BANCA.MI": {"tipo": "bank", "provenienza": "dichiarato", "settore_policy": "banks"},          # senza tema
}


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "CACHE_PATH", str(tmp_path / "sector_cache.json"))


@pytest.fixture
def finto(monkeypatch):
    """Negozio live sintetico con simboli inventati e modificabile dal test."""
    voci = {
        tk: {"tipo": "dat" if tk == "TESORO" else "etf",
             "provenienza": "dichiarato", "settore_tema": tema,
             "bucket_economico": ECON[tk]}
        for tk, tema in OVR.items()
    }
    negozio = {"veicoli": voci, "origine": "finto.json", "motivo": None}
    monkeypatch.setattr(ps.cl, "carica_veicoli", lambda: negozio)
    monkeypatch.setattr(se, "SECTOR_OF", dict(POLICY))
    return negozio


def _fetch_factory(calls, mapping):
    def fetch(tk):
        calls.append(tk)
        return mapping.get(tk, {"sector": None, "industry": None})
    return fetch


# --------------------------------------------------------------------------
# le viste vengono dal negozio (lotto 2b)
# --------------------------------------------------------------------------

def test_settori_tema_e_bucket_derivati_dal_negozio(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(VOCI), encoding="utf-8")
    n = cl.carica_veicoli(str(p))
    assert ps.settori_tema_del_negozio(n) == {"TESORO": "Crypto treasury (BTC)",
                                              "CHIPX.MI": "ETF Chip (tema)", "NUOVO.MI": "ETF Nuovo (tema)"}
    assert ps.bucket_economici_del_negozio(n) == {"TESORO": "Crypto", "CHIPX.MI": "Technology"}
    assente = cl.carica_veicoli(str(tmp_path / "manca.json"))
    assert ps.settori_tema_del_negozio(assente) == {} and ps.bucket_economici_del_negozio(assente) == {}


def test_le_viste_all_import_sono_derivate_dal_negozio_corrente():
    assert isinstance(ps.SECTOR_OVERRIDES, dict) and isinstance(ps.ECON_BUCKET_OF, dict)
    assert set(ps.NEGOZIO_ESITO) >= {"veicoli", "origine", "motivo"}
    corrente = cl.carica_veicoli()          # riletto ORA dal disco, non l'esito dell'import
    if corrente["origine"] in ("assente", "illeggibile"):
        pytest.skip("negozio dei veicoli %s: la misura sarebbe vacua ({} == {})" % corrente["origine"])
    assert ps.SECTOR_OVERRIDES == ps.settori_tema_del_negozio(corrente)
    assert ps.ECON_BUCKET_OF == ps.bucket_economici_del_negozio(corrente)


def test_negozio_assente_o_illeggibile_dichiarato_nelle_note(finto, monkeypatch):
    summary = {"positions": [{"ticker": "BANCA.MI", "valore_mercato": 1000.0}], "stale_positions": None}
    fetch = _fetch_factory([], {"BANCA.MI": {"sector": "Financial Services", "industry": "Banks"}})
    monkeypatch.setattr(ps.cl, "carica_veicoli", lambda: {
        "veicoli": {}, "origine": "assente", "motivo": "negozio non trovato: x"})
    out = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    joined = " ".join(out["notes"])
    assert "negozio dei veicoli ASSENTE" in joined and "override" in joined
    monkeypatch.setattr(ps.cl, "carica_veicoli", lambda: {
        "veicoli": {}, "origine": "illeggibile", "motivo": "virgola in piu'"})
    out2 = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    joined2 = " ".join(out2["notes"])
    assert "negozio dei veicoli ILLEGGIBILE" in joined2 and "virgola in piu'" in joined2
    # negozio leggibile: nessuna nota sul negozio
    monkeypatch.setattr(ps.cl, "carica_veicoli", lambda: finto)
    out3 = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    assert not any("negozio dei veicoli" in n for n in out3["notes"])


# --------------------------------------------------------------------------
# la meccanica di ieri, sui simboli inventati
# --------------------------------------------------------------------------

def test_override_vince_e_fetch_solo_su_miss(finto):
    calls = []
    fetch = _fetch_factory(calls, {"BANCA.MI": {"sector": "Financial Services",
                                                "industry": "Banks"}})
    m = ps.get_sector_map(["TESORO", "BANCA.MI"], fetch=fetch)
    assert m["TESORO"]["sector"] == "Crypto treasury (BTC)"    # override DAT dichiarato
    assert m["TESORO"]["source"] == "override"
    assert calls == ["BANCA.MI"]                              # niente fetch sugli override
    assert m["BANCA.MI"]["sector"] == "Financial Services"
    assert m["BANCA.MI"]["source"] == "yfinance"


def test_cache_persiste_su_disco(finto):
    calls = []
    fetch = _fetch_factory(calls, {"SALUTE.MI": {"sector": "Healthcare", "industry": "Devices"}})
    ps.get_sector_map(["SALUTE.MI"], fetch=fetch)
    m2 = ps.get_sector_map(["SALUTE.MI"], fetch=fetch)
    assert calls == ["SALUTE.MI"], "secondo giro deve leggere dalla cache, non rifetchare"
    assert m2["SALUTE.MI"]["source"] == "cache(yfinance)"
    assert os.path.exists(ps.CACHE_PATH)


def test_fetch_fallito_resta_none_dichiarato(finto):
    m = ps.get_sector_map(["ZZZ"], fetch=lambda tk: {"sector": None, "industry": None})
    assert m["ZZZ"]["sector"] is None                        # n.d., mai inventato
    assert "retry" in m["ZZZ"]["source"]


def test_fetch_fallito_non_avvelena_la_cache(finto, tmp_path):
    """Review MEDIA-2: il None NON si cachea (retry al giro dopo) e un refetch
    fallito NON sovrascrive un valore buono (che resta, dichiarato STALE)."""
    calls = []
    ps.get_sector_map(["ZZZ"], fetch=_fetch_factory(calls, {}))       # fallisce
    ps.get_sector_map(["ZZZ"], fetch=_fetch_factory(calls, {}))       # RIprova
    assert calls == ["ZZZ", "ZZZ"], "il null cacheato avrebbe evitato il retry"
    # valore buono, poi TTL scaduto e refetch fallito -> si tiene il vecchio, STALE
    ok = _fetch_factory([], {"SALUTE.MI": {"sector": "Healthcare", "industry": "Dev"}})
    ps.get_sector_map(["SALUTE.MI"], fetch=ok)
    with open(ps.CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)
    cache["SALUTE.MI"]["ts"] = 0                                      # scade il TTL
    with open(ps.CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    m = ps.get_sector_map(["SALUTE.MI"], fetch=_fetch_factory([], {}))      # refetch KO
    assert m["SALUTE.MI"]["sector"] == "Healthcare"
    assert "STALE" in m["SALUTE.MI"]["source"]


def test_fx_incomplete_propagato(finto):
    """FX rotto nel summary -> nessun peso misto pubblicato."""
    summary = {"positions": [{"ticker": "TESORO", "valore_mercato": 1000.0}],
               "stale_positions": None, "fx_incomplete": ["TESORO:USD"]}
    out = ps.compute_sector_exposure(summary=summary, fetch=lambda tk: {"sector": None, "industry": None})
    assert "FX incompleto" in out["error"] and "TESORO:USD" in out["error"]
    assert "by_sector" not in out


def _summary():
    return {"positions": [
        {"ticker": "BANCA.MI", "valore_mercato": 40000.0},
        {"ticker": "BANCA2.MI", "valore_mercato": 20000.0},
        {"ticker": "TESORO", "valore_mercato": 30000.0},
        {"ticker": "ZZZ", "valore_mercato": 10000.0},        # settore n.d.
    ], "stale_positions": ["ZZZ"]}


def test_exposure_pesi_hhi_e_bucket_nd(finto):
    fetch = _fetch_factory([], {
        "BANCA.MI": {"sector": "Financial Services", "industry": "Banks"},
        "BANCA2.MI": {"sector": "Financial Services", "industry": "Banks"},
        "ZZZ": {"sector": None, "industry": None},
    })
    out = ps.compute_sector_exposure(summary=_summary(), fetch=fetch)
    by = {b["sector"]: b for b in out["by_sector"]}
    assert by["Financial Services"]["weight_pct"] == pytest.approx(60.0)
    assert by["Crypto treasury (BTC)"]["weight_pct"] == pytest.approx(30.0)
    assert by["n.d."]["weight_pct"] == pytest.approx(10.0)
    assert by["n.d."]["tickers"] == ["ZZZ"]
    hhi_atteso = 0.6 ** 2 + 0.3 ** 2 + 0.1 ** 2
    assert out["hhi_sector"] == pytest.approx(hhi_atteso, abs=1e-4)
    assert out["coverage_pct"] == pytest.approx(90.0)
    # buchi dichiarati: n.d. + prezzi stale
    joined = " ".join(out["notes"])
    assert "ZZZ" in joined and "NON spalmato" in joined and "STALE" in joined


def test_ttl_scaduto_refetch_ok_aggiorna_cache(finto):
    """Coda fase 1a: TTL scaduto + refetch BUONO -> il valore nuovo vince e si ricachea."""
    ps.get_sector_map(["SALUTE.MI"], fetch=_fetch_factory([], {"SALUTE.MI": {"sector": "Healthcare",
                                                                             "industry": "Dev"}}))
    with open(ps.CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)
    cache["SALUTE.MI"]["ts"] = 0                                      # scade il TTL
    with open(ps.CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    calls = []
    m = ps.get_sector_map(["SALUTE.MI"], fetch=_fetch_factory(calls, {"SALUTE.MI": {"sector": "Technology",
                                                                                    "industry": "X"}}))
    assert calls == ["SALUTE.MI"], "TTL scaduto deve rifetchare"
    assert m["SALUTE.MI"]["sector"] == "Technology"
    m2 = ps.get_sector_map(["SALUTE.MI"], fetch=_fetch_factory([], {}))     # ora dalla cache nuova
    assert m2["SALUTE.MI"]["sector"] == "Technology"
    assert m2["SALUTE.MI"]["source"] == "cache(yfinance)"


def test_valore_mercato_none_non_crasha(finto):
    """Coda fase 1a: valore_mercato None -> peso 0 dichiarabile, nessun crash."""
    summary = {"positions": [{"ticker": "TESORO", "valore_mercato": None},
                             {"ticker": "BANCA.MI", "valore_mercato": 1234.56}],
               "stale_positions": None}
    fetch = _fetch_factory([], {"BANCA.MI": {"sector": "Financial Services", "industry": "B"}})
    out = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    by = {b["sector"]: b for b in out["by_sector"]}
    assert by["Financial Services"]["weight_pct"] == pytest.approx(100.0)
    assert by["Crypto treasury (BTC)"]["weight_pct"] == pytest.approx(0.0)
    bye = {b["bucket"]: b for b in out["econ_axis"]["by_bucket"]}
    assert bye["Crypto"]["weight_pct"] == pytest.approx(0.0)    # anche sull'asse econ


def test_econ_axis_nd_da_fetch_fallito_e_coverage(finto):
    """Review 1b (BASSA-7): fetch fallito -> n.d. anche sull'asse econ, coverage <100."""
    fetch = _fetch_factory([], {"BANCA.MI": {"sector": "Financial Services", "industry": "B"}})
    summary = {"positions": [{"ticker": "BANCA.MI", "valore_mercato": 1234.56},
                             {"ticker": "ZZZ", "valore_mercato": 1234.56}],
               "stale_positions": None}
    out = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    ea = out["econ_axis"]
    by = {b["bucket"]: b for b in ea["by_bucket"]}
    assert by["n.d."]["tickers"] == ["ZZZ"]
    assert ea["coverage_pct"] == pytest.approx(50.0)


def test_econ_axis_voce_manuale_che_maschera_gics_dichiarata(finto, monkeypatch):
    """Review 1b (MEDIA-1): un single-stock finito per sbaglio nel bucket economico
    con voce diversa dal GICS vero NON deve mascherare la fonte zitto (classe F-16)."""
    fetch = _fetch_factory([], {"SOLARE.MI": {"sector": "Technology", "industry": "Solar"}})
    finto["veicoli"]["SOLARE.MI"] = {
        "tipo": "single", "provenienza": "dichiarato", "settore_tema": None,
        "bucket_economico": "Energy"}
    summary = {"positions": [{"ticker": "SOLARE.MI", "valore_mercato": 1000.0}],
               "stale_positions": None}
    out = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    by = {b["bucket"]: b for b in out["econ_axis"]["by_bucket"]}
    assert "Energy" in by                                     # la voce manuale si applica...
    joined = " ".join(out["notes"])
    assert "SOLARE.MI" in joined and "DIVERGE" in joined      # ...ma la divergenza e' dichiarata


def test_econ_axis_unifica_etf_e_single_stock(finto):
    """Fase 1b (chiude review MEDIA-4 fase 1a): un ETF settoriale (override tema) e un
    single-stock GICS Technology nello STESSO bucket economico; il vecchio asse resta separato."""
    fetch = _fetch_factory([], {"SEMI.MI": {"sector": "Technology", "industry": "Semis"}})
    summary = {"positions": [{"ticker": "CHIPX.MI", "valore_mercato": 1000.0},
                             {"ticker": "SEMI.MI", "valore_mercato": 1000.0}],
               "stale_positions": None}
    out = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    ea = out["econ_axis"]
    by = {b["bucket"]: b for b in ea["by_bucket"]}
    assert by["Technology"]["weight_pct"] == pytest.approx(100.0)
    assert by["Technology"]["tickers"] == ["CHIPX.MI", "SEMI.MI"]
    assert ea["hhi"] == pytest.approx(1.0, abs=1e-4)
    assert len(out["by_sector"]) == 2       # asse tematico invariato: 2 bucket


def test_econ_axis_multi_settore_dichiarato_non_spalmato(finto):
    """Panieri paese/EM e holding: bucket a se' DICHIARATO (mai spalmati su un
    settore senza le holdings), ma COPERTI (multi-settore != n.d.)."""
    summary = {"positions": [{"ticker": "PAESE.MI", "valore_mercato": 1000.0},
                             {"ticker": "FONDO.L", "valore_mercato": 3000.0}],
               "stale_positions": None}
    out = ps.compute_sector_exposure(summary=summary, fetch=_fetch_factory([], {}))
    ea = out["econ_axis"]
    by = {b["bucket"]: b for b in ea["by_bucket"]}
    assert by[ps._MULTI_PREFIX + " (paniere paese/EM)"]["weight_pct"] == pytest.approx(25.0)
    assert by[ps._MULTI_PREFIX + " (holding finta)"]["weight_pct"] == pytest.approx(75.0)
    assert ea["multi_sector_weight_pct"] == pytest.approx(100.0)
    assert ea["coverage_pct"] == pytest.approx(100.0)


def test_econ_axis_override_senza_bucket_econ_dichiarato(finto, monkeypatch):
    """Un override tematico NUOVO senza bucket economico non deve rimescolare gli assi
    zitto: n.d. + nota dichiarata che dice DOVE aggiungerlo (nel negozio, non nel codice)."""
    finto["veicoli"]["FAKE.MI"] = {
        "tipo": "etf", "provenienza": "dichiarato",
        "settore_tema": "ETF Test (tema)", "bucket_economico": None}
    summary = {"positions": [{"ticker": "FAKE.MI", "valore_mercato": 1000.0}],
               "stale_positions": None}
    out = ps.compute_sector_exposure(summary=summary, fetch=_fetch_factory([], {}))
    by = {b["bucket"]: b for b in out["econ_axis"]["by_bucket"]}
    assert "n.d." in by
    joined = " ".join(out["notes"])
    assert "FAKE.MI" in joined and "bucket_economico" in joined and "negozio" in joined


def test_divergenza_sizing_dichiarata_solo_se_vera(finto):
    fetch = _fetch_factory([], {
        "BANCA.MI": {"sector": "Financial Services", "industry": "Banks"},   # compatibile
        "SEMI.MI": {"sector": "Consumer Cyclical", "industry": "x"},          # DIVERGE da semis
    })
    summary = {"positions": [{"ticker": "BANCA.MI", "valore_mercato": 1234.56},
                             {"ticker": "SEMI.MI", "valore_mercato": 1234.56}],
               "stale_positions": None}
    out = ps.compute_sector_exposure(summary=summary, fetch=fetch)
    joined = " ".join(out["notes"])
    assert "SEMI.MI" in joined and "divergenza" in joined.lower()
    assert "BANCA.MI" not in joined                           # il caso ovvio NON fa rumore


def test_compute_rilegge_il_negozio_una_volta_e_vede_la_modifica(monkeypatch):
    """Una modifica A->B deve cambiare tema e bucket senza riavviare il processo."""
    stato = {"tema": "Tema A", "bucket": "Technology"}
    letture = []

    def carica():
        letture.append(dict(stato))
        return {"veicoli": {"CAMBIO.MI": {
                    "tipo": "etf", "provenienza": "dichiarato",
                    "settore_tema": stato["tema"],
                    "bucket_economico": stato["bucket"],
                }},
                "origine": "sintetico.json", "motivo": None}

    monkeypatch.setattr(ps.cl, "carica_veicoli", carica)
    monkeypatch.setattr(se, "SECTOR_OF", {})
    summary = {"positions": [{"ticker": "CAMBIO.MI", "valore_mercato": 1000.0}]}
    mai_fetch = lambda _tk: (_ for _ in ()).throw(AssertionError("override ignorato"))

    primo = ps.compute_sector_exposure(summary=summary, fetch=mai_fetch)
    stato.update(tema="Tema B", bucket="Energy")
    secondo = ps.compute_sector_exposure(summary=summary, fetch=mai_fetch)

    assert primo["by_sector"][0]["sector"] == "Tema A"
    assert primo["econ_axis"]["by_bucket"][0]["bucket"] == "Technology"
    assert secondo["by_sector"][0]["sector"] == "Tema B"
    assert secondo["econ_axis"]["by_bucket"][0]["bucket"] == "Energy"
    assert len(letture) == 2, "una sola lettura coerente per ogni compute"
