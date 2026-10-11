"""R-FONTI 10/10 v4 (Opus 5.5): riserve della terza revisione (874285d), provate su dati SINTETICI (CIK 0009990077,
nomi ZZ* e cifre inventati), nella PIPELINE intera (esegui_profilo) con profilo legacy e nuovo, su 6-K primario
(sequenza 1) e su allegato EX-99.1.

A  frase del periodo con soggetto l'emittente ma risultati di un'altra entita' SENZA parola di relazione
   («results of <Nome>», «financial statements of <Nome>», possessivo «<Nome>'s results», relativa «in which /
   where / whereby <Nome> reported»): non verificato, dichiarato col nome;
B  blocco «<controllata> / 99.9% owned by / <emittente>» sopra il titolo e nota 1 «<emittente> owns 99.9% of
   <controllata>, the entity reported in these statements»: non verificato;
C  «<emittente> and its subsidiary <Nome> (together, the Group) reported consolidated results ...»: VERIFICATO
   (gruppo dell'emittente, come «and its subsidiaries»);
D  look-ahead riproducibile: il giorno di riferimento della run (`oggi`) si passa e vale per tutta la chiamata.
Rete assente (tripwire del conftest), nessuna AI.
"""
from datetime import date, datetime

import pytest

from bellomberg.market_data import sec_xbrl
from bellomberg.market_data.filing_verifica import identita_6k
from tests.test_fonti_v2_riserve import BASE_URL, PROFILO_LEGACY, PROFILO_NUOVO, _esegui, _sei_k
from tests.test_fonti_v3_identita import PRE, TITOLO, cf_annuale  # noqa: F401  (fixture)

PROFILI = pytest.mark.parametrize("profilo", [PROFILO_LEGACY, PROFILO_NUOVO], ids=["legacy", "nuovo"])
DOCUMENTI = pytest.mark.parametrize("tipo,seq", [("6-K", 1), ("EX-99.1", 2)], ids=["primario", "ex99"])
PERIODO = "for the three months ended June 30, 2025"

CASI_A = {
    "risultati_di": f"<p>Zztest Holdings announces the second quarter 2025 results of Zzbank S.A. {PERIODO}.</p>",
    "possessivo": f"<p>Zztest Holdings publishes Zzbank S.A.'s consolidated results {PERIODO}.</p>",
    "prospetti_di": (f"<p>Zztest Holdings furnishes herewith the unaudited financial statements of Zzbank S.A. "
                     f"{PERIODO}.</p>"),
    "relativa_in_which": (f"<p>Zztest Holdings furnishes the press release in which Zzbank S.A. reported net income "
                          f"of 12 million {PERIODO}.</p>"),
    "relativa_where": (f"<p>Zztest Holdings furnishes the report where Zzbank S.A. reported net income "
                       f"{PERIODO}.</p>"),
    "relativa_whereby": (f"<p>Zztest Holdings furnishes the release whereby Zzbank S.A. announced its results "
                         f"{PERIODO}.</p>"),
}
B = (PRE + "<p>Zzpagamentos S.A.</p><p>99.9% owned by</p><p>Zztest Holdings Ltd.</p>" + TITOLO
     + "<p>1. OPERATIONS</p><p>Zztest Holdings Ltd. (\"Company\") owns 99.9% of Zzpagamentos S.A., the entity "
       "reported in these statements.</p>")
# B scomposto: solo il blocco (nota 1 dell'emittente pulita) e solo la nota (intestazione pulita)
B_SOLO_BLOCCO = (PRE + "<p>Zzpagamentos S.A.</p><p>99.9% owned by</p><p>Zztest Holdings Ltd.</p>" + TITOLO
                 + "<p>1. OPERATIONS</p><p>Zztest Holdings Ltd. (“Company” or “Zztest”) was incorporated as an "
                   "exempted company. The Company and its consolidated subsidiaries are the “Group”.</p>")
B_SOLO_NOTA = (PRE + "<p>12</p><p>Zztest Holdings Ltd.</p>" + TITOLO
               + "<p>1. OPERATIONS</p><p>Zztest Holdings Ltd. (\"Company\") owns 99.9% of Zzpagamentos S.A., the "
                 "entity reported in these statements.</p>")
CASI_ALTRA_ENTITA = {**{f"A_{k}": (v, "Zzbank") for k, v in CASI_A.items()},
                     "B": (B, "Zzpagamentos"), "B_solo_blocco": (B_SOLO_BLOCCO, "Zzpagamentos"),
                     # v5: nella nota 1 «owns» (forma attiva) non conta; conta «the entity reported in these»
                     "B_solo_nota": (B_SOLO_NOTA, "the entity reported in these")}

C = (f"<p>Zztest Holdings and its subsidiary Zzpay S.A. (together, the Group) reported consolidated results "
     f"{PERIODO}.</p>")
C_CONSOLIDATI = (f"<p>Zztest Holdings and its wholly-owned subsidiary Zzpay S.A. reported consolidated results "
                 f"{PERIODO}.</p>")
C_PLURALE = f"<p>Zztest Holdings and its subsidiaries reported consolidated results {PERIODO}.</p>"
# nomi comuni dopo «statements of»/«results of» e «internal controls»: non sono un'altra entita' ne' una relazione
PROSPETTI_COMUNI = (f"<p>Zztest Holdings furnishes the Unaudited Interim Condensed Consolidated Statements of Income "
                    f"and the Results of Operations {PERIODO}; Management assessed the internal controls over "
                    f"financial reporting at Zztest Holdings Ltd.</p>")


@PROFILI
@DOCUMENTI
@pytest.mark.parametrize("caso", list(CASI_ALTRA_ENTITA))
def test_pipeline_risultati_o_prospetti_di_altra_entita_non_verificati(monkeypatch, tmp_path, profilo, tipo, seq,
                                                                       caso):
    corpo, atteso = CASI_ALTRA_ENTITA[caso]
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo), profilo, tipo=tipo, seq=seq)
    assert c["stato"] == "non_applicabile", (caso, c)
    assert any("identita' nel corpo del 6-K non provata" in m and atteso in m for m in c["motivi"]), c["motivi"]
    assert "metadati" not in c


@PROFILI
@DOCUMENTI
@pytest.mark.parametrize("corpo", [C, C_CONSOLIDATI, C_PLURALE, PROSPETTI_COMUNI],
                         ids=["C_together_the_group", "C_consolidated", "C_plurale", "nomi_comuni"])
def test_pipeline_gruppo_dell_emittente_verificato(monkeypatch, tmp_path, profilo, tipo, seq, corpo):
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo), profilo, tipo=tipo, seq=seq)
    assert c["stato"] == "verificato", c["motivi"]


def _identita(tmp_path, corpo):
    p = tmp_path / "zz.htm"
    p.write_bytes(_sei_k(corpo))
    return identita_6k(p, url=BASE_URL + "000999007729000009/zz.htm", profilo=PROFILO_LEGACY)


@pytest.mark.parametrize("corpo,atteso", [
    # C senza designazione del gruppo ne' «consolidated»: soggetto composto, falso rifiuto DICHIARATO col nome
    (f"<p>Zztest Holdings and its subsidiary Zzpay S.A. released results {PERIODO}.</p>", "Zzpay"),
    # C con, nel resto della frase, i risultati di un'altra entita': il gruppo non copre la frase intera
    (f"<p>Zztest Holdings and its subsidiary Zzpay S.A. (together, the Group) published the results of Zzbank "
     f"S.A. {PERIODO}.</p>", "Zzbank")], ids=["senza_gruppo", "gruppo_ma_risultati_di_altra"])
def test_gruppo_solo_con_designazione_e_resto_della_frase_letto(tmp_path, corpo, atteso):
    motivo, _ = _identita(tmp_path, corpo)
    assert motivo and atteso in motivo, motivo


def test_motivo_A_nomina_l_entita_e_la_forma(tmp_path):
    motivo, _ = _identita(tmp_path, CASI_A["possessivo"])
    assert "Zzbank S.A." in motivo and "possessivo" in motivo
    motivo, _ = _identita(tmp_path, CASI_A["relativa_in_which"])
    assert "in which Zzbank" in motivo


# ================================================================== D: giorno di riferimento della run
def test_depositato_entro_accetta_date_e_datetime():
    assert sec_xbrl.depositato_entro("2025-04-16", "2025-04-16", oggi=date(2025, 4, 16))
    assert not sec_xbrl.depositato_entro("2025-04-16", "2025-04-16", oggi=datetime(2025, 4, 17, 9, 30))
    assert sec_xbrl.giorno_di_riferimento(datetime(2025, 4, 16, 23, 59)) == "2025-04-16"


@pytest.mark.parametrize("oggi,visto", [("2025-04-16", True), (date(2025, 4, 16), True), ("2026-10-10", False)])
def test_storico_riproducibile_col_giorno_della_run(cf_annuale, oggi, visto):  # noqa: F811
    # FY2024 depositato il 2025-04-16, cutoff 2025-04-16: visto solo se la run era DI quel giorno; l'esito non
    # dipende piu' dall'orologio di sistema del giorno in cui si riesegue
    h = sec_xbrl.get_financial_history("ZZTEST", fino_al="2025-04-16", oggi=oggi)
    assert (2024 in h["items"]["revenue"]) is visto
    lp = sec_xbrl.quarterly_history(cf_annuale["facts"], fino_al="2025-04-16", oggi=oggi)["latest_period"]
    assert lp["period_end"] == ("2024-12-31" if visto else "2023-12-31")


# ================================================================== qualsiasi emittente estero (non solo il primo caso)
# Tre forme di ragione sociale diverse dall'emittente dei casi sopra: nome di piu' parole con S.p.A. e controllata
# col nome SIMILE al padre (alias esteso), «Group» + N.V. con controllata B.V., sigla + «Holdings plc».
EMITTENTI = {
    "spa_controllata_omonima": ("Zzalfa Energia", "Zzalfa Energia S.p.A.", "Zzalfa Energia Rinnovabili S.r.l.",
                                "Zzalfa Energia Rinnovabili"),
    "group_nv": ("Zzdelta Group", "Zzdelta Group N.V.", "Zzdelta Finance B.V.", "Zzdelta Finance"),
    "sigla_plc": ("ZZQ Holdings", "ZZQ Holdings plc", "ZZQ Bank Limited", "ZZQ Bank"),
}


def _profilo_di(nome):
    import re as _re
    rx = r"\b" + r"\s+".join(_re.escape(p.lower()) for p in nome.split()) + r"\b"
    return {**PROFILO_LEGACY, "nome": nome, "verifica": {**PROFILO_LEGACY["verifica"], "emittente": rx}}


def _corpi_altra_entita(reg, sub):
    return {
        "A_risultati_di": f"<p>{reg} announces the second quarter 2025 results of {sub} {PERIODO}.</p>",
        "A_possessivo": f"<p>{reg} publishes {sub}'s consolidated results {PERIODO}.</p>",
        "A_relativa": f"<p>{reg} furnishes the press release in which {sub} reported net income {PERIODO}.</p>",
        "B_blocco_e_nota": (PRE + f"<p>{sub}</p><p>99.9% owned by</p><p>{reg}</p>" + TITOLO
                            + f"<p>1. OPERATIONS</p><p>{reg} (\"Company\") owns 99.9% of {sub}, the entity reported "
                              "in these statements.</p>"),
    }


def _corpi_dell_emittente(reg, sub):
    return {
        "comunicato": f"<p>{reg} today reported its unaudited consolidated results {PERIODO}.</p>",
        "C_gruppo": (f"<p>{reg} and its subsidiary {sub} (together, the Group) reported consolidated results "
                     f"{PERIODO}.</p>"),
        "prospetto_T_e_N": (PRE + f"<p>12</p><p>{reg}</p>" + TITOLO
                            + f"<p>1. OPERATIONS</p><p>{reg} (“Company”) was incorporated as a public company. The "
                              "Company and its consolidated subsidiaries are the “Group”.</p>"),
    }


@pytest.mark.parametrize("emittente", list(EMITTENTI))
@pytest.mark.parametrize("caso", ["A_risultati_di", "A_possessivo", "A_relativa", "B_blocco_e_nota"])
def test_pipeline_altri_emittenti_documento_di_altra_entita_non_verificato(monkeypatch, tmp_path, emittente,
                                                                            caso):
    nome, reg, sub, atteso = EMITTENTI[emittente]
    corpo = _corpi_altra_entita(reg, sub)[caso]
    if caso.startswith("B"):  # il motivo del blocco e' troncato a 120 battute: si legge la relazione
        atteso = "owned by"
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo, registrante=reg), _profilo_di(nome))
    assert c["stato"] == "non_applicabile", (emittente, caso, c)
    assert any("identita' nel corpo del 6-K non provata" in m and atteso in m for m in c["motivi"]), c["motivi"]


@pytest.mark.parametrize("emittente", list(EMITTENTI))
@pytest.mark.parametrize("caso", ["comunicato", "C_gruppo", "prospetto_T_e_N"])
def test_pipeline_altri_emittenti_documento_dell_emittente_verificato(monkeypatch, tmp_path, emittente, caso):
    nome, reg, sub, _ = EMITTENTI[emittente]
    corpo = _corpi_dell_emittente(reg, sub)[caso]
    c = _esegui(monkeypatch, tmp_path, _sei_k(corpo, registrante=reg), _profilo_di(nome))
    assert c["stato"] == "verificato", (emittente, caso, c["motivi"])
