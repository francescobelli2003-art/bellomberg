# -*- coding: utf-8 -*-
"""Lotto 5 del criterio (1), 05/09 (Fable 5.1): i DATI DEL BOOK che vivevano nei moduli fuori
dalla classificazione (alias listino->fonte, lista IV, fattori, LEI, CIK/CEF dei gestori,
correzioni dei titoli) stanno in NEGOZI PRIVATI in data/, con la FORMA in un .example.json
tracciato.

Regole misurate qui (le stesse di news_search_terms / fonti_guidance / veicoli):
- negozio assente o illeggibile = voci VUOTE con origine e motivo scritti, mai un ripiego muto;
- UNA voce malformata rende illeggibile il negozio INTERO (mezzo negozio caricato e' un ripiego);
- le chiavi con `_` davanti sono note;
- l'esempio si carica col caricatore VERO ed e' SELEZIONATO dall'export; il negozio vero no;
- i consumatori leggono dal negozio a ogni chiamata, e il letterale non esiste piu' nel sorgente.
I simboli qui sono INVENTATI e verificati contro basi e nomi del DB (metodo dei lotti 2-4).
"""
import json
import os
import subprocess
import sys

import pytest

import bellomberg.storage.negozi_privati as np_

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))   # convenzione di tests/test_fonti_example.py

ESEMPI = ["alias_fonti.example.json", "iv_tickers.example.json",
          "fattori_portafoglio.example.json", "lei_emittenti.example.json",
          "istituzioni.example.json", "retro_title_correzioni.example.json",
          "prezzi_speciali.example.json"]


def _scrivi(tmp_path, nome, contenuto):
    p = tmp_path / nome
    p.write_text(contenuto if isinstance(contenuto, str) else json.dumps(contenuto),
                 encoding="utf-8")
    return str(p)


def _esempio(nome):
    return os.path.join(REPO, "src", "bellomberg", "resources", "examples", nome)


# ------------------------------------------------ A. il contratto del caricatore comune

def _valida_semplice(grezzo):
    return np_.mappa_canonica(grezzo, np_.stringa_piena)


def test_negozio_assente_dichiara_origine_motivo_ed_esempio(tmp_path):
    r = np_.carica(str(tmp_path / "manca.json"), "manca.example.json", _valida_semplice)
    assert r["voci"] == {} and r["origine"] == "assente"
    assert "manca.example.json" in r["motivo"] and "manca.json" in r["motivo"]


def test_json_rotto_e_illeggibile_col_motivo(tmp_path):
    p = _scrivi(tmp_path, "rotto.json", "{ non json")
    r = np_.carica(p, "x.example.json", _valida_semplice)
    assert r["voci"] == {} and r["origine"] == "illeggibile" and r["motivo"]


@pytest.mark.parametrize("testo, chiave", [
    ('{"tickers": ["ALFA"], "tickers": []}', "tickers"),
    ('{"finnhub": {"ALFA": "A", "ALFA": "B"}}', "ALFA"),
])
def test_chiave_json_duplicata_non_puo_sovrascrivere_in_silenzio(tmp_path, testo, chiave):
    p = _scrivi(tmp_path, "duplicato.json", testo)
    chiamate = []
    r = np_.carica(p, "x.example.json", lambda dato: chiamate.append(dato))
    assert r["origine"] == "illeggibile"
    assert r["voci"] == {} and chiave in r["motivo"]
    assert not chiamate, "il dato ambiguo non deve arrivare al validatore"


def test_negozio_che_non_e_un_oggetto_e_illeggibile(tmp_path):
    p = _scrivi(tmp_path, "lista.json", [1, 2])
    r = np_.carica(p, "x.example.json", _valida_semplice)
    assert r["origine"] == "illeggibile" and "list" in r["motivo"]


def test_una_voce_malformata_rende_illeggibile_il_negozio_intero(tmp_path):
    p = _scrivi(tmp_path, "mezzo.json", {"ALFA": "ok", "BETA": 12})
    r = np_.carica(p, "x.example.json", _valida_semplice)
    assert r["voci"] == {}, "mezzo negozio caricato e' un ripiego muto"
    assert r["origine"] == "illeggibile" and "BETA" in r["motivo"]


def test_le_chiavi_con_underscore_sono_note(tmp_path):
    p = _scrivi(tmp_path, "note.json", {"_leggimi": "nota", "ALFA": "ok"})
    r = np_.carica(p, "x.example.json", _valida_semplice)
    assert r["voci"] == {"ALFA": "ok"} and r["origine"] == p and r["motivo"] is None


@pytest.mark.parametrize("chiave", ["alfa", " ALFA", "AL FA"])
def test_chiave_non_canonica_e_malformata(tmp_path, chiave):
    """Ogni lookup fa .upper().strip(): una chiave scritta diversamente non verrebbe MAI
    trovata e il log direbbe «voce assente», vero per il codice e falso per chi l'ha scritta."""
    p = _scrivi(tmp_path, "k.json", {chiave: "ok"})
    r = np_.carica(p, "x.example.json", _valida_semplice)
    assert r["origine"] == "illeggibile" and "MAIUSCOL" in r["motivo"]


def test_il_vuoto_ha_la_forma_delle_voci(tmp_path):
    r = np_.carica(str(tmp_path / "no.json"), "x.example.json",
                   lambda g: np_.lista_canonica(g.get("tickers")), nome="tickers", vuoto=())
    assert r["tickers"] == () and r["origine"] == "assente"


def test_lista_canonica_rifiuta_doppioni_vuoti_e_non_stringhe():
    assert np_.lista_canonica(["ALFA", "BETA"]) == ("ALFA", "BETA")
    for cattiva in (["ALFA", "ALFA"], ["ALFA", ""], ["alfa"], "ALFA", [1], None):
        with pytest.raises(ValueError):
            np_.lista_canonica(cattiva)


# ------------------------------------------------ B1. alias listino -> fonte (finnhub, sec)

def test_alias_esempio_si_carica_col_caricatore_vero():
    r = np_.carica_alias(_esempio("alias_fonti.example.json"))
    assert r["motivo"] is None
    assert r["alias"]["finnhub"]["ACME.MI"] == "ACM" and r["alias"]["sec"]["ACME"] == "ACM"


def test_alias_negozio_assente_da_quattro_sezioni_vuote_dichiarate(tmp_path):
    r = np_.carica_alias(str(tmp_path / "no.json"))
    assert r["alias"] == {"finnhub": {}, "sec": {}, "yfinance": {},
                          "correlazione": {}} and r["origine"] == "assente"


def test_alias_una_sezione_sconosciuta_e_malformata(tmp_path):
    p = _scrivi(tmp_path, "a.json", {"finnhub": {}, "sec": {}, "bloomberg": {}})
    r = np_.carica_alias(p)
    assert r["origine"] == "illeggibile" and "bloomberg" in r["motivo"]


def test_alias_sec_con_suffisso_nella_chiave_e_malformato(tmp_path):
    """La sezione sec e' per BASE (prima del punto): una chiave col punto non verrebbe mai
    trovata da lookup_cik, che confronta la base."""
    p = _scrivi(tmp_path, "a.json", {"sec": {"ACME.MI": "ACM"}})
    r = np_.carica_alias(p)
    assert r["origine"] == "illeggibile" and "ACME.MI" in r["motivo"]


def test_finnhub_norm_ticker_traduce_col_negozio(tmp_path, monkeypatch):
    from bellomberg.market_data import finnhub_news
    p = _scrivi(tmp_path, "a.json", {"finnhub": {"ACME.MI": "ACM"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    assert finnhub_news._norm_ticker("ACME.MI") == "ACM"
    assert finnhub_news._norm_ticker("acme.mi ") == "ACM"
    assert finnhub_news._norm_ticker("BETA.MI") == "BETA", "senza alias resta la ricostruzione"
    assert finnhub_news._norm_ticker("GAMMA") == "GAMMA"


def test_finnhub_negozio_assente_ricostruisce_per_suffisso(tmp_path, monkeypatch):
    from bellomberg.market_data import finnhub_news
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(tmp_path / "no.json"))
    assert finnhub_news._norm_ticker("BETA.MI") == "BETA"
    assert finnhub_news._norm_ticker("THETA.L") == "THETA.L"


def test_sec_alias_verificato_apre_la_sec_e_traduce_il_cik(tmp_path, monkeypatch):
    from bellomberg.market_data import sec_edgar
    p = _scrivi(tmp_path, "a.json", {"sec": {"ACME": "ACM"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    assert sec_edgar.ticker_ambiguo_per_cik("ACME.MI") is None
    monkeypatch.setattr(sec_edgar, "_CIK_CACHE", {"ACM": "0000000009"})
    assert sec_edgar.lookup_cik("ACME.MI") == "0000000009"


def test_sec_senza_alias_il_suffisso_resta_ambiguo_e_dice_dove_metterlo(tmp_path, monkeypatch):
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(tmp_path / "no.json"))
    msg = sec_edgar.ticker_ambiguo_per_cik("BETA.MI")
    assert msg and "omonim" in msg.lower()
    assert "alias_fonti" in msg, "il messaggio deve dire in quale negozio va l'alias verificato"
    assert sec_edgar.ticker_ambiguo_per_cik("GAMMA") is None


# ------------------------------------------------ B2. la lista IV (iv_history + vol_cone)

def test_iv_esempio_si_carica():
    r = np_.carica_iv(_esempio("iv_tickers.example.json"))
    assert r["motivo"] is None and "ACME" in r["tickers"]


def test_iv_tickers_dal_negozio_e_assenza_dichiarata(tmp_path, monkeypatch):
    from bellomberg.market_data import iv_history
    monkeypatch.setattr(np_, "PERCORSO_IV", str(tmp_path / "no.json"))
    assert iv_history.iv_tickers() == ()
    p = _scrivi(tmp_path, "iv.json", {"tickers": ["ALFA", "ZETA"]})
    monkeypatch.setattr(np_, "PERCORSO_IV", p)
    assert iv_history.iv_tickers() == ("ALFA", "ZETA")


def test_iv_negozio_senza_la_chiave_tickers_e_malformato(tmp_path):
    p = _scrivi(tmp_path, "iv.json", {"simboli": ["ALFA"]})
    assert np_.carica_iv(p)["origine"] == "illeggibile"


def test_vol_cone_fuori_lista_dichiara_la_lista_del_negozio(tmp_path, monkeypatch):
    from bellomberg.portfolio import vol_cone
    p = _scrivi(tmp_path, "iv.json", {"tickers": ["ALFA"]})
    monkeypatch.setattr(np_, "PERCORSO_IV", p)
    out = vol_cone.compute_vol_cone("ZETA")
    assert out["error"] and "fuori dalla lista" in out["error"] and "ALFA" in out["error"]


def test_vol_cone_negozio_assente_lo_dichiara_invece_di_una_lista_vuota(tmp_path, monkeypatch):
    from bellomberg.portfolio import vol_cone
    monkeypatch.setattr(np_, "PERCORSO_IV", str(tmp_path / "no.json"))
    out = vol_cone.compute_vol_cone("ZETA")
    assert out["error"] and "assente" in out["error"] and "iv_tickers" in out["error"]


def test_save_daily_snapshot_senza_negozio_non_salva_e_dichiara(tmp_path, monkeypatch):
    from bellomberg.market_data import iv_history
    monkeypatch.setattr(np_, "PERCORSO_IV", str(tmp_path / "no.json"))
    out = iv_history.save_daily_snapshot(db_path=str(tmp_path / "x.db"))
    assert not out.get("saved"), out
    assert "assente" in (out.get("error") or ""), out


# ------------------------------------------------ B3. i fattori (crypto-correlati, regioni)

def test_fattori_esempio_si_carica():
    """Con le regioni AMMESSE DAL CONSUMATORE (portfolio_factors.REGIONAL_FF), non un insieme
    scritto a mano: e' quello che rende illeggibile il negozio a runtime (review 05/09)."""
    import bellomberg.portfolio.portfolio_factors as pf
    r = np_.carica_fattori(_esempio("fattori_portafoglio.example.json"),
                           regioni_valide=set(pf.REGIONAL_FF))
    assert r["motivo"] is None
    assert "KRYPTO" in r["fattori"]["crypto_correlati"]
    assert r["fattori"]["regioni"]["THETA.L"] == "us"


def test_fattori_regione_sconosciuta_e_malformata(tmp_path):
    p = _scrivi(tmp_path, "f.json", {"crypto_correlati": [], "regioni": {"THETA.L": "marte"}})
    r = np_.carica_fattori(p, regioni_valide={"us"})
    assert r["origine"] == "illeggibile" and "marte" in r["motivo"]


def test_portfolio_factors_legge_crypto_e_regioni_dal_negozio(tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_factors as pf
    p = _scrivi(tmp_path, "f.json", {"crypto_correlati": ["KRYPTO"], "regioni": {"THETA.L": "us"}})
    monkeypatch.setattr(np_, "PERCORSO_FATTORI", p)
    assert pf._is_crypto_correlated("krypto") is True
    assert pf._is_crypto_correlated("GAMMA") is False
    assert pf._region_for_ticker("THETA.L") == "us"
    assert pf._region_for_ticker("KAPPA.MI") == "europe", "senza override resta il suffisso"


def test_portfolio_factors_negozio_assente_regione_dal_suffisso(tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_factors as pf
    monkeypatch.setattr(np_, "PERCORSO_FATTORI", str(tmp_path / "no.json"))
    assert pf._region_for_ticker("KAPPA.MI") == "europe"
    assert pf._is_crypto_correlated("KRYPTO") is False


def _compute_factors_col_negozio(pf, monkeypatch, percorso):
    """`compute_portfolio_factors` con le parti pesanti (DB, download FF, OLS) sostituite:
    resta il cablaggio del negozio nel loop e nel payload — la review 05/09 ha trovato che
    nessun test lo esercitava (un nome mai assegnato passava banco e batterie)."""
    monkeypatch.setattr(np_, "PERCORSO_FATTORI", percorso)
    for flag in ("NUMPY_OK", "YF_OK", "SM_OK", "REQ_OK"):
        monkeypatch.setattr(pf, flag, True)
    monkeypatch.setattr(pf, "FACTORS_RESULT_CACHE", {"ts": 0, "key": "", "data": None})

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "THETA.L", "valore_mercato": 1000.0},
                                  {"ticker": "KRYPTO", "valore_mercato": 500.0},
                                  {"ticker": "GAMMA", "valore_mercato": 500.0}]}
    monkeypatch.setattr(pf, "MemoryDB", _DB)
    # lotto 6: _yf_ticker(ticker, salta) — la lambda deve accettare il secondo argomento,
    # altrimenti la fixture nasconde la firma vera con un TypeError
    monkeypatch.setattr(pf, "_yf_ticker", lambda t, salta: t)
    pd = pytest.importorskip("pandas")
    ff = pd.DataFrame({"Mkt-RF": [0.0, 0.0]}, index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    monkeypatch.setattr(pf, "download_ff5_returns", lambda force=False, region="us": ff)
    visti = {}

    def _esposizione(ticker, period="3y", ff_returns=None, add_btc_factor=False, factor_region="us", salta=None):
        visti[ticker] = (factor_region, add_btc_factor)
        base = {k: 0.0 for k in ("alpha_annualized_pct", "beta_market", "beta_smb", "beta_hml",
                                 "beta_rmw", "beta_cma", "beta_mom")}
        return dict(base, ticker=ticker, has_btc_factor=add_btc_factor, r_squared=0.5,
                    alpha_tstat=0.0, n_obs=100, factor_region=factor_region)
    monkeypatch.setattr(pf, "compute_holding_exposure", _esposizione)
    return pf.compute_portfolio_factors(force=True), visti


def test_compute_portfolio_factors_usa_il_negozio_e_lo_dichiara_nel_payload(tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_factors as pf
    p = _scrivi(tmp_path, "f.json", {"crypto_correlati": ["KRYPTO"], "regioni": {"THETA.L": "us"}})
    out, visti = _compute_factors_col_negozio(pf, monkeypatch, p)
    assert "error" not in out, out.get("error")
    assert visti["THETA.L"] == ("us", False), "l'override di regione del negozio non e' arrivato all'OLS"
    assert visti["KRYPTO"] == ("us", True), "il fattore BTC del negozio non e' arrivato all'OLS"
    assert out["per_holding"]["THETA.L"]["region_source"] == "override"
    assert "euristica" in out["per_holding"]["GAMMA"]["region_source"]
    filtri = out["filters"]
    assert filtri["btc_factor_for"] == ["KRYPTO"] and filtri["region_overrides"] == {"THETA.L": "us"}
    assert filtri["negozio_fattori"] == {"origine": p, "motivo": None}


def test_compute_portfolio_factors_a_negozio_assente_lo_dice_nel_payload(tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_factors as pf
    out, visti = _compute_factors_col_negozio(pf, monkeypatch, str(tmp_path / "no.json"))
    assert "error" not in out, out.get("error")
    assert visti["KRYPTO"] == ("us", False), "senza negozio nessun titolo e' crypto-correlato"
    assert out["filters"]["btc_factor_for"] == [] and out["filters"]["region_overrides"] == {}
    assert out["filters"]["negozio_fattori"]["origine"] == "assente"
    assert "fattori_portafoglio.example.json" in out["filters"]["negozio_fattori"]["motivo"]


# ------------------------------------------------ B4. i LEI (esef)

def test_lei_esempio_si_carica():
    r = np_.carica_lei(_esempio("lei_emittenti.example.json"))
    assert r["motivo"] is None and len(r["lei"]["ACME.MI"]) == 20


def test_lei_di_forma_sbagliata_e_malformato(tmp_path):
    p = _scrivi(tmp_path, "l.json", {"ACME.MI": "troppo-corto"})
    r = np_.carica_lei(p)
    assert r["origine"] == "illeggibile" and "ACME.MI" in r["motivo"]


def test_esef_resolve_lei_dal_negozio_e_lo_dice(tmp_path, monkeypatch):
    from bellomberg.market_data import esef
    p = _scrivi(tmp_path, "l.json", {"ACME.MI": "ESEMPIOLEI0000000010"})
    monkeypatch.setattr(np_, "PERCORSO_LEI", p)
    lei, nota = esef.resolve_lei("ACME.MI")
    assert lei == "ESEMPIOLEI0000000010" and "negozio" in nota.lower()


# ------------------------------------------------ B5. gestori: CIK 13F e fondi chiusi

def test_istituzioni_esempio_si_carica():
    r = np_.carica_istituzioni(_esempio("istituzioni.example.json"))
    assert r["motivo"] is None
    assert r["istituzioni"]["cik"]["esempio_capital"] == "0000000001"
    assert r["istituzioni"]["cef"]["THETA.L"]["investor"] == "esempio_capital"


def test_istituzioni_cef_che_punta_a_slug_ignoto_e_malformato(tmp_path):
    p = _scrivi(tmp_path, "i.json", {"cik": {"a_b": "0000000001"},
                                     "cef": {"THETA.L": {"investor": "nessuno", "manager": "X"}}})
    r = np_.carica_istituzioni(p)
    assert r["origine"] == "illeggibile" and "nessuno" in r["motivo"]


def test_istituzioni_cik_non_a_dieci_cifre_e_malformato(tmp_path):
    p = _scrivi(tmp_path, "i.json", {"cik": {"a_b": "123"}})
    assert np_.carica_istituzioni(p)["origine"] == "illeggibile"


def test_istituzioni_slug_non_minuscolo_e_malformato(tmp_path):
    p = _scrivi(tmp_path, "i.json", {"cik": {"Esempio Capital": "0000000001"}})
    assert np_.carica_istituzioni(p)["origine"] == "illeggibile"


def test_sec_13f_slug_sconosciuto_elenca_gli_slug_del_negozio(tmp_path, monkeypatch):
    from bellomberg.market_data import sec_edgar
    p = _scrivi(tmp_path, "i.json", {"cik": {"esempio_capital": "0000000001",
                                             "altro_fondo": "0000000002"}})
    monkeypatch.setattr(np_, "PERCORSO_ISTITUZIONI", p)
    with pytest.raises(sec_edgar.InvestitoreSconosciuto) as ei:
        sec_edgar.get_13f_holdings("slug_che_non_esiste")
    assert "esempio_capital" in str(ei.value) and "altro_fondo" in str(ei.value)


def test_sec_13f_negozio_assente_e_un_ko_dichiarato_non_un_elenco_vuoto(tmp_path, monkeypatch):
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(np_, "PERCORSO_ISTITUZIONI", str(tmp_path / "no.json"))
    with pytest.raises(sec_edgar.InvestitoreSconosciuto) as ei:
        sec_edgar.get_13f_holdings("esempio_capital")
    assert "assente" in str(ei.value) and "istituzioni" in str(ei.value)


def test_agent_tools_13f_legge_lo_stesso_negozio(tmp_path, monkeypatch):
    from bellomberg.agents import agent_tools
    p = _scrivi(tmp_path, "i.json", {"cik": {"esempio_capital": "0000000001"}})
    monkeypatch.setattr(np_, "PERCORSO_ISTITUZIONI", p)
    out = agent_tools.tool_get_13f_filing("slug_che_non_esiste")
    assert out.get("error") and "esempio_capital" in out["error"]


def test_agent_tools_13f_negozio_assente_e_un_ko_dichiarato(tmp_path, monkeypatch):
    from bellomberg.agents import agent_tools
    monkeypatch.setattr(np_, "PERCORSO_ISTITUZIONI", str(tmp_path / "no.json"))
    out = agent_tools.tool_get_13f_filing("esempio_capital")
    assert out.get("error") and "assente" in out["error"]


def test_cef_lookthrough_legge_i_gestori_dal_negozio(tmp_path, monkeypatch):
    from bellomberg.valuation import cef_lookthrough
    monkeypatch.setattr(np_, "PERCORSO_ISTITUZIONI", str(tmp_path / "no.json"))
    out = cef_lookthrough.get_lookthrough("THETA.L")
    assert out.get("error") and "assente" in out["error"]
    p = _scrivi(tmp_path, "i.json", {"cik": {"esempio_capital": "0000000001"},
                                     "cef": {"THETA.L": {"investor": "esempio_capital",
                                                         "manager": "Esempio Capital"}}})
    monkeypatch.setattr(np_, "PERCORSO_ISTITUZIONI", p)
    out = cef_lookthrough.get_lookthrough("ZETA.MI")
    # 05/09 (chat e3, cura della regressione CEF): il messaggio CONTA i fondi coperti
    # invece di ELENCARLI (l'elenco era il book del PM e usciva nel payload del tool e
    # nel prompt del Capo). La prova che i gestori vengono dal NEGOZIO resta: il
    # conteggio e' quello del negozio finto scritto qui sopra, una voce.
    assert out.get("error"), "i CEF coperti vengono dal negozio"
    assert "1 fondo chiuso" in out["error"], "il conteggio viene dal negozio"
    assert "THETA.L" not in out["error"], "la copertura si conta, non si elenca"


# ------------------------------------------------ B6. le correzioni dei titoli (retro_title_chats)

def test_correzioni_esempio_si_carica():
    r = np_.carica_correzioni_titoli(_esempio("retro_title_correzioni.example.json"))
    assert r["motivo"] is None
    assert r["correzioni"][12]["titolo"].startswith("Acme")
    assert r["correzioni"][12]["deve_contenere"] == ["Acme"]
    assert r["intatti"][13] == ["ACME.MI"]


def test_correzioni_id_non_intero_e_malformato(tmp_path):
    base = {"titolo": "t", "cosa_c_era": "c", "perche": "p"}
    p = _scrivi(tmp_path, "c.json", {"correzioni": {"x1": base}})
    r = np_.carica_correzioni_titoli(p)
    assert r["origine"] == "illeggibile" and "x1" in r["motivo"]


def test_correzioni_id_in_entrambe_le_liste_e_malformato(tmp_path):
    base = {"titolo": "t", "cosa_c_era": "c", "perche": "p"}
    p = _scrivi(tmp_path, "c.json", {"correzioni": {"5": base}, "intatti": {"5": ["A"]}})
    r = np_.carica_correzioni_titoli(p)
    assert r["origine"] == "illeggibile" and "5" in r["motivo"]


def test_correzioni_negozio_assente_da_mappe_vuote(tmp_path):
    r = np_.carica_correzioni_titoli(str(tmp_path / "no.json"))
    assert r["correzioni"] == {} and r["intatti"] == {} and r["origine"] == "assente"


def test_retro_title_chats_senza_negozio_si_ferma_dichiarando(tmp_path, monkeypatch):
    rt = pytest.importorskip("bellomberg.cli.retro_title_chats")
    monkeypatch.setattr(np_, "PERCORSO_CORREZIONI_TITOLI", str(tmp_path / "no.json"))
    with pytest.raises(RuntimeError) as ei:
        rt.correzioni()
    assert "assente" in str(ei.value)


# ------------------------------------------------ B7. i prezzi speciali (lotto 6, 05/09)

def test_prezzi_esempio_si_carica_col_caricatore_vero():
    r = np_.carica_prezzi_speciali(_esempio("prezzi_speciali.example.json"))
    assert r["motivo"] is None, r["motivo"]
    assert "KRYPTO" in r["prezzi"]["senza_yfinance"]
    assert r["prezzi"]["coingecko"]["KRYPTO"] == "krypto-coin"


def test_prezzi_negozio_assente_da_insieme_e_mappa_vuoti(tmp_path):
    r = np_.carica_prezzi_speciali(str(tmp_path / "no.json"))
    assert r["prezzi"] == {"senza_yfinance": frozenset(), "coingecko": {}}
    assert r["origine"] == "assente"
    assert "prezzi_speciali.example.json" in r["motivo"]


def test_prezzi_le_due_sezioni_sono_indipendenti(tmp_path):
    """Un id CoinGecko NON implica lo skip: e' la ragione per cui le sezioni sono due
    (nel file dei prezzi tre crypto generiche hanno un proxy yfinance e vanno scaricate)."""
    p = _scrivi(tmp_path, "prezzi.json", {"senza_yfinance": ["ALFA"],
                                          "coingecko": {"OMEGA": "omega-coin"}})
    r = np_.carica_prezzi_speciali(p)
    assert r["prezzi"]["senza_yfinance"] == frozenset({"ALFA"})
    assert r["prezzi"]["coingecko"] == {"OMEGA": "omega-coin"}


def test_prezzi_sezione_sconosciuta_rende_illeggibile(tmp_path):
    p = _scrivi(tmp_path, "prezzi.json", {"senza_yfinance": [], "fonte_strana": {}})
    r = np_.carica_prezzi_speciali(p)
    assert r["origine"] == "illeggibile" and "fonte_strana" in r["motivo"]


@pytest.mark.parametrize("id_cg", ["Hyper-Liquid", "krypto coin", "krypto--coin", "", 7])
def test_prezzi_id_coingecko_malformato_rende_illeggibile(tmp_path, id_cg):
    """L'id sta nell'URL dell'API: minuscolo, cifre e trattini singoli. Una maiuscola o
    uno spazio darebbero un 404 muto al giro dei prezzi, non un errore."""
    p = _scrivi(tmp_path, "prezzi.json", {"senza_yfinance": [], "coingecko": {"KRYPTO": id_cg}})
    r = np_.carica_prezzi_speciali(p)
    assert r["origine"] == "illeggibile"
    assert r["prezzi"] == {"senza_yfinance": frozenset(), "coingecko": {}}


def test_prezzi_simbolo_con_spazio_interno_e_malformato(tmp_path):
    """`lista_canonica` toglie gli spazi ai bordi ma non vede quelli INTERNI: un «ALFA BETA»
    passerebbe e non combacerebbe con nessun ticker, cioe' uno skip muto."""
    p = _scrivi(tmp_path, "prezzi.json", {"senza_yfinance": ["ALFA BETA"]})
    r = np_.carica_prezzi_speciali(p)
    assert r["origine"] == "illeggibile" and "spazi" in r["motivo"]


def test_prezzi_sezione_obbligatoria_mancante(tmp_path):
    p = _scrivi(tmp_path, "prezzi.json", {"coingecko": {"KRYPTO": "krypto-coin"}})
    r = np_.carica_prezzi_speciali(p)
    assert r["origine"] == "illeggibile" and "senza_yfinance" in r["motivo"]


def test_prezzi_chiave_minuscola_rende_illeggibile(tmp_path):
    """price_updater cerca con .upper(): una chiave minuscola non verrebbe MAI trovata
    e il log direbbe «nessun id», vero per il codice e falso per chi l'ha scritta."""
    p = _scrivi(tmp_path, "prezzi.json", {"senza_yfinance": [], "coingecko": {"krypto": "krypto-coin"}})
    assert np_.carica_prezzi_speciali(p)["origine"] == "illeggibile"


# ------------------------------------------------ C. l'export nei due versi

@pytest.mark.parametrize("esempio", ESEMPI)
def test_esempio_SELEZIONATO_dall_export_e_il_negozio_no(esempio):
    """Essere nominati nell'allowlist non e' uscire: si misura con le funzioni VERE
    dell'export. Nel tree pubblico salta con motivo dichiarato."""
    allowlist = os.path.join(REPO, "tools", "release", "policy", "ALLOWLIST.txt")
    if not os.path.exists(allowlist):
        pytest.skip("policy/ALLOWLIST.txt assente: siamo nel tree pubblico (P2)")
    from tools.release import export_pubblico as ep
    from tools.release import verifica_pubblico as vp
    tracciati = ep.file_tracciati(REPO)
    scelti, _ = ep.seleziona(tracciati, vp.leggi_lista(allowlist))
    assert "src/bellomberg/resources/examples/" + esempio in scelti
    negozio = "data/" + esempio.replace(".example", "")
    assert negozio not in scelti and negozio not in tracciati


@pytest.mark.parametrize("esempio", ESEMPI)
def test_esempio_tracciato_e_negozio_ignorato(esempio):
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8", errors="replace").returncode

    percorso_esempio = "src/bellomberg/resources/examples/" + esempio
    assert git("ls-files", "--error-unmatch", percorso_esempio) == 0, "%s non e' tracciato" % esempio
    assert git("check-ignore", percorso_esempio) == 1
    negozio = "data/" + esempio.replace(".example", "")
    assert git("check-ignore", negozio) == 0, "%s DEVE restare ignorato" % negozio


# ------------------------------------------------ D. il letterale non esiste piu' nel sorgente

@pytest.mark.parametrize("modulo,letterale", [
    ("src/bellomberg/market_data/finnhub_news.py", "EXPLICIT = {"),
    ("src/bellomberg/market_data/sec_edgar.py", "_TICKER_ALIASES"),
    ("src/bellomberg/market_data/sec_edgar.py", "INSTITUTIONAL_CIKS = {"),
    ("src/bellomberg/market_data/iv_history.py", "IV_TICKERS: Sequence"),
    ("src/bellomberg/portfolio/portfolio_factors.py", "CRYPTO_CORRELATED_TICKERS = {"),
    ("src/bellomberg/portfolio/portfolio_factors.py", "REGION_OVERRIDES: Dict"),
    ("src/bellomberg/market_data/esef.py", "LEI_BY_TICKER = {"),
    ("src/bellomberg/agents/agent_tools.py", "INSTITUTIONS_CIK = {"),
    ("src/bellomberg/valuation/cef_lookthrough.py", "CEF_MANAGERS = {"),
    ("src/bellomberg/cli/retro_title_chats.py", "CORREZIONI = {"),
    # lotto 6 (05/09): la lista dei simboli senza yfinance e la mappa CoinGecko
    ("src/bellomberg/portfolio/portfolio_analytics.py", "SKIP_TICKERS = {"),
    ("src/bellomberg/portfolio/portfolio_factors.py", "SKIP_TICKERS = {"),
    ("src/bellomberg/portfolio/portfolio_garch.py", "SKIP_TICKERS = {"),
    ("src/bellomberg/portfolio/portfolio_montecarlo.py", "SKIP_TICKERS = {"),
    ("src/bellomberg/portfolio/portfolio_risk.py", "SKIP_TICKERS = {"),
    ("src/bellomberg/cli/price_updater.py", "COINGECKO_MAP = {"),
])
def test_il_letterale_non_esiste_piu_nel_sorgente(modulo, letterale):
    """La migrazione e' finita solo quando il dato cablato non c'e' piu'."""
    src = open(os.path.join(REPO, modulo), encoding="utf-8").read()
    assert letterale not in src


@pytest.mark.parametrize("modulo,argomenti", [
    ("src/bellomberg/market_data/sec_edgar.py", ["13f"]),
    ("src/bellomberg/market_data/esef.py", []),
])
def test_il_main_senza_argomento_non_ha_un_simbolo_di_default(modulo, argomenti):
    """`python sec_edgar.py 13f` e `python esef.py` interrogavano un gestore e un emittente
    del book scritti nel sorgente: ora chiedono l'argomento e si fermano (uscita 2)."""
    r = subprocess.run([sys.executable, "-B", os.path.join(REPO, modulo), *argomenti],
                       cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 2, (r.returncode, r.stdout[-300:], r.stderr[-300:])
    assert "uso" in (r.stdout + r.stderr).lower()
