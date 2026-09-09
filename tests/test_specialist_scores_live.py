"""Lettura live del negozio nello score fundamentals, su simboli sintetici."""
import bellomberg.storage.classificazione as cl
import bellomberg.agents.specialist_scores as ss


def _negozio(dat):
    return {
        "veicoli": {
            ticker: {"tipo": "dat", "classe_size": "veicolo"}
            for ticker in dat
        },
        "origine": "sintetico.json",
        "motivo": None,
    }


def _ticker_valutato(score):
    dettagli = [r[0].strip() for r in score["lines"] if r[0].startswith("  ")]
    assert len(dettagli) == 1
    return dettagli[0]


def test_fundamentals_rilegge_dat_una_volta_e_vede_la_modifica(monkeypatch):
    stato = {"dat": {"ALFA"}}
    letture = []

    def carica():
        letture.append(set(stato["dat"]))
        return _negozio(stato["dat"])

    monkeypatch.setattr(cl, "carica_veicoli", carica)
    portfolio = {"positions": [
        {"ticker": "ALFA", "peso_pct": 60},
        {"ticker": "BETA", "peso_pct": 40},
    ]}
    valuations = {
        "ALFA": {"fair_value": 12.0, "price": 10.0},
        "BETA": {"fair_value": 12.0, "price": 10.0},
    }

    primo = ss.fundamentals_score(portfolio, valuations=valuations, max_names=1)
    stato["dat"] = {"BETA"}
    secondo = ss.fundamentals_score(portfolio, valuations=valuations, max_names=1)

    assert _ticker_valutato(primo) == "BETA"
    assert _ticker_valutato(secondo) == "ALFA"
    assert letture == [{"ALFA"}, {"BETA"}], "una lettura per score, non per ticker"
