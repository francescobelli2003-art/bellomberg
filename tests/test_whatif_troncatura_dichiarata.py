"""F43 (2) — nel what-if di F5 una vendita PIU' GRANDE della posizione veniva
applicata come chiusura senza che nessun campo lo dicesse: `modifications_applied`
registrava l'importo CHIESTO, e con `amount_pct > 100` dichiarava perfino piu' EUR
tolti di quanti la posizione ne valesse (27/08, Fable 5, chat backend).

Contratto ADDITIVO su ogni voce `remove`/`trim` di `modifications_applied`:
  amount_eur_effettivo   quanto il what-if ha tolto davvero (<= posizione)
  truncated              True se il chiesto superava la posizione (o il 100%)
  note                   il motivo, solo quando truncated
  amount_pct_chiesto     (trim) la percentuale chiesta; `amount_pct` resta il tetto
Le chiavi di prima (`amount_eur`, `amount_pct`, `amount_eur_resolved`) NON cambiano
significato: `amount_eur` resta il chiesto.

La prova passa per `run_monte_carlo_v3` VERA col motore VERO (offline: DB e
`_download_returns` stubbati, nessuna rete; `add` non e' sotto prova perche'
interroga yfinance). Sola lettura: il DB e' finto.

LINGUA (13/09, Claude Opus 5): dal lotto i18n C5 del 12/09 `note` e `reason` sono frasi
bilingui di `core.presentation.message`, rese nella lingua catturata alla chiamata. Senza
contesto quella lingua la decide `data/preferences.json` di chi lancia la suite (file assente
= «it»): tre asserzioni in inglese (cinque casi) erano rosse per questo, non per il motore.
Ogni chiamata qui fissa la lingua col `language_context`, e le frasi attese stanno in questo
file: non si importano dal modulo sotto prova.
"""
import numpy as np
import pandas as pd
import pytest

import bellomberg.portfolio.portfolio_montecarlo as pm
from bellomberg.core.language import language_context

# tre posizioni: il motore rifiuta un portafoglio con UN solo titolo («only 1
# ticker(s) with returns»), e qui AAA viene chiusa per intero in meta' dei casi
POSIZIONI = ({"ticker": "AAA", "valore_mercato": 10000.0},
             {"ticker": "BBB", "valore_mercato": 30000.0},
             {"ticker": "CCC", "valore_mercato": 20000.0})

# le frasi che il PM legge, congelate qui nelle due lingue (l'oracolo non viene dal modulo)
NOTA_CHIUSURA_SENZA_IMPORTO = {"it": "posizione chiusa per intero (nessun importo specificato)",
                               "en": "full position closed (no amount specified)"}
MOTIVO_IMPORTO_NON_POSITIVO = {
    "it": "REMOVE richiede amount_eur/amount_pct positivo (ometti entrambi = chiusura totale)",
    "en": "REMOVE requires positive amount_eur/amount_pct (omit both = full close)"}


class _DB:
    def get_portfolio_summary(self):
        return {"positions": [dict(p) for p in POSIZIONI],
                "totale_valore_mercato_eur": 60000.0}


@pytest.fixture
def v3(monkeypatch):
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2022-01-03", periods=400)
    rdf = pd.DataFrame({"AAA": rng.normal(0, 0.01, 400),
                        "BBB": rng.normal(0, 0.015, 400),
                        "CCC": rng.normal(0, 0.012, 400)}, index=idx)
    monkeypatch.setattr(pm, "MemoryDB", _DB)
    monkeypatch.setattr(pm, "_download_returns", lambda *a, **k: rdf)
    pm.invalidate_cache()

    def corri(mods, lingua="it", **kw):
        kw.setdefault("sample_paths_n", 1)
        with language_context(lingua):
            return pm.run_monte_carlo_v3(horizon_days=20, n_sims=300, method="block_bootstrap",
                                         modifications=mods, **kw)
    yield corri
    pm.invalidate_cache()


def _applicata(out, ticker="AAA"):
    assert "error" not in out, out.get("error")
    voci = [m for m in out["modifications_applied"] if m["ticker"] == ticker]
    assert len(voci) == 1, out["modifications_applied"]
    assert out["skipped_modifications"] == []
    # 13/09: le chiavi del contratto si pretendono con un'ASSERZIONE, non con un KeyError: il
    # banco di mutazioni conta solo le cadute di assert, e una chiave tolta e' il difetto vero
    voce = voci[0]
    if voce["action"] in ("remove", "trim"):
        assert {"amount_eur_effettivo", "truncated"} <= set(voce), sorted(voce)
        assert not voce["truncated"] or voce.get("note"), "troncata senza nota: %r" % voce
    return voce


# ---------------------------------------------------------------- remove, EUR

def test_vendita_piu_grande_della_posizione_e_applicata_come_chiusura_e_lo_dice(v3):
    out = v3([{"action": "remove", "ticker": "AAA", "amount_eur": 25000}])
    m = _applicata(out)
    assert m["amount_eur"] == 25000.0              # il CHIESTO resta (contratto di prima)
    assert m["amount_eur_effettivo"] == 10000.0    # quanto e' stato tolto davvero
    assert m["truncated"] is True
    assert "25000.00" in m["note"] and "10000.00" in m["note"] and "15000.00" in m["note"]
    assert "AAA" not in out["weights_post"]
    assert out["nav_post_eur"] == 50000.0


def test_vendita_entro_la_posizione_non_e_troncata(v3):
    out = v3([{"action": "remove", "ticker": "AAA", "amount_eur": 4000}])
    m = _applicata(out)
    assert m["amount_eur"] == 4000.0
    assert m["amount_eur_effettivo"] == 4000.0
    assert m["truncated"] is False
    assert m.get("note") is None
    assert out["weights_post"]["AAA"] == round(6000.0 / 56000.0, 6)


def test_vendita_esatta_e_una_chiusura_non_una_troncatura(v3):
    out = v3([{"action": "remove", "ticker": "AAA", "amount_eur": 10000}])
    m = _applicata(out)
    assert m["amount_eur_effettivo"] == 10000.0
    assert m["truncated"] is False
    assert "AAA" not in out["weights_post"]


@pytest.mark.parametrize("lingua", ["it", "en"])
def test_chiusura_senza_importo_resta_dichiarata_come_prima(v3, lingua):
    out = v3([{"action": "remove", "ticker": "AAA"}], lingua=lingua)
    m = _applicata(out)
    assert m["amount_eur"] == 10000.0
    assert m["amount_eur_effettivo"] == 10000.0
    assert m["truncated"] is False
    assert m["note"] == NOTA_CHIUSURA_SENZA_IMPORTO[lingua]


# ------------------------------------------------------------ remove, percento

def test_percentuale_oltre_100_dichiara_la_troncatura_e_non_inventa_euro(v3):
    out = v3([{"action": "remove", "ticker": "AAA", "amount_pct": 150}])
    m = _applicata(out)
    assert m["amount_pct"] == 150.0                 # il chiesto
    assert m["amount_eur_resolved"] == 10000.0      # prima diceva 15000: piu' della posizione
    assert m["amount_eur_effettivo"] == 10000.0
    assert m["truncated"] is True
    assert "150" in m["note"] and "100" in m["note"]
    assert "AAA" not in out["weights_post"]


def test_percentuale_entro_100_non_e_troncata(v3):
    out = v3([{"action": "remove", "ticker": "AAA", "amount_pct": 25}])
    m = _applicata(out)
    assert m["amount_eur_resolved"] == 2500.0
    assert m["amount_eur_effettivo"] == 2500.0
    assert m["truncated"] is False
    assert m.get("note") is None
    assert out["weights_post"]["AAA"] == round(7500.0 / 57500.0, 6)


def test_la_nota_non_arrotonda_la_percentuale_a_intero(v3):
    """Review 27/08: con `:.0f` «chiesto il 100% ma la posizione e' il 100%»."""
    out = v3([{"action": "remove", "ticker": "AAA", "amount_pct": 100.4}])
    m = _applicata(out)
    assert m["truncated"] is True
    assert "100.4%" in m["note"]


# ----------------------------------------------- remove, i casi della review

@pytest.mark.parametrize("lingua", ["it", "en"])
@pytest.mark.parametrize("mod", [
    {"action": "remove", "ticker": "AAA", "amount_eur": 0},
    {"action": "remove", "ticker": "AAA", "amount_eur": -5000},
    {"action": "remove", "ticker": "AAA", "amount_pct": -30},
])
def test_importo_non_positivo_viene_saltato_con_motivo_non_chiuso_zitto(v3, mod, lingua):
    """Prima finiva nel ramo «full position closed (no amount specified)»: la posizione
    spariva e la nota diceva che nessun importo era stato dato."""
    out = v3([mod], lingua=lingua)
    assert out["modifications_applied"] == []
    assert len(out["skipped_modifications"]) == 1
    assert out["skipped_modifications"][0]["reason"] == MOTIVO_IMPORTO_NON_POSITIVO[lingua]
    assert out["weights_post"]["AAA"] == round(10000.0 / 60000.0, 6)


def test_rumore_sotto_il_centesimo_non_e_una_troncatura(monkeypatch, v3):
    """`valore_mercato` in produzione e' quantita*prezzo*fx: 9999.996 nel DB, 10000.00
    a schermo. Chiedere 10000 non e' una troncatura «di 0.00 EUR»."""
    class _DBRumore:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "AAA", "valore_mercato": 9999.996},
                                  {"ticker": "BBB", "valore_mercato": 30000.0},
                                  {"ticker": "CCC", "valore_mercato": 20000.0}],
                    "totale_valore_mercato_eur": 59999.996}
    monkeypatch.setattr(pm, "MemoryDB", _DBRumore)
    out = v3([{"action": "remove", "ticker": "AAA", "amount_eur": 10000}])
    m = _applicata(out)
    assert m["truncated"] is False
    assert m.get("note") is None
    assert m["amount_eur_effettivo"] == 10000.0
    assert "AAA" not in out["weights_post"]


def test_eur_e_percento_insieme_vince_l_eur_e_la_percentuale_non_sparisce(v3):
    out = v3([{"action": "remove", "ticker": "AAA", "amount_eur": 4000, "amount_pct": 50}])
    m = _applicata(out)
    assert m["amount_eur_effettivo"] == 4000.0
    assert m.get("amount_pct_ignorata") == 50.0
    assert "amount_pct" not in m


# ----------------------------------------------------------------------- trim

def test_trim_oltre_100_dichiara_il_tetto_e_la_percentuale_chiesta(v3):
    out = v3([{"action": "trim", "ticker": "AAA", "amount_pct": 130}])
    m = _applicata(out)
    assert m["amount_pct"] == 100.0                 # il tetto, com'era
    assert m["amount_pct_chiesto"] == 130.0
    assert m["amount_eur_effettivo"] == 10000.0
    assert m["truncated"] is True
    assert "130" in m["note"] and "100" in m["note"]
    assert "AAA" not in out["weights_post"]


def test_trim_entro_100_non_e_troncato(v3):
    out = v3([{"action": "trim", "ticker": "AAA", "amount_pct": 50}])
    m = _applicata(out)
    assert m["amount_pct"] == 50.0
    assert m["amount_pct_chiesto"] == 50.0
    assert m["amount_eur_effettivo"] == 5000.0
    assert m["truncated"] is False
    assert m.get("note") is None
    assert out["weights_post"]["AAA"] == round(5000.0 / 55000.0, 6)


# ---------------------------------------------------------------------- cache

@pytest.mark.parametrize("lingua", ["it", "en"])
def test_la_cache_non_presta_la_dichiarazione_di_un_altro_chiamante(v3, lingua):
    """«vendi 25k di AAA» e «chiudi AAA» lasciano lo STESSO portafoglio post, quindi
    la stessa chiave di cache: la simulazione e' la stessa (giusto), la dichiarazione
    deve essere di chi chiama."""
    primo = v3([{"action": "remove", "ticker": "AAA", "amount_eur": 25000}], lingua=lingua)
    secondo = v3([{"action": "remove", "ticker": "AAA"}], lingua=lingua)
    assert _applicata(primo)["truncated"] is True
    m2 = _applicata(secondo)
    assert m2["truncated"] is False
    assert m2["amount_eur"] == 10000.0
    assert m2["note"] == NOTA_CHIUSURA_SENZA_IMPORTO[lingua]
    # la cache ha servito davvero (senza seed due simulazioni distinte non coincidono)
    assert primo["es_99_pct"] == secondo["es_99_pct"]


def test_la_dichiarazione_del_chiamante_non_sporca_la_voce_in_cache(v3):
    """Col campione intero `_vista_paths` restituisce l'entry stessa: la dichiarazione
    del secondo chiamante deve finire su una copia, non sull'entry."""
    v3([{"action": "remove", "ticker": "AAA", "amount_eur": 25000}])
    v3([{"action": "remove", "ticker": "AAA"}], sample_paths_n=pm.SAMPLE_PATHS_POOL)
    entry = [e for k, e in pm._CACHE.items() if k.startswith("mc:v3:")]
    assert len(entry) == 1, list(pm._CACHE.keys())
    in_cache = entry[0]["data"]["modifications_applied"][0]
    assert in_cache.get("truncated") is True and in_cache["amount_eur"] == 25000.0


# ------------------------------------------------------------------ cablaggio

def test_l_endpoint_v3_consegna_il_dict_del_motore_senza_spogliarlo(monkeypatch):
    from bellomberg.api import bellomberg_api
    catturato = {}
    atteso = {"modifications_applied": [{"action": "remove", "ticker": "AAA",
                                         "amount_eur": 25000.0, "amount_eur_effettivo": 10000.0,
                                         "truncated": True, "note": "n"}],
              "skipped_modifications": [], "version": "v3"}

    def _finto(**kw):
        catturato.update(kw)
        return atteso
    monkeypatch.setattr(pm, "run_monte_carlo_v3", _finto)

    out = bellomberg_api.post_portfolio_montecarlo_v3(
        {"modifications": [{"action": "remove", "ticker": "AAA", "amount_eur": 25000}]})
    assert out is atteso
    assert catturato["modifications"] == [{"action": "remove", "ticker": "AAA", "amount_eur": 25000}]
