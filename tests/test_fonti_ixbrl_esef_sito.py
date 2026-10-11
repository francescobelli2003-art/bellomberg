"""R-FONTI 10/10 (Opus 5.5): quattro buchi di fonte misurati su due emittenti del book, provati qui su dati
SINTETICI (CIK 0009990042, LEI 999900ZZFONTI0000042, nomi e cifre inventati).

1. numeri e storico SEC: companyfacts fermo -> fatti dall'iXBRL del deposito, fonte dichiarata, conflitti;
2. 6-K: «three-month period ended» e varianti, regole automatiche vecchie aggiornate;
3. PDF «Relazione finanziaria» senza aggettivo: semestrale/annuale/da confermare, mai annuale per difetto;
4. storico ESEF: repository fermo -> pacchetto ufficiale dal sito dell'emittente, comparativi rideterminati.
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta

import pytest

from bellomberg.market_data import esef, esef_sito, filing_numeri, filing_profili_auto, sec_edgar, sec_xbrl
from bellomberg.market_data.esef_sito import classifica_pdf, scegli_pdf
from bellomberg.market_data.filing_numeri import numeri_per_coppia
from bellomberg.market_data.filing_profili_auto import REGOLE_6K_LEGACY, aggiorna_regole_6k, sonda_tipo_6k
from bellomberg.market_data.filing_verifica import PERIODO_6K_STANDARD, TIPO_6K_STANDARD, verifica_documento

CIK = "0009990042"
ALTRO_CIK = "0009990043"
ACCN_URL = "https://www.sec.gov/Archives/edgar/data/9990042/000999004226000123/zzfonti-20251231.htm"
ACCN = "0009990042-26-000123"

# ------------------------------------------------------------------ iXBRL SEC sintetico

NS = ('xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
      'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
      'xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
      'xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full" '
      'xmlns:dei="http://xbrl.sec.gov/dei/2024" xmlns:zz="http://zzfonti.example/2025" '
      'xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"')


def _ctx(cid, periodo, cik=CIK, segmento=""):
    seg = f"<xbrli:segment>{segmento}</xbrli:segment>" if segmento else ""
    return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}'
            f'</xbrli:identifier>{seg}</xbrli:entity><xbrli:period>{periodo}</xbrli:period></xbrli:context>')


def _durata(a, b):
    return f"<xbrli:startDate>{a}</xbrli:startDate><xbrli:endDate>{b}</xbrli:endDate>"


def _nf(fid, concetto, valore, ctx, unita="usd"):
    return (f'<ix:nonFraction id="{fid}" name="{concetto}" contextRef="{ctx}" unitRef="{unita}" decimals="-3" '
            f'scale="3" format="ixt:num-dot-decimal">{valore}</ix:nonFraction>')


def ixbrl_20f(*, ricavi_24="800", ricavi_25="1,000", utile_24="120", utile_25="150", fine_dei="December 31, 2025",
              cik=CIK, extra=""):
    """20-F inline XBRL sintetico: FY2025 e comparativo FY2024 (migliaia di USD, scale=3)."""
    header = ('<div style="display:none"><ix:header><ix:hidden>'
              f'<ix:nonNumeric id="dpe" name="dei:DocumentPeriodEndDate" contextRef="d25" '
              f'format="ixt:date-monthname-day-year-en">{fine_dei}</ix:nonNumeric></ix:hidden><ix:resources>'
              + _ctx("d25", _durata("2025-01-01", "2025-12-31"), cik)
              + _ctx("d24", _durata("2024-01-01", "2024-12-31"), cik)
              + _ctx("i25", "<xbrli:instant>2025-12-31</xbrli:instant>", cik)
              + _ctx("i24", "<xbrli:instant>2024-12-31</xbrli:instant>", cik)
              + _ctx("x25", _durata("2025-01-01", "2025-12-31"), ALTRO_CIK)
              + _ctx("s25", _durata("2025-01-01", "2025-12-31"), cik,
                     '<xbrldi:explicitMember dimension="ifrs-full:SegmentsAxis">zz:RetailMember</xbrldi:explicitMember>')
              + '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
              '</ix:resources></ix:header></div>')
    corpo = ("<p>" + _nf("r25", "ifrs-full:Revenue", ricavi_25, "d25") + _nf("r24", "ifrs-full:Revenue", ricavi_24, "d24")
             + _nf("u25", "ifrs-full:ProfitLossAttributableToOwnersOfParent", utile_25, "d25")
             + _nf("u24", "ifrs-full:ProfitLossAttributableToOwnersOfParent", utile_24, "d24")
             + _nf("e25", "ifrs-full:EquityAttributableToOwnersOfParent", "2,200", "i25")
             + _nf("e24", "ifrs-full:EquityAttributableToOwnersOfParent", "1,900", "i24")
             + _nf("rx", "ifrs-full:Revenue", "9,999", "x25")       # altra entita': fuori
             + _nf("rs", "ifrs-full:Revenue", "333", "s25")         # scomposizione per asse: fuori
             + extra + "</p>")
    return (f'<?xml version="1.0" encoding="utf-8"?><html {NS} xml:lang="en"><head><title>ZZ Fonti</title></head>'
            f'<body>{header}{corpo}</body></html>').encode("utf-8")


def _cf(righe):
    """companyfacts sintetico: {concetto: [(start, end, val)]} in ifrs-full/USD, 20-F FY."""
    return {"facts": {"ifrs-full": {nome: {"units": {"USD": [
        {"start": a, "end": b, "val": v, "form": "20-F", "fp": "FY", "filed": "2025-04-10",
         "accn": "0009990042-25-000077", "fy": 2024} if a else
        {"end": b, "val": v, "form": "20-F", "fp": "FY", "filed": "2025-04-10", "accn": "0009990042-25-000077",
         "fy": 2024} for a, b, v in lista]}} for nome, lista in righe.items()}}}


CF_24 = _cf({"Revenue": [("2023-01-01", "2023-12-31", 700000), ("2024-01-01", "2024-12-31", 800000)],
             "ProfitLossAttributableToOwnersOfParent": [("2024-01-01", "2024-12-31", 120000)],
             "EquityAttributableToOwnersOfParent": [(None, "2024-12-31", 1900000)]})


def _coppia(path, url=ACCN_URL):
    return {"prima": {"url": "https://www.sec.gov/Archives/edgar/data/9990042/000999004225000077/zz-2024.htm",
                      "metadati": {"periodo_inizio": "2024-01-01", "periodo_fine": "2024-12-31"}},
            "dopo": {"url": url, "path": str(path), "metadati": {"periodo_inizio": "2025-01-01",
                                                                "periodo_fine": "2025-12-31", "tipo": "annuale"}}}


@pytest.fixture
def cf_fermo(monkeypatch):
    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", lambda cik, **kw: CF_24)


def _doc(tmp_path, contenuto, nome="zzfonti-20251231.htm"):
    p = tmp_path / nome
    p.write_bytes(contenuto)
    return p


def test_numeri_dall_ixbrl_quando_companyfacts_e_fermo(tmp_path, cf_fermo):
    out = numeri_per_coppia(CIK, _coppia(_doc(tmp_path, ixbrl_20f())))
    assert out["stato"] == "ok" and out["origine"] == "ixbrl_locale"
    assert out["fonte"].startswith(f"iXBRL del deposito {ACCN} convertito in locale")
    assert out["fonte"] != "SEC companyfacts" and "companyfacts non contiene ancora" in out["nota_fonte"]
    voci = {v["voce"]: v for v in out["voci"]}
    assert (voci["ricavi"]["prima"], voci["ricavi"]["dopo"]) == (800000, 1000000.0)
    assert voci["ricavi"]["fonte_prima"] == "SEC companyfacts" and "iXBRL" in voci["ricavi"]["fonte_dopo"]
    assert voci["utile_netto"]["dopo"] == 150000.0 and voci["utile_netto"]["valuta"] == "USD"
    assert "conflitti" not in out  # anno sovrapposto coerente: nessun conflitto
    assert out["conversione"]["altre_entita"] >= 1  # il fatto dell'altro CIK e' stato tolto


def test_anno_sovrapposto_rideterminato_vale_il_deposito_e_si_dichiara(tmp_path, cf_fermo):
    # v2 (riserva MEDIO-2): il comparativo diverso nel deposito piu' recente e' un RIDETERMINATO: vale il deposito
    out = numeri_per_coppia(CIK, _coppia(_doc(tmp_path, ixbrl_20f(ricavi_24="790"))))
    ricavi = next(v for v in out["voci"] if v["voce"] == "ricavi")
    assert ricavi["prima"] == 790000.0 and ricavi["rideterminato"]["valore_companyfacts"] == 800000
    (c,) = out["rideterminazioni_fatti"]
    assert c["concetto"] == "ifrs-full:Revenue" and c["fine"] == "2024-12-31"
    assert c["valore_precedente"] == 800000 and c["valore_usato"] == 790000.0
    assert any("rideterminati" in a for a in out["avvisi"])


def test_documento_senza_ixbrl_resta_non_aggiornato(tmp_path, cf_fermo):
    p = _doc(tmp_path, b"<html><body>6-K three-month period ended March 31, 2026</body></html>", "zz6k.htm")
    out = numeri_per_coppia(CIK, _coppia(p))
    assert out["stato"] == "non_aggiornato" and out["voci"] == []
    assert "senza inline XBRL" in out["motivo"] and out["fonte"] == "SEC companyfacts"


def test_ixbrl_di_un_altro_emittente_rifiutato(tmp_path, cf_fermo):
    out = numeri_per_coppia(CIK, _coppia(_doc(tmp_path, ixbrl_20f(cik=ALTRO_CIK))))
    assert out["stato"] == "non_aggiornato" and "non convertito" in out["motivo"] and CIK in out["motivo"]


def test_periodo_del_documento_diverso_rifiutato(tmp_path, cf_fermo):
    out = numeri_per_coppia(CIK, _coppia(_doc(tmp_path, ixbrl_20f(fine_dei="June 30, 2025"))))
    assert out["stato"] == "non_aggiornato" and "2025-06-30" in out["motivo"]


def test_valori_incoerenti_nello_stesso_documento_tolti_e_dichiarati(tmp_path, cf_fermo):
    doppio = _nf("u25b", "ifrs-full:ProfitLossAttributableToOwnersOfParent", "151", "d25")
    out = numeri_per_coppia(CIK, _coppia(_doc(tmp_path, ixbrl_20f(extra=doppio))))
    assert "utile_netto" not in {v["voce"] for v in out["voci"]}
    assert any(s["voce"] == "utile_netto" for s in out["scarti"])
    assert out["incoerenti"][0]["valori"] == [150000.0, 151000.0]


def test_fatti_ixbrl_guardie():
    from bellomberg.market_data import ixbrl_oim
    import io
    raw = ixbrl_oim.converti_xhtml(io.BytesIO(ixbrl_20f()))
    letto = sec_xbrl.fatti_ixbrl(raw, CIK, meta={"accn": ACCN})
    righe = letto["facts"]["ifrs-full"]["Revenue"]["units"]["USD"]
    assert sorted((r.get("start"), r["end"], r["val"]) for r in righe) == [
        ("2024-01-01", "2024-12-31", 800000.0), ("2025-01-01", "2025-12-31", 1000000.0)]
    assert all(r["fonte"] == "ixbrl_locale" and r["accn"] == ACCN for r in righe)
    assert letto["periodo_documento"] == ["2025-12-31"] and letto["altre_entita"] >= 1
    assert sec_xbrl.accession_da_url(ACCN_URL) == ACCN and sec_xbrl.accession_da_url("https://x.example/a") is None


# ------------------------------------------------------------------ storico SEC (get_financial_history)

def _submissions(*, report="2025-12-31", inline=1, form="20-F"):
    return {"filings": {"recent": {"form": [form, "6-K"], "reportDate": [report, "2026-03-31"],
                                   "filingDate": ["2026-04-08", "2026-05-14"],
                                   "accessionNumber": [ACCN, "0009990042-26-000200"],
                                   "primaryDocument": ["zzfonti-20251231.htm", "zz6k.htm"],
                                   "isInlineXBRL": [inline, 0]}}}


@pytest.fixture
def storico(monkeypatch, tmp_path):
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path / "xbrl_cache"))
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: None)
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t, motivo=None: CIK)
    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", lambda cik, **kw: CF_24)
    monkeypatch.setattr(sec_xbrl, "cache_superata", lambda cik: None)
    conta = {"submissions": 0, "download": 0}

    def monta(contenuto=None, submissions=None):
        def _sub(cik):
            conta["submissions"] += 1
            return submissions or _submissions()

        def _scarica(url, cartella):
            conta["download"] += 1
            assert url == "https://www.sec.gov/Archives/edgar/data/9990042/000999004226000123/zzfonti-20251231.htm"
            dati = contenuto or ixbrl_20f()
            p = tmp_path / "scaricato.htm"
            p.write_bytes(dati)
            return {"stato": "ok", "path": str(p), "sha256": hashlib.sha256(dati).hexdigest()}
        monkeypatch.setattr(sec_xbrl, "_submissions_sec", _sub)
        monkeypatch.setattr(sec_xbrl, "_scarica_documento_sec", _scarica)
        return conta
    return monta


def test_storico_aggiunge_l_esercizio_mancante_dichiarato(storico):
    conta = storico()
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert r["items"]["revenue"] == {2023: 700000, 2024: 800000, 2025: 1000000.0}
    assert r["items"]["equity"][2025] == 2200000.0 and r["years"][-1] == 2025
    rip = r["ripiego_ixbrl"]
    assert rip["stato"] == "ok" and rip["origine"] == "ixbrl_locale" and rip["rideterminazioni"] == []
    assert rip["anni_per_voce"]["revenue"] == [2025] and rip["depositi"][0]["accn"] == ACCN
    assert r["_source"].startswith(f"iXBRL del deposito {ACCN} convertito in locale: FY2025")  # in testa (v2)
    lp = r["latest_period"]
    assert lp["period_end"] == "2025-12-31" and lp["accession"] == ACCN
    assert lp["metric_metadata"]["revenue"]["source"] == f"iXBRL del deposito {ACCN} convertito in locale"
    # seconda lettura: conversione e elenco in cache (deposito immutabile), nessuna rete
    assert sec_xbrl.get_financial_history("ZZFONTI")["items"]["revenue"][2025] == 1000000.0
    assert conta == {"submissions": 1, "download": 1}


def test_storico_anno_sovrapposto_divergente_vale_il_deposito_recente(storico):
    # v2 (riserva MEDIO-2): come nello storico ESEF vale il deposito piu' recente, dichiarato
    storico(ixbrl_20f(ricavi_24="790"))
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert r["items"]["revenue"][2024] == 790000.0
    (c,) = r["ripiego_ixbrl"]["rideterminazioni"]
    assert (c["concetto"], c["anno"], c["valore_precedente"], c["fonte_precedente"]) == (
        "ifrs-full:Revenue", 2024, 800000, "SEC companyfacts")


def test_storico_deposito_non_inline_dichiarato(storico):
    storico(submissions=_submissions(inline=0))
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert 2025 not in r["items"]["revenue"] and r["ripiego_ixbrl"]["stato"] == "errore"
    assert "non inline XBRL" in r["ripiego_ixbrl"]["motivo"]


def test_storico_senza_rete_dichiara_e_prosegue(monkeypatch, tmp_path, ripiego_ixbrl_giu):
    # rete giu' ESPLICITA (v2: il tripwire del conftest fa fallire il test, non risponde ConnectionError)
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path / "xbrl_cache"))
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: None)
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t, motivo=None: CIK)
    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", lambda cik, **kw: CF_24)
    r = sec_xbrl.get_financial_history("ZZFONTI")
    assert r["items"]["revenue"][2024] == 800000 and 2025 not in r["items"]["revenue"]
    assert r["ripiego_ixbrl"]["stato"] == "errore" and "ConnectionError" in r["ripiego_ixbrl"]["motivo"]


def test_ripiego_non_necessario_senza_rete():
    recente = (datetime(2026, 9, 1) - timedelta(days=100)).date().isoformat()
    facts = _cf({"Revenue": [("2025-06-01", recente, 5000)]})["facts"]
    out = sec_xbrl.ripiego_ixbrl_annuale(CIK, facts, oggi=datetime(2026, 9, 1))
    assert out["stato"] == "non_necessario" and out["depositi"] == []


# ------------------------------------------------------------------ 6-K: periodo trimestrale

@pytest.mark.parametrize("frase", [
    "for the three-month period ended March 31, 2026", "for the three-months period ended March 31, 2026",
    "for the three month period ended March 31, 2026", "for the quarter ended March 31, 2026",
    "for the three‑month period ended March 31, 2026", "for the three and six-month periods ended June 30, 2026"])
def test_trimestrale_riconosciuto(frase):
    assert sonda_tipo_6k(frase) == "trimestrale"
    m = re.search(PERIODO_6K_STANDARD["trimestrale"], frase, re.I)
    assert m and m.group("mesi").lower() in ("three", "quarter")
    assert re.search(TIPO_6K_STANDARD, frase, re.I)


@pytest.mark.parametrize("frase", [
    "for the twelve-month period ended December 31, 2025", "for the 13-month period ended March 31, 2026",
    "for the twenty-three months ended March 31, 2026", "for the years ended December 31, 2025 and 2024"])
def test_nessun_falso_trimestrale(frase):
    assert sonda_tipo_6k(frase) is None
    assert not re.search(PERIODO_6K_STANDARD["trimestrale"], frase, re.I)


def test_six_month_period_e_semestrale():
    frase = "for the six-month period ended June 30, 2026"
    assert sonda_tipo_6k(frase) == "semestrale"
    assert not re.search(PERIODO_6K_STANDARD["trimestrale"], frase, re.I)
    assert re.search(PERIODO_6K_STANDARD["semestrale"], frase, re.I).group("fine") == "June 30, 2026"


def _profilo_6k(**verifica):
    return {"ticker": "ZZFONTI", "emittente_id": "CIK:" + CIK, "cik": CIK, "nome": "ZZ Fonti Holdings",
            "lingua": "en", "perimetro": "consolidato", "tipo": "trimestrale", "forme_sec": ["6-K"],
            "periodo_regola": "piu_recente", "sezioni": {}, "sezioni_intero": True,
            "verifica": {"lingua": r"\b(?:the|and|of)\b", "perimetro": r"consolidated",
                         "emittente": r"\bzz(?:[\s\-.,]|&)+fonti(?:[\s\-.,]|&)+holdings\b", **verifica}}


def test_regole_6k_automatiche_vecchie_aggiornate():
    vecchio = _profilo_6k(tipo=REGOLE_6K_LEGACY["tipo"], periodo=REGOLE_6K_LEGACY["trimestrale"])
    nuovo, nota = aggiorna_regole_6k(vecchio)
    assert nuovo["verifica"]["tipo"] == TIPO_6K_STANDARD
    assert nuovo["verifica"]["periodo"] == PERIODO_6K_STANDARD["trimestrale"]
    assert nuovo["verifica"]["emittente"] == vecchio["verifica"]["emittente"] and "tipo e periodo" in nota
    assert vecchio["verifica"]["tipo"] == REGOLE_6K_LEGACY["tipo"]  # profilo originale intatto
    a_mano = _profilo_6k(tipo=r"interim\s+report", periodo=REGOLE_6K_LEGACY["semestrale"])
    assert aggiorna_regole_6k(a_mano) == (a_mano, None)  # scritte a mano o di un altro tipo: invariate
    assert aggiorna_regole_6k({**vecchio, "forme_sec": ["20-F"]})[1] is None


def test_6k_three_month_period_verificato_con_le_regole_aggiornate(tmp_path):
    testo = ("<html><body><p>ZZ Fonti Holdings Ltd.</p><p>Unaudited interim condensed consolidated financial "
             "statements as of and for the three-month period ended March 31, 2026 and 2025.</p>"
             "<p>The statements of the Group are consolidated.</p></body></html>")
    p = tmp_path / "zzfs1q26_6k.htm"
    p.write_text(testo, encoding="utf-8")
    url = "https://www.sec.gov/Archives/edgar/data/9990042/000999004226000300/zzfs1q26_6k.htm"
    vecchio = _profilo_6k(tipo=REGOLE_6K_LEGACY["tipo"], periodo=REGOLE_6K_LEGACY["trimestrale"])
    prima = verifica_documento(str(p), url=url, profilo=vecchio, catalogo={"form": "6-K"})
    assert prima["stato"] != "ok" and any("tipo: prova testuale assente" in m for m in prima["motivi"])
    dopo = verifica_documento(str(p), url=url, profilo=aggiorna_regole_6k(vecchio)[0], catalogo={"form": "6-K"})
    assert dopo["stato"] == "ok", dopo["motivi"]
    meta = dopo["documento"]["metadati"]
    assert (meta["tipo"], meta["periodo_inizio"], meta["periodo_fine"]) == ("trimestrale", "2026-01-01", "2026-03-31")


def test_pipeline_usa_le_regole_6k_aggiornate_e_lo_dichiara(monkeypatch, tmp_path):
    from bellomberg.market_data import filing_pipeline
    visti = []

    def _raccogli(profilo, archivio, oggi=None):
        visti.append(profilo["verifica"])
        return [], [], [], {}
    monkeypatch.setattr(filing_pipeline, "_raccogli", _raccogli)
    vecchio = _profilo_6k(tipo=REGOLE_6K_LEGACY["tipo"], periodo=REGOLE_6K_LEGACY["trimestrale"])
    out = filing_pipeline.esegui_profilo(vecchio, archivio=str(tmp_path / "archivio"))
    assert visti[0]["tipo"] == TIPO_6K_STANDARD and visti[0]["periodo"] == PERIODO_6K_STANDARD["trimestrale"]
    assert any("regole tipo e periodo del profilo automatico" in x for x in out["copertura"]["limiti"])


# ------------------------------------------------------------------ PDF «Relazione finanziaria»

BASE = "https://www.zzfonti.example/files/investors/"
H1_26 = "ING_Relazione_Finanziaria_30_giugno_2026.pdf"
H1_25 = "ING_Relazione_Finanziaria_30_giugno_2025_v31.7.pdf"
COPERTINA_H1_26 = "ACCELERATING GROWTH HALF-YEAR FINANCIAL REPORT AT 30 JUNE 2026 ZZ FONTI | CONTENTS 1 Disclaimer"
COPERTINA_H1_25 = ("1 2 Disclaimer This document contains forward-looking statements. ZZ FONTI | CONTENTS 3 "
                   "Directors' Report ... Half-year Financial Statements ... 45")


def _pdf(nome, pagina=None, chiusura=None):
    return classifica_pdf({"url": BASE + nome, "testo": nome}, prima_pagina=pagina, chiusura_esercizio=chiusura)


@pytest.mark.parametrize("nome,pagina,periodo", [(H1_26, None, "2026-06-30"), (H1_26, COPERTINA_H1_26, "2026-06-30"),
                                                  (H1_25, None, "2025-06-30"), (H1_25, COPERTINA_H1_25, "2025-06-30")])
def test_relazione_finanziaria_30_giugno_e_semestrale(nome, pagina, periodo):
    # GENERALITA' UE (Opus 5.5): la data nel nome decide solo con l'esercizio dell'emittente noto (qui al 31/12,
    # dai depositi ESEF); con l'esercizio ignoto la sola data resta da confermare (test_generalita_esef_ue)
    e = _pdf(nome, pagina, chiusura="12-31")
    assert e["ammesso"] and e["tipo"] == "semestrale" and e["periodo"] == periodo and e["periodo_stato"] == "certo"
    assert "semestrale" in e["tipo_base"]


@pytest.mark.parametrize("nome", ["ING_Relazione_Finanziaria_31_dicembre_2025.pdf",
                                  "Relazione_finanziaria_annuale_2025.pdf", "Annual_Report_2025.pdf"])
def test_annuale_resta_annuale(nome):
    e = _pdf(nome, chiusura="12-31")
    assert e["ammesso"] and e["tipo"] == "annuale" and e["periodo"] == "2025-12-31"


def test_relazione_finanziaria_ambigua_da_confermare_mai_annuale():
    e = _pdf("Relazione_Finanziaria_2025.pdf")
    assert not e["ammesso"] and e["tipo"] == "da_confermare" and e["serve_prima_pagina"]
    assert "tipo da confermare" in e["motivo"]
    e = _pdf("Relazione_Finanziaria_31_marzo_2026.pdf")  # chiusura che non decide fra i due
    assert not e["ammesso"] and e["tipo"] == "da_confermare"
    e = _pdf("Relazione_Finanziaria_2025.pdf", "ZZ Fonti Annual Report 2025")
    assert e["ammesso"] and e["tipo"] == "annuale"
    # nome a fine giugno, copertina da annuale. GENERALITA' UE (Opus 5.5): il titolo esplicito vince sulla data,
    # ma il periodo resta da confermare con le due date viste (prima: tipo da confermare)
    for chiusura in (None, "12-31"):
        e = _pdf(H1_26, "ZZ FONTI ANNUAL REPORT 2025", chiusura=chiusura)
        assert e["ammesso"] and e["tipo"] == "annuale" and e["periodo_stato"] == "da_confermare"
        assert e["periodi_visti"] == ["2025-12-31", "2026-06-30"] and "presunta" in e["base_periodo"]


def test_scegli_pdf_coppia_semestrale():
    s = scegli_pdf([{"url": BASE + n, "testo": n} for n in (H1_26, H1_25, "ING_Relazione_Finanziaria_31_dicembre_2025.pdf")],
                   oggi=date(2026, 10, 10), chiusura_esercizio="12-31")
    assert s["tipo"] == "semestrale"
    assert s["ultimo"]["url"].endswith(H1_26) and s["precedente"]["url"].endswith(H1_25)


# ------------------------------------------------------------------ storico ESEF dal sito dell'emittente

LEI = "999900ZZFONTI0000042"
DUR = {2024: "2024-01-01T00:00:00/2025-01-01T00:00:00", 2025: "2025-01-01T00:00:00/2026-01-01T00:00:00"}
IST = {2024: "2025-01-01T00:00:00", 2025: "2026-01-01T00:00:00"}


def _oim(fatti, lei=LEI):
    return {"documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},
            "facts": {f"f{i}": {"value": str(v), "decimals": -6,
                                "dimensions": {"concept": "ifrs-full:" + c, "entity": "scheme:" + lei,
                                               "period": per, "unit": "iso4217:EUR"}}
                      for i, (c, per, v) in enumerate(fatti)}}


def _repo(periodo_fine="2024-12-31", anno=2024):
    return {"index_fetched_at": 1e12, "filings": {"7001": {
        "period_end": periodo_fine, "date_added": "2025-03-18", "extract_error": None,
        "facts": {"Revenue": [[anno, 500e6, "EUR"]], "Assets": [[anno, 1820e6, "EUR"]],
                  "EquityAttributableToOwnersOfParent": [[anno, 600e6, "EUR"]]}}}}


@pytest.fixture
def esef_finto(monkeypatch, tmp_path):
    monkeypatch.setattr(esef, "CACHE_DIR", str(tmp_path / "xbrl_cache"))
    monkeypatch.setattr(esef, "resolve_lei", lambda t, company_name=None: (LEI, "LEI sintetico"))
    monkeypatch.setattr(esef, "_save_cache", lambda lei, cache: None)
    stato = {"scaricati": 0}

    def monta(repo, righe=(), raw=None):
        monkeypatch.setattr(esef, "_refresh_entity_cache", lambda lei: json.loads(json.dumps(repo)))
        monkeypatch.setattr(esef_sito, "righe_da_cache", lambda lei: list(righe))

        def _scarica(url, archivio, hosts):
            stato["scaricati"] += 1
            assert hosts == {"www.zzfonti.example"}
            p = tmp_path / "pacchetto.json"
            p.write_text(json.dumps(raw), encoding="utf-8")
            return {"stato": "ok", "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "pacchetto_sha256": "ab" * 32}
        monkeypatch.setattr(esef_sito, "scarica_pacchetto_json", _scarica)
        return stato
    return monta


RIGA_SITO = {"id": "https://www.zzfonti.example/docs/999900ZZFONTI0000042-2025-12-31-1-en.zip",
             "period_end": "2025-12-31", "json_url": "https://www.zzfonti.example/docs/999900ZZFONTI0000042-2025-12-31-1-en.zip",
             "origine": "sito"}
FY25_SITO = [("Revenue", DUR[2025], 610e6), ("Revenue", DUR[2024], 500e6), ("Assets", IST[2025], 2000e6),
             ("Assets", IST[2024], 1815e6), ("EquityAttributableToOwnersOfParent", IST[2025], 700e6)]


def test_storico_esef_integra_il_pacchetto_dal_sito_e_dichiara_il_rideterminato(esef_finto):
    esef_finto(_repo(), [RIGA_SITO], _oim(FY25_SITO))
    r = esef.get_esef_history("ZZF.MI")
    assert r["years"][-1] == 2025 and r["items"]["revenue"] == {2024: 500e6, 2025: 610e6}
    assert r["items"]["total_assets"] == {2024: 1815e6, 2025: 2000e6}  # vale il deposito piu' recente
    (rid,) = r["rideterminazioni"]  # solo dove il valore cambia
    assert rid["voci"] == ["total_assets"] and rid["anno"] == 2024
    assert (rid["valore_precedente"], rid["valore_usato"]) == (1820e6, 1815e6)
    assert rid["nota"] == "comparativo rideterminato nel deposito FY2025"
    assert rid["deposito_usato"] == "FY2025 (sito dell'emittente)"
    assert r["sito_emittente"]["stato"] == "ok" and r["sito_emittente"]["depositi"][0]["period_end"] == "2025-12-31"
    assert "dal sito dell'emittente, convertito in locale per FY2025" in r["_source"]
    assert "FY2025 dal sito dell'emittente" in r["coverage_note"]


def test_storico_esef_pacchetto_di_un_altra_entita_rifiutato(esef_finto):
    esef_finto(_repo(), [RIGA_SITO], _oim(FY25_SITO, lei="999900ZZALTRA0000099"))
    r = esef.get_esef_history("ZZF.MI")
    assert r["years"][-1] == 2024 and r["items"]["total_assets"] == {2024: 1820e6}
    assert r["sito_emittente"]["stato"] == "errore" and "LEI " + LEI in r["sito_emittente"]["motivo"]
    assert any("esercizi dopo il repository" in g for g in r["gaps"])


def test_storico_esef_fermo_senza_pacchetti_dichiarato(esef_finto):
    esef_finto(_repo(), [])
    r = esef.get_esef_history("ZZF.MI")
    assert r["sito_emittente"]["stato"] == "nessun_pacchetto"
    assert any("nessun pacchetto ESEF annuale piu' recente dal sito" in g for g in r["gaps"])


def test_storico_esef_aggiornato_non_tocca_il_sito(esef_finto):
    anno = date.today().year - 1
    stato = esef_finto(_repo(f"{anno}-12-31", anno), [RIGA_SITO], _oim(FY25_SITO))
    r = esef.get_esef_history("ZZF.MI")
    assert r["sito_emittente"]["stato"] == "non_necessario" and stato["scaricati"] == 0
    assert "rideterminazioni" not in r
