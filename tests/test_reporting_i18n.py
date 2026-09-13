"""Real PDF/MIME output in both languages; synthetic data and a local SMTP sink."""
from datetime import datetime
from pathlib import Path
import sys
import types

import pytest
from pypdf import PdfReader

from bellomberg.reporting import email_sender as email
from bellomberg.reporting import pdf_institutional, pdf_report


MEMO = "## BLUF\nCitazione personale: non tradurmi.\n\n## ACTION TABLE\n| Action | Ticker | EUR | Timing | Confidence |\n|---|---|---|---|---|\n| HOLD | ALFA | 0 | now | HIGH |\n\n## Detail\nOriginal source quotation. [src: fixture]\n"
PORTFOLIO = {"positions": [{"ticker": "ALFA", "peso_pct": 50, "pl_pct": 10}],
             "totale_valore_mercato_eur": 1200, "totale_pl_eur": 200,
             "cash_disponibile_eur": 800, "nav_total_eur": 2000, "n_positions": 1}


@pytest.mark.parametrize("language,label", [("it", "verifica aritmetica"), ("en", "arithmetic checks")])
def test_linter_rendering_language_preserves_quotes_and_check_counts(language, label):
    from bellomberg.reporting.memo_linter import build_linter_block
    from bellomberg.core.language import language_context
    memo = "A: 25k (50% del NAV)\nAvailable cash 50k.\n## Scenario Table\n| Scenario | Prob |\n|---|---|\n| A | 60% |\n| B | 60% |\n"
    with language_context(language):
        block = build_linter_block(memo, {"nav_total_eur": 100000, "cash_disponibile_eur": 10000})
    assert label in block
    assert '25k (50% del NAV)' in block
    assert block.count('\n- ') == 3
    if language == 'en':
        assert 'disclosed' in block and 'probabilities' in block and 'Database' in block


def test_generated_scoreboard_languages_keep_scores_metrics_and_cache_unchanged():
    import copy
    from bellomberg.agents import specialist_scores as scores
    risk = {'portfolio': {'vol_annual_pct':22.5, 'sharpe':.78, 'beta_vs_spy':1.18,
                          'var_95_1d_pct':-3.2, 'max_dd_1y_pct':-17.4}}
    results = [scores.quant_score({}, copy.deepcopy(risk), language=lang) for lang in ('it','en')]
    assert results[0]['metrics'] == results[1]['metrics']
    assert results[0]['score'] == results[1]['score'] and results[0]['max_score'] == results[1]['max_score']
    assert [r[1:] for r in results[0]['lines']] == [r[1:] for r in results[1]['lines']]
    assert 'RISCHIO' in results[0]['verdict'] and 'RISK' in results[1]['verdict']
    cache = {'quant': results[1]}; before = copy.deepcopy(cache)
    rendered = scores.format_scoreboard(cache, language='en')
    assert 'Portfolio risk' in rendered and 'unavailable' in rendered
    assert cache == before
    historic = {'quant': {**results[0], 'verdict':'Verdetto storico da preservare'}}
    assert 'Verdetto storico da preservare' in scores.format_scoreboard(historic, language='en')


@pytest.mark.parametrize("language,expected,unwanted", [
    ("it", "Sintesi e profilo del portafoglio", "Portfolio overview"),
    ("en", "Portfolio overview", "Sintesi e profilo del portafoglio"),
])
def test_institutional_pdf_extracts_localised_labels_preserving_memo(tmp_path, monkeypatch, language, expected, unwanted):
    monkeypatch.setattr(pdf_institutional, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(pdf_institutional, "_gen_charts", lambda *a: (None, None, None))
    path = tmp_path / (language + ".pdf")
    result = pdf_institutional.build_institutional_memo(MEMO, PORTFOLIO, output_path=str(path), language=language)
    assert result == str(path) and path.stat().st_size > 1000
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    assert expected in text and unwanted not in text
    assert "Citazione personale: non tradurmi." in text
    assert "ALFA" in text and "HOLD" in text
    assert ("Pagina" if language == "it" else "Page") in text
    assert ("200" in text) and ("800" in text)
    # Translated cover title must stay inside the existing dark band.
    cover_title = 'Nota di ricerca settimanale' if language == 'it' else 'Weekly Research Note'
    drawn = []
    PdfReader(path).pages[0].extract_text(visitor_text=lambda value, cm, tm, font, size:
        drawn.append((value.strip(), tm[4], size)) if cover_title in value else None)
    assert drawn
    _, bold, _ = pdf_institutional._register_fonts()
    for value, x, size in drawn:
        width = pdf_institutional.pdfmetrics.stringWidth(value, bold, size)
        assert x + width <= pdf_institutional.A4[0] * .38 - pdf_institutional.cm + .1


@pytest.mark.parametrize("language,expected,unwanted", [
    ("it", "Nota di ricerca settimanale", "Weekly Research Note"),
    ("en", "Weekly Research Note", "Posizioni attive"),
])
def test_classic_pdf_real_text(tmp_path, monkeypatch, language, expected, unwanted):
    monkeypatch.setattr(pdf_report, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(pdf_report, "CHARTS_AVAILABLE", False)
    path = tmp_path / (language + ".pdf")
    pdf_report.build_pdf_report(MEMO, PORTFOLIO, output_path=str(path), language=language)
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    assert expected in text and unwanted not in text
    assert "Citazione personale: non tradurmi." in text


class SMTP:
    sent = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, *args):
        pass

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    SMTP.sent.clear()
    monkeypatch.setattr(email.smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setattr(email, "EMAIL_FROM", "source@example.test")
    monkeypatch.setattr(email, "EMAIL_TO", "sink@example.test")
    monkeypatch.setattr(email, "EMAIL_PASSWORD", "test-only")
    return SMTP


@pytest.mark.parametrize("language,subject,missing", [
    ("it", "Nota di ricerca settimanale", "Nessun modello Excel allegato"),
    ("en", "Weekly Research Note", "No Excel valuation model attached"),
])
def test_email_real_mime_and_missing_models(tmp_path, smtp, language, subject, missing):
    pdf = tmp_path / "memo.pdf"
    pdf.write_bytes(b"%PDF synthetic attachment")
    body = email.corpo_valutazioni({}, [], language=language)
    assert email.invia_email_multi_allegati([str(pdf)], body_extra=body, language=language)
    msg, = smtp.sent
    assert subject in str(msg["Subject"])
    alternatives = {part.get_content_type(): part.get_payload(decode=True).decode("utf-8")
                    for part in msg.walk() if part.get_content_type() in {"text/plain", "text/html"}}
    assert set(alternatives) == {"text/plain", "text/html"}
    for text in alternatives.values():
        assert subject in text and missing in text
    assert [p.get_payload(decode=True) for p in msg.walk() if p.get_filename()] == [pdf.read_bytes()]


def test_explicit_email_subject_and_existing_body_are_verbatim(smtp):
    subject = "Titolo originale del PM"
    body = "Testo storico: non riscriverlo"
    assert email.invia_briefing_email(body, oggetto=subject, language="en")
    msg, = smtp.sent
    assert str(msg["Subject"]) == subject
    assert body in msg.get_payload()[0].get_payload(decode=True).decode()


def test_invalid_language_refused_before_file_or_smtp(tmp_path, smtp):
    path = tmp_path / "never.pdf"
    with pytest.raises(ValueError, match="language"):
        pdf_report.build_pdf_report(MEMO, output_path=str(path), language="fr")
    with pytest.raises(ValueError, match="language"):
        email.invia_briefing_email("test", language="fr")
    assert not path.exists() and not smtp.sent


@pytest.mark.parametrize("language,expected", [("it", "Ripartizione settoriale"), ("en", "Sector Breakdown")])
def test_chart_labels_and_plotted_data(tmp_path, monkeypatch, language, expected):
    from bellomberg.reporting import charts_agent
    captured = {}

    def save(fig, name):
        captured["text"] = " ".join(t.get_text() for t in fig.texts)
        captured["text"] += " ".join(t.get_text() for ax in fig.axes for t in ax.texts)
        path = tmp_path / (name + ".png")
        fig.savefig(path)
        return str(path)

    monkeypatch.setattr(charts_agent, "_save", save)
    positions = [{"ticker": "ALFA", "sector": "Original sector name", "peso_pct": 37.5},
                 {"ticker": "BETA", "peso_pct": 62.5}]
    path = charts_agent.chart_sector_breakdown(positions, language=language)
    assert Path(path).stat().st_size > 1000
    assert expected.upper() in captured["text"].upper()
    assert "Original sector name" in captured["text"]
    assert positions[0]["peso_pct"] == 37.5 and positions[1]["peso_pct"] == 62.5


@pytest.mark.parametrize("language,heading", [("it", "PROFILO DI RISCHIO QUANTITATIVO"), ("en", "QUANTITATIVE RISK PROFILE")])
def test_quant_appendix_pdf_with_all_data_providers_stubbed(tmp_path, monkeypatch, language, heading):
    from bellomberg.reporting import charts_quant, charts_rates
    providers = {
        "portfolio_analytics": ("compute_nav_history", {}),
        "portfolio_risk": ("compute_portfolio_risk", {"portfolio": {"sharpe": 0.8, "vol_annual_pct": 12.5}}),
        "portfolio_garch": ("compute_portfolio_garch", {}),
        "portfolio_montecarlo": ("run_monte_carlo", {}),
        "portfolio_factors": ("compute_portfolio_factors", {}),
        "advanced_metrics": ("portfolio_metrics", {}),
    }
    for name, (function, result) in providers.items():
        module = types.ModuleType("bellomberg.portfolio." + name)
        setattr(module, function, lambda value=result: value)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(charts_rates, "yield_curves_chart", lambda: None)
    monkeypatch.setattr(charts_rates, "credit_chart", lambda: None)
    monkeypatch.setattr(charts_quant, "REPORT_DIR", str(tmp_path))
    path = tmp_path / ("quant-" + language + ".pdf")
    assert charts_quant.build_quant_appendix_v2(output_path=str(path), language=language) == str(path)
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    assert heading in text
    assert ("Annualised volatility" if language == "en" else "Volatilit") in text
