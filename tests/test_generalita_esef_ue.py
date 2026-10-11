"""GENERALITA' UE (Opus 5.5, 10/10/2026): tre buchi trovati dall'audit di generalita' su emittenti UE, provati su
dati SINTETICI (emittenti, LEI, siti e cifre inventati; nessuna rete).

1. esercizio non solare: la data di chiusura nel nome di una «relazione finanziaria» senza aggettivo decide solo
   se coerente con la chiusura d'esercizio dell'emittente (dai depositi ESEF); il titolo esplicito vince; con
   l'esercizio ignoto la sola data lascia il tipo da confermare;
2. filtro «prima pagina non finanziaria»: titoli delle relazioni in DE/FR/ES/NL, righe d'indice e titoli oltre le
   prime battute non contano;
3. niente look-ahead nello storico ESEF: `fino_al` filtra repository (date_added) e pacchetti del sito (data in
   cui sono stati visti), passato dai tre chiamanti.
"""
import hashlib
import json
from datetime import date

import pytest

from bellomberg.market_data import esef, esef_sito
from bellomberg.market_data.esef_sito import classifica_pdf, scegli_pdf
from tests.esef_sito_sintetici import get_finto, pagina

BASE = "https://www.zetagen.example/investitori/"
LEI = "999900ZZGENUE0000731"


def _pdf(nome, pagina_=None, chiusura=None, testo=None):
    return classifica_pdf({"url": BASE + nome, "testo": testo if testo is not None else nome},
                          prima_pagina=pagina_, chiusura_esercizio=chiusura)


# ------------------------------------------------------------------ 1. esercizio non solare

def test_esercizio_al_30_giugno_annuale_e_semestrale_dalla_data():
    e = _pdf("Relazione_Finanziaria_30_giugno_2026.pdf", chiusura="06-30")
    assert e["ammesso"] and e["tipo"] == "annuale" and e["periodo"] == "2026-06-30" and e["periodo_stato"] == "certo"
    assert "chiusura dell'esercizio dell'emittente (06-30)" in e["tipo_base"]
    e = _pdf("Relazione_Finanziaria_31_dicembre_2025.pdf", chiusura="06-30")
    assert e["ammesso"] and e["tipo"] == "semestrale" and e["periodo"] == "2025-12-31"
    e = _pdf("Bilancio_consolidato_31_dicembre_2025.pdf", chiusura="06-30")  # «bilancio» fuori dal mese
    assert e["ammesso"] and e["tipo"] == "semestrale"
    e = _pdf("Bilancio_consolidato_30_giugno_2026.pdf", chiusura="06-30")
    assert e["ammesso"] and e["tipo"] == "annuale"


def test_esercizio_solare_noto_resta_come_prima():
    e = _pdf("Relazione_Finanziaria_30_giugno_2026.pdf", chiusura="12-31")
    assert e["ammesso"] and e["tipo"] == "semestrale" and e["periodo"] == "2026-06-30"
    e = _pdf("Relazione_Finanziaria_31_dicembre_2025.pdf", chiusura="12-31")
    assert e["ammesso"] and e["tipo"] == "annuale" and e["periodo"] == "2025-12-31"


@pytest.mark.parametrize("nome", ["Relazione_Finanziaria_30_giugno_2026.pdf", "Relazione_Finanziaria_31_dicembre_2025.pdf"])
def test_esercizio_ignoto_la_sola_data_e_da_confermare(nome):
    e = _pdf(nome)
    assert not e["ammesso"] and e["tipo"] == "da_confermare" and e["serve_prima_pagina"]
    assert "esercizio dell'emittente ignoto" in e["motivo"]


def test_chiusura_incoerente_con_l_esercizio_da_confermare():
    e = _pdf("Relazione_Finanziaria_31_marzo_2026.pdf", chiusura="12-31")
    assert not e["ammesso"] and e["tipo"] == "da_confermare" and "incoerente" in e["motivo"]


def test_titolo_esplicito_vince_sulla_data():
    # emittente al 30/06: copertina «annuale al 30 giugno» (prima: discordi -> mai ammessa)
    e = _pdf("Relazione_Finanziaria_30_giugno_2026.pdf", "Relazione finanziaria annuale al 30 giugno 2026",
             chiusura="06-30")
    assert e["ammesso"] and e["tipo"] == "annuale" and e["periodo_stato"] == "certo"
    # esercizio ignoto: decide il titolo; la data della copertina conferma il periodo
    e = _pdf("Relazione_Finanziaria_30_giugno_2026.pdf", "Relazione finanziaria annuale al 30 giugno 2026")
    assert e["ammesso"] and e["tipo"] == "annuale" and e["periodo_stato"] == "certo"
    # titolo semestrale contro una data da annuale: vince il titolo, la discordanza e' scritta
    e = _pdf("Relazione_Finanziaria_30_giugno_2026.pdf", "Relazione finanziaria semestrale al 30 giugno 2026",
             chiusura="06-30")
    assert e["ammesso"] and e["tipo"] == "semestrale" and "prevale sulla data nel nome" in e["tipo_base"]


def test_annuale_col_solo_anno_chiude_nel_mese_dell_esercizio():
    e = _pdf("Annual_Report_2026.pdf", chiusura="06-30")
    assert e["ammesso"] and e["periodo"] == "2026-06-30" and e["periodo_stato"] == "da_confermare"
    assert "esercizio dell'emittente al 30/06 presunta" in e["base_periodo"]
    assert _pdf("Annual_Report_2026.pdf")["periodo"] == "2026-12-31"  # esercizio ignoto: come prima


def test_scegli_pdf_esercizio_al_30_giugno_prende_l_annuale():
    pdf = [{"url": BASE + n, "testo": n} for n in ("Relazione_Finanziaria_30_giugno_2026.pdf",
                                                   "Relazione_Finanziaria_30_giugno_2025.pdf",
                                                   "Relazione_Finanziaria_31_dicembre_2025.pdf")]
    s = scegli_pdf(pdf, oggi=date(2026, 10, 10), chiusura_esercizio="06-30")
    assert s["tipo"] == "annuale" and s["ultimo"]["periodo"] == "2026-06-30" and s["precedente"]["periodo"] == "2025-06-30"
    assert scegli_pdf(pdf, oggi=date(2026, 10, 10)) is None  # esercizio ignoto: nessuna scelta sulla sola data


def test_chiusura_esercizio_dai_depositi():
    pk = [{"lei": LEI, "period_end": "2025-06-30"}, {"lei": LEI, "period_end": "2024-06-30"}]
    c = esef_sito.chiusura_esercizio_emittente(LEI, pk, repository_fn=lambda lei: [])
    assert c["chiusura"] == "06-30" and c["periodi"] == ["2024-06-30", "2025-06-30"]
    # 52/53 settimane: i primi giorni del mese contano per il mese prima
    c = esef_sito.chiusura_esercizio_emittente(LEI, [{"lei": LEI, "period_end": "2026-07-03"}], repository_fn=lambda lei: [])
    assert c["chiusura"] == "06-30"
    # v3: fra i depositi annuali verificati vale il piu' recente, il cambio d'esercizio e' dichiarato
    c = esef_sito.chiusura_esercizio_emittente(LEI, [], repository_fn=lambda lei: ["2023-12-31", "2025-03-31"])
    assert c["chiusura"] == "03-31" and "cambio d'esercizio" in c["base"]
    # senza LEI dell'emittente nessuna chiusura, anche con un solo LEI sul sito
    altro = {"lei": "999900ZZALTRA0000419", "period_end": "2025-12-31"}
    assert esef_sito.chiusura_esercizio_emittente(None, pk + [altro])["chiusura"] is None
    c = esef_sito.chiusura_esercizio_emittente(None, pk)
    assert c["chiusura"] is None and "LEI dell'emittente non noto" in c["base"]
    c = esef_sito.chiusura_esercizio_emittente(LEI, [], repository_fn=lambda lei: [])
    assert c["chiusura"] is None and "nessun deposito ESEF" in c["base"]


def test_chiusura_dal_repository_in_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path))
    (tmp_path / f"esef_{LEI}.json").write_text(json.dumps({"filings": {"4417": {"period_end": "2025-06-30"}}}),
                                               encoding="utf-8")
    assert esef_sito.chiusura_esercizio_emittente(LEI)["chiusura"] == "06-30"
    assert esef_sito.chiusura_voce({"lei": LEI, "pacchetti": []}) == "06-30"
    assert esef_sito.chiusura_voce({"chiusura_esercizio": {"chiusura": None}}) is None  # registrata: vale quella


def test_scopri_registra_chiusura_e_prima_data_dei_pacchetti(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path / "xbrl"))
    sito, ir = "https://www.zetagen.example/", "https://www.zetagen.example/investors"
    pacchetto = f"https://www.zetagen.example/files/{LEI}-2025-06-30-1-it.zip"
    relazione = "https://www.zetagen.example/files/Relazione_Finanziaria_30_giugno_2025.pdf"
    pagine = {sito: pagina((ir, "Investor relations")),
              ir: pagina((pacchetto, "ESEF 2025"), (relazione, "Relazione finanziaria 30 giugno 2025"))}
    lette = []

    def gira(oggi, forza=False):
        return esef_sito.scopri("ZZGEN.MI", lei=LEI, oggi=oggi, forza=forza, cache_dir=tmp_path / "sito",
                                sito_fn=lambda t: sito, prima_pagina_fn=lette.append,
                                navigatore_fn=lambda d: esef_sito.Navigatore(d, get=get_finto(pagine, []),
                                                                             dormi=lambda s: None))
    esito = gira(date(2025, 11, 17))
    assert esito["chiusura_esercizio"]["chiusura"] == "06-30"
    assert [p["visto_il"] for p in esito["pacchetti"]] == ["2025-11-17"]
    assert lette == []  # chiusura nota: la relazione si decide senza la prima pagina
    assert "relazione annuale al 2025-06-30" in esito["motivi"][0]
    # esplorazione successiva: la PRIMA data resta
    voce = json.loads((tmp_path / "sito" / esef_sito._nome_file("ZZGEN.MI")).read_text(encoding="utf-8"))
    voce["at"] = "2025-11-17T08:00:00"
    (tmp_path / "sito" / esef_sito._nome_file("ZZGEN.MI")).write_text(json.dumps(voce), encoding="utf-8")
    assert [p["visto_il"] for p in gira(date(2026, 2, 23), forza=True)["pacchetti"]] == ["2025-11-17"]
    assert esef_sito.pdf_trovati("ZZGEN.MI", cache_dir=tmp_path / "sito", oggi=date(2026, 2, 23))["tipo"] == "annuale"


# ------------------------------------------------------------------ 2. prima pagina non finanziaria, piu' lingue

@pytest.mark.parametrize("nome,testa", [
    ("Geschaeftsbericht_2025.pdf",
     "Geschäftsbericht 2025 Zetagen AG Inhalt An die Aktionäre 2 Vergütungsbericht 127 Konzernabschluss 143"),
    ("Document_d_enregistrement_universel_2025.pdf",
     "Document d'enregistrement universel 2025 Sommaire Rapport sur la rémunération 213 Comptes consolidés 241"),
    ("Informe_anual_2025.pdf", "Informe Anual 2025 Indice Informe anual sobre remuneraciones de los consejeros 307"),
    ("Annual_Report_2025.pdf", "Contents Remuneration report 89 Annual Report 2025 Zetagen plc"),
    ("Jaarverslag_2025.pdf", "Jaarverslag 2025 Inhoud Remuneratierapport 113 Jaarrekening 131"),
])
def test_relazione_finanziaria_con_indice_ammessa(nome, testa):
    e = _pdf(nome, testa)
    assert e["ammesso"] and e["tipo"] == "annuale", e["motivo"]


@pytest.mark.parametrize("nome,testa", [
    ("Annual_Report_2025.pdf", "Remuneration Report 2025 Zetagen plc Letter from the chair"),
    ("Geschaeftsbericht_2025.pdf", "Vergütungsbericht 2025 der Zetagen AG gemäß § 162 AktG"),
    ("Document_d_enregistrement_universel_2025.pdf", "Rapport sur la rémunération des mandataires sociaux 2025"),
    ("Informe_anual_2025.pdf", "Informe anual sobre remuneraciones de los consejeros 2025 Zetagen S.A."),
    ("Informe_anual_2025.pdf", "Informe anual de gobierno corporativo 2025 Zetagen S.A."),
    ("Jaarverslag_2025.pdf", "Remuneratierapport 2025 Zetagen N.V."),
    ("Relazione_finanziaria_annuale_2025.pdf",
     "Relazione sulla politica in materia di remunerazione e sui compensi corrisposti ai sensi dell'art. 123-ter TUF"),
    ("Annual_Report_2025.pdf", "Compensation Report pursuant to Section 162 Zetagen plc"),
])
def test_prima_pagina_non_finanziaria_resta_da_confermare(nome, testa):
    e = _pdf(nome, testa)
    assert not e["ammesso"] and e["tipo"] == "da_confermare" and "non la relazione finanziaria" in e["motivo"]


@pytest.mark.parametrize("testa,tipo", [
    ("Halbjahresfinanzbericht 2026 Zetagen AG", "semestrale"), ("Rapport financier semestriel 2026", "semestrale"),
    ("Informe financiero semestral 2026", "semestrale"), ("Halfjaarbericht 2026", "semestrale"),
    ("Jahresfinanzbericht 2025", "annuale"), ("Rapport financier annuel 2025", "annuale"),
    ("Cuentas anuales 2025", "annuale"), ("Jaarverslag 2025", "annuale"),
    ("Informe anual sobre remuneraciones 2025", None),  # titolo NON finanziario: nessun tipo
])
def test_titoli_delle_relazioni_in_piu_lingue(testa, tipo):
    assert esef_sito._tipo_dal_titolo(testa) == tipo


# ------------------------------------------------------------------ 3. niente look-ahead nello storico ESEF

DUR = {2024: "2024-01-01T00:00:00/2025-01-01T00:00:00", 2025: "2025-01-01T00:00:00/2026-01-01T00:00:00"}
URL_PK = f"https://www.zetagen.example/files/{LEI}-2025-12-31-1-en.zip"


def _fatti(anno, ricavi):
    return {"Revenue": [[anno, ricavi, "EUR"]], "Assets": [[anno, ricavi * 3 + 17, "EUR"]]}


def _repo(*righe):
    return {"index_fetched_at": 1e12, "filings": {
        str(5101 + i): {"period_end": f"{anno}-12-31", "date_added": aggiunto, "extract_error": None,
                        "facts": _fatti(anno, ricavi)} for i, (anno, aggiunto, ricavi) in enumerate(righe)}}


def test_entro_il_cutoff_stessa_regola_della_sec():
    assert esef_sito.entro_il_cutoff("2025-04-22", "2025-06-30", oggi="2026-10-10")
    assert not esef_sito.entro_il_cutoff("2025-06-30", "2025-06-30", oggi="2026-10-10")  # stesso giorno, storico
    assert esef_sito.entro_il_cutoff("2026-10-10", "2026-10-10", oggi="2026-10-10")  # run di oggi
    assert not esef_sito.entro_il_cutoff(None, "2025-06-30")  # data ignota: mai
    assert esef_sito.entro_il_cutoff(None, None)  # senza cutoff: tutto


@pytest.fixture
def storico(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path / "xbrl"))
    monkeypatch.setattr(esef_sito, "CARTELLA", str(tmp_path / "sito"))
    monkeypatch.setattr(esef, "resolve_lei", lambda t, company_name=None: (LEI, "LEI sintetico"))
    monkeypatch.setattr(esef, "_save_cache", lambda lei, cache: None)
    (tmp_path / "sito").mkdir()
    scaricati = []

    def monta(repo, pacchetti=(), giorno=None):
        monkeypatch.setattr(esef, "_refresh_entity_cache", lambda lei: json.loads(json.dumps(repo)))
        voce = {"ticker": "ZZGEN.MI", "pacchetti": list(pacchetti)}
        if giorno:
            voce.update(giorno=giorno, at=giorno + "T09:00:00")
        (tmp_path / "sito" / "ZZGEN.MI.json").write_text(json.dumps(voce), encoding="utf-8")
        raw = {"facts": {f"f{i}": {"value": str(v), "decimals": -6, "dimensions": {
            "concept": "ifrs-full:" + c, "entity": "scheme:" + LEI, "period": DUR[a], "unit": "iso4217:EUR"}}
            for i, (c, a, v) in enumerate([("Revenue", 2025, 733e6), ("Revenue", 2024, 641e6)])}}

        def scarica(url, archivio, hosts):
            scaricati.append(url)
            p = tmp_path / "pacchetto.json"
            p.write_text(json.dumps(raw), encoding="utf-8")
            return {"stato": "ok", "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "pacchetto_sha256": "cd" * 32}
        monkeypatch.setattr(esef_sito, "scarica_pacchetto_json", scarica)
        return scaricati
    return monta


def test_storico_con_cutoff_esclude_i_depositi_successivi_e_dichiara(storico):
    storico(_repo((2022, None, 523e6), (2023, "2024-04-17", 587e6), (2024, "2025-04-23", 641e6)))
    r = esef.get_esef_history("ZZGEN.MI", fino_al="2025-01-31")
    assert r["years"] == [2023] and r["fino_al"] == "2025-01-31"
    assert any("FY2024" in x and "depositato il 2025-04-23" in x for x in r["esclusi_fino_al"])
    assert any("FY2022" in x and "data di deposito ignota" in x for x in r["esclusi_fino_al"])
    assert any("FY2022" in g and "data di deposito ignota" in g for g in r["gaps"])  # buco dichiarato
    senza = esef.get_esef_history("ZZGEN.MI")  # senza cutoff: tutti, nessuna chiave nuova
    assert senza["years"] == [2022, 2023, 2024] and "fino_al" not in senza and "esclusi_fino_al" not in senza


def test_storico_con_cutoff_tutto_escluso_errore_che_lo_dice(storico):
    storico(_repo((2024, "2025-04-23", 641e6)))
    r = esef.get_esef_history("ZZGEN.MI", fino_al="2024-12-31")
    assert "error" in r and "noto al 2024-12-31" in r["error"] and "depositato il 2025-04-23" in r["error"]


def test_pacchetto_dal_sito_visto_dopo_il_cutoff_escluso(storico):
    pk = {"url": URL_PK, "lei": LEI, "period_end": "2025-12-31", "versione": 1, "lingua": "en", "formato": "zip",
          "visto_il": "2026-04-29"}
    scaricati = storico(_repo((2024, "2025-04-23", 641e6)), [pk], giorno="2026-09-14")
    r = esef.get_esef_history("ZZGEN.MI", fino_al="2026-03-31")
    assert r["years"] == [2024] and scaricati == []
    assert any("visto sul sito il 2026-04-29" in x for x in r["esclusi_fino_al"])
    r = esef.get_esef_history("ZZGEN.MI", fino_al="2026-06-30")
    assert r["years"] == [2024, 2025] and r["items"]["revenue"][2025] == 733e6 and scaricati == [URL_PK]
    assert r["esclusi_fino_al"] == []


def test_pacchetto_senza_data_escluso_e_dichiarato_come_buco(storico):
    pk = {"url": URL_PK, "lei": LEI, "period_end": "2025-12-31", "versione": 1, "lingua": "en", "formato": "zip"}
    scaricati = storico(_repo((2024, "2025-04-23", 641e6)), [pk])  # voce senza giorno ne' at
    r = esef.get_esef_history("ZZGEN.MI", fino_al="2026-08-31")
    assert r["years"] == [2024] and scaricati == []
    assert any("data di pubblicazione ignota" in g for g in r["gaps"])
    # senza cutoff il pacchetto entra come prima
    assert esef.get_esef_history("ZZGEN.MI")["years"] == [2024, 2025]


def test_voce_senza_prima_data_usa_il_giorno_dell_esplorazione(storico):
    pk = {"url": URL_PK, "lei": LEI, "period_end": "2025-12-31", "versione": 1, "lingua": "en", "formato": "zip"}
    storico(_repo((2024, "2025-04-23", 641e6)), [pk], giorno="2026-05-12")
    assert esef.get_esef_history("ZZGEN.MI", fino_al="2026-05-11")["years"] == [2024]
    assert esef.get_esef_history("ZZGEN.MI", fino_al="2026-06-30")["years"] == [2024, 2025]


def test_righe_da_cache_senza_cutoff_identiche(tmp_path):
    pk = {"url": URL_PK, "lei": LEI, "period_end": "2025-12-31", "versione": 1, "lingua": "en", "formato": "zip",
          "visto_il": "2026-04-29"}
    (tmp_path / "ZZGEN.MI.json").write_text(json.dumps({"pacchetti": [pk]}), encoding="utf-8")
    assert esef_sito.righe_da_cache(LEI, cache_dir=tmp_path) == [
        {"id": URL_PK, "period_end": "2025-12-31", "json_url": URL_PK, "report_url": URL_PK, "language": None,
         "date_added": None, "origine": "sito", "lingua_nome": "en"}]
    esclusi = []
    assert esef_sito.righe_da_cache(LEI, cache_dir=tmp_path, fino_al="2026-04-01", esclusi=esclusi) == []
    assert len(esclusi) == 1 and "2026-04-29" in esclusi[0]


# ------------------------------------------------------------------ 3b. i chiamanti passano il cutoff

def test_chat_tools_passa_as_of_al_ripiego_esef(monkeypatch):
    from bellomberg.agents import chat_tools
    from bellomberg.market_data import sec_xbrl
    chiamate = []
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: {"error": "SEC assente"})
    monkeypatch.setattr(esef, "get_esef_history", lambda t, years=10, **kw: chiamate.append(kw) or {"error": "x"})
    monkeypatch.setattr(chat_tools, "_con_ultimo_periodo", lambda r, *a, **k: r)
    chat_tools.dispatch("get_financial_history", {"ticker": "ZZGEN.MI"}, as_of="2026-03-31")
    chat_tools.dispatch("get_financial_history", {"ticker": "ZZGEN.MI"})
    assert chiamate == [{"fino_al": "2026-03-31"}, {}]


def test_market_pack_storico_col_cutoff(monkeypatch):
    from bellomberg.market_data import sec_xbrl, trade_idea_market_pack as tm
    viste = []
    monkeypatch.setattr(sec_xbrl, "get_financial_history",
                        lambda t, years=10, **kw: viste.append(("sec", kw)) or {"error": "SEC assente"})
    monkeypatch.setattr(esef, "get_esef_history",
                        lambda t, years=10, company_name=None, **kw: viste.append(("esef", kw)) or {"years": []})
    tm._default_history("ZZGEN.MI", "Zetagen", fino_al="2026-10-02")
    assert viste == [("sec", {"fino_al": "2026-10-02"}), ("esef", {"fino_al": "2026-10-02"})]
    assert tm._cutoff_iso(date(2026, 10, 2)) == "2026-10-02" and tm._cutoff_iso(None) is None


def test_market_pack_predefinito_lega_il_cutoff(monkeypatch):
    from bellomberg.market_data import trade_idea_market_pack as tm
    from tests.test_trade_idea_market_pack import INFO, TODAY, Fornitore, _base_specs, _fx_specs
    viste = []
    monkeypatch.setattr(tm, "_default_history", lambda t, n, fino_al=None: viste.append(fino_al) or {"error": "n.d."})
    tm.build_market_pack(candidate={"ticker": "ZZCAND.MI", "name": "Zeta Sintetica"},
                         peers=["QQPEER1.PA", "QQPEER2.L"], today=TODAY, fetch=Fornitore(_base_specs()),
                         fx_fetch=Fornitore(_fx_specs()), info_fetch=lambda s: dict(INFO[s]),
                         peer_selector=lambda t, i: pytest.fail("selettore"),
                         resolve_symbols=lambda tks: {t: t for t in tks})
    assert viste == [TODAY.isoformat()]


def test_dcf_storico_col_cutoff_e_staleness_sul_cutoff(monkeypatch):
    from bellomberg.market_data import sec_xbrl
    from bellomberg.valuation import dcf_engine
    viste = []
    monkeypatch.setattr(sec_xbrl, "get_financial_history",
                        lambda t, years=10, **kw: viste.append(("sec", kw)) or {"error": "SEC assente"})
    monkeypatch.setattr(esef, "get_esef_history",
                        lambda t, years=10, company_name=None, **kw: viste.append(("esef", kw))
                        or {"years": [2021, 2022], "items": {}, "_source": "ESEF finto"})
    h = dcf_engine._fetch_history("ZZGEN.MI", {}, as_of="2024-06-30")
    assert viste == [("sec", {"fino_al": "2024-06-30"}), ("esef", {"fino_al": "2024-06-30"})]
    assert "error" not in h  # FY2022 non e' vecchio rispetto al cutoff 2024
    viste.clear()
    h = dcf_engine._fetch_history("ZZGEN.MI", {})
    assert viste == [("sec", {}), ("esef", {})]
    assert "STALE" in h["error"]  # rispetto a oggi lo e' (come prima)


def test_sector_analysis_passa_il_cutoff_allo_storico(monkeypatch):
    from bellomberg.valuation import dcf_engine, sector_analysis
    viste = []
    monkeypatch.setattr(dcf_engine, "_fetch_history", lambda t, info, as_of=None: viste.append(as_of) or {"years": []})
    sector_analysis.default_sector_providers()["filings"]("ZZGEN.MI", as_of="2025-09-30")
    assert viste == ["2025-09-30"]


# ================================================================== v2 dopo la review avversariale (Opus 5.5)
# D1: chiusura d'esercizio mai «il deposito piu' recente»

def test_d1_semestrale_esef_dopo_l_annuale_non_inverte_i_tipi():
    pk = [{"lei": LEI, "period_end": "2025-12-31"}, {"lei": LEI, "period_end": "2026-06-30"}]
    c = esef_sito.chiusura_esercizio_emittente(LEI, pk, repository_fn=lambda lei: [])
    assert c["chiusura"] is None and "~6 mesi" in c["base"]
    e = _pdf("Relazione_Finanziaria_31_dicembre_2025.pdf", chiusura=c["chiusura"])
    assert not e["ammesso"] and e["tipo"] == "da_confermare"  # mai annuale/semestrale invertiti con periodo certo


def test_d1_depositi_annuali_in_cache_decidono_e_i_semestrali_si_dichiarano():
    pk = [{"lei": LEI, "period_end": "2025-12-31"}, {"lei": LEI, "period_end": "2026-06-30"}]
    c = esef_sito.chiusura_esercizio_emittente(LEI, pk, repository_fn=lambda lei: ["2024-12-31"])
    assert c["chiusura"] == "12-31" and "2026-06-30" in c["base"] and "infrannuali" in c["base"]


def test_v3_r1a_cambio_d_esercizio_fra_depositi_annuali_verificati():
    c = esef_sito.chiusura_esercizio_emittente(
        LEI, [], repository_fn=lambda lei: ["2022-12-31", "2023-12-31", "2024-12-31", "2025-06-30"])
    assert c["chiusura"] == "06-30" and "cambio d'esercizio" in c["base"] and "infrannual" not in c["base"]
    e = _pdf("Relazione_Finanziaria_30_giugno_2026.pdf", chiusura=c["chiusura"])
    assert e["ammesso"] and e["tipo"] == "annuale"  # prima: semestrale «certo»
    c = esef_sito.chiusura_esercizio_emittente(LEI, [], repository_fn=lambda lei: ["2023-12-31", "2024-12-31",
                                                                                  "2026-03-31"])
    assert c["chiusura"] == "03-31"  # transizione di 15 mesi


def test_v3_r1b_pacchetti_non_verificati_decidono_solo_senza_semestrali():
    def ch(*fini):
        return esef_sito.chiusura_esercizio_emittente(LEI, [{"lei": LEI, "period_end": d} for d in fini],
                                                      repository_fn=lambda lei: [])
    c = ch("2025-12-31", "2025-06-30", "2026-06-30")  # annuale + due semestrali sul sito
    assert c["chiusura"] is None and "~6 mesi" in c["base"]
    c = ch("2024-06-30", "2025-06-30")
    assert c["chiusura"] == "06-30" and "non verificati" in c["base"]
    c = ch("2023-12-31", "2025-03-31")  # mesi diversi senza un deposito verificato
    assert c["chiusura"] is None and "mesi diversi" in c["base"]


def test_d1_chiusura_verificata_sui_fatti_annuali_in_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path))
    (tmp_path / f"esef_{LEI}.json").write_text(json.dumps({"sito": {"u": {"chiusura_verificata": "2025-09-30"}}}),
                                               encoding="utf-8")
    pk = [{"lei": LEI, "period_end": "2026-03-31"}]
    assert esef_sito.chiusura_esercizio_emittente(LEI, pk)["chiusura"] == "09-30"


def test_lei_sbagliato_dichiarato():
    pk = [{"lei": "999900ZZALTRA0000419", "period_end": "2025-06-30"}]
    c = esef_sito.chiusura_esercizio_emittente(LEI, pk, repository_fn=lambda lei: [])
    assert c["chiusura"] is None and "di un altro LEI" in c["base"] and "da verificare" in c["base"]


# D2: righe d'indice, date, rimandi, comunicati, titoli finanziari con un tema non finanziario

_PREMESSA = ("This document has been translated from the original German version. In case of discrepancies the "
             "German version prevails. Forward-looking statements are subject to risks and uncertainties. ")


@pytest.mark.parametrize("nome,testa", [
    ("Annual_Report_2025.pdf", "Remuneration Report for the financial year ended 31 December 2025 Zetagen SE"),
    ("Geschaeftsbericht_2025.pdf", "Vergütungsbericht für das Geschäftsjahr vom 1. Januar bis 31. Dezember 2025"),
    ("Document_d_enregistrement_universel_2025.pdf", "Rapport sur la rémunération des mandataires au 31 décembre 2025"),
    ("Annual_Report_2025.pdf", "Corporate Governance Report pursuant to Article 89 of the Companies Act"),
    ("Informe_anual_2025.pdf", "Informe anual de gobierno corporativo conforme al artículo 540 de la Ley"),
    ("Annual_Report_2025.pdf", "Sustainability Report 2025 covering 12 months to December"),
    ("Annual_Report_2025.pdf", _PREMESSA + "Remuneration Report 2025"),  # nessuna soglia di posizione
    ("Annual_Report_2025.pdf", "Zetagen plc " + "Strategic report and highlights of the year " * 6
     + "Remuneration report"),
    ("Annual_Report_2025.pdf", "Rapport financier annuel – rémunération des dirigeants 2025"),
    ("Geschaeftsbericht_2025.pdf", "Geschäftsbericht Nachhaltigkeit 2025"),
    ("Annual_Report_2025.pdf", "Pressemitteilung 12. März 2026: Zetagen veröffentlicht Jahresfinanzbericht 2025"),
    ("Annual_Report_2025.pdf", "Communiqué de presse : Zetagen publie son rapport financier annuel 2025"),
])
def test_d2_copertine_non_finanziarie(nome, testa):
    e = _pdf(nome, testa)
    assert not e["ammesso"] and e["tipo"] == "da_confermare", e


@pytest.mark.parametrize("nome,testa", [
    ("Annual_Report_2025.pdf", "Contents Remuneration Report 2025 ......... 112 Consolidated financial statements .... 140"),
    ("Annual_Report_2025.pdf", "Remuneration Report............ 98"),
    ("Annual_Report_2025.pdf", "Sustainability Report and Annual Report 2025"),
    ("Annual_Report_2025.pdf", "Zetagen Integrated Annual Report 2025 Sustainability Report included"),
    ("Annual_Report_2025.pdf", "Sommaire 1 Rapport sur le gouvernement d'entreprise 45 Rapport financier annuel 2025"),
    ("Annual_Report_2025.pdf", "Strategy 4 Remuneration report 89 Group accounts 131"),  # due coppie titolo-numero
])
def test_d2_indici_e_relazioni_restano_ammessi(nome, testa):
    e = _pdf(nome, testa)
    assert e["ammesso"] and e["tipo"] == "annuale", e["motivo"]


@pytest.mark.parametrize("testa", [
    "Annual Report – Governance, Strategy and Financial Statements 2025",
    "Geschäftsbericht – Nachhaltigkeit als Strategie 2025",
    "Rapport financier annuel – gouvernance d'entreprise et états financiers 2025",
    "Bilancio 2025 Relazione finanziaria annuale – sostenibilità integrata",
    "Investors | Press releases | Annual Report 2025 Zetagen plc",
    "Nota de prensa: ver más abajo. Cuentas anuales consolidadas 2025 e informe de gestión",
])
def test_v3_r2_copertine_vere_con_tema_o_menu_ammesse(testa):
    e = _pdf("Annual_Report_2025.pdf", testa)
    assert e["ammesso"] and e["tipo"] == "annuale", e["motivo"]


@pytest.mark.parametrize("testa", [
    "Remuneration Report 2025 Contents Introduction 2 Remuneration policy 4 Remuneration report 12",
    "Inhalt Vergütungsbericht 2025 1 Einleitung 3",
    "Nota de prensa: Zetagen publica sus cuentas anuales 2025",
])
def test_v3_r3_titolo_prima_dell_indice_non_e_una_riga_d_indice(testa):
    e = _pdf("Annual_Report_2025.pdf", testa)
    assert not e["ammesso"] and e["tipo"] == "da_confermare"


def test_d2_comunicato_dei_risultati_col_nome_da_comunicato_resta_ammesso():
    e = _pdf("Annual_results_2025_press_release.pdf", "Press release 12 March 2026: Zetagen annual results 2025")
    assert e["ammesso"] and e["tipo_documento"] == "comunicato_risultati"


# D4: regola dei sei mesi con esercizi al 31/03 e al 30/09

@pytest.mark.parametrize("nome,chiusura,tipo", [
    ("Interim_Report_30_September_2025.pdf", "03-31", "semestrale"),
    ("Interim_Report_31_March_2026.pdf", "09-30", "semestrale"),
    ("Interim_Report_31_March_2026.pdf", "12-31", "trimestrale"),  # esercizio solare: resta un trimestre
    ("Interim_Report_31_March_2026.pdf", None, "trimestrale"),
])
def test_d4_semestre_a_marzo_o_settembre(nome, chiusura, tipo):
    e = _pdf(nome, chiusura=chiusura)
    assert e["ammesso"] and e["tipo"] == tipo


def test_minori_52_53_settimane_e_cutoff_malformato():
    assert esef_sito._tipo_dalla_chiusura("2025-10-03", "09-30")[0] == "annuale"
    assert esef_sito._tipo_dalla_chiusura("2026-04-02", "09-30")[0] == "semestrale"
    assert not esef_sito.entro_il_cutoff("2025", "2025-06-30")


# D3: l'attivazione dal sito usa la chiusura registrata nella voce

def test_d3_attivazione_usa_la_chiusura_della_voce():
    from bellomberg.market_data import filing_attivazione as fa
    voce = {"ticker": "ZZGEN.MI", "sito": "https://www.zetagen.example/", "accesso": {"stato": "ok"},
            "chiusura_esercizio": {"chiusura": "12-31"}, "prime_pagine": {},
            "pdf": [{"url": "https://www.zetagen.example/ir/Relazione_finanziaria_31_12_2025.pdf", "testo": ""}]}
    r = fa._attiva_sito(None, "ZZGEN.MI", None, None, "nessuna fonte ufficiale", trovato=voce)
    assert "nome dell'emittente non noto" in r["motivo"]  # PDF scelto: senza chiusura sarebbe «nessun documento»
    r = fa._attiva_sito(None, "ZZGEN.MI", None, None, "nessuna fonte ufficiale",
                        trovato={**voce, "chiusura_esercizio": {"chiusura": None}})
    assert "nessun documento periodico ammesso" in r["motivo"]  # esercizio ignoto: la sola data non basta


def test_d3_aggiornamento_periodo_del_profilo_con_esercizio_al_30_giugno(monkeypatch):
    from bellomberg.market_data import filing_attivazione as fa
    monkeypatch.setattr(fa, "alias_emittente", lambda *a, **k: ([], []))
    url = "https://www.zetagen.example/ir/Annual_Report_2026.pdf"

    class Store:
        def get_profile(self, t):
            return {"profile": {"origine_collegamento": fa.ORIGINE_COLLEGAMENTO_SITO, "ir_urls": [url],
                                "nome": "Zetagen"}}
    voce = {"ticker": "ZZGEN.MI", "sito": "https://www.zetagen.example/", "accesso": {"stato": "ok"},
            "chiusura_esercizio": {"chiusura": "06-30"}, "prime_pagine": {}, "pdf": [{"url": url, "testo": ""}]}
    r = fa.aggiorna_dal_sito(Store(), "ZZGEN.MI", scopri_fn=lambda t, **k: voce)
    assert r["esito"] == "invariato" and "2026-06-30" in r["motivo"]  # prima: presunto al 2026-12-31


# Mutazioni sopravvissute alla review (M2, M4, M6, M10, M17)

def _sito_finto(pagine, chiamate):
    return lambda d: esef_sito.Navigatore(d, get=get_finto(pagine, chiamate), dormi=lambda s: None)


def test_m17_scopri_usa_la_chiusura_dalla_home(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path / "xbrl"))
    sito = "https://www.zetagen.example/"
    pk = f"https://www.zetagen.example/files/{LEI}-2025-06-30-1-it.zip"
    rel = "https://www.zetagen.example/files/Relazione_Finanziaria_30_giugno_2025.pdf"
    chiamate = []
    esito = esef_sito.scopri("ZZGEN.MI", lei=LEI, oggi=date(2025, 11, 17), cache_dir=tmp_path / "sito",
                             sito_fn=lambda t: sito, prima_pagina_fn=lambda u: "Zetagen",
                             navigatore_fn=_sito_finto({sito: pagina((pk, "ESEF"), (rel, "Relazione"))}, chiamate))
    # la home ha gia' la relazione (annuale al 30/06): nessun candidato IR standard provato
    assert not any("investors.zetagen.example" in c for c in chiamate)
    assert "relazione annuale al 2025-06-30" in esito["motivi"][0]


def test_m2_riepilogo_scarti_con_la_chiusura(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path / "xbrl"))
    sito = "https://www.zetagen.example/"
    pk = f"https://www.zetagen.example/files/{LEI}-2025-12-31-1-it.zip"
    rel = "https://www.zetagen.example/files/Relazione_Finanziaria_31_marzo_2026.pdf"
    esito = esef_sito.scopri("ZZGEN.MI", lei=LEI, oggi=date(2026, 5, 18), cache_dir=tmp_path / "sito",
                             sito_fn=lambda t: sito, prima_pagina_fn=lambda u: "Zetagen",
                             navigatore_fn=_sito_finto({sito: pagina((pk, "ESEF"), (rel, "Relazione"))}, []))
    argomenti = dict(oggi=date(2026, 5, 18), prime_pagine=esito["prime_pagine"], dominio=esef_sito.domini_voce(esito))
    con = esef_sito.riepilogo_scarti(esito["pdf"], chiusura_esercizio="12-31", **argomenti)
    assert con != esef_sito.riepilogo_scarti(esito["pdf"], **argomenti)
    assert con in esito["motivi"]


def test_m4_scopri_senza_fonte_usa_la_chiusura_della_voce():
    voce = {"ticker": "ZZGEN.MI", "sito": "https://www.zetagen.example/", "chiusura_esercizio": {"chiusura": "06-30"},
            "pdf": [{"url": "https://www.zetagen.example/ir/Relazione_Finanziaria_30_giugno_2026.pdf", "testo": ""}]}
    (r,) = esef_sito.scopri_senza_fonte(["ZZGEN.MI"], oggi=date(2026, 10, 10), consigliere_fn=lambda: False,
                                        scopri_fn=lambda t, oggi=None: voce)
    assert r["pdf"] is True


def test_m6_depositi_dal_sito_misura_l_attesa_al_cutoff(storico):
    storico(_repo((2023, "2024-04-17", 587e6)))
    r = esef.get_esef_history("ZZGEN.MI", fino_al="2024-06-30")
    assert r["sito_emittente"]["stato"] == "non_necessario"  # al cutoff il FY2024 non era ancora atteso
    assert esef.get_esef_history("ZZGEN.MI")["sito_emittente"]["stato"] == "nessun_pacchetto"  # oggi si'


def test_m10_righe_da_cache_vale_la_data_piu_antica(tmp_path):
    base = {"url": URL_PK, "lei": LEI, "period_end": "2025-12-31", "versione": 1, "lingua": "en", "formato": "zip"}
    (tmp_path / "A.json").write_text(json.dumps({"pacchetti": [dict(base, visto_il="2026-05-19")]}), encoding="utf-8")
    (tmp_path / "B.json").write_text(json.dumps({"pacchetti": [dict(base, visto_il="2026-02-11")]}), encoding="utf-8")
    (riga,) = esef_sito.righe_da_cache(LEI, cache_dir=tmp_path, fino_al="2026-03-31", esclusi=[])
    assert riga["visto_il"] == "2026-02-11"


def test_visto_il_sopravvive_a_un_esplorazione_fallita(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path / "xbrl"))
    sito = "https://www.zetagen.example/"
    pk = f"https://www.zetagen.example/files/{LEI}-2025-06-30-1-it.zip"
    path = tmp_path / "sito" / esef_sito._nome_file("ZZGEN.MI")

    def gira(oggi, pagine):
        if path.exists():  # oltre il limite di un'esplorazione l'ora
            v = json.loads(path.read_text(encoding="utf-8"))
            v["at"] = "2020-01-01T00:00:00"
            path.write_text(json.dumps(v), encoding="utf-8")
        return esef_sito.scopri("ZZGEN.MI", lei=LEI, oggi=oggi, forza=True, cache_dir=tmp_path / "sito",
                                sito_fn=lambda t: sito, prima_pagina_fn=lambda u: "",
                                navigatore_fn=_sito_finto(pagine, []))
    gira(date(2025, 11, 17), {sito: pagina((pk, "ESEF"))})
    fallita = gira(date(2026, 1, 12), {})  # sito giu': nessuna pagina, nessun pacchetto
    assert fallita["fallita"] and fallita["pacchetti_visti"] == {pk: "2025-11-17"}
    assert [p["visto_il"] for p in gira(date(2026, 3, 9), {sito: pagina((pk, "ESEF"))})["pacchetti"]] == ["2025-11-17"]


def test_d2_rimando_normativo_non_e_un_indice():
    # due rimandi («Article 89», «Article 91») non sono due coppie titolo-numero di un indice
    e = _pdf("Annual_Report_2025.pdf",
             "Corporate Governance Report pursuant to Article 89 and Article 91 of the Companies Act")
    assert not e["ammesso"] and e["tipo"] == "da_confermare"


def test_d3_prova_scelte_usa_la_chiusura_della_voce(monkeypatch):
    from bellomberg.market_data import filing_attivazione as fa
    scelte = []
    monkeypatch.setattr(fa, "profilo_dal_sito", lambda t, **k: scelte.append(k["scelta"]) or {"motivo": "prova"})
    voce = {"ticker": "ZZGEN.MI", "sito": "https://www.zetagen.example/", "accesso": {"stato": "ok"},
            "chiusura_esercizio": {"chiusura": "12-31"}, "prime_pagine": {},
            "pdf": [{"url": "https://www.zetagen.example/ir/Relazione_finanziaria_31_12_2025.pdf", "testo": ""}]}
    fa._prova_scelte("ZZGEN.MI", "Zetagen", voce, alias=[])
    assert [s["ultimo"]["periodo"] for s in scelte] == ["2025-12-31"]  # senza chiusura: nessuna scelta


def test_v3_n7_esplorazione_fallita_dopo_un_pacchetto_nuovo(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path / "xbrl"))
    sito = "https://www.zetagen.example/"
    pk1 = f"https://www.zetagen.example/files/{LEI}-2024-06-30-1-it.zip"
    pk2 = f"https://www.zetagen.example/files/{LEI}-2025-06-30-1-it.zip"
    path = tmp_path / "sito" / esef_sito._nome_file("ZZGEN.MI")

    def gira(oggi, pagine):
        if path.exists():
            v = json.loads(path.read_text(encoding="utf-8"))
            v["at"] = "2020-01-01T00:00:00"
            path.write_text(json.dumps(v), encoding="utf-8")
        return esef_sito.scopri("ZZGEN.MI", lei=LEI, oggi=oggi, forza=True, cache_dir=tmp_path / "sito",
                                sito_fn=lambda t: sito, prima_pagina_fn=lambda u: "",
                                navigatore_fn=_sito_finto(pagine, []))
    gira(date(2025, 2, 3), {sito: pagina((pk1, "ESEF 2024"))})
    nuovo = gira(date(2025, 11, 17), {sito: pagina((pk1, "ESEF 2024"), (pk2, "ESEF 2025"))})
    assert nuovo["pacchetti_visti"] == {pk1: "2025-02-03", pk2: "2025-11-17"}  # il nuovo entra subito
    fallita = gira(date(2026, 1, 12), {})
    assert fallita["fallita"] and fallita["pacchetti_visti"] == {pk1: "2025-02-03", pk2: "2025-11-17"}


@pytest.mark.parametrize("testa", [
    # giorno seguito dal mese, con altre due coppie titolo-numero nella pagina (N2)
    "Zetagen SE Remuneration Report for the year ended 31 December 2025 Highlights 4 Group accounts 131",
    "Zetagen plc Remuneration report covering 12 directors",  # una sola coppia non fa un indice (N5)
    "Vergütungsbericht 1 Einleitung 3 Vergütungssystem 7",  # titolo a inizio pagina (W9)
    "Zetagen SE Remuneration Report Contents Introduction 2 Policy 4",  # titolo prima dell'intestazione (W10)
    "Zetagen SE Corporate Governance Report pursuant to Article 89 and Article 91 of the Act",  # rimandi (W13)
])
def test_v3_copertine_non_finanziarie_senza_indice(testa):
    e = _pdf("Annual_Report_2025.pdf", testa)
    assert not e["ammesso"] and e["tipo"] == "da_confermare"


@pytest.mark.parametrize("testa", [
    "Zetagen AG Konzern Investor Relations Kontakt Pressemitteilung Archiv Geschäftsbericht 2025",  # W6
    "Home | Press release | Annual Report 2025 Zetagen plc",  # voce di menu (W7)
    "Press releases Annual Report 2025 Zetagen plc",  # plurale: sezione del sito, non un comunicato (W12)
])
def test_v3_comunicato_solo_come_prima_intestazione(testa):
    e = _pdf("Annual_Report_2025.pdf", testa)
    assert e["ammesso"] and e["tipo"] == "annuale", e["motivo"]
