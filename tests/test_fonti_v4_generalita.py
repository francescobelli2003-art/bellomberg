"""R-FONTI 10/10 v4 (Opus 5.5): audit di GENERALITA' delle guardie 6-K e del ripiego iXBRL (non solo i casi da cui
sono nate). Dati SINTETICI (nomi Zz*/Zeta*, cifre inventate), nessuna rete, nessuna AI.

1  intestazione del prospetto con forme giuridiche estese e suffissi di gruppo dopo l'alias («BANCO X S.A. AND
   SUBSIDIARIES», «Grupo X, S.A.B. de C.V.», «PT X (Persero) Tbk», «X A/S», «X Partners L.P.»): e' l'emittente;
   la controllata col nome del padre («X Distribuidora S.A.», «X Group Finance B.V.») resta un'altra entita';
2  parola di relazione nella frase del periodo: conta solo un nome in apposizione stretta con un segnale di
   entita' (forma giuridica o verbo di risultati); collettivi, «parent company of» col soggetto emittente e
   parole maiuscole qualsiasi («Brazil», «Brazilian») non contano;
6  dei:DocumentPeriodEndDate in formati diversi («Dec. 31, 2025», «31.12.2025», ...) e, se non convertibile,
   fine del periodo del contesto del fatto (dichiarata);
7  forme 6-K residue: «ending», «semester ended», «first half of 2025» (tipo), «3-month», «31st March 2026»,
   «31.03.2026»; parole da documento: MD&A, Vorstand, conseil d'administration.
"""
import re

import pytest

from bellomberg.market_data import sec_xbrl
from bellomberg.market_data.filing_profili_auto import sonda_tipo_6k
from bellomberg.market_data.filing_verifica import (PERIODO_6K_STANDARD, TIPO_6K_STANDARD, _data, identita_6k)

CIK_URL = "https://www.sec.gov/Archives/edgar/data/9990077/000999007729000009/zz.htm"


def _copertina(registrante):
    return (f"UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nFORM 6-K\nREPORT OF FOREIGN PRIVATE ISSUER\n"
            f"For the month of May 2026\n{registrante}\n(Exact name of registrant as specified in its charter)\n"
            "Indicate by check mark whether the registrant files or will file annual reports under cover of "
            "Form 20-F or Form 40-F.\nForm 20-F X Form 40-F\n\n")


def _prospetto(intestazione, societa_nota):
    revisore = ("Report of Independent Registered Public Accounting Firm\nWe have reviewed the accompanying interim "
                "condensed consolidated statement of financial position.\n" + "x " * 30 + "\n")
    return ("Index to the financial statements\nPage 1\n" + revisore + "Page 2\n" + "\n".join(intestazione)
            + "\nUnaudited Interim Condensed Consolidated Financial Statements\nfor the three months ended March 31,"
              " 2026\n(In thousands)\n" + "Total assets 1 2 3\n" * 5 + "Notes to the financial statements\n"
            + f"1. OPERATIONS\n{societa_nota} (the \"Company\") is a company incorporated in 2010.\n")


def _identita(tmp_path, nome, testo):
    p = tmp_path / "zz.txt"
    p.write_text(_copertina(nome.upper()) + testo, encoding="utf-8")
    return identita_6k(p, url=CIK_URL, profilo={"nome": nome, "forme_sec": ["6-K"], "verifica": {}})


# ---------------------------------------------------------------- 1. intestazioni del prospetto
@pytest.mark.parametrize("nome,intestazione", [
    ("Grupo Zetavision, S.A.B. de C.V.", ["Grupo Zetavision, S.A.B. de C.V."]),
    ("Banco Zetamerica S.A.", ["BANCO ZETAMERICA S.A. AND SUBSIDIARIES"]),
    ("Banco Zetamerica S.A.", ["Banco Zetamerica S.A. and Subsidiaries"]),
    ("PT Zetakom Indonesia (Persero) Tbk", ["PT Zetakom Indonesia (Persero) Tbk"]),
    ("Zetanord A/S", ["Zetanord A/S"]),
    ("Zetafield Infrastructure Partners L.P.", ["Zetafield Infrastructure Partners L.P."]),
    ("Zeta Electronics Co., Ltd.", ["Zeta Electronics Co., Ltd. and its subsidiaries"]),
    ("Zetabras S.A.", ["Zetabras S.A. e controladas"]),
], ids=["sab_de_cv", "and_subsidiaries_maiuscolo", "and_subsidiaries", "persero_tbk", "a_s", "l_p",
        "co_ltd_and_its_subsidiaries", "e_controladas"])
def test_intestazione_con_forme_estese_e_gruppo_e_l_emittente(tmp_path, nome, intestazione):
    motivo, prova = _identita(tmp_path, nome, _prospetto(intestazione, nome))
    assert motivo is None, motivo


@pytest.mark.parametrize("nome,intestazione,societa,atteso", [
    ("Zetabras S.A.", ["Zetabras Distribuidora S.A."], "Zetabras Distribuidora S.A.", "Zetabras Distribuidora"),
    ("Zeta Holdings Ltd.", ["Zeta Pagamentos S.A.", "A subsidiary of", "Zeta Holdings Ltd."], "Zeta Pagamentos S.A.",
     "Zeta Pagamentos"),
    ("Zzdelta Group N.V.", ["Zzdelta Group Finance B.V."], "Zzdelta Group Finance B.V.", "Finance"),
    ("Banco Zetamerica S.A.", ["BANCO ZETAMERICA LEASING S.A. AND SUBSIDIARIES"], "Banco Zetamerica Leasing S.A.",
     "LEASING"),
], ids=["controllata_nome_del_padre", "a_subsidiary_of", "finance_bv", "leasing_and_subsidiaries"])
def test_intestazione_della_controllata_col_nome_del_padre_rifiutata(tmp_path, nome, intestazione, societa, atteso):
    motivo, _ = _identita(tmp_path, nome, _prospetto(intestazione, societa))
    assert motivo and "altra entita'" in motivo and atteso in motivo, motivo


# ---------------------------------------------------------------- 2. relazioni nella frase del periodo
def _comunicato(frase):
    return f"Exhibit 99.1\nSao Paulo, May 12, 2026 - {frase}\nHighlights\nRevenue grew.\n"


@pytest.mark.parametrize("nome,frase", [
    ("Zetabras S.A.", "Zetabras S.A. and its affiliates reported results for the quarter ended March 31, 2026, in "
                      "Brazil and Mexico."),
    ("Zetabras S.A.", "Zetabras S.A., the largest partner of Brazilian retailers, reported results for the quarter "
                      "ended March 31, 2026."),
    ("Zeta Group Holdings, Inc.", "Zeta Group Holdings, Inc., the parent company of Zeta Bank, reported consolidated "
                                  "results for the quarter ended March 31, 2026."),
    ("Zetabras S.A.", "Zetabras S.A. reported results for the quarter ended March 31, 2026, including its share of "
                      "the joint venture Raizeta."),
    ("Zetabras S.A.", "Zetabras S.A., which operates through its subsidiary Zetabras Energia, reported consolidated "
                      "results for the quarter ended March 31, 2026."),
    ("Zetabras S.A.", "We present the Management Discussion and Analysis of Zetabras S.A. (\"Zetabras\" or the "
                      "\"Company\") for the quarter ended March 31, 2026."),
    # la controllata ha la forma giuridica: conta solo la regola «parent company of» col soggetto emittente
    ("Zeta Group Holdings, Inc.", "Zeta Group Holdings, Inc., the parent company of Zeta Bank Ltd., reported "
                                  "consolidated results for the quarter ended March 31, 2026."),
    # collettivo con nomi societari: «partners» plurale non attribuisce i risultati
    ("Zetabras S.A.", "Zetabras S.A., working with its partners Zzone S.A. and Zztwo S.A., reported results for the "
                      "quarter ended March 31, 2026."),
], ids=["affiliates_e_paesi", "partner_di_aggettivo", "parent_company_of", "joint_venture_senza_forma",
        "controllata_senza_forma_ne_verbo", "mdna", "parent_company_of_con_forma", "partners_collettivo"])
def test_relazioni_senza_altra_entita_attribuita_verificate(tmp_path, nome, frase):
    motivo, _ = _identita(tmp_path, nome, _comunicato(frase))
    assert motivo is None, motivo


@pytest.mark.parametrize("nome,frase,atteso", [
    ("Zetabras S.A.", "Zetabras S.A. presents the results for the quarter ended March 31, 2026 of its partner "
                      "Zzpartner Ltd.", "Zzpartner"),
    ("Zetabras S.A.", "Zetabras S.A. and its affiliate Zzaff Ltd. reported results for the quarter ended March 31, "
                      "2026.", "Zzaff"),
    ("Zetabras S.A.", "Zetabras S.A. furnishes, on behalf of Zzother S.A., its results for the quarter ended March "
                      "31, 2026.", "Zzother"),
    ("Zetabras S.A.", "Zetabras S.A. announces that the joint venture Zzjv released results for the quarter ended "
                      "March 31, 2026.", "Zzjv"),
    # nome SUBITO PRIMA della parola di relazione («Zzpartner Ltd., its partner»)
    ("Zetabras S.A.", "Zetabras S.A. presents the results for the quarter ended March 31, 2026 of Zzpartner Ltd., "
                      "its payments partner.", "Zzpartner"),
], ids=["partner_con_forma", "affiliate_singolare_con_forma", "on_behalf_of", "jv_che_pubblica",
        "apposizione_prima"])
def test_relazione_con_altra_entita_in_apposizione_rifiutata(tmp_path, nome, frase, atteso):
    motivo, _ = _identita(tmp_path, nome, _comunicato(frase))
    assert motivo and atteso in motivo, motivo


# ---------------------------------------------------------------- 6. dei:DocumentPeriodEndDate
@pytest.mark.parametrize("valore", ["Dec. 31, 2025", "31 Dec 2025", "December 31st, 2025", "31.12.2025",
                                    "2025-12-31", "December 31, 2025"])
def test_data_del_documento_in_formati_diversi(valore):
    assert sec_xbrl._data_iso(valore) == "2025-12-31"


def _raw_dei(valore):
    return {"documentInfo": {"namespaces": {"cik": "http://www.sec.gov/CIK", "dei": "http://xbrl.sec.gov/dei/2024"}},
            "facts": {"f1": {"value": valore, "dimensions": {
                "entity": "cik:0009990077", "concept": "dei:DocumentPeriodEndDate",
                "period": "2025-01-01T00:00:00/2026-01-01T00:00:00"}}}}


def test_data_non_convertibile_usa_la_fine_del_contesto_dichiarata():
    # «12/31/2025»: l'ordine giorno/mese non e' nel valore, la data non si indovina dal testo
    assert sec_xbrl._data_iso("12/31/2025") == "12/31/2025"
    r = sec_xbrl.fatti_ixbrl(_raw_dei("12/31/2025"), "9990077")
    assert r["periodo_documento"] == ["2025-12-31"]
    assert r["periodo_documento_dal_contesto"] == ["2025-12-31"]
    r = sec_xbrl.fatti_ixbrl(_raw_dei("Dec. 31, 2025"), "9990077")
    assert r["periodo_documento"] == ["2025-12-31"] and "periodo_documento_dal_contesto" not in r


# ---------------------------------------------------------------- 7. forme 6-K residue
@pytest.mark.parametrize("testo,tipo,fine", [
    ("for the three months ended 31st March 2026", "trimestrale", "2026-03-31"),
    ("for the three-month period ended 31.03.2026", "trimestrale", "2026-03-31"),
    ("for the 3-month period ended March 31, 2026", "trimestrale", "2026-03-31"),
    ("for the semester ended June 30, 2025", "semestrale", "2025-06-30"),
], ids=["ordinale", "data_col_punto", "3_month", "semester"])
def test_forme_6k_residue_riconosciute(testo, tipo, fine):
    assert re.search(TIPO_6K_STANDARD, testo, re.I)
    m = re.search(PERIODO_6K_STANDARD[tipo], testo, re.I)
    assert m and _data(m["fine"]).isoformat() == fine
    assert sonda_tipo_6k(testo) == tipo


@pytest.mark.parametrize("testo,tipo", [("for the three months ending March 31, 2026", "trimestrale"),
                                        ("for the quarter ending June 30, 2026", "trimestrale"),
                                        ("for the six months ending June 30, 2026", "semestrale")],
                         ids=["three_months_ending", "guidance_quarter_ending", "six_months_ending"])
def test_ending_vale_per_il_tipo_mai_per_il_periodo(testo, tipo):
    # v5: «for the quarter ending June 30, 2026» e' di solito una previsione: tipo si', periodo NO (da confermare)
    assert re.search(TIPO_6K_STANDARD, testo, re.I) and sonda_tipo_6k(testo) == tipo
    assert not re.search(PERIODO_6K_STANDARD["trimestrale"], testo, re.I)
    assert not re.search(PERIODO_6K_STANDARD["semestrale"], testo, re.I)


# ---------------------------------------------------------------- v5 (ri-verifica di d487ac7)
@pytest.mark.parametrize("frase", ["owns and operates 120 stores in Brazil.",
                                   "holds a banking license granted by the Central Bank.",
                                   "is a holding company which controls the Zetabras group.",
                                   "holds licenses to operate in Brazil and owns 100% of Zetabras Energia Ltda."],
                         ids=["owns_and_operates", "holds_license", "controls_group", "owns_controllata"])
@pytest.mark.parametrize("testa", ["Exhibit 99.1\nZetabras S.A. reported results for the quarter ended March 31, "
                                   "2026.\n", "Exhibit 99.1\nIndex\n"], ids=["con_comunicato", "solo_prospetto"])
def test_nota_1_forme_attive_dell_emittente_non_sono_relazione(tmp_path, frase, testa):
    corpo = (testa + "Page 2\nZetabras S.A.\nUnaudited Interim Condensed Consolidated Financial Statements\nfor the "
             "three months ended March 31, 2026\n" + "Total 1 2\n" * 3
             + f"Notes\n1. OPERATIONS\nZetabras S.A. (the \"Company\") {frase}\n")
    motivo, _ = _identita(tmp_path, "Zetabras S.A.", corpo)
    assert motivo is None, motivo


@pytest.mark.parametrize("frase,atteso", [
    ("is a wholly-owned subsidiary of Zetaholding Ltd.", "wholly-owned"),
    ("is owned by Zetaholding Ltd.", "owned by"),
    ("is controlled by Zetaholding Ltd.", "controlled by"),
    ("is held by Zetaholding Ltd.", "held by"),
    ("is a subsidiary of Zetaholding Ltd.", "subsidiary")], ids=["wholly_owned", "owned_by", "controlled_by",
                                                               "held_by", "subsidiary_of"])
def test_nota_1_forme_passive_o_di_dipendenza_restano_rifiutate(tmp_path, frase, atteso):
    corpo = ("Exhibit 99.1\nIndex\nPage 2\nZetabras S.A.\nUnaudited Interim Condensed Consolidated Financial "
             "Statements\nfor the three months ended March 31, 2026\n" + "Total 1 2\n" * 3
             + f"Notes\n1. OPERATIONS\nZetabras S.A. (the \"Company\") {frase}\n")
    motivo, _ = _identita(tmp_path, "Zetabras S.A.", corpo)
    assert motivo and "relazione" in motivo and atteso in motivo, motivo


@pytest.mark.parametrize("frase", [
    "Mexico City, April 28, 2026 - Grupo Zetavision, S.A.B. de C.V. (\"Zetavision\" or \"the Company\") announced "
    "results for the first quarter ended March 31, 2026.",
    "Ciudad de México, 28 de abril de 2026 - Grupo Zetavision, S.A.B. de C.V. reporta sus resultados del primer "
    "trimestre terminado el 31 de marzo de 2026, for the three months ended March 31, 2026."],
    ids=["comunicato_inglese", "comunicato_spagnolo"])
def test_copertina_del_corpo_con_la_radice_dell_alias(tmp_path, frase):
    motivo, _ = _identita(tmp_path, "Grupo Zetavision, S.A.B. de C.V.", f"Exhibit 99.1\n{frase}\nHighlights\n")
    assert motivo is None, motivo


@pytest.mark.parametrize("nome,frase", [
    ("Zetanord A/S", "Zetanord A/S today reported sales growth for the three months ended March 31, 2026."),
    ("PT Zetakom Indonesia (Persero) Tbk", "PT Zetakom Indonesia (Persero) Tbk today reported results for the three "
                                           "months ended March 31, 2026."),
    ("Zetafield Infrastructure Partners L.P.", "Zetafield Infrastructure Partners L.P. today reported results for "
                                               "the three months ended March 31, 2026.")],
    ids=["a_s", "persero_tbk", "l_p"])
def test_forma_estesa_dopo_l_alias_non_e_un_altro_nome(tmp_path, nome, frase):
    # v5: «A/S», «(Persero) Tbk», «L.P.» dopo l'alias sono la forma dell'emittente, non «alias esteso da un altro nome»
    motivo, _ = _identita(tmp_path, nome, f"Exhibit 99.1\n{frase}\nHighlights\n")
    assert motivo is None, motivo


@pytest.mark.parametrize("filed,fino_al,oggi,atteso", [
    ("2026-10-11", "2026-12-31", "2026-10-10", False),   # cutoff nel futuro: vale il giorno della run
    ("2026-10-10", "2026-12-31", "2026-10-10", True),
    ("2026-10-09", "2026-12-31", "2026-10-10", True),
    ("2026-10-11", None, "2026-10-10", False),           # solo oggi: filed <= oggi
    ("2026-10-10", None, "2026-10-10", True),
    (None, None, "2026-10-10", False),
    ("2099-01-01", None, None, True),                    # ne' cutoff ne' giorno: nessun filtro (com'era)
    ("2025-04-16", "2025-04-16", "2026-10-10", False),   # cutoff storico, stesso giorno: escluso (v3)
])
def test_depositato_entro_cutoff_effettivo(filed, fino_al, oggi, atteso):
    assert sec_xbrl.depositato_entro(filed, fino_al, oggi=oggi) is atteso


def test_semester_vale_sei_mesi_nel_periodo():
    from bellomberg.market_data.filing_verifica import _periodo_testuale
    m = re.search(PERIODO_6K_STANDARD["semestrale"], "for the semester ended June 30, 2025", re.I)
    inizio, fine, regola = _periodo_testuale(m, "semestrale")
    assert (inizio.isoformat(), fine.isoformat(), regola["mesi"]) == ("2025-01-01", "2025-06-30", 6)


def test_first_half_e_un_tipo_senza_data_finale_ne_periodo_indovinato():
    testo = "results for the first half of 2025"
    assert re.search(TIPO_6K_STANDARD, testo, re.I)
    assert not re.search(PERIODO_6K_STANDARD["semestrale"], testo, re.I)  # periodo da confermare, dichiarato


@pytest.mark.parametrize("testo", ["for the twenty-three months ended March 31, 2026",
                                   "for the nine-month period ended September 30, 2025"])
def test_durate_non_trimestrali_non_diventano_trimestre(testo):
    assert not re.search(PERIODO_6K_STANDARD["trimestrale"], testo, re.I)


def test_data_con_la_barra_rifiutata():
    with pytest.raises(ValueError):
        _data("12/31/2025")


# ---------------------------------------------------------------- v6: dateline e forme in maiuscolo
_PERIODO_V6 = "results for the quarter ended March 31, 2026."


@pytest.mark.parametrize("nome,frase", [
    ("Zetabras S.A.", f"TOKYO, May 15, 2026 /PRNewswire/ -- Zetabras S.A. reported {_PERIODO_V6}"),
    ("Zetabras S.A.", f"MÉXICO, D.F., a 28 de abril de 2026.- Zetabras S.A. reported {_PERIODO_V6}"),
    ("Grupo Zetavision, S.A.B. de C.V.", f"GRUPO ZETAVISION, S.A.B. DE C.V. (BMV: ZTV) reported {_PERIODO_V6}"),
], ids=["wire_fra_barre", "a_data_punto_trattino", "sab_de_cv_maiuscolo"])
def test_v6_dateline_e_forma_maiuscola_dell_emittente(tmp_path, nome, frase):
    motivo, _ = _identita(tmp_path, nome, f"Exhibit 99.1\n{frase}\nHighlights\n")
    assert motivo is None, motivo


@pytest.mark.parametrize("nome,frase,atteso", [
    ("Zetabras S.A.", f"TOKYO, May 15, 2026 /PRNewswire/ -- Zetapay S.A. reported {_PERIODO_V6}", "Zetapay"),
    ("Zetabras S.A.", f"MÉXICO, D.F., a 28 de abril de 2026.- Zetapay S.A. reported {_PERIODO_V6}", "Zetapay"),
    ("Grupo Zetavision, S.A.B. de C.V.", f"GRUPO ZETAVISION TELECOM, S.A. DE C.V. (BMV: ZTT) reported {_PERIODO_V6}",
     "TELECOM"),
], ids=["wire_altro_soggetto", "a_data_altro_soggetto", "controllata_maiuscola"])
def test_v6_altro_soggetto_dopo_il_dateline_resta_rifiutato(tmp_path, nome, frase, atteso):
    # l'emittente in testa al corpo («<emittente> - Exhibit 99.1»): il rifiuto e' quello della frase del periodo
    motivo, _ = _identita(tmp_path, nome, f"{nome} - Exhibit 99.1\n{frase}\nHighlights\n")
    assert motivo and atteso in motivo, motivo


# ---------------------------------------------------------------- 5. iXBRL su piu' file del deposito
def _istanza(cik):
    """Istanza XBRL (.xml) estratta da EDGAR: fatti dell'emittente che il documento primario non porta."""
    from tests.test_fonti_ixbrl_esef_sito import _ctx, _durata
    return ('<?xml version="1.0" encoding="utf-8"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
            'xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full" '
            'xmlns:dei="http://xbrl.sec.gov/dei/2024">'
            + _ctx("d25", _durata("2025-01-01", "2025-12-31"), cik)
            + _ctx("i25", "<xbrli:instant>2025-12-31</xbrli:instant>", cik)
            + '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
            '<dei:DocumentPeriodEndDate contextRef="d25">2025-12-31</dei:DocumentPeriodEndDate>'
            '<ifrs-full:Revenue contextRef="d25" unitRef="usd" decimals="-3">1000000</ifrs-full:Revenue>'
            '<ifrs-full:ProfitLossAttributableToOwnersOfParent contextRef="d25" unitRef="usd" decimals="-3">'
            '-150000</ifrs-full:ProfitLossAttributableToOwnersOfParent>'
            '<ifrs-full:EquityAttributableToOwnersOfParent contextRef="i25" unitRef="usd" decimals="-3">2200000'
            '</ifrs-full:EquityAttributableToOwnersOfParent></xbrli:xbrl>').encode("utf-8")


@pytest.fixture
def deposito_su_piu_file(monkeypatch, tmp_path):
    import hashlib
    from bellomberg.market_data import sec_edgar
    from tests.test_fonti_ixbrl_esef_sito import ALTRO_CIK, CF_24, CIK, _submissions, ixbrl_20f
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path / "xbrl_cache"))
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: None)
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t, motivo=None: CIK)
    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", lambda cik, **kw: CF_24)
    monkeypatch.setattr(sec_xbrl, "cache_superata", lambda cik: None)
    monkeypatch.setattr(sec_xbrl, "_submissions_sec", lambda cik: _submissions())
    scaricati = []

    def monta(istanza):
        def _scarica(url, cartella):
            scaricati.append(url.rsplit("/", 1)[-1])
            if url.endswith("_htm.xml"):
                if istanza is None:
                    return {"stato": "errore", "motivo": "HTTP 404"}
                dati, nome = istanza, "istanza.xml"
            else:  # documento primario: solo fatti di un'ALTRA entita' (prospetti in un altro file)
                dati, nome = ixbrl_20f(cik=ALTRO_CIK), "primario.htm"
            p = tmp_path / nome
            p.write_bytes(dati)
            return {"stato": "ok", "path": str(p), "sha256": hashlib.sha256(dati).hexdigest()}
        monkeypatch.setattr(sec_xbrl, "_scarica_documento_sec", _scarica)
        return scaricati
    return monta, CIK


def test_prospetti_in_un_altro_file_letti_dall_istanza_dichiarata(deposito_su_piu_file):
    monta, cik = deposito_su_piu_file
    scaricati = monta(_istanza(cik))
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert r["items"]["revenue"][2025] == 1000000.0 and r["items"]["net_income"][2025] == -150000.0
    dep = r["ripiego_ixbrl"]["depositi"][0]
    assert dep["documento_letto"].endswith("zzfonti-20251231_htm.xml")
    assert "istanza XBRL estratta da EDGAR" in dep["fonte"]
    assert scaricati == ["zzfonti-20251231.htm", "zzfonti-20251231_htm.xml"]


def test_istanza_assente_fallimento_dichiarato_con_entrambi_i_motivi(deposito_su_piu_file):
    monta, _ = deposito_su_piu_file
    monta(None)
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert 2025 not in r["items"]["revenue"]
    motivo = r["ripiego_ixbrl"]["motivo"]
    assert "documento primario: iXBRL senza fatti numerici dell'emittente" in motivo and "istanza XBRL: HTTP 404" in motivo


def test_istanza_di_un_altra_entita_non_entra(deposito_su_piu_file):
    from tests.test_fonti_ixbrl_esef_sito import ALTRO_CIK
    monta, _ = deposito_su_piu_file
    monta(_istanza(ALTRO_CIK))
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert 2025 not in r["items"]["revenue"]
    assert "istanza XBRL: iXBRL senza fatti numerici dell'emittente" in r["ripiego_ixbrl"]["motivo"]


@pytest.mark.parametrize("oggi,fine", [("2025-04-15", "2023-12-31"), ("2025-04-16", "2024-12-31")])
def test_trimestri_con_cutoff_futuro_limitati_al_giorno_della_run(oggi, fine):
    # v5: cutoff 2025-12-31 ma run del giorno `oggi`: il 20-F FY2024 depositato il 2025-04-16 non e' ancora noto
    from tests.test_fonti_v3_identita import _cf_annuale
    lp = sec_xbrl.quarterly_history(_cf_annuale()["facts"], fino_al="2025-12-31", oggi=oggi)["latest_period"]
    assert lp["period_end"] == fine


# ---------------------------------------------------------------- v7: il punto dopo una forma giuridica
_Q_V7 = "results for the quarter ended March 31, 2026."


@pytest.mark.parametrize("nome,frase,atteso", [
    ("Zetapark B.V.", f"This report is furnished by Zetapark B.V. Zetapay reported {_Q_V7}", "Zetapay"),
    ("Zetafield Partners L.P.", f"This report is furnished by Zetafield Partners L.P. Zetapay reported {_Q_V7}",
     "Zetapay"),
    ("Zetakabu K.K.", f"This report is furnished by Zetakabu K.K. Zetapay reported {_Q_V7}", "Zetapay"),
    ("Zetabras S.A.A.", f"This report is furnished by Zetabras S.A.A. Zetapay reported {_Q_V7}", "Zetapay"),
    ("Grupo Zetavision, S.A.B. de C.V.",
     f"This report is furnished by Grupo Zetavision, S.A.B. Zetacable reported {_Q_V7}", "Zetacable"),
    ("Zetabras S.A.", f"This report is furnished by Zetabras S.A. Zetapay reported {_Q_V7}", "Zetapay"),
    ("Zeta Holdings Inc.", f"This report is furnished by Zeta Holdings Inc. Zetapay reported {_Q_V7}", "Zetapay"),
], ids=["b_v", "l_p", "k_k", "s_a_a", "s_a_b", "s_a_gia_abbreviazione", "inc_senza_punti"])
def test_v7_forma_seguita_da_maiuscola_chiude_la_frase(tmp_path, nome, frase, atteso):
    motivo, _ = _identita(tmp_path, nome, f"Exhibit 99.1\n{frase}\nHighlights\n")
    assert motivo and atteso in motivo, motivo


@pytest.mark.parametrize("nome,frase", [
    ("Grupo Zetavision, S.A.B. de C.V.", f"GRUPO ZETAVISION, S.A.B. DE C.V. (BMV: ZTV) reported {_Q_V7}"),
    ("Zeta Holdings Inc.", f"Zeta Holdings Inc. The Company reported {_Q_V7}"),
    ("Zeta Holdings Ltd.", "Zeta Holdings Ltd. Revenue rose in the quarter ended March 31, 2026."),
], ids=["sab_de_cv_maiuscolo", "inc_the_company", "ltd_revenue_rose"])
def test_v7_forma_che_continua_il_nome_o_frase_dell_emittente(tmp_path, nome, frase):
    motivo, _ = _identita(tmp_path, nome, f"Exhibit 99.1\n{frase}\nHighlights\n")
    assert motivo is None, motivo
