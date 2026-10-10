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
    # fix score 09/10 (Opus 5.5): la vol si punteggia contro il target del mandato (SINTETICO)
    mandato = {'rischio': {'volatilita_target_pct': 20, 'stress_gfc_pct': 25}}
    results = [scores.quant_score({}, copy.deepcopy(risk), mandato=mandato, language=lang) for lang in ('it','en')]
    assert results[0]['metrics'] == results[1]['metrics']
    assert results[0]['score'] == results[1]['score'] and results[0]['max_score'] == results[1]['max_score']
    # A7 04/10: righe n.d. dichiarate (punti None) hanno il motivo nella lingua; stesse righe,
    # stesso ordine, stessi punti, e stesso valore per ogni riga punteggiata.
    it_lines, en_lines = results[0]['lines'], results[1]['lines']
    assert len(it_lines) == len(en_lines)
    assert [r[2] for r in it_lines] == [r[2] for r in en_lines]
    for it_row, en_row in zip(it_lines, en_lines):
        if it_row[2] is None:
            assert it_row[1].startswith('n.d.:') and en_row[1].startswith('n/a:'), (it_row, en_row)
        else:
            assert it_row[1] == en_row[1]
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


def test_institutional_pdf_wraps_long_action_cells_inside_their_column(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_institutional, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(pdf_institutional, "_gen_charts", lambda *a: (None, None, None))
    long_timing = ("Wait for the earnings release, preserve the cash reserve, and reassess the position " * 4).strip()
    memo = (
        "## ACTION TABLE\n"
        "| Action | Ticker | EUR | Timing | Confidence |\n"
        "|---|---|---:|---|---|\n"
        f"| HOLD | ALFA | 0 | {long_timing} | HIGH |\n"
        "| BUY | BETA | 100 | After results | MEDIUM |\n"
        "\n## BLUF\nA compact summary.\n"
    )
    path = tmp_path / "wrapped-action-table.pdf"
    pdf_institutional.build_institutional_memo(memo, {}, output_path=str(path), language="en")

    events = []
    def record_position(text, cm, tm, _font, size):
        if text.strip():
            x=cm[0]*tm[4]+cm[2]*tm[5]+cm[4]
            y=cm[1]*tm[4]+cm[3]*tm[5]+cm[5]
            scale=(cm[0]**2+cm[1]**2)**0.5
            events.append((text.strip(),x,y,size,scale))

    PdfReader(path).pages[1].extract_text(visitor_text=record_position)
    timing_x = next(x for text, x, _y, _size, _scale in events if text == "Timing")
    confidence_x = next(x for text, x, _y, _size, _scale in events if text == "Confidence")
    alfa_y = next(y for text, _x, y, _size, _scale in events if text == "ALFA")
    beta_y = next(y for text, _x, y, _size, _scale in events if text == "BETA")
    reg, _bold, _italic = pdf_institutional._register_fonts()
    timing_lines = [
        (text, x, size, scale)
        for text, x, y, size, scale in events
        if timing_x <= x < confidence_x and beta_y < y <= alfa_y and text
    ]

    assert timing_lines
    assert all(
        x + pdf_institutional.pdfmetrics.stringWidth(text, reg, size) * scale <= confidence_x - 2
        for text, x, size, scale in timing_lines
    )


def test_institutional_pdf_cleans_fenced_tables_and_moves_checks_to_appendix(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_institutional, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(pdf_institutional, "_gen_charts", lambda *a: (None, None, None))
    memo = """## ACTION TABLE
| Action | Ticker | EUR | Timing | Confidence |
|---|---|---:|---|---|
| HOLD | ALFA | 0 | Wait | HIGH |

## BLUF
The table layout should stay readable.

## 2. Portfolio
Portfolio details follow.
```markdown
| Ticker | Weight | Note |
|---|---:|---|
| ALFA.DE | 25% | Long term allocation |
```
```text
==============================
Ticker     Issuer                Weight
NOVA.DE    Example Holdings      25%
==============================
```
```text
Asset | Target weight | Base case | Risk note
----- | ------------- | --------- | ---------
NOVA.DE | 25% | Hold | Margin
```

## 12. Closing note
The body ends before the audit appendix.

## MEMO LINTER
- Arithmetic warning preserved.

## ACTION VALIDATOR
- Risk flag preserved.

## QUALITA' DATI
- fred.us_10y: stale observation preserved.
"""
    path = tmp_path / "clean-fenced-table.pdf"
    pdf_institutional.build_institutional_memo(memo, {}, output_path=str(path), language="en")
    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages)

    assert "```" not in text
    assert "=====\n" not in text
    assert "ALFA.DE" in text and "Long term allocation" in text
    assert "NOVA.DE" in text and "Example Holdings" in text and "25%" in text
    assert "Base case" in text and "Margin" in text
    assert "Automated checks" in text
    assert "Arithmetic warning preserved." in text
    assert "Risk flag preserved." in text
    assert "fred.us_10y: stale observation preserved." in text
    assert pages[-1].find("Automated checks") < pages[-1].find("MEMO LINTER")
    assert pages[-1].find("The body ends before the audit appendix.") == -1

    cell_positions = {}
    reader.pages[1].extract_text(visitor_text=lambda value, cm, tm, _font, _size:
        cell_positions.setdefault(value.strip(), (cm[4] + tm[4], cm[5] + tm[5]))
        if value.strip() in {"Asset", "Target weight", "Base case", "Risk note"} else None)
    header_positions = [cell_positions[label] for label in ("Asset", "Target weight", "Base case", "Risk note")]
    assert len(header_positions) == 4
    assert max(y for _x, y in header_positions) - min(y for _x, y in header_positions) < 1


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
