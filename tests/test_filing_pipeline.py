"""Automazione I-20: rete/cataloghi sintetici, lettore e confronto reali."""
from datetime import date
import json
from pathlib import Path

import pytest


@pytest.fixture
def profilo():
    return {
        "ticker": "ACME.TEST", "emittente_id": "EMITTENTE:ACME",
        "lingua": "en", "tipo": "annuale", "perimetro": "consolidato",
        "fonti": ["ir"], "ir_urls": ["https://acme.example/investors"],
        "host_documenti": ["cdn.acme.example"],
        "verifica": {
            "emittente": r"Acme European plc", "lingua": r"Report language: English",
            "tipo": r"Annual report", "perimetro": r"Consolidated financial statements",
            "periodo": r"Reporting period: (?P<inizio>\d{4}-\d{2}-\d{2}) to (?P<fine>\d{4}-\d{2}-\d{2})",
        },
        "sezioni": {
            "rischi": {"inizio": r"^Risk factors$", "fine": r"^Management discussion$"},
            "gestione": {"inizio": r"^Management discussion$", "fine": r"^Other information$"},
        },
    }


def relazione(anno, *, inizio=None, fine=None, rischio="Demand is uncertain.", tipo="Annual report"):
    return (f'<html><h1>Acme European plc</h1><p>{tipo}</p>'
            '<p>Report language: English</p><p>Consolidated financial statements</p>'
            f'<p>Reporting period: {inizio or str(anno) + "-01-01"} to '
            f'{fine or str(anno) + "-12-31"}</p>'
            f'<h1>Risk factors</h1><p>{rischio}</p>'
            '<h1>Management discussion</h1><p>Revenue is stable.</p>'
            '<h1>Other information</h1><p>End of report.</p></html>').encode()


def rete(monkeypatch, pagine):
    from bellomberg.market_data import lettore_trimestrali
    chiamate = []

    class Risposta:
        def __init__(self, url, content):
            self.url, self.content = url, content
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
            self.status_code = 200

        def raise_for_status(self):
            pass

    def get(url, **kwargs):
        chiamate.append(url)
        assert url in pagine, f"rete non prevista: {url}"
        valore = pagine[url]
        if isinstance(valore, Exception):
            raise valore
        return Risposta(url, valore)

    monkeypatch.setattr(lettore_trimestrali.requests, "get", get)
    return chiamate


def pagina_ir(urls):
    return ('<html>' + ''.join(f'<a href="{url}">Annual results</a>' for url in urls)
            + '</html>').encode()


def esegui(profilo, tmp_path, **kwargs):
    from bellomberg.market_data.filing_pipeline import esegui_profilo
    return esegui_profilo(profilo, archivio=tmp_path / "archivio",
                         oggi=date(2026, 9, 10), **kwargs)


def test_no_sources_declares_unavailable_without_network(profilo, tmp_path, monkeypatch):
    profilo["fonti"] = []
    chiamate = rete(monkeypatch, {})
    out = esegui(profilo, tmp_path)
    assert out["stato"] == "non_disponibile"
    assert out["motivi"]
    assert out["confronto_corrente"] is None
    assert out["freschezza"]["ultimo_periodo"] == "n.d."
    assert chiamate == []


def test_ir_annual_pair_cites_verified_snapshots_without_promising_freshness(profilo, tmp_path, monkeypatch):
    urls = [f"https://cdn.acme.example/annual-{anno}.html" for anno in (2025, 2024)]
    rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir(urls),
                      urls[0]: relazione(2025, rischio="Demand is declining."),
                      urls[1]: relazione(2024)})
    out = esegui(profilo, tmp_path)
    assert out["confronto_corrente"]["stato"] == "ok"
    assert out["coppia"]["prima"]["metadati"]["periodo_fine"] == "2024-12-31"
    assert out["coppia"]["dopo"]["metadati"]["periodo_fine"] == "2025-12-31"
    assert out["freschezza"]["stato"] == "n.d."
    assert out["freschezza"]["checked_at"] == "2026-09-10"
    assert any("pagina" in motivo for motivo in out["copertura"]["limiti"])
    change = out["confronto_corrente"]["cambiamenti"][0]
    assert change["prima"]["url"] == urls[1]
    assert change["dopo"]["url"] == urls[0]
    assert change["dopo"]["sha256"] == out["coppia"]["dopo"]["sha256"]


@pytest.mark.parametrize("years", [(2025, 2023), (2025,)])
def test_missing_immediately_previous_year_is_not_replaced(profilo, tmp_path, monkeypatch, years):
    urls = [f"https://acme.example/annual-{anno}.html" for anno in years]
    rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir(urls),
                      **{url: relazione(anno) for url, anno in zip(urls, years)}})
    out = esegui(profilo, tmp_path)
    assert out["confronto_corrente"] is None
    assert out["coppia"] is None
    assert any("omologo" in m for m in out["motivi"])


def test_quarters_require_same_season_not_previous_quarter(profilo, tmp_path, monkeypatch):
    profilo["tipo"] = "trimestrale"
    profilo["verifica"]["tipo"] = "Quarterly report"
    periodi = [("2025-04-01", "2025-06-30"), ("2025-01-01", "2025-03-31"),
               ("2024-04-01", "2024-06-30")]
    urls = [f"https://acme.example/quarter-{fine}.html" for _, fine in periodi]
    rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir(urls), **{
        url: relazione(0, inizio=a, fine=b, tipo="Quarterly report")
        for url, (a, b) in zip(urls, periodi)}})
    out = esegui(profilo, tmp_path)
    assert out["coppia"]["prima"]["metadati"]["periodo_fine"] == "2024-06-30"
    assert out["coppia"]["dopo"]["metadati"]["periodo_fine"] == "2025-06-30"


def test_unverified_new_sec_filing_labels_old_comparison_historical(profilo, tmp_path, monkeypatch):
    from bellomberg.market_data import sec_edgar
    profilo.update(emittente_id="CIK:0000000001", fonti=["sec"])
    urls = [f"https://www.sec.gov/annual-{anno}.html" for anno in (2026, 2025, 2024)]
    rows = [{"url": url, "form": "10-K", "report_date": f"{anno}-12-31",
             "filed_date": f"{anno + 1}-02-01", "emittente_id": profilo["emittente_id"]}
            for anno, url in zip((2026, 2025, 2024), urls)]
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", lambda ticker: {
        "stato": "ok", "documenti": rows, "motivi": []})
    rete(monkeypatch, {urls[0]: b'<html>Unreadable identity and period.</html>',
                      urls[1]: relazione(2025), urls[2]: relazione(2024)})
    out = esegui(profilo, tmp_path)
    assert out["ultimo_non_verificato"] is True
    assert out["confronto_corrente"] is None
    assert out["confronto_storico"]["stato"] == "ok"
    assert out["coppia"]["ambito"] == "storico"
    assert out["stato"] == "parziale"


@pytest.mark.parametrize("uguale", [True, False])
def test_same_period_hashes_deduplicate_or_declare_ambiguous(profilo, tmp_path, monkeypatch, uguale):
    urls = ["https://acme.example/annual-2025.html", "https://acme.example/annual-copy.html",
            "https://acme.example/annual-2024.html"]
    rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir(urls), urls[0]: relazione(2025),
                      urls[1]: relazione(2025, rischio="Demand is uncertain." if uguale else "Changed version."),
                      urls[2]: relazione(2024)})
    out = esegui(profilo, tmp_path)
    if uguale:
        assert out["confronto_corrente"]["stato"] == "ok"
        assert any(c["stato"] == "duplicato" for c in out["candidati"])
    else:
        assert out["confronto_corrente"] is None
        assert any(c["stato"] == "versione_ambigua" for c in out["candidati"])
        assert out["ultimo_non_verificato"] is True


def test_ir_external_link_is_rejected_without_contact(profilo, tmp_path, monkeypatch):
    evil = "https://evil.example/annual-report.html"
    chiamate = rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir([evil])})
    out = esegui(profilo, tmp_path)
    assert chiamate == profilo["ir_urls"]
    assert out["candidati"][0]["stato"] == "non_verificato"
    assert "host" in " ".join(out["candidati"][0]["motivi"])


def test_europe_uses_explicit_lei_without_sec_or_fuzzy_lookup(profilo, tmp_path, monkeypatch):
    from bellomberg.market_data import esef, sec_edgar
    profilo.update(emittente_id="LEI:ACMEEXPLICITLEI", fonti=["esef"])
    def vietato(*args, **kwargs):
        pytest.fail("lookup non autorizzato")
    monkeypatch.setattr(esef, "resolve_lei", vietato)
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", vietato)
    urls = [f"https://filings.xbrl.org/annual-{anno}.html" for anno in (2025, 2024)]
    lei_chiamati = []
    def elenco(lei):
        lei_chiamati.append(lei)
        return [{"report_url": url, "period_end": f"{anno}-12-31", "language": "en",
                 "emittente_id": profilo["emittente_id"]}
                for url, anno in zip(urls, (2025, 2024))]
    monkeypatch.setattr(esef, "_list_filings", elenco)
    rete(monkeypatch, {url: relazione(anno) for url, anno in zip(urls, (2025, 2024))})
    out = esegui(profilo, tmp_path)
    assert lei_chiamati == ["ACMEEXPLICITLEI"]
    assert out["confronto_corrente"]["stato"] == "ok"
    assert any("non esaustivo" in m for m in out["copertura"]["limiti"])


def test_esef_other_catalog_language_does_not_consume_document_budget(profilo, tmp_path, monkeypatch):
    from bellomberg.market_data import esef
    profilo.update(emittente_id="LEI:ACMEEXPLICITLEI", fonti=["esef"], lingua="it")
    profilo["verifica"]["lingua"] = r"Lingua del rapporto: italiano"
    english = "https://filings.xbrl.org/annual-2025-en.html"
    italiane = [f"https://filings.xbrl.org/annual-{anno}-it.html" for anno in (2025, 2024)]
    rows = [{"report_url": english, "period_end": "2025-12-31", "language": "en",
             "emittente_id": profilo["emittente_id"]}] + [
        {"report_url": url, "period_end": f"{anno}-12-31", "language": "it",
         "emittente_id": profilo["emittente_id"]} for url, anno in zip(italiane, (2025, 2024))]
    monkeypatch.setattr(esef, "_list_filings", lambda lei: rows)
    chiamate = rete(monkeypatch, {url: relazione(anno).replace(b"Report language: English", b"Lingua del rapporto: italiano")
                                 for url, anno in zip(italiane, (2025, 2024))})
    out = esegui(profilo, tmp_path, max_documenti=2)
    assert english not in chiamate
    assert out["copertura"]["documenti_tentati"] == 2
    assert out["confronto_corrente"]["stato"] == "ok"
    assert out["ultimo_non_verificato"] is False
    scarto = next(c for c in out["candidati"] if c["url"] == english)
    assert scarto["stato"] == "non_applicabile"
    assert any("lingua" in m for m in scarto["motivi"])


@pytest.mark.parametrize("lingua_catalogo", [None, "it"])
def test_esef_unknown_or_contradicted_language_remains_unverified(profilo, tmp_path, monkeypatch, lingua_catalogo):
    from bellomberg.market_data import esef
    profilo.update(emittente_id="LEI:ACMEEXPLICITLEI", fonti=["esef"], lingua="it")
    profilo["verifica"]["lingua"] = r"Lingua del rapporto: italiano"
    url = "https://filings.xbrl.org/annual-2025.html"
    monkeypatch.setattr(esef, "_list_filings", lambda lei: [{
        "report_url": url, "period_end": "2025-12-31", "language": lingua_catalogo,
        "emittente_id": profilo["emittente_id"]}])
    chiamate = rete(monkeypatch, {url: relazione(2025)})
    out = esegui(profilo, tmp_path)
    assert chiamate == [url]
    assert out["candidati"][0]["stato"] == "non_verificato"
    assert out["ultimo_non_verificato"] is True
    assert out["confronto_corrente"] is None


def test_esef_unverified_second_base_version_makes_comparison_historical(profilo, tmp_path, monkeypatch):
    from bellomberg.market_data import esef
    profilo.update(emittente_id="LEI:ACMEEXPLICITLEI", fonti=["esef"])
    urls = ["https://filings.xbrl.org/annual-2025.html",
            "https://filings.xbrl.org/annual-2024.html",
            "https://filings.xbrl.org/annual-2024-other.html"]
    rows = [{"report_url": url, "period_end": fine, "language": "en",
             "emittente_id": profilo["emittente_id"]}
            for url, fine in zip(urls, ("2025-12-31", "2024-12-31", "2024-12-31"))]
    monkeypatch.setattr(esef, "_list_filings", lambda lei: rows)
    chiamate = rete(monkeypatch, {urls[0]: relazione(2025), urls[1]: relazione(2024),
                                 urls[2]: relazione(2024, rischio="Another version.")})
    out = esegui(profilo, tmp_path, max_documenti=2)
    assert chiamate == urls[:2]
    assert out["candidati"][2]["stato"] == "non_verificato"
    assert out["confronto_corrente"] is None
    assert out["confronto_storico"]["stato"] == "ok"
    assert out["coppia"]["ambito"] == "storico"
    assert any("base non verificata" in motivo for motivo in out["motivi"])


def test_expected_report_date_elapsed_is_declared_even_when_diff_succeeds(profilo, tmp_path, monkeypatch):
    profilo.update(next_report_date="2026-03-01", verificato_il="2026-02-01",
                   next_report_source="https://acme.example/calendar")
    urls = [f"https://acme.example/annual-{anno}.html" for anno in (2025, 2024)]
    rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir(urls),
                      **{url: relazione(anno) for url, anno in zip(urls, (2025, 2024))}})
    out = esegui(profilo, tmp_path)
    assert out["confronto_corrente"]["stato"] == "ok"
    assert out["freschezza"]["stato"] == "stale"
    assert out["freschezza"]["next_report_date"] == "2026-03-01"
    assert out["stato"] == "parziale"


@pytest.mark.parametrize("campi", [
    {"next_report_date": "2026-03-01"},
    {"next_report_date": "2026-03-01", "verificato_il": "2026-02-01"},
    {"next_report_date": "2026-03-01", "next_report_source": "https://acme.example/calendar"},
    {"next_report_date": "2026-03-01", "next_report_source": "https://acme.example/calendar", "verificato_il": "non-data"},
    {"next_report_date": "2026-03-01", "next_report_source": "https://acme.example/calendar", "verificato_il": "2026-10-01"},
])
def test_unsupported_calendar_cannot_establish_staleness(profilo, tmp_path, monkeypatch, campi):
    profilo.update(campi, fonti=[])
    rete(monkeypatch, {})
    out = esegui(profilo, tmp_path)
    assert out["freschezza"]["stato"] == "n.d."
    assert any("calendario" in motivo.lower() for motivo in out["freschezza"]["motivi"])


def test_bytes_changed_after_download_are_rejected_by_pipeline(profilo, tmp_path, monkeypatch):
    from bellomberg.market_data import filing_verifica
    urls = [f"https://acme.example/annual-{anno}.html" for anno in (2025, 2024)]
    rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir(urls),
                      **{url: relazione(anno) for url, anno in zip(urls, (2025, 2024))}})
    originale = filing_verifica.verifica_documento
    mutazioni = []

    def modifica_prima_della_verifica(path, **kwargs):
        if kwargs["url"] == urls[0]:
            Path(path).write_bytes(relazione(2025, rischio="Changed after download."))
            mutazioni.append(path)
        return originale(path, **kwargs)

    monkeypatch.setattr(filing_verifica, "verifica_documento", modifica_prima_della_verifica)
    out = esegui(profilo, tmp_path)
    assert len(mutazioni) == 1
    assert out["confronto_corrente"] is None
    candidato = next(c for c in out["candidati"] if c["url"] == urls[0])
    assert candidato["stato"] == "non_verificato"
    assert any("hash" in m for m in candidato["motivi"])


def test_candidate_limit_is_visible_and_prevents_current_claim(profilo, tmp_path, monkeypatch):
    urls = [f"https://acme.example/annual-{anno}.html" for anno in (2025, 2024, 2023)]
    rete(monkeypatch, {profilo["ir_urls"][0]: pagina_ir(urls),
                      **{url: relazione(anno) for url, anno in zip(urls, (2025, 2024, 2023))}})
    out = esegui(profilo, tmp_path, max_documenti=2)
    assert out["copertura"]["candidati_osservati"] == 3
    assert out["copertura"]["documenti_tentati"] == 2
    assert out["confronto_corrente"] is None
    assert out["confronto_storico"]["stato"] == "ok"
    assert any("limite" in m for m in out["motivi"])


@pytest.mark.parametrize("periodo_rettificato", ["2025-12-31", "2024-12-31"])
def test_sec_amendment_is_visible_and_blocks_current_comparison(profilo, tmp_path, monkeypatch, periodo_rettificato):
    from bellomberg.market_data import sec_edgar
    profilo.update(emittente_id="CIK:0000000001", fonti=["sec"])
    urls = [f"https://www.sec.gov/annual-{anno}.html" for anno in (2025, 2024)]
    rettifica = "https://www.sec.gov/annual-amended.html"
    rows = [{"url": url, "form": "10-K", "report_date": f"{anno}-12-31",
             "emittente_id": profilo["emittente_id"]} for anno, url in zip((2025, 2024), urls)]
    rows.insert(0, {"url": rettifica, "form": "10-K/A", "report_date": periodo_rettificato,
                    "emittente_id": profilo["emittente_id"]})
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", lambda ticker: {
        "stato": "ok", "documenti": rows, "motivi": []})
    chiamate = rete(monkeypatch, {url: relazione(anno) for url, anno in zip(urls, (2025, 2024))})
    out = esegui(profilo, tmp_path)
    assert rettifica not in chiamate
    assert out["confronto_corrente"] is None
    assert out["confronto_storico"]["stato"] == "ok"
    assert any("rettifica" in motivo for motivo in out["motivi"])


def test_censused_ir_source_keeps_documented_calendar(profilo, tmp_path, monkeypatch):
    from bellomberg.market_data import fonti_guidance
    url_ir = profilo.pop("ir_urls")[0]
    urls = [f"https://acme.example/annual-{anno}.html" for anno in (2025, 2024)]
    fonte = {"stato": "ok", "ir_url": url_ir, "next_report_date": "2026-03-01",
             "next_report_source": "https://acme.example/calendar", "verificato_il": "2026-02-01"}
    monkeypatch.setattr(fonti_guidance, "fonte_per", lambda ticker: fonte)
    rete(monkeypatch, {url_ir: pagina_ir(urls),
                      **{url: relazione(anno) for url, anno in zip(urls, (2025, 2024))}})
    out = esegui(profilo, tmp_path)
    assert out["freschezza"]["next_report_source"] == fonte["next_report_source"]
    assert out["freschezza"]["verificato_il"] == "2026-02-01"
    assert out["freschezza"]["stato"] == "stale"
    assert out["confronto_corrente"]["stato"] == "ok"


def test_catalog_failure_is_declared_without_false_empty_success(profilo, tmp_path, monkeypatch):
    from bellomberg.market_data import sec_edgar
    profilo.update(emittente_id="CIK:0000000001", fonti=["sec"])
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", lambda ticker: {
        "stato": "errore", "documenti": [], "motivi": ["contatto SEC mancante"]})
    rete(monkeypatch, {})
    out = esegui(profilo, tmp_path)
    assert out["stato"] == "non_disponibile"
    assert out["fonti"][0]["stato"] == "errore"
    assert any("contatto SEC mancante" in m for m in out["motivi"])


def test_cli_reads_profile_and_returns_declared_json(profilo, tmp_path, monkeypatch, capsys):
    from bellomberg.market_data.filing_pipeline import main
    profilo["fonti"] = []
    path = tmp_path / "profilo.json"
    path.write_text(json.dumps(profilo), encoding="utf-8")
    rete(monkeypatch, {})
    status = main([str(path), "--archivio", str(tmp_path / "archivio")])
    result = json.loads(capsys.readouterr().out)
    assert status == 1
    assert result["ticker"] == "ACME.TEST"
    assert result["stato"] == "non_disponibile"
