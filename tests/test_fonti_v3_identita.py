"""R-FONTI 10/10 v3 (Opus 5.5): riserve della revisione v2 (70e6e48), provate su dati SINTETICI (CIK 0009990077,
nomi ZZ* e cifre inventati), nella PIPELINE intera (esegui_profilo) con profilo legacy e nuovo, su 6-K primario
(sequenza 1) e su allegato EX-99.1.

Principio: PRECISIONE prima del richiamo. Un documento di un'altra entita' attribuito all'emittente e' il
difetto grave; un falso rifiuto e' accettabile se dichiarato col motivo.
ALTO R3/R9  prospetto della controllata intestato con l'emittente come controllante (blocco di intestazione) e
            nota 1 dell'emittente in relazione con la controllata: non verificato;
ALTO R2     «<emittente> announces that <altra entita'> released ...», «..., its partner, ...»: non verificato;
MEDIO R4/R5/R10  il documento primario non promuove piu' un segnale solo: servono T e N dell'emittente (o la
            frase del periodo con soggetto l'emittente); periodo «... of its payments partner ...»: non verificato;
MEDIO       companyfacts filtrato per data di deposito con fino_al (nessun look-ahead nello storico annuale),
            deposito dello stesso giorno del cutoff storico escluso.
Rete assente (tripwire del conftest), nessuna AI.
"""
import pytest

from bellomberg.market_data import sec_xbrl
from bellomberg.market_data.filing_verifica import identita_6k
from tests.test_fonti_v2_riserve import (
    BASE_URL, CONTROLLATA, D8, E8, KPMG_ALTRA, PARTNER_NOTA, ALTRA_FINTECH, PROFILO_LEGACY, PROFILO_NUOVO, _esegui,
    _prospetto, _sei_k)

PROFILI = pytest.mark.parametrize("profilo", [PROFILO_LEGACY, PROFILO_NUOVO], ids=["legacy", "nuovo"])
DOCUMENTI = pytest.mark.parametrize("tipo,seq", [("6-K", 1), ("EX-99.1", 2)], ids=["primario", "ex99"])

PRE = _prospetto(nota=False, titolo=False)  # indice, revisore senza nome, tabelle: nessun alias nella testa
TITOLO = ("<p>Unaudited Interim Condensed Consolidated Financial Statements</p>"
          "<p>for the three-month period ended June 30, 2025</p>")
NOTA_EMITTENTE = ("<p>1. OPERATIONS</p><p>Zztest Holdings Ltd. (“Company” or “Zztest”) was incorporated as an "
                  "exempted company. The Company and its consolidated subsidiaries are the “Group”.</p>")

# ---- casi della revisione v2 (descritti dal coordinatore), nomi sintetici
R2 = ("<p>Zztest Holdings announces that Zzbank S.A., its partner, released consolidated results for the three "
      "months ended June 30, 2025.</p>")
R2_SOLO_THAT = ("<p>Zztest Holdings announces that Zzbank S.A. released consolidated results for the three months "
                "ended June 30, 2025.</p>")
R2_APPOSIZIONE = ("<p>Zztest Holdings presents the consolidated results of Zzbank S.A., its subsidiary, for the "
                  "three months ended June 30, 2025.</p>")
R3 = (PRE + "<p>Zztest Pagamentos S.A.</p><p>A subsidiary of</p><p>Zztest Holdings Ltd.</p>" + TITOLO
      + "<p>1. OPERATIONS</p><p>Zztest Pagamentos S.A. (\"Company\") provides payment services.</p>")
# solo il blocco di intestazione tradisce la controllata: la nota 1 e' dell'emittente e pulita
R3_SOLO_BLOCCO = PRE + "<p>Zztest Pagamentos S.A.</p><p>A subsidiary of</p><p>Zztest Holdings Ltd.</p>" + TITOLO + NOTA_EMITTENTE
R9 = (PRE + "<p>Zztest Pagamentos S.A.</p><p>A subsidiary of</p><p>Zztest Holdings Ltd.</p>" + TITOLO
      + "<p>1. OPERATIONS</p><p>Zztest Holdings Ltd. (\"Company\") is the parent of Zztest Pagamentos S.A., whose "
        "statements are presented herein.</p>")
# solo la nota 1 tradisce la controllata: intestazione dell'emittente pulita
R9_SOLO_NOTA = (PRE + "<p>12</p><p>Zztest Holdings Ltd.</p>" + TITOLO
                + "<p>1. OPERATIONS</p><p>Zztest Holdings Ltd. (\"Company\") is the parent of Zztest Pagamentos "
                  "S.A., whose statements are presented herein.</p>")
R4 = (PRE + "<p>Zzinvest Pagamentos</p>" + TITOLO + "<p>Zzinvest Pagamentos</p><p>Unaudited Interim Condensed "
      "Consolidated Statements of Income</p>" + NOTA_EMITTENTE)
R5 = ("<p>Zztest Holdings presents the consolidated financial results for the three months ended June 30, 2025 of "
      "its payments partner Zzpartner S.A.</p>")
R10 = (PRE + "<p>12</p><p>Zztest Holdings Ltd.</p><p>Unaudited Interim Condensed Consolidated Financial "
       "Statements</p><p>for the three-month period ended June 30, 2025 - carve-out of the payments business of "
       "Zzpartner S.A.</p>")
SOLO_T = _prospetto(nota=False)       # v2: verificato sul primario per il solo titolo
SOLO_N = (_prospetto(titolo=False)     # v2: verificato sul primario per la sola nota 1
          + "<p>Unaudited Interim Condensed Consolidated Statements of Income for the three-month period "
            "ended June 30, 2025</p>")

CASI_ALTRA_ENTITA = {
    "R2_that_e_apposizione": (R2, "Zzbank"), "R2_solo_that": (R2_SOLO_THAT, "Zzbank"),
    "R2_apposizione": (R2_APPOSIZIONE, "Zzbank"), "R3": (R3, "Zztest Pagamentos"),
    "R3_solo_blocco": (R3_SOLO_BLOCCO, "Zztest Pagamentos"), "R9": (R9, "Zztest Pagamentos"),
    "R9_solo_nota": (R9_SOLO_NOTA, "parent of"), "R4": (R4, "Zzinvest Pagamentos"),
    "R5": (R5, "Zzpartner"), "R10": (R10, "carve-out"),
    "solo_titolo": (SOLO_T, "servono"), "solo_nota": (SOLO_N, "servono"),
    # casi dell'autore v2 (riproposti anche sul documento primario)
    "D8": (D8, "altro soggetto"), "E8": (E8, "altro soggetto"), "controllata": (CONTROLLATA, "Zztest Pagamentos"),
    "altra_fintech": (ALTRA_FINTECH, "nessun alias"),
    "kpmg_altra_entita": (KPMG_ALTRA.split("<p>Zzother S.A.</p>")[0], "nessun alias"),
    "kpmg_e_prospetti_altra_entita": (KPMG_ALTRA, "Zzother"), "partner_con_nota_1": (PARTNER_NOTA, "Zzpartner"),
}


@PROFILI
@DOCUMENTI
@pytest.mark.parametrize("caso", list(CASI_ALTRA_ENTITA))
def test_pipeline_documento_di_altra_entita_o_segnale_solo_non_verificato(monkeypatch, tmp_path, profilo, tipo,
                                                                         seq, caso):
    corpo, atteso = CASI_ALTRA_ENTITA[caso]
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo), profilo, tipo=tipo, seq=seq)
    assert c["stato"] == "non_applicabile", (caso, c)
    assert any("identita' nel corpo del 6-K non provata" in m and atteso in m for m in c["motivi"]), c["motivi"]
    assert "metadati" not in c


# ---- repliche SINTETICHE delle forme dei documenti reali (6-K di emittenti del book): devono restare verificati
def _pagina(n, testo_prima):
    """Fine pagina come nei prospetti reali: testo della pagina, numero di pagina, intestazione dell'emittente."""
    return f"<p>{testo_prima}</p><p>{n}</p><p>Zztest Holdings Ltd.</p>" + TITOLO


REALE_PROSPETTO = (
    PRE + _pagina(12, "The accompanying notes are an integral part of these unaudited interim condensed "
                      "consolidated financial statements.")
    + NOTA_EMITTENTE
    # pagina successiva: testo con una controllata e la parola «subsidiary» PRIMA del numero di pagina
    + _pagina(13, "• Zztest Colombia Compania de Financiamiento S.A (\"Zztest Colombia\") is an indirect subsidiary "
                  "domiciled in Zzland. Zztest Colombia is engaged in the issuance of credit cards")
    + _pagina(14, "The subsidiaries below are the most relevant entities included in these statements")
    + "<p>The financial statements of the foreign subsidiaries held in functional currencies are translated.</p>")
REALE_COMUNICATO = ("<p>Zztest Holdings Reports Second Quarter 2025 Financial Results</p><p>Zzcity, August 14, 2025 – "
                    "Zztest Holdings Ltd. (NYSE: ZZT), one of the largest digital banking platforms, today reported its "
                    "unaudited results for the second quarter ended on June 30, 2025.</p><p>A Summary of Consolidated "
                    "Financial and Operating Metrics is presented for the three-month periods ended June 30, 2025.</p>")
REALE_CONSOLIDATA = ("<p>Zztest Holdings Ltd. Report on Form 6-K for the three months ended June 30, 2025</p><p>Our "
                     "condensed consolidated financial statements for the three months ended June 30, 2024 have "
                     "accordingly been adjusted, with Zzother being consolidated therein based on its financial "
                     "statements for the same three-month period.</p>")
REALE_NOTA_THE_COMPANY = ("<p>Enclosure: Zztest Holdings Ltd.’s Second Quarter and Six Months ended June 28, 2025:</p>"
                          "<p>1.The Company</p><p>Zztest Holdings Ltd. (the “Company”) is registered in Zzland with "
                          "its corporate legal seat in Zzcity.</p>")


@PROFILI
@DOCUMENTI
@pytest.mark.parametrize("corpo", [_prospetto(), REALE_PROSPETTO, REALE_COMUNICATO, REALE_CONSOLIDATA,
                                   REALE_NOTA_THE_COMPANY],
                         ids=["indice_revisore_T_e_N", "prospetto_con_pagine_e_controllate", "comunicato",
                              "consolidamento_di_altra_entita", "nota_the_company"])
def test_pipeline_repliche_dei_documenti_reali_verificate(monkeypatch, tmp_path, profilo, tipo, seq, corpo):
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo), profilo, tipo=tipo, seq=seq)
    assert c["stato"] == "verificato", c["motivi"]
    assert "documento primario" not in c.get("identita_verifica", "")


def test_prova_strutturale_dichiarata_senza_documento_primario(tmp_path):
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k(REALE_PROSPETTO))
    motivo, prova = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY,
                                catalogo={"documento_sec": "6-K", "documento_seq": 1})
    assert motivo is None and prova == ("segnali strutturali del prospetto: titolo del prospetto intestato "
                                        "all'emittente e nota «1. Operations» dell'emittente")


def test_intestazione_sgml_del_primario_non_promuove_un_segnale_solo(tmp_path):
    # v2: l'intestazione SGML «6-K / sequenza 1» sotto il CIK del profilo bastava col solo titolo
    sgml = b"<DOCUMENT>\n<TYPE>6-K\n<SEQUENCE>1\n<FILENAME>zz.htm\n<TEXT>\n"
    p = tmp_path / "zz.htm"
    p.write_bytes(sgml + _sei_k(SOLO_T))
    motivo, _ = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY)
    assert motivo and "il documento primario del deposito non basta" in motivo


@pytest.mark.parametrize("corpo", [
    # la testa del corpo nomina l'emittente, ma i prospetti sono della controllata: non basta la testa
    "<p>Zztest Holdings Ltd. - Exhibit 99.1</p>" + R3_SOLO_BLOCCO,
    "<p>Zztest Holdings Ltd. - Exhibit 99.1</p><p>1. OPERATIONS</p><p>Zzpartner S.A. (\"Company\") is a payments "
    "company.</p><p>For the three-month period ended June 30, 2025 the consolidated revenue grew.</p>"],
    ids=["testa_emittente_prospetto_controllata", "testa_emittente_nota_1_partner"])
def test_testa_dell_emittente_non_copre_prospetti_di_altra_entita(tmp_path, corpo):
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k(corpo))
    motivo, _ = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY)
    assert motivo and "di altra entita'" in motivo


@pytest.mark.parametrize("corpo", [
    # persona che dichiara, non un'entita': «that» con la citazione del CEO
    "<p>Zztest Holdings reported results for the quarter ended June 30, 2025. CEO John Zzsmith said that the "
    "quarter was strong.</p>",
    # gruppo consolidato: «and its subsidiaries» (plurale) non e' una relazione con un'altra entita'
    "<p>Zztest Holdings and its subsidiaries reported consolidated results for the three months ended June 30, "
    "2025.</p>"], ids=["citazione_ceo", "gruppo_e_controllate"])
def test_frasi_vere_dell_emittente_non_rifiutate(tmp_path, corpo):
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k(corpo))
    motivo, _ = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY)
    assert motivo is None, motivo


# ================================================================== MEDIO: look-ahead di companyfacts con fino_al
def _ob(fy, val, filed, form="20-F"):
    return {"start": f"{fy}-01-01", "end": f"{fy}-12-31", "val": val, "form": form, "fp": "FY", "filed": filed,
            "accn": f"0009990077-{str(fy)[2:]}-0000{fy % 10}"}


def _cf_annuale():
    # FY2024 depositato il 2025-04-16: con fino_al=2025-03-01 NON deve comparire (prima della v3 compariva)
    obs = [_ob(2022, 111, "2023-04-20"), _ob(2023, 222, "2024-04-18"), _ob(2024, 333, "2025-04-16")]
    eq = [{**o, "start": None, "val": o["val"] * 10} for o in obs]
    return {"facts": {"ifrs-full": {"Revenue": {"units": {"USD": obs}},
                                   "ProfitLoss": {"units": {"USD": [dict(o, val=o["val"] // 3) for o in obs]}},
                                   "Equity": {"units": {"USD": eq}}}}, "entityName": "Zztest Holdings Ltd."}


@pytest.fixture
def cf_annuale(monkeypatch):
    from bellomberg.market_data import sec_edgar
    dati = _cf_annuale()
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t: "0009990077")
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: None)
    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", lambda cik, **k: dati)
    # il ripiego iXBRL non deve andare in rete: elenco vuoto
    monkeypatch.setattr(sec_xbrl, "_submissions_sec", lambda cik: {"filings": {"recent": {}}})
    return dati


def test_storico_annuale_con_fino_al_senza_depositi_successivi(cf_annuale):
    h = sec_xbrl.get_financial_history("ZZTEST", fino_al="2025-03-01")
    assert 2024 not in h["items"]["revenue"] and h["items"]["revenue"][2023] == 222
    assert h["years"][-1] == 2023
    assert h["latest_period"]["period_end"] == "2023-12-31"
    # senza cutoff (run corrente) il FY2024 c'e'
    assert sec_xbrl.get_financial_history("ZZTEST")["items"]["revenue"][2024] == 333


def test_ultima_fine_annuale_del_ripiego_vede_solo_i_depositi_entro_il_cutoff(cf_annuale, monkeypatch):
    viste = []
    vero = sec_xbrl.ripiego_ixbrl_annuale

    def spia(cik, facts, fino_al=None, oggi=None):
        viste.append(sec_xbrl._ultima_fine_annuale(facts))
        return vero(cik, facts, fino_al=fino_al, oggi=oggi)
    monkeypatch.setattr(sec_xbrl, "ripiego_ixbrl_annuale", spia)
    sec_xbrl.get_financial_history("ZZTEST", fino_al="2025-03-01")
    assert viste == ["2023-12-31"]


def test_deposito_del_giorno_del_cutoff_storico_escluso(cf_annuale):
    # cutoff storico (giorno passato) uguale alla data di deposito: senza ora non era noto alla run -> escluso
    h = sec_xbrl.get_financial_history("ZZTEST", fino_al="2025-04-16")
    assert 2024 not in h["items"]["revenue"]
    assert sec_xbrl.get_financial_history("ZZTEST", fino_al="2025-04-17")["items"]["revenue"][2024] == 333


def test_noto_al_cutoff_regola_dichiarata():
    assert sec_xbrl.depositato_entro("2025-04-15", "2025-04-16", oggi="2026-10-10")
    assert not sec_xbrl.depositato_entro("2025-04-16", "2025-04-16", oggi="2026-10-10")
    # cutoff = oggi (run corrente): cio' che companyfacts riporta adesso e' gia' pubblicato
    assert sec_xbrl.depositato_entro("2026-10-10", "2026-10-10", oggi="2026-10-10")
    assert not sec_xbrl.depositato_entro(None, "2025-04-16", oggi="2026-10-10")
    assert sec_xbrl.depositato_entro("2099-01-01", None, oggi="2026-10-10")
