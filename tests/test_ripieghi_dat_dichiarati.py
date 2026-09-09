# -*- coding: utf-8 -*-
"""Lotto 5b del criterio (1), aggiornato alla fonte live del 09/09.

- Contratto superato: `specialist_scores` non importa piu' lo snapshot
  `dcf_engine.DAT_TICKERS`; classifica DAT e veicoli dal negozio live una volta per score.
- Contratto corrente: negozio assente/illeggibile = nessuna esclusione e buco dichiarato;
  negozio leggibile = la modifica si vede senza riavvio.
- `scripts/pulizia_tesi_invalide.py`: l'elenco delle DAT decide QUALI tesi CANCELLARE; senza
  `dcf_engine` lo script si FERMA prima di aprire il DB, dichiarando il motivo (un insieme
  vuoto zitto avrebbe lasciato in piedi tesi invalide, un elenco cablato ne avrebbe cancellate
  su un dato non piu' vero).
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))


def _senza_dcf_engine(monkeypatch):
    """`from dcf_engine import DAT_TICKERS` deve fallire come su un clone senza il motore."""
    monkeypatch.setitem(sys.modules, 'bellomberg.valuation.dcf_engine', None)


def test_fundamentals_score_senza_negozio_non_esclude_nessuno_e_lo_dichiara(monkeypatch):
    import bellomberg.agents.specialist_scores as ss
    monkeypatch.setattr(ss.cl, "carica_veicoli", lambda: {
        "veicoli": {}, "origine": "assente", "motivo": "file sintetico assente"})
    pdj = {"positions": [{"ticker": "ALFA", "peso_pct": 40}, {"ticker": "BETA.DE", "peso_pct": 30}]}
    val = {"ALFA": {"fair_value": 12.0, "price": 10.0}, "BETA.DE": {"fair_value": 9.0, "price": 10.0}}
    out = ss.fundamentals_score(pdj, valuations=val, max_names=4)
    assert out, "senza motore il punteggio deve comunque uscire coi nomi valutabili"
    etichette = [str(r[0]) + " " + str(r[1]) for r in out["lines"]]
    assert any("Negozio" in e and "non disponibil" in e.lower() for e in etichette), (
        "il buco (veicoli/DAT non escludibili) non e' dichiarato nelle righe: %r" % (etichette,))
    assert "DAT" in out["verdict"] and "negozio" in out["verdict"].lower(), out["verdict"]
    assert "2/2 nomi valutati" in out["verdict"], "senza elenco nessuno viene escluso: %r" % (out["verdict"],)


def test_fundamentals_score_col_negozio_esclude_la_dat_senza_note(monkeypatch):
    """La dichiarazione compare SOLO quando il buco c'e' (frase di stato al presente)."""
    import bellomberg.agents.specialist_scores as ss
    monkeypatch.setattr(ss.cl, "carica_veicoli", lambda: {
        "veicoli": {"GAMMA": {"tipo": "dat", "classe_size": "veicolo"}},
        "origine": "sintetico.json", "motivo": None})
    pdj = {"positions": [{"ticker": "ALFA", "peso_pct": 40}, {"ticker": "GAMMA", "peso_pct": 30}]}
    val = {"ALFA": {"fair_value": 12.0, "price": 10.0}, "GAMMA": {"fair_value": 9.0, "price": 10.0}}
    out = ss.fundamentals_score(pdj, valuations=val, max_names=4)
    assert out and "1/1 nomi valutati" in out["verdict"], out and out["verdict"]
    assert not any("Negozio" in str(r[0]) for r in out["lines"]), out["lines"]


def test_pulizia_tesi_senza_dcf_engine_si_ferma_prima_del_db(monkeypatch, tmp_path, capsys):
    pulizia = pytest.importorskip("pulizia_tesi_invalide")
    _senza_dcf_engine(monkeypatch)
    monkeypatch.setattr(pulizia, "DB", str(tmp_path / "non_esiste.db"))
    monkeypatch.setattr(pulizia, "APPLY", False)
    pulizia.main()          # se arrivasse al DB: OperationalError sul file inesistente
    out = capsys.readouterr().out
    assert "ABORT" in out and "dcf_engine" in out, out
    assert not (tmp_path / "non_esiste.db").exists()
