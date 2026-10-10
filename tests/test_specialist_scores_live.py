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
    assert score['metrics']['n_valued']==1
    # 09/10: la riga per nome e' "  TICKER (peso X%)"
    dettagli = [str(r[0]).split()[0] for r in score["lines"]
                if str(r[0]).split() and str(r[0]).split()[0] in {'ALFA','BETA'}]
    assert len(dettagli) == 1
    return dettagli[0]


def test_fundamentals_rilegge_dat_una_volta_e_vede_la_modifica(monkeypatch,tmp_path):
    from datetime import date
    import test_sector_usability as fixtures
    monkeypatch.setattr(fixtures, "DAY", date.today().isoformat())
    # 09/10 (Opus 5.5): MOS sul prezzo corrente; qui l'oggetto non e' la quotazione
    from test_score_fondamentali_news_correzione import prezzo_corrente_uguale_al_modello
    prezzo_corrente_uguale_al_modello(monkeypatch)
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
    from test_ripieghi_dat_dichiarati import _documented_values
    valuations = _documented_values(tmp_path,('ALFA','BETA'))

    primo = ss.fundamentals_score(portfolio, valuations=valuations, max_names=1)
    stato["dat"] = {"BETA"}
    secondo = ss.fundamentals_score(portfolio, valuations=valuations, max_names=1)

    assert _ticker_valutato(primo) == "BETA"
    assert _ticker_valutato(secondo) == "ALFA"
    assert letture == [{"ALFA"}, {"BETA"}], "una lettura per score, non per ticker"
