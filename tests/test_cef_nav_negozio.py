"""cef_nav legge il negozio dei veicoli: il registro dei fetcher e' per FONTE, la copertura per
simbolo viene dalle voci `tipo=cef` con `nav_fonte` riconosciuta (05/09, Fable 5.1, lotto 2b).

Prima: CEF_SOURCES era un dict cablato col fondo del PM e il messaggio d'errore ELENCAVA i fondi
coperti (il book nel testo che il modello legge). Ora: `FETCH_NAV = {dominio: fetcher}`,
`CEF_SOURCES` derivato dal negozio (stessa forma di ieri: fetch, nav_currency, name),
`CEF_SENZA_FONTE` = i cef dichiarati senza una fonte riconosciuta, col motivo; il messaggio porta
un conteggio e un'istruzione, mai un elenco. Zero rete: fetcher e yfinance sono finti, il negozio
e' un file in tmp_path, nessun simbolo del book.
"""
import json
import re
import sys
import types

import pytest

from bellomberg.valuation import cef_nav
import bellomberg.storage.classificazione as cl

# la chiave del fetcher VERO (un dominio, non un simbolo): i test la leggono dal registro
CHIAVE = next(iter(cef_nav.FETCH_NAV))

VOCI = {
    "FONDO.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": CHIAVE, "nav_valuta": "USD",
                "nome": "Fondo Chiuso Finto", "classe_size": "veicolo"},
    "FONDO2.L": {"tipo": "cef", "provenienza": "dichiarato"},                       # senza fonte
    "FONDO3.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": "sito-finto.example",
                 "nav_valuta": "USD"},                                                # fonte ignota
    "FONDO4.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": CHIAVE},   # senza valuta
    "TESORO": {"tipo": "dat", "provenienza": "dichiarato", "sottostante": "BTC-USD", "nav_fonte": CHIAVE,
               "nav_valuta": "USD"},                                                  # dat: non e' un cef
    "FONDO5.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": CHIAVE, "nav_valuta": "EUR",
                 "nome": "Fondo In Euro"},                                             # valuta != fetcher
    "ACME": {"tipo": "operating", "provenienza": "dichiarato"},
}


def _neg(tmp_path, voci=VOCI, nome="v.json"):
    p = tmp_path / nome
    p.write_text(json.dumps(voci), encoding="utf-8")
    return cl.carica_veicoli(str(p))


QUOTE = {"regularMarketPrice": 80.0, "currency": "USD"}   # la quotazione finta (mutabile dai test)


def _fetch_finto():
    """Il fetcher del registro, finto: NAV 100 USD (review 05/09: senza questo stub, sotto una
    mutazione i test chiamavano il sito VERO e la mutazione cadeva per rete, non per asserzione)."""
    return {"nav_per_share_usd": 100.0, "nav_as_of": None, "nav_currency": "USD",
            "nav_source": "sito-finto.example (NAV finto)"}


@pytest.fixture(autouse=True)
def _senza_cache_e_senza_rete(monkeypatch):
    monkeypatch.setattr(cef_nav, "_CACHE", {})
    for k in list(cef_nav.FETCH_NAV):
        monkeypatch.setitem(cef_nav.FETCH_NAV, k, _fetch_finto)
    QUOTE.update({"regularMarketPrice": 80.0, "currency": "USD"})
    yf = types.ModuleType("yfinance")

    class _T:
        def __init__(self, t):
            self.info = dict(QUOTE)
    yf.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", yf)


def test_il_registro_e_per_fonte_non_per_simbolo():
    assert cef_nav.FETCH_NAV, "il registro dei fetcher non puo' essere vuoto"
    for chiave, fn in cef_nav.FETCH_NAV.items():
        # un dominio (dominio.tld, minuscolo), non un ticker: `psh.l` non passa (TLD di 2+ lettere)
        assert re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", chiave), chiave
        assert callable(fn)


def test_cef_sources_derivato_dal_negozio(tmp_path):
    n = _neg(tmp_path)
    s = cef_nav.cef_sources_del_negozio(n)
    assert set(s) == {"FONDO.L"}
    assert s["FONDO.L"]["fetch"] is cef_nav.FETCH_NAV[CHIAVE]
    assert s["FONDO.L"]["nav_currency"] == "USD" and s["FONDO.L"]["name"] == "Fondo Chiuso Finto"
    assert s["FONDO.L"]["nav_fonte"] == CHIAVE


def test_cef_senza_fonte_dichiarati_con_motivo(tmp_path):
    n = _neg(tmp_path)
    sf = cef_nav.cef_senza_fonte_del_negozio(n)
    assert set(sf) == {"FONDO2.L", "FONDO3.L", "FONDO4.L", "FONDO5.L"}
    assert "nav_fonte" in sf["FONDO2.L"]
    assert "EUR" in sf["FONDO5.L"] and "USD" in sf["FONDO5.L"]      # la fonte pubblica in USD
    assert "sito-finto.example" in sf["FONDO3.L"] and "FETCH_NAV" in sf["FONDO3.L"]
    assert "nav_valuta" in sf["FONDO4.L"]


def test_le_viste_all_import_sono_derivate_dal_negozio_corrente():
    corrente = cl.carica_veicoli()
    if corrente["origine"] in ("assente", "illeggibile"):
        pytest.skip("negozio dei veicoli %s: la misura sarebbe vacua ({} == {})" % corrente["origine"])
    def _senza_fetch(d):     # il fixture autouse sostituisce i fetcher: si confronta il resto
        return {t: {k: v for k, v in voce.items() if k != "fetch"} for t, voce in d.items()}
    assert _senza_fetch(cef_nav.CEF_SOURCES) == _senza_fetch(cef_nav.cef_sources_del_negozio(corrente))
    assert cef_nav.CEF_SENZA_FONTE == cef_nav.cef_senza_fonte_del_negozio(corrente)


def test_get_cef_nav_usa_il_fetcher_del_registro_e_dichiara_la_fonte(tmp_path, monkeypatch):
    n = _neg(tmp_path)
    chiamate = []

    def _fetch_contato():
        chiamate.append(1)
        return _fetch_finto()
    monkeypatch.setitem(cef_nav.FETCH_NAV, CHIAVE, _fetch_contato)
    out = cef_nav.get_cef_nav("FONDO.L", negozio=n)
    assert "error" not in out, out
    assert chiamate == [1]
    assert out["name"] == "Fondo Chiuso Finto" and out["nav_fonte"] == CHIAVE
    assert out["nav_per_share_usd"] == 100.0
    assert out["discount_to_nav_pct"] == pytest.approx(-20.0)   # 80 / 100 - 1


def test_quotazione_in_gbp_convertita_col_tasso_dichiarato(tmp_path, monkeypatch):
    """Il ramo che la produzione prende su un fondo quotato a Londra (review 05/09: nessun
    test lo copriva; `/100` sparito = tutto verde). 4200 GBp -> 42 GBP -> 56,28 USD a 1,34;
    sconto = 56,28/100 - 1 = -43,72%."""
    n = _neg(tmp_path)
    QUOTE.update({"regularMarketPrice": 4200.0, "currency": "GBp"})
    monkeypatch.setattr(cef_nav, "_fx_rate", lambda yf, pair: 1.34 if pair == "GBPUSD=X" else None)
    out = cef_nav.get_cef_nav("FONDO.L", negozio=n)
    assert "error" not in out, out
    assert out["market_price_currency"] == "GBp"
    assert out["market_price_in_nav_ccy"] == pytest.approx(56.28)
    assert out["discount_to_nav_pct"] == pytest.approx(-43.7, abs=0.05)
    assert "GBp/100" in out["fx_conversion"] and "1.3400" in out["fx_conversion"]
    # FX giu': lo sconto e' n.d. DICHIARATO, mai calcolato su pence
    cef_nav._CACHE.clear()
    monkeypatch.setattr(cef_nav, "_fx_rate", lambda yf, pair: None)
    out2 = cef_nav.get_cef_nav("FONDO.L", negozio=n)
    assert "discount_to_nav_pct" not in out2 and "FX" in out2["discount"]


def test_valuta_del_negozio_diversa_dalla_fonte_e_rifiutata(tmp_path):
    """Review 05/09: `nav_valuta` e' testo libero e il fetcher lavora in USD: con EUR nel
    negozio lo sconto mescolava le valute zitto e lo spec affermava una conversione mai fatta."""
    n = _neg(tmp_path)
    out = cef_nav.get_cef_nav("FONDO5.L", negozio=n)
    assert "error" in out, out
    assert "EUR" in out["error"] and "USD" in out["error"] and "nav_valuta" in out["error"]
    assert "discount_to_nav_pct" not in out


def test_fetcher_che_solleva_diventa_errore_dichiarato(tmp_path, monkeypatch):
    n = _neg(tmp_path)

    def _rotto():
        raise ConnectionError("sito irraggiungibile")
    monkeypatch.setitem(cef_nav.FETCH_NAV, CHIAVE, _rotto)
    out = cef_nav.get_cef_nav("FONDO.L", negozio=n)
    assert "error" in out and "ConnectionError" in out["error"] and CHIAVE in out["error"]


def test_la_cache_non_serve_un_payload_di_una_voce_cambiata(tmp_path):
    """Review 05/09: cache per solo ticker = dopo un edit del negozio (nome, fonte, valuta) il
    tool serviva il payload vecchio per un'ora pur rileggendo il negozio a ogni chiamata."""
    n = _neg(tmp_path)
    out = cef_nav.get_cef_nav("FONDO.L", negozio=n)
    assert out["name"] == "Fondo Chiuso Finto"
    voci = dict(VOCI)
    voci["FONDO.L"] = dict(VOCI["FONDO.L"], nome="Fondo Rinominato")
    n2 = _neg(tmp_path, voci, "v2.json")
    out2 = cef_nav.get_cef_nav("FONDO.L", negozio=n2)
    assert out2["name"] == "Fondo Rinominato"
    # stessa voce, seconda chiamata: dalla cache (il fetcher non riparte)
    out3 = cef_nav.get_cef_nav("FONDO.L", negozio=n2)
    assert out3 is out2


def test_cef_senza_fonte_riceve_il_suo_errore_senza_altri_simboli(tmp_path):
    n = _neg(tmp_path)
    for t in ("FONDO2.L", "FONDO3.L", "FONDO4.L", "FONDO5.L"):
        out = cef_nav.get_cef_nav(t, negozio=n)
        assert "error" in out and t in out["error"], out
        assert "senza fonte NAV" in out["error"] and "nav_fonte" in out["error"]
        for altro in set(VOCI) - {t}:
            assert altro not in out["error"], (t, altro)
    assert "sito-finto.example" in cef_nav.get_cef_nav("FONDO3.L", negozio=n)["error"]


def test_non_configurato_conta_e_non_elenca(tmp_path):
    n = _neg(tmp_path)
    for t in ("ACME", "TESORO", "ZZZQ.XX"):
        out = cef_nav.get_cef_nav(t, negozio=n)
        assert "error" in out and t in out["error"], out
        assert "1 fond" in out["error"], out                       # il conteggio, non l'elenco
        assert "nav_fonte" in out["error"]                         # l'istruzione
        for altro in set(VOCI) - {t}:
            assert altro not in out["error"], (t, altro)


def test_negozio_assente_o_illeggibile_dichiarato(tmp_path):
    assente = cl.carica_veicoli(str(tmp_path / "manca.json"))
    out = cef_nav.get_cef_nav("FONDO.L", negozio=assente)
    assert "error" in out and "ASSENTE" in out["error"]
    (tmp_path / "rotto.json").write_text("{", encoding="utf-8")
    rotto = cl.carica_veicoli(str(tmp_path / "rotto.json"))
    out2 = cef_nav.get_cef_nav("FONDO.L", negozio=rotto)
    assert "error" in out2 and "ILLEGGIBILE" in out2["error"]
    assert cef_nav.cef_sources_del_negozio(rotto) == {} and cef_nav.cef_senza_fonte_del_negozio(rotto) == {}


def test_il_messaggio_sul_negozio_vero_non_elenca_le_sue_viste():
    """Come `_vietati` nel cablaggio: misurato contro il negozio VERO (vuoto in un clone:
    vacuo ma onesto; il cancello del repo pubblico fa il resto)."""
    out = cef_nav.get_cef_nav("ZZZQ.XX")
    assert "error" in out
    for t in set(cef_nav.CEF_SOURCES) | set(cef_nav.CEF_SENZA_FONTE) | set(cl.carica_veicoli()["veicoli"]):
        assert t not in out["error"], t
