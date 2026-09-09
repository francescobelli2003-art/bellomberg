"""Test OFFLINE lotto (a) 25/07 sera: log a prova di pipe morta (autopsia (40)).

La classe di guasto: backend zombie -> stdout/stderr orfani -> print lancia
OSError -> se succede DENTRO un except (print_exc) la seconda eccezione scappa
dal handler e il client vede un 500 muto. Qui si simula la pipe morta e si
verifica che: (1) i _log blindati non propagano MAI; (2) l'endpoint /news/macro
risponde normale anche con la pipe morta; (3) un errore del fetch resta la
HTTPException dichiarata (con str(e) nel detail), mai un OSError di log.

Zero rete: fetch stubbato, stato limiter su file tmp, print/stderr finti.
"""
import builtins
import json

import pytest

import bellomberg.market_data.news_aggregator as na
from bellomberg.cli import briefing_engine
from bellomberg.agents import chat_engine
from bellomberg.market_data import finnhub_news
from bellomberg.market_data import sec_edgar
import bellomberg.api.bellomberg_api as api


def _pipe_morta(*args, **kwargs):
    raise OSError(22, "Invalid argument")  # il WinError 232/22 della pipe chiusa


class _StderrMorto:
    def write(self, *a, **k):
        raise OSError(22, "Invalid argument")

    def flush(self):
        raise OSError(22, "Invalid argument")


@pytest.fixture
def stato_pulito(tmp_path, monkeypatch):
    p = tmp_path / "news_rate_state.json"
    p.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(na, "_NEWS_RATE_PATH", str(p))


def test_log_blindati_non_propagano(monkeypatch):
    monkeypatch.setattr(builtins, "print", _pipe_morta)
    na._log("x")
    briefing_engine._log("x")
    chat_engine._log("x")
    finnhub_news._log("x")
    # 22/08 (voce E): `sec_edgar._log` era una print nuda e il ramo «non e' un
    # filer» di lookup_cik ha smesso di essere silenzioso — con la pipe morta
    # l'OSError finiva nell'except, che richiamava _perche e la faceva USCIRE
    # dalla funzione invece di degradare.
    sec_edgar._log("x")
    api._safe_print("x")


def test_safe_trace_non_propaga(monkeypatch):
    # print_exc scrive su sys.stderr: e' il punto che rilanciava dentro l'except
    monkeypatch.setattr("sys.stderr", _StderrMorto())
    try:
        raise ValueError("boom")
    except ValueError:
        api._safe_trace()  # non deve propagare nulla


def test_macro_risponde_con_pipe_morta(stato_pulito, monkeypatch):
    monkeypatch.setattr(builtins, "print", _pipe_morta)
    monkeypatch.setattr("sys.stderr", _StderrMorto())
    # fetch che LOGGA (come il giro RSS vero con Barron's morto) e poi risponde
    def _fetch_che_logga(**kw):
        na._log("RSS finto failed")
        return [{"title": "t", "url": "u"}]
    monkeypatch.setattr(na, "fetch_macro_news", _fetch_che_logga)
    out = api.get_news_macro()
    assert out["count"] == 1 and "fonti_mute" in out


def test_macro_errore_fetch_resta_dichiarato(stato_pulito, monkeypatch):
    # Errore vero del fetch + pipe morta: deve uscire la HTTPException col
    # detail vero (il 500 JSON dichiarato), NON l'OSError del print_exc.
    from fastapi import HTTPException
    monkeypatch.setattr(builtins, "print", _pipe_morta)
    monkeypatch.setattr("sys.stderr", _StderrMorto())
    def _fetch_rotto(**kw):
        raise ValueError("provider esploso")
    monkeypatch.setattr(na, "fetch_macro_news", _fetch_rotto)
    with pytest.raises(HTTPException) as exc:
        api.get_news_macro()
    assert exc.value.status_code == 500
    assert "provider esploso" in str(exc.value.detail)
