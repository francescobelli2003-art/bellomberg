"""Scenario arithmetic uses independent, synthetic Markdown tables (R03)."""
import pytest

from bellomberg.core.language import language_context
from bellomberg.reporting.memo_linter import _check_scenario_sum, build_linter_block


def table(rows, header="Scenario | Probabilita", heading="Tabella Scenari"):
    return (f"## {heading}\n| {header} |\n|"
            + "|".join("---" for _ in header.split("|")) + "|\n"
            + "".join(f"| {row} |\n" for row in rows))


@pytest.mark.parametrize("total", ["Totale", " TOTAL ", "**Totale:**", "_total_"])
def test_total_is_not_a_fourth_scenario(total):
    assert _check_scenario_sum(table(["A | 60%", "B | 40%", f"{total} | 100%"])) == []


@pytest.mark.parametrize("separator", ["\n", "\n### Seconda distribuzione\n", "\n## Scenario Table\n"])
def test_independent_tables_are_not_added_together(separator):
    first = table(["A | 60%", "B | 40%", "Totale | 100%"])
    second = table(["C | 70%", "D | 30%", "Total | 100%"],
                   header="Case | Impact | Probability").replace(
                       "C | 70%", "C | -12% | 70%").replace(
                       "D | 30%", "D | +7% | 30%").replace(
                       "Total | 100%", "Total | n.d. | 100%")
    assert _check_scenario_sum(first + separator + second.split("\n", 1)[1]) == []


def test_opposite_errors_in_independent_tables_do_not_cancel():
    first = table(["A | 30%", "B | 30%"])
    second = table(["C | 70%", "D | 70%"])
    warnings = _check_scenario_sum(first + "\n" + second)
    assert len(warnings) == 2
    assert "60%" in warnings[0] and "140%" in warnings[1]


def test_wrong_declared_total_is_checked_separately():
    warnings = _check_scenario_sum(table(["A | 60%", "B | 40%", "Totale | 80%"]))
    assert len(warnings) == 1
    assert "totale dichiarato" in warnings[0].lower()
    assert "80%" in warnings[0] and "100%" in warnings[0]


def test_correct_declared_total_does_not_hide_wrong_distribution():
    warnings = _check_scenario_sum(table(["A | 60%", "B | 60%", "Totale | 120%"]))
    assert len(warnings) == 1
    assert "sommano 120%" in warnings[0]


def test_total_in_a_sentence_is_a_scenario_not_a_total_row():
    warnings = _check_scenario_sum(table(["Perdita totale del mercato | 25%", "Base | 45%",
                                           "Rialzo | 30%", "Totale | 100%"]))
    assert warnings == []


@pytest.mark.parametrize("second, clean", [("35", True), ("45", True), ("34,9", False), ("45,1", False)])
def test_existing_tolerance_is_preserved(second, clean):
    warnings = _check_scenario_sum(table(["A | 60%", f"B | {second}%"]))
    assert (warnings == []) is clean


def test_decimal_comma_and_impact_percentages_do_not_contaminate_probability():
    assert _check_scenario_sum(table(
        ["A | -15% | 62,5%", "B | +21% | 37,5%", "Totale | +6% | 100,0%"],
        header="Scenario | Impatto | Probabilità")) == []


def test_qualitative_table_does_not_infer_probabilities_from_impact():
    assert _check_scenario_sum(table(["A | alta | -15%", "B | bassa | +12%"],
                                     header="Scenario | Probabilità | Impatto")) == []


@pytest.mark.parametrize("language, heading, header, note, marker", [
    ("it", "Tabella Scenari", "Scenario | Probabilita condizionata", "", "non applicabile"),
    ("en", "Scenario Table", "Scenario | Conditional probability", "", "not applicable"),
    ("it", "Tabella Scenari", "Scenario | Probabilita", "Probabilita condizionate su denominatori differenti.\n", "non applicabile"),
    ("en", "Scenario Table", "Scenario | Probability", "Conditional probabilities with different denominators.\n", "not applicable"),
])
def test_conditional_probabilities_are_explicitly_not_applicable(language, heading, header, note, marker):
    memo = table(["A | 70%", "B | 80%"], header, heading)
    memo = memo.replace(f"## {heading}\n", f"## {heading}\n{note}")
    with language_context(language):
        warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and marker in warnings[0].lower()
    assert "150%" not in warnings[0]


def test_different_denominator_column_is_explicitly_not_applicable():
    warnings = _check_scenario_sum(table(
        ["A | 70% | Evento X", "B | 80% | Evento Y"], "Scenario | Prob | Denominatore"))
    assert len(warnings) == 1 and "non applicabile" in warnings[0].lower()


def test_conditional_table_does_not_suppress_next_ordinary_table():
    conditional = table(["A | 70%", "B | 80%"], "Scenario | Prob condizionata")
    ordinary = table(["C | 65%", "D | 65%"]).split("\n", 1)[1]
    warnings = _check_scenario_sum(conditional + "\n" + ordinary)
    assert len(warnings) == 2
    assert "non applicabile" in warnings[0].lower() and "130%" in warnings[1]


def test_english_warning_and_non_scenario_section_scope():
    with language_context("en"):
        warnings = _check_scenario_sum(table(["A | 60%", "B | 70%"], "Scenario | Probability", "Scenario Table"))
    assert len(warnings) == 1 and "probabilities total 130%" in warnings[0]
    assert _check_scenario_sum(table(["A | 60%", "B | 70%"], heading="Other analysis")) == []


@pytest.mark.parametrize("declared, mismatch", [("80", True), ("100", False)])
def test_single_scenario_checks_declared_total(declared, mismatch):
    warnings = _check_scenario_sum(table(["Unico esito | 100%", f"Totale | {declared}%"]))
    assert bool(warnings) is mismatch
    if mismatch:
        assert "totale dichiarato 80%" in warnings[0] and "100%" in warnings[0]


@pytest.mark.parametrize("label, note", [
    ("Aiuto condizionato", ""),
    ("Conditional aid", ""),
    ("Aiuto", "L'aiuto e' condizionato a una riforma.\n"),
    ("Aid", "Conditional aid depends on a reform.\n"),
])
def test_conditional_event_does_not_suppress_ordinary_probability_sum(label, note):
    memo = table([f"{label} | 60%", "Nessun aiuto | 60%"])
    memo = memo.replace("## Tabella Scenari\n", "## Tabella Scenari\n" + note)
    warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and "sommano 120%" in warnings[0]


@pytest.mark.parametrize("probability", ["30–40%", "30 – 40%", "30-40%", "30—40%", "30%–40%"])
def test_probability_interval_is_explicitly_not_a_point_estimate(probability):
    warnings = _check_scenario_sum(table([f"A | {probability}", "B | 60%"]))
    assert len(warnings) == 1 and "non applicabile" in warnings[0]


@pytest.mark.parametrize("probability, expected", [("+60%", "sommano 120%"), ("-60%", "non applicabile")])
def test_signed_numeric_probabilities_never_look_qualitative(probability, expected):
    warnings = _check_scenario_sum(table([f"A | {probability}", f"B | {probability}"]))
    assert len(warnings) == 1 and expected in warnings[0]


@pytest.mark.parametrize("first, second", [("-10%", "110%"), ("105%", "0%"),
                                          ("- 5%", "95%"), ("−5%", "95%")])
def test_invalid_individual_probability_cannot_pass_sum_tolerance(first, second):
    warnings = _check_scenario_sum(table([f"A | {first}", f"B | {second}"]))
    assert len(warnings) == 1 and "non applicabile" in warnings[0]


def test_conditional_notation_in_probability_cell_remains_not_applicable():
    warnings = _check_scenario_sum(table([r"A | P(A\|X) = 60%", r"B | P(B\|Y) = 60%"]))
    assert len(warnings) == 1 and "non applicabile" in warnings[0]


@pytest.mark.parametrize("note, first, second", [
    ("", r"P(A\|X)", r"P(B\|Y)"),
    ("Probabilities are conditional on different events.\n", "A", "B"),
])
def test_explicit_conditional_declaration_in_labels_or_prose(note, first, second):
    memo = table([f"{first} | 60%", f"{second} | 60%"],
                 "Scenario | Probability", "Scenario Table")
    memo = memo.replace("## Scenario Table\n", "## Scenario Table\n" + note)
    with language_context("en"):
        warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and "not applicable" in warnings[0]
    assert "120%" not in warnings[0]


def test_warning_reaches_real_pdf_renderer(tmp_path, monkeypatch):
    import pymupdf
    from bellomberg.reporting import pdf_institutional as renderer

    # The fixture has no portfolio or NAV history: only the memo body is under test.
    monkeypatch.setattr(renderer, "_gen_charts", lambda *args: (None, None, None))

    memo = table(["A | 60%", "B | 40%", "Total | 80%"],
                 "Scenario | Probability", "Scenario Table")
    block = build_linter_block(memo, {}, language="en")
    path = tmp_path / "synthetic_scenario_warning.pdf"
    with language_context("en"):
        assert renderer.build_institutional_memo(memo + "\n" + block, output_path=str(path),
                                                 title_date="Synthetic test")
    with pymupdf.open(path) as document:
        rendered = " ".join(page.get_text() for page in document)
        for page in document:
            if "MEMO LINTER" in page.get_text():
                page.get_pixmap().save(str(tmp_path / "synthetic_scenario_warning.png"))
    (tmp_path / "synthetic_scenario_warning.txt").write_text(rendered, encoding="utf-8")
    assert "MEMO LINTER" in rendered
    assert "declared total" in rendered.lower() and "80%" in rendered and "100%" in rendered
    assert "180%" not in rendered
