"""R-FONTI 10/10 v2 (Opus 5.5): riserve del revisore sulla prima versione (9b355fa), provate su dati SINTETICI
(CIK 0009990077/0009990042, LEI 999900ZZFONTI0000042, nomi ZZ* e cifre inventati).

ALTO-1   identita' dei 6-K: la guardia nel CORPO vale per OGNI 6-K verificato con regole testuali (profilo nuovo,
         automatico aggiornato o legacy, ripiego standard), e riconosce il 6-K legittimo che si apre con indice e
         relazione del revisore (segnali strutturali del prospetto; v3: titolo E nota 1, il documento primario
         non basta piu', vedi test_fonti_v3_identita);
MEDIO-2  anno sovrapposto: vale il deposito piu' recente (comparativo rideterminato), dichiarato fino al contesto
         e alla ricevuta del tool, con la discontinuita' della serie;
MEDIO-3  memoria dei fallimenti del ripiego iXBRL (accession + sha256, errore dell'elenco), cache per fino_al;
MEDIO-4  storico ESEF dal sito: solo pacchetti dell'esercizio annuale, periodo controllato nei fatti;
BASSI    duplicati coerenti per arrotondamento, classifica_pdf (30 giugno, 1H, remunerazione), tripwire di rete.
Rete assente (tripwire del conftest), nessuna AI.
"""
import hashlib
import json
import time
from datetime import datetime

import pytest

from bellomberg.market_data import esef, esef_sito, filing_pipeline, sec_edgar, sec_xbrl
from bellomberg.market_data.esef_sito import classifica_pdf
from bellomberg.market_data.filing_numeri import numeri_per_coppia
from bellomberg.market_data.filing_profili_auto import REGOLE_6K_LEGACY, aggiorna_regole_6k
from bellomberg.market_data.filing_verifica import PERIODO_6K_STANDARD, TIPO_6K_STANDARD, identita_6k
from tests.test_filing_pipeline import rete
from tests.test_fonti_ixbrl_esef_sito import (  # noqa: F401  (fixture importate: storico, esef_finto, cf_fermo)
    ACCN, CF_24, CIK as CIK_FONTI, DUR, IST, LEI, RIGA_SITO, _cf, _coppia, _ctx, _doc, _durata, _nf, _oim, _repo,
    _submissions, cf_fermo, esef_finto, ixbrl_20f, storico)

CIK = "0009990077"
BASE_URL = "https://www.sec.gov/Archives/edgar/data/9990077/"
REGISTRANTE = "Zztest Holdings Ltd."
# profilo 6-K con le regole SALVATE dal generatore automatico prima del 06/10 (come i profili legacy del DB)
PROFILO_LEGACY = {"ticker": "ZZTEST", "emittente_id": "CIK:" + CIK, "cik": CIK, "nome": "Zztest Holdings",
                  "lingua": "en", "tipo": "trimestrale", "perimetro": "consolidato", "fonti": ["sec"],
                  "forme_sec": ["6-K"], "sezioni": {}, "sezioni_intero": True, "periodo_regola": "piu_recente",
                  "stesso_periodo": "piu_lungo", "origine_collegamento": "confermato_utente",
                  "verifica": {"lingua": r"\b(?:the|and|of)\b", "perimetro": "consolidated",
                               "tipo": REGOLE_6K_LEGACY["tipo"], "emittente": r"\bzztest\s+holdings\b",
                               "periodo": REGOLE_6K_LEGACY["trimestrale"]}}
# profilo nuovo (regole correnti del generatore): stesso buco prima della v2
PROFILO_NUOVO = {**PROFILO_LEGACY, "verifica": {**PROFILO_LEGACY["verifica"], "tipo": TIPO_6K_STANDARD,
                                                "periodo": PERIODO_6K_STANDARD["trimestrale"]}}


def _sei_k(corpo, registrante=REGISTRANTE):
    return ('<html lang="en"><body><p>FORM 6-K</p><p>For the month of August, 2025</p>'
            f'<p>{registrante}</p><p>(Exact name of registrant as specified in its charter)</p>'
            '<p>Indicate by check mark whether the registrant files annual reports under cover of Form 20-F or '
            f'Form 40-F.</p>{corpo}<p>All figures are consolidated.</p></body></html>').encode()


# ---- corpi dei casi del revisore (sotto la copertina del registrante, CIK del titolo)
D8 = ("<p>Zztest Holdings - Exhibit 99.1</p><p>Zzpartner, the payments partner of Zztest Holdings, today released "
      "its consolidated financial results for the three months ended June 30, 2025.</p>")
E8 = ("<p>Zzpartner Reports Second Quarter Results</p><p>Zztest Holdings' payments partner Zzpartner today released "
      "its consolidated financial results for the three months ended June 30, 2025.</p>")
CONTROLLATA = ("<p>Zztest Pagamentos S.A., a subsidiary of Zztest Holdings, today released its consolidated "
               "financial results for the three months ended June 30, 2025.</p>")
ALTRA_FINTECH = ("<p>Zzfintech Inc. Reports Second Quarter 2025 Results</p><p>Zzfintech Inc. today released its "
                 "consolidated financial results for the three months ended June 30, 2025.</p>")
KPMG_ALTRA = ("<p>KPMG Zzrevisori Ltda.</p><p>Independent Auditors' report on review of interim "
              "condensed consolidated financial statements</p><p>To the Board of Directors and Shareholders of "
              "Zzother S.A.</p><p>We have reviewed the accompanying interim condensed consolidated statement of "
              "financial position of Zzother S.A. as of June 30, 2025, and the consolidated statement of profit or "
              "loss for the three months ended June 30, 2025.</p>"
              # prospetti e nota 1 DELL'ALTRA entita', che cita il registrante come controllante
              "<p>Zzother S.A.</p><p>Unaudited Interim Condensed Consolidated Financial Statements</p>"
              "<p>for the three-month period ended June 30, 2025</p><p>1. OPERATIONS</p><p>Zzother S.A. (\"Company\"), "
              "a subsidiary of Zztest Holdings Ltd., provides payment services.</p>")
PARTNER_NOTA = ("<p>Zzpartner Results</p><p>For the three-month period ended June 30, 2025 the consolidated revenue "
                "grew.</p><p>1. OPERATIONS</p><p>Zzpartner S.A. (\"Company\") is the payments partner of Zztest "
                "Holdings Ltd.</p><p>Zzpartner S.A.</p><p>Unaudited Interim Condensed Consolidated Financial "
                "Statements</p><p>for the three-month period ended June 30, 2025</p>")


def _prospetto(nota=True, titolo=True):
    """6-K legittimo come quello reale del Q1 2026: indice, conclusione del revisore senza il nome, prospetti
    numerici; il nome dell'emittente arriva solo dopo ~6.000 battute (oltre la testa del corpo)."""
    righe = "".join(f"<tr><td>Line item {i}</td><td>{100 + i}</td><td>{90 + i}</td></tr>" for i in range(260))
    corpo = ("<p>Contents Page</p><p>Unaudited Interim Condensed Consolidated Statements of Income 5</p>"
             "<p>Notes to the Unaudited Interim Condensed Consolidated Financial Statements 13</p>"
             "<p>Conclusion</p><p>Based on our review, nothing has come to our attention that causes us to believe "
             "that the accompanying interim condensed consolidated financial statements as at June 30, 2025, are not "
             "prepared, in all material respects, in accordance with IAS 34.</p><p>KPMG Zzrevisori "
             f"Ltda.</p><table>{righe}</table>")
    if titolo:
        corpo += ("<p>Zztest Holdings Ltd.</p><p>Unaudited Interim Condensed Consolidated Financial Statements</p>"
                  "<p>for the three-month period ended June 30, 2025</p>")
    if nota:
        corpo += ("<p>1. OPERATIONS</p><p>Zztest Holdings Ltd. (“Company” or “Zztest”) was incorporated as an "
                  "exempted company. The Company and its consolidated subsidiaries are the “Group”.</p>")
    return corpo


def _esegui(monkeypatch, tmp_path, html, profilo=PROFILO_LEGACY, tipo="EX-99.1", seq=2):
    """Pipeline intera (esegui_profilo) su UN 6-K sintetico sotto il CIK del titolo."""
    acc = "0009990077-29-000009"
    url = BASE_URL + acc.replace("-", "") + "/zz-q2-interim-results.htm"
    righe = [{"ticker": "ZZTEST", "form": "6-K", "filed_date": "2025-08-14", "accession": acc, "url": url,
              "emittente_id": "CIK:" + CIK, "issuer": "Synthetic", "report_date": "", "items": [],
              "fonte": "SEC EDGAR"}]
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", lambda *a, **k: {"stato": "ok", "motivi": [],
                                                                         "documenti": righe})
    monkeypatch.setattr(sec_edgar, "allegati_filing", lambda cik, a, **k: [
        {"seq": seq, "descrizione": tipo, "tipo": tipo, "ixbrl": False, "url": url}])
    rete(monkeypatch, {url: html})
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "a", oggi="2025-10-31")
    (c,) = out["candidati"]
    return c


@pytest.mark.parametrize("profilo", [PROFILO_LEGACY, PROFILO_NUOVO], ids=["legacy", "nuovo"])
@pytest.mark.parametrize("corpo,atteso", [
    (D8, "altro soggetto"), (E8, "altro soggetto"), (CONTROLLATA, "Zztest Pagamentos"),
    (ALTRA_FINTECH, "nessun alias"), (KPMG_ALTRA.split("<p>Zzother S.A.</p>")[0], "nessun alias"),
    (KPMG_ALTRA, "Zzother"), (PARTNER_NOTA, "Zzpartner")],
    ids=["D8", "E8", "controllata", "altra_fintech", "kpmg_altra_entita", "kpmg_e_prospetti_altra_entita",
         "partner_con_nota_1"])
def test_pipeline_6k_di_altra_entita_sotto_profilo_non_verificato(monkeypatch, tmp_path, profilo, corpo, atteso):
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo), profilo)
    assert c["stato"] != "verificato" and c["stato"] == "non_applicabile", c
    assert any("identita' nel corpo del 6-K non provata" in m and atteso in m for m in c["motivi"]), c["motivi"]
    assert "metadati" not in c


def test_pipeline_6k_altra_entita_era_verificato_prima_della_guardia(monkeypatch, tmp_path):
    # la prova che la guardia serve: senza identita_6k il profilo (copertina del registrante) basta
    from bellomberg.market_data import filing_verifica
    monkeypatch.setattr(filing_verifica, "identita_6k", lambda *a, **k: (None, "guardia spenta"))
    assert _esegui(monkeypatch, tmp_path, _sei_k(D8))["stato"] == "verificato"


@pytest.mark.parametrize("tipo,seq", [("6-K", 1), ("EX-99.1", 2)])
def test_pipeline_6k_vero_con_indice_e_revisore_verificato(monkeypatch, tmp_path, tipo, seq):
    # titolo del prospetto E nota «1. Operations» dell'emittente: basta anche in un allegato EX-99
    c = _esegui(monkeypatch, tmp_path, _sei_k(_prospetto()), tipo=tipo, seq=seq)
    assert c["stato"] == "verificato", c["motivi"]
    assert c["metadati"]["periodo_fine"] == "2025-06-30"
    assert "segnali strutturali del prospetto" in c["identita_verifica"]
    # v3: il documento primario non e' piu' una prova d'identita' (servono titolo E nota 1 dell'emittente)
    assert "documento primario" not in c["identita_verifica"]


@pytest.mark.parametrize("tipo,seq", [("6-K", 1), ("EX-99.1", 2)])
def test_pipeline_un_solo_segnale_non_basta_nemmeno_nel_documento_primario(monkeypatch, tmp_path, tipo, seq):
    # v3 (riserva MEDIO R4/R5/R10 della revisione v2): il documento primario non promuove un segnale solo
    c = _esegui(monkeypatch, tmp_path, _sei_k(_prospetto(nota=False)), tipo=tipo, seq=seq)
    assert c["stato"] == "non_applicabile"
    assert any("titolo del prospetto" in m and "il documento primario del deposito non basta" in m
               for m in c["motivi"]), c["motivi"]


def test_pipeline_documento_primario_con_registrante_di_un_altra_societa(monkeypatch, tmp_path):
    html = _sei_k(_prospetto(nota=False), registrante="Zzaltra Fintech Inc.")
    c = _esegui(monkeypatch, tmp_path, html, tipo="6-K", seq=1)
    assert c["stato"] != "verificato", c


@pytest.mark.parametrize("riga", ["Zzpartner S.A., the payments partner of Zztest Holdings Ltd.",
                                  "Zztest Holdings Ltd.'s payments partner Zzpartner",
                                  "A subsidiary of Zztest Holdings Ltd.",
                                  # senza forma giuridica a fine riga: solo l'ancora a inizio riga lo esclude
                                  "Zzpartner report prepared for its partner Zztest Holdings"])
def test_segnale_strutturale_non_vale_con_l_emittente_come_partner_o_controllante(tmp_path, riga):
    corpo = _prospetto(nota=False, titolo=False) + (
        f"<p>{riga}</p><p>Unaudited Interim Condensed Consolidated Financial Statements</p>"
        "<p>for the three-month period ended June 30, 2025</p>"
        f"<p>1. OPERATIONS</p><p>{riga} (\"Company\") provides services.</p>")
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k(corpo))
    motivo, _ = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY,
                            catalogo={"documento_sec": "6-K", "documento_seq": 1})
    assert motivo, riga


def test_prospetti_intestati_anche_a_un_altra_entita_non_bastano(tmp_path):
    corpo = _prospetto() + ("<p>Zzsub Pagamentos S.A.</p><p>Unaudited Interim Condensed Consolidated Financial "
                            "Statements</p><p>for the three-month period ended June 30, 2025</p>")
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k(corpo))
    motivo, _ = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY,
                            catalogo={"documento_sec": "6-K", "documento_seq": 1})
    assert motivo and "Zzsub Pagamentos" in motivo


@pytest.mark.parametrize("corpo", [
    # forme REALI dei 6-K di un emittente del book (nomi sintetici): con la guardia su ogni 6-K erano falsi rifiuti
    "<p>Enclosure: Zztest Holdings Ltd.’s Second Quarter and Six Months ended June 27, 2026:</p><p>Unaudited Interim "
    "Consolidated Statements of Income for the three and six months ended June 27, 2026.</p><p>There were no material "
    "changes in the critical accounting policies during the three and six months ended June 27, 2026.</p>",
    "<p>Zztest Holdings Reports Q2 2026 Financial Results</p><p>Zzcity, July 23, 2026 – Zztest Holdings Ltd. "
    "(NYSE: ZZT), a global leader serving</p><p>customers across the world, reported U.S. GAAP financial results for the second "
    "quarter</p><p>ended June 27, 2026. This press release also contains non-U.S. GAAP measures.</p>",
    "<p>KPMG Zzrevisori Ltda.</p><p>To the Shareholders and Board of Directors of Zztest Holdings Ltd.</p>"
    "<p>We have reviewed the interim condensed consolidated financial statements for the three months ended June 30, "
    "2026.</p>",
    # «U.S.» non chiude la frase: «Dollar» non e' un soggetto
    "<p>Zztest Holdings reported U.S. Dollar results for the quarter ended June 30, 2026.</p>"],
    ids=["enclosure_e_there", "us_gaap", "revisore_del_registrante", "us_dollar"])
def test_6k_veri_del_registrante_non_rifiutati_dalla_guardia(tmp_path, corpo):
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k(corpo))
    motivo, prova = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY)
    assert motivo is None, motivo


def test_etichetta_enclosure_non_nasconde_il_partner(tmp_path):
    # tolta l'etichetta, il soggetto e' il registrante col possessivo «'s payments partner Zzpartner»: altro soggetto
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k("<p>Enclosure: Zztest Holdings' payments partner Zzpartner today released its consolidated "
                         "results for the quarter ended June 30, 2025.</p>"))
    motivo, _ = identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY)
    assert motivo and "Zzpartner" in motivo


@pytest.mark.parametrize("corpo,verificato", [(_prospetto(), True), (E8, False), (ALTRA_FINTECH, False)],
                         ids=["vero_indice_revisore", "E8", "altra_fintech"])
def test_profilo_senza_nome_usa_la_regola_emittente(monkeypatch, tmp_path, corpo, verificato):
    # profilo scritto a mano senza «nome»: il nome viene dal testo che la regola «emittente» riconosce
    profilo = {k: v for k, v in PROFILO_LEGACY.items() if k != "nome"}
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo), profilo, tipo="6-K", seq=1)
    assert (c["stato"] == "verificato") == verificato, c


def test_nota_regole_legacy_non_dice_identita_invariata():
    _, nota = aggiorna_regole_6k(PROFILO_LEGACY)
    assert "invariata" not in nota and "corpo" in nota


def test_regola_legacy_semestrale_aggiornata():
    vecchio = {**PROFILO_LEGACY, "tipo": "semestrale",
               "verifica": {**PROFILO_LEGACY["verifica"], "periodo": REGOLE_6K_LEGACY["semestrale"]}}
    nuovo, nota = aggiorna_regole_6k(vecchio)
    assert nuovo["verifica"]["periodo"] == PERIODO_6K_STANDARD["semestrale"] and "tipo e periodo" in nota
    assert REGOLE_6K_LEGACY["semestrale"].startswith(r"(?P<mesi>six|6)\s+months\s+ended\s+")


# ================================================================== MEDIO-2: anno sovrapposto

def test_storico_comparativo_rideterminato_vale_il_deposito_recente_e_si_dichiara(storico):
    storico(ixbrl_20f(ricavi_24="790"))
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert r["items"]["revenue"] == {2023: 700000, 2024: 790000.0, 2025: 1000000.0}
    (rid,) = r["rideterminazioni"]
    assert (rid["voce"], rid["anno"], rid["valore_precedente"], rid["valore_usato"]) == ("revenue", 2024, 800000,
                                                                                         790000.0)
    assert rid["fonte_precedente"] == "SEC companyfacts" and rid["fonte_usata"].startswith("iXBRL del deposito")
    assert r["ripiego_ixbrl"]["rideterminazioni"] == r["rideterminazioni"]
    assert r["discontinuita"]["revenue"]["tra"] == [2023, 2024]
    assert r["_source"].startswith(f"iXBRL del deposito {ACCN} convertito in locale: FY2025")
    assert "1 comparativi rideterminati" in r["_source"].split(" + SEC XBRL companyfacts")[0]
    assert r["derived"]["net_margin"][2024] == round(120000 / 790000, 4)  # derivate sulla stessa base


def test_storico_arrotondamento_non_e_rideterminazione(monkeypatch, storico):
    cf = _cf({"Revenue": [("2023-01-01", "2023-12-31", 700000), ("2024-01-01", "2024-12-31", 800400)],
              "ProfitLossAttributableToOwnersOfParent": [("2024-01-01", "2024-12-31", 120000)],
              "EquityAttributableToOwnersOfParent": [(None, "2024-12-31", 1900000)]})
    storico()
    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", lambda cik, **kw: cf)
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert r["items"]["revenue"][2024] == 800400  # 800 migliaia (decimals=-3) = 800.400 entro 500
    assert "rideterminazioni" not in r and r["ripiego_ixbrl"]["rideterminazioni"] == []


def test_numeri_comparativo_rideterminato_nel_documento(tmp_path, cf_fermo):
    out = numeri_per_coppia(CIK_FONTI, _coppia(_doc(tmp_path, ixbrl_20f(ricavi_24="790"))))
    ricavi = next(v for v in out["voci"] if v["voce"] == "ricavi")
    assert (ricavi["prima"], ricavi["dopo"]) == (790000.0, 1000000.0)  # stessa base dei due lati
    assert ricavi["fonte_prima"].startswith("iXBRL") and ricavi["rideterminato"]["valore_companyfacts"] == 800000
    (rid,) = out["rideterminazioni"]
    assert rid["voce"] == "ricavi" and rid["valore_precedente"] == 800000
    assert any("rideterminati" in a for a in out["avvisi"])
    utile = next(v for v in out["voci"] if v["voce"] == "utile_netto")
    assert utile["fonte_prima"] == "SEC companyfacts" and "rideterminato" not in utile
    from bellomberg.agents.filing_context import _riga_numeri
    riga = _riga_numeri(out)
    assert "rideterminati nel deposito piu' recente" in riga and "ricavi" in riga.split("rideterminati")[1]


def test_ricevuta_del_tool_nomina_il_ripiego_in_testa(monkeypatch):
    from bellomberg.agents import chat_tools
    from bellomberg.market_data import freschezza_trimestrale as ft
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: {"stato": "non_disponibile", "ticker": t})
    pagina = {"ticker": "ZZFONTI", "items": {}, "ripiego_ixbrl": {
        "anni_per_voce": {"revenue": [2025]}, "depositi": [{"fonte": sec_xbrl.fonte_ixbrl(ACCN)}],
        "rideterminazioni": [{"fonte_usata": sec_xbrl.fonte_ixbrl(ACCN), "voce": "receivables"}]}}
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: pagina)
    r = chat_tools.dispatch("get_financial_history", {"ticker": "ZZFONTI"})
    assert r["_source"].startswith(f"iXBRL del deposito {ACCN} convertito in locale: FY2025")
    assert "1 comparativi rideterminati" in r["_source"] and r["_source"].endswith("SEC XBRL companyfacts (ZZFONTI)")
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: {
        "ticker": "ZZFONTI", "items": {}, "ripiego_ixbrl": {"anni_per_voce": {}, "depositi": [],
                                                            "rideterminazioni": []}})
    assert chat_tools.dispatch("get_financial_history", {"ticker": "ZZFONTI"})["_source"] == \
        "SEC XBRL companyfacts (ZZFONTI)"


# ================================================================== MEDIO-3: cache del ripiego

def test_conversione_fallita_ricordata_per_accession_e_sha(storico):
    conta = storico(ixbrl_20f(cik="0009990043"))  # iXBRL di un altro emittente: conversione rifiutata
    r1 = sec_xbrl.get_financial_history("ZZFONTI")
    assert r1["ripiego_ixbrl"]["stato"] == "errore" and "0009990042" in r1["ripiego_ixbrl"]["motivo"]
    r2 = sec_xbrl.get_financial_history("ZZFONTI")
    assert conta == {"submissions": 1, "download": 1}  # niente secondo download da 80 MB
    assert "gia' fallito" in r2["ripiego_ixbrl"]["motivo"] and "sha256" in r2["ripiego_ixbrl"]["motivo"]


def test_errore_dell_elenco_ricordato(monkeypatch, storico):
    conta = storico()
    chiamate = []

    def _sub(cik):
        chiamate.append(cik)
        raise ConnectionError("SEC giu'")
    monkeypatch.setattr(sec_xbrl, "_submissions_sec", _sub)
    r1 = sec_xbrl.get_financial_history("ZZFONTI")
    r2 = sec_xbrl.get_financial_history("ZZFONTI")
    assert len(chiamate) == 1 and conta["download"] == 0
    assert "ConnectionError" in r1["ripiego_ixbrl"]["motivo"] and "non ritentato" in r2["ripiego_ixbrl"]["motivo"]


def test_fino_al_mai_look_ahead_anche_dalla_cache(storico):
    conta = storico()
    prima = sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"], fino_al="2026-03-01")
    assert prima["stato"] == "nessun_deposito" and conta["download"] == 0  # 20-F depositato l'08/04/2026
    oggi = sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"])
    assert oggi["stato"] == "ok" and oggi["depositi"][0]["accn"] == ACCN
    dopo = sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"], fino_al="2026-03-01")
    assert dopo["stato"] == "nessun_deposito" and dopo["depositi"] == []  # cache piena, cutoff rispettato
    # v3: il giorno stesso del deposito con un cutoff storico e' escluso (data senza ora), il giorno dopo c'e'
    assert sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"], fino_al="2026-04-08")["stato"] == "nessun_deposito"
    assert sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"], fino_al="2026-04-09")["stato"] == "ok"
    assert conta == {"submissions": 1, "download": 1}  # un solo elenco per tutti i fino_al
    r = sec_xbrl.get_financial_history("ZZFONTI", fino_al="2026-03-01")
    assert 2025 not in r["items"]["revenue"]


def test_elenco_riletto_dopo_il_ttl(storico):
    conta = storico()
    sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"])
    sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"])
    assert conta["submissions"] == 1
    path = sec_xbrl._ripiego_path(CIK_FONTI)
    cache = json.loads(open(path, encoding="utf-8").read())
    cache["elenco_letto_at"] = time.time() - sec_xbrl.RIPIEGO_TTL_S - 5
    open(path, "w", encoding="utf-8").write(json.dumps(cache))
    sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"])
    assert conta == {"submissions": 2, "download": 1}  # elenco riletto, conversione dalla cache


@pytest.mark.parametrize("oggi,necessario", [(datetime(2025, 12, 20), False), (datetime(2026, 4, 10), True)])
def test_soglia_dell_esercizio_atteso(storico, oggi, necessario):
    conta = storico()
    out = sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"], oggi=oggi)
    assert (out["stato"] != "non_necessario") == necessario and (conta["submissions"] == 1) == necessario
    assert sec_xbrl.GIORNI_ESERCIZIO_ATTESO == 400


def test_deposito_dell_esercizio_gia_in_companyfacts_escluso(storico):
    conta = storico(submissions=_submissions(report="2024-12-31"))  # il 20-F FY2024, gia' in companyfacts
    out = sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"], oggi=datetime(2026, 10, 10))
    assert out["stato"] == "nessun_deposito" and conta["download"] == 0


def test_identificatore_con_altro_schema_e_stesso_numero_escluso():
    from bellomberg.market_data import ixbrl_oim
    import io
    altro = _ctx("o25", _durata("2025-01-01", "2025-12-31")).replace("http://www.sec.gov/CIK",
                                                                    "http://www.zzaltro.example/id")
    doc = ixbrl_20f(extra=_nf("ro", "ifrs-full:GrossProfit", "7,777", "o25")).replace(
        b"</ix:resources>", altro.encode() + b"</ix:resources>")
    letto = sec_xbrl.fatti_ixbrl(ixbrl_oim.converti_xhtml(io.BytesIO(doc)), CIK_FONTI)
    assert "GrossProfit" not in letto["facts"]["ifrs-full"] and letto["altre_entita"] >= 2


# ================================================================== MEDIO-4: storico ESEF dal sito

def test_pacchetto_semestrale_dal_sito_escluso_e_dichiarato(esef_finto):
    riga = {**RIGA_SITO, "period_end": "2025-06-30",
            "json_url": RIGA_SITO["json_url"].replace("2025-12-31", "2025-06-30")}
    stato = esef_finto(_repo(), [riga], _oim([("Revenue", "2025-01-01T00:00:00/2025-07-01T00:00:00", 300e6),
                                              ("Assets", "2025-07-01T00:00:00", 1900e6)]))
    r = esef.get_esef_history("ZZF.MI")
    assert stato["scaricati"] == 0 and r["years"][-1] == 2024 and r["items"]["total_assets"] == {2024: 1820e6}
    assert r["sito_emittente"]["stato"] == "nessun_pacchetto" and "2025-06-30" in r["sito_emittente"]["motivo"]
    assert "semestrale" in r["sito_emittente"]["motivo"]


def test_pacchetto_col_nome_2025_e_fatti_2024_rifiutato(esef_finto):
    esef_finto(_repo("2023-12-31", 2023), [RIGA_SITO], _oim([("Revenue", DUR[2024], 500e6),
                                                              ("Assets", IST[2024], 1820e6)]))
    r = esef.get_esef_history("ZZF.MI")
    assert 2025 not in r["years"] and r["sito_emittente"]["stato"] == "errore"
    assert "chiude il 2025-12-31" in r["sito_emittente"]["motivo"] and "2024-12-31" in r["sito_emittente"]["motivo"]


def test_storico_esef_discontinuita_dichiarata(esef_finto):
    from tests.test_fonti_ixbrl_esef_sito import FY25_SITO
    repo = _repo()
    repo["filings"]["7000"] = {"period_end": "2023-12-31", "date_added": "2024-03-18", "extract_error": None,
                               "facts": {"Assets": [[2023, 1700e6, "EUR"]]}}
    esef_finto(repo, [RIGA_SITO], _oim(FY25_SITO))
    r = esef.get_esef_history("ZZF.MI")
    assert r["items"]["total_assets"] == {2023: 1700e6, 2024: 1815e6, 2025: 2000e6}
    assert r["discontinuita"]["total_assets"]["tra"] == [2023, 2024] and "revenue" not in r["discontinuita"]


def test_due_esercizi_mancanti_ammessi(esef_finto):
    # repository fermo al FY2023: il pacchetto FY2025 (24 mesi dopo) e' un esercizio annuale
    from tests.test_fonti_ixbrl_esef_sito import FY25_SITO
    esef_finto(_repo("2023-12-31", 2023), [RIGA_SITO], _oim(FY25_SITO))
    r = esef.get_esef_history("ZZF.MI")
    assert r["years"][-1] == 2025 and r["sito_emittente"]["stato"] == "ok"


# ================================================================== BASSI

def test_duplicati_coerenti_per_arrotondamento_non_incoerenti(tmp_path, cf_fermo):
    preciso = ('<ix:nonFraction id="u25p" name="ifrs-full:ProfitLossAttributableToOwnersOfParent" contextRef="d25" '
               'unitRef="usd" decimals="0" format="ixt:num-dot-decimal">150,123</ix:nonFraction>')
    out = numeri_per_coppia(CIK_FONTI, _coppia(_doc(tmp_path, ixbrl_20f(extra=preciso))))
    utile = next(v for v in out["voci"] if v["voce"] == "utile_netto")
    assert utile["dopo"] == 150123.0 and "incoerenti" not in out  # 150 migliaia (decimals=-3) ~ 150.123
    lontano = preciso.replace("150,123", "150,623")  # 623 oltre la mezza unita' (500): incoerente
    out = numeri_per_coppia(CIK_FONTI, _coppia(_doc(tmp_path, ixbrl_20f(extra=lontano), "b.htm")))
    assert "utile_netto" not in {v["voce"] for v in out["voci"]} and out["incoerenti"][0]["valori"] == [
        150000.0, 150623.0]


B = "https://www.zzfonti.example/files/investors/"


@pytest.mark.parametrize("nome,pagina,tipo,ammesso", [
    ("Relazione_Finanziaria_Bilancio_30_giugno_2026.pdf", None, "semestrale", True),
    ("ZZ_Bilancio_30_giugno_2026.pdf", None, "semestrale", True),
    ("Relazione_finanziaria_annuale_30_giugno_2025.pdf", None, "annuale", True),  # esercizio a giugno, esplicito
    ("ZZ_1H_2026_Report.pdf", None, "semestrale", True),
    ("ZZ_1H_2026.pdf", None, "semestrale", False),  # nome generico: decide la prima pagina
    ("Annual_Report_2025.pdf", "Remuneration Report 2025 - report on remuneration policy", "da_confermare", False),
    ("Relazione_finanziaria_annuale_2025.pdf", "Relazione sulla politica in materia di remunerazione 2025",
     "da_confermare", False),
    ("Annual_Report_2025.pdf", "ZZ Fonti Annual Report 2025 - Contents - Remuneration report 210", "annuale", True),
    ("Bilancio_consolidato_31_dicembre_2025.pdf", None, "annuale", True)])
def test_classifica_pdf_riserve_v2(nome, pagina, tipo, ammesso):
    e = classifica_pdf({"url": B + nome, "testo": nome}, prima_pagina=pagina)
    assert (e["tipo"], e["ammesso"]) == (tipo, ammesso), e
    if tipo == "semestrale" and ammesso:
        assert e["periodo"] == "2026-06-30"


def test_tripwire_rete_ripiego_non_inghiottito():
    with pytest.raises(pytest.fail.Exception):
        try:
            sec_xbrl._submissions_sec(CIK_FONTI)
        except Exception:  # come l'except del ripiego: NON deve bastare
            pass
    vecchio = _cf({"Revenue": [("2023-01-01", "2023-12-31", 700000)]})["facts"]
    with pytest.raises(pytest.fail.Exception):
        sec_xbrl.ripiego_ixbrl_annuale("0009990099", vecchio, oggi=datetime(2026, 10, 10))


def test_rete_giu_esplicita_dichiarata(ripiego_ixbrl_giu, monkeypatch, tmp_path):
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path / "xbrl_cache"))
    out = sec_xbrl.ripiego_ixbrl_annuale(CIK_FONTI, CF_24["facts"], oggi=datetime(2026, 10, 10))
    assert out["stato"] == "errore" and "ConnectionError" in out["motivo"]
