import sys
import types
import pytest

from bellomberg.valuation import dcf_calibration


CW = {
    "rf": 0.03,
    "erp": 0.05,
    "crp": 0.01,
    "rf_source": "rf sintetico",
    "erp_source": "erp sintetico",
    "crp_source": "crp sintetico",
}
PRIOR = {"beta_u": 1.1}


def _damodaran_con_tax(monkeypatch, risposta):
    modulo = types.ModuleType("damodaran_data")
    modulo.get_tax_marginal = lambda _country: risposta
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.damodaran_data', modulo)


@pytest.mark.parametrize("guasto", [False, True])
def test_wacc_dichiara_il_default_fiscale_quando_il_dato_marginale_manca(monkeypatch, guasto):
    _damodaran_con_tax(monkeypatch, None)
    if guasto:
        def _errore(_country):
            raise RuntimeError("fonte fiscale indisponibile")
        monkeypatch.setattr(sys.modules['bellomberg.market_data.damodaran_data'], "get_tax_marginal", _errore)

    risultato = dcf_calibration._wacc_inputs_v1(CW, PRIOR, {}, "Atlantide")

    assert risultato["tax"] == 0.25
    assert risultato["sources"]["tax"] == (
        "tax 25% DEFAULT (paese 'Atlantide' non nel dataset marginali): dichiarato"
    )


def test_wacc_preserva_il_dato_fiscale_disponibile(monkeypatch):
    _damodaran_con_tax(
        monkeypatch,
        {"value": 0.31, "source": "tax marginale sintetica [src: test]"},
    )

    risultato = dcf_calibration._wacc_inputs_v1(CW, PRIOR, {}, "Italia")

    assert risultato["tax"] == 0.31
    assert risultato["sources"]["tax"] == "tax marginale sintetica [src: test]"
