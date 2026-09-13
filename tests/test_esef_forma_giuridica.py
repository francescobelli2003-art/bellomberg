# -*- coding: utf-8 -*-
"""resolve_lei trova l'emittente anche quando il repository usa la forma giuridica lunga.

Audit run 10/09 (memo #54): get_valuation su un titolo -> "storico n.d.
(SEC e ESEF): 'X AG': nessuna entita' sul repository ESEF". Misurato dal vivo l'11/09:
filter ilike '%X AG%' -> 0 entita'; '%X%' -> 'X Aktiengesellschaft' con LEI. La query
usava le prime tre parole del nome Yahoo; il confronto esatto (_norm_issuer) la forma
giuridica la toglieva GIA'. Serve la ricerca progressiva: nome pieno, poi nome senza
forma giuridica; la guardia di ambiguita' resta.

Nessuna rete: requests.get finto. Nomi e LEI INVENTATI.
"""
import json

import pytest

import bellomberg.storage.negozi_privati as np_
from bellomberg.market_data import esef


def _entita(nome, lei):
    return {"id": lei, "attributes": {"name": nome, "identifier": lei}}


class _Resp:
    def __init__(self, data, ok=True, status=200):
        self._data = data
        self.ok = ok
        self.status_code = status

    def json(self):
        return {"data": self._data}


def _repo(monkeypatch, tmp_path, tabella):
    """tabella: {pezzo di filtro ilike -> lista entita'}; registra le query fatte."""
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    p = tmp_path / "lei.json"
    p.write_text(json.dumps({"ACME.MI": "ESEMPIOLEI0000000010"}), encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_LEI", str(p))
    query = []

    def get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/entities")
        val = json.loads(params["filter"])[0]["val"]
        query.append(val)
        for k, v in tabella.items():
            if val.lower() == k.lower():
                return _Resp(v)
        return _Resp([])

    import requests
    monkeypatch.setattr(requests, "get", get)
    return query


def test_il_nome_con_forma_giuridica_breve_trova_la_forma_lunga(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {"%stahlwerk%": [_entita("Stahlwerk Aktiengesellschaft", "529900STAHLWERK00001")]})
    lei, nota = esef.resolve_lei("STW.DE", company_name="Stahlwerk AG")
    assert lei == "529900STAHLWERK00001", nota
    assert query == ["%Stahlwerk AG%", "%stahlwerk%"], query
    assert "per NOME" in nota and "Stahlwerk Aktiengesellschaft" in nota


def test_la_prima_query_che_risponde_basta(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {"%Stahlwerk AG%": [_entita("Stahlwerk AG", "529900STAHLWERK00001")]})
    lei, _ = esef.resolve_lei("STW.DE", company_name="Stahlwerk AG")
    assert lei == "529900STAHLWERK00001" and query == ["%Stahlwerk AG%"]


def test_ambiguita_dopo_la_normalizzazione_resta_un_errore_dichiarato(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {"%banca%": [_entita("Banca Alfa S.p.A.", "8156001111111111AAAA"),
                                              _entita("Banca Beta S.p.A.", "8156002222222222BBBB")]})
    lei, nota = esef.resolve_lei("BNC.MI", company_name="Banca S.p.A.")
    assert lei is None and "ambigue" in nota, nota


def test_zero_entita_su_tutte_le_forme_e_dichiarato(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {})
    lei, nota = esef.resolve_lei("XYZ.PA", company_name="Xyz Holding SA")
    assert lei is None and "nessuna entita'" in nota
    assert len(query) >= 2, "la ricerca deve provare anche il nome senza forma giuridica"


def test_il_negozio_privato_vince_e_non_interroga_la_rete(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {})
    lei, nota = esef.resolve_lei("ACME.MI", company_name="Acme S.p.A.")
    assert lei == "ESEMPIOLEI0000000010" and query == []
